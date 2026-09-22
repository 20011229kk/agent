#!/usr/bin/env python3
"""按真实步骤顺序检查 workflow 里的 shell 变量是否"先赋值后使用"。

为什么需要这个检查器
--------------------
外部复核原样执行了 `merge-gate.yml` 的"准备产物目录"步骤，得到
`RAW_ROOT: unbound variable` 退出 1：该变量唯一的赋值发生在**后面**的步骤里。
后果比想象的严重——不是预期的"缺证据 INCOMPLETE"，而是流程本身中断：下载与组装步骤
被跳过，后续 `if: always()` 也补不出可信脚本、绑定变量或有效报告。

`yaml.safe_load` 能通过，`actionlint` 也不看跨步骤的 `GITHUB_ENV` 数据流。所以这里做一件
很窄但很具体的事：**按步骤顺序模拟变量的可见性**。

可见性规则（与 GitHub Actions 实际行为对齐）
-------------------------------------------
一个步骤里 `$VAR` / `${VAR}` 可见，当且仅当 VAR 属于：

1. workflow / job / step 级 `env:`
2. 更早步骤里 `echo "VAR=..." >> $GITHUB_ENV` 写入的
3. 同一步骤内先 `VAR=...` 赋值过的（含 `export VAR=`、`for VAR in`、`read VAR`）
4. runner 注入的内置变量（GITHUB_*、RUNNER_*、HOME 等）
5. 用 `${VAR:-default}` / `${VAR:?}` 等带默认值形式引用的（`set -u` 下不会炸）

判据只覆盖 `run:` 里的 shell 变量。`${{ }}` 表达式由 Actions 在运行前展开，不在此列，
但 `${{ env.X }}` 会被记为对 X 的**使用**，因为 X 若未定义会展开成空串并静默改变行为。

用法
----
  python3 scripts/check_workflow_env.py [--root .] [--workflow PATH]...

退出码：0 全部通过；1 存在问题；2 找不到 workflow。
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FATAL: 需要 PyYAML", file=sys.stderr)
    raise

# runner 注入或 POSIX 常见内置，不需要自己赋值
BUILTIN = {
    "HOME", "PATH", "PWD", "SHELL", "USER", "TMPDIR", "CI", "IFS", "TERM",
    "GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_PATH", "GITHUB_STEP_SUMMARY",
    "GITHUB_WORKSPACE", "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF",
    "GITHUB_REF_NAME", "GITHUB_BASE_REF", "GITHUB_HEAD_REF", "GITHUB_ACTOR",
    "GITHUB_RUN_ID", "GITHUB_RUN_NUMBER", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
    "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_SERVER_URL",
    "GITHUB_API_URL", "GITHUB_TOKEN", "RUNNER_OS", "RUNNER_ARCH",
    "RUNNER_TEMP", "RUNNER_TOOL_CACHE", "RUNNER_WORKSPACE",
}

# ${VAR} 或 $VAR（排除 ${{ }} 表达式、$((...)) 算术、$1 等位置参数）
USE_BRACED = re.compile(r"(?<!\$)\$\{(?!\{)([A-Za-z_][A-Za-z0-9_]*)\}")
USE_BRACED_DEFAULT = re.compile(r"(?<!\$)\$\{(?!\{)([A-Za-z_][A-Za-z0-9_]*)\s*[:#%/]")
USE_PLAIN = re.compile(r"(?<![\$\w])\$([A-Za-z_][A-Za-z0-9_]*)")
EXPR_ENV = re.compile(r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

ASSIGN = re.compile(r"^\s*(?:export\s+|readonly\s+|local\s+)?"
                    r"([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
ASSIGN_INLINE = re.compile(r"(?:^|[;&|(]\s*|\bthen\s+|\bdo\s+)"
                           r"([A-Za-z_][A-Za-z0-9_]*)=", re.M)
FOR_VAR = re.compile(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b")
# `read [-r] [-d x] VAR...`：变量名可能后跟 `;`、`do`、换行
READ_STMT = re.compile(
    r"\bread\b"
    # 选项：只有 -d/-n/-N/-t/-u/-p/-a 才带参数；否则会把变量名当成选项参数吃掉
    # （`read -r d` 里 d 被当成 -r 的参数，正是第一版的 bug）
    r"((?:\s+-(?:[dnNtupa]\s+(?:'[^']*'|\"[^\"]*\"|\S+)|[A-Za-z]+))*)"
    r"((?:\s+[A-Za-z_][A-Za-z0-9_]*)+)")
GITHUB_ENV_WRITE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=[^\n]*?>>\s*\"?\$\{?GITHUB_ENV\}?\"?")
GITHUB_ENV_HEREDOC = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)=", re.M)


class Issue:
    def __init__(self, workflow, job, step, code, name, detail):
        self.workflow = workflow
        self.job = job
        self.step = step
        self.code = code
        self.name = name
        self.detail = detail

    def __str__(self):
        return "[%s] %s / job=%s / step=%r: %s（%s）" % (
            self.code, os.path.basename(self.workflow), self.job, self.step,
            self.name, self.detail)


def _uses(script: str):
    """脚本里用到的变量名（去掉带默认值的安全引用）。"""
    safe = set(USE_BRACED_DEFAULT.findall(script))
    used = set(USE_BRACED.findall(script)) | set(USE_PLAIN.findall(script))
    return used - safe


def _assigns(script: str):
    names = set(ASSIGN.findall(script)) | set(ASSIGN_INLINE.findall(script))
    names |= set(FOR_VAR.findall(script))
    for _opts, targets in READ_STMT.findall(script):
        names |= set(targets.split())
    return names


def _github_env_writes(script: str):
    """本步骤写进 $GITHUB_ENV 的变量（对后续步骤可见）。"""
    names = set()
    for m in GITHUB_ENV_WRITE.finditer(script):
        names.add(m.group(1))
    # heredoc 形式：cat >> "$GITHUB_ENV" <<EOF ... VAR=... EOF
    if "GITHUB_ENV" in script and "<<" in script:
        names |= set(GITHUB_ENV_HEREDOC.findall(script))
    return names


def check_workflow(path: str):
    issues = []
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    workflow_env = set((data.get("env") or {}).keys())

    for job_name, job in sorted((data.get("jobs") or {}).items()):
        if not isinstance(job, dict):
            continue
        visible = set(BUILTIN) | workflow_env | set((job.get("env") or {}).keys())
        for idx, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            step_name = step.get("name") or step.get("uses") or "step#%d" % idx
            step_env = set((step.get("env") or {}).keys())
            here = visible | step_env

            script = step.get("run") or ""
            # ${{ env.X }} 也算使用：X 未定义会展开成空串，静默改变行为
            expr_used = set(EXPR_ENV.findall(yaml.safe_dump(step,
                                                            allow_unicode=True)))
            if script:
                assigned_here = _assigns(script)
                for name in sorted(_uses(script) | expr_used):
                    if name in here or name in assigned_here:
                        continue
                    issues.append(Issue(
                        path, job_name, step_name, "ENV_USED_BEFORE_SET", name,
                        "在本步骤读取，但既不在 workflow/job/step 的 env 里，"
                        "也没有更早步骤写入 $GITHUB_ENV，本步骤内也未赋值 —— "
                        "set -u 下会直接 unbound variable 失败"))
                visible |= _github_env_writes(script)
            else:
                for name in sorted(expr_used):
                    if name not in here:
                        issues.append(Issue(
                            path, job_name, step_name, "ENV_EXPR_UNDEFINED", name,
                            "${{ env.%s }} 未定义 —— 会展开成空串并静默改变行为" % name))
    return issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--workflow", action="append", default=[])
    args = ap.parse_args(argv)

    paths = args.workflow or sorted(
        glob.glob(os.path.join(args.root, ".github", "workflows", "*.yml")) +
        glob.glob(os.path.join(args.root, ".github", "workflows", "*.yaml")))
    if not paths:
        print("NO_WORKFLOWS")
        return 2

    all_issues = []
    for p in paths:
        all_issues.extend(check_workflow(p))

    print("workflows: %d" % len(paths))
    if all_issues:
        print("verdict: FAIL (%d 项)" % len(all_issues))
        for i in all_issues:
            print("  %s" % i)
        return 1
    print("verdict: PASS")
    print("note: 只检查 shell 变量的先赋值后使用；不证明步骤逻辑正确，"
          "也不替代真实运行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

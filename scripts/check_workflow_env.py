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

可见性规则与保证边界
----------------------
一个步骤里 `$VAR` / `${VAR}` 在**当前词法位置**可见，当且仅当 VAR 属于：

1. workflow / job / step 级 `env:`
2. 更早步骤里 `echo "VAR=..." >> $GITHUB_ENV` 写入的
3. 同一步骤内、当前引用**之前**已经出现的 `VAR=...` 赋值
   （含 `export VAR=`、`for VAR in`、`read VAR`）
4. runner 注入的内置变量（GITHUB_*、RUNNER_*、HOME 等）
5. **当前这一处**使用 `${VAR:-default}` / `${VAR:+alt}` / `${VAR:=value}` /
   `${VAR:?message}` 等参数展开保护；保护不传播到后续 `$VAR`。默认值右侧的普通
   `$OTHER` 仍按展开顺序检查；其中 `:=`/`=` 只有在右侧引用检查完成后才把外层变量
   标为已赋值，`:-`/`-` 不会。

嵌套参数展开（如 `${X:-${Y:-fallback}}`）当前不可靠建模，必须显式产生
`ENV_EXPANSION_UNSUPPORTED`，**不能静默 PASS**。

本检查器刻意只承诺**线性词法顺序**：它按字符位置判断"引用前是否出现赋值"，
不建模 `if/case` 分支支配关系、循环是否实际进入、函数调用、子 shell/命令替换的作用域、
`eval/source` 或间接变量名。PASS 的准确含义是：**受支持语法中没有词法上的先读后写**；
它不证明脚本按所有真实控制流都能运行。关键步骤仍需真实 shell 故障路径测试。

`${{ }}` 表达式由 Actions 在运行前展开，不作为 shell 变量；但 `${{ env.X }}` 会被记为
对 X 的使用，因为 X 未定义会展开成空串并静默改变行为。

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

# shell 参数展开。逐次判断，不能把同名变量的安全引用推广到整段脚本。
# `${X:-fallback}` 只保护这一处；后面的 `$X` 仍然是不安全引用。
BRACED_PARAM = re.compile(
    r"(?<!\$)\$\{(?!\{)([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}")
USE_PLAIN = re.compile(r"(?<![\$\w])\$([A-Za-z_][A-Za-z0-9_]*)")
EXPR_ENV = re.compile(r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# 受 `set -u` 保护的参数展开操作符。#/%/替换等要求变量已经定义，不在此列。
SAFE_PARAM_OPS = (":-", "-", ":+", "+", ":=", "=", ":?", "?")
ASSIGNING_PARAM_OPS = (":=", "=")

# 赋值事件：支持行首、`; & | (` 后、then/do 后的普通赋值。
# `+=` 也算赋值，但第一次就用 += 的 shell 细节不在本检查器保证范围内。
ASSIGN_EVENT = re.compile(
    r"(?:^|[;\n&|(]\s*|\bthen\s+|\bdo\s+)\s*"
    r"(?:export\s+|readonly\s+|local\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\+?=", re.M)
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


def _safe_param_operator(tail: str):
    """返回参数展开操作符；不是受保护形式则返回 None。"""
    for op in SAFE_PARAM_OPS:  # 长操作符在前，避免 `:-` 被 `-` 抢先
        if tail.startswith(op):
            return op
    return None


def _command_end(script: str, start: int) -> int:
    """返回赋值所在简单命令的词法末尾（`;` 或换行）。

    不尝试解析引号/命令替换；这就是文档声明的线性词法保证边界。把赋值在命令末尾才
    设为可见，可正确处理 `X=$X`：右侧展开发生在赋值生效前，不能因为看见 `X=` 就
    提前把后面的 `$X` 洗白。
    """
    positions = [p for p in (script.find(";", start), script.find("\n", start))
                 if p >= 0]
    return min(positions) if positions else len(script)


def _unsupported_expansions(script: str):
    """返回无法可靠建模的嵌套 `${...${...}...}` 位置。

    当前解析器用正则识别最外层参数展开，无法正确配对任意嵌套大括号。与其静默漏掉
    内层引用，不如明确返回 ENV_EXPANSION_UNSUPPORTED。普通 RHS `$Y` 已支持；
    需要嵌套 `${Y:-...}` 时应使用真实 shell 检查或后续引入词法解析器。
    """
    return [m.start() for m in re.finditer(r"\$\{(?!\{)[^}]*\$\{(?!\{)", script)]


def _script_events(script: str):
    """生成 `(位置, 优先级, kind, name)` 事件，按源码位置处理。

    priority：同一位置先处理 use，再处理 assignment，避免 RHS 引用被当前赋值提前满足。
    """
    events = []

    # `${VAR...}`：每一处引用独立判断。安全默认值不会保护后续同名引用。
    # 默认值 RHS 里的普通 `$Y` 由后面的 USE_PLAIN **照常收集**；不能把整个
    # `${...}` 范围屏蔽掉，否则 `${X:-$Y}` 会漏掉 Y、`${X:=$X}` 会漏掉 RHS 的 X。
    for m in BRACED_PARAM.finditer(script):
        name, tail = m.group(1), m.group(2)
        op = _safe_param_operator(tail)
        if op is None:
            events.append((m.start(), 0, "use", name))
        elif op in ASSIGNING_PARAM_OPS:
            # `${X:=v}` 当前引用本身受保护，并在展开完成后赋值
            events.append((m.end(), 1, "assign", name))

    # `$VAR`：只跳过位于 `${{...}}` Actions 表达式内部的片段。
    # 位于 `${X:-$Y}` 里的 `$Y` **不能跳过**：外层 `:-` 只保护 X，不保护 Y。
    expression_ranges = [(m.start(), m.end()) for m in
                         re.finditer(r"\$\{\{.*?\}\}", script, re.S)]
    for m in USE_PLAIN.finditer(script):
        pos = m.start()
        if any(a <= pos < b for a, b in expression_ranges):
            continue
        events.append((pos, 0, "use", m.group(1)))

    # 普通赋值在简单命令结束后才视为生效（RHS 可能引用同名变量）
    for m in ASSIGN_EVENT.finditer(script):
        events.append((_command_end(script, m.end()), 1, "assign", m.group(1)))

    # for 变量在 `do` 之后的循环体内可见
    for m in FOR_VAR.finditer(script):
        do = re.search(r"\bdo\b", script[m.end():])
        pos = m.end() + do.end() if do else m.end()
        events.append((pos, 1, "assign", m.group(1)))

    # read 目标在 read 命令完成后可见
    for m in READ_STMT.finditer(script):
        for name in m.group(2).split():
            events.append((m.end(), 1, "assign", name))

    return sorted(events, key=lambda x: (x[0], x[1], x[3]))


def _analyze_script(script: str, initially_visible):
    """返回 `(undefined, unsupported)`。

    undefined: `[(name, line)]`；unsupported: `[(construct, line)]`。
    """
    visible = set(initially_visible)
    undefined = []
    seen = set()
    for pos, _priority, kind, name in _script_events(script):
        if kind == "assign":
            visible.add(name)
            continue
        if name not in visible and name not in seen:
            line = script.count("\n", 0, pos) + 1
            undefined.append((name, line))
            seen.add(name)

    unsupported = []
    for pos in _unsupported_expansions(script):
        unsupported.append(("nested_parameter_expansion",
                            script.count("\n", 0, pos) + 1))
    return undefined, unsupported


def _undefined_uses(script: str, initially_visible):
    """兼容内部调用：返回未定义引用；未支持语法由 `_analyze_script` 单独返回。"""
    return _analyze_script(script, initially_visible)[0]


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
                # shell 引用按字符位置处理：未来赋值不能提前生效，默认值只保护当前引用。
                undefined, unsupported = _analyze_script(script, here)
                for name, line_no in undefined:
                    issues.append(Issue(
                        path, job_name, step_name, "ENV_USED_BEFORE_SET", name,
                        "第 {} 行读取，但在该词法位置之前既不在 workflow/job/step env，"
                        "也没有更早步骤写入 $GITHUB_ENV，本步骤此前也未赋值。"
                        "注意：本检查只保证线性词法顺序，不分析分支/子 shell/函数控制流".format(
                            line_no)))
                for construct, line_no in unsupported:
                    issues.append(Issue(
                        path, job_name, step_name, "ENV_EXPANSION_UNSUPPORTED", construct,
                        "第 {} 行包含嵌套参数展开 `${{...${{...}}...}}`，当前线性解析器"
                        "无法可靠配对大括号与判断 RHS 引用；必须用真实 shell 检查或"
                        "改写为受支持的普通 `$VAR` RHS，不能静默视为 PASS".format(line_no)))
                # `${{ env.X }}` 在 shell 执行前由 Actions 展开，同步骤 shell 赋值救不了它
                for name in sorted(expr_used):
                    if name not in here:
                        issues.append(Issue(
                            path, job_name, step_name, "ENV_EXPR_UNDEFINED", name,
                            "${{ env.%s }} 未定义 —— 会展开成空串并静默改变行为" % name))
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
    print("note: 只检查受支持语法中的线性词法先读后写；不分析分支支配、函数调用、"
          "子 shell/命令替换作用域、eval/source，也不替代真实故障路径执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

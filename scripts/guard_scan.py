#!/usr/bin/env python3
"""质量门槛变化扫描 —— 风险**提示器**，不是判定器（交付 R7）。

能识别什么:
    * 显式 skip / xfail / ignore 标记新增
    * 断言删除（diff 中减少的 assert / expect 行）
    * 阈值数字变化（覆盖率、超时、通过率等数值下调）
    * 忽略范围扩大（.gitignore / 排除规则 / noqa / type: ignore）
    * 重试次数或策略变化
    * 测试文件删除
    * 受保护路径被候选变更触及

**不能**识别什么（必须写在输出里，不得含糊）:
    * 断言语义是否被削弱（`assert x == 1` 改成 `assert x is not None` 仍是一条 assert）
    * 数据清理是否真的有效
    * 测试是否真的验证了它声称验证的行为

因此本工具输出一律标注"提示，非结论"。命中项要求说明与替代覆盖证据；未关闭则计入
reviewer 阻断项，由人判断，而不是由本工具判定 FAIL。

宁可误报，不承诺完整召回。误报率统计见 `--stats`。

用法:
    python3 scripts/guard_scan.py --diff-file patch.diff [--json]
    git diff target...candidate | python3 scripts/guard_scan.py --stdin
    python3 scripts/guard_scan.py --root . --diff-file patch.diff --with-waivers
"""
import argparse
import dataclasses
import hashlib
import json
import pathlib
import re
import sys
from typing import Dict, List, Optional

DISCLAIMER = (
    "提示，非结论。本工具不能判断断言语义是否被削弱、数据清理是否有效、"
    "测试是否真的验证了它声称验证的行为。命中项需人工确认说明与替代覆盖。"
)

# --- 规则定义 -------------------------------------------------------------

SKIP_PATTERNS = [
    (r"@(?:pytest\.mark\.)?skip\b", "pytest skip"),
    (r"@(?:pytest\.mark\.)?skipif\b", "pytest skipif"),
    (r"@(?:pytest\.mark\.)?xfail\b", "pytest xfail"),
    (r"\bpytest\.skip\s*\(", "运行时 pytest.skip()"),
    (r"@(?:unittest\.)?skip\b", "unittest skip"),
    (r"@Ignore\b", "JUnit @Ignore"),
    (r"@Disabled\b", "JUnit 5 @Disabled"),
    (r"\b(?:it|describe|test)\.skip\s*\(", "JS it/describe/test.skip"),
    (r"\bxit\s*\(|\bxdescribe\s*\(", "JS xit/xdescribe"),
    (r"\bt\.Skip\s*\(", "Go t.Skip()"),
    (r"#\s*\[ignore\]", "Rust #[ignore]"),
]

ASSERT_PATTERNS = [
    r"^\s*assert\b",
    r"^\s*self\.assert\w+\s*\(",
    r"^\s*expect\s*\(",
    r"^\s*assertThat\s*\(",
    r"^\s*\w+\.should\b",
    r"^\s*require\.\w+\s*\(",
    r"^\s*assert_\w+!\s*\(",
]

# 阈值型键名 → 数值下调即为风险
THRESHOLD_KEYS = [
    "fail_under", "fail-under", "min_coverage", "minCoverage", "coverage_threshold",
    "threshold", "max_attempts", "maxAttempts", "retries", "retry", "reruns",
    "timeout", "pass_rate", "passRate", "flaky_rate", "minInstructionCoverage",
    "max_failures", "maxFailures", "tolerance",
]
THRESHOLD_RE = re.compile(
    r"(?P<key>" + "|".join(re.escape(k) for k in THRESHOLD_KEYS) +
    r")\s*[:=]\s*(?P<val>-?\d+(?:\.\d+)?)", re.I
)

IGNORE_WIDENING_PATTERNS = [
    (r"#\s*noqa\b", "Python noqa"),
    (r"#\s*type:\s*ignore", "mypy type: ignore"),
    (r"//\s*eslint-disable", "eslint-disable"),
    (r"//\s*@ts-ignore|//\s*@ts-nocheck", "TypeScript ignore"),
    (r"#\s*pylint:\s*disable", "pylint disable"),
    (r"@SuppressWarnings\b", "Java SuppressWarnings"),
    (r"//\s*nolint", "Go nolint"),
    (r"#\s*pragma:\s*no cover", "coverage 排除"),
    (r"\ballow\s*\(\s*dead_code\s*\)", "Rust allow(dead_code)"),
]

RETRY_KEY_RE = re.compile(
    r"(reruns|retries|retry|max_attempts|maxAttempts|flaky|rerun_except|"
    r"accept_last_pass_without_explanation|allow_retry_to_clear_confirmed_blocking_defect)",
    re.I,
)

TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]+\.\w+$|_test\.\w+$|"
    r"\.spec\.\w+$|Test\.java$|Tests\.cs$"
)

IGNORE_FILE_RE = re.compile(r"(^|/)(\.gitignore|\.kiroignore|\.coveragerc|"
                            r"\.eslintignore|\.dockerignore)$")

# 受保护路径（与 CODEOWNERS / docs/protected-paths.md 对应）
PROTECTED_PREFIXES = [
    "qa/baseline/",
    "qa/plan/",
    "scripts/gate_check.py",
    "scripts/guard_scan.py",
    "scripts/trace_matrix.py",
    "scripts/validate_spec.py",
    "scripts/validate_agents.py",
    "CODEOWNERS",
    "Makefile",
    ".github/",
    ".gitlab-ci.yml",
    ".kiro/agents/",
    "steering/",
    ".doc/specs/",
    "docs/capability-matrix.md",
]


@dataclasses.dataclass
class Hint:
    code: str
    file: str
    detail: str
    evidence: str
    waived: bool = False
    waiver_reason: Optional[str] = None

    @property
    def hint_id(self):
        """稳定的逐项标识。

        仅按规则码确认是不够的：同一个 `SKIP_ADDED` 可能出现在多个文件里，
        用规则码去"确认"会一次关掉全部同类提示。标识取 (code, file, evidence)
        的摘要，保证逐项可寻址且跨次运行稳定。
        """
        payload = "|".join([self.code, self.file, self.evidence])
        return "{}:{}".format(
            self.code, hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12])

    def as_dict(self):
        out = dataclasses.asdict(self)
        out["hint_id"] = self.hint_id
        return out


@dataclasses.dataclass
class ScanResult:
    hints: List[Hint]
    files_scanned: int
    disclaimer: str = DISCLAIMER

    def as_dict(self):
        out = dataclasses.asdict(self)
        out["hints"] = [h.as_dict() for h in self.hints]
        out["open_hints"] = len(self.open_hints)
        out["waived_hints"] = len([h for h in self.hints if h.waived])
        return out

    @property
    def open_hints(self):
        return [h for h in self.hints if not h.waived]


# --- diff 解析 -------------------------------------------------------------


@dataclasses.dataclass
class FileDiff:
    path: str
    added: List[str]
    removed: List[str]
    is_deleted: bool = False
    is_new: bool = False


def parse_unified_diff(text):
    """解析 unified diff。返回 FileDiff 列表。

    只依赖 `---` / `+++` / `@@` 和 `+`/`-` 行，不依赖 git 扩展头，便于手工构造 fixture。

    路径解析规则（删除文件时 `+++` 是 `/dev/null`，路径必须取自同一文件的 `---` 行，
    而不是去全局回溯 —— 后者在多文件 diff 中会错归属）:

        --- a/X   +++ b/X          → 修改，path = X
        --- /dev/null  +++ b/X     → 新增，path = X
        --- a/X   +++ /dev/null    → 删除，path = X（取自 --- 行）
    """
    files = []
    cur = None
    old_path = None
    in_hunk = False

    def flush():
        if cur is not None and cur.path:
            files.append(cur)

    for line in text.splitlines():
        if line.startswith("diff --git"):
            flush()
            cur = None
            old_path = None
            in_hunk = False
            continue

        if line.startswith("--- "):
            # 新文件段开始
            flush()
            raw_old = line[4:].strip()
            old_path = None if raw_old == "/dev/null" else _strip_prefix(raw_old)
            cur = FileDiff(path="", added=[], removed=[], is_new=(old_path is None))
            in_hunk = False
            continue

        if line.startswith("+++ "):
            if cur is None:
                cur = FileDiff(path="", added=[], removed=[])
            raw_new = line[4:].strip()
            if raw_new == "/dev/null":
                cur.is_deleted = True
                cur.path = old_path or ""
            else:
                cur.path = _strip_prefix(raw_new)
            in_hunk = False
            continue

        if line.startswith("@@"):
            in_hunk = True
            continue

        if cur is None or not in_hunk:
            continue

        if line.startswith("+"):
            cur.added.append(line[1:])
        elif line.startswith("-"):
            cur.removed.append(line[1:])

    flush()
    return files


def _strip_prefix(path):
    for p in ("a/", "b/"):
        if path.startswith(p):
            return path[len(p):]
    return path


# --- 各规则 ---------------------------------------------------------------


def scan_skips(fd, hints):
    for line in fd.added:
        for pat, label in SKIP_PATTERNS:
            if re.search(pat, line):
                hints.append(Hint(
                    "SKIP_ADDED", fd.path,
                    "新增跳过标记（{}）—— 需说明原因与替代覆盖".format(label),
                    line.strip()[:160]))
                break


def scan_assertion_removal(fd, hints):
    # 文件被删除时，断言必然一起消失 —— 该事实已由 TEST_FILE_DELETED 覆盖。
    # 再报一条 ASSERTION_REMOVED 是对同一事实重复报警，只会增加噪音。
    if fd.is_deleted:
        return

    def count(lines):
        n = 0
        for line in lines:
            for pat in ASSERT_PATTERNS:
                if re.search(pat, line):
                    n += 1
                    break
        return n

    removed = count(fd.removed)
    added = count(fd.added)
    if removed > added:
        hints.append(Hint(
            "ASSERTION_REMOVED", fd.path,
            "断言行减少（删除 {} 条，新增 {} 条）—— 需说明是等价替换还是覆盖下降。"
            "注意：本工具只数行数，不判断语义强弱".format(removed, added),
            "assert_removed={} assert_added={}".format(removed, added)))


def scan_thresholds(fd, hints):
    def collect(lines):
        out = {}
        for line in lines:
            for m in THRESHOLD_RE.finditer(line):
                key = m.group("key").lower()
                try:
                    out.setdefault(key, []).append(float(m.group("val")))
                except ValueError:
                    continue
        return out

    old = collect(fd.removed)
    new = collect(fd.added)

    for key in set(old) & set(new):
        old_max = max(old[key])
        new_max = max(new[key])
        if new_max == old_max:
            continue
        # 覆盖率/通过率类下调是风险；重试/超时/容忍度类上调是风险
        looser_when_lower = any(k in key for k in
                                ("coverage", "threshold", "pass_rate", "passrate",
                                 "fail_under", "fail-under", "mininstruction"))
        looser = (new_max < old_max) if looser_when_lower else (new_max > old_max)
        hints.append(Hint(
            "THRESHOLD_CHANGED", fd.path,
            "阈值 {} 由 {} 改为 {}{} —— 需说明依据".format(
                key, old_max, new_max, "（放宽）" if looser else "（收紧）"),
            "{}: {} -> {}".format(key, old_max, new_max)))

    for key in set(new) - set(old):
        if any(k in key for k in ("retry", "rerun", "attempt", "flaky")):
            hints.append(Hint(
                "RETRY_POLICY_CHANGED", fd.path,
                "新增重试相关配置 {}={} —— 重试策略变更属需审查的质量规则变化".format(
                    key, max(new[key])),
                "{}={}".format(key, max(new[key]))))


def scan_ignore_widening(fd, hints):
    for line in fd.added:
        for pat, label in IGNORE_WIDENING_PATTERNS:
            if re.search(pat, line):
                hints.append(Hint(
                    "IGNORE_WIDENED", fd.path,
                    "新增忽略/抑制标记（{}）—— 需说明范围与原因".format(label),
                    line.strip()[:160]))
                break

    if IGNORE_FILE_RE.search(fd.path):
        meaningful = [l for l in fd.added if l.strip() and not l.strip().startswith("#")]
        if meaningful:
            hints.append(Hint(
                "IGNORE_FILE_WIDENED", fd.path,
                "忽略文件新增 {} 条规则 —— 可能把失败或未覆盖代码排除在检查之外".format(
                    len(meaningful)),
                "; ".join(x.strip() for x in meaningful[:5])[:200]))


def scan_retry_keys(fd, hints):
    for line in fd.added:
        if RETRY_KEY_RE.search(line) and not THRESHOLD_RE.search(line):
            hints.append(Hint(
                "RETRY_POLICY_CHANGED", fd.path,
                "触及重试/间歇失败相关配置 —— 属需审查的质量规则变化",
                line.strip()[:160]))
            break


def scan_test_deletion(fd, hints):
    if fd.is_deleted and TEST_PATH_RE.search(fd.path):
        hints.append(Hint(
            "TEST_FILE_DELETED", fd.path,
            "测试文件被删除 —— 需说明是重复项清理还是覆盖下降，并提供替代覆盖证据",
            "file deleted"))


def scan_protected_paths(fd, hints):
    for prefix in PROTECTED_PREFIXES:
        if fd.path == prefix or fd.path.startswith(prefix):
            hints.append(Hint(
                "PROTECTED_PATH_TOUCHED", fd.path,
                "候选变更触及受保护路径（{}）—— 需 CODEOWNERS 审批；"
                "门禁规则的参照版本取自 target_sha，本次修改不对自身生效".format(prefix),
                "protected prefix: {}".format(prefix)))
            break


RULES = [
    scan_skips,
    scan_assertion_removal,
    scan_thresholds,
    scan_ignore_widening,
    scan_retry_keys,
    scan_test_deletion,
    scan_protected_paths,
]


# --- 豁免 -----------------------------------------------------------------


def load_waivers(root):
    """豁免声明。放在受保护路径下，需 CODEOWNERS 审批才能新增。

    格式（qa/plan/guard-waivers.yaml）:
        waivers:
          - code: SKIP_ADDED
            file: tests/test_x.py
            reason: "上游接口下线，已由 TC-20 手工用例替代覆盖"
            alternative_coverage: "TC-20"
    """
    path = root / "qa" / "plan" / "guard-waivers.yaml"
    if not path.is_file():
        return []
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return data.get("waivers") or []


def apply_waivers(hints, waivers):
    for h in hints:
        for w in waivers:
            if w.get("code") != h.code:
                continue
            wf = w.get("file")
            if wf and wf != h.file:
                continue
            # 豁免必须同时给出原因与替代覆盖，否则不生效
            if w.get("reason") and w.get("alternative_coverage"):
                h.waived = True
                h.waiver_reason = "{}（替代覆盖：{}）".format(
                    w["reason"], w["alternative_coverage"])
                break
    return hints


# --- 入口 -----------------------------------------------------------------


def scan_diff(diff_text, root=None, with_waivers=False):
    files = parse_unified_diff(diff_text)
    hints = []  # type: List[Hint]
    for fd in files:
        for rule in RULES:
            rule(fd, hints)

    if with_waivers and root is not None:
        apply_waivers(hints, load_waivers(pathlib.Path(root)))

    return ScanResult(hints=hints, files_scanned=len(files))


def to_gate_findings(result):
    """转成 gate_check 的 Finding，供直接持有 ScanResult 的调用方使用。

    全部为 HINT —— 本工具不产生判定。未关闭的提示项应由 reviewer 处理，
    reviewer 的阻断意见才通过 review-verdict 进入门禁判定。

    注意：`gate_check --diff-file` 走的是 `gate_check.check_guard_hints()`，
    它额外负责"未关闭提示 → GUARD_HINTS_UNREVIEWED"这一步。两处都必须产出 HINT，
    各有测试约束（`test_gate_findings_are_always_hints` /
    `test_guard_hints_never_produce_quality_failure`）。
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import gate_check as gc

    out = []
    for h in result.open_hints:
        out.append(gc.Finding(
            code="GUARD_" + h.code,
            kind="HINT",
            subject=h.file,
            detail="{} | {}".format(h.detail, DISCLAIMER),
        ))
    return out


def render_text(result):
    lines = ["=== guard_scan（质量门槛变化提示） ==="]
    lines.append("扫描文件 {} 个；提示 {} 条（未豁免 {} 条）".format(
        result.files_scanned, len(result.hints), len(result.open_hints)))
    if not result.hints:
        lines.append("无提示项")
    for h in result.hints:
        tag = "WAIVED" if h.waived else "OPEN"
        lines.append("  [{}] {} · {}".format(tag, h.code, h.file))
        lines.append("        {}".format(h.detail))
        lines.append("        证据: {}".format(h.evidence))
        if h.waived:
            lines.append("        豁免: {}".format(h.waiver_reason))
    lines.append("")
    lines.append("声明: {}".format(DISCLAIMER))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="质量门槛变化扫描（提示器，非判定器）")
    ap.add_argument("--root", default=".")
    ap.add_argument("--diff-file", default=None)
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--with-waivers", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stats", action="store_true", help="只输出计数")
    args = ap.parse_args()

    if args.stdin:
        diff_text = sys.stdin.read()
    elif args.diff_file:
        p = pathlib.Path(args.diff_file)
        if not p.is_file():
            print("FATAL: diff 文件不存在: {}".format(p), file=sys.stderr)
            return 2
        diff_text = p.read_text(encoding="utf-8", errors="replace")
    else:
        print("需要 --diff-file 或 --stdin", file=sys.stderr)
        return 2

    result = scan_diff(diff_text, root=args.root, with_waivers=args.with_waivers)

    if args.stats:
        print(json.dumps({
            "files_scanned": result.files_scanned,
            "hints": len(result.hints),
            "open_hints": len(result.open_hints),
        }, indent=2, ensure_ascii=False))
    elif args.json:
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(result))

    # 提示器**不**以非零码阻断 —— 判定归 gate_check 与 reviewer
    return 0


if __name__ == "__main__":
    sys.exit(main())

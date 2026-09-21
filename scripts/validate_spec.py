#!/usr/bin/env python3
"""结构校验器 —— spec 三件套 + 试点前基线 + YAML 模板。

定位：这是**结构检查**，只证明格式合规。它不证明需求完整、设计正确或预期无误。
不要把本脚本的通过表述为内容质量的证据（见 design.md 的 Testing Strategy）。

用法:
    python3 scripts/validate_spec.py [--root .] [--json]

退出码:
    0  全部检查通过
    1  存在校验失败
    2  用法/环境错误
"""
import argparse
import json
import pathlib
import re
import sys
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺依赖时给出可操作提示
    print("FATAL: 需要 PyYAML。系统 python3 应已自带；请确认使用的是含 PyYAML 的解释器。",
          file=sys.stderr)
    sys.exit(2)

SPEC_DIR = pathlib.Path(".doc/specs/qa-agents")
BASELINE_FILE = pathlib.Path("qa/metrics/pilot-baseline.yaml")
TEMPLATE_YAMLS = [
    pathlib.Path("templates/requirements.yaml"),
    pathlib.Path("templates/required-scope.yaml"),
]

REQUIRED_IDS = ["R{}".format(i) for i in range(1, 10)]

# EARS 关键字。验收标准必须用这些词表达可判定的条件与义务。
EARS_CONDITION = ["WHEN", "IF", "WHILE", "WHERE"]
EARS_OBLIGATION = ["SHALL"]

DESIGN_SECTIONS = [
    "Overview",
    "Architecture",
    "Sequence",
    "Component",
    "Constraints",
    "Testing Strategy",
]

# tasks.md 中合法的状态标记
TASK_MARKERS = {" ", "~", "x", "!", "-"}
TASK_LINE_RE = re.compile(r"^\s*-\s*\[(.)\]\s+\S")
# 看起来是任务项但没有状态标记的行（顶层列表项，排除表格与引用）
BARE_TASK_RE = re.compile(r"^-\s+(?!\[)\S")

METRIC_STATUSES = {"MEASURED", "MISSING"}


class Result:
    def __init__(self):
        self.failures = []  # type: List[Dict[str, str]]
        self.checks = 0

    def check(self, ok, code, detail):
        self.checks += 1
        if not ok:
            self.failures.append({"code": code, "detail": detail})
        return ok

    @property
    def ok(self):
        return not self.failures


def read(path):
    # type: (pathlib.Path) -> Optional[str]
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def validate_requirements(root, res):
    path = root / SPEC_DIR / "requirements.md"
    text = read(path)
    if not res.check(text is not None, "SPEC_MISSING",
                     "缺少 {}".format(path)):
        return

    for rid in REQUIRED_IDS:
        res.check(re.search(r"^##\s+" + rid + r"\b", text, re.M) is not None,
                  "REQ_ID_MISSING",
                  "requirements.md 缺少需求条目标题 '## {}'".format(rid))

    res.check("User Story" in text, "USER_STORY_MISSING",
              "requirements.md 未出现 User Story 段落")

    for kw in EARS_CONDITION:
        res.check(re.search(r"\*\*" + kw + r"\*\*|\b" + kw + r"\b", text) is not None,
                  "EARS_CONDITION_MISSING",
                  "requirements.md 未出现 EARS 条件关键字 {}".format(kw))
    for kw in EARS_OBLIGATION:
        res.check(kw in text, "EARS_OBLIGATION_MISSING",
                  "requirements.md 未出现 EARS 义务关键字 {}".format(kw))

    res.check("SHALL NOT" in text, "EARS_NEGATIVE_MISSING",
              "requirements.md 未出现任何 SHALL NOT —— 缺少禁止性验收标准")

    # 六条反例验收必须在需求中落地，否则 R7/R6 无从验证
    res.check("反例验收" in text, "COUNTEREXAMPLE_SECTION_MISSING",
              "requirements.md 缺少反例验收章节")


def validate_design(root, res):
    path = root / SPEC_DIR / "design.md"
    text = read(path)
    if not res.check(text is not None, "SPEC_MISSING",
                     "缺少 {}".format(path)):
        return

    for section in DESIGN_SECTIONS:
        res.check(re.search(r"^#{1,3}\s+.*" + re.escape(section), text, re.M | re.I) is not None,
                  "DESIGN_SECTION_MISSING",
                  "design.md 缺少必备章节 '{}'".format(section))

    res.check("```mermaid" in text, "DESIGN_DIAGRAM_MISSING",
              "design.md 缺少图（Architecture / Sequence 至少一张 mermaid）")

    res.check("残余风险" in text, "RESIDUAL_RISK_MISSING",
              "design.md 未载明残余风险（溯源分离防不住弱化断言）")


def validate_tasks(root, res):
    path = root / SPEC_DIR / "tasks.md"
    text = read(path)
    if not res.check(text is not None, "SPEC_MISSING",
                     "缺少 {}".format(path)):
        return

    lines = text.splitlines()
    task_count = 0
    in_code = False
    for idx, line in enumerate(lines, start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        m = TASK_LINE_RE.match(line)
        if m:
            task_count += 1
            marker = m.group(1)
            res.check(marker in TASK_MARKERS, "TASK_MARKER_INVALID",
                      "tasks.md:{} 状态标记 '[{}]' 非法，合法值 {}".format(
                          idx, marker, sorted(TASK_MARKERS)))
            continue

        if BARE_TASK_RE.match(line):
            res.check(False, "TASK_MARKER_MISSING",
                      "tasks.md:{} 顶层列表项缺少状态标记: {}".format(idx, line.strip()[:60]))

    res.check(task_count > 0, "TASK_EMPTY", "tasks.md 未包含任何带状态标记的任务项")

    res.check("唯一任务状态来源" in text, "TASK_AUTHORITY_MISSING",
              "tasks.md 未声明自身为唯一任务状态来源")


def _walk_metrics(node, path_parts, res):
    """递归找出所有含 status 字段的度量项并校验其完整性。

    注意：`meta.status` 用的是 COMPLETE/INCOMPLETE，与度量项的 MEASURED/MISSING
    是不同的取值域，必须跳过，否则会把整份基线的汇总状态误判为度量项。
    """
    if isinstance(node, dict):
        if path_parts == ["meta"]:
            for key, val in node.items():
                _walk_metrics(val, path_parts + [str(key)], res)
            return
        if "status" in node:
            where = ".".join(path_parts) or "<root>"
            status = node.get("status")
            res.check(status in METRIC_STATUSES, "BASELINE_STATUS_INVALID",
                      "{}: status='{}' 非法，合法值 {}".format(
                          where, status, sorted(METRIC_STATUSES)))

            if status == "MISSING":
                reason = node.get("reason")
                res.check(bool(reason and str(reason).strip()),
                          "BASELINE_MISSING_WITHOUT_REASON",
                          "{}: status=MISSING 但缺少 reason —— R9 要求显式说明为什么采不到".format(where))
                if "value" in node:
                    res.check(node.get("value") is None,
                              "BASELINE_MISSING_WITH_VALUE",
                              "{}: status=MISSING 但 value 非 null（不得以 0 或占位值填充）".format(where))
            elif status == "MEASURED":
                res.check(node.get("value") is not None,
                          "BASELINE_MEASURED_WITHOUT_VALUE",
                          "{}: status=MEASURED 但 value 为空".format(where))
                res.check(bool(node.get("source")),
                          "BASELINE_MEASURED_WITHOUT_SOURCE",
                          "{}: status=MEASURED 但缺少 source".format(where))
            return  # 度量项内部不再递归

        for key, val in node.items():
            _walk_metrics(val, path_parts + [str(key)], res)
    elif isinstance(node, list):
        for i, val in enumerate(node):
            _walk_metrics(val, path_parts + ["[{}]".format(i)], res)


def validate_baseline(root, res):
    path = root / BASELINE_FILE
    text = read(path)
    if not res.check(text is not None, "BASELINE_MISSING",
                     "缺少试点前基线 {} —— R9 要求基线先于试点采集".format(path)):
        return

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        res.check(False, "BASELINE_YAML_INVALID",
                  "{} 不是合法 YAML: {}".format(path, exc))
        return

    if not res.check(isinstance(data, dict), "BASELINE_SHAPE_INVALID",
                     "{} 顶层必须是映射".format(path)):
        return

    meta = data.get("meta") or {}
    res.check(isinstance(meta, dict) and meta.get("status") in {"COMPLETE", "INCOMPLETE"},
              "BASELINE_META_STATUS_INVALID",
              "meta.status 必须为 COMPLETE 或 INCOMPLETE")

    for field in ("schema_version", "collected_at", "collected_by"):
        res.check(bool(meta.get(field)), "BASELINE_META_FIELD_MISSING",
                  "meta.{} 缺失".format(field))

    _walk_metrics(data, [], res)

    # 一致性：存在任一 MISSING 度量项时，meta.status 不得为 COMPLETE
    missing_found = _has_missing(data)
    if missing_found and isinstance(meta, dict):
        res.check(meta.get("status") != "COMPLETE",
                  "BASELINE_STATUS_INCONSISTENT",
                  "存在 status=MISSING 的度量项，meta.status 不得为 COMPLETE")


def _has_missing(node):
    if isinstance(node, dict):
        if node.get("status") == "MISSING":
            return True
        return any(_has_missing(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_missing(v) for v in node)
    return False


def validate_templates(root, res):
    for rel in TEMPLATE_YAMLS:
        path = root / rel
        text = read(path)
        if not res.check(text is not None, "TEMPLATE_MISSING",
                         "缺少模板 {}".format(path)):
            continue
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            res.check(False, "TEMPLATE_YAML_INVALID",
                      "模板 {} 不是合法 YAML（拷贝后无法使用）: {}".format(path, exc))


def validate_protected_paths(root, res):
    """CODEOWNERS 是可信计算基的强制点，缺关键条目等于保护失效。"""
    path = root / "CODEOWNERS"
    text = read(path)
    if not res.check(text is not None, "CODEOWNERS_MISSING",
                     "缺少 CODEOWNERS —— 可信计算基失去强制点"):
        return

    must_cover = [
        "/qa/baseline/",
        "/qa/plan/",
        "/scripts/gate_check.py",
        "/scripts/guard_scan.py",
        "/scripts/trace_matrix.py",
        "/CODEOWNERS",
        # 改 rubric 等于改验收口径；改反例 fixture 可让"知道自己做不了"这条检查失效
        "/evals/rubrics/",
        "/evals/fixtures/",
    ]
    for entry in must_cover:
        res.check(entry in text, "CODEOWNERS_ENTRY_MISSING",
                  "CODEOWNERS 未覆盖受保护路径 {}".format(entry))


def run(root):
    res = Result()
    validate_requirements(root, res)
    validate_design(root, res)
    validate_tasks(root, res)
    validate_baseline(root, res)
    validate_templates(root, res)
    validate_protected_paths(root, res)
    return res


def main():
    ap = argparse.ArgumentParser(description="spec 三件套与基线的结构校验")
    ap.add_argument("--root", default=".", help="仓库根目录")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    res = run(root)

    payload = {
        "root": str(root),
        "checks_run": res.checks,
        "failures": res.failures,
        "verdict": "PASS" if res.ok else "FAIL",
        "note": "结构检查只证明格式合规，不证明需求完整或预期正确。",
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("root: {}".format(root))
        print("checks run: {}".format(res.checks))
        if res.ok:
            print("verdict: PASS")
        else:
            print("verdict: FAIL ({} 项)".format(len(res.failures)))
            for f in res.failures:
                print("  [{}] {}".format(f["code"], f["detail"]))
        print("note: {}".format(payload["note"]))

    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())

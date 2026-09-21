#!/usr/bin/env python3
"""场景评测执行器。

**本脚本不调用模型。** 它只做两件确定性的事：

  1. 对已产出的用例文件跑结构检查（rubric 里 judge=structural 的条目）
  2. 把 judge=human 的条目展开成待人工填写的清单

调用模型、收集输出由执行者完成。这样评测脚本本身是确定性的、可单测的 ——
否则"评测脚本"自己就成了不可验证的东西。

**报告把结构结果与人工结果分列，不合成总分。** 结构检查全过只证明格式合规，
不证明需求覆盖完整或预期正确。把前者表述成后者是本项目明确禁止的。

用法:
    python3 evals/run_evals.py --list
    python3 evals/run_evals.py --fixture boundary-range --cases path/to/cases.md \\
        --model <标识> --agent-config-version <版本>
    python3 evals/run_evals.py --fixture boundary-range --cases c.md --json
"""
import argparse
import json
import pathlib
import re
import sys
import time
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FATAL: 需要 PyYAML（系统 python3 应已自带）", file=sys.stderr)
    sys.exit(2)

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "fixtures"
RUBRIC_DIR = HERE / "rubrics"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

# 模糊词表：出现即视为预期不可机器校验（对应 rubric S2）
VAGUE_TERMS = [
    "正常显示", "功能可用", "体验流畅", "符合预期", "无异常", "正确显示",
    "正常工作", "运行正常", "表现良好", "没有问题", "正常返回",
]

# 边界标记的识别模式。用例文本里出现任一形式即算命中。
BOUNDARY_PATTERNS = {
    "min-1": [r"min\s*-\s*1", r"最小值\s*-\s*1", r"下边界外", r"小于最小"],
    "min": [r"\bmin\b", r"最小值", r"下边界"],
    "max": [r"\bmax\b", r"最大值", r"上边界"],
    "max+1": [r"max\s*\+\s*1", r"最大值\s*\+\s*1", r"上边界外", r"超过最大", r"超长"],
    "illegal-type": [r"非法类型", r"类型非法", r"非字符串", r"类型错误", r"TYPE_INVALID"],
}

REQUIRED_CASE_FIELDS = ["manual", "layer", "priority", "method"]


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------


def parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return yaml.safe_load(m.group(1)), text[m.end():]


def load_fixture(fixture_id):
    path = FIXTURE_DIR / "{}.md".format(fixture_id)
    if not path.is_file():
        raise FileNotFoundError("fixture 不存在: {}".format(path))
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not fm:
        raise ValueError("fixture 缺少 frontmatter: {}".format(path))
    return fm, body, path


def load_rubric(name="qa-design"):
    path = RUBRIC_DIR / "{}.yaml".format(name)
    if not path.is_file():
        raise FileNotFoundError("rubric 不存在: {}".format(path))
    return yaml.safe_load(path.read_text(encoding="utf-8")), path


def load_cases(cases_path):
    """载入 qa-design 产出的用例文件。返回 (frontmatter, 正文)。"""
    path = pathlib.Path(cases_path)
    if not path.is_file():
        raise FileNotFoundError("用例文件不存在: {}".format(path))
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return fm or {}, body, path


def extract_blocking_items(body):
    """从产出正文里识别阻塞项。

    约定：阻塞项以 `- [阻塞]` 或 `**阻塞项**` 标记，或出现在「阻塞项」小节下。
    这是格式约定，能否算真正的阻塞理由由 rubric H2 人工判断。
    """
    items = []
    in_section = False
    for line in body.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,4}\s*.*阻塞", stripped):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,4}\s", stripped):
            in_section = False
        if re.search(r"\[阻塞\]|\*\*阻塞项\*\*", stripped):
            items.append(stripped)
        elif in_section and stripped.startswith(("-", "*", "1.")):
            items.append(stripped)
    return items


# ---------------------------------------------------------------------------
# 结构检查
# ---------------------------------------------------------------------------


class Check:
    def __init__(self, cid, passed, detail):
        self.cid = cid
        self.passed = passed
        self.detail = detail

    def as_dict(self):
        return {"id": self.cid, "passed": self.passed, "detail": self.detail}


def check_s1_covers(fixture_body, cases_fm, results):
    """每条用例声明 covers，且引用的 REQ 存在于 fixture 中。"""
    fixture_reqs = set(re.findall(r"\b(REQ-[A-Za-z0-9_.-]+)", fixture_body))
    entries = cases_fm.get("cases") or []
    if not entries:
        results.append(Check("S1", False, "用例 frontmatter 中没有 cases 条目"))
        return
    problems = []
    for e in entries:
        tid = e.get("id", "<无 id>")
        covers = e.get("covers") or []
        if isinstance(covers, str):
            covers = [covers]
        if not covers:
            problems.append("{} 未声明 covers".format(tid))
            continue
        for c in covers:
            if str(c) not in fixture_reqs:
                problems.append("{} 的 covers 引用了 fixture 中不存在的 {}".format(tid, c))
    results.append(Check("S1", not problems,
                         "；".join(problems) if problems
                         else "{} 条用例的 covers 均有效".format(len(entries))))


def check_s2_vague(body, results):
    hits = []
    for line_no, line in enumerate(body.splitlines(), start=1):
        for term in VAGUE_TERMS:
            if term in line:
                hits.append("第 {} 行出现「{}」".format(line_no, term))
    results.append(Check("S2", not hits,
                         "；".join(hits[:8]) if hits else "未发现模糊词"))


def check_s3_boundaries(fixture_fm, body, results):
    required = (fixture_fm.get("expect") or {}).get("required_boundaries") or []
    if not required:
        results.append(Check("S3", True, "fixture 未声明边界要求，跳过"))
        return
    missing = []
    for name in required:
        pats = BOUNDARY_PATTERNS.get(name, [re.escape(name)])
        if not any(re.search(p, body, re.I) for p in pats):
            missing.append(name)
    results.append(Check("S3", not missing,
                         "缺少边界：{}".format(missing) if missing
                         else "声明的 {} 个边界全部出现".format(len(required))))


def check_s4_fields(cases_fm, results):
    entries = cases_fm.get("cases") or []
    if not entries:
        results.append(Check("S4", False, "无 cases 条目可检查"))
        return
    problems = []
    for e in entries:
        tid = e.get("id", "<无 id>")
        for field in REQUIRED_CASE_FIELDS:
            if field not in e:
                problems.append("{} 缺 {}".format(tid, field))
    results.append(Check("S4", not problems,
                         "；".join(problems[:8]) if problems
                         else "全部用例字段完整"))


def check_s5_blocking_count(fixture_fm, body, results):
    expect = fixture_fm.get("expect") or {}
    found = len(extract_blocking_items(body))

    lo = expect.get("blocking_items_min")
    hi = expect.get("blocking_items_max")
    exact = expect.get("blocking_items")

    if exact is not None:
        ok = found == exact
        detail = "期望 {} 个，实际 {} 个".format(exact, found)
    elif lo is not None or hi is not None:
        ok = (lo is None or found >= lo) and (hi is None or found <= hi)
        detail = "期望区间 [{}, {}]，实际 {} 个".format(
            lo if lo is not None else "-", hi if hi is not None else "-", found)
    else:
        results.append(Check("S5", True, "fixture 未声明阻塞项期望，跳过"))
        return

    results.append(Check("S5", ok, detail))


def run_structural(fixture_fm, fixture_body, cases_fm, cases_body):
    results = []  # type: List[Check]
    check_s1_covers(fixture_body, cases_fm, results)
    check_s2_vague(cases_body, results)
    check_s3_boundaries(fixture_fm, cases_body, results)
    check_s4_fields(cases_fm, results)
    check_s5_blocking_count(fixture_fm, cases_body, results)
    return results


# ---------------------------------------------------------------------------
# 人工清单
# ---------------------------------------------------------------------------


def build_human_checklist(rubric):
    out = []
    for c in rubric.get("criteria") or []:
        if c.get("judge") != "human":
            continue
        out.append({
            "id": c["id"],
            "weight": c.get("weight"),
            "statement": c.get("statement"),
            "guidance": c.get("guidance"),
            "verdict": None,          # 待人工填写：pass | fail
            "evidence": None,         # 待人工填写：具体依据
        })
    return out


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def build_report(fixture_id, fixture_fm, structural, human, meta, rubric):
    blocking_failed = []
    for c in structural:
        spec = next((x for x in (rubric.get("criteria") or []) if x["id"] == c.cid), {})
        if not c.passed and spec.get("weight") == "blocking":
            blocking_failed.append(c.cid)

    return {
        "fixture": fixture_id,
        "fixture_kind": fixture_fm.get("kind"),
        "meta": meta,
        "rubric_version": (rubric.get("meta") or {}).get("rubric_version"),
        # 两类结果分列，**不合成总分**
        "structural": {
            "results": [c.as_dict() for c in structural],
            "passed": sum(1 for c in structural if c.passed),
            "total": len(structural),
            "blocking_failed": blocking_failed,
            "proves": "格式合规",
            "does_not_prove": "需求覆盖完整、预期正确、用例质量达标",
        },
        "human": {
            "checklist": human,
            "status": "PENDING" if any(i["verdict"] is None for i in human) else "FILLED",
            "note": "未填写完成前，本次评测不得表述为通过",
        },
        "error_accounting": rubric.get("error_accounting"),
    }


def render_text(report):
    lines = []
    lines.append("=== 场景评测：{} （{}）===".format(
        report["fixture"], report["fixture_kind"]))
    m = report["meta"]
    lines.append("模型 {} / agent 配置 {} / 提示词 {} / rubric {}".format(
        m.get("model") or "<未记录>",
        m.get("agent_config_version") or "<未记录>",
        m.get("prompt_version") or "<未记录>",
        report.get("rubric_version")))
    if not m.get("model") or not m.get("agent_config_version"):
        lines.append("  ⚠ 模型或配置版本未记录 —— 本次结果不可与其他运行比较")

    s = report["structural"]
    lines.append("")
    lines.append("--- 结构检查（自动）{}/{} ---".format(s["passed"], s["total"]))
    for r in s["results"]:
        lines.append("  [{}] {} — {}".format(
            "PASS" if r["passed"] else "FAIL", r["id"], r["detail"]))
    if s["blocking_failed"]:
        lines.append("  阻断级未通过：{}".format(s["blocking_failed"]))
    lines.append("  证明：{}".format(s["proves"]))
    lines.append("  **不**证明：{}".format(s["does_not_prove"]))

    h = report["human"]
    lines.append("")
    lines.append("--- 人工评分（待填）状态 {} ---".format(h["status"]))
    for i in h["checklist"]:
        lines.append("  [{}] {} · {}".format(
            i["verdict"] or "待填", i["id"], i["statement"]))
        if i.get("guidance"):
            lines.append("        判断方法：{}".format(i["guidance"].strip().splitlines()[0]))
    lines.append("  {}".format(h["note"]))

    lines.append("")
    lines.append("结构与人工两部分分列呈现，不合成总分。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def list_fixtures():
    rows = []
    for p in sorted(FIXTURE_DIR.glob("*.md")):
        fm, _body = parse_frontmatter(p.read_text(encoding="utf-8"))
        if not fm:
            continue
        rows.append((fm.get("fixture_id", p.stem), fm.get("kind", "?"),
                     (fm.get("expect") or {})))
    return rows


def main():
    ap = argparse.ArgumentParser(description="场景评测：结构检查 + 人工清单")
    ap.add_argument("--list", action="store_true", help="列出 fixture")
    ap.add_argument("--fixture", default=None)
    ap.add_argument("--cases", default=None, help="qa-design 产出的用例文件")
    ap.add_argument("--rubric", default="qa-design")
    ap.add_argument("--model", default=None)
    ap.add_argument("--agent-config-version", default=None)
    ap.add_argument("--prompt-version", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="写出报告 JSON 的路径")
    args = ap.parse_args()

    if args.list:
        rows = list_fixtures()
        if args.json:
            print(json.dumps([{"fixture_id": a, "kind": b, "expect": c}
                              for a, b, c in rows], indent=2, ensure_ascii=False))
        else:
            print("可用 fixture：")
            for fid, kind, expect in rows:
                print("  {:24s} {:9s} {}".format(fid, kind, json.dumps(
                    expect, ensure_ascii=False)))
            print("\nrubric：{}".format(
                [p.stem for p in sorted(RUBRIC_DIR.glob('*.yaml'))]))
        return 0

    if not args.fixture:
        print("需要 --fixture（或 --list）", file=sys.stderr)
        return 2

    fixture_fm, fixture_body, _fp = load_fixture(args.fixture)
    rubric, _rp = load_rubric(args.rubric)

    if not args.cases:
        # 没有产出文件时，只打印该 fixture 的期望与人工清单，便于执行前准备
        human = build_human_checklist(rubric)
        payload = {
            "fixture": args.fixture,
            "fixture_kind": fixture_fm.get("kind"),
            "expect": fixture_fm.get("expect"),
            "human_checklist": human,
            "note": "未提供 --cases，仅输出期望与人工清单；结构检查需要产出文件",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json
              else json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    cases_fm, cases_body, _cp = load_cases(args.cases)
    structural = run_structural(fixture_fm, fixture_body, cases_fm, cases_body)
    human = build_human_checklist(rubric)
    meta = {
        "model": args.model,
        "agent_config_version": args.agent_config_version,
        "prompt_version": args.prompt_version,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    report = build_report(args.fixture, fixture_fm, structural, human, meta, rubric)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))

    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                       encoding="utf-8")

    # 结构检查有阻断级失败 → 非零退出。人工部分待填**不**算通过，也不算失败，
    # 由执行者填完后另行判定。
    return 1 if report["structural"]["blocking_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

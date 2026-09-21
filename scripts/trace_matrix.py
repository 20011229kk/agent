#!/usr/bin/env python3
"""追溯矩阵 —— 派生产物，无写入者。

数据流（每一层都有唯一写入者，矩阵本身谁都不写）:

    REQ  (qa/baseline/<f>/requirements.yaml   由人确认)
     └─ TC  (qa/cases/<f>/*.md frontmatter    由 qa-design 写)
         └─ AT  (collect 清单                  由测试框架产出)
             └─ attempt (run.json             由框架/CI 产出)

设计要点（对应 R4）:

* 覆盖率**分母来自基线**，不来自矩阵。矩阵少收录基线条目 → 报缺口，不会算出"全覆盖"。
* **已自动化只认实际 collect 到的测试**。没有 collect 清单时，状态是
  `AUTOMATION_EVIDENCE_ABSENT`（证据缺失），**不是** 0% 已自动化 —— 二者含义不同，
  混同就是另一种形式的"假数据"。
* `manual: true` 的 TC 不进可自动化分母，**但仍进必测范围校验**。
* 手工用例无 AT、通过用例无缺陷，都**不判错**。
* 基线内容哈希变更 → 关联运行证据标 STALE。

用法:
    python3 scripts/trace_matrix.py --root . [--feature F] [--json] [--fail-on-gap]
    python3 scripts/trace_matrix.py --update-hash --feature F     # 计算并写入基线 content_hash
"""
import argparse
import csv
import dataclasses
import hashlib
import json
import pathlib
import re
import sys
from typing import Dict, List, Optional, Set

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FATAL: 需要 PyYAML（系统 python3 应已自带）", file=sys.stderr)
    sys.exit(2)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
CASE_MARKER_RE = re.compile(r"@case\(\s*['\"](TC-[A-Za-z0-9_.-]+)['\"]\s*\)")

VALID_ROOT_CAUSES = {"UNKNOWN", "ENV", "DATA", "SCRIPT", "PRODUCT", "DEPENDENCY"}
VALID_STABILITY = {"稳定失败", "间歇失败", "未确认"}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Gap:
    code: str
    severity: str          # BLOCKING | GAP | HINT
    subject: str
    detail: str

    def as_dict(self):
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Requirement:
    id: str
    statement: str
    blocking: bool
    risk: str


@dataclasses.dataclass
class Case:
    id: str
    covers: List[str]
    manual: bool
    source: str
    baseline_version: Optional[str] = None


@dataclasses.dataclass
class CollectedTest:
    node_id: str
    cases: List[str]
    params: Optional[str] = None


@dataclasses.dataclass
class Attempt:
    node_id: str
    attempt: int
    status: str            # passed | failed | error | skipped
    params: Optional[str] = None


@dataclasses.dataclass
class TraceReport:
    feature: str
    baseline_version: Optional[str]
    baseline_hash_current: Optional[str]
    automation_evidence: str            # PRESENT | AUTOMATION_EVIDENCE_ABSENT
    run_evidence: str                   # PRESENT | RUN_EVIDENCE_ABSENT
    stale: bool
    counts: Dict[str, int]
    per_requirement: Dict[str, Dict[str, object]]
    per_case: Dict[str, Dict[str, object]]
    gaps: List[Gap]
    # 唯一有效执行范围：required_cases ∪ 必测 REQ 展开出的 TC。
    # 门禁只认这一份，避免两份范围清单各判一半。
    effective_required_cases: List[str] = dataclasses.field(default_factory=list)
    # 运行记录与必测范围的原始 meta，交给门禁做版本交叉核对。
    run_meta: Dict[str, object] = dataclasses.field(default_factory=dict)
    scope_meta: Dict[str, object] = dataclasses.field(default_factory=dict)

    def as_dict(self):
        out = dataclasses.asdict(self)
        out["gaps"] = [g.as_dict() for g in self.gaps]
        return out

    @property
    def has_blocking(self):
        return any(g.severity == "BLOCKING" for g in self.gaps)

    @property
    def has_gap(self):
        return any(g.severity in ("BLOCKING", "GAP") for g in self.gaps)


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------


def _parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    return yaml.safe_load(m.group(1))


def canonical_baseline_payload(data):
    """用于计算 content_hash 的规范化载荷。

    只覆盖会影响验收口径的内容（requirements 列表 + baseline_version），
    **排除 content_hash 自身**，否则无法自指计算。
    """
    meta = data.get("meta") or {}
    return json.dumps(
        {
            "baseline_version": meta.get("baseline_version"),
            "requirements": data.get("requirements") or [],
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def compute_baseline_hash(data):
    payload = canonical_baseline_payload(data).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load_baseline(root, feature, gaps):
    path = root / "qa" / "baseline" / feature / "requirements.yaml"
    if not path.is_file():
        gaps.append(Gap("BASELINE_ABSENT", "BLOCKING", feature,
                        "缺少需求基线 {} —— 没有分母，覆盖率无从计算".format(path)))
        return None, [], None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        gaps.append(Gap("BASELINE_YAML_INVALID", "BLOCKING", str(path), str(exc)))
        return None, [], None

    meta = data.get("meta") or {}
    raw_reqs = data.get("requirements") or []

    if not raw_reqs:
        gaps.append(Gap("BASELINE_EMPTY", "BLOCKING", feature,
                        "基线未包含任何 REQ —— 空基线不得判通过"))

    for field in ("baseline_version", "confirmed_by", "baseline_approval_ref"):
        if not meta.get(field):
            gaps.append(Gap("BASELINE_META_INCOMPLETE", "BLOCKING", feature,
                            "基线 meta.{} 缺失 —— 基线未确认，门禁应判 INCOMPLETE".format(field)))

    reqs = []
    seen = set()  # type: Set[str]
    for item in raw_reqs:
        if not isinstance(item, dict) or not item.get("id"):
            gaps.append(Gap("BASELINE_ENTRY_INVALID", "BLOCKING", feature,
                            "基线条目缺少 id: {}".format(item)))
            continue
        rid = str(item["id"])
        if rid in seen:
            gaps.append(Gap("DUPLICATE_REQ", "BLOCKING", rid, "基线中 REQ id 重复"))
            continue
        seen.add(rid)
        stmt = item.get("statement") or ""
        if not stmt.strip():
            gaps.append(Gap("REQ_STATEMENT_EMPTY", "BLOCKING", rid,
                            "REQ 缺少可验证的验收标准文本"))
        reqs.append(Requirement(
            id=rid,
            statement=stmt,
            blocking=bool(item.get("blocking", False)),
            risk=str(item.get("risk") or "unknown"),
        ))

    return data, reqs, meta.get("content_hash")


def load_cases(root, feature, gaps):
    cases = []          # type: List[Case]
    seen = {}           # type: Dict[str, str]
    case_dir = root / "qa" / "cases" / feature
    if not case_dir.is_dir():
        return cases

    for path in sorted(case_dir.glob("*.md")):
        fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if not fm:
            gaps.append(Gap("CASE_FILE_NO_FRONTMATTER", "GAP", str(path),
                            "用例文件缺少 frontmatter，无法参与追溯"))
            continue
        file_baseline_version = fm.get("baseline_version")
        for entry in fm.get("cases") or []:
            if not isinstance(entry, dict) or not entry.get("id"):
                gaps.append(Gap("CASE_ENTRY_INVALID", "GAP", str(path),
                                "用例条目缺少 id: {}".format(entry)))
                continue
            tid = str(entry["id"])
            if tid in seen:
                gaps.append(Gap("DUPLICATE_TC", "BLOCKING", tid,
                                "TC id 重复：{} 与 {}".format(seen[tid], path)))
                continue
            seen[tid] = str(path)
            covers = entry.get("covers") or []
            if isinstance(covers, str):
                covers = [covers]
            if not covers:
                gaps.append(Gap("CASE_WITHOUT_COVERS", "GAP", tid,
                                "用例未声明 covers，无法计入任何 REQ 的覆盖"))
            cases.append(Case(
                id=tid,
                covers=[str(c) for c in covers],
                manual=bool(entry.get("manual", False)),
                source=str(path),
                baseline_version=file_baseline_version,
            ))
    return cases


def load_collected(root, feature, explicit_path, gaps):
    """载入 collect 清单。返回 (tests, evidence_status)。

    没有清单时返回 AUTOMATION_EVIDENCE_ABSENT —— 这与"已确认没有自动化"不是一回事。
    """
    if explicit_path:
        path = pathlib.Path(explicit_path)
        if not path.is_absolute():
            path = root / path
    else:
        path = root / "qa" / "trace" / feature / "collected.json"

    if not path.is_file():
        gaps.append(Gap("AUTOMATION_EVIDENCE_ABSENT", "GAP", feature,
                        "缺少 collect 清单 {} —— 已自动化状态不可判定。"
                        "注意：这不等于'没有自动化'。".format(path)))
        return [], "AUTOMATION_EVIDENCE_ABSENT"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        gaps.append(Gap("COLLECTED_INVALID", "BLOCKING", str(path), str(exc)))
        return [], "AUTOMATION_EVIDENCE_ABSENT"

    tests = []
    for item in data.get("tests") or []:
        node_id = item.get("node_id")
        if not node_id:
            gaps.append(Gap("COLLECTED_ENTRY_INVALID", "GAP", str(path),
                            "collect 条目缺少 node_id: {}".format(item)))
            continue
        marks = item.get("cases")
        if marks is None:
            marks = CASE_MARKER_RE.findall(json.dumps(item, ensure_ascii=False))
        tests.append(CollectedTest(
            node_id=str(node_id),
            cases=[str(c) for c in marks],
            params=item.get("params"),
        ))
    return tests, "PRESENT"


def load_runs(root, feature, run_id, gaps):
    """载入运行记录。返回 (run_meta, attempts, evidence_status)。"""
    run_dir = root / "qa" / "runs"
    candidates = []
    if run_id:
        p = run_dir / run_id / "run.json"
        if p.is_file():
            candidates = [p]
    else:
        candidates = sorted(run_dir.glob("*/run.json"))

    if not candidates:
        gaps.append(Gap("RUN_EVIDENCE_ABSENT", "GAP", feature,
                        "无运行记录 —— 已执行/已通过状态不可判定"))
        return {}, [], "RUN_EVIDENCE_ABSENT"

    path = candidates[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        gaps.append(Gap("RUN_INVALID", "BLOCKING", str(path), str(exc)))
        return {}, [], "RUN_EVIDENCE_ABSENT"

    attempts = []
    for item in data.get("attempts") or []:
        if not item.get("node_id"):
            gaps.append(Gap("ATTEMPT_ENTRY_INVALID", "GAP", str(path),
                            "attempt 缺少 node_id: {}".format(item)))
            continue
        attempts.append(Attempt(
            node_id=str(item["node_id"]),
            attempt=int(item.get("attempt", 1)),
            status=str(item.get("status") or "unknown"),
            params=item.get("params"),
        ))
    return data, attempts, "PRESENT"


def load_required_scope(root, feature, gaps):
    path = root / "qa" / "plan" / feature / "required-scope.yaml"
    if not path.is_file():
        gaps.append(Gap("REQUIRED_SCOPE_ABSENT", "BLOCKING", feature,
                        "缺少必测范围 {} —— 必测范围不得由运行结果反推".format(path)))
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        gaps.append(Gap("REQUIRED_SCOPE_INVALID", "BLOCKING", str(path), str(exc)))
        return {}


def load_defects(root, gaps):
    """返回 tc_id -> [defect dict]，以及 req_id -> [defect dict]。

    阻断判定契约（三个字段各管一件事，缺一不可）:

      closed              已关闭 → 不再阻断，但仍记录在案
      confirmed_blocking  人已确认它阻断 → 阻断
      severity            落在 required-scope 的 blocking_defect_severities 内 → 阻断

    未关闭且满足后两者之一即为阻断。`severity` 由门禁按策略配置判定，
    因此 `blocking_defect_severities` 是真的参与计算，不是占位。
    """
    by_tc = {}    # type: Dict[str, List[dict]]
    by_req = {}   # type: Dict[str, List[dict]]
    ddir = root / "qa" / "defects"
    if not ddir.is_dir():
        return by_tc, by_req
    for path in sorted(ddir.glob("*.md")):
        fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if not fm:
            gaps.append(Gap("DEFECT_NO_FRONTMATTER", "HINT", str(path),
                            "缺陷文件缺少 frontmatter，无法参与追溯"))
            continue
        rec = {
            "id": fm.get("id") or path.stem,
            "severity": fm.get("severity"),
            "confirmed_blocking": bool(fm.get("confirmed_blocking", False)),
            "closed": bool(fm.get("closed", False)),
            "root_cause": fm.get("root_cause"),
            "stability": fm.get("stability"),
            "source": str(path),
        }

        tc_links = [str(x) for x in (fm.get("tc") or [])]
        req_links = [str(x) for x in (fm.get("req") or [])]
        if not tc_links and not req_links:
            gaps.append(Gap("DEFECT_UNLINKED", "GAP", rec["id"],
                            "缺陷既未关联 TC 也未关联 REQ —— 无法进入任何范围的阻断判定"))
        rc = rec["root_cause"]
        if rc is not None and rc not in VALID_ROOT_CAUSES:
            gaps.append(Gap("DEFECT_ROOT_CAUSE_INVALID", "GAP", rec["id"],
                            "root_cause='{}' 非法；FLAKY 不是根因取值，合法值 {}".format(
                                rc, sorted(VALID_ROOT_CAUSES))))
        st = rec["stability"]
        if st is not None and st not in VALID_STABILITY:
            gaps.append(Gap("DEFECT_STABILITY_INVALID", "GAP", rec["id"],
                            "stability='{}' 非法，合法值 {}".format(st, sorted(VALID_STABILITY))))

        for tc in tc_links:
            by_tc.setdefault(tc, []).append(rec)
        for rq in req_links:
            by_req.setdefault(rq, []).append(rec)
    return by_tc, by_req


# ---------------------------------------------------------------------------
# 派生
# ---------------------------------------------------------------------------


def build_report(root, feature, collected_path=None, run_id=None):
    gaps = []  # type: List[Gap]

    _data, reqs, stored_hash = load_baseline(root, feature, gaps)
    cases = load_cases(root, feature, gaps)
    collected, automation_evidence = load_collected(root, feature, collected_path, gaps)
    run_meta, attempts, run_evidence = load_runs(root, feature, run_id, gaps)
    scope = load_required_scope(root, feature, gaps)
    defects_by_tc, defects_by_req = load_defects(root, gaps)

    req_ids = {r.id for r in reqs}
    case_by_id = {c.id: c for c in cases}

    current_hash = compute_baseline_hash(_data) if _data else None
    baseline_meta = (_data or {}).get("meta") or {}
    baseline_version = baseline_meta.get("baseline_version")

    # ---- 版本交叉核对：用例文件与必测范围都必须声明本次基线版本 ----
    scope_meta = (scope or {}).get("meta") or {}
    if baseline_version:
        scope_bv = scope_meta.get("baseline_version")
        if scope_bv and str(scope_bv) != str(baseline_version):
            gaps.append(Gap("SCOPE_BASELINE_VERSION_MISMATCH", "BLOCKING", feature,
                            "必测范围声明 baseline_version={}，当前基线为 {} —— "
                            "范围与基线版本不匹配".format(scope_bv, baseline_version)))
        elif not scope_bv:
            gaps.append(Gap("SCOPE_BASELINE_VERSION_ABSENT", "BLOCKING", feature,
                            "必测范围未声明 baseline_version —— 无法确认它针对哪一版基线"))

        for c in cases:
            if c.baseline_version is None:
                gaps.append(Gap("CASE_BASELINE_VERSION_ABSENT", "BLOCKING", c.id,
                                "用例文件未声明 baseline_version（{}）".format(c.source)))
            elif str(c.baseline_version) != str(baseline_version):
                gaps.append(Gap("CASE_BASELINE_VERSION_MISMATCH", "BLOCKING", c.id,
                                "用例文件声明 baseline_version={}，当前基线为 {}（{}）".format(
                                    c.baseline_version, baseline_version, c.source)))

    # ---- STALE 与运行记录绑定字段 ----
    # 关键：缺字段**不能**跳过核对。"字段非空且不相等才报错"会让删掉字段反而通过。
    stale = False
    if run_meta:
        run_hash = run_meta.get("baseline_hash")
        if not run_hash:
            gaps.append(Gap("RUN_BASELINE_HASH_ABSENT", "BLOCKING", feature,
                            "运行记录未绑定 baseline_hash —— 无法确认证据对应哪一版基线"))
        elif current_hash and run_hash != current_hash:
            stale = True
            gaps.append(Gap("EVIDENCE_STALE", "BLOCKING", feature,
                            "运行记录的 baseline_hash={} 与当前基线 {} 不一致 —— "
                            "基线已变更，关联证据失效".format(run_hash, current_hash)))

        run_bv = run_meta.get("baseline_version")
        if not run_bv:
            gaps.append(Gap("RUN_BASELINE_VERSION_ABSENT", "BLOCKING", feature,
                            "运行记录未绑定 baseline_version"))
        elif baseline_version and str(run_bv) != str(baseline_version):
            gaps.append(Gap("RUN_BASELINE_VERSION_MISMATCH", "BLOCKING", feature,
                            "运行记录 baseline_version={}，当前基线为 {}".format(
                                run_bv, baseline_version)))
    if stored_hash and current_hash and stored_hash != current_hash:
        gaps.append(Gap("BASELINE_HASH_MISMATCH", "BLOCKING", feature,
                        "基线 meta.content_hash 与内容不符（存 {}，算 {}）—— "
                        "基线被改动但未重算哈希".format(stored_hash, current_hash)))

    # ---- 错标：TC.covers 指向不存在的 REQ ----
    for c in cases:
        for rid in c.covers:
            if rid not in req_ids:
                gaps.append(Gap("MISLABELED_COVERS", "BLOCKING", c.id,
                                "covers 引用不存在的 REQ '{}'（{}）".format(rid, c.source)))

    # ---- 错标 / 未归属：collect 到的测试 ----
    at_by_case = {}   # type: Dict[str, List[CollectedTest]]
    for t in collected:
        if not t.cases:
            gaps.append(Gap("UNATTRIBUTED_AT", "HINT", t.node_id,
                            "测试无 @case 标记，未归属到任何 TC"))
            continue
        for tid in t.cases:
            if tid not in case_by_id:
                gaps.append(Gap("MISLABELED_CASE_MARKER", "BLOCKING", t.node_id,
                                "@case 引用不存在的 TC '{}'".format(tid)))
                continue
            at_by_case.setdefault(tid, []).append(t)

    # ---- attempts 按 node_id + params 归组 ----
    attempts_by_key = {}  # type: Dict[str, List[Attempt]]
    for a in attempts:
        key = "{}|{}".format(a.node_id, a.params if a.params is not None else "")
        attempts_by_key.setdefault(key, []).append(a)
    for key in attempts_by_key:
        attempts_by_key[key].sort(key=lambda x: x.attempt)

    # ---- 逐 TC 四状态 ----
    per_case = {}
    for c in cases:
        ats = at_by_case.get(c.id, [])
        automated = None
        if automation_evidence == "PRESENT":
            automated = bool(ats)
            if not automated and not c.manual:
                gaps.append(Gap("UNAUTOMATED_TC", "GAP", c.id,
                                "非 manual 用例没有对应的自动化测试（漏标或未实现）"))

        # 三态聚合：缺失 / 跳过 **不是**失败。
        #   any failed        → False（确有失败）
        #   else any missing/skipped → None（结果不完整，属证据问题）
        #   else all passed   → True
        exec_states = []
        at_statuses = {}
        saw_failed = False
        saw_unknown = False
        intermittent = False

        for t in ats:
            key = "{}|{}".format(t.node_id, t.params if t.params is not None else "")
            seq = attempts_by_key.get(key, [])
            if not seq:
                exec_states.append(False)
                at_statuses[key] = "no_attempt"
                saw_unknown = True
                continue
            exec_states.append(True)
            last = seq[-1]
            at_statuses[key] = last.status
            if last.status == "passed":
                pass
            elif last.status in ("failed", "error"):
                saw_failed = True
            else:
                # skipped / xfailed / unknown —— 没测，不是测出问题
                saw_unknown = True
            if len(seq) > 1 and any(s.status != "passed" for s in seq[:-1]):
                intermittent = True

        executed = None
        passed_last = None
        if run_evidence == "PRESENT" and ats:
            executed = bool(exec_states) and all(exec_states)
            if saw_failed:
                passed_last = False
            elif saw_unknown:
                passed_last = None
            else:
                passed_last = True

        # 缺陷：直接关联该 TC 的，加上关联其所覆盖 REQ 的（REQ 级阻断必须传播到范围内）
        tc_defects = list(defects_by_tc.get(c.id, []))
        req_defects = []
        for rid in c.covers:
            for d in defects_by_req.get(rid, []):
                if d["id"] not in {x["id"] for x in tc_defects + req_defects}:
                    req_defects.append(d)

        all_defects = tc_defects + req_defects
        open_defects = [d for d in all_defects if not d["closed"]]

        per_case[c.id] = {
            "covers": c.covers,
            "manual": c.manual,
            "source": c.source,
            "at_node_ids": [t.node_id for t in ats],
            "at_statuses": at_statuses,
            "designed": True,
            "automated": automated,
            "executed": executed,
            # last_result = 观察；gate_accepted 由 gate_check.py 判定，此处只提供输入
            "last_result": passed_last,
            "has_failed_attempt": saw_failed,
            "has_unknown_result": saw_unknown,
            "intermittent": intermittent,
            "defects": [d["id"] for d in all_defects],
            "defects_via_req": [d["id"] for d in req_defects],
            "open_defect_severities": sorted(
                {str(d["severity"]) for d in open_defects if d["severity"]}),
            "confirmed_blocking_defect": any(
                d["confirmed_blocking"] for d in open_defects),
        }

    # ---- 逐 REQ 汇总 ----
    per_req = {}
    for r in reqs:
        linked = [c for c in cases if r.id in c.covers]
        auto_candidates = [c for c in linked if not c.manual]
        per_req[r.id] = {
            "statement": r.statement,
            "blocking": r.blocking,
            "risk": r.risk,
            "tc_ids": [c.id for c in linked],
            "designed": bool(linked),
            "automatable_tc": len(auto_candidates),
            "automated_tc": sum(
                1 for c in auto_candidates if per_case[c.id]["automated"] is True
            ),
            "executed_tc": sum(
                1 for c in linked if per_case[c.id]["executed"] is True
            ),
            "passed_tc": sum(
                1 for c in linked if per_case[c.id]["last_result"] is True
            ),
            "manual_tc": sum(1 for c in linked if c.manual),
        }
        if not linked:
            gaps.append(Gap("UNCOVERED_REQ", "GAP", r.id,
                            "基线条目没有任何用例覆盖：{}".format(r.statement[:60])))

    # ---- 必测范围：先算出**唯一有效执行范围**，再校验 ----
    # required_requirements 不能只检查 REQ 是否存在就完事 —— 那样按 REQ 指定的范围
    # 根本不参与结果判定。有效范围 = 显式 TC 清单 ∪ 必测 REQ 展开出的 TC。
    declared_cases = [str(x) for x in (scope.get("required_cases") or [])]
    required_reqs = [str(x) for x in (scope.get("required_requirements") or [])]

    for rid in required_reqs:
        if rid not in req_ids:
            gaps.append(Gap("SCOPE_REQ_UNKNOWN", "BLOCKING", rid,
                            "必测范围引用不存在的 REQ"))

    expanded = []
    for rid in required_reqs:
        if rid not in req_ids:
            continue
        covering = [c.id for c in cases if rid in c.covers]
        if not covering:
            gaps.append(Gap("REQUIRED_REQ_WITHOUT_CASE", "BLOCKING", rid,
                            "必测 REQ 没有任何用例覆盖 —— 无法验证，不得放行"))
            continue
        expanded.extend(covering)

    effective = []
    for tid in declared_cases + expanded:
        if tid not in effective:
            effective.append(tid)

    # 两份范围清单不一致时必须可见，否则读报告的人会以为 required_cases 就是全部
    only_from_req = [t for t in expanded if t not in declared_cases]
    if only_from_req:
        gaps.append(Gap("SCOPE_EXPANDED_FROM_REQ", "HINT", feature,
                        "必测 REQ 展开出 {} 条未列在 required_cases 中的用例：{} —— "
                        "已并入有效执行范围".format(len(only_from_req), sorted(set(only_from_req)))))

    for tid in effective:
        if tid not in case_by_id:
            gaps.append(Gap("SCOPE_CASE_UNKNOWN", "BLOCKING", tid,
                            "必测范围引用不存在的 TC"))
            continue
        c = case_by_id[tid]
        info = per_case[tid]
        if c.manual:
            # 手工必测项走同一条范围判定路径；v1 暂不支持采集人工执行证据 → 缺证据
            if not info["at_node_ids"] and info["executed"] is not True:
                gaps.append(Gap("MANUAL_EVIDENCE_ABSENT", "BLOCKING", tid,
                                "手工必测项缺少人工执行证据 —— 不得放行，门禁应判 INCOMPLETE"))
        else:
            if run_evidence != "PRESENT" or info["executed"] is not True:
                gaps.append(Gap("REQUIRED_CASE_NOT_EXECUTED", "BLOCKING", tid,
                                "必测用例未执行或无执行证据"))

    # ---- 计数 ----
    automatable = [c for c in cases if not c.manual]
    counts = {
        "req_total": len(reqs),
        "req_designed": sum(1 for v in per_req.values() if v["designed"]),
        "tc_total": len(cases),
        "tc_manual": sum(1 for c in cases if c.manual),
        "tc_automatable": len(automatable),
        "tc_automated": sum(
            1 for c in automatable if per_case[c.id]["automated"] is True
        ),
        "tc_executed": sum(1 for c in cases if per_case[c.id]["executed"] is True),
        "tc_last_pass": sum(1 for c in cases if per_case[c.id]["last_result"] is True),
        "collected_tests": len(collected),
        "attempts": len(attempts),
        "required_cases_declared": len(declared_cases),
        "required_cases_effective": len(effective),
    }

    return TraceReport(
        feature=feature,
        baseline_version=baseline_version,
        baseline_hash_current=current_hash,
        automation_evidence=automation_evidence,
        run_evidence=run_evidence,
        stale=stale,
        counts=counts,
        per_requirement=per_req,
        per_case=per_case,
        gaps=gaps,
        effective_required_cases=effective,
        # 只保留绑定相关字段，不把整份 attempts 带进报告
        run_meta={k: v for k, v in (run_meta or {}).items() if k != "attempts"},
        scope_meta=dict(scope_meta or {}),
    )


def write_matrix_csv(report, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["REQ", "TC", "AT", "manual", "designed", "automated",
                    "executed", "last_result", "intermittent", "BUG"])
        for rid, rinfo in sorted(report.per_requirement.items()):
            if not rinfo["tc_ids"]:
                w.writerow([rid, "", "", "", "yes", "", "", "", "", ""])
                continue
            for tid in rinfo["tc_ids"]:
                cinfo = report.per_case[tid]
                ats = cinfo["at_node_ids"] or [""]
                for at in ats:
                    w.writerow([
                        rid, tid, at,
                        "yes" if cinfo["manual"] else "no",
                        "yes",
                        _tri(cinfo["automated"]),
                        _tri(cinfo["executed"]),
                        _tri(cinfo["last_result"]),
                        "yes" if cinfo["intermittent"] else "no",
                        ";".join(cinfo["defects"]),
                    ])
    return out_path


def _tri(value):
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def discover_features(root):
    out = []
    bdir = root / "qa" / "baseline"
    if bdir.is_dir():
        out.extend(p.name for p in sorted(bdir.iterdir()) if p.is_dir())
    return out


def update_hash(root, feature):
    path = root / "qa" / "baseline" / feature / "requirements.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    new_hash = compute_baseline_hash(data)
    data.setdefault("meta", {})["content_hash"] = new_hash
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return new_hash


def main():
    ap = argparse.ArgumentParser(description="派生追溯矩阵与四状态覆盖统计")
    ap.add_argument("--root", default=".")
    ap.add_argument("--feature", default=None, help="只处理指定 feature")
    ap.add_argument("--collected", default=None, help="collect 清单路径")
    ap.add_argument("--run-id", default=None, help="指定运行记录")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-gap", action="store_true",
                    help="存在 BLOCKING 或 GAP 时以非零码退出")
    ap.add_argument("--update-hash", action="store_true",
                    help="重算并写入基线 content_hash（需 --feature）")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()

    if args.update_hash:
        if not args.feature:
            print("FATAL: --update-hash 需要 --feature", file=sys.stderr)
            return 2
        print(update_hash(root, args.feature))
        return 0

    features = [args.feature] if args.feature else discover_features(root)
    if not features:
        payload = {
            "verdict": "NO_FEATURE",
            "detail": "qa/baseline/ 下没有任何 feature —— 尚无需求基线，覆盖率无从计算",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json
              else payload["detail"])
        return 1 if args.fail_on_gap else 0

    reports = []
    for feat in features:
        rep = build_report(root, feat, args.collected, args.run_id)
        reports.append(rep)
        write_matrix_csv(rep, root / "qa" / "trace" / feat / "matrix.csv")

    any_gap = any(r.has_gap for r in reports)

    if args.json:
        print(json.dumps({"reports": [r.as_dict() for r in reports]},
                         indent=2, ensure_ascii=False))
    else:
        for r in reports:
            print("=== feature: {} (baseline {}) ===".format(r.feature, r.baseline_version))
            print("  证据状态: automation={} run={} stale={}".format(
                r.automation_evidence, r.run_evidence, r.stale))
            c = r.counts
            print("  已设计   {}/{} REQ".format(c["req_designed"], c["req_total"]))
            print("  已自动化 {}/{} 可自动化 TC（manual {} 条不进分母）".format(
                c["tc_automated"], c["tc_automatable"], c["tc_manual"]))
            print("  已执行   {}/{} TC".format(c["tc_executed"], c["tc_total"]))
            print("  末次通过 {}/{} TC  ← 观察值，不等于门禁接受".format(
                c["tc_last_pass"], c["tc_total"]))
            if r.gaps:
                print("  缺口 {} 项:".format(len(r.gaps)))
                for g in r.gaps:
                    print("    [{}] {} — {}".format(g.severity, g.code, g.detail))
            else:
                print("  无缺口")

    if args.fail_on_gap and any_gap:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""合并门禁判定 —— 纯函数，产出 merge-gate-report。

**这是合并门禁，不是发布准出。** 合并通过不等于发布验收完成；发布准出是另一层决策，
消费本报告 + 环境验证 + 回归结果。

判定顺序（短路）:

    1. 任一阻断项**明确失败**            → FAIL          （质量失败）
    2. 无明确失败，但必需证据**不完整**   → INCOMPLETE    （证据不足）
    3. 全部必需检查满足                  → PASS

两种不通过必须可区分：`FAIL` 是"测出问题了"，`INCOMPLETE` 是"没测够/证据不成立"。
报告始终列全所有失败项与缺失项，不因汇总状态而隐藏明细。

版本绑定分两类取值:

    policy_version    ← target_sha 上**已批准**版本（门禁规则、重试策略、review_mode）
    baseline_version  ← 针对本次变更**已确认**的版本（需求基线、本次必测范围）

merge-base 只用于计算差异范围，不作规则来源。规则变更在合并进目标分支后对**后续**
候选生效，不对提出该变更的候选自身生效。

v1 只实现**完整性**规则，阈值项留配置位但不填任意数字 —— 没有项目依据的阈值是编造。

用法:
    python3 scripts/gate_check.py --root . [--feature F] [--json]
"""
import argparse
import dataclasses
import json
import pathlib
import sys
from typing import Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import trace_matrix as tm  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FATAL: 需要 PyYAML", file=sys.stderr)
    sys.exit(2)

VERDICT_FAIL = "FAIL"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_PASS = "PASS"

REVIEW_MODES = {"manual", "automated_required"}

# 六字段版本绑定
BINDING_FIELDS = [
    "candidate_sha",
    "target_sha",
    "policy_version",
    "baseline_version",
    "baseline_hash",
    "baseline_approval_ref",
]


@dataclasses.dataclass
class Finding:
    """门禁发现项。

    kind:
      QUALITY_FAILURE   —— 明确失败，导致 FAIL
      EVIDENCE_MISSING  —— 证据不完整，导致 INCOMPLETE
      HINT              —— 提示，不影响判定
    """
    code: str
    kind: str
    subject: str
    detail: str

    def as_dict(self):
        return dataclasses.asdict(self)


@dataclasses.dataclass
class GateReport:
    verdict: str
    feature: str
    binding: Dict[str, Optional[str]]
    review_mode: Optional[str]
    findings: List[Finding]
    verified_scope: Dict[str, object]
    unverified: List[str]
    counts: Dict[str, int]
    thresholds: Dict[str, object]
    note: str

    def as_dict(self):
        out = dataclasses.asdict(self)
        out["findings"] = [f.as_dict() for f in self.findings]
        return out

    @property
    def quality_failures(self):
        return [f for f in self.findings if f.kind == "QUALITY_FAILURE"]

    @property
    def evidence_missing(self):
        return [f for f in self.findings if f.kind == "EVIDENCE_MISSING"]


# ---------------------------------------------------------------------------
# 输入载入
# ---------------------------------------------------------------------------


def load_yaml(path):
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None


def load_json(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_policy(root, feature, findings):
    """门禁规则。

    v1 从 required-scope.yaml 读取（retry_policy / review_mode / blocking 等级）。
    这些字段属受保护门禁配置，其**参照版本应取自 target_sha 上的已批准版本**；
    本地运行读工作副本，因此本地结果只是自检，合并门禁由 CI 重新生成。
    """
    scope = load_yaml(root / "qa" / "plan" / feature / "required-scope.yaml")
    if scope is None:
        findings.append(Finding(
            "POLICY_ABSENT", "EVIDENCE_MISSING", feature,
            "缺少门禁配置 qa/plan/{}/required-scope.yaml —— 必测范围与规则均不可知".format(feature)))
        return {}
    return scope


# ---------------------------------------------------------------------------
# 各检查项
# ---------------------------------------------------------------------------


def check_binding(binding, findings, trace=None):
    """六字段版本绑定完整性 + merge-base 误用检测 + 与实际基线交叉核对。

    仅检查"调用方给的六个字段非空"是不够的：字段齐全不代表它们指向的就是本次实际使用的
    基线。调用方完全可以传一份与磁盘基线无关的 hash/version。
    """
    for field in BINDING_FIELDS:
        if not binding.get(field):
            findings.append(Finding(
                "BINDING_FIELD_MISSING", "EVIDENCE_MISSING", field,
                "版本绑定字段 {} 缺失 —— 证据无法与候选对应".format(field)))

    if trace is not None:
        if binding.get("baseline_hash") and trace.baseline_hash_current and \
                binding["baseline_hash"] != trace.baseline_hash_current:
            findings.append(Finding(
                "BINDING_BASELINE_HASH_MISMATCH", "EVIDENCE_MISSING", "baseline_hash",
                "绑定的 baseline_hash={} 与实际基线内容 {} 不一致 —— "
                "评估上下文与实际使用的基线不是同一份".format(
                    binding["baseline_hash"], trace.baseline_hash_current)))

        if binding.get("baseline_version") and trace.baseline_version and \
                str(binding["baseline_version"]) != str(trace.baseline_version):
            findings.append(Finding(
                "BINDING_BASELINE_VERSION_MISMATCH", "EVIDENCE_MISSING", "baseline_version",
                "绑定的 baseline_version={}，实际基线为 {}".format(
                    binding["baseline_version"], trace.baseline_version)))

    if binding.get("policy_source") == "merge_base":
        findings.append(Finding(
            "POLICY_FROM_MERGE_BASE", "EVIDENCE_MISSING", "policy_version",
            "门禁规则取自 merge-base —— merge-base 是共同祖先，可能早于目标分支上"
            "最新的已批准规则；规则必须取 target_sha 上的已批准版本"))

    # 候选用自带新规则给自己放行
    if binding.get("policy_version") and binding.get("policy_from_candidate"):
        findings.append(Finding(
            "POLICY_FROM_CANDIDATE", "QUALITY_FAILURE", "policy_version",
            "门禁规则取自候选自身的变更 —— 规则变更须合并进目标分支后对后续候选生效，"
            "不得对提出该变更的候选自身生效"))


def check_review(root, binding, policy, findings):
    """评审链路。返回 (mode, trusted_verdict)。

    `trusted_verdict` 只在**来源、版本都已验证**时才非 None。这个返回值是后续消费
    处置记录（如关闭质量门槛提示）的唯一合法输入 —— 磁盘上存在一份 JSON 不等于它可信。

    manual 模式**不**消费本地 verdict 文件：人工模式的处置依据是平台审批记录，
    本地无法验证其来源与权限。否则候选只要往仓库里塞一份 JSON 就能关掉提示。
    """
    mode = policy.get("review_mode")
    verdict_path = root / "qa" / "reports" / "review-verdict.json"
    verdict = load_json(verdict_path)

    if mode not in REVIEW_MODES:
        findings.append(Finding(
            "REVIEW_MODE_INVALID", "EVIDENCE_MISSING", "review_mode",
            "review_mode='{}' 非法，合法值 {} —— 模式必须预先固定".format(
                mode, sorted(REVIEW_MODES))))
        return mode, None

    if mode == "manual":
        if not binding.get("human_approval_ref"):
            findings.append(Finding(
                "HUMAN_APPROVAL_ABSENT", "EVIDENCE_MISSING", "human_approval_ref",
                "review_mode=manual 但缺少平台人工审批记录引用 —— "
                "审批记录来自平台 API，不读仓库内文件"))
        if verdict is not None:
            findings.append(Finding(
                "REVIEW_VERDICT_IGNORED_IN_MANUAL_MODE", "HINT", str(verdict_path),
                "manual 模式下发现本地 review-verdict.json —— 该文件**不被消费**，"
                "其来源与权限无法在本地验证；处置依据只认平台审批记录"))
        return mode, None

    # ---- automated_required ----
    if verdict is None:
        findings.append(Finding(
            "REVIEW_VERDICT_ABSENT", "EVIDENCE_MISSING", str(verdict_path),
            "review_mode=automated_required 但缺少 review-verdict.json —— "
            "一律 INCOMPLETE，不得临时切到 manual 后通过"))
        return mode, None

    trusted = True

    if verdict.get("produced_by") != "ci-review-executor":
        trusted = False
        findings.append(Finding(
            "REVIEW_VERDICT_UNTRUSTED", "QUALITY_FAILURE", str(verdict_path),
            "review-verdict.json 的 produced_by='{}' 不是 CI 评审执行器 —— "
            "候选分支自提的评审结论不构成评审通过".format(verdict.get("produced_by"))))

    # 缺字段不能跳过核对，否则删掉 candidate_sha 反而放行
    vsha = verdict.get("candidate_sha")
    if not vsha:
        trusted = False
        findings.append(Finding(
            "REVIEW_VERDICT_UNBOUND", "EVIDENCE_MISSING", str(verdict_path),
            "评审结论未绑定 candidate_sha —— 无法确认它评的是哪一版候选"))
    elif binding.get("candidate_sha") and vsha != binding["candidate_sha"]:
        trusted = False
        findings.append(Finding(
            "REVIEW_VERDICT_STALE", "EVIDENCE_MISSING", str(verdict_path),
            "评审结论绑定 candidate_sha={}，当前候选为 {} —— "
            "候选新增 commit 后旧评审结论失效".format(vsha, binding["candidate_sha"])))

    if not verdict.get("review_config_version"):
        trusted = False
        findings.append(Finding(
            "REVIEW_CONFIG_VERSION_ABSENT", "EVIDENCE_MISSING", str(verdict_path),
            "评审结论未记录 review_config_version —— 无法确认评审用的是哪一版规则"))

    if verdict.get("status") == "BLOCKING":
        open_items = [i for i in (verdict.get("blocking_items") or [])
                      if not i.get("closed")]
        if open_items:
            findings.append(Finding(
                "REVIEW_BLOCKING_OPEN", "QUALITY_FAILURE", str(verdict_path),
                "评审存在 {} 项未关闭的阻断意见".format(len(open_items))))
    elif verdict.get("status") not in ("APPROVED", "NO_BLOCKING"):
        trusted = False
        findings.append(Finding(
            "REVIEW_VERDICT_UNPARSEABLE", "EVIDENCE_MISSING", str(verdict_path),
            "评审结论 status='{}' 无法解析 —— automated_required 下判 INCOMPLETE".format(
                verdict.get("status"))))

    return mode, (verdict if trusted else None)


def check_guard_hints(guard_result, trusted_verdict, findings):
    """质量门槛变化提示 → 报告提示段落 + reviewer 阻断路径。

    guard_scan 本身不产生判定（它只是提示器）。R7 要求命中项必须有说明与替代覆盖证据，
    未关闭则计入 reviewer 阻断项。关闭一条提示只有两条合法路径:

      1. 受保护路径下的豁免（同时给出 reason 与 alternative_coverage）
      2. **已验证来源与版本**的评审处置（`trusted_verdict`），且逐项给出
         `hint_id` + `reason` + `alternative_coverage`

    两条硬约束:
      * 确认必须按 `hint_id` 逐项进行。只按规则码确认会让一个 `SKIP_ADDED`
        一次关掉多个文件里的不同提示。
      * 未验证的 verdict 一律不消费 —— 见 `check_review()` 的 trusted 判定。
    """
    if not guard_result:
        return

    all_hints = guard_result.get("hints") or []
    open_hints = [h for h in all_hints if not h.get("waived")]

    for h in all_hints:
        detail = "{} | {}".format(h.get("detail", ""), guard_result.get("disclaimer", ""))
        if h.get("waived"):
            detail = "已豁免：{} | {}".format(h.get("waiver_reason"), detail)
        findings.append(Finding(
            "GUARD_" + str(h.get("code")), "HINT", str(h.get("file")), detail))

    if not open_hints:
        return

    # 逐项处置记录：hint_id -> 处置内容。只接受已验证的 verdict。
    dispositions = {}
    malformed = []
    if trusted_verdict:
        raw = trusted_verdict.get("guard_hints_acknowledged") or []
        for item in raw:
            if not isinstance(item, dict):
                # 旧式"只给规则码"的写法一律不接受
                malformed.append(str(item))
                continue
            hid = item.get("hint_id")
            if not hid:
                malformed.append(str(item))
                continue
            if not item.get("reason") or not item.get("alternative_coverage"):
                malformed.append(str(hid))
                continue
            dispositions[str(hid)] = item

    if malformed:
        findings.append(Finding(
            "GUARD_ACK_MALFORMED", "EVIDENCE_MISSING", "guard_scan",
            "{} 条门槛提示处置记录不合格 —— 必须逐项给出 hint_id、reason 与 "
            "alternative_coverage；只给规则码不构成处置。不合格项：{}".format(
                len(malformed), sorted(set(malformed)))))

    unreviewed = [h for h in open_hints
                  if str(h.get("hint_id")) not in dispositions]
    if unreviewed:
        findings.append(Finding(
            "GUARD_HINTS_UNREVIEWED", "EVIDENCE_MISSING", "guard_scan",
            "{} 项质量门槛变化提示未经已验证的评审处置，也未提供带替代覆盖的豁免 —— "
            "命中项需说明与替代覆盖证据；未关闭则计入 reviewer 阻断项。未处置项：{}".format(
                len(unreviewed),
                sorted({"{}@{}".format(h.get("code"), h.get("file"))
                        for h in unreviewed}))))


def check_blocking_severities(policy, findings):
    sev = policy.get("blocking_defect_severities")
    if not sev:
        findings.append(Finding(
            "BLOCKING_SEVERITY_UNDEFINED", "EVIDENCE_MISSING", "blocking_defect_severities",
            "阻断缺陷等级未定义 —— v1 不发明数字，需项目显式列出哪些等级阻断合并"))
        return []
    return [str(s) for s in sev]


def check_trace(trace, findings):
    """把追溯缺口翻译成门禁发现项。

    分类原则：缺口是"证据/覆盖不成立"还是"测出了问题"。
    追溯层的 BLOCKING 基本都属于证据不成立 → EVIDENCE_MISSING。
    """
    quality_codes = set()          # 追溯层不产生质量失败结论
    for g in trace.gaps:
        if g.severity == "HINT":
            kind = "HINT"
        elif g.code in quality_codes:
            kind = "QUALITY_FAILURE"
        elif g.severity == "BLOCKING":
            kind = "EVIDENCE_MISSING"
        else:
            kind = "EVIDENCE_MISSING"
        findings.append(Finding(g.code, kind, g.subject, g.detail))


def check_results(trace, policy, blocking_severities, findings):
    """结果判定 —— last_result（观察）与 gate_accepted（判定）分离。

    三条硬规则:
      1. 已确认的阻断产品缺陷，不能被一次重试通过覆盖
      2. 未解释的间歇失败，不得仅凭末次通过满足门禁
      3. 明确失败的阻断项 → FAIL
    """
    retry = policy.get("retry_policy") or {}
    accept_last_pass = bool(retry.get("accept_last_pass_without_explanation", False))
    allow_clear_blocking = bool(
        retry.get("allow_retry_to_clear_confirmed_blocking_defect", False))

    if allow_clear_blocking:
        findings.append(Finding(
            "RETRY_POLICY_UNSAFE", "QUALITY_FAILURE", "retry_policy",
            "allow_retry_to_clear_confirmed_blocking_defect=true —— "
            "已确认的阻断产品缺陷不得被重试通过覆盖"))

    # 唯一有效执行范围由追溯层算出（required_cases ∪ 必测 REQ 展开），
    # 门禁不再自己读 required_cases —— 否则按 REQ 指定的范围不参与结果判定。
    required_cases = list(trace.effective_required_cases)
    blocking_set = {str(s) for s in (blocking_severities or [])}

    gate_accepted = {}
    for tid in required_cases:
        info = trace.per_case.get(tid)
        if info is None:
            continue  # 追溯层已报 SCOPE_CASE_UNKNOWN

        last = info["last_result"]
        accepted = None

        # 阻断缺陷判定契约：未关闭 且（人已确认阻断 或 严重级落在策略清单内）
        severity_blocking = sorted(
            set(info.get("open_defect_severities") or []) & blocking_set)
        via_req = info.get("defects_via_req") or []

        if info["confirmed_blocking_defect"] or severity_blocking:
            accepted = False
            reason = []
            if info["confirmed_blocking_defect"]:
                reason.append("已确认阻断")
            if severity_blocking:
                reason.append("严重级 {} 在阻断清单内".format(severity_blocking))
            findings.append(Finding(
                "BLOCKING_DEFECT_NOT_CLEARED", "QUALITY_FAILURE", tid,
                "存在阻断缺陷 {}（{}{}）；末次结果={} —— "
                "重试通过不得抹掉已确认阻断".format(
                    info["defects"], "；".join(reason),
                    "；其中 {} 经 REQ 级关联传播".format(via_req) if via_req else "",
                    last)))
        elif last is False:
            accepted = False
            findings.append(Finding(
                "REQUIRED_CASE_FAILED", "QUALITY_FAILURE", tid,
                "必测用例明确失败（状态：{}）".format(info.get("at_statuses"))))
        elif last is None:
            # 缺失 / 跳过 / 未完成都是证据问题，不是"测出了失败"
            accepted = None
            findings.append(Finding(
                "REQUIRED_CASE_RESULT_MISSING", "EVIDENCE_MISSING", tid,
                "必测用例无有效结果（状态：{}）—— 未执行、被跳过或执行中断，"
                "不得当作已测出的失败".format(info.get("at_statuses") or "无 attempt")))
        elif info["intermittent"] and not accept_last_pass:
            accepted = None
            findings.append(Finding(
                "UNEXPLAINED_INTERMITTENT", "EVIDENCE_MISSING", tid,
                "存在未解释的间歇失败；末次通过**不自动**满足门禁。"
                "重跑只提供稳定性证据，不能排除 PRODUCT 根因"))
        else:
            accepted = True

        gate_accepted[tid] = accepted

    return gate_accepted


EVIDENCE_KINDS = {
    "collect": {"trusted_ci"},
    "runs": {"trusted_ci"},
    "defects": {"tracker"},
}
UNTRUSTED_KIND = "candidate_copy"


def check_evidence_sources(root, findings):
    """证据来源可信性：**缺失不等于零阻断，候选副本不等于可信**。

    为什么需要这一条（外部复核实测出的两个洞）：

    1. CI 组装判定树时漏掉了 `qa/defects`。同一套基线/规则/用例/运行结果，带缺陷记录时
       门禁 FAIL（BLOCKING_DEFECT_NOT_CLEARED），按当时的目录清单组装后变成
       **PASS / findings=[]** —— 已确认阻断因为"输入没带进来"而消失。
       只靠"把目录加进清单"修不够：目录为空时同样会静默变成"零阻断"。
    2. 原始产物与 collect 清单允许退回候选副本。候选能任意增删自己的证据，
       "文件存在且能解析"不构成可信。

    所以判定前要求一份来源声明 `qa/evidence-source.yaml`：

        collect: {kind: trusted_ci, ref: "<artifact/run 标识>"}
        runs:    {kind: trusted_ci, ref: "..."}
        defects: {kind: tracker,    ref: "<查询/导出标识>"}

    缺文件、缺条目、`kind` 不在可信取值内（例如 candidate_copy）、可信来源缺 `ref`，
    一律记 EVIDENCE_MISSING → 三态判定收敛到 INCOMPLETE。这不是阈值发明，
    是把"我们不知道这份证据从哪来"如实表达成"证据不足"。
    """
    path = root / "qa" / "evidence-source.yaml"
    data = load_yaml(path)
    if data is None:
        findings.append(Finding(
            "EVIDENCE_SOURCE_UNDECLARED", "EVIDENCE_MISSING", str(path),
            "缺少证据来源声明 —— 无法确认 collect/运行结果/缺陷记录来自可信执行还是"
            "候选副本。缺失不得等同于零阻断"))
        return

    if not isinstance(data, dict):
        findings.append(Finding(
            "EVIDENCE_SOURCE_INVALID", "EVIDENCE_MISSING", str(path),
            "证据来源声明不是映射结构"))
        return

    for kind_name, trusted_values in sorted(EVIDENCE_KINDS.items()):
        entry = data.get(kind_name)
        if not isinstance(entry, dict):
            findings.append(Finding(
                "EVIDENCE_SOURCE_ENTRY_ABSENT", "EVIDENCE_MISSING", kind_name,
                "证据来源声明缺少 {} 条目 —— 无法判断其可信性".format(kind_name)))
            continue
        kind = entry.get("kind")
        if not kind:
            findings.append(Finding(
                "EVIDENCE_SOURCE_KIND_ABSENT", "EVIDENCE_MISSING", kind_name,
                "{} 未声明 kind".format(kind_name)))
            continue
        if kind == UNTRUSTED_KIND:
            findings.append(Finding(
                "EVIDENCE_SOURCE_UNTRUSTED", "EVIDENCE_MISSING", kind_name,
                "{} 来自候选副本（kind={}）—— 候选可任意增删该证据，"
                "不能据此判通过；需改为 {}".format(
                    kind_name, kind, sorted(trusted_values))))
            continue
        if kind not in trusted_values:
            findings.append(Finding(
                "EVIDENCE_SOURCE_KIND_UNKNOWN", "EVIDENCE_MISSING", kind_name,
                "{} 的 kind={} 不在可信取值 {} 内".format(
                    kind_name, kind, sorted(trusted_values))))
            continue
        if not entry.get("ref"):
            findings.append(Finding(
                "EVIDENCE_SOURCE_REF_ABSENT", "EVIDENCE_MISSING", kind_name,
                "{} 声明为可信来源 {} 但没有 ref —— 无法回溯到具体导出/运行".format(
                    kind_name, kind)))


def check_run_integrity(root, feature, binding, findings):
    """执行完整性：结果缺失、执行中断、证据版本不匹配。"""
    run_dir = root / "qa" / "runs"
    runs = sorted(run_dir.glob("*/run.json")) if run_dir.is_dir() else []
    if not runs:
        findings.append(Finding(
            "RUN_ABSENT", "EVIDENCE_MISSING", feature,
            "无运行记录 —— 必测项未执行"))
        return

    data = load_json(runs[-1])
    if data is None:
        findings.append(Finding(
            "RUN_UNPARSEABLE", "EVIDENCE_MISSING", str(runs[-1]),
            "运行记录无法解析"))
        return

    if data.get("interrupted"):
        findings.append(Finding(
            "RUN_INTERRUPTED", "EVIDENCE_MISSING", str(runs[-1]),
            "执行中断 —— 结果不完整，不得判通过"))

    # 必填绑定字段：缺字段**不能**跳过核对。
    # "字段非空且不相等才报错"的写法会让删掉字段反而通过 —— 这是反向激励。
    rsha = data.get("candidate_sha")
    if not rsha:
        findings.append(Finding(
            "RUN_CANDIDATE_SHA_ABSENT", "EVIDENCE_MISSING", str(runs[-1]),
            "运行记录未绑定 candidate_sha —— 无法确认这份证据属于哪一版候选"))
    elif binding.get("candidate_sha") and rsha != binding["candidate_sha"]:
        findings.append(Finding(
            "RUN_CANDIDATE_MISMATCH", "EVIDENCE_MISSING", str(runs[-1]),
            "运行记录 candidate_sha={}，当前候选为 {} —— 证据与候选版本不匹配".format(
                rsha, binding["candidate_sha"])))

    # 运行记录可以不声明 target_sha / policy_version，但一旦声明就必须与本次一致
    for field in ("target_sha", "policy_version"):
        run_val = data.get(field)
        exp_val = binding.get(field)
        if run_val and exp_val and str(run_val) != str(exp_val):
            findings.append(Finding(
                "RUN_{}_MISMATCH".format(field.upper()), "EVIDENCE_MISSING",
                str(runs[-1]),
                "运行记录 {}={}，本次评估为 {} —— 证据来自另一套规则/目标版本".format(
                    field, run_val, exp_val)))

    # 凭据泄露检查：配置标识必须脱敏
    cfg = json.dumps(data.get("config") or {}, ensure_ascii=False)
    for marker in ("password", "token", "secret", "Bearer ", "cookie"):
        if marker.lower() in cfg.lower():
            findings.append(Finding(
                "RUN_CONFIG_NOT_REDACTED", "QUALITY_FAILURE", str(runs[-1]),
                "运行记录 config 中可能包含凭据（命中 '{}'）—— 必须脱敏".format(marker)))
            break


# ---------------------------------------------------------------------------
# 主判定
# ---------------------------------------------------------------------------


def evaluate(root, feature, binding, trace=None, guard_result=None):
    """纯判定函数。

    binding 由调用方（CI）提供，不从候选工作副本推断。
    guard_result 是 guard_scan.scan_diff(...).as_dict() 的结果，可为 None。
    """
    root = pathlib.Path(root)
    findings = []  # type: List[Finding]

    policy = load_policy(root, feature, findings)
    review_mode, trusted_verdict = check_review(root, binding, policy, findings)
    blocking_sev = check_blocking_severities(policy, findings)

    if trace is None:
        trace = tm.build_report(root, feature)

    # binding 的交叉核对需要追溯层算出的实际基线，所以放在 build_report 之后
    check_binding(binding, findings, trace=trace)
    check_trace(trace, findings)
    check_run_integrity(root, feature, binding, findings)
    check_evidence_sources(root, findings)
    gate_accepted = check_results(trace, policy, blocking_sev, findings)

    # 只消费**已验证**的处置记录；未验证的本地 JSON 一律不作数
    check_guard_hints(guard_result, trusted_verdict, findings)

    # ---- 三态判定，短路 ----
    quality = [f for f in findings if f.kind == "QUALITY_FAILURE"]
    missing = [f for f in findings if f.kind == "EVIDENCE_MISSING"]

    if quality:
        verdict = VERDICT_FAIL
    elif missing:
        verdict = VERDICT_INCOMPLETE
    else:
        verdict = VERDICT_PASS

    verified_scope = {
        "feature": feature,
        # 声明范围与**有效范围**都列出：两者不同时读报告的人必须看得见
        "required_cases_declared": [str(x) for x in (policy.get("required_cases") or [])],
        "required_requirements": [str(x) for x in (policy.get("required_requirements") or [])],
        "required_cases_effective": list(trace.effective_required_cases),
        "gate_accepted": gate_accepted,
        "last_result": {
            tid: trace.per_case[tid]["last_result"]
            for tid in trace.per_case
        },
        "automation_evidence": trace.automation_evidence,
        "run_evidence": trace.run_evidence,
        "baseline_stale": trace.stale,
    }

    unverified = sorted({f.subject for f in missing})

    counts = {
        "quality_failures": len(quality),
        "evidence_missing": len(missing),
        "hints": len([f for f in findings if f.kind == "HINT"]),
        "req_total": trace.counts.get("req_total", 0),
        "req_designed": trace.counts.get("req_designed", 0),
        "tc_automatable": trace.counts.get("tc_automatable", 0),
        "tc_automated": trace.counts.get("tc_automated", 0),
        "tc_executed": trace.counts.get("tc_executed", 0),
        "tc_last_pass": trace.counts.get("tc_last_pass", 0),
    }

    return GateReport(
        verdict=verdict,
        feature=feature,
        binding=dict(binding),
        review_mode=review_mode,
        findings=findings,
        verified_scope=verified_scope,
        unverified=unverified,
        counts=counts,
        thresholds={
            "status": "NOT_CONFIGURED",
            "note": "v1 只实现完整性规则；阈值项留配置位但不填任意数字（无项目依据的阈值是编造）",
        },
        note="这是合并门禁（merge gate），不是发布准出。合并通过不等于发布验收完成。"
             "本地运行仅供自检；合并门禁只认 CI 从可信执行的原始产物重新生成的报告。",
    )


def render_text(rep):
    lines = []
    lines.append("=== merge-gate-report: {} ===".format(rep.feature))
    lines.append("verdict: {}".format(rep.verdict))
    if rep.verdict == VERDICT_FAIL:
        lines.append("  ↳ 质量失败：测出了明确的问题")
    elif rep.verdict == VERDICT_INCOMPLETE:
        lines.append("  ↳ 证据不足：没测够或证据不成立（与质量失败不是一回事）")
    lines.append("review_mode: {}".format(rep.review_mode))
    lines.append("binding:")
    for k in BINDING_FIELDS:
        lines.append("  {}: {}".format(k, rep.binding.get(k) or "<缺失>"))

    c = rep.counts
    lines.append("覆盖（实际验证范围）:")
    lines.append("  已设计 {}/{} REQ；已自动化 {}/{} 可自动化 TC；已执行 {} TC；末次通过 {} TC".format(
        c["req_designed"], c["req_total"], c["tc_automated"], c["tc_automatable"],
        c["tc_executed"], c["tc_last_pass"]))
    declared = rep.verified_scope.get("required_cases_declared") or []
    effective = rep.verified_scope.get("required_cases_effective") or []
    lines.append("  必测范围: 声明 {} 条 / 有效 {} 条{}".format(
        len(declared), len(effective),
        "（有效范围含必测 REQ 展开出的用例）" if len(effective) > len(declared) else ""))
    lines.append("  gate_accepted: {}".format(rep.verified_scope["gate_accepted"]))

    # 报告始终列全所有失败项与缺失项
    if rep.quality_failures:
        lines.append("质量失败 {} 项:".format(len(rep.quality_failures)))
        for f in rep.quality_failures:
            lines.append("  [FAIL] {} · {} — {}".format(f.code, f.subject, f.detail))
    if rep.evidence_missing:
        lines.append("证据缺失 {} 项:".format(len(rep.evidence_missing)))
        for f in rep.evidence_missing:
            lines.append("  [MISSING] {} · {} — {}".format(f.code, f.subject, f.detail))
    hints = [f for f in rep.findings if f.kind == "HINT"]
    if hints:
        lines.append("提示 {} 项（非结论）:".format(len(hints)))
        for f in hints:
            lines.append("  [HINT] {} · {} — {}".format(f.code, f.subject, f.detail))

    lines.append("未验证项: {}".format(", ".join(rep.unverified) or "无"))
    lines.append("阈值: {}".format(rep.thresholds["status"]))
    lines.append("note: {}".format(rep.note))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="合并门禁判定（非发布准出）")
    ap.add_argument("--root", default=".")
    ap.add_argument("--feature", default=None)
    ap.add_argument("--candidate-sha", default=None)
    ap.add_argument("--target-sha", default=None)
    ap.add_argument("--policy-version", default=None)
    ap.add_argument("--human-approval-ref", default=None)
    ap.add_argument("--diff-file", default=None,
                    help="候选 diff，用于把 guard_scan 的提示段落并入报告")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="写出 merge-gate-report.json 的路径")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()

    guard_result = None
    if args.diff_file:
        diff_path = pathlib.Path(args.diff_file)
        if not diff_path.is_file():
            print("FATAL: diff 文件不存在: {}".format(diff_path), file=sys.stderr)
            return 2
        import guard_scan as gs
        guard_result = gs.scan_diff(
            diff_path.read_text(encoding="utf-8", errors="replace"),
            root=root, with_waivers=True,
        ).as_dict()
    features = [args.feature] if args.feature else tm.discover_features(root)
    if not features:
        # 可预期的缺证据分支也必须**落盘**一份机器可读报告。
        # 外部复核实测：无有效基线时这里提前 return，stdout 显示 INCOMPLETE 但
        # --out 指向的文件根本不存在 —— CI 里的 `if: always()` 只保证步骤被尝试执行，
        # 不保证脚本写了文件，下游会读到上一次的旧报告或什么都读不到。
        msg = "qa/baseline/ 下没有任何 feature —— 无需求基线，门禁无从判定"
        payload = {
            "verdict": VERDICT_INCOMPLETE,
            "feature": args.feature,
            "detail": msg,
            "binding": {
                "candidate_sha": args.candidate_sha,
                "target_sha": args.target_sha,
                "policy_version": args.policy_version,
            },
            "findings": [{
                "code": "BASELINE_ABSENT",
                "kind": "EVIDENCE_MISSING",
                "subject": str(root / "qa" / "baseline"),
                "detail": msg,
            }],
            "note": "本报告由缺证据分支生成；verdict 固定为 INCOMPLETE。",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False)
              if args.json else msg)
        if args.out:
            out = pathlib.Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                           encoding="utf-8")
        return 1

    exit_code = 0
    for feat in features:
        baseline = load_yaml(root / "qa" / "baseline" / feat / "requirements.yaml") or {}
        meta = baseline.get("meta") or {}
        binding = {
            "candidate_sha": args.candidate_sha,
            "target_sha": args.target_sha,
            "policy_version": args.policy_version,
            "baseline_version": meta.get("baseline_version"),
            "baseline_hash": meta.get("content_hash"),
            "baseline_approval_ref": meta.get("baseline_approval_ref"),
            "human_approval_ref": args.human_approval_ref,
        }
        rep = evaluate(root, feat, binding, guard_result=guard_result)

        if args.json:
            print(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_text(rep))

        if args.out:
            out = pathlib.Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False),
                           encoding="utf-8")

        if rep.verdict != VERDICT_PASS:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

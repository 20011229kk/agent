"""gate_check.py 测试 —— 六条反例验收是必过项。

反例清单（requirements.md「反例验收」）:
  1. 没有真正执行的越权探针不得算通过     → 由 Task 0 判据保证，此处不适用
  2. 先失败后通过不得自动抹掉阻断
  3. 手工必测项缺证据不得放行
  4. 旧 SHA 的评审结论不得用于新候选版本
  5. 候选分支自提 approved: true 不构成评审通过
  6. automated_required 下评审失败不得临时切 manual 后通过

另外验证三态可区分：FAIL（测出问题）与 INCOMPLETE（证据不足）不得混同。
"""
import json
import pathlib
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import gate_check as gc  # noqa: E402
import trace_matrix as tm  # noqa: E402

FEATURE = "login"
SHA_NEW = "cafebabe"
SHA_OLD = "deadbeef"


# ---------------------------------------------------------------------------
# fixture 构造
# ---------------------------------------------------------------------------


def write_baseline(root, reqs, version="1.0.0"):
    data = {
        "meta": {
            "feature": FEATURE,
            "baseline_version": version,
            "confirmed_by": "zhang.san",
            "confirmed_at": "2026-09-20",
            "baseline_approval_ref": "https://example/mr/1",
        },
        "requirements": reqs,
    }
    data["meta"]["content_hash"] = tm.compute_baseline_hash(data)
    path = root / "qa" / "baseline" / FEATURE / "requirements.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return data


def req(rid):
    return {"id": rid, "statement": "验收标准 {}".format(rid),
            "risk": "high", "blocking": True}


def write_cases(root, cases):
    fm = {"feature": FEATURE, "baseline_version": "1.0.0", "cases": cases}
    path = root / "qa" / "cases" / FEATURE / "core.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
                    + "---\n\n# cases\n", encoding="utf-8")


def case(tid, covers, manual=False):
    return {"id": tid, "covers": covers, "manual": manual,
            "layer": "api", "priority": "P0", "method": "等价类"}


def write_collected(root, tests):
    path = root / "qa" / "trace" / FEATURE / "collected.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tests": tests}, ensure_ascii=False), encoding="utf-8")


def write_run(root, attempts, baseline_hash, candidate_sha=SHA_NEW,
              interrupted=False, config=None):
    path = root / "qa" / "runs" / "20260920-1200" / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "run_id": "20260920-1200",
        "candidate_sha": candidate_sha,
        "baseline_version": "1.0.0",
        "baseline_hash": baseline_hash,
        "interrupted": interrupted,
        "config": config or {"env": "qa-isolated", "traceId": "abc-123"},
        "attempts": attempts,
    }, ensure_ascii=False), encoding="utf-8")


def write_scope(root, required_cases, review_mode="manual",
                accept_last_pass=False, allow_clear_blocking=False,
                severities=None):
    data = {
        "meta": {"feature": FEATURE, "baseline_version": "1.0.0",
                 "confirmed_by": "zhang.san"},
        "required_requirements": [],
        "required_cases": required_cases,
        "retry_policy": {
            "max_attempts": 1,
            "accept_last_pass_without_explanation": accept_last_pass,
            "allow_retry_to_clear_confirmed_blocking_defect": allow_clear_blocking,
        },
        "blocking_defect_severities": severities if severities is not None else ["S1"],
        "review_mode": review_mode,
    }
    path = root / "qa" / "plan" / FEATURE / "required-scope.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def write_defect(root, did, tcs, confirmed_blocking=True):
    fm = {"id": did, "tc": tcs, "req": [], "severity": "S1",
          "confirmed_blocking": confirmed_blocking,
          "root_cause": "PRODUCT", "stability": "间歇失败"}
    path = root / "qa" / "defects" / "{}.md".format(did)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
                    + "---\n\n# defect\n", encoding="utf-8")


def write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW,
                  produced_by="ci-review-executor", blocking_items=None):
    path = root / "qa" / "reports" / "review-verdict.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": status,
        "candidate_sha": candidate_sha,
        "produced_by": produced_by,
        "review_config_version": "1.0.0",
        "blocking_items": blocking_items or [],
    }, ensure_ascii=False), encoding="utf-8")


def binding(candidate_sha=SHA_NEW, human_approval_ref="https://example/mr/1/approval",
            **over):
    b = {
        "candidate_sha": candidate_sha,
        "target_sha": "1111111",
        "policy_version": "1.0.0",
        "baseline_version": "1.0.0",
        "baseline_hash": None,       # 由调用方填
        "baseline_approval_ref": "https://example/mr/1",
        "human_approval_ref": human_approval_ref,
    }
    b.update(over)
    return b


def codes(rep):
    return [f.code for f in rep.findings]


def write_evidence_source(root, collect="trusted_ci", runs="trusted_ci",
                         defects="tracker", refs=True):
    """证据来源声明。缺它一律 INCOMPLETE（缺失不等于零阻断）。"""
    def entry(kind):
        e = {"kind": kind}
        if refs:
            e["ref"] = "ci-run/12345"
        return e

    data = {"collect": entry(collect), "runs": entry(runs),
            "defects": entry(defects)}
    path = root / "qa" / "evidence-source.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _evidence_source(tmp_path):
    """所有 gate 测试默认带可信来源声明，否则每条都会因 INCOMPLETE 而失去区分度。

    来源可信性本身的反例在 test_evidence_source_* 里单独测。
    """
    write_evidence_source(tmp_path)


@pytest.fixture()
def clean(tmp_path):
    """一切正常、应当 PASS 的最小仓库。"""
    data = write_baseline(tmp_path, [req("REQ-1")])
    h = data["meta"]["content_hash"]
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_run(tmp_path, [{"node_id": "t::a", "attempt": 1, "status": "passed"}], h)
    write_scope(tmp_path, ["TC-1"])
    return tmp_path, h


# ---------------------------------------------------------------------------
# 基准：必须能 PASS，否则反例测试无意义
# ---------------------------------------------------------------------------


def test_clean_repo_passes(clean):
    root, h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.verdict == gc.VERDICT_PASS, codes(rep)
    assert rep.verified_scope["gate_accepted"] == {"TC-1": True}


# ---------------------------------------------------------------------------
# 反例 2：先失败后通过不得自动抹掉阻断
# ---------------------------------------------------------------------------


def test_counterexample_2_retry_does_not_clear_confirmed_blocking_defect(clean):
    root, h = clean
    write_run(root, [
        {"node_id": "t::a", "attempt": 1, "status": "failed"},
        {"node_id": "t::a", "attempt": 2, "status": "passed"},
    ], h)
    write_defect(root, "BUG-1", ["TC-1"], confirmed_blocking=True)

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert "BLOCKING_DEFECT_NOT_CLEARED" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL
    # last_result 是观察值（末次通过），gate_accepted 是判定（不接受）——两者必须分离
    assert rep.verified_scope["last_result"]["TC-1"] is True
    assert rep.verified_scope["gate_accepted"]["TC-1"] is False


def test_unexplained_intermittent_does_not_satisfy_gate(clean):
    """未解释的间歇失败：末次通过也不自动满足门禁，判 INCOMPLETE。"""
    root, h = clean
    write_run(root, [
        {"node_id": "t::a", "attempt": 1, "status": "failed"},
        {"node_id": "t::a", "attempt": 2, "status": "passed"},
    ], h)

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert "UNEXPLAINED_INTERMITTENT" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE
    assert rep.verified_scope["last_result"]["TC-1"] is True
    assert rep.verified_scope["gate_accepted"]["TC-1"] is None


def test_retry_policy_allowing_blocking_clear_is_quality_failure(clean):
    """重试策略放宽到可抹掉阻断缺陷 —— 本身就是质量规则失败。"""
    root, h = clean
    write_scope(root, ["TC-1"], allow_clear_blocking=True)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "RETRY_POLICY_UNSAFE" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL


def test_preapproved_retry_rule_permits_last_pass(clean):
    """预先批准的重试规则下，间歇失败可按规则处理 —— 但需显式配置。"""
    root, h = clean
    write_run(root, [
        {"node_id": "t::a", "attempt": 1, "status": "failed"},
        {"node_id": "t::a", "attempt": 2, "status": "passed"},
    ], h)
    write_scope(root, ["TC-1"], accept_last_pass=True)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "UNEXPLAINED_INTERMITTENT" not in codes(rep)
    assert rep.verified_scope["gate_accepted"]["TC-1"] is True


# ---------------------------------------------------------------------------
# 反例 3：手工必测项缺证据不得放行
# ---------------------------------------------------------------------------


def test_counterexample_3_manual_required_case_without_evidence_blocks(tmp_path):
    data = write_baseline(tmp_path, [req("REQ-1")])
    h = data["meta"]["content_hash"]
    write_cases(tmp_path, [case("TC-9", ["REQ-1"], manual=True)])
    write_collected(tmp_path, [])
    write_run(tmp_path, [], h)
    write_scope(tmp_path, ["TC-9"])

    rep = gc.evaluate(tmp_path, FEATURE, binding(baseline_hash=h))

    assert "MANUAL_EVIDENCE_ABSENT" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE
    assert "TC-9" in rep.unverified


# ---------------------------------------------------------------------------
# 反例 4：旧 SHA 的评审结论不得用于新候选
# ---------------------------------------------------------------------------


def test_counterexample_4_stale_review_verdict_rejected(clean):
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_OLD)

    rep = gc.evaluate(root, FEATURE, binding(candidate_sha=SHA_NEW, baseline_hash=h))

    assert "REVIEW_VERDICT_STALE" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_fresh_review_verdict_accepted(clean):
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW)
    rep = gc.evaluate(root, FEATURE, binding(candidate_sha=SHA_NEW, baseline_hash=h))
    assert rep.verdict == gc.VERDICT_PASS, codes(rep)


# ---------------------------------------------------------------------------
# 反例 5：候选自提 approved 不构成评审通过
# ---------------------------------------------------------------------------


def test_counterexample_5_self_submitted_approval_rejected(clean):
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    # 候选分支自己塞了一份"已批准"
    write_verdict(root, status="APPROVED", candidate_sha=SHA_NEW,
                  produced_by="candidate-branch")

    rep = gc.evaluate(root, FEATURE, binding(candidate_sha=SHA_NEW, baseline_hash=h))

    assert "REVIEW_VERDICT_UNTRUSTED" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL


def test_open_blocking_review_item_fails(clean):
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    write_verdict(root, status="BLOCKING", candidate_sha=SHA_NEW,
                  blocking_items=[{"id": "RV-1", "closed": False,
                                   "detail": "断言被删除且无替代覆盖"}])
    rep = gc.evaluate(root, FEATURE, binding(candidate_sha=SHA_NEW, baseline_hash=h))
    assert "REVIEW_BLOCKING_OPEN" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL


# ---------------------------------------------------------------------------
# 反例 6：automated_required 下评审失败不得切 manual 通过
# ---------------------------------------------------------------------------


def test_counterexample_6_missing_verdict_under_automated_required_is_incomplete(clean):
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    # 不产出 verdict（模拟 agent 超时 / 权限拒绝 / 输出无法解析）
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert "REVIEW_VERDICT_ABSENT" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_counterexample_6_switching_to_manual_after_failure_does_not_pass(clean):
    """把模式偷偷改成 manual 但没有平台审批记录 —— 仍然不得通过。"""
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="manual")
    rep = gc.evaluate(root, FEATURE,
                      binding(baseline_hash=h, human_approval_ref=None))
    assert "HUMAN_APPROVAL_ABSENT" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_unparseable_verdict_is_incomplete(clean):
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    write_verdict(root, status="¿?", candidate_sha=SHA_NEW)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "REVIEW_VERDICT_UNPARSEABLE" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_invalid_review_mode_is_incomplete(clean):
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="whatever")
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "REVIEW_MODE_INVALID" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


# ---------------------------------------------------------------------------
# 三态可区分性：FAIL ≠ INCOMPLETE
# ---------------------------------------------------------------------------


def test_quality_failure_yields_fail_not_incomplete(clean):
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "failed"}], h)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.verdict == gc.VERDICT_FAIL
    assert "REQUIRED_CASE_FAILED" in codes(rep)
    assert rep.counts["quality_failures"] >= 1


def test_missing_evidence_yields_incomplete_not_fail(clean):
    root, h = clean
    # 删掉运行记录 → 证据不足，但没有任何"明确失败"
    (root / "qa" / "runs" / "20260920-1200" / "run.json").unlink()
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.verdict == gc.VERDICT_INCOMPLETE
    assert rep.counts["quality_failures"] == 0
    assert rep.counts["evidence_missing"] >= 1


def test_fail_takes_precedence_over_incomplete(clean):
    """同时存在明确失败与证据缺失时，判 FAIL，但缺失项仍必须列全。"""
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "failed"}], h)
    write_scope(root, ["TC-1"], review_mode="automated_required")  # 缺 verdict

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert rep.verdict == gc.VERDICT_FAIL
    assert "REQUIRED_CASE_FAILED" in codes(rep)
    assert "REVIEW_VERDICT_ABSENT" in codes(rep)   # 不因汇总为 FAIL 而隐藏
    assert rep.counts["evidence_missing"] >= 1


def test_report_lists_all_findings_not_just_first(clean):
    root, h = clean
    (root / "qa" / "runs" / "20260920-1200" / "run.json").unlink()
    write_scope(root, ["TC-1"], review_mode="automated_required", severities=[])
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    # 至少同时含：阻断等级未定义、评审结论缺失、运行记录缺失
    assert "BLOCKING_SEVERITY_UNDEFINED" in codes(rep)
    assert "REVIEW_VERDICT_ABSENT" in codes(rep)
    assert "RUN_ABSENT" in codes(rep)


# ---------------------------------------------------------------------------
# 版本绑定
# ---------------------------------------------------------------------------


def test_missing_binding_field_is_incomplete(clean):
    root, h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h, target_sha=None))
    assert "BINDING_FIELD_MISSING" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_policy_from_merge_base_is_rejected(clean):
    """merge-base 是共同祖先，可能早于目标分支最新已批准规则。"""
    root, h = clean
    b = binding(baseline_hash=h)
    b["policy_source"] = "merge_base"
    rep = gc.evaluate(root, FEATURE, b)
    assert "POLICY_FROM_MERGE_BASE" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_policy_from_candidate_is_quality_failure(clean):
    """候选用自带的新规则给自己放行。"""
    root, h = clean
    b = binding(baseline_hash=h)
    b["policy_from_candidate"] = True
    rep = gc.evaluate(root, FEATURE, b)
    assert "POLICY_FROM_CANDIDATE" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL


def test_run_candidate_mismatch_is_incomplete(clean):
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "passed"}], h,
              candidate_sha=SHA_OLD)
    rep = gc.evaluate(root, FEATURE, binding(candidate_sha=SHA_NEW, baseline_hash=h))
    assert "RUN_CANDIDATE_MISMATCH" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_interrupted_run_is_incomplete(clean):
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "passed"}], h,
              interrupted=True)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "RUN_INTERRUPTED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_baseline_stale_blocks(clean):
    root, h = clean
    # 基线新增一条 → 哈希变化 → 旧运行证据 STALE
    write_baseline(root, [req("REQ-1"), req("REQ-2")], version="1.1.0")
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "EVIDENCE_STALE" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


# ---------------------------------------------------------------------------
# 其他完整性与安全
# ---------------------------------------------------------------------------


def test_blocking_severity_undefined_is_incomplete(clean):
    root, h = clean
    write_scope(root, ["TC-1"], severities=[])
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "BLOCKING_SEVERITY_UNDEFINED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_credential_in_run_config_is_quality_failure(clean):
    """脱敏验收：用形状真实的假凭据，断言具体敏感值被拦住，
    同时 traceId 等诊断字段保留。"""
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "passed"}], h,
              config={"env": "qa", "traceId": "abc-123",
                      "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.fake.sig"})
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "RUN_CONFIG_NOT_REDACTED" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL


def test_redacted_config_with_traceid_passes(clean):
    """脱敏后保留 traceId 不应被误判。"""
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "passed"}], h,
              config={"env": "qa", "traceId": "abc-123", "authorization": "<redacted>"})
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "RUN_CONFIG_NOT_REDACTED" not in codes(rep)
    assert rep.verdict == gc.VERDICT_PASS, codes(rep)


def test_thresholds_are_not_configured_in_v1(clean):
    """v1 不填任意阈值数字。"""
    root, h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.thresholds["status"] == "NOT_CONFIGURED"


def test_report_states_it_is_merge_gate_not_release(clean):
    root, h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "合并门禁" in rep.note
    assert "不是发布准出" in rep.note


# ---------------------------------------------------------------------------
# guard_scan 提示段落并入报告（R7 的落地路径）
# ---------------------------------------------------------------------------


def _guard_result(added_line="@pytest.mark.skip", path="tests/test_a.py",
                  root=None, with_waivers=False):
    import guard_scan as gs
    d = "\n".join([
        "diff --git a/{p} b/{p}".format(p=path),
        "--- a/{}".format(path),
        "+++ b/{}".format(path),
        "@@ -1,2 +1,3 @@",
        "+{}".format(added_line),
    ]) + "\n"
    return gs.scan_diff(d, root=root, with_waivers=with_waivers).as_dict()


def test_guard_hints_appear_in_report_as_hints(clean):
    root, h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h),
                      guard_result=_guard_result())
    guard_findings = [f for f in rep.findings if f.code.startswith("GUARD_SKIP_ADDED")]
    assert guard_findings
    assert guard_findings[0].kind == "HINT"
    assert "提示，非结论" in guard_findings[0].detail


def test_unreviewed_guard_hint_yields_incomplete(clean):
    """R7：命中项未关闭则计入 reviewer 阻断路径，不得被静默忽略。"""
    root, h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h),
                      guard_result=_guard_result())
    assert "GUARD_HINTS_UNREVIEWED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_guard_hint_acknowledged_by_reviewer_clears_it(clean):
    """合法处置：已验证的 verdict + 逐项 hint_id + 说明 + 替代覆盖。"""
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    guard = _guard_result()
    hint_id = guard["hints"][0]["hint_id"]

    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW)
    path = root / "qa" / "reports" / "review-verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guard_hints_acknowledged"] = [{
        "hint_id": hint_id,
        "reason": "上游接口已下线，该测试不再适用",
        "alternative_coverage": "TC-20 手工用例",
    }]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h), guard_result=guard)
    assert "GUARD_HINTS_UNREVIEWED" not in codes(rep)
    assert "GUARD_ACK_MALFORMED" not in codes(rep)
    assert rep.verdict == gc.VERDICT_PASS, codes(rep)


def test_ack_by_rule_code_only_is_rejected(clean):
    """F4 回归：只给规则码不构成处置。

    一个 SKIP_ADDED 可能出现在多个文件里，按规则码"确认"会一次关掉全部同类提示。
    """
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW)
    path = root / "qa" / "reports" / "review-verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guard_hints_acknowledged"] = ["SKIP_ADDED"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h),
                      guard_result=_guard_result())
    assert "GUARD_ACK_MALFORMED" in codes(rep)
    assert "GUARD_HINTS_UNREVIEWED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_ack_without_reason_or_coverage_is_rejected(clean):
    """F4 回归：逐项 hint_id 但缺说明或替代覆盖 —— 不算处置。"""
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    guard = _guard_result()
    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW)
    path = root / "qa" / "reports" / "review-verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guard_hints_acknowledged"] = [{"hint_id": guard["hints"][0]["hint_id"],
                                           "reason": "先放过"}]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h), guard_result=guard)
    assert "GUARD_ACK_MALFORMED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def _guard_result_two_files(root=None, with_waivers=False):
    """同一规则码、两个不同文件 —— 用来验证确认必须逐项、不能按规则码批量关闭。"""
    import guard_scan as gs
    parts = []
    for path in ("tests/test_a.py", "tests/test_b.py"):
        parts.append("\n".join([
            "diff --git a/{p} b/{p}".format(p=path),
            "--- a/{}".format(path),
            "+++ b/{}".format(path),
            "@@ -1,2 +1,3 @@",
            "+@pytest.mark.skip",
        ]) + "\n")
    return gs.scan_diff("".join(parts), root=root, with_waivers=with_waivers).as_dict()


def test_ack_with_code_field_instead_of_hint_id_is_rejected(clean):
    """F4 回归：处置记录用 `code` 字段冒充逐项标识 —— 不接受。

    由变异检验暴露：把 `hid = item.get("hint_id")` 放宽成
    `item.get("hint_id") or item.get("code")` 时，原有测试全绿。
    原因是当时的反例用的是裸字符串，会被 isinstance 检查挡掉，
    没有覆盖"字典里写 code"这条路径。
    """
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW)
    path = root / "qa" / "reports" / "review-verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guard_hints_acknowledged"] = [{
        "code": "SKIP_ADDED",
        "reason": "看起来很正式",
        "alternative_coverage": "TC-20",
    }]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h),
                      guard_result=_guard_result())
    assert "GUARD_ACK_MALFORMED" in codes(rep)
    assert "GUARD_HINTS_UNREVIEWED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_hint_ids_differ_per_file_for_same_rule(clean):
    """F4 回归：同一规则码在不同文件里必须是不同的 hint_id。"""
    guard = _guard_result_two_files()
    ids = [h["hint_id"] for h in guard["hints"]]
    assert len(guard["hints"]) == 2
    assert len(set(ids)) == 2, ids
    assert all(i.startswith("SKIP_ADDED:") for i in ids)


def test_ack_for_one_file_does_not_close_same_code_in_another_file(clean):
    """F4 回归（核心）：处置 test_a.py 的 SKIP_ADDED，不得连带关闭 test_b.py 的同类提示。

    由变异检验暴露：把 hint_id 退化成只用规则码时，原有测试全绿 ——
    因为每个用例里只有一条提示，测不出批量关闭。
    """
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    guard = _guard_result_two_files()
    first = [x for x in guard["hints"] if x["file"] == "tests/test_a.py"][0]

    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW)
    path = root / "qa" / "reports" / "review-verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guard_hints_acknowledged"] = [{
        "hint_id": first["hint_id"],
        "reason": "test_a 的上游接口已下线",
        "alternative_coverage": "TC-20",
    }]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h), guard_result=guard)

    unreviewed = [f for f in rep.findings if f.code == "GUARD_HINTS_UNREVIEWED"]
    assert unreviewed, "test_b.py 的提示未被处置，必须仍然计入"
    assert "tests/test_b.py" in unreviewed[0].detail
    assert "tests/test_a.py" not in unreviewed[0].detail
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_acking_both_files_clears_all(clean):
    """两条都逐项处置后方可通过。"""
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    guard = _guard_result_two_files()

    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW)
    path = root / "qa" / "reports" / "review-verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guard_hints_acknowledged"] = [
        {"hint_id": x["hint_id"], "reason": "已评估", "alternative_coverage": "TC-20"}
        for x in guard["hints"]
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h), guard_result=guard)
    assert "GUARD_HINTS_UNREVIEWED" not in codes(rep)
    assert rep.verdict == gc.VERDICT_PASS, codes(rep)


def test_ack_for_other_hint_does_not_close_this_one(clean):
    """F4 回归：hint_id 必须逐项对应，处置别的提示不能关掉这一条。"""
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    guard = _guard_result()
    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW)
    path = root / "qa" / "reports" / "review-verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guard_hints_acknowledged"] = [{
        "hint_id": "SKIP_ADDED:ffffffffffff",
        "reason": "x", "alternative_coverage": "TC-99",
    }]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h), guard_result=guard)
    assert "GUARD_HINTS_UNREVIEWED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_manual_mode_does_not_consume_local_verdict_for_guard_acks(clean):
    """F4 回归（核心误放行）：manual 模式下，候选往仓库塞一份 JSON 不能关掉提示。

    原缺陷：check_review 对 manual 提前返回、不做校验，evaluate 却仍从磁盘读同一份
    文件交给 check_guard_hints 消费 —— 于是 produced_by=candidate、旧 SHA 的文件
    也能把 INCOMPLETE 变成 PASS。
    """
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="manual")
    guard = _guard_result()
    path = root / "qa" / "reports" / "review-verdict.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": "APPROVED",
        "candidate_sha": SHA_OLD,
        "produced_by": "candidate",
        "guard_hints_acknowledged": [{
            "hint_id": guard["hints"][0]["hint_id"],
            "reason": "自己批准", "alternative_coverage": "无",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h), guard_result=guard)

    assert "REVIEW_VERDICT_IGNORED_IN_MANUAL_MODE" in codes(rep)
    assert "GUARD_HINTS_UNREVIEWED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_untrusted_verdict_cannot_close_guard_hints(clean):
    """automated_required 下来源不可信的 verdict 也不能关闭提示。"""
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    guard = _guard_result()
    write_verdict(root, status="NO_BLOCKING", candidate_sha=SHA_NEW,
                  produced_by="candidate-branch")
    path = root / "qa" / "reports" / "review-verdict.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guard_hints_acknowledged"] = [{
        "hint_id": guard["hints"][0]["hint_id"],
        "reason": "x", "alternative_coverage": "y",
    }]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h), guard_result=guard)
    assert "REVIEW_VERDICT_UNTRUSTED" in codes(rep)
    assert "GUARD_HINTS_UNREVIEWED" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL


def test_waived_guard_hint_does_not_require_review(clean):
    """带原因与替代覆盖的豁免可关闭提示。"""
    root, h = clean
    wpath = root / "qa" / "plan" / "guard-waivers.yaml"
    wpath.parent.mkdir(parents=True, exist_ok=True)
    wpath.write_text(yaml.safe_dump({"waivers": [{
        "code": "SKIP_ADDED",
        "file": "tests/test_a.py",
        "reason": "上游接口下线",
        "alternative_coverage": "TC-20",
    }]}, allow_unicode=True), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h),
                      guard_result=_guard_result(root=root, with_waivers=True))
    assert "GUARD_HINTS_UNREVIEWED" not in codes(rep)
    waived = [f for f in rep.findings if f.code == "GUARD_SKIP_ADDED"]
    assert waived and "已豁免" in waived[0].detail
    assert rep.verdict == gc.VERDICT_PASS, codes(rep)


def test_waiver_without_alternative_coverage_still_requires_review(clean):
    """只写原因不给替代覆盖 —— 豁免不生效，仍需评审确认。"""
    root, h = clean
    wpath = root / "qa" / "plan" / "guard-waivers.yaml"
    wpath.parent.mkdir(parents=True, exist_ok=True)
    wpath.write_text(yaml.safe_dump({"waivers": [{
        "code": "SKIP_ADDED",
        "file": "tests/test_a.py",
        "reason": "先跳过",
    }]}, allow_unicode=True), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h),
                      guard_result=_guard_result(root=root, with_waivers=True))
    assert "GUARD_HINTS_UNREVIEWED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_guard_hints_never_produce_quality_failure(clean):
    """提示器不产生 FAIL —— 判定权在人，不在正则。"""
    root, h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h),
                      guard_result=_guard_result())
    guard = [f for f in rep.findings if f.code.startswith("GUARD_")]
    assert guard
    assert all(f.kind != "QUALITY_FAILURE" for f in guard
               if f.code != "GUARD_HINTS_UNREVIEWED")
    assert rep.verdict != gc.VERDICT_FAIL


def test_no_guard_result_does_not_affect_verdict(clean):
    root, h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h), guard_result=None)
    assert rep.verdict == gc.VERDICT_PASS
    assert not [f for f in rep.findings if f.code.startswith("GUARD_")]


def test_serialized_report_contains_every_finding(clean):
    """R6：报告始终列全所有失败与缺失项。

    这条测的是**序列化产物**，不是内存里的 findings 属性 —— CI 写进
    merge-gate-report.json、人实际读到的是序列化结果。截断它就等于隐藏明细。
    """
    root, h = clean
    (root / "qa" / "runs" / "20260920-1200" / "run.json").unlink()
    write_scope(root, ["TC-1"], review_mode="automated_required", severities=[])
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h, target_sha=None))

    assert len(rep.findings) >= 4, "本场景应产生多条发现，否则测不出截断"

    payload = json.loads(json.dumps(rep.as_dict(), ensure_ascii=False))
    serialized_codes = [f["code"] for f in payload["findings"]]

    assert len(serialized_codes) == len(rep.findings), \
        "序列化后的 findings 数量与实际不一致 —— 报告隐藏了明细"
    for f in rep.findings:
        assert f["code"] if isinstance(f, dict) else f.code in serialized_codes


def test_serialized_report_preserves_all_finding_kinds(clean):
    """同时存在质量失败与证据缺失时，序列化产物必须两类都保留。"""
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "failed"}], h)
    write_scope(root, ["TC-1"], review_mode="automated_required", severities=[])
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    payload = rep.as_dict()
    kinds = {f["kind"] for f in payload["findings"]}
    codes_out = {f["code"] for f in payload["findings"]}

    assert "QUALITY_FAILURE" in kinds
    assert "EVIDENCE_MISSING" in kinds
    assert {"REQUIRED_CASE_FAILED", "REVIEW_VERDICT_ABSENT",
            "BLOCKING_SEVERITY_UNDEFINED"} <= codes_out


def test_rendered_text_contains_every_finding_code(clean):
    """人可读报告同样不得省略任何一条。"""
    root, h = clean
    (root / "qa" / "runs" / "20260920-1200" / "run.json").unlink()
    write_scope(root, ["TC-1"], review_mode="automated_required", severities=[])
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    text = gc.render_text(rep)
    for f in rep.findings:
        assert f.code in text, "人可读报告漏掉了 {}".format(f.code)


# ---------------------------------------------------------------------------
# F1 回归：按 REQ 指定的必测范围必须参与结果判定
# ---------------------------------------------------------------------------


def _scope_by_req(root, reqs, cases=None, review_mode="manual"):
    data = {
        "meta": {"feature": FEATURE, "baseline_version": "1.0.0",
                 "confirmed_by": "zhang.san"},
        "required_requirements": reqs,
        "required_cases": cases or [],
        "retry_policy": {"max_attempts": 1,
                         "accept_last_pass_without_explanation": False,
                         "allow_retry_to_clear_confirmed_blocking_defect": False},
        "blocking_defect_severities": ["S1"],
        "review_mode": review_mode,
    }
    path = root / "qa" / "plan" / FEATURE / "required-scope.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def test_required_req_expands_to_cases_and_failure_fails(clean):
    """原缺陷：required_requirements 只校验存在性，真正的结果判定只遍历
    required_cases。于是 required_cases 为空时，关联测试失败也照样 PASS。"""
    root, h = clean
    _scope_by_req(root, ["REQ-1"], cases=[])
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "failed"}], h)

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert rep.verdict == gc.VERDICT_FAIL
    assert "REQUIRED_CASE_FAILED" in codes(rep)
    assert rep.verified_scope["required_cases_effective"] == ["TC-1"]
    assert rep.verified_scope["required_cases_declared"] == []


def test_required_req_expands_to_manual_case_without_evidence(clean):
    root, h = clean
    write_cases(root, [case("TC-1", ["REQ-1"], manual=True)])
    write_collected(root, [])
    write_run(root, [], h)
    _scope_by_req(root, ["REQ-1"], cases=[])

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert "MANUAL_EVIDENCE_ABSENT" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_scope_expansion_is_visible_in_report(clean):
    """两份范围清单不一致时必须可见，否则读报告的人会以为声明的就是全部。"""
    root, h = clean
    _scope_by_req(root, ["REQ-1"], cases=[])
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "SCOPE_EXPANDED_FROM_REQ" in codes(rep)
    text = gc.render_text(rep)
    assert "有效" in text


def test_required_req_without_any_case_blocks(clean):
    root, h = clean
    write_cases(root, [case("TC-1", ["REQ-1"])])
    _scope_by_req(root, ["REQ-1"], cases=[])
    # 把基线换成含 REQ-2 且无用例覆盖
    write_baseline(root, [req("REQ-1"), req("REQ-2")], version="1.0.0")
    _scope_by_req(root, ["REQ-2"], cases=[])
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "REQUIRED_REQ_WITHOUT_CASE" in codes(rep)
    assert rep.verdict != gc.VERDICT_PASS


# ---------------------------------------------------------------------------
# F2 回归：缺字段不得跳过版本核对
# ---------------------------------------------------------------------------


def test_run_without_candidate_sha_is_incomplete(clean):
    """原缺陷："字段非空且不相等才报错" —— 删掉字段反而通过。"""
    root, h = clean
    path = root / "qa" / "runs" / "20260920-1200" / "run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["candidate_sha"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "RUN_CANDIDATE_SHA_ABSENT" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_run_without_baseline_hash_is_incomplete(clean):
    root, h = clean
    path = root / "qa" / "runs" / "20260920-1200" / "run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["baseline_hash"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "RUN_BASELINE_HASH_ABSENT" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_binding_hash_differing_from_actual_baseline_is_incomplete(clean):
    """调用方传的 binding 六字段齐全，但与磁盘上的实际基线不是同一份。"""
    root, _h = clean
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash="sha256:" + "0" * 64))
    assert "BINDING_BASELINE_HASH_MISMATCH" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_run_declaring_stale_target_sha_is_incomplete(clean):
    root, h = clean
    path = root / "qa" / "runs" / "20260920-1200" / "run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["target_sha"] = "0000000"
    payload["policy_version"] = "0.0.1"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "RUN_TARGET_SHA_MISMATCH" in codes(rep)
    assert "RUN_POLICY_VERSION_MISMATCH" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_review_verdict_without_candidate_sha_is_incomplete(clean):
    root, h = clean
    write_scope(root, ["TC-1"], review_mode="automated_required")
    write_verdict(root, status="NO_BLOCKING", candidate_sha=None)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "REVIEW_VERDICT_UNBOUND" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


# ---------------------------------------------------------------------------
# F3 回归：只关联 REQ 的阻断缺陷必须传播
# ---------------------------------------------------------------------------


def test_req_level_blocking_defect_blocks(clean):
    """原缺陷：load_defects 返回的 by_req 映射被存入变量后从未使用。"""
    root, h = clean
    fm = {"id": "BUG-9", "tc": [], "req": ["REQ-1"], "severity": "S1",
          "confirmed_blocking": True, "root_cause": "PRODUCT",
          "stability": "稳定失败"}
    p = root / "qa" / "defects" / "BUG-9.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n",
                 encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert "BLOCKING_DEFECT_NOT_CLEARED" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL
    assert rep.verified_scope["last_result"]["TC-1"] is True
    assert rep.verified_scope["gate_accepted"]["TC-1"] is False


def test_closed_defect_does_not_block(clean):
    """关闭状态参与判定：已关闭的缺陷记录在案但不阻断。"""
    root, h = clean
    fm = {"id": "BUG-10", "tc": ["TC-1"], "req": [], "severity": "S1",
          "confirmed_blocking": True, "closed": True,
          "root_cause": "PRODUCT", "stability": "稳定失败"}
    p = root / "qa" / "defects" / "BUG-10.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n",
                 encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "BLOCKING_DEFECT_NOT_CLEARED" not in codes(rep)
    assert rep.verdict == gc.VERDICT_PASS, codes(rep)


def test_severity_in_blocking_list_blocks_even_without_confirmation(clean):
    """blocking_defect_severities 必须真的参与计算，不能只是占位配置。"""
    root, h = clean
    fm = {"id": "BUG-11", "tc": ["TC-1"], "req": [], "severity": "S1",
          "confirmed_blocking": False, "root_cause": "PRODUCT",
          "stability": "稳定失败"}
    p = root / "qa" / "defects" / "BUG-11.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n",
                 encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "BLOCKING_DEFECT_NOT_CLEARED" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL


def test_confirmed_blocking_blocks_even_when_severity_not_listed(clean):
    """两条阻断路径必须独立生效：人已确认阻断时，即使严重级不在策略清单内也要阻断。

    由变异检验暴露：把条件从 `confirmed_blocking or severity_blocking` 改成只看
    `severity_blocking`，66 项测试仍全过 —— 因为此前所有 confirmed_blocking 用例的
    severity 都恰好是 S1（在阻断清单内），两条路径从未被分别测到。
    """
    root, h = clean
    write_scope(root, ["TC-1"], severities=["S1"])
    fm = {"id": "BUG-13", "tc": ["TC-1"], "req": [], "severity": "S3",
          "confirmed_blocking": True, "root_cause": "PRODUCT",
          "stability": "稳定失败"}
    p = root / "qa" / "defects" / "BUG-13.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n",
                 encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "BLOCKING_DEFECT_NOT_CLEARED" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL
    detail = [f for f in rep.findings if f.code == "BLOCKING_DEFECT_NOT_CLEARED"][0].detail
    assert "已确认阻断" in detail


def test_req_level_confirmed_blocking_with_unlisted_severity_blocks(clean):
    """REQ 级传播 + 仅靠 confirmed_blocking 的组合也要阻断。"""
    root, h = clean
    write_scope(root, ["TC-1"], severities=["S1"])
    fm = {"id": "BUG-14", "tc": [], "req": ["REQ-1"], "severity": "S4",
          "confirmed_blocking": True, "root_cause": "PRODUCT",
          "stability": "间歇失败"}
    p = root / "qa" / "defects" / "BUG-14.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n",
                 encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "BLOCKING_DEFECT_NOT_CLEARED" in codes(rep)
    assert rep.verdict == gc.VERDICT_FAIL


def test_severity_outside_blocking_list_does_not_block(clean):
    root, h = clean
    write_scope(root, ["TC-1"], severities=["S1"])
    fm = {"id": "BUG-12", "tc": ["TC-1"], "req": [], "severity": "S3",
          "confirmed_blocking": False, "root_cause": "PRODUCT",
          "stability": "稳定失败"}
    p = root / "qa" / "defects" / "BUG-12.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n",
                 encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "BLOCKING_DEFECT_NOT_CLEARED" not in codes(rep)
    assert rep.verdict == gc.VERDICT_PASS, codes(rep)


# ---------------------------------------------------------------------------
# F5 回归：缺失 / 跳过不是质量失败
# ---------------------------------------------------------------------------


def test_empty_attempts_is_incomplete_not_fail(clean):
    """run.json 存在但 attempts 为空 —— 没测，不是测出失败。"""
    root, h = clean
    write_run(root, [], h)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert rep.verdict == gc.VERDICT_INCOMPLETE
    assert rep.counts["quality_failures"] == 0
    assert "REQUIRED_CASE_FAILED" not in codes(rep)
    assert "REQUIRED_CASE_RESULT_MISSING" in codes(rep)
    assert rep.verified_scope["last_result"]["TC-1"] is None


def test_skipped_required_test_is_incomplete_not_fail(clean):
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "skipped"}], h)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))

    assert rep.verdict == gc.VERDICT_INCOMPLETE
    assert rep.counts["quality_failures"] == 0
    assert "REQUIRED_CASE_RESULT_MISSING" in codes(rep)


def test_error_status_is_a_real_failure(clean):
    """error 与 skipped 不同：它是执行中发生的失败，算质量失败。"""
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "error"}], h)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.verdict == gc.VERDICT_FAIL
    assert "REQUIRED_CASE_FAILED" in codes(rep)


def test_failed_plus_skipped_still_fails(clean):
    """一个 AT 失败、另一个跳过 —— 失败优先，但缺失项仍列全。"""
    root, h = clean
    write_collected(root, [
        {"node_id": "t::a", "cases": ["TC-1"]},
        {"node_id": "t::b", "cases": ["TC-1"]},
    ])
    write_run(root, [
        {"node_id": "t::a", "attempt": 1, "status": "failed"},
        {"node_id": "t::b", "attempt": 1, "status": "skipped"},
    ], h)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.verdict == gc.VERDICT_FAIL
    assert "REQUIRED_CASE_FAILED" in codes(rep)


def test_render_text_shows_both_failure_kinds_distinctly(clean):
    root, h = clean
    write_run(root, [{"node_id": "t::a", "attempt": 1, "status": "failed"}], h)
    write_scope(root, ["TC-1"], review_mode="automated_required")
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    text = gc.render_text(rep)
    assert "质量失败" in text
    assert "证据缺失" in text
    assert "未验证项" in text


# ---------------------------------------------------------------------------
# 证据来源可信性（外部复核：判定树漏掉缺陷记录 → 已确认阻断消失）
# ---------------------------------------------------------------------------


def test_evidence_source_missing_is_incomplete(clean):
    """缺来源声明 → INCOMPLETE。缺失不得等同于"零阻断"。"""
    root, h = clean
    (root / "qa" / "evidence-source.yaml").unlink()
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.verdict == gc.VERDICT_INCOMPLETE
    assert "EVIDENCE_SOURCE_UNDECLARED" in codes(rep)


@pytest.mark.parametrize("kind_name", ["collect", "runs", "defects"])
def test_candidate_copy_source_is_untrusted(clean, kind_name):
    """任一类证据来自候选副本 → INCOMPLETE。

    候选能任意增删自己的缺陷记录与产物；"文件存在且能解析"不构成可信。
    """
    root, h = clean
    kwargs = {kind_name: "candidate_copy"}
    write_evidence_source(root, **kwargs)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.verdict == gc.VERDICT_INCOMPLETE
    assert "EVIDENCE_SOURCE_UNTRUSTED" in codes(rep)
    assert any(f.subject == kind_name for f in rep.findings
               if f.code == "EVIDENCE_SOURCE_UNTRUSTED")


def test_unknown_source_kind_is_incomplete(clean):
    root, h = clean
    write_evidence_source(root, runs="magic")
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "EVIDENCE_SOURCE_KIND_UNKNOWN" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_trusted_source_without_ref_is_incomplete(clean):
    """声明可信但没有 ref → 无法回溯到具体导出/运行。"""
    root, h = clean
    write_evidence_source(root, refs=False)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "EVIDENCE_SOURCE_REF_ABSENT" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_missing_entry_is_incomplete(clean):
    root, h = clean
    path = root / "qa" / "evidence-source.yaml"
    path.write_text(yaml.safe_dump({"runs": {"kind": "trusted_ci",
                                             "ref": "x"}}),
                    encoding="utf-8")
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    found = codes(rep)
    assert found.count("EVIDENCE_SOURCE_ENTRY_ABSENT") == 2, found  # collect + defects
    assert rep.verdict == gc.VERDICT_INCOMPLETE


def test_non_mapping_source_file_is_incomplete(clean):
    root, h = clean
    (root / "qa" / "evidence-source.yaml").write_text("- a\n- b\n", encoding="utf-8")
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert "EVIDENCE_SOURCE_INVALID" in codes(rep)


def test_confirmed_blocking_defect_still_blocks_with_trusted_sources(clean):
    """阳性对照：带可信来源声明时，已确认阻断缺陷必须照常 FAIL。

    这条和上面几条一起构成"输入齐全时会阻断 / 输入缺失时不会静默通过"的对照。
    """
    root, h = clean
    write_defect(root, "BUG-1", ["TC-1"], confirmed_blocking=True)
    rep = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert rep.verdict == gc.VERDICT_FAIL
    assert "BLOCKING_DEFECT_NOT_CLEARED" in codes(rep)


def test_dropping_defects_dir_does_not_silently_pass(clean):
    """复核的核心反例：把缺陷记录从判定输入里去掉，不得变成 PASS / findings=[]。

    现在缺陷目录缺失时，来源声明仍要求 defects 为可信来源；若组装时漏掉整份证据，
    声明也无从满足 → INCOMPLETE。
    """
    root, h = clean
    write_defect(root, "BUG-1", ["TC-1"], confirmed_blocking=True)
    before = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert before.verdict == gc.VERDICT_FAIL

    # 模拟"组装判定树时漏掉 qa/defects"
    import shutil
    shutil.rmtree(root / "qa" / "defects")
    # 同时模拟组装方只能声明它拿到的是候选副本 / 或干脆声明不了
    (root / "qa" / "evidence-source.yaml").unlink()
    after = gc.evaluate(root, FEATURE, binding(baseline_hash=h))
    assert after.verdict != gc.VERDICT_PASS, codes(after)
    assert after.verdict == gc.VERDICT_INCOMPLETE


# ---------------------------------------------------------------------------
# 缺证据分支也必须落盘报告（always() 不保证脚本写文件）
# ---------------------------------------------------------------------------


def test_report_written_even_when_no_baseline(tmp_path, monkeypatch, capsys):
    """无有效基线时 CLI 提前 return，但 --out 必须已经写出 INCOMPLETE 报告。"""
    out = tmp_path / "reports" / "merge-gate-report.json"
    empty_root = tmp_path / "empty"
    (empty_root / "qa").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", [
        "gate_check.py", "--root", str(empty_root), "--json",
        "--candidate-sha", SHA_NEW, "--out", str(out),
    ])
    rc = gc.main()
    assert rc == 1
    assert out.is_file(), "缺基线时没有落盘报告 —— CI 的 always() 救不了这种情况"
    data = json.loads(out.read_text())
    assert data["verdict"] == gc.VERDICT_INCOMPLETE
    assert data["findings"][0]["code"] == "BASELINE_ABSENT"
    assert data["binding"]["candidate_sha"] == SHA_NEW

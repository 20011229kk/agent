"""trace_matrix.py 测试 —— 以反例为主。

每个测试在临时目录里造一份最小仓库，只注入一个缺陷，断言对应缺口码出现。
重点覆盖 R4 的几个容易被做漂亮的地方：
  * 假全覆盖（分母来自基线，不来自矩阵）
  * 证据缺失 ≠ 已确认未自动化
  * manual 用例不进自动化分母但进必测校验
  * 手工用例无 AT、通过用例无缺陷不得判错
"""
import json
import pathlib
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import trace_matrix as tm  # noqa: E402

FEATURE = "login"


# ---------------------------------------------------------------------------
# fixture 构造
# ---------------------------------------------------------------------------


def write_baseline(root, reqs, version="1.0.0", with_hash=True,
                   confirmed_by="zhang.san", approval_ref="https://example/mr/1"):
    data = {
        "meta": {
            "feature": FEATURE,
            "baseline_version": version,
            "confirmed_by": confirmed_by,
            "confirmed_at": "2026-09-20",
            "baseline_approval_ref": approval_ref,
        },
        "requirements": reqs,
    }
    if with_hash:
        data["meta"]["content_hash"] = tm.compute_baseline_hash(data)
    path = root / "qa" / "baseline" / FEATURE / "requirements.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return data


def req(rid, blocking=True):
    return {"id": rid, "statement": "验收标准 {}".format(rid),
            "risk": "high", "blocking": blocking}


def write_cases(root, cases, version="1.0.0"):
    fm = {"feature": FEATURE, "baseline_version": version, "cases": cases}
    path = root / "qa" / "cases" / FEATURE / "core.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n\n# cases\n",
        encoding="utf-8",
    )


def case(tid, covers, manual=False):
    return {"id": tid, "covers": covers, "manual": manual,
            "layer": "api", "priority": "P0", "method": "等价类"}


def write_collected(root, tests):
    path = root / "qa" / "trace" / FEATURE / "collected.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"framework": "pytest", "tests": tests},
                               ensure_ascii=False), encoding="utf-8")


def write_run(root, attempts, baseline_hash=None, run_id="20260920-1200"):
    path = root / "qa" / "runs" / run_id / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "candidate_sha": "abc123",
        "baseline_version": "1.0.0",
        "attempts": attempts,
    }
    if baseline_hash:
        payload["baseline_hash"] = baseline_hash
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_scope(root, required_cases=None, required_reqs=None):
    data = {
        "meta": {"feature": FEATURE, "baseline_version": "1.0.0",
                 "confirmed_by": "zhang.san"},
        "required_requirements": required_reqs or [],
        "required_cases": required_cases or [],
        "retry_policy": {"max_attempts": 1,
                         "accept_last_pass_without_explanation": False,
                         "allow_retry_to_clear_confirmed_blocking_defect": False},
        "blocking_defect_severities": ["S1"],
        "review_mode": "manual",
    }
    path = root / "qa" / "plan" / FEATURE / "required-scope.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def write_defect(root, did, tcs, severity="S1", confirmed_blocking=True,
                 root_cause="PRODUCT", stability="稳定失败"):
    fm = {"id": did, "tc": tcs, "req": [], "severity": severity,
          "confirmed_blocking": confirmed_blocking,
          "root_cause": root_cause, "stability": stability}
    path = root / "qa" / "defects" / "{}.md".format(did)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n\n# defect\n",
        encoding="utf-8",
    )


def codes(report):
    return [g.code for g in report.gaps]


def build(root, **kw):
    return tm.build_report(pathlib.Path(root), FEATURE, **kw)


@pytest.fixture()
def happy(tmp_path):
    """一个干净的最小仓库：2 REQ / 2 TC / 2 AT / 全部通过。"""
    data = write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"]), case("TC-2", ["REQ-2"])])
    write_collected(tmp_path, [
        {"node_id": "tests/t.py::test_a", "cases": ["TC-1"]},
        {"node_id": "tests/t.py::test_b", "cases": ["TC-2"]},
    ])
    write_run(tmp_path, [
        {"node_id": "tests/t.py::test_a", "attempt": 1, "status": "passed"},
        {"node_id": "tests/t.py::test_b", "attempt": 1, "status": "passed"},
    ], baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1", "TC-2"], required_reqs=["REQ-1", "REQ-2"])
    return tmp_path


# ---------------------------------------------------------------------------
# 基准
# ---------------------------------------------------------------------------


def test_happy_path_has_no_blocking_gap(happy):
    rep = build(happy)
    assert not rep.has_blocking, codes(rep)
    assert rep.counts["req_designed"] == 2
    assert rep.counts["tc_automated"] == 2
    assert rep.counts["tc_executed"] == 2
    assert rep.counts["tc_last_pass"] == 2


def test_passing_case_without_defect_is_not_an_error(happy):
    """通过的用例没有缺陷是正常状态，不得判错。"""
    rep = build(happy)
    assert "MISSING_DEFECT" not in codes(rep)
    assert rep.per_case["TC-1"]["defects"] == []


# ---------------------------------------------------------------------------
# 假全覆盖 —— R4 的核心反例
# ---------------------------------------------------------------------------


def test_fake_full_coverage_is_detected(tmp_path):
    """基线 10 条，用例只覆盖 8 条：必须报 2 条未覆盖，不能算成全覆盖。"""
    reqs = [req("REQ-{}".format(i)) for i in range(1, 11)]
    write_baseline(tmp_path, reqs)
    write_cases(tmp_path, [case("TC-{}".format(i), ["REQ-{}".format(i)])
                           for i in range(1, 9)])
    write_scope(tmp_path)
    rep = build(tmp_path)

    uncovered = [g.subject for g in rep.gaps if g.code == "UNCOVERED_REQ"]
    assert sorted(uncovered) == ["REQ-10", "REQ-9"]
    assert rep.counts["req_total"] == 10
    assert rep.counts["req_designed"] == 8


def test_empty_baseline_does_not_pass(tmp_path):
    write_baseline(tmp_path, [])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "BASELINE_EMPTY" in codes(rep)
    assert rep.has_blocking


def test_absent_baseline_is_blocking(tmp_path):
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "BASELINE_ABSENT" in codes(rep)
    assert rep.has_blocking


def test_baseline_meta_incomplete_is_blocking(tmp_path):
    """基线未确认（缺审批引用）→ 阻断，门禁应判 INCOMPLETE。"""
    write_baseline(tmp_path, [req("REQ-1")], approval_ref=None)
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "BASELINE_META_INCOMPLETE" in codes(rep)


# ---------------------------------------------------------------------------
# 证据缺失 ≠ 已确认未自动化
# ---------------------------------------------------------------------------


def test_missing_collect_manifest_yields_unknown_not_zero(tmp_path):
    """没有 collect 清单时，automated 必须是 unknown（None），不能当成 False。"""
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    rep = build(tmp_path)

    assert rep.automation_evidence == "AUTOMATION_EVIDENCE_ABSENT"
    assert "AUTOMATION_EVIDENCE_ABSENT" in codes(rep)
    assert rep.per_case["TC-1"]["automated"] is None
    # 关键：不得因证据缺失而产生"漏标"结论
    assert "UNAUTOMATED_TC" not in codes(rep)


def test_missing_run_yields_unknown_execution(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert rep.run_evidence == "RUN_EVIDENCE_ABSENT"
    assert rep.per_case["TC-1"]["executed"] is None
    assert rep.per_case["TC-1"]["last_result"] is None


# ---------------------------------------------------------------------------
# manual 用例
# ---------------------------------------------------------------------------


def test_manual_case_excluded_from_automation_denominator(tmp_path):
    write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")])
    write_cases(tmp_path, [
        case("TC-1", ["REQ-1"]),
        case("TC-9", ["REQ-2"], manual=True),
    ])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_scope(tmp_path)
    rep = build(tmp_path)

    assert rep.counts["tc_total"] == 2
    assert rep.counts["tc_manual"] == 1
    assert rep.counts["tc_automatable"] == 1
    assert rep.counts["tc_automated"] == 1


def test_manual_case_without_at_is_not_an_error(tmp_path):
    """手工用例没有自动化脚本是正常状态，不得报漏标。"""
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-9", ["REQ-1"], manual=True)])
    write_collected(tmp_path, [])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "UNAUTOMATED_TC" not in codes(rep)


def test_manual_required_case_without_evidence_blocks(tmp_path):
    """反例 3：手工必测项缺证据不得放行。"""
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-9", ["REQ-1"], manual=True)])
    write_collected(tmp_path, [])
    write_run(tmp_path, [])
    write_scope(tmp_path, required_cases=["TC-9"])
    rep = build(tmp_path)

    assert "MANUAL_EVIDENCE_ABSENT" in codes(rep)
    assert rep.has_blocking


# ---------------------------------------------------------------------------
# 错标 / 漏标 / 未归属
# ---------------------------------------------------------------------------


def test_mislabeled_covers_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-404"])])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "MISLABELED_COVERS" in codes(rep)
    assert rep.has_blocking


def test_mislabeled_case_marker_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-404"]}])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "MISLABELED_CASE_MARKER" in codes(rep)


def test_unautomated_non_manual_case_reports_gap(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "UNAUTOMATED_TC" in codes(rep)


def test_unattributed_test_is_hint_only(tmp_path):
    """AT 没有 @case 标记只提示，不阻断。"""
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [
        {"node_id": "t::a", "cases": ["TC-1"]},
        {"node_id": "t::orphan", "cases": []},
    ])
    write_scope(tmp_path)
    rep = build(tmp_path)
    hints = [g for g in rep.gaps if g.code == "UNATTRIBUTED_AT"]
    assert len(hints) == 1
    assert hints[0].severity == "HINT"


def test_duplicate_tc_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"]), case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "DUPLICATE_TC" in codes(rep)


def test_duplicate_req_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1"), req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "DUPLICATE_REQ" in codes(rep)


# ---------------------------------------------------------------------------
# 参数化与多对多
# ---------------------------------------------------------------------------


def test_parametrized_case_requires_all_params_to_pass(tmp_path):
    """同一 TC 的多个 param，只要有一个没过，该 TC 就不算末次通过。"""
    data = write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [
        {"node_id": "t::a", "cases": ["TC-1"], "params": "min"},
        {"node_id": "t::a", "cases": ["TC-1"], "params": "max"},
    ])
    write_run(tmp_path, [
        {"node_id": "t::a", "params": "min", "attempt": 1, "status": "passed"},
        {"node_id": "t::a", "params": "max", "attempt": 1, "status": "failed"},
    ], baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)
    assert rep.per_case["TC-1"]["last_result"] is False


def test_many_to_many_requires_all_ats_to_pass(tmp_path):
    """一个 TC 关联多个 AT，全部通过才算末次通过。"""
    data = write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [
        {"node_id": "t::a", "cases": ["TC-1"]},
        {"node_id": "t::b", "cases": ["TC-1"]},
    ])
    write_run(tmp_path, [
        {"node_id": "t::a", "attempt": 1, "status": "passed"},
        {"node_id": "t::b", "attempt": 1, "status": "failed"},
    ], baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)
    assert rep.per_case["TC-1"]["last_result"] is False


def test_one_at_covering_multiple_cases(tmp_path):
    data = write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"]), case("TC-2", ["REQ-2"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1", "TC-2"]}])
    write_run(tmp_path, [{"node_id": "t::a", "attempt": 1, "status": "passed"}],
              baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert rep.per_case["TC-1"]["automated"] is True
    assert rep.per_case["TC-2"]["automated"] is True


# ---------------------------------------------------------------------------
# 重试与间歇失败
# ---------------------------------------------------------------------------


def test_retry_pass_is_marked_intermittent(tmp_path):
    """先失败后通过：last_result 为 True，但必须标记 intermittent，
    不得因此消除产品缺陷可能。gate_accepted 由 gate_check 另行判定。"""
    data = write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_run(tmp_path, [
        {"node_id": "t::a", "attempt": 1, "status": "failed"},
        {"node_id": "t::a", "attempt": 2, "status": "passed"},
    ], baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)

    info = rep.per_case["TC-1"]
    assert info["last_result"] is True
    assert info["intermittent"] is True


def test_confirmed_blocking_defect_is_surfaced(tmp_path):
    """即使末次通过，已确认的阻断缺陷也必须出现在矩阵里。"""
    data = write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_run(tmp_path, [
        {"node_id": "t::a", "attempt": 1, "status": "failed"},
        {"node_id": "t::a", "attempt": 2, "status": "passed"},
    ], baseline_hash=data["meta"]["content_hash"])
    write_defect(tmp_path, "BUG-1", ["TC-1"], confirmed_blocking=True)
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)

    info = rep.per_case["TC-1"]
    assert info["last_result"] is True
    assert info["confirmed_blocking_defect"] is True


def test_flaky_as_root_cause_is_rejected(tmp_path):
    """FLAKY 不是根因取值。"""
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    write_defect(tmp_path, "BUG-2", ["TC-1"], root_cause="FLAKY")
    rep = build(tmp_path)
    assert "DEFECT_ROOT_CAUSE_INVALID" in codes(rep)


# ---------------------------------------------------------------------------
# STALE 与哈希
# ---------------------------------------------------------------------------


def test_baseline_change_makes_evidence_stale(tmp_path):
    data = write_baseline(tmp_path, [req("REQ-1")])
    old_hash = data["meta"]["content_hash"]
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_run(tmp_path, [{"node_id": "t::a", "attempt": 1, "status": "passed"}],
              baseline_hash=old_hash)
    write_scope(tmp_path, required_cases=["TC-1"])

    # 基线新增一条 REQ → 内容哈希变化 → 旧运行证据失效
    write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")], version="1.1.0")

    rep = build(tmp_path)
    assert rep.stale is True
    assert "EVIDENCE_STALE" in codes(rep)


def test_baseline_hash_not_recomputed_is_blocking(tmp_path):
    """基线内容被改但 content_hash 没更新 —— 必须报不符。"""
    data = write_baseline(tmp_path, [req("REQ-1")])
    path = tmp_path / "qa" / "baseline" / FEATURE / "requirements.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["requirements"].append(req("REQ-2"))          # 改内容
    # 故意不更新 content_hash
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    write_cases(tmp_path, [case("TC-1", ["REQ-1"]), case("TC-2", ["REQ-2"])])
    write_scope(tmp_path)

    rep = build(tmp_path)
    assert "BASELINE_HASH_MISMATCH" in codes(rep)
    assert data["meta"]["content_hash"] != rep.baseline_hash_current


def test_update_hash_makes_it_consistent(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")], with_hash=False)
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)

    new_hash = tm.update_hash(tmp_path, FEATURE)
    rep = build(tmp_path)
    assert new_hash == rep.baseline_hash_current
    assert "BASELINE_HASH_MISMATCH" not in codes(rep)


# ---------------------------------------------------------------------------
# 必测范围
# ---------------------------------------------------------------------------


def test_required_scope_absent_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    rep = build(tmp_path)
    assert "REQUIRED_SCOPE_ABSENT" in codes(rep)


def test_scope_referencing_unknown_case_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path, required_cases=["TC-999"])
    rep = build(tmp_path)
    assert "SCOPE_CASE_UNKNOWN" in codes(rep)


def test_scope_referencing_unknown_req_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path, required_reqs=["REQ-999"])
    rep = build(tmp_path)
    assert "SCOPE_REQ_UNKNOWN" in codes(rep)


def test_required_case_not_executed_is_blocking(tmp_path):
    data = write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"]), case("TC-2", ["REQ-2"])])
    write_collected(tmp_path, [
        {"node_id": "t::a", "cases": ["TC-1"]},
        {"node_id": "t::b", "cases": ["TC-2"]},
    ])
    # 只跑了 TC-1
    write_run(tmp_path, [{"node_id": "t::a", "attempt": 1, "status": "passed"}],
              baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1", "TC-2"])
    rep = build(tmp_path)
    blocked = [g.subject for g in rep.gaps if g.code == "REQUIRED_CASE_NOT_EXECUTED"]
    assert blocked == ["TC-2"]


# ---------------------------------------------------------------------------
# 有效执行范围（F1）
# ---------------------------------------------------------------------------


def test_effective_scope_expands_required_reqs(tmp_path):
    write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"]), case("TC-2", ["REQ-2"])])
    write_scope(tmp_path, required_reqs=["REQ-1"], required_cases=[])
    rep = build(tmp_path)
    assert rep.effective_required_cases == ["TC-1"]
    assert "SCOPE_EXPANDED_FROM_REQ" in codes(rep)


def test_effective_scope_is_union_without_duplicates(tmp_path):
    write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"]), case("TC-2", ["REQ-2"])])
    write_scope(tmp_path, required_reqs=["REQ-1"], required_cases=["TC-1", "TC-2"])
    rep = build(tmp_path)
    assert rep.effective_required_cases == ["TC-1", "TC-2"]
    assert "SCOPE_EXPANDED_FROM_REQ" not in codes(rep)


def test_required_req_with_no_covering_case_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path, required_reqs=["REQ-2"], required_cases=[])
    rep = build(tmp_path)
    assert "REQUIRED_REQ_WITHOUT_CASE" in codes(rep)
    assert rep.has_blocking


# ---------------------------------------------------------------------------
# 三态结果聚合（F5）
# ---------------------------------------------------------------------------


def test_skipped_status_yields_unknown_not_false(tmp_path):
    data = write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_run(tmp_path, [{"node_id": "t::a", "attempt": 1, "status": "skipped"}],
              baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)
    info = rep.per_case["TC-1"]
    assert info["last_result"] is None
    assert info["has_unknown_result"] is True
    assert info["has_failed_attempt"] is False


def test_error_status_yields_false(tmp_path):
    data = write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_run(tmp_path, [{"node_id": "t::a", "attempt": 1, "status": "error"}],
              baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)
    assert rep.per_case["TC-1"]["last_result"] is False


def test_failed_wins_over_skipped(tmp_path):
    data = write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [
        {"node_id": "t::a", "cases": ["TC-1"]},
        {"node_id": "t::b", "cases": ["TC-1"]},
    ])
    write_run(tmp_path, [
        {"node_id": "t::a", "attempt": 1, "status": "failed"},
        {"node_id": "t::b", "attempt": 1, "status": "skipped"},
    ], baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)
    assert rep.per_case["TC-1"]["last_result"] is False


def test_no_attempt_yields_unknown_not_false(tmp_path):
    data = write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_run(tmp_path, [], baseline_hash=data["meta"]["content_hash"])
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)
    info = rep.per_case["TC-1"]
    assert info["last_result"] is None
    assert info["executed"] is False
    assert info["at_statuses"] == {"t::a|": "no_attempt"}


# ---------------------------------------------------------------------------
# REQ 级缺陷传播与阻断契约（F3）
# ---------------------------------------------------------------------------


def test_req_level_defect_propagates_to_covering_cases(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    fm = {"id": "BUG-9", "tc": [], "req": ["REQ-1"], "severity": "S1",
          "confirmed_blocking": True, "root_cause": "PRODUCT",
          "stability": "稳定失败"}
    p = tmp_path / "qa" / "defects" / "BUG-9.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n",
                 encoding="utf-8")

    rep = build(tmp_path)
    info = rep.per_case["TC-1"]
    assert "BUG-9" in info["defects"]
    assert info["defects_via_req"] == ["BUG-9"]
    assert info["confirmed_blocking_defect"] is True


def test_closed_defect_is_recorded_but_not_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    write_defect(tmp_path, "BUG-1", ["TC-1"], confirmed_blocking=True)
    path = tmp_path / "qa" / "defects" / "BUG-1.md"
    text = path.read_text(encoding="utf-8").replace("---\n\n# defect",
                                                   "closed: true\n---\n\n# defect")
    path.write_text(text, encoding="utf-8")

    rep = build(tmp_path)
    info = rep.per_case["TC-1"]
    assert "BUG-1" in info["defects"]
    assert info["confirmed_blocking_defect"] is False


def test_unlinked_defect_reports_gap(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    fm = {"id": "BUG-X", "tc": [], "req": [], "severity": "S2",
          "confirmed_blocking": False, "root_cause": "UNKNOWN",
          "stability": "未确认"}
    p = tmp_path / "qa" / "defects" / "BUG-X.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n",
                 encoding="utf-8")
    rep = build(tmp_path)
    assert "DEFECT_UNLINKED" in codes(rep)


# ---------------------------------------------------------------------------
# 版本交叉核对（F2）
# ---------------------------------------------------------------------------


def test_run_missing_baseline_hash_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_collected(tmp_path, [{"node_id": "t::a", "cases": ["TC-1"]}])
    write_run(tmp_path, [{"node_id": "t::a", "attempt": 1, "status": "passed"}],
              baseline_hash=None)
    write_scope(tmp_path, required_cases=["TC-1"])
    rep = build(tmp_path)
    assert "RUN_BASELINE_HASH_ABSENT" in codes(rep)


def test_case_file_declaring_stale_baseline_version_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")], version="2.0.0")
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])], version="1.0.0")
    write_scope(tmp_path)
    rep = build(tmp_path)
    assert "CASE_BASELINE_VERSION_MISMATCH" in codes(rep)


def test_scope_declaring_stale_baseline_version_is_blocking(tmp_path):
    write_baseline(tmp_path, [req("REQ-1")], version="2.0.0")
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])], version="2.0.0")
    write_scope(tmp_path)          # write_scope 固定写 1.0.0
    rep = build(tmp_path)
    assert "SCOPE_BASELINE_VERSION_MISMATCH" in codes(rep)


# ---------------------------------------------------------------------------
# CSV 产出
# ---------------------------------------------------------------------------


def test_matrix_csv_is_written_and_derived(happy, tmp_path):
    rep = build(happy)
    out = happy / "qa" / "trace" / FEATURE / "matrix.csv"
    tm.write_matrix_csv(rep, out)
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("REQ,TC,AT,manual")
    assert "REQ-1,TC-1,tests/t.py::test_a" in text


def test_uncovered_req_appears_in_csv(tmp_path):
    write_baseline(tmp_path, [req("REQ-1"), req("REQ-2")])
    write_cases(tmp_path, [case("TC-1", ["REQ-1"])])
    write_scope(tmp_path)
    rep = build(tmp_path)
    out = tmp_path / "matrix.csv"
    tm.write_matrix_csv(rep, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("REQ-2,,") for line in lines), lines

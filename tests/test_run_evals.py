"""run_evals.py 测试。

重点约束两件事：
  1. 结构检查真的能抓到格式问题（正反例）
  2. **结构结果与人工结果不得合成总分**，且结构结果必须自带"不证明什么"的声明
     —— 把"格式合规"表述成"质量达标"是本项目明确禁止的
"""
import json
import pathlib
import subprocess
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evals"))

import run_evals as re_mod  # noqa: E402

SCRIPT = REPO / "evals" / "run_evals.py"


# ---------------------------------------------------------------------------
# 产出文件构造
# ---------------------------------------------------------------------------


def write_cases(tmp_path, cases, body):
    fm = {"feature": "demo", "baseline_version": "1.0.0", "cases": cases}
    p = tmp_path / "cases.md"
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
                 + "---\n\n" + body, encoding="utf-8")
    return p


def case(tid, covers, **over):
    d = {"id": tid, "covers": covers, "manual": False,
         "layer": "api", "priority": "P0", "method": "边界值"}
    d.update(over)
    return d


GOOD_BODY = """# 用例

| 编号 | 步骤 | 预期结果 |
|---|---|---|
| TC-1 | 用户名 min（3 字符） | HTTP 201，响应体 userId 非空 |
| TC-2 | 用户名 min-1（2 字符） | HTTP 400，error_code == USERNAME_LENGTH_INVALID |
| TC-3 | 用户名 max（32 字符） | HTTP 201，响应体 userId 非空 |
| TC-4 | 用户名 max+1（33 字符，超长） | HTTP 400，error_code == USERNAME_LENGTH_INVALID |
| TC-5 | 用户名传入数字（非字符串，非法类型） | HTTP 400，error_code == USERNAME_TYPE_INVALID |
"""


def good_cases(tmp_path):
    return write_cases(tmp_path, [
        case("TC-1", ["REQ-1"]), case("TC-2", ["REQ-1"]),
        case("TC-3", ["REQ-1"]), case("TC-4", ["REQ-1"]),
        case("TC-5", ["REQ-1"]),
    ], GOOD_BODY)


def run_struct(fixture_id, cases_path):
    fm, body, _ = re_mod.load_fixture(fixture_id)
    cfm, cbody, _ = re_mod.load_cases(cases_path)
    return {c.cid: c for c in re_mod.run_structural(fm, body, cfm, cbody)}


# ---------------------------------------------------------------------------
# fixture 与 rubric 自身
# ---------------------------------------------------------------------------


def test_all_fixtures_have_valid_frontmatter():
    rows = re_mod.list_fixtures()
    assert len(rows) >= 3
    for fid, kind, expect in rows:
        assert fid
        assert kind in ("positive", "negative", "mixed")
        assert isinstance(expect, dict)


def test_fixtures_include_a_negative_case():
    """反例比正例重要：它测的是"会不会知道自己做不了"。"""
    kinds = {kind for _fid, kind, _e in re_mod.list_fixtures()}
    assert "negative" in kinds
    assert "mixed" in kinds


def test_rubric_separates_structural_and_human():
    rubric, _ = re_mod.load_rubric()
    judges = {c["judge"] for c in rubric["criteria"]}
    assert judges == {"structural", "human"}
    assert any(c["judge"] == "human" and c["weight"] == "blocking"
               for c in rubric["criteria"]), "必须有阻断级的人工判断项"


def test_rubric_accounts_false_negative_and_positive_separately():
    rubric, _ = re_mod.load_rubric()
    acc = rubric["error_accounting"]
    assert "false_negative" in acc and "false_positive" in acc
    assert acc["false_negative"]["cost"] != acc["false_positive"]["cost"], \
        "漏报与误报代价必须区分，否则合成一个准确率会抹平差异"


# ---------------------------------------------------------------------------
# S1 covers
# ---------------------------------------------------------------------------


def test_s1_passes_on_valid_covers(tmp_path):
    res = run_struct("boundary-range", good_cases(tmp_path))
    assert res["S1"].passed, res["S1"].detail


def test_s1_fails_when_covers_missing(tmp_path):
    p = write_cases(tmp_path, [case("TC-1", [])], GOOD_BODY)
    res = run_struct("boundary-range", p)
    assert not res["S1"].passed
    assert "covers" in res["S1"].detail


def test_s1_fails_when_covers_points_to_unknown_req(tmp_path):
    p = write_cases(tmp_path, [case("TC-1", ["REQ-404"])], GOOD_BODY)
    res = run_struct("boundary-range", p)
    assert not res["S1"].passed
    assert "REQ-404" in res["S1"].detail


# ---------------------------------------------------------------------------
# S2 模糊词
# ---------------------------------------------------------------------------


def test_s2_passes_on_verifiable_expectations(tmp_path):
    res = run_struct("boundary-range", good_cases(tmp_path))
    assert res["S2"].passed, res["S2"].detail


@pytest.mark.parametrize("term", ["正常显示", "功能可用", "符合预期", "无异常"])
def test_s2_fails_on_vague_terms(tmp_path, term):
    body = GOOD_BODY + "\n| TC-6 | 提交 | {} |\n".format(term)
    p = write_cases(tmp_path, [case("TC-1", ["REQ-1"])], body)
    res = run_struct("boundary-range", p)
    assert not res["S2"].passed
    assert term in res["S2"].detail


# ---------------------------------------------------------------------------
# S3 边界
# ---------------------------------------------------------------------------


def test_s3_passes_when_all_boundaries_present(tmp_path):
    res = run_struct("boundary-range", good_cases(tmp_path))
    assert res["S3"].passed, res["S3"].detail


def test_s3_fails_when_a_boundary_is_missing(tmp_path):
    body = GOOD_BODY.replace("用户名 max+1（33 字符，超长）", "用户名 32 字符再加一个")
    p = write_cases(tmp_path, [case("TC-1", ["REQ-1"])], body)
    res = run_struct("boundary-range", p)
    assert not res["S3"].passed
    assert "max+1" in res["S3"].detail


# ---------------------------------------------------------------------------
# S4 字段
# ---------------------------------------------------------------------------


def test_s4_fails_when_manual_flag_missing(tmp_path):
    entry = case("TC-1", ["REQ-1"])
    del entry["manual"]
    p = write_cases(tmp_path, [entry], GOOD_BODY)
    res = run_struct("boundary-range", p)
    assert not res["S4"].passed
    assert "manual" in res["S4"].detail


# ---------------------------------------------------------------------------
# S5 阻塞项计数 —— 两个方向都要能抓
# ---------------------------------------------------------------------------


def test_s5_positive_fixture_rejects_unexpected_blocking(tmp_path):
    """正例需求被标阻塞 = 误报，必须抓到。"""
    body = GOOD_BODY + "\n## 阻塞项\n\n- REQ-1 描述不清晰\n"
    p = write_cases(tmp_path, [case("TC-1", ["REQ-1"])], body)
    res = run_struct("boundary-range", p)
    assert not res["S5"].passed
    assert "实际 1" in res["S5"].detail


def test_s5_negative_fixture_requires_blocking_items(tmp_path):
    """反例需求没被标阻塞 = 漏报，必须抓到。"""
    p = write_cases(tmp_path, [case("TC-1", ["REQ-1"])], "# 用例\n\n无\n")
    res = run_struct("unverifiable-criteria", p)
    assert not res["S5"].passed


def test_s5_negative_fixture_passes_with_enough_blocking(tmp_path):
    body = """# 阻塞项

- [阻塞] REQ-1 未给出可验证的"顺畅"判定依据
- [阻塞] REQ-2 未给出响应时间指标与统计口径
- [阻塞] REQ-3 未定义"常见异常注册行为"的判定规则
"""
    p = write_cases(tmp_path, [case("TC-1", ["REQ-1"])], body)
    res = run_struct("unverifiable-criteria", p)
    assert res["S5"].passed, res["S5"].detail


def test_s5_mixed_fixture_accepts_range(tmp_path):
    body = GOOD_BODY + """
## 阻塞项

- [阻塞] REQ-2 通知渠道未确定，无法给出可验证预期
"""
    p = write_cases(tmp_path, [case("TC-1", ["REQ-1"])], body)
    res = run_struct("mixed-partial", p)
    assert res["S5"].passed, res["S5"].detail


# ---------------------------------------------------------------------------
# 报告：两类结果不得合成
# ---------------------------------------------------------------------------


def test_report_keeps_structural_and_human_separate(tmp_path):
    fm, body, _ = re_mod.load_fixture("boundary-range")
    cfm, cbody, _ = re_mod.load_cases(good_cases(tmp_path))
    rubric, _ = re_mod.load_rubric()
    structural = re_mod.run_structural(fm, body, cfm, cbody)
    human = re_mod.build_human_checklist(rubric)
    rep = re_mod.build_report("boundary-range", fm, structural, human,
                              {"model": "m", "agent_config_version": "v"}, rubric)

    assert "structural" in rep and "human" in rep
    # 关键：没有任何合成的总分字段
    for forbidden in ("score", "total_score", "overall", "pass_rate"):
        assert forbidden not in rep, "不得出现合成总分字段 {}".format(forbidden)


def test_structural_section_declares_what_it_does_not_prove(tmp_path):
    fm, body, _ = re_mod.load_fixture("boundary-range")
    cfm, cbody, _ = re_mod.load_cases(good_cases(tmp_path))
    rubric, _ = re_mod.load_rubric()
    rep = re_mod.build_report(
        "boundary-range", fm, re_mod.run_structural(fm, body, cfm, cbody),
        re_mod.build_human_checklist(rubric), {}, rubric)

    s = rep["structural"]
    assert s["proves"] == "格式合规"
    assert "质量" in s["does_not_prove"]


def test_human_section_starts_pending_and_blocks_pass_claim(tmp_path):
    fm, _body, _ = re_mod.load_fixture("boundary-range")
    rubric, _ = re_mod.load_rubric()
    rep = re_mod.build_report("boundary-range", fm, [],
                              re_mod.build_human_checklist(rubric), {}, rubric)
    assert rep["human"]["status"] == "PENDING"
    assert all(i["verdict"] is None for i in rep["human"]["checklist"])
    assert "不得表述为通过" in rep["human"]["note"]


def test_missing_model_version_is_flagged_in_text(tmp_path):
    fm, body, _ = re_mod.load_fixture("boundary-range")
    cfm, cbody, _ = re_mod.load_cases(good_cases(tmp_path))
    rubric, _ = re_mod.load_rubric()
    rep = re_mod.build_report(
        "boundary-range", fm, re_mod.run_structural(fm, body, cfm, cbody),
        re_mod.build_human_checklist(rubric), {}, rubric)
    text = re_mod.render_text(rep)
    assert "不可与其他运行比较" in text


def test_text_report_states_no_combined_score(tmp_path):
    fm, body, _ = re_mod.load_fixture("boundary-range")
    cfm, cbody, _ = re_mod.load_cases(good_cases(tmp_path))
    rubric, _ = re_mod.load_rubric()
    rep = re_mod.build_report(
        "boundary-range", fm, re_mod.run_structural(fm, body, cfm, cbody),
        re_mod.build_human_checklist(rubric), {"model": "m",
                                               "agent_config_version": "v"}, rubric)
    text = re_mod.render_text(rep)
    assert "不合成总分" in text
    assert "**不**证明" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_list_works():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--list", "--json"],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    rows = json.loads(proc.stdout)
    assert any(r["kind"] == "negative" for r in rows)


def test_cli_exits_nonzero_on_blocking_structural_failure(tmp_path):
    p = write_cases(tmp_path, [case("TC-1", ["REQ-404"])], GOOD_BODY)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--fixture", "boundary-range",
         "--cases", str(p), "--json"],
        capture_output=True, text=True)
    assert proc.returncode == 1
    rep = json.loads(proc.stdout)
    assert "S1" in rep["structural"]["blocking_failed"]


def test_cli_exits_zero_on_clean_structural(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--fixture", "boundary-range",
         "--cases", str(good_cases(tmp_path)), "--json",
         "--model", "m", "--agent-config-version", "v"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout
    rep = json.loads(proc.stdout)
    assert rep["structural"]["blocking_failed"] == []
    # 结构全过时，人工部分仍是 PENDING —— 不构成"评测通过"
    assert rep["human"]["status"] == "PENDING"


def test_cli_without_cases_outputs_expectations_only():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--fixture", "unverifiable-criteria", "--json"],
        capture_output=True, text=True)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["fixture_kind"] == "negative"
    assert payload["human_checklist"]

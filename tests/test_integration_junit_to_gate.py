"""集成测试：JUnit 原生报告 → rebuild_run_json → trace_matrix → gate_check。

为什么必须有这一层
------------------
`rebuild_run_json` 的 32 项单测只证明"转换器自己的输出约定"，证明不了它与下游能对上。
外部复核用真实调用链发现：第一版输出 `attempts[].attempt_id` 与嵌套 `version_binding`，
而 `trace_matrix.load_runs` 读 `node_id`、`gate_check` 读顶层绑定字段，结果整条证据链
根本没接通（所有 attempt 被判 ATTEMPT_ENTRY_INVALID 跳过）。单测全绿、集成全断。

所以这里断言的对象是**门禁最终结论**，而不是中间数据结构。
"""

import json
import pathlib
import subprocess
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import gate_check as gc      # noqa: E402
import rebuild_run_json as rr  # noqa: E402
import trace_matrix as tm    # noqa: E402

TOOL = str(REPO / "scripts" / "rebuild_run_json.py")
FEATURE = "login"
CANDIDATE = "cafebabe"
TARGET = "1111111"
POLICY = "1.0.0"

# 被测项目里 collect 出来的真实 node_id（pytest 风格）
NODE_ID = "tests/test_login.py::test_ok"
# 对应的 JUnit 属性：pytest --junitxml 会写成 classname="tests.test_login"
JUNIT_CLASSNAME = "tests.test_login"
JUNIT_NAME = "test_ok"


def write_baseline(root, version="1.0.0"):
    data = {
        "meta": {
            "feature": FEATURE,
            "baseline_version": version,
            "confirmed_by": "zhang.san",
            "confirmed_at": "2026-09-21",
            "baseline_approval_ref": "https://example/mr/1",
        },
        "requirements": [{"id": "REQ-1", "statement": "登录成功",
                          "risk": "high", "blocking": True}],
    }
    data["meta"]["content_hash"] = tm.compute_baseline_hash(data)
    path = root / "qa" / "baseline" / FEATURE / "requirements.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path, data


def write_cases(root):
    fm = {"feature": FEATURE, "baseline_version": "1.0.0",
          "cases": [{"id": "TC-1", "covers": ["REQ-1"], "manual": False,
                     "layer": "api", "priority": "P0", "method": "等价类"}]}
    path = root / "qa" / "cases" / FEATURE / "core.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True,
                                             sort_keys=False) + "---\n\n# cases\n",
                    encoding="utf-8")


def write_collected(root, node_id=NODE_ID):
    path = root / "qa" / "trace" / FEATURE / "collected.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tests": [{"node_id": node_id,
                                           "cases": ["TC-1"]}]}),
                    encoding="utf-8")


def write_scope(root):
    data = {
        "meta": {"feature": FEATURE, "baseline_version": "1.0.0",
                 "confirmed_by": "zhang.san"},
        "required_requirements": [],
        "required_cases": ["TC-1"],
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


def write_junit(root, name="junit.xml", classname=JUNIT_CLASSNAME,
                case_name=JUNIT_NAME, inner="", extra_cases=""):
    raw = root / "qa" / "runs" / "R1" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / name
    path.write_text(
        '<?xml version="1.0"?>\n<testsuite name="pytest">\n'
        '<testcase classname="%s" name="%s" time="0.01">%s</testcase>\n'
        '%s</testsuite>\n' % (classname, case_name, inner, extra_cases),
        encoding="utf-8")
    return raw


def run_rebuild(root, raw_dir, baseline_file, strategy="pytest", extra=None):
    out = root / "qa" / "runs" / "R1" / "run.json"
    argv = [sys.executable, TOOL,
            "--raw", str(raw_dir),
            "--out", str(out),
            "--run-id", "R1",
            "--candidate-sha", CANDIDATE,
            "--target-sha", TARGET,
            "--policy-version", POLICY,
            "--baseline-file", str(baseline_file),
            "--node-id-strategy", strategy]
    argv.extend(extra or [])
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc, out


def binding(baseline_hash):
    return {
        "candidate_sha": CANDIDATE,
        "target_sha": TARGET,
        "policy_version": POLICY,
        "baseline_version": "1.0.0",
        "baseline_hash": baseline_hash,
        "baseline_approval_ref": "https://example/mr/1",
        "human_approval_ref": "https://example/mr/1/approval",
    }


def codes(rep):
    return [f.code for f in rep.findings]


def write_evidence_source(root):
    """证据来源声明：CI 组装判定树时必须写，缺它一律 INCOMPLETE。"""
    data = {
        "collect": {"kind": "trusted_ci", "ref": "ci-run/999"},
        "runs": {"kind": "trusted_ci", "ref": "ci-run/999"},
        "defects": {"kind": "tracker", "ref": "tracker-query/QA-1"},
    }
    path = root / "qa" / "evidence-source.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")

    # tracker 来源还必须带导出清单：来源标签证明不了"导出内容是否漏带"
    manifest = root / "qa" / "defects" / "export-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "query_ref": "tracker://project=QA",
        "exported_at": "2026-09-22T10:00:00+08:00",
        "complete": True,
        "records": [],
    }), encoding="utf-8")


@pytest.fixture()
def repo(tmp_path):
    baseline_path, data = write_baseline(tmp_path)
    write_cases(tmp_path)
    write_collected(tmp_path)
    write_scope(tmp_path)
    write_evidence_source(tmp_path)
    return tmp_path, baseline_path, data["meta"]["content_hash"]


# --------------------------------------------------------------------------
# 正例：整条链路必须真的接通
# --------------------------------------------------------------------------

def test_full_chain_passes(repo):
    root, baseline_path, bhash = repo
    raw = write_junit(root)
    proc, out = run_rebuild(root, raw, baseline_path)
    assert proc.returncode == 0, proc.stdout.decode()

    run = json.loads(out.read_text())
    # 顶层绑定字段（gate_check 读这一层）
    assert run["candidate_sha"] == CANDIDATE
    assert run["baseline_version"] == "1.0.0"
    assert run["baseline_hash"] == bhash, "baseline_hash 算法必须与消费者一致"
    assert run["interrupted"] is False
    # attempts 用 node_id（trace_matrix 读这一层）
    assert run["attempts"][0]["node_id"] == NODE_ID

    rep = gc.evaluate(root, FEATURE, binding(bhash))
    found = codes(rep)
    for must_not in ("ATTEMPT_ENTRY_INVALID", "RUN_CANDIDATE_SHA_ABSENT",
                     "RUN_BASELINE_HASH_ABSENT", "RUN_BASELINE_VERSION_ABSENT",
                     "REQUIRED_CASE_NOT_EXECUTED", "REQUIRED_CASE_RESULT_MISSING"):
        assert must_not not in found, found
    assert rep.verdict == gc.VERDICT_PASS, found
    assert rep.verified_scope["gate_accepted"] == {"TC-1": True}


def test_baseline_hash_matches_trace_matrix_algorithm(repo):
    """baseline_hash 必须等于 trace_matrix.compute_baseline_hash 的结果。

    第一版 CI 用 git tree SHA 当 baseline_version、用文件哈希清单的哈希当
    baseline_hash，与消费者是两套契约，有真实基线也对不上。
    """
    root, baseline_path, bhash = repo
    version, computed, err = rr.derive_baseline_binding(str(baseline_path))
    assert err is None
    assert version == "1.0.0"
    assert computed == bhash
    assert computed.startswith("sha256:")


# --------------------------------------------------------------------------
# 反例：证据不完整必须变成 INCOMPLETE，不能因为"已解析部分都通过"放行
# --------------------------------------------------------------------------

def test_one_good_one_corrupt_report_is_incomplete(repo):
    """一个通过报告 + 一个损坏报告 → 门禁必须 INCOMPLETE。"""
    root, baseline_path, bhash = repo
    raw = write_junit(root)
    (raw / "broken.xml").write_text("<testsuite><unclosed>", encoding="utf-8")

    proc, out = run_rebuild(root, raw, baseline_path)
    assert proc.returncode == 0
    run = json.loads(out.read_text())
    assert run["incomplete"] is True
    assert run["interrupted"] is True, "interrupted 才是下游消费的字段"

    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert "RUN_INTERRUPTED" in codes(rep)
    assert rep.verdict == gc.VERDICT_INCOMPLETE, codes(rep)


def test_all_passed_but_interrupted_still_not_pass(repo):
    """所有已解析结果都是 passed，但证据不完整 → 不得 PASS。"""
    root, baseline_path, bhash = repo
    raw = write_junit(root)
    (raw / "empty.xml").write_text("<testsuite></testsuite>", encoding="utf-8")
    proc, out = run_rebuild(root, raw, baseline_path)
    assert proc.returncode == 0
    run = json.loads(out.read_text())
    assert run["counts"].get("passed") == 1
    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert rep.verdict != gc.VERDICT_PASS, codes(rep)


def test_wrong_node_id_strategy_is_not_silently_passed(repo):
    """映射错了不能静默通过：raw 策略产出的 node_id 与 collect 清单对不上。"""
    root, baseline_path, bhash = repo
    raw = write_junit(root)
    proc, out = run_rebuild(root, raw, baseline_path, strategy="raw")
    assert proc.returncode == 0
    run = json.loads(out.read_text())
    assert run["attempts"][0]["node_id"] != NODE_ID

    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert rep.verdict != gc.VERDICT_PASS
    assert "REQUIRED_CASE_RESULT_MISSING" in codes(rep) or \
           "REQUIRED_CASE_NOT_EXECUTED" in codes(rep), codes(rep)


def test_failed_test_is_fail_not_incomplete(repo):
    """三态可区分：测出问题是 FAIL，不是 INCOMPLETE。"""
    root, baseline_path, bhash = repo
    raw = write_junit(root, inner='<failure message="assert 1 == 2"/>')
    proc, out = run_rebuild(root, raw, baseline_path)
    assert proc.returncode == 0
    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert rep.verdict == gc.VERDICT_FAIL, codes(rep)


def test_skipped_required_case_is_incomplete_not_fail(repo):
    """必测项被 skip 是证据不足，不是质量失败。"""
    root, baseline_path, bhash = repo
    raw = write_junit(root, inner='<skipped message="needs device"/>')
    proc, out = run_rebuild(root, raw, baseline_path)
    assert proc.returncode == 0
    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert rep.verdict == gc.VERDICT_INCOMPLETE, codes(rep)


def test_baseline_changed_after_run_is_stale(repo):
    """运行之后基线被改 → 证据失效（EVIDENCE_STALE）。"""
    root, baseline_path, bhash = repo
    raw = write_junit(root)
    proc, out = run_rebuild(root, raw, baseline_path)
    assert proc.returncode == 0

    data = yaml.safe_load(baseline_path.read_text())
    data["requirements"].append({"id": "REQ-2", "statement": "新增",
                                 "risk": "high", "blocking": True})
    data["meta"]["content_hash"] = tm.compute_baseline_hash(data)
    baseline_path.write_text(yaml.safe_dump(data, allow_unicode=True,
                                            sort_keys=False), encoding="utf-8")

    rep = gc.evaluate(root, FEATURE, binding(data["meta"]["content_hash"]))
    assert rep.verdict != gc.VERDICT_PASS
    assert "EVIDENCE_STALE" in codes(rep), codes(rep)


# --------------------------------------------------------------------------
# node_id 映射
# --------------------------------------------------------------------------

@pytest.mark.parametrize("classname,name,expect_node,expect_params", [
    ("tests.test_login", "test_ok", "tests/test_login.py::test_ok", None),
    ("tests.test_login", "test_p[1-2]", "tests/test_login.py::test_p", "1-2"),
    ("tests.api.test_x", "test_y", "tests/api/test_x.py::test_y", None),
    ("tests.test_login.TestC", "test_m",
     "tests/test_login.py::TestC::test_m", None),
])
def test_pytest_strategy_mapping(classname, name, expect_node, expect_params):
    node, params, note = rr.map_node_id(classname, name, "pytest")
    assert (node, params) == (expect_node, expect_params), note


def test_raw_strategy_keeps_classname_form():
    node, params, note = rr.map_node_id("a.b", "c[1]", "raw")
    assert node == "a.b::c" and params == "1"


def test_map_strategy_requires_explicit_entry():
    node, params, note = rr.map_node_id("a.b", "c", "map", {})
    assert node is None
    assert note.startswith("NODE_ID_UNMAPPED")


def test_map_strategy_uses_mapping():
    node, params, note = rr.map_node_id("a.b", "c", "map",
                                        {"a.b::c": "real/path.py::c"})
    assert node == "real/path.py::c" and note is None


def test_unmapped_entries_make_run_incomplete(repo):
    """映射不出 node_id 的记录不能悄悄丢：必须体现为证据不完整。"""
    root, baseline_path, bhash = repo
    raw = write_junit(root)
    proc, out = run_rebuild(root, raw, baseline_path, strategy="map")
    assert proc.returncode == 0
    run = json.loads(out.read_text())
    assert run["attempts"] == []
    assert run["interrupted"] is True
    assert any("NODE_ID_UNMAPPED" in r for r in run["incomplete_reasons"])


def test_duplicate_records_without_order_evidence_are_incomplete(repo):
    """同一 node_id 出现两次，默认不猜顺序：记为证据不完整。

    JUnit 没有尝试序号。重复可能是重试、分片重复或不同环境；而"先失败后通过不得自动
    抹掉阻断"恰恰依赖顺序。把计数器提到全局只解决"都变成 1"这个表象。
    """
    root, baseline_path, bhash = repo
    extra = ('<testcase classname="%s" name="%s" time="0.02"/>\n'
             % (JUNIT_CLASSNAME, JUNIT_NAME))
    raw = write_junit(root, inner='<failure message="first try"/>',
                      extra_cases=extra)
    proc, out = run_rebuild(root, raw, baseline_path)
    assert proc.returncode == 0
    run = json.loads(out.read_text())
    assert run["interrupted"] is True
    assert any("DUPLICATE_ATTEMPTS_WITHOUT_ORDER_EVIDENCE" in r
               for r in run["incomplete_reasons"])
    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert rep.verdict != gc.VERDICT_PASS


def test_retry_order_declared_assigns_sequence(repo):
    """显式声明 document-order 时才分配序号，并把该假设写进报告。"""
    root, baseline_path, bhash = repo
    extra = ('<testcase classname="%s" name="%s" time="0.02"/>\n'
             % (JUNIT_CLASSNAME, JUNIT_NAME))
    raw = write_junit(root, inner='<failure message="first try"/>',
                      extra_cases=extra)
    proc, out = run_rebuild(root, raw, baseline_path,
                            extra=["--retry-order", "document-order"])
    assert proc.returncode == 0
    run = json.loads(out.read_text())
    seq = [(a["attempt"], a["status"]) for a in run["attempts"]]
    assert seq == [(1, "failed"), (2, "passed")], seq
    assert run["interrupted"] is False
    assert "执行顺序" in run["retry_order_assumption"]


def test_cross_file_duplicates_are_grouped_not_reset(repo):
    """跨文件的同一 node_id 必须视为同一组，不能每个文件各自从 1 开始。"""
    root, baseline_path, bhash = repo
    raw = write_junit(root, name="first.xml",
                     inner='<failure message="first"/>')
    write_junit(root, name="second.xml")
    proc, out = run_rebuild(root, raw, baseline_path,
                            extra=["--retry-order", "document-order"])
    assert proc.returncode == 0
    run = json.loads(out.read_text())
    seq = sorted((a["attempt"], a["status"]) for a in run["attempts"])
    assert seq == [(1, "failed"), (2, "passed")], seq
    assert run["duplicate_keys"], "跨文件重复未被识别"


def test_map_strategy_parametrized_node_id_matches_collect(tmp_path):
    """map 策略下参数化映射必须拆出 base node_id，否则与 collect 对不上。

    第一版返回原 mapping 值，node_id 带着 [one] 后缀、同时又给 params=one，
    整链直接 REQUIRED_CASE_NOT_EXECUTED。
    """
    node, params, note = rr.map_node_id(
        "tests.test_login", "test_ok",
        "map", {"tests.test_login::test_ok": "tests/test_login.py::test_ok[one]"})
    assert node == "tests/test_login.py::test_ok"
    assert params == "one"


def test_map_strategy_parametrized_full_chain(repo):
    root, baseline_path, bhash = repo
    # collect 按无后缀 node_id + params 匹配
    path = root / "qa" / "trace" / FEATURE / "collected.json"
    path.write_text(json.dumps({"tests": [{"node_id": NODE_ID,
                                           "params": "one",
                                           "cases": ["TC-1"]}]}),
                    encoding="utf-8")
    raw = write_junit(root)
    mapping = {"%s::%s" % (JUNIT_CLASSNAME, JUNIT_NAME): "%s[one]" % NODE_ID}
    map_file = root / "node-id-map.json"
    map_file.write_text(json.dumps(mapping), encoding="utf-8")
    proc, out = run_rebuild(root, raw, baseline_path, strategy="map",
                            extra=["--node-id-map", str(map_file)])
    assert proc.returncode == 0, proc.stdout.decode()
    run = json.loads(out.read_text())
    assert run["attempts"][0]["node_id"] == NODE_ID
    assert run["attempts"][0]["params"] == "one"

    rep = gc.evaluate(root, FEATURE, binding(bhash))
    found = [f.code for f in rep.findings]
    assert "REQUIRED_CASE_NOT_EXECUTED" not in found, found
    assert "REQUIRED_CASE_RESULT_MISSING" not in found, found


# --------------------------------------------------------------------------
# 基线来源
# --------------------------------------------------------------------------

def test_baseline_file_and_explicit_values_are_mutually_exclusive(repo):
    root, baseline_path, bhash = repo
    raw = write_junit(root)
    proc, _ = run_rebuild(root, raw, baseline_path,
                          extra=["--baseline-version", "9.9.9"])
    assert proc.returncode == 64
    assert b"USAGE_ERROR" in proc.stdout


def test_baseline_file_missing_meta_version_is_data_error(tmp_path):
    bad = tmp_path / "requirements.yaml"
    bad.write_text("meta: {}\nrequirements: []\n", encoding="utf-8")
    version, bhash, err = rr.derive_baseline_binding(str(bad))
    assert version is None and "meta.baseline_version" in err


def test_baseline_file_not_found_is_data_error(tmp_path):
    version, bhash, err = rr.derive_baseline_binding(str(tmp_path / "nope.yaml"))
    assert version is None and "不存在" in err


# --------------------------------------------------------------------------
# 顺序未知必须传播到消费者：判定不得随输入排列改变（第五轮复核 P2）
# --------------------------------------------------------------------------

def _two_files_run(root, baseline_path, first_status, second_status, extra=None):
    """写两份报告：first.xml / second.xml，各一条同 node_id 的结果。"""
    raw = root / "qa" / "runs" / "R1" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name, status in (("first.xml", first_status),
                         ("second.xml", second_status)):
        inner = '' if status == "passed" else '<failure message="x"/>'
        (raw / name).write_text(
            '<testsuite><testcase classname="%s" name="%s" time="0.01">%s'
            '</testcase></testsuite>' % (JUNIT_CLASSNAME, JUNIT_NAME, inner),
            encoding="utf-8")
    return run_rebuild(root, raw, baseline_path, extra=extra)


def test_verdict_is_stable_under_input_permutation(repo):
    """同一组 failed/passed，仅交换输入排列，门禁结论必须一致。

    复核实测：failed,passed → last_result=true/INCOMPLETE；
              passed,failed → last_result=false/FAIL。
    判定随文件排列改变，说明"顺序未知"没有传播到消费者。
    """
    root, baseline_path, bhash = repo
    verdicts = set()
    last_results = set()
    for first, second in (("failed", "passed"), ("passed", "failed")):
        proc, out = _two_files_run(root, baseline_path, first, second)
        assert proc.returncode == 0, proc.stdout.decode()
        rep = gc.evaluate(root, FEATURE, binding(bhash))
        verdicts.add(rep.verdict)
        last_results.add(rep.verified_scope["last_result"]["TC-1"])
    assert len(verdicts) == 1, "门禁结论随输入排列改变: %s" % verdicts
    assert last_results == {None}, \
        "顺序未知时不得给出末次结果: %s" % last_results
    assert verdicts == {gc.VERDICT_INCOMPLETE}, verdicts


def test_order_unknown_surfaces_as_finding(repo):
    root, baseline_path, bhash = repo
    proc, out = _two_files_run(root, baseline_path, "failed", "passed")
    assert proc.returncode == 0
    run = json.loads(out.read_text())
    # 顺序未知 → 不给序号，而不是都写 1
    assert [a["attempt"] for a in run["attempts"]] == [None, None]
    assert all(a.get("order_known") is False for a in run["attempts"])

    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert "ATTEMPT_ORDER_UNKNOWN" in [f.code for f in rep.findings]


def test_declared_order_gives_stable_last_result(repo):
    """显式声明顺序后，末次结果才成立，且与声明的顺序一致。"""
    root, baseline_path, bhash = repo
    proc, out = _two_files_run(root, baseline_path, "failed", "passed",
                               extra=["--retry-order", "document-order"])
    assert proc.returncode == 0
    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert rep.verified_scope["last_result"]["TC-1"] is True
    assert "ATTEMPT_ORDER_UNKNOWN" not in [f.code for f in rep.findings]


def test_declared_order_reversed_gives_failure(repo):
    """声明顺序为 passed→failed 时末次是失败 —— 由声明决定，不由文件名决定。"""
    root, baseline_path, bhash = repo
    proc, out = _two_files_run(root, baseline_path, "passed", "failed",
                               extra=["--retry-order", "document-order"])
    assert proc.returncode == 0
    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert rep.verified_scope["last_result"]["TC-1"] is False


def test_out_of_order_attempt_numbers_are_sorted(repo):
    """序号乱序写入时按序号排序，不按出现顺序取末次。"""
    root, baseline_path, bhash = repo
    run_path = root / "qa" / "runs" / "R1" / "run.json"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps({
        "run_id": "R1",
        "candidate_sha": CANDIDATE,
        "target_sha": TARGET,
        "policy_version": POLICY,
        "baseline_version": "1.0.0",
        "baseline_hash": bhash,
        "interrupted": False,
        "config": {},
        "attempts": [
            {"node_id": NODE_ID, "attempt": 2, "status": "passed"},
            {"node_id": NODE_ID, "attempt": 1, "status": "failed"},
        ],
    }), encoding="utf-8")
    rep = gc.evaluate(root, FEATURE, binding(bhash))
    assert rep.verified_scope["last_result"]["TC-1"] is True
    assert "ATTEMPT_ORDER_UNKNOWN" not in [f.code for f in rep.findings]

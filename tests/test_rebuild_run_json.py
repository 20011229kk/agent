"""scripts/rebuild_run_json.py 的测试。

这份脚本在证据链上的位置决定了测试重点：它是**门禁输入的唯一合法来源**。
所以测的是"会不会把不该通过的东西变成看起来能通过的输入"：

- 版本绑定字段缺失必须拒绝生成，不能填空字符串（gate_check 侧实测过"字段缺失反而
  跳过核对"这类误放行）
- skipped 不得被记成 failed，也不得被记成 passed
- 原始报告缺失或解析失败必须体现为 incomplete，不能因为"已解析部分都 passed"就干净
- 敏感信息必须脱敏
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import rebuild_run_json as rr  # noqa: E402

TOOL = os.path.join(ROOT, "scripts", "rebuild_run_json.py")

BINDING = {
    "run_id": "2026-09-21-01",
    "candidate_sha": "c" * 40,
    "target_sha": "t" * 40,
    "policy_version": "p" * 40,
    "baseline_version": "v1",
    "baseline_hash": "b" * 64,
}

CLI_BINDING = [
    "--run-id", BINDING["run_id"],
    "--candidate-sha", BINDING["candidate_sha"],
    "--target-sha", BINDING["target_sha"],
    "--policy-version", BINDING["policy_version"],
    "--baseline-version", BINDING["baseline_version"],
    "--baseline-hash", BINDING["baseline_hash"],
]


def write_junit(path, cases, suite_attrs=""):
    body = []
    for case in cases:
        name = case["name"]
        cls = case.get("classname", "pkg.Mod")
        time = case.get("time", "0.01")
        inner = case.get("inner", "")
        body.append('<testcase classname="%s" name="%s" time="%s">%s</testcase>'
                    % (cls, name, time, inner))
    xml = '<?xml version="1.0"?>\n<testsuite %s>\n%s\n</testsuite>\n' % (
        suite_attrs, "\n".join(body))
    path.write_text(xml)
    return str(path)


# --------------------------------------------------------------------------
# 状态映射
# --------------------------------------------------------------------------

def test_passed_failed_error_skipped_mapping(tmp_path):
    p = write_junit(tmp_path / "junit.xml", [
        {"name": "ok"},
        {"name": "bad", "inner": '<failure message="assert 1 == 2"/>'},
        {"name": "boom", "inner": '<error message="ImportError"/>'},
        {"name": "later", "inner": '<skipped message="needs device"/>'},
    ])
    run = rr.build([p], BINDING)
    got = {a["node_id"].rsplit("::", 1)[1]: a["status"] for a in run["attempts"]}
    assert got == {"ok": "passed", "bad": "failed", "boom": "error",
                   "later": "skipped"}
    assert run["counts"] == {"passed": 1, "failed": 1, "error": 1, "skipped": 1}


def test_skipped_is_not_failed_and_not_passed(tmp_path):
    """把"没测"算成"测出问题"或"通过"都会让门禁给出错误分类。"""
    p = write_junit(tmp_path / "junit.xml",
                    [{"name": "later", "inner": '<skipped/>'}])
    run = rr.build([p], BINDING)
    assert run["counts"] == {"skipped": 1}
    assert "failed" not in run["counts"] and "passed" not in run["counts"]


def test_error_wins_over_skipped_when_both_present(tmp_path):
    """同时带 error 与 skipped 时按更坏的记，避免失败被读成跳过。"""
    p = write_junit(tmp_path / "junit.xml", [
        {"name": "weird", "inner": '<skipped/><error message="x"/>'}])
    run = rr.build([p], BINDING)
    assert run["attempts"][0]["status"] == "error"


def test_failure_wins_over_skipped(tmp_path):
    p = write_junit(tmp_path / "junit.xml", [
        {"name": "weird", "inner": '<skipped/><failure message="x"/>'}])
    run = rr.build([p], BINDING)
    assert run["attempts"][0]["status"] == "failed"


def test_node_id_mapped_from_classname_and_name(tmp_path):
    """下游读的字段是 node_id，且 pytest 策略会把 classname 转成文件路径。

    第一版输出 attempt_id="a.b.C::t1"，trace_matrix 全部判 ATTEMPT_ENTRY_INVALID 跳过。
    """
    p = write_junit(tmp_path / "junit.xml",
                    [{"name": "t1", "classname": "a.b.C"}])
    run = rr.build([p], BINDING)
    assert "attempt_id" not in run["attempts"][0]
    assert run["attempts"][0]["node_id"] == "a/b.py::C::t1"


def test_node_id_without_classname(tmp_path):
    (tmp_path / "j.xml").write_text(
        '<testsuite><testcase name="solo"/></testsuite>')
    run = rr.build([str(tmp_path / "j.xml")], BINDING)
    assert run["attempts"][0]["node_id"] == "solo"
    assert any("NO_CLASSNAME" in n for n in run["mapping_notes"])


def test_duration_parsed_and_bad_duration_tolerated(tmp_path):
    p = write_junit(tmp_path / "junit.xml", [
        {"name": "a", "time": "1.25"},
        {"name": "b", "time": "not-a-number"},
    ])
    run = rr.build([p], BINDING)
    durations = {a["node_id"].rsplit("::", 1)[1]: a["duration_s"]
                 for a in run["attempts"]}
    assert durations["a"] == 1.25
    assert durations["b"] is None


def test_nested_testsuites_are_collected(tmp_path):
    (tmp_path / "j.xml").write_text(
        '<testsuites><testsuite><testcase name="a"/></testsuite>'
        '<testsuite><testcase name="b"/></testsuite></testsuites>')
    run = rr.build([str(tmp_path / "j.xml")], BINDING)
    assert len(run["attempts"]) == 2


# --------------------------------------------------------------------------
# 证据完整性
# --------------------------------------------------------------------------

def test_unparsable_file_marks_incomplete_even_if_others_pass(tmp_path):
    good = write_junit(tmp_path / "good.xml", [{"name": "ok"}])
    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuite><unclosed>")
    run = rr.build([good, str(bad)], BINDING)
    assert run["counts"] == {"passed": 1}
    assert run["incomplete"] is True, "有文件解析不了却报告完整"
    assert any("bad.xml" in r for r in run["incomplete_reasons"])


def test_empty_report_is_incomplete(tmp_path):
    (tmp_path / "j.xml").write_text("<testsuite></testsuite>")
    run = rr.build([str(tmp_path / "j.xml")], BINDING)
    assert run["incomplete"] is True
    assert "NO_ATTEMPTS" in run["incomplete_reasons"]


def test_sources_record_hash_per_file(tmp_path):
    p = write_junit(tmp_path / "junit.xml", [{"name": "ok"}])
    run = rr.build([p], BINDING)
    src = run["sources"][0]
    assert len(src["sha256"]) == 64
    assert src["attempts"] == 1
    assert src["error"] is None


def test_version_binding_is_emitted_at_top_level(tmp_path):
    """绑定字段必须在**顶层**：gate_check.check_run_integrity 读的就是顶层。

    第一版放在嵌套的 version_binding 里，导致 RUN_CANDIDATE_SHA_ABSENT 等全部报缺。
    """
    p = write_junit(tmp_path / "junit.xml", [{"name": "ok"}])
    run = rr.build([p], BINDING)
    assert "version_binding" not in run
    for field in ("candidate_sha", "target_sha", "policy_version",
                  "baseline_version", "baseline_hash"):
        assert run[field] == BINDING[field], field


def test_interrupted_field_is_emitted_for_downstream(tmp_path):
    """下游用 interrupted 判"证据不完整不得通过"；只写自造的 incomplete 没有消费者。"""
    p = write_junit(tmp_path / "junit.xml", [{"name": "ok"}])
    clean = rr.build([p], BINDING)
    assert clean["interrupted"] is False

    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuite><unclosed>")
    dirty = rr.build([p, str(bad)], BINDING)
    assert dirty["interrupted"] is True
    assert dirty["incomplete"] is True


def test_config_field_present_and_empty_for_redaction_scan(tmp_path):
    """gate_check 会扫 config 找凭据痕迹，字段必须存在。"""
    p = write_junit(tmp_path / "junit.xml", [{"name": "ok"}])
    run = rr.build([p], BINDING)
    assert run["config"] == {}


def test_empty_but_valid_report_is_flagged(tmp_path):
    """合法但零 testcase 的报告（分片丢结果）必须记为证据缺口。

    只在"聚合后一条 attempt 都没有"时才报 NO_ATTEMPTS 会漏掉"一份好报告 + 一份空报告"。
    """
    good = write_junit(tmp_path / "good.xml", [{"name": "ok"}])
    (tmp_path / "empty.xml").write_text("<testsuite></testsuite>")
    run = rr.build([good, str(tmp_path / "empty.xml")], BINDING)
    assert run["counts"] == {"passed": 1}
    assert run["interrupted"] is True
    assert any("EMPTY_REPORT" in r for r in run["incomplete_reasons"])


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------

# 全部为合成 canary，不是真实凭据。断言的是**凭据值本身**不再出现，
# 而不是"某个前缀不见了"——上一版断言 'Bearer zzz' 整串不出现，结果正则只吃掉了
# Bearer，凭据值 zzz 原样留在输出里，测试照样通过。判据比被测对象弱的又一例。
CANARY = "CANARY7f3a9b2c4d"


@pytest.mark.parametrize("raw", [
    'password=%s' % CANARY,
    'passwd: %s' % CANARY,
    'token: %s' % CANARY,
    'api_key=%s' % CANARY,
    'secret=%s' % CANARY,
    'Authorization: Bearer %s' % CANARY,
    'authorization=%s' % CANARY,
    'Bearer %s' % CANARY,
    'Basic %s' % CANARY,
    'cookie: session=%s' % CANARY,
    'postgres://user:%s@db:5432/x' % CANARY,
])
def test_credential_value_itself_is_removed(tmp_path, raw):
    p = write_junit(tmp_path / "junit.xml", [
        {"name": "t", "inner": '<failure message="%s"/>' % raw}])
    run = rr.build([p], BINDING)
    msg = run["attempts"][0]["message"]
    assert CANARY not in msg, "凭据值仍在输出里: %r" % msg
    # 同时确认整份 run.json 里任何位置都没有残留
    assert CANARY not in json.dumps(run, ensure_ascii=False)


def test_redaction_marks_where_it_acted(tmp_path):
    p = write_junit(tmp_path / "junit.xml", [
        {"name": "t", "inner": '<failure message="token: %s"/>' % CANARY}])
    run = rr.build([p], BINDING)
    assert "redacted" in run["attempts"][0]["message"]


def test_authorization_scheme_alone_is_not_enough(tmp_path):
    """回归：只删掉 Bearer 而留下凭据值的写法必须被判失败。"""
    p = write_junit(tmp_path / "junit.xml", [
        {"name": "t",
         "inner": '<failure message="Authorization: Bearer %s"/>' % CANARY}])
    run = rr.build([p], BINDING)
    msg = run["attempts"][0]["message"]
    assert CANARY not in msg
    assert "Bearer %s" % CANARY not in msg


def test_long_message_truncated(tmp_path):
    long = "x" * 5000
    p = write_junit(tmp_path / "junit.xml", [
        {"name": "t", "inner": '<failure message="%s"/>' % long}])
    run = rr.build([p], BINDING)
    msg = run["attempts"][0]["message"]
    assert len(msg) < 2100
    assert msg.endswith("<truncated>")


def test_failure_text_body_used_when_no_message_attr(tmp_path):
    (tmp_path / "j.xml").write_text(
        '<testsuite><testcase name="t"><failure>boom detail</failure>'
        '</testcase></testsuite>')
    run = rr.build([str(tmp_path / "j.xml")], BINDING)
    assert run["attempts"][0]["message"] == "boom detail"


# --------------------------------------------------------------------------
# 文件收集与 CLI
# --------------------------------------------------------------------------

def test_collect_raw_recurses_directories(tmp_path):
    sub = tmp_path / "raw" / "nested"
    sub.mkdir(parents=True)
    write_junit(sub / "a.xml", [{"name": "a"}])
    write_junit(tmp_path / "raw" / "b.xml", [{"name": "b"}])
    files = rr.collect_raw_files([str(tmp_path / "raw")])
    assert len(files) == 2


def test_collect_raw_deduplicates(tmp_path):
    p = write_junit(tmp_path / "a.xml", [{"name": "a"}])
    files = rr.collect_raw_files([str(tmp_path), p])
    assert files.count(p) == 1


def test_cli_happy_path(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write_junit(raw / "junit.xml", [{"name": "ok"},
                                    {"name": "bad",
                                     "inner": '<failure message="m"/>'}])
    out = tmp_path / "run.json"
    r = subprocess.run([sys.executable, TOOL, "--raw", str(raw),
                        "--out", str(out)] + CLI_BINDING,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 0, r.stdout.decode()
    data = json.loads(out.read_text())
    assert data["counts"] == {"passed": 1, "failed": 1}
    assert data["incomplete"] is False


@pytest.mark.parametrize("drop", ["--run-id", "--candidate-sha", "--target-sha",
                                  "--policy-version", "--baseline-version",
                                  "--baseline-hash"])
def test_cli_refuses_missing_binding_field(tmp_path, drop):
    """每个版本绑定字段单独缺失都必须拒绝——不能只测"全给了"这一种情况。"""
    raw = tmp_path / "raw"
    raw.mkdir()
    write_junit(raw / "junit.xml", [{"name": "ok"}])
    out = tmp_path / "run.json"
    argv = [sys.executable, TOOL, "--raw", str(raw), "--out", str(out)]
    skip_next = False
    for item in CLI_BINDING:
        if skip_next:
            skip_next = False
            continue
        if item == drop:
            skip_next = True
            continue
        argv.append(item)
    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 64, r.stdout.decode()
    assert b"USAGE_ERROR" in r.stdout
    assert not out.exists(), "被拒绝时不得留下可用的 run.json"


def test_cli_refuses_empty_string_binding(tmp_path):
    """空字符串等于没给，必须同样拒绝。"""
    raw = tmp_path / "raw"
    raw.mkdir()
    write_junit(raw / "junit.xml", [{"name": "ok"}])
    out = tmp_path / "run.json"
    argv = [sys.executable, TOOL, "--raw", str(raw), "--out", str(out)]
    for item in CLI_BINDING:
        argv.append("" if item == BINDING["target_sha"] else item)
    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 64
    assert not out.exists()


def test_cli_missing_raw_dir_exit_65(tmp_path):
    out = tmp_path / "run.json"
    r = subprocess.run([sys.executable, TOOL, "--raw", str(tmp_path / "nope"),
                        "--out", str(out)] + CLI_BINDING,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 65
    assert b"DATA_ERROR" in r.stdout
    assert not out.exists()


def test_cli_requires_raw(tmp_path):
    out = tmp_path / "run.json"
    r = subprocess.run([sys.executable, TOOL, "--out", str(out)] + CLI_BINDING,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 64


def test_cli_reports_incomplete_on_unparsable(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "bad.xml").write_text("<testsuite><unclosed>")
    out = tmp_path / "run.json"
    r = subprocess.run([sys.executable, TOOL, "--raw", str(raw),
                        "--out", str(out)] + CLI_BINDING,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 0
    assert b"INCOMPLETE" in r.stdout
    data = json.loads(out.read_text())
    assert data["incomplete"] is True

"""guard_scan.py 测试 —— 每条规则都配正反例。

正例 = 该风险出现时必须命中；反例 = 正常变更不得命中（否则工具会被当噪音关掉）。

还验证两条定位约束：
  * 输出必须带"提示，非结论"声明
  * 转成门禁发现项时一律是 HINT，绝不产生 FAIL
"""
import json
import pathlib
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import guard_scan as gs  # noqa: E402


def diff(path, added=(), removed=(), deleted=False, new=False):
    """构造最小 unified diff。"""
    old_hdr = "/dev/null" if new else "a/{}".format(path)
    new_hdr = "/dev/null" if deleted else "b/{}".format(path)
    lines = [
        "diff --git a/{p} b/{p}".format(p=path),
        "--- {}".format(old_hdr),
        "+++ {}".format(new_hdr),
        "@@ -1,3 +1,3 @@",
    ]
    lines += ["-{}".format(l) for l in removed]
    lines += ["+{}".format(l) for l in added]
    return "\n".join(lines) + "\n"


def codes(result):
    return [h.code for h in result.hints]


# ---------------------------------------------------------------------------
# 定位约束
# ---------------------------------------------------------------------------


def test_disclaimer_always_present():
    r = gs.scan_diff(diff("tests/test_a.py", added=["    x = 1"]))
    assert "提示，非结论" in r.disclaimer
    assert "提示，非结论" in gs.render_text(r)


def test_disclaimer_states_what_it_cannot_do():
    """声明必须明确说出不能判断什么，不得含糊。"""
    assert "断言语义" in gs.DISCLAIMER
    assert "数据清理" in gs.DISCLAIMER


def test_gate_findings_are_always_hints():
    """提示器不产生判定：转成门禁发现项时只能是 HINT。"""
    r = gs.scan_diff(diff("tests/test_a.py", added=["@pytest.mark.skip"]))
    findings = gs.to_gate_findings(r)
    assert findings
    assert {f.kind for f in findings} == {"HINT"}
    assert all(f.code.startswith("GUARD_") for f in findings)
    assert all("提示，非结论" in f.detail for f in findings)


def test_scan_exits_zero_even_with_hints(tmp_path):
    """提示器不以非零码阻断 —— 阻断归 gate_check 与 reviewer。"""
    import subprocess
    p = tmp_path / "p.diff"
    p.write_text(diff("tests/test_a.py", added=["@pytest.mark.skip"]), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "guard_scan.py"),
         "--diff-file", str(p)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "SKIP_ADDED" in proc.stdout


# ---------------------------------------------------------------------------
# 规则 1：skip / xfail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "@pytest.mark.skip(reason='later')",
    "@pytest.mark.skipif(True, reason='x')",
    "@pytest.mark.xfail",
    "    pytest.skip('nope')",
    "@unittest.skip('x')",
    "@Ignore",
    "@Disabled",
    "    it.skip('should work', () => {})",
    "    xit('should work', () => {})",
    "    t.Skip()",
    "#[ignore]",
])
def test_skip_added_is_detected(line):
    r = gs.scan_diff(diff("tests/test_a.py", added=[line]))
    assert "SKIP_ADDED" in codes(r), line


def test_removing_a_skip_is_not_flagged():
    """删掉 skip 是好事，不该提示。"""
    r = gs.scan_diff(diff("tests/test_a.py", removed=["@pytest.mark.skip"],
                          added=["def test_a():"]))
    assert "SKIP_ADDED" not in codes(r)


def test_word_skipped_in_prose_is_not_flagged():
    """普通文字里出现 skip 不该命中。"""
    r = gs.scan_diff(diff("README.md", added=["We skipped the manual step."]))
    assert "SKIP_ADDED" not in codes(r)


# ---------------------------------------------------------------------------
# 规则 2：断言删除 —— 含"看似合规但删掉断言"
# ---------------------------------------------------------------------------


def test_assertion_removal_is_detected():
    r = gs.scan_diff(diff(
        "tests/test_a.py",
        removed=["    assert resp.status_code == 201",
                 "    assert resp.json()['userId']"],
        added=["    pass"],
    ))
    assert "ASSERTION_REMOVED" in codes(r)


def test_looks_compliant_but_assertions_deleted_is_detected():
    """核心反例：改动看起来是正常重构（函数还在、还叫 test_），
    但断言被悄悄删掉，只留一句无关调用。"""
    r = gs.scan_diff(diff(
        "tests/test_register.py",
        removed=[
            "def test_register_boundary():",
            "    resp = client.post('/api/register', json={'username': 'ab'})",
            "    assert resp.status_code == 400",
            "    assert resp.json()['error_code'] == 'USERNAME_LENGTH_INVALID'",
        ],
        added=[
            "def test_register_boundary():",
            "    resp = client.post('/api/register', json={'username': 'ab'})",
            "    logger.info(resp.status_code)",
        ],
    ))
    assert "ASSERTION_REMOVED" in codes(r)


def test_equivalent_assertion_rewrite_is_not_flagged():
    """等量替换（删 2 加 2）不提示 —— 否则正常重构会被淹没。"""
    r = gs.scan_diff(diff(
        "tests/test_a.py",
        removed=["    assert a == 1", "    assert b == 2"],
        added=["    assert a == 1, 'a'", "    assert b == 2, 'b'"],
    ))
    assert "ASSERTION_REMOVED" not in codes(r)


def test_adding_assertions_is_not_flagged():
    r = gs.scan_diff(diff("tests/test_a.py",
                          added=["    assert a == 1", "    assert b == 2"]))
    assert "ASSERTION_REMOVED" not in codes(r)


def test_assertion_count_only_documented_as_line_count():
    """工具只数行数这件事必须写在 detail 里，避免被当成语义判断。"""
    r = gs.scan_diff(diff("tests/test_a.py",
                          removed=["    assert x == 1"], added=["    pass"]))
    hint = [h for h in r.hints if h.code == "ASSERTION_REMOVED"][0]
    assert "只数行数" in hint.detail


# ---------------------------------------------------------------------------
# 规则 3：阈值变化
# ---------------------------------------------------------------------------


def test_coverage_threshold_lowered_is_detected():
    r = gs.scan_diff(diff("setup.cfg",
                          removed=["fail_under = 90"], added=["fail_under = 60"]))
    hints = [h for h in r.hints if h.code == "THRESHOLD_CHANGED"]
    assert hints
    assert "放宽" in hints[0].detail


def test_coverage_threshold_raised_is_reported_as_tightened():
    r = gs.scan_diff(diff("setup.cfg",
                          removed=["fail_under = 60"], added=["fail_under = 90"]))
    hints = [h for h in r.hints if h.code == "THRESHOLD_CHANGED"]
    assert hints
    assert "收紧" in hints[0].detail


def test_retry_count_raised_is_detected_as_looser():
    r = gs.scan_diff(diff("pytest.ini",
                          removed=["reruns = 0"], added=["reruns = 3"]))
    hints = [h for h in r.hints if h.code == "THRESHOLD_CHANGED"]
    assert hints
    assert "放宽" in hints[0].detail


def test_unrelated_number_change_is_not_flagged():
    """普通数字变化（非阈值键）不提示。"""
    r = gs.scan_diff(diff("src/app.py",
                          removed=["page_size = 10"], added=["page_size = 20"]))
    assert "THRESHOLD_CHANGED" not in codes(r)


def test_new_retry_config_is_flagged():
    r = gs.scan_diff(diff("pytest.ini", added=["reruns = 2"]))
    assert "RETRY_POLICY_CHANGED" in codes(r)


# ---------------------------------------------------------------------------
# 规则 4：忽略范围扩大
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "x = undefined  # noqa",
    "y = z  # type: ignore",
    "// eslint-disable-next-line",
    "// @ts-ignore",
    "foo()  # pylint: disable=no-member",
    "@SuppressWarnings(\"unchecked\")",
    "bar() // nolint",
    "def helper():  # pragma: no cover",
])
def test_ignore_marker_added_is_detected(line):
    r = gs.scan_diff(diff("src/app.py", added=[line]))
    assert "IGNORE_WIDENED" in codes(r), line


def test_gitignore_new_rule_is_detected():
    r = gs.scan_diff(diff(".gitignore", added=["tests/flaky/", "*.failed"]))
    assert "IGNORE_FILE_WIDENED" in codes(r)


def test_gitignore_comment_only_is_not_flagged():
    r = gs.scan_diff(diff(".gitignore", added=["# 说明：构建产物"]))
    assert "IGNORE_FILE_WIDENED" not in codes(r)


def test_normal_source_change_is_not_flagged():
    """反例：普通业务代码改动，一条提示都不该有。"""
    r = gs.scan_diff(diff(
        "src/service.py",
        removed=["    return self._legacy_lookup(uid)"],
        added=["    return self._repo.find_by_id(uid)"],
    ))
    assert r.hints == [], codes(r)


# ---------------------------------------------------------------------------
# 规则 5：重试策略
# ---------------------------------------------------------------------------


def test_retry_policy_field_change_is_detected():
    r = gs.scan_diff(diff(
        "qa/plan/login/required-scope.yaml",
        removed=["  accept_last_pass_without_explanation: false"],
        added=["  accept_last_pass_without_explanation: true"],
    ))
    assert "RETRY_POLICY_CHANGED" in codes(r)


def test_blocking_defect_clear_flag_change_is_detected():
    r = gs.scan_diff(diff(
        "qa/plan/login/required-scope.yaml",
        added=["  allow_retry_to_clear_confirmed_blocking_defect: true"],
    ))
    assert "RETRY_POLICY_CHANGED" in codes(r)


# ---------------------------------------------------------------------------
# 规则 6：测试文件删除
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "tests/test_login.py",
    "src/__tests__/login.spec.ts",
    "pkg/login_test.go",
    "spec/login_spec.rb",
    "src/test/java/LoginTest.java",
])
def test_test_file_deletion_is_detected(path):
    r = gs.scan_diff(diff(path, removed=["def test_a(): pass"], deleted=True))
    assert "TEST_FILE_DELETED" in codes(r), path


def test_deleted_file_is_attributed_to_correct_path_in_multi_file_diff():
    """回归：多文件 diff 中，删除文件的提示必须归属到被删的那个文件。

    由端到端 demo 暴露 —— 单文件 diff 的测试掩盖了这个缺陷：删除时 +++ 是 /dev/null，
    路径必须取自 --- 行，而之前的实现用了一个跨文件共享的游标去回溯，导致错归属。
    """
    d = (diff("tests/test_register.py",
              removed=["    assert resp.status_code == 400"],
              added=["    logger.info(resp.status_code)"])
         + diff("tests/test_login_dup.py",
                removed=["def test_login_duplicate():", "    assert login().ok"],
                deleted=True)
         + diff("src/service.py",
                removed=["    return old()"], added=["    return new()"]))

    r = gs.scan_diff(d)
    deleted = [h for h in r.hints if h.code == "TEST_FILE_DELETED"]

    assert len(deleted) == 1, [h.file for h in deleted]
    assert deleted[0].file == "tests/test_login_dup.py", deleted[0].file
    # 断言删除的提示必须留在 test_register.py 上，不得跑到别的文件
    removed_hints = [h for h in r.hints if h.code == "ASSERTION_REMOVED"]
    assert [h.file for h in removed_hints] == ["tests/test_register.py"]


def test_deleted_file_paths_are_independent_across_multiple_deletions():
    d = (diff("tests/test_a.py", removed=["def test_a(): assert 1"], deleted=True)
         + diff("tests/test_b.py", removed=["def test_b(): assert 2"], deleted=True))
    r = gs.scan_diff(d)
    deleted = sorted(h.file for h in r.hints if h.code == "TEST_FILE_DELETED")
    assert deleted == ["tests/test_a.py", "tests/test_b.py"]


def test_non_test_file_deletion_is_not_flagged():
    r = gs.scan_diff(diff("src/legacy.py", removed=["def old(): pass"], deleted=True))
    assert "TEST_FILE_DELETED" not in codes(r)


def test_new_test_file_is_not_flagged():
    r = gs.scan_diff(diff("tests/test_new.py", added=["def test_a(): assert 1"],
                          new=True))
    assert "TEST_FILE_DELETED" not in codes(r)


# ---------------------------------------------------------------------------
# 规则 7：受保护路径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "qa/baseline/login/requirements.yaml",
    "qa/plan/login/required-scope.yaml",
    "scripts/gate_check.py",
    "CODEOWNERS",
    ".github/workflows/ci.yml",
    "docs/capability-matrix.md",
])
def test_protected_path_touch_is_detected(path):
    r = gs.scan_diff(diff(path, added=["x"]))
    assert "PROTECTED_PATH_TOUCHED" in codes(r), path


def test_protected_path_hint_mentions_target_sha_rule():
    r = gs.scan_diff(diff("scripts/gate_check.py", added=["x"]))
    hint = [h for h in r.hints if h.code == "PROTECTED_PATH_TOUCHED"][0]
    assert "target_sha" in hint.detail


def test_ordinary_path_is_not_flagged_as_protected():
    r = gs.scan_diff(diff("qa/cases/login/core.md", added=["| TC-5 |"]))
    assert "PROTECTED_PATH_TOUCHED" not in codes(r)


# ---------------------------------------------------------------------------
# 豁免机制
# ---------------------------------------------------------------------------


def test_waiver_with_reason_and_alternative_coverage_closes_hint(tmp_path):
    path = tmp_path / "qa" / "plan" / "guard-waivers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"waivers": [{
        "code": "SKIP_ADDED",
        "file": "tests/test_a.py",
        "reason": "上游接口已下线",
        "alternative_coverage": "TC-20 手工用例",
    }]}, allow_unicode=True), encoding="utf-8")

    r = gs.scan_diff(diff("tests/test_a.py", added=["@pytest.mark.skip"]),
                     root=tmp_path, with_waivers=True)
    assert len(r.hints) == 1
    assert r.hints[0].waived is True
    assert r.open_hints == []


def test_waiver_without_alternative_coverage_does_not_close_hint(tmp_path):
    """只写原因、不给替代覆盖 —— 豁免不生效。"""
    path = tmp_path / "qa" / "plan" / "guard-waivers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"waivers": [{
        "code": "SKIP_ADDED",
        "file": "tests/test_a.py",
        "reason": "先跳过，回头再说",
    }]}, allow_unicode=True), encoding="utf-8")

    r = gs.scan_diff(diff("tests/test_a.py", added=["@pytest.mark.skip"]),
                     root=tmp_path, with_waivers=True)
    assert r.hints[0].waived is False
    assert len(r.open_hints) == 1


def test_waiver_for_other_file_does_not_apply(tmp_path):
    path = tmp_path / "qa" / "plan" / "guard-waivers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"waivers": [{
        "code": "SKIP_ADDED",
        "file": "tests/test_other.py",
        "reason": "x",
        "alternative_coverage": "TC-1",
    }]}, allow_unicode=True), encoding="utf-8")

    r = gs.scan_diff(diff("tests/test_a.py", added=["@pytest.mark.skip"]),
                     root=tmp_path, with_waivers=True)
    assert r.hints[0].waived is False


def test_waived_hints_excluded_from_gate_findings(tmp_path):
    path = tmp_path / "qa" / "plan" / "guard-waivers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"waivers": [{
        "code": "SKIP_ADDED",
        "reason": "x",
        "alternative_coverage": "TC-1",
    }]}, allow_unicode=True), encoding="utf-8")

    r = gs.scan_diff(diff("tests/test_a.py", added=["@pytest.mark.skip"]),
                     root=tmp_path, with_waivers=True)
    assert gs.to_gate_findings(r) == []


# ---------------------------------------------------------------------------
# 序列化边界（Task 6a 变异检验暴露过的盲区）
# ---------------------------------------------------------------------------


def test_serialized_result_contains_every_hint():
    d = (diff("tests/test_a.py", added=["@pytest.mark.skip"])
         + diff("setup.cfg", removed=["fail_under = 90"], added=["fail_under = 10"])
         + diff("tests/test_b.py", removed=["    assert x == 1"], added=["    pass"]))
    r = gs.scan_diff(d)
    payload = json.loads(json.dumps(r.as_dict(), ensure_ascii=False))
    assert len(payload["hints"]) == len(r.hints) >= 3
    assert payload["open_hints"] == len(r.open_hints)
    assert "提示，非结论" in payload["disclaimer"]


def test_rendered_text_contains_every_hint_code():
    d = (diff("tests/test_a.py", added=["@pytest.mark.skip"])
         + diff("tests/test_b.py", deleted=True, removed=["def test_x(): pass"]))
    r = gs.scan_diff(d)
    text = gs.render_text(r)
    for h in r.hints:
        assert h.code in text


# ---------------------------------------------------------------------------
# 误报率：正常变更集上应当很安静
# ---------------------------------------------------------------------------


def test_false_positive_rate_on_normal_changes():
    """一组典型的正常变更，不应产生提示。宁可误报，但不能对普通改动狂叫。"""
    normal = [
        diff("src/service.py", removed=["    x = old()"], added=["    x = new()"]),
        diff("README.md", added=["## 安装", "先装 python3"]),
        diff("src/models.py", added=["class User:", "    name: str"]),
        diff("tests/test_new.py", new=True,
             added=["def test_x():", "    assert compute() == 3"]),
        diff("qa/cases/login/core.md", added=["| TC-7 | 注册 | 无 | ... | ... | P1 |"]),
    ]
    total_hints = 0
    for d in normal:
        total_hints += len(gs.scan_diff(d).hints)
    assert total_hints == 0, "正常变更产生了 {} 条误报".format(total_hints)


def test_mixed_diff_flags_only_the_risky_file():
    d = (diff("src/service.py", removed=["    x = old()"], added=["    x = new()"])
         + diff("tests/test_a.py", added=["@pytest.mark.xfail"]))
    r = gs.scan_diff(d)
    assert codes(r) == ["SKIP_ADDED"]
    assert r.hints[0].file == "tests/test_a.py"
    assert r.files_scanned == 2

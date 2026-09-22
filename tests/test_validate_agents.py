"""scripts/validate_agents.py 的测试。

原则：每一条检查都要有**会触发它的反例**。首轮跑真实配置直接 PASS 是危险信号——
校验器可能什么都没查。下面对每个拒绝码单独构造反例，并额外验证真实配置四份全过。
"""

import copy
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import validate_agents as va  # noqa: E402

TOOL = os.path.join(ROOT, "scripts", "validate_agents.py")


def base_writer():
    """一份本身合规的可写角色配置。"""
    return {
        "name": "qa-design",
        "description": "d",
        "prompt": "p",
        "tools": ["read", "write"],
        "permissions": {
            "rules": [
                {"capability": "fs_write", "match": ["qa/cases/**"],
                 "effect": "allow"},
                {"capability": "fs_write",
                 "match": [".kiro/**", "qa/baseline/**", "qa/plan/**",
                           "scripts/**", "isolation/**", "tests/**",
                           ".github/**"],
                 "effect": "deny"},
            ]
        },
    }


def base_reader():
    return {
        "name": "qa-reviewer",
        "description": "d",
        "prompt": "p",
        "tools": ["read"],
        "permissions": {"rules": []},
    }


def codes(findings):
    return [f.code for f in findings]


def check(cfg, filename=None):
    filename = filename or ("%s.json" % cfg.get("name", "x"))
    return va.check_config(os.path.join("/fake", filename), cfg)


# --------------------------------------------------------------------------
# 基线：构造的合规配置本身必须过，否则下面的反例说明不了问题
# --------------------------------------------------------------------------

def test_baseline_writer_is_clean():
    assert check(base_writer()) == []


def test_baseline_reader_is_clean():
    assert check(base_reader()) == []


# --------------------------------------------------------------------------
# 逐条反例
# --------------------------------------------------------------------------

def test_name_missing():
    cfg = base_writer()
    del cfg["name"]
    assert "NAME_MISSING" in codes(check(cfg, "qa-design.json"))


def test_name_mismatch_with_filename():
    cfg = base_writer()
    cfg["name"] = "other"
    assert "NAME_MISMATCH" in codes(check(cfg, "qa-design.json"))


@pytest.mark.parametrize("field", ["description", "prompt"])
def test_required_text_fields(field):
    cfg = base_writer()
    del cfg[field]
    assert "FIELD_MISSING" in codes(check(cfg))


def test_tools_missing():
    cfg = base_writer()
    cfg["tools"] = []
    assert "TOOLS_MISSING" in codes(check(cfg))


def test_tool_not_in_registry_knowledge():
    """实测：knowledge 被接受但不匹配任何工具（静默无效）。"""
    cfg = base_writer()
    cfg["tools"] = ["read", "write", "knowledge"]
    assert "TOOL_NOT_IN_REGISTRY" in codes(check(cfg))


@pytest.mark.parametrize("tool", ["spec", "context"])
def test_tool_unverified_rejected(tool):
    """spec/context 的匹配结果未能验证，不得进正式配置。"""
    cfg = base_writer()
    cfg["tools"] = ["read", "write", tool]
    assert "TOOL_UNVERIFIED" in codes(check(cfg))


def test_tool_unknown():
    cfg = base_writer()
    cfg["tools"] = ["read", "write", "telepathy"]
    assert "TOOL_UNKNOWN" in codes(check(cfg))


def test_permissions_block_missing():
    """含 CLI-only 字段且缺 permissions 块的配置被 IDE 静默跳过。"""
    cfg = base_writer()
    del cfg["permissions"]
    assert "PERMISSIONS_BLOCK_MISSING" in codes(check(cfg))


def test_permissions_rules_not_list():
    cfg = base_writer()
    cfg["permissions"] = {"rules": "nope"}
    assert "PERMISSIONS_RULES_NOT_LIST" in codes(check(cfg))


def test_rule_not_object():
    cfg = base_writer()
    cfg["permissions"]["rules"].append("oops")
    assert "RULE_NOT_OBJECT" in codes(check(cfg))


def test_rule_capability_unknown():
    cfg = base_writer()
    cfg["permissions"]["rules"].append(
        {"capability": "telepathy", "match": ["x"], "effect": "allow"})
    assert "RULE_CAPABILITY_UNKNOWN" in codes(check(cfg))


def test_rule_effect_unknown():
    cfg = base_writer()
    cfg["permissions"]["rules"].append(
        {"capability": "fs_write", "match": ["x"], "effect": "maybe"})
    assert "RULE_EFFECT_UNKNOWN" in codes(check(cfg))


def test_rule_match_empty():
    cfg = base_writer()
    cfg["permissions"]["rules"].append(
        {"capability": "fs_write", "match": [], "effect": "allow"})
    assert "RULE_MATCH_EMPTY" in codes(check(cfg))


def test_rule_effect_ask_rejected():
    """ask 在 headless/子代理下等同 deny —— 会变成交互能跑、自动化静默失败。"""
    cfg = base_writer()
    cfg["permissions"]["rules"].append(
        {"capability": "fs_write", "match": ["qa/cases/extra/**"],
         "effect": "ask"})
    assert "RULE_EFFECT_ASK" in codes(check(cfg))


def test_readonly_role_with_write_tool():
    cfg = base_reader()
    cfg["tools"] = ["read", "write"]
    found = codes(check(cfg))
    assert "READONLY_ROLE_HAS_WRITE_TOOL" in found


def test_readonly_role_with_shell_tool():
    cfg = base_reader()
    cfg["tools"] = ["read", "shell"]
    assert "READONLY_ROLE_HAS_WRITE_TOOL" in codes(check(cfg))


def test_readonly_role_using_deny_is_rejected():
    """只读必须用 tools 省略表达；deny 会按交集传播给子代理。"""
    cfg = base_reader()
    cfg["permissions"]["rules"].append(
        {"capability": "fs_write", "match": ["**"], "effect": "deny"})
    assert "READONLY_ROLE_USES_DENY" in codes(check(cfg))


def test_qa_lead_deny_is_rejected_too():
    cfg = {
        "name": "qa-lead",
        "description": "d",
        "prompt": "p",
        "tools": ["read", "subagent", "todo_list"],
        "permissions": {"rules": [
            {"capability": "fs_write", "match": ["**"], "effect": "deny"}]},
    }
    assert "READONLY_ROLE_USES_DENY" in codes(check(cfg))


def test_write_paths_must_be_explicit_allow():
    cfg = base_writer()
    cfg["permissions"]["rules"] = [
        r for r in cfg["permissions"]["rules"] if r["effect"] != "allow"]
    assert "WRITE_PATHS_NOT_EXPLICIT" in codes(check(cfg))


@pytest.mark.parametrize("missing", ["qa/baseline/", "qa/plan/", "scripts/",
                                     "isolation/", "tests/", ".github/",
                                     ".kiro/"])
def test_each_protected_prefix_must_be_denied(missing):
    cfg = base_writer()
    deny = cfg["permissions"]["rules"][1]
    deny["match"] = [m for m in deny["match"] if not m.startswith(missing)]
    found = codes(check(cfg))
    assert "PROTECTED_PATH_NOT_DENIED" in found, missing


def test_allow_inside_protected_path_rejected():
    cfg = base_writer()
    cfg["permissions"]["rules"][0]["match"] = ["scripts/**"]
    found = codes(check(cfg))
    assert "ALLOW_INSIDE_PROTECTED" in found


def test_role_without_write_tool():
    cfg = base_writer()
    cfg["tools"] = ["read"]
    assert "ROLE_WITHOUT_WRITE_TOOL" in codes(check(cfg))


def test_allowed_tools_must_be_subset():
    cfg = base_writer()
    cfg["allowedTools"] = ["read", "shell"]
    assert "ALLOWED_TOOLS_NOT_SUBSET" in codes(check(cfg))


def test_allowed_tools_not_list():
    cfg = base_writer()
    cfg["allowedTools"] = "read"
    assert "ALLOWED_TOOLS_NOT_LIST" in codes(check(cfg))


def test_subagent_settings_without_tool():
    cfg = base_writer()
    cfg["toolsSettings"] = {"subagent": {"availableAgents": []}}
    assert "SUBAGENT_SETTINGS_WITHOUT_TOOL" in codes(check(cfg))


def test_subagent_target_missing(tmp_path):
    cfg = {
        "name": "qa-lead",
        "description": "d",
        "prompt": "p",
        "tools": ["read", "subagent", "todo_list"],
        "toolsSettings": {"subagent": {"availableAgents": ["ghost"]}},
        "permissions": {"rules": []},
    }
    path = tmp_path / "qa-lead.json"
    path.write_text(json.dumps(cfg))
    findings = va.check_config(str(path), cfg)
    assert "SUBAGENT_TARGET_MISSING" in codes(findings)


def test_subagent_target_present(tmp_path):
    (tmp_path / "qa-design.json").write_text("{}")
    cfg = {
        "name": "qa-lead",
        "description": "d",
        "prompt": "p",
        "tools": ["read", "subagent", "todo_list"],
        "toolsSettings": {"subagent": {"availableAgents": ["qa-design"]}},
        "permissions": {"rules": []},
    }
    path = tmp_path / "qa-lead.json"
    path.write_text(json.dumps(cfg))
    assert "SUBAGENT_TARGET_MISSING" not in codes(va.check_config(str(path), cfg))


# --------------------------------------------------------------------------
# 目录级与 CLI
# --------------------------------------------------------------------------

def test_invalid_json_reported(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    paths, findings = va.validate_dir(str(tmp_path))
    assert codes(findings) == ["JSON_INVALID"]


def test_non_object_json_reported(tmp_path):
    (tmp_path / "list.json").write_text("[1,2]")
    paths, findings = va.validate_dir(str(tmp_path))
    assert codes(findings) == ["JSON_NOT_OBJECT"]


def test_cli_missing_dir_exit_2(tmp_path):
    r = subprocess.run([sys.executable, TOOL, "--agents-dir",
                        str(tmp_path / "nope")],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 2
    assert b"AGENTS_DIR_MISSING" in r.stdout


def test_cli_empty_dir_exit_2(tmp_path):
    r = subprocess.run([sys.executable, TOOL, "--agents-dir", str(tmp_path)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 2
    assert b"NO_CONFIGS" in r.stdout


def test_cli_violation_exit_1(tmp_path):
    bad = base_writer()
    bad["tools"] = ["read", "write", "knowledge"]
    (tmp_path / "qa-design.json").write_text(json.dumps(bad))
    r = subprocess.run([sys.executable, TOOL, "--agents-dir", str(tmp_path)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 1
    assert b"TOOL_NOT_IN_REGISTRY" in r.stdout


# --------------------------------------------------------------------------
# 真实配置
# --------------------------------------------------------------------------

REAL_DIR = os.path.join(ROOT, ".kiro", "agents")


def test_real_configs_exist():
    assert os.path.isdir(REAL_DIR), "正式角色配置目录缺失"
    names = sorted(os.path.basename(p) for p in
                   __import__("glob").glob(os.path.join(REAL_DIR, "*.json")))
    assert names == ["qa-design.json", "qa-executor.json", "qa-lead.json",
                     "qa-reviewer.json"], names


def test_real_configs_pass():
    paths, findings = va.validate_dir(REAL_DIR)
    assert findings == [], [str(f) for f in findings]


def test_real_qa_lead_expresses_no_write_by_omission():
    with open(os.path.join(REAL_DIR, "qa-lead.json")) as fh:
        cfg = json.load(fh)
    assert "write" not in cfg["tools"] and "shell" not in cfg["tools"]
    assert cfg["permissions"]["rules"] == [], "qa-lead 不得用 deny 表达不写"


def test_real_qa_executor_denies_every_protected_prefix():
    with open(os.path.join(REAL_DIR, "qa-executor.json")) as fh:
        cfg = json.load(fh)
    denied = [m for r in cfg["permissions"]["rules"]
              if r["capability"] == "fs_write" and r["effect"] == "deny"
              for m in r["match"]]
    for prefix in va.PROTECTED_PREFIXES:
        # 用 covers_prefix 而不是 startswith：只 deny 目录里某个文件不算覆盖
        assert any(va.covers_prefix(d, prefix) for d in denied), prefix


def test_real_configs_have_no_ask_effect():
    for name in ("qa-design", "qa-executor", "qa-lead", "qa-reviewer"):
        with open(os.path.join(REAL_DIR, "%s.json" % name)) as fh:
            cfg = json.load(fh)
        for rule in cfg["permissions"]["rules"]:
            assert rule["effect"] != "ask", name


def test_real_executor_shell_denies_destructive_commands():
    """具备 shell 的角色至少要在工具层拒掉提交/推送/破坏性命令。

    注意这不是边界：item 5 已实测 shell 子进程写入绕过能力层。这条只保证
    工具层这一道没有缺失。
    """
    with open(os.path.join(REAL_DIR, "qa-executor.json")) as fh:
        cfg = json.load(fh)
    denied = [m for r in cfg["permissions"]["rules"]
              if r["capability"] == "shell" and r["effect"] == "deny"
              for m in r["match"]]
    for must in ("git push", "git commit", "rm -rf", "sudo"):
        assert any(d.startswith(must) for d in denied), must


# --------------------------------------------------------------------------
# 外部复核实测出的三处过弱判据 / 崩溃 —— 永久回归
# --------------------------------------------------------------------------

def test_R1_deny_single_file_does_not_count_as_covering_directory():
    """把每个受保护目录的 deny 缩成只 deny 一个文件，当时零 findings。

    存在一条同目录规则 ≠ 该目录被完整拒绝。
    """
    cfg = base_writer()
    cfg["permissions"]["rules"][1]["match"] = [
        "qa/baseline/one-file.txt", "qa/plan/one-file.txt",
        "scripts/one-file.txt", "isolation/one-file.txt",
        "tests/one-file.txt", ".github/one-file.txt", ".kiro/one-file.txt",
    ]
    found = codes(check(cfg))
    assert found.count("PROTECTED_PATH_NOT_DENIED") == 7, found


def test_R2_overbroad_allow_is_rejected():
    """把 allow 改成 `**`，当时零 findings。"""
    cfg = base_writer()
    cfg["permissions"]["rules"][0]["match"] = ["**"]
    found = codes(check(cfg))
    assert "ALLOW_OVERBROAD" in found, found


@pytest.mark.parametrize("pattern", ["*", "/**", "./**", "**/*"])
def test_other_overbroad_patterns_rejected(pattern):
    cfg = base_writer()
    cfg["permissions"]["rules"][0]["match"] = [pattern]
    assert "ALLOW_OVERBROAD" in codes(check(cfg))


def test_allow_outside_role_scope_rejected():
    """写别的角色的产物目录也要拒绝，哪怕它不在受保护清单里。"""
    cfg = base_writer()          # qa-design
    cfg["permissions"]["rules"][0]["match"] = ["qa/defects/**"]
    found = codes(check(cfg))
    assert "ALLOW_OUTSIDE_ROLE_SCOPE" in found, found


def test_unknown_writable_role_must_declare_scope():
    cfg = base_writer()
    cfg["name"] = "qa-newcomer"
    found = codes(check(cfg, "qa-newcomer.json"))
    assert "ROLE_WRITE_SCOPE_UNDECLARED" in found


def test_R3_malformed_tools_entry_does_not_crash():
    """tools=[{}] 当时抛 TypeError: unhashable type: dict，而不是输出违规。"""
    cfg = base_writer()
    cfg["tools"] = ["read", "write", {}]
    found = codes(check(cfg))       # 不得抛异常
    assert "TOOL_ENTRY_NOT_STRING" in found, found


@pytest.mark.parametrize("bad", [123, None, [], {"a": 1}])
def test_various_malformed_tool_entries(bad):
    cfg = base_writer()
    cfg["tools"] = ["read", "write", bad]
    assert "TOOL_ENTRY_NOT_STRING" in codes(check(cfg))


def test_malformed_match_entry_does_not_crash():
    cfg = base_writer()
    cfg["permissions"]["rules"][0]["match"] = [{"nested": True}, "qa/cases/**"]
    found = codes(check(cfg))       # 不得抛异常
    assert "WRITE_PATHS_NOT_EXPLICIT" not in found


# --------------------------------------------------------------------------
# covers_prefix 的判定表
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pattern,prefix,expected", [
    ("qa/baseline/**", "qa/baseline/", True),
    ("qa/baseline/*", "qa/baseline/", True),
    ("qa/baseline", "qa/baseline/", True),
    ("qa/baseline/", "qa/baseline/", True),
    ("qa/**", "qa/baseline/", True),          # 上层递归通配也算覆盖
    ("qa/baseline/one.txt", "qa/baseline/", False),
    ("qa/baselines/**", "qa/baseline/", False),   # 相近目录名不得误算
    ("", "qa/baseline/", False),
    ("other/**", "qa/baseline/", False),
])
def test_covers_prefix_table(pattern, prefix, expected):
    assert va.covers_prefix(pattern, prefix) is expected


def test_real_configs_still_pass_after_stricter_rules():
    """判据加严之后真实配置必须仍然合规——否则说明配置本身写松了。"""
    paths, findings = va.validate_dir(REAL_DIR)
    assert findings == [], [str(f) for f in findings]

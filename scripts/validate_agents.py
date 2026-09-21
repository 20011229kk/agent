#!/usr/bin/env python3
"""校验 .kiro/agents/*.json 是否符合实测得出的约束。

为什么需要这个校验器
--------------------
正式角色配置里每一条写法都对应一条**实测结论**（`docs/capability-matrix.md`）。
这些结论违反直觉，很容易在后续编辑中被"顺手改回常规写法"，例如：

- 给 `qa-lead` 加一条 `fs_write` deny 表达"它不写"——实测 item 7：父代理的 deny 按
  **交集**传播给子代理，会把子代理该有的写权限一起禁掉。表达"不写"只能靠 `tools` 省略。
- 依赖 `ask` 让使用者临时批准写入——实测：headless 与子代理场景下 `ask` 等同 deny，
  会变成"交互能跑、自动化静默失败"。预期写路径必须显式 `allow`。
- 配置里带 CLI-only 字段（`allowedTools` / `toolsSettings`）却没有 `permissions` 块——
  实测该组合被 IDE **静默跳过**，配置看着在、实际没生效。
- 用 `knowledge` 这类不在标签注册表内的 `tools` 取值——实测被接受但不匹配任何工具，
  同样是静默无效。

判据只针对**可静态检查**的项。它不证明角色行为正确，也不证明权限真的被强制
（item 5 / item 8 已实测两条绕过路径）。

用法
----
  python3 scripts/validate_agents.py [--root .] [--agents-dir .kiro/agents]

退出码：0 全部通过；1 存在违规；2 目录缺失或无配置。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

# item 12 实测：`todo_list` 是具体工具 ID；`knowledge` 不在注册表内（静默无效）；
# `spec` / `context` 的匹配结果**未能验证**，因此不放进白名单，用到就报错。
VERIFIED_TOOLS = {"read", "write", "shell", "subagent", "todo_list"}
KNOWN_INVALID_TOOLS = {"knowledge"}
UNVERIFIED_TOOLS = {"spec", "context"}

WRITE_TOOLS = {"write", "shell"}

# 只读角色：既不写文件也不执行 shell
READ_ONLY_ROLES = {"qa-lead", "qa-reviewer"}

# 受保护路径（与 docs/protected-paths.md、isolation/mount-policy.yaml 对齐）。
# 具备 write 的角色必须显式 deny 这些路径 —— 不是因为 deny 足够可靠
# （item 5 已证明 shell 可绕过），而是因为缺了它连工具层这一道都没有。
PROTECTED_PREFIXES = [
    "qa/baseline/",
    "qa/plan/",
    "scripts/",
    "isolation/",
    "tests/",
    ".github/",
]

CAPABILITIES = {"fs_read", "fs_write", "shell", "web_search", "execute_bash"}
EFFECTS = {"allow", "deny", "ask"}


class Finding:
    def __init__(self, path: str, code: str, message: str):
        self.path = path
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return "[%s] %s: %s" % (self.code, os.path.basename(self.path), self.message)


def _rules(cfg: dict) -> list:
    perms = cfg.get("permissions")
    if not isinstance(perms, dict):
        return []
    rules = perms.get("rules")
    return rules if isinstance(rules, list) else []


def _matches(rule: dict) -> list:
    m = rule.get("match")
    if isinstance(m, str):
        return [m]
    return m if isinstance(m, list) else []


def check_config(path: str, cfg: dict) -> list:
    findings = []
    name = cfg.get("name")

    if not name:
        findings.append(Finding(path, "NAME_MISSING", "缺少 name"))
    elif name != os.path.splitext(os.path.basename(path))[0]:
        findings.append(Finding(path, "NAME_MISMATCH",
                                "name=%r 与文件名不一致" % name))

    for field in ("description", "prompt"):
        if not cfg.get(field):
            findings.append(Finding(path, "FIELD_MISSING", "缺少 %s" % field))

    tools = cfg.get("tools")
    if not isinstance(tools, list) or not tools:
        findings.append(Finding(path, "TOOLS_MISSING", "tools 必须是非空列表"))
        tools = []

    for tool in tools:
        if tool in KNOWN_INVALID_TOOLS:
            findings.append(Finding(
                path, "TOOL_NOT_IN_REGISTRY",
                "%r 不在标签注册表内（实测被接受但不匹配任何工具，静默无效）" % tool))
        elif tool in UNVERIFIED_TOOLS:
            findings.append(Finding(
                path, "TOOL_UNVERIFIED",
                "%r 的匹配结果未能验证（item 12），不得用于正式配置" % tool))
        elif tool not in VERIFIED_TOOLS:
            findings.append(Finding(
                path, "TOOL_UNKNOWN",
                "%r 不在实测白名单 %s 内" % (tool, sorted(VERIFIED_TOOLS))))

    # permissions 块必须存在（即便规则为空）：实测含 CLI-only 字段且缺该块的配置
    # 会被 IDE 静默跳过
    if not isinstance(cfg.get("permissions"), dict):
        findings.append(Finding(
            path, "PERMISSIONS_BLOCK_MISSING",
            "必须带 permissions 块（即便 rules 为空）：含 CLI-only 字段且缺该块的"
            "配置被 IDE 静默跳过"))
    elif not isinstance(cfg["permissions"].get("rules"), list):
        findings.append(Finding(path, "PERMISSIONS_RULES_NOT_LIST",
                                "permissions.rules 必须是列表"))

    rules = _rules(cfg)
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            findings.append(Finding(path, "RULE_NOT_OBJECT", "rules[%d] 不是对象" % i))
            continue
        cap = rule.get("capability")
        eff = rule.get("effect")
        if cap not in CAPABILITIES:
            findings.append(Finding(path, "RULE_CAPABILITY_UNKNOWN",
                                    "rules[%d].capability=%r 未知" % (i, cap)))
        if eff not in EFFECTS:
            findings.append(Finding(path, "RULE_EFFECT_UNKNOWN",
                                    "rules[%d].effect=%r 未知" % (i, eff)))
        if not _matches(rule):
            findings.append(Finding(path, "RULE_MATCH_EMPTY",
                                    "rules[%d].match 为空" % i))
        if eff == "ask":
            findings.append(Finding(
                path, "RULE_EFFECT_ASK",
                "rules[%d] 使用 ask：headless 与子代理场景下 ask 等同 deny，"
                "会变成\"交互能跑、自动化静默失败\"。预期路径请显式 allow" % i))

    # 后续分析只看结构合法的规则项。非对象项已在上面单独报过 RULE_NOT_OBJECT；
    # 不过滤会让下面的 r.get(...) 直接抛 AttributeError —— 校验器自己崩掉比漏报更糟，
    # 因为调用方看到的是异常而不是判定。
    rules = [r for r in rules if isinstance(r, dict)]

    is_read_only = name in READ_ONLY_ROLES
    has_write = bool(WRITE_TOOLS & set(tools))

    if is_read_only:
        if has_write:
            findings.append(Finding(
                path, "READONLY_ROLE_HAS_WRITE_TOOL",
                "只读角色的 tools 不得含 %s" % sorted(WRITE_TOOLS & set(tools))))
        # 只读角色用 tools 省略表达，不能用 deny：deny 按交集传播给子代理
        for i, rule in enumerate(rules):
            if rule.get("effect") == "deny":
                findings.append(Finding(
                    path, "READONLY_ROLE_USES_DENY",
                    "rules[%d] 是 deny：只读必须用 tools 省略表达。实测 item 7 父代理的"
                    "deny 按交集传播给子代理，会把子代理该有的写权限一起禁掉" % i))
    else:
        if not has_write:
            findings.append(Finding(path, "ROLE_WITHOUT_WRITE_TOOL",
                                    "非只读角色却没有 write/shell"))
        allow_paths = [m for r in rules if r.get("capability") == "fs_write"
                       and r.get("effect") == "allow" for m in _matches(r)]
        if not allow_paths:
            findings.append(Finding(
                path, "WRITE_PATHS_NOT_EXPLICIT",
                "具备 write 的角色必须显式 allow 其写路径（ask 在自动化场景等同 deny）"))

        denied = [m for r in rules if r.get("capability") == "fs_write"
                  and r.get("effect") == "deny" for m in _matches(r)]
        for prefix in PROTECTED_PREFIXES:
            if not any(d.startswith(prefix) for d in denied):
                findings.append(Finding(
                    path, "PROTECTED_PATH_NOT_DENIED",
                    "未 deny 受保护路径 %s*（工具层这一道不能省；注意 item 5 已证明 "
                    "shell 可绕过它，真正边界靠执行隔离与受保护分支）" % prefix))

        # 写路径不得落在受保护路径内
        for a in allow_paths:
            for prefix in PROTECTED_PREFIXES:
                if a.startswith(prefix):
                    findings.append(Finding(
                        path, "ALLOW_INSIDE_PROTECTED",
                        "allow 的写路径 %r 落在受保护路径 %s* 内" % (a, prefix)))

    # allowedTools 是 CLI-only 字段；若出现必须是 tools 的子集
    allowed_tools = cfg.get("allowedTools")
    if allowed_tools is not None:
        if not isinstance(allowed_tools, list):
            findings.append(Finding(path, "ALLOWED_TOOLS_NOT_LIST",
                                    "allowedTools 必须是列表"))
        else:
            extra = set(allowed_tools) - set(tools)
            if extra:
                findings.append(Finding(path, "ALLOWED_TOOLS_NOT_SUBSET",
                                        "allowedTools 含 tools 之外的项: %s"
                                        % sorted(extra)))

    # 委派目标必须存在于同目录（availableAgents 的实际效果未验证，但指向不存在的
    # 角色一定是错的）
    settings = cfg.get("toolsSettings")
    if isinstance(settings, dict):
        sub = settings.get("subagent")
        if isinstance(sub, dict):
            if "subagent" not in tools:
                findings.append(Finding(
                    path, "SUBAGENT_SETTINGS_WITHOUT_TOOL",
                    "配了 toolsSettings.subagent 但 tools 里没有 subagent"))
            for key in ("availableAgents", "trustedAgents"):
                vals = sub.get(key)
                if vals is None:
                    continue
                if not isinstance(vals, list):
                    findings.append(Finding(path, "SUBAGENT_LIST_NOT_LIST",
                                            "%s 必须是列表" % key))
                    continue
                for target in vals:
                    sibling = os.path.join(os.path.dirname(path), "%s.json" % target)
                    if not os.path.isfile(sibling):
                        findings.append(Finding(
                            path, "SUBAGENT_TARGET_MISSING",
                            "%s 指向不存在的角色 %r" % (key, target)))

    return findings


def validate_dir(agents_dir: str):
    paths = sorted(glob.glob(os.path.join(agents_dir, "*.json")))
    findings = []
    for path in paths:
        try:
            with open(path) as fh:
                cfg = json.load(fh)
        except ValueError as exc:
            findings.append(Finding(path, "JSON_INVALID", "解析失败: %s" % exc))
            continue
        if not isinstance(cfg, dict):
            findings.append(Finding(path, "JSON_NOT_OBJECT", "顶层不是对象"))
            continue
        findings.extend(check_config(path, cfg))
    return paths, findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--agents-dir", default=None)
    args = ap.parse_args(argv)

    agents_dir = args.agents_dir or os.path.join(args.root, ".kiro", "agents")
    if not os.path.isdir(agents_dir):
        print("AGENTS_DIR_MISSING: %s" % agents_dir)
        return 2

    paths, findings = validate_dir(agents_dir)
    print("agents dir: %s" % agents_dir)
    print("configs: %d" % len(paths))
    if not paths:
        print("NO_CONFIGS")
        return 2

    if findings:
        print("verdict: FAIL (%d 项)" % len(findings))
        for f in findings:
            print("  %s" % f)
        return 1

    print("verdict: PASS")
    print("note: 只检查可静态核对的写法约束；**不证明**权限真的被强制"
          "（item 5 shell 绕过、item 8 委派路径失效均已实测）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

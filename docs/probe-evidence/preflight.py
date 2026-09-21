#!/usr/bin/env python3
"""探针执行前自检 + canary 重置。

为什么需要它：探针的判据依赖 canary 的"前后变化"。如果上一轮跑完没重置，
阳性对照写入的内容可能与现有内容相同 → 哈希不变 → 对照看起来失败 → 整个探针作废。
带着脏状态跑探针，比不跑更糟，因为会得出错误结论。

做四件事:
  1. 校验工作区结构完整（目录、探针配置、hook）
  2. 校验每份探针配置是合法 JSON 且声明了必要字段
  3. 把 canary 重置为本轮唯一的初始内容，并重算基线
  4. 提示操作者必须记录的会话规则快照

用法:
    python3 /tmp/kiro-probe/preflight.py            # 自检 + 重置
    python3 /tmp/kiro-probe/preflight.py --check-only
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path("/tmp/kiro-probe")

REQUIRED_DIRS = ["protected", "allowed", "ask-zone", "log", ".kiro/agents", ".kiro/hooks"]

REQUIRED_AGENTS = {
    "probe-readonly": {"items": [2, 12]},
    "probe-write-scoped": {"items": [3, 5, 6]},
    "probe-parent-omit": {"items": [7, 8]},
    "probe-parent-deny": {"items": [7]},
    "probe-write-ask": {"items": [8]},
    "probe-tagcheck": {"items": [12]},
}

REQUIRED_HOOKS = ["probe-file-and-stop.json"]

# 每轮重置时写入的初始内容。带时间戳，确保与上一轮不同，
# 从而任何"内容相同导致哈希不变"的假阴性都不可能发生。
CANARIES = ["protected/a.txt", "protected/b.txt",
            "allowed/a.txt", "allowed/b.txt",
            "ask-zone/a.txt"]


class Report:
    def __init__(self):
        self.problems = []
        self.notes = []

    def fail(self, code, detail):
        self.problems.append({"code": code, "detail": detail})

    def note(self, text):
        self.notes.append(text)

    @property
    def ok(self):
        return not self.problems


def check_structure(rep):
    for d in REQUIRED_DIRS:
        p = ROOT / d
        if not p.is_dir():
            rep.fail("DIR_MISSING", "缺少目录 {}".format(p))


def check_agents(rep):
    adir = ROOT / ".kiro" / "agents"
    if not adir.is_dir():
        return
    found = {p.stem for p in adir.glob("*.json")}
    for name, meta in REQUIRED_AGENTS.items():
        if name not in found:
            rep.fail("AGENT_CONFIG_MISSING",
                     "缺少探针配置 {}.json（item {} 需要）".format(name, meta["items"]))
            continue
        path = adir / "{}.json".format(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            rep.fail("AGENT_CONFIG_INVALID", "{} 不是合法 JSON: {}".format(path, exc))
            continue
        if data.get("name") != name:
            rep.fail("AGENT_NAME_MISMATCH",
                     "{} 的 name 字段为 '{}'，与文件名不一致".format(path, data.get("name")))
        if not data.get("tools"):
            rep.fail("AGENT_TOOLS_MISSING", "{} 未声明 tools".format(path))
        if "ONE-OFF PROBE AGENT" not in (data.get("description") or ""):
            rep.fail("AGENT_NOT_MARKED_PROBE",
                     "{} 的 description 未标注为一次性探针配置 —— "
                     "避免被误当成正式团队 agent".format(path))

    extra = found - set(REQUIRED_AGENTS)
    if extra:
        rep.note("发现额外的 agent 配置（不影响探针，但确认它们不是正式配置）: {}".format(
            sorted(extra)))


def check_hooks(rep):
    hdir = ROOT / ".kiro" / "hooks"
    if not hdir.is_dir():
        return
    for name in REQUIRED_HOOKS:
        p = hdir / name
        if not p.is_file():
            rep.fail("HOOK_MISSING", "缺少 hook {}（item 10/11 需要）".format(p))
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            rep.fail("HOOK_INVALID", "{} 不是合法 JSON: {}".format(p, exc))
            continue
        if data.get("version") != "v1":
            rep.fail("HOOK_VERSION_UNEXPECTED",
                     "{} 的 version 为 '{}'，期望 v1".format(p, data.get("version")))
        if not data.get("hooks"):
            rep.fail("HOOK_EMPTY", "{} 未定义任何 hook".format(p))


def check_workspace_hint(rep):
    """提醒：探针配置只在 /tmp/kiro-probe 被当作工作区打开时才加载。"""
    rep.note("探针配置只在 Kiro 把 /tmp/kiro-probe 作为**工作区**打开时才加载。"
             "若你在别的工作区里切 agent，会找不到这些配置。")
    rep.note("每项探针执行前必须在聊天里跑 /tools 并把输出存入 "
             "log/session-rules-<item>.txt —— 本机存在 session 作用域授权，"
             "不记录会把它的效果误归因为 agent 配置。")


def reset_canaries():
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    written = {}
    for rel in CANARIES:
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        content = "canary {} reset-at {}\n".format(rel, stamp)
        p.write_text(content, encoding="utf-8")
        written[rel] = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # 清掉上一轮的 hook 触发日志与一次性标记，避免旧记录被当成本轮证据
    for stale in ("item10-hook-fired.log", "item11-hook-fired.log",
                  "item11-blocked-once"):
        sp = ROOT / "log" / stale
        if sp.exists():
            sp.unlink()

    baseline = ROOT / "log" / "baseline-hashes.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(json.dumps(written, indent=2), encoding="utf-8")
    return stamp, written


def main():
    ap = argparse.ArgumentParser(description="探针执行前自检与 canary 重置")
    ap.add_argument("--check-only", action="store_true",
                    help="只自检，不重置 canary")
    args = ap.parse_args()

    rep = Report()
    check_structure(rep)
    check_agents(rep)
    check_hooks(rep)
    check_workspace_hint(rep)

    print("=== 探针包自检 ===")
    if rep.problems:
        print("发现 {} 个问题：".format(len(rep.problems)))
        for p in rep.problems:
            print("  [{}] {}".format(p["code"], p["detail"]))
    else:
        print("结构、配置、hook 均正常")

    if not args.check_only:
        if not rep.ok:
            print("\n自检未通过，**不重置 canary** —— 先修好结构再跑探针。")
        else:
            stamp, written = reset_canaries()
            print("\n=== canary 已重置（时间戳 {}）===".format(stamp))
            for rel, h in written.items():
                print("  {}  {}".format(h[:16], rel))
            print("基线已写入 log/baseline-hashes.json")
            print("上一轮的 hook 触发日志与一次性标记已清除")

    print("\n=== 操作提醒 ===")
    for n in rep.notes:
        print("  · {}".format(n))

    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())

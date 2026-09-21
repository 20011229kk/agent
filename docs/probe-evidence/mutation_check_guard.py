#!/usr/bin/env python3
"""对 guard_scan.py 与它接进 gate_check 的路径做变异检验。

每条变异对应一种"让门槛下降不被发现"的手法。
"""
import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path("/Users/AI/test-agent")
GUARD = REPO / "scripts" / "guard_scan.py"
GATE = REPO / "scripts" / "gate_check.py"
T_GUARD = REPO / "tests" / "test_guard_scan.py"
T_GATE = REPO / "tests" / "test_gate_check.py"

# (名称, 目标文件, 测试文件, 原文, 替换)
MUTATIONS = [
    ("skip 检测失效", GUARD, T_GUARD,
     "            if re.search(pat, line):\n                hints.append(Hint(\n                    \"SKIP_ADDED\", fd.path,",
     "            if False:\n                hints.append(Hint(\n                    \"SKIP_ADDED\", fd.path,"),

    ("断言删除检测失效", GUARD, T_GUARD,
     "    if removed > added:",
     "    if False:"),

    ("阈值方向判断反了（放宽说成收紧）", GUARD, T_GUARD,
     "        looser = (new_max < old_max) if looser_when_lower else (new_max > old_max)",
     "        looser = (new_max > old_max) if looser_when_lower else (new_max < old_max)"),

    ("测试文件删除不报", GUARD, T_GUARD,
     "    if fd.is_deleted and TEST_PATH_RE.search(fd.path):",
     "    if False:"),

    ("受保护路径触及不报", GUARD, T_GUARD,
     "        if fd.path == prefix or fd.path.startswith(prefix):",
     "        if False:"),

    ("忽略标记新增不报", GUARD, T_GUARD,
     "            if re.search(pat, line):\n                hints.append(Hint(\n                    \"IGNORE_WIDENED\", fd.path,",
     "            if False:\n                hints.append(Hint(\n                    \"IGNORE_WIDENED\", fd.path,"),

    ("豁免不要求替代覆盖（只写原因即可关闭）", GUARD, T_GUARD,
     "            if w.get(\"reason\") and w.get(\"alternative_coverage\"):",
     "            if w.get(\"reason\"):"),

    ("提示器升级为判定器（产生 QUALITY_FAILURE）", GUARD, T_GUARD,
     "            kind=\"HINT\",",
     "            kind=\"QUALITY_FAILURE\","),

    ("去掉「提示，非结论」声明", GUARD, T_GUARD,
     "    \"提示，非结论。本工具不能判断断言语义是否被削弱、数据清理是否有效、\"",
     "    \"扫描完成。\"  # "),

    ("序列化时截断 hints", GUARD, T_GUARD,
     "        out[\"hints\"] = [h.as_dict() for h in self.hints]",
     "        out[\"hints\"] = [h.as_dict() for h in self.hints[:1]]"),

    ("未评审的提示项被静默忽略", GATE, T_GATE,
     "    if unreviewed:",
     "    if False:"),

    # F4 修复把 hints 先取出为 all_hints，原模式失效
    ("guard 提示根本不进报告", GATE, T_GATE,
     "    for h in all_hints:",
     "    for h in []:"),
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(tests):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests), "-q", "--no-header"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()
    return proc.returncode, tail[-1] if tail else ""


def main():
    originals = {GUARD: GUARD.read_text(encoding="utf-8"),
                 GATE: GATE.read_text(encoding="utf-8")}
    hashes = {p: sha(p) for p in originals}

    for tests in (T_GUARD, T_GATE):
        rc, tail = run(tests)
        print("BASELINE {}: rc={} {}".format(tests.name, rc, tail))
        if rc != 0:
            print("FATAL: 基线未通过")
            return 2

    results = []
    for name, target, tests, old, new in MUTATIONS:
        src = originals[target]
        if old not in src:
            results.append((name, "PATTERN_NOT_FOUND", ""))
            continue
        target.write_text(src.replace(old, new, 1), encoding="utf-8")
        rc, tail = run(tests)
        target.write_text(src, encoding="utf-8")
        assert sha(target) == hashes[target], "恢复失败: {}".format(target)
        results.append((name, "CAUGHT" if rc != 0 else "NOT_CAUGHT", tail))

    print("\n=== 变异检验结果 ===")
    ok = True
    for name, verdict, tail in results:
        print("[{}] {}".format(verdict, name))
        if tail:
            print("        {}".format(tail))
        if verdict != "CAUGHT":
            ok = False

    print("\n恢复校验: {}".format(
        "OK" if all(sha(p) == hashes[p] for p in originals) else "MISMATCH"))
    print("总判定: {}".format("ALL_MUTATIONS_CAUGHT" if ok else "SOME_MUTATIONS_ESCAPED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

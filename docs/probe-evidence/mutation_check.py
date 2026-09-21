#!/usr/bin/env python3
"""变异检验：故意破坏 trace_matrix.py 的关键判定，确认测试会失败。

如果某个变异下测试仍然全绿，说明该行为没有被真正约束（测试是装饰而非验证）。
每次变异后都恢复原文件，并用哈希确认恢复无误。
"""
import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path("/Users/AI/test-agent")
TARGET = REPO / "scripts" / "trace_matrix.py"
TESTS = REPO / "tests" / "test_trace_matrix.py"

MUTATIONS = [
    (
        "证据缺失时把 automated 当成 False（而不是 unknown）",
        "        automated = None\n        if automation_evidence == \"PRESENT\":",
        "        automated = False\n        if automation_evidence == \"PRESENT\":",
    ),
    (
        "把 manual 用例算进可自动化分母",
        "    automatable = [c for c in cases if not c.manual]",
        "    automatable = list(cases)",
    ),
    (
        # F5 修复把布尔 all() 换成三态聚合后，原模式失效（PATTERN_NOT_FOUND），
        # 这里重定目标：让失败状态不再登记为失败。
        "参数化/多对多：失败不再登记（任一通过即算通过）",
        "            elif last.status in (\"failed\", \"error\"):\n                saw_failed = True",
        "            elif last.status in (\"failed\", \"error\"):\n                pass",
    ),
    (
        "覆盖率分母改成用例数（而非基线条数）—— 制造假全覆盖",
        "        \"req_total\": len(reqs),",
        "        \"req_total\": sum(1 for v in per_req.values() if v[\"designed\"]),",
    ),
    (
        "去掉手工必测项的证据检查",
        "                gaps.append(Gap(\"MANUAL_EVIDENCE_ABSENT\", \"BLOCKING\", tid,",
        "                pass  # noqa\n            if False:\n                gaps.append(Gap(\"MANUAL_EVIDENCE_ABSENT\", \"BLOCKING\", tid,",
    ),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_tests():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS), "-q", "--no-header", "-x"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    return proc.returncode, (proc.stdout or "").strip().splitlines()[-1:]


def main():
    original = TARGET.read_text(encoding="utf-8")
    baseline_hash = sha(TARGET)

    rc, tail = run_tests()
    print("BASELINE: rc={} {}".format(rc, tail))
    if rc != 0:
        print("FATAL: 基线测试未通过，变异检验无意义")
        return 2

    results = []
    for name, old, new in MUTATIONS:
        if old not in original:
            results.append((name, "PATTERN_NOT_FOUND", None))
            continue
        TARGET.write_text(original.replace(old, new, 1), encoding="utf-8")
        rc, tail = run_tests()
        TARGET.write_text(original, encoding="utf-8")
        assert sha(TARGET) == baseline_hash, "恢复失败！"
        verdict = "CAUGHT" if rc != 0 else "NOT_CAUGHT"
        results.append((name, verdict, tail))

    print("\n=== 变异检验结果 ===")
    all_caught = True
    for name, verdict, tail in results:
        print("[{}] {}".format(verdict, name))
        if tail:
            print("        {}".format(tail[0] if tail else ""))
        if verdict != "CAUGHT":
            all_caught = False

    print("\n恢复校验: {}".format("OK" if sha(TARGET) == baseline_hash else "MISMATCH"))
    print("总判定: {}".format("ALL_MUTATIONS_CAUGHT" if all_caught else "SOME_MUTATIONS_ESCAPED"))
    return 0 if all_caught else 1


if __name__ == "__main__":
    sys.exit(main())

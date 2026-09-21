#!/usr/bin/env python3
"""对 gate_check.py 做变异检验，重点是六条反例验收与三态判定顺序。

每个变异都对应一种"为了让门禁变绿"的真实手法。若某变异下测试仍全绿，
说明该反例并没有被真正约束。
"""
import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path("/Users/AI/test-agent")
TARGET = REPO / "scripts" / "gate_check.py"
TESTS = REPO / "tests" / "test_gate_check.py"

MUTATIONS = [
    (
        # F3 修复后原模式会匹配到 reason 文案行而非门禁条件行，变异因此没有语义效果、
        # 被误报为 NOT_CAUGHT。这是"模式漂移伪装成覆盖丢失"的一例，比 PATTERN_NOT_FOUND
        # 更危险。重定目标：直接打掉门禁条件。
        "反例2：允许重试通过抹掉已确认阻断缺陷",
        "        if info[\"confirmed_blocking_defect\"] or severity_blocking:",
        "        if severity_blocking:",
    ),
    (
        "反例2：未解释的间歇失败直接算通过",
        "        elif info[\"intermittent\"] and not accept_last_pass:",
        "        elif False:",
    ),
    (
        # F2 修复把"字段非空才核对"改成"先查存在性再核对"，条件行结构随之变化
        "反例4：不检查评审结论是否绑定当前候选 SHA",
        "    elif binding.get(\"candidate_sha\") and vsha != binding[\"candidate_sha\"]:",
        "    elif False:",
    ),
    (
        "反例5：不检查 review-verdict 的产出者（候选自提也算）",
        "    if verdict.get(\"produced_by\") != \"ci-review-executor\":",
        "    if False:",
    ),
    (
        "反例6：automated_required 下缺 verdict 也放行",
        "    if verdict is None:",
        "    if False:",
    ),
    (
        "三态：证据缺失也判 PASS",
        "    elif missing:\n        verdict = VERDICT_INCOMPLETE",
        "    elif False:\n        verdict = VERDICT_INCOMPLETE",
    ),
    (
        "三态：把 FAIL 降级为 INCOMPLETE（掩盖质量失败）",
        "    if quality:\n        verdict = VERDICT_FAIL",
        "    if quality:\n        verdict = VERDICT_INCOMPLETE",
    ),
    (
        "报告只列第一条发现（隐藏其余缺失项）",
        "        out[\"findings\"] = [f.as_dict() for f in self.findings]",
        "        out[\"findings\"] = [f.as_dict() for f in self.findings[:1]]",
    ),
    (
        "脱敏检查失效（凭据可进运行记录）",
        "    for marker in (\"password\", \"token\", \"secret\", \"Bearer \", \"cookie\"):",
        "    for marker in ():",
    ),
    (
        "版本绑定字段缺失也不报",
        "        if not binding.get(field):",
        "        if False:",
    ),
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_tests():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS), "-q", "--no-header"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()
    return proc.returncode, tail[-1] if tail else ""


def main():
    original = TARGET.read_text(encoding="utf-8")
    base_hash = sha(TARGET)

    rc, tail = run_tests()
    print("BASELINE: rc={} {}".format(rc, tail))
    if rc != 0:
        print("FATAL: 基线未通过，变异检验无意义")
        return 2

    results = []
    for name, old, new in MUTATIONS:
        if old not in original:
            results.append((name, "PATTERN_NOT_FOUND", ""))
            continue
        TARGET.write_text(original.replace(old, new, 1), encoding="utf-8")
        rc, tail = run_tests()
        TARGET.write_text(original, encoding="utf-8")
        assert sha(TARGET) == base_hash, "恢复失败"
        results.append((name, "CAUGHT" if rc != 0 else "NOT_CAUGHT", tail))

    print("\n=== 变异检验结果 ===")
    ok = True
    for name, verdict, tail in results:
        print("[{}] {}".format(verdict, name))
        if tail:
            print("        {}".format(tail))
        if verdict != "CAUGHT":
            ok = False

    print("\n恢复校验: {}".format("OK" if sha(TARGET) == base_hash else "MISMATCH"))
    print("总判定: {}".format("ALL_MUTATIONS_CAUGHT" if ok else "SOME_MUTATIONS_ESCAPED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

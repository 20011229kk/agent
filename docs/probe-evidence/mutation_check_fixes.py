#!/usr/bin/env python3
"""对 F1–F5 五处修复做变异检验。

每条变异都把修复"退回"到原来的缺陷形态。若某条未被捕获，说明该修复没有被测试约束住，
下次重构就会悄悄退化。
"""
import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path("/Users/AI/test-agent")
TRACE = REPO / "scripts" / "trace_matrix.py"
GATE = REPO / "scripts" / "gate_check.py"
GUARD = REPO / "scripts" / "guard_scan.py"
TESTS = REPO / "tests"

MUTATIONS = [
    # ---- F1：按 REQ 指定的必测范围参与结果判定 ----
    ("F1 门禁退回只读 required_cases（REQ 范围不参与判定）", GATE,
     "    required_cases = list(trace.effective_required_cases)",
     "    required_cases = [str(x) for x in (policy.get(\"required_cases\") or [])]"),

    ("F1 有效范围不再展开必测 REQ", TRACE,
     "        expanded.extend(covering)",
     "        pass"),

    ("F1 必测 REQ 无用例覆盖也放行", TRACE,
     "            gaps.append(Gap(\"REQUIRED_REQ_WITHOUT_CASE\", \"BLOCKING\", rid,",
     "            pass\n        if False:\n            gaps.append(Gap(\"REQUIRED_REQ_WITHOUT_CASE\", \"BLOCKING\", rid,"),

    # ---- F2：缺字段不得跳过版本核对 ----
    ("F2 run.candidate_sha 缺失时跳过核对", GATE,
     "    if not rsha:\n        findings.append(Finding(\n            \"RUN_CANDIDATE_SHA_ABSENT\",",
     "    if False:\n        findings.append(Finding(\n            \"RUN_CANDIDATE_SHA_ABSENT\","),

    ("F2 run.baseline_hash 缺失时跳过核对", TRACE,
     "        if not run_hash:\n            gaps.append(Gap(\"RUN_BASELINE_HASH_ABSENT\",",
     "        if False:\n            gaps.append(Gap(\"RUN_BASELINE_HASH_ABSENT\","),

    ("F2 不再把 binding 与实际基线交叉核对", GATE,
     "    if trace is not None:",
     "    if False:"),

    ("F2 run 声明的 target_sha/policy_version 不再核对", GATE,
     "        if run_val and exp_val and str(run_val) != str(exp_val):",
     "        if False:"),

    ("F2 用例/范围的 baseline_version 不再核对", TRACE,
     "    if baseline_version:\n        scope_bv = scope_meta.get(\"baseline_version\")",
     "    if False:\n        scope_bv = scope_meta.get(\"baseline_version\")"),

    # ---- F3：REQ 级阻断缺陷传播 + 严重级契约 ----
    ("F3 REQ 级缺陷不再传播到覆盖它的用例", TRACE,
     "        for rid in c.covers:\n            for d in defects_by_req.get(rid, []):",
     "        for rid in []:\n            for d in defects_by_req.get(rid, []):"),

    ("F3 已关闭的缺陷仍然阻断", TRACE,
     "        open_defects = [d for d in all_defects if not d[\"closed\"]]",
     "        open_defects = list(all_defects)"),

    ("F3 blocking_defect_severities 退回占位（不参与计算）", GATE,
     "        if info[\"confirmed_blocking_defect\"] or severity_blocking:",
     "        if info[\"confirmed_blocking_defect\"]:"),

    ("F3 confirmed_blocking 路径被禁用（只看严重级）", GATE,
     "        if info[\"confirmed_blocking_defect\"] or severity_blocking:",
     "        if severity_blocking:"),

    ("F3 两条阻断路径全部禁用", GATE,
     "        if info[\"confirmed_blocking_defect\"] or severity_blocking:",
     "        if False:"),

    # ---- F4：只消费已验证的处置记录 ----
    ("F4 manual 模式重新消费本地 verdict", GATE,
     "        return mode, None\n\n    # ---- automated_required ----",
     "        return mode, verdict\n\n    # ---- automated_required ----"),

    ("F4 来源不可信的 verdict 也当可信", GATE,
     "        trusted = False\n        findings.append(Finding(\n            \"REVIEW_VERDICT_UNTRUSTED\",",
     "        trusted = True\n        findings.append(Finding(\n            \"REVIEW_VERDICT_UNTRUSTED\","),

    ("F4 处置记录退回按规则码匹配", GATE,
     "            hid = item.get(\"hint_id\")",
     "            hid = item.get(\"hint_id\") or item.get(\"code\")"),

    ("F4 处置不再要求说明与替代覆盖", GATE,
     "            if not item.get(\"reason\") or not item.get(\"alternative_coverage\"):",
     "            if False:"),

    ("F4 hint_id 不再包含文件与证据（同类提示会被一次关掉）", GUARD,
     "        payload = \"|\".join([self.code, self.file, self.evidence])",
     "        payload = self.code"),

    # ---- F5：缺失/跳过不是失败 ----
    ("F5 跳过重新算作失败", TRACE,
     "            else:\n                # skipped / xfailed / unknown —— 没测，不是测出问题\n                saw_unknown = True",
     "            else:\n                saw_failed = True"),

    ("F5 无 attempt 重新算作失败", TRACE,
     "                at_statuses[key] = \"no_attempt\"\n                saw_unknown = True",
     "                at_statuses[key] = \"no_attempt\"\n                saw_failed = True"),

    ("F5 三态聚合退回布尔 all()", TRACE,
     "            if saw_failed:\n                passed_last = False\n            elif saw_unknown:\n                passed_last = None",
     "            if saw_failed or saw_unknown:\n                passed_last = False\n            elif False:\n                passed_last = None"),
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS), "-q", "--no-header"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()
    return proc.returncode, tail[-1] if tail else ""


def main():
    targets = {TRACE: TRACE.read_text(encoding="utf-8"),
               GATE: GATE.read_text(encoding="utf-8"),
               GUARD: GUARD.read_text(encoding="utf-8")}
    hashes = {p: sha(p) for p in targets}

    rc, tail = run()
    print("BASELINE: rc={} {}".format(rc, tail))
    if rc != 0:
        print("FATAL: 基线未通过，变异检验无意义")
        return 2

    results = []
    for name, target, old, new in MUTATIONS:
        src = targets[target]
        if old not in src:
            results.append((name, "PATTERN_NOT_FOUND", ""))
            continue
        target.write_text(src.replace(old, new, 1), encoding="utf-8")
        rc, tail = run()
        target.write_text(src, encoding="utf-8")
        assert sha(target) == hashes[target], "恢复失败: {}".format(target)
        results.append((name, "CAUGHT" if rc != 0 else "NOT_CAUGHT", tail))

    print("\n=== F1–F5 修复变异检验 ===")
    ok = True
    for name, verdict, tail in results:
        print("[{}] {}".format(verdict, name))
        if tail:
            print("        {}".format(tail))
        if verdict != "CAUGHT":
            ok = False

    print("\n恢复校验: {}".format(
        "OK" if all(sha(p) == hashes[p] for p in targets) else "MISMATCH"))
    print("总判定: {}".format("ALL_MUTATIONS_CAUGHT" if ok else "SOME_MUTATIONS_ESCAPED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""端到端 demo：在临时仓库里验证轨 A 的 Demo 判据。

场景一：一切齐备 → PASS
场景二：删掉运行记录 → INCOMPLETE（证据不足，质量失败数 0）
场景三：必测用例失败 → FAIL（质量失败数 ≥1）
场景四：先失败后通过 + 已确认阻断缺陷 → FAIL，last_result 与 gate_accepted 分离
场景五：带门槛下降的 diff → 提示进报告并要求评审确认
场景六：基线 10 条只覆盖 8 条 → 精确报出未覆盖，分母仍为 10

在临时目录进行，不污染仓库。
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path("/Users/AI/test-agent")
sys.path.insert(0, str(REPO / "scripts"))

import gate_check as gc  # noqa: E402
import guard_scan as gs  # noqa: E402
import trace_matrix as tm  # noqa: E402
import yaml  # noqa: E402

FEATURE = "login"
SHA = "cafebabe"


def req(rid):
    return {"id": rid, "statement": "验收标准 {}".format(rid),
            "risk": "high", "blocking": True}


def setup(root, reqs=None, cases=None, attempts=None, defect=None,
          scope_cases=None):
    reqs = reqs if reqs is not None else [req("REQ-1")]
    data = {"meta": {"feature": FEATURE, "baseline_version": "1.0.0",
                     "confirmed_by": "zhang.san", "confirmed_at": "2026-09-20",
                     "baseline_approval_ref": "https://example/mr/1"},
            "requirements": reqs}
    data["meta"]["content_hash"] = tm.compute_baseline_hash(data)
    p = root / "qa" / "baseline" / FEATURE / "requirements.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    cases = cases if cases is not None else [
        {"id": "TC-1", "covers": ["REQ-1"], "manual": False,
         "layer": "api", "priority": "P0", "method": "等价类"}]
    fm = {"feature": FEATURE, "baseline_version": "1.0.0", "cases": cases}
    p = root / "qa" / "cases" / FEATURE / "core.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
                 + "---\n", encoding="utf-8")

    p = root / "qa" / "trace" / FEATURE / "collected.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"tests": [
        {"node_id": "tests/t.py::test_{}".format(c["id"].lower().replace("-", "_")),
         "cases": [c["id"]]} for c in cases if not c.get("manual")]}), encoding="utf-8")

    if attempts is None:
        attempts = [{"node_id": "tests/t.py::test_tc_1", "attempt": 1, "status": "passed"}]
    p = root / "qa" / "runs" / "r1" / "run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "run_id": "r1", "candidate_sha": SHA, "baseline_version": "1.0.0",
        "baseline_hash": data["meta"]["content_hash"],
        "config": {"env": "qa-isolated", "traceId": "t-1", "authorization": "<redacted>"},
        "attempts": attempts}), encoding="utf-8")

    scope = {"meta": {"feature": FEATURE, "baseline_version": "1.0.0",
                      "confirmed_by": "zhang.san"},
             "required_requirements": [], "required_cases": scope_cases or ["TC-1"],
             "retry_policy": {"max_attempts": 1,
                              "accept_last_pass_without_explanation": False,
                              "allow_retry_to_clear_confirmed_blocking_defect": False},
             "blocking_defect_severities": ["S1"], "review_mode": "manual"}
    p = root / "qa" / "plan" / FEATURE / "required-scope.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(scope, allow_unicode=True, sort_keys=False), encoding="utf-8")

    if defect:
        p = root / "qa" / "defects" / "BUG-1.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\n" + yaml.safe_dump(defect, allow_unicode=True) + "---\n",
                     encoding="utf-8")

    return data["meta"]["content_hash"]


def binding(h):
    return {"candidate_sha": SHA, "target_sha": "1111", "policy_version": "1.0.0",
            "baseline_version": "1.0.0", "baseline_hash": h,
            "baseline_approval_ref": "https://example/mr/1",
            "human_approval_ref": "https://example/mr/1/approval"}


def show(title, rep):
    print("\n" + "=" * 70)
    print("场景：{}".format(title))
    print("=" * 70)
    print("verdict           : {}".format(rep.verdict))
    print("质量失败 / 证据缺失: {} / {}".format(
        rep.counts["quality_failures"], rep.counts["evidence_missing"]))
    print("last_result       : {}".format(rep.verified_scope["last_result"]))
    print("gate_accepted     : {}".format(rep.verified_scope["gate_accepted"]))
    print("未验证项           : {}".format(rep.unverified or "无"))
    for f in rep.findings:
        print("  [{}] {} · {}".format(f.kind, f.code, f.subject))


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="qa-e2e-"))
    try:
        # 场景一
        r1 = tmp / "s1"; r1.mkdir()
        h = setup(r1)
        show("一切齐备 → 期望 PASS", gc.evaluate(r1, FEATURE, binding(h)))

        # 场景二
        r2 = tmp / "s2"; r2.mkdir()
        h = setup(r2)
        (r2 / "qa" / "runs" / "r1" / "run.json").unlink()
        show("删掉运行记录 → 期望 INCOMPLETE（质量失败 0）",
             gc.evaluate(r2, FEATURE, binding(h)))

        # 场景三
        r3 = tmp / "s3"; r3.mkdir()
        h = setup(r3, attempts=[{"node_id": "tests/t.py::test_tc_1",
                                 "attempt": 1, "status": "failed"}])
        show("必测用例失败 → 期望 FAIL", gc.evaluate(r3, FEATURE, binding(h)))

        # 场景四
        r4 = tmp / "s4"; r4.mkdir()
        h = setup(r4,
                  attempts=[{"node_id": "tests/t.py::test_tc_1", "attempt": 1, "status": "failed"},
                            {"node_id": "tests/t.py::test_tc_1", "attempt": 2, "status": "passed"}],
                  defect={"id": "BUG-1", "tc": ["TC-1"], "req": [], "severity": "S1",
                          "confirmed_blocking": True, "root_cause": "PRODUCT",
                          "stability": "间歇失败"})
        show("先失败后通过 + 已确认阻断缺陷 → 期望 FAIL 且 last_result≠gate_accepted",
             gc.evaluate(r4, FEATURE, binding(h)))

        # 场景五
        r5 = tmp / "s5"; r5.mkdir()
        h = setup(r5)
        diff_text = pathlib.Path("/tmp/demo-candidate.diff").read_text(encoding="utf-8")
        guard = gs.scan_diff(diff_text, root=r5, with_waivers=True).as_dict()
        show("带门槛下降的 diff → 提示进报告并要求评审确认",
             gc.evaluate(r5, FEATURE, binding(h), guard_result=guard))

        # 场景六
        r6 = tmp / "s6"; r6.mkdir()
        reqs = [req("REQ-{}".format(i)) for i in range(1, 11)]
        cases = [{"id": "TC-{}".format(i), "covers": ["REQ-{}".format(i)],
                  "manual": False, "layer": "api", "priority": "P0",
                  "method": "等价类"} for i in range(1, 9)]
        setup(r6, reqs=reqs, cases=cases, scope_cases=[])
        rep = tm.build_report(r6, FEATURE)
        print("\n" + "=" * 70)
        print("场景：基线 10 条、用例覆盖 8 条 → 期望精确报出 2 条未覆盖，分母仍为 10")
        print("=" * 70)
        print("req_total     : {}".format(rep.counts["req_total"]))
        print("req_designed  : {}".format(rep.counts["req_designed"]))
        uncovered = sorted(g.subject for g in rep.gaps if g.code == "UNCOVERED_REQ")
        print("未覆盖         : {}".format(uncovered))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

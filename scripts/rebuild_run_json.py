#!/usr/bin/env python3
"""从框架原生报告（JUnit XML）重建门禁输入 `run.json`。

为什么必须"重建"而不是直接用候选提交里的 run.json
--------------------------------------------------
`run.json` 是门禁的输入。如果它来自候选分支的工作副本，候选就能直接编造通过结果——
`item 5` 已实测 shell 子进程写入完全绕过 Kiro 能力层，所以"靠权限规则保护这份文件"
不成立。唯一可靠的做法是在受保护的 CI 里，**从机器产出的原始报告重新生成**它。

本脚本因此属于受保护路径（见 `docs/protected-paths.md`）：改它等于改证据来源。

输入
----
JUnit XML（近乎所有测试框架都能产出：pytest `--junitxml`、jest `jest-junit`、
Maven Surefire、go-junit-report、Robot Framework `--xunit` 等）。选它是为了
不假设被测项目用哪个框架。

**尚未支持**：JSON report、TRX、TAP。遇到这些格式应当扩展本脚本并补测试，
而不是在 CI 里临时转换（临时转换脚本不在受保护路径内，等于把证据来源搬到了不受保护的地方）。

状态映射（三态的来源，别在下游再猜一次）
----------------------------------------
| JUnit 元素 | attempt.status |
|---|---|
| 无 failure/error/skipped 子元素 | passed |
| `<failure>` | failed |
| `<error>` | error |
| `<skipped>` | skipped |

`skipped` **不是** failed：把"没测"算成"测出问题"会让门禁给出错误分类。
三态聚合规则在 `scripts/trace_matrix.py` / `gate_check.py`，此处只忠实转录。

版本绑定
--------
六个字段必须由**调用方显式传入**，脚本不自行推断、也不从候选文件里读：
`candidate_sha` / `target_sha` / `policy_version` / `baseline_version` /
`baseline_hash` / `run_id`。缺任何一个直接拒绝，而不是填空字符串——
`gate_check` 侧已实测过"字段缺失反而跳过核对"这类误放行（F2 修复）。

用法
----
  python3 scripts/rebuild_run_json.py \
      --raw qa/runs/<id>/raw --out qa/runs/<id>/run.json \
      --run-id <id> --candidate-sha ... --target-sha ... \
      --policy-version ... --baseline-version ... --baseline-hash ...

退出码：0 成功；64 参数缺失；65 原始报告缺失或不可解析；0 但报告里标注
`incomplete: true` 表示有解析不了的文件（下游据此判 INCOMPLETE，不得当成通过）。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

EX_USAGE = 64
EX_DATAERR = 65

# 配置标识脱敏：原生报告里常带连接串、token、路径。只保留可追溯的 traceId 之类。
SENSITIVE_PATTERNS = [
    (re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"), r"\1=<redacted>"),
    (re.compile(r"(?i)(token|secret|api[_-]?key)\s*[=:]\s*\S+"), r"\1=<redacted>"),
    (re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*)://[^\s\"']*:[^\s\"'@]*@"), "<redacted>@"),
    (re.compile(r"(?i)authorization:\s*\S+"), "authorization: <redacted>"),
]

MAX_MESSAGE_CHARS = 2000


def redact(text: str) -> str:
    if not text:
        return ""
    out = text
    for pattern, repl in SENSITIVE_PATTERNS:
        out = pattern.sub(repl, out)
    if len(out) > MAX_MESSAGE_CHARS:
        out = out[:MAX_MESSAGE_CHARS] + "...<truncated>"
    return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _case_status(case: ET.Element) -> tuple:
    """返回 (status, message)。顺序固定：error > failure > skipped > passed。

    一个 case 同时带 failure 与 skipped 的情况确实存在（框架实现差异），
    此时按"更坏"的那个记，避免把失败读成跳过。
    """
    for tag, status in (("error", "error"), ("failure", "failed"),
                        ("skipped", "skipped")):
        node = case.find(tag)
        if node is not None:
            msg = node.get("message") or (node.text or "")
            return status, redact(msg.strip())
    return "passed", ""


def _case_id(case: ET.Element) -> str:
    """稳定的 attempt 标识：classname::name，缺 classname 时退化为 name。"""
    name = case.get("name") or "<unnamed>"
    cls = case.get("classname") or ""
    return "%s::%s" % (cls, name) if cls else name


def _iter_testcases(root: ET.Element):
    if root.tag == "testcase":
        yield root
        return
    for case in root.iter("testcase"):
        yield case


def parse_junit(path: str):
    """解析一份 JUnit XML，返回 (attempts, error)。"""
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return None, "XML 解析失败: %s" % exc
    except OSError as exc:
        return None, "读取失败: %s" % exc

    attempts = []
    for case in _iter_testcases(tree.getroot()):
        status, message = _case_status(case)
        attempt = {
            "attempt_id": _case_id(case),
            "status": status,
            "duration_s": None,
            "source_file": os.path.basename(path),
        }
        raw_time = case.get("time")
        if raw_time:
            try:
                attempt["duration_s"] = float(raw_time)
            except ValueError:
                attempt["duration_s"] = None
        if message:
            attempt["message"] = message
        attempts.append(attempt)
    return attempts, None


def collect_raw_files(raw_dirs) -> list:
    files = []
    for spec in raw_dirs:
        if os.path.isdir(spec):
            files.extend(sorted(glob.glob(os.path.join(spec, "**", "*.xml"),
                                          recursive=True)))
        else:
            files.extend(sorted(glob.glob(spec)))
    # 去重且保持顺序
    seen = set()
    unique = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


def build(raw_files, binding: dict) -> dict:
    attempts = []
    sources = []
    parse_errors = []

    for path in raw_files:
        parsed, err = parse_junit(path)
        entry = {"path": path, "sha256": None, "attempts": 0, "error": None}
        try:
            entry["sha256"] = sha256_file(path)
        except OSError as exc:
            entry["error"] = "哈希失败: %s" % exc
        if err:
            entry["error"] = err
            parse_errors.append("%s: %s" % (path, err))
        else:
            entry["attempts"] = len(parsed)
            attempts.extend(parsed)
        sources.append(entry)

    counts = {}
    for a in attempts:
        counts[a["status"]] = counts.get(a["status"], 0) + 1

    run = {
        "schema": "qa-run/v1",
        "generated_by": "scripts/rebuild_run_json.py",
        "generated_from": "framework-native JUnit XML (machine output)",
        "run_id": binding["run_id"],
        "version_binding": {
            "candidate_sha": binding["candidate_sha"],
            "target_sha": binding["target_sha"],
            "policy_version": binding["policy_version"],
            "baseline_version": binding["baseline_version"],
            "baseline_hash": binding["baseline_hash"],
        },
        "sources": sources,
        "attempts": attempts,
        "counts": counts,
        # 有任何原始文件解析不了 → 证据不完整。下游必须据此判 INCOMPLETE，
        # 不能因为"已解析的部分都 passed"就放行。
        "incomplete": bool(parse_errors) or not attempts,
        "incomplete_reasons": parse_errors + ([] if attempts else ["NO_ATTEMPTS"]),
        "notes": [
            "本文件由 CI 从机器产出的原始报告重建；候选分支工作副本中的同名文件不得用于门禁。",
            "skipped 不计为 failed；三态聚合规则在 trace_matrix / gate_check。",
            "message 字段已做敏感信息脱敏并截断，完整内容见 sources 指向的原始产物。",
        ],
    }
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", action="append", default=[],
                    help="原始报告目录或 glob，可多次给出")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--candidate-sha", default=None)
    ap.add_argument("--target-sha", default=None)
    ap.add_argument("--policy-version", default=None)
    ap.add_argument("--baseline-version", default=None)
    ap.add_argument("--baseline-hash", default=None)
    args = ap.parse_args(argv)

    required = {
        "run_id": args.run_id,
        "candidate_sha": args.candidate_sha,
        "target_sha": args.target_sha,
        "policy_version": args.policy_version,
        "baseline_version": args.baseline_version,
        "baseline_hash": args.baseline_hash,
    }
    missing = sorted(k for k, v in required.items() if not v)
    if missing:
        # 不填空字符串：gate_check 侧已实测过"字段缺失反而跳过核对"这类误放行
        print("USAGE_ERROR: 缺少版本绑定字段 %s（拒绝生成，不填空值）" % missing,
              file=sys.stderr)
        return EX_USAGE
    if not args.raw:
        print("USAGE_ERROR: 至少需要一个 --raw", file=sys.stderr)
        return EX_USAGE

    raw_files = collect_raw_files(args.raw)
    if not raw_files:
        print("DATA_ERROR: 在 %s 下找不到任何原始报告（*.xml）。"
              "产物缺失本身就是证据不完整，请让门禁判 INCOMPLETE，"
              "不要用本地副本补位。" % args.raw, file=sys.stderr)
        return EX_DATAERR

    run = build(raw_files, required)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(run, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print("写入 %s" % args.out)
    print("原始报告 %d 份，attempt %d 条，计数 %s"
          % (len(raw_files), len(run["attempts"]), run["counts"]))
    if run["incomplete"]:
        print("INCOMPLETE: %s" % run["incomplete_reasons"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

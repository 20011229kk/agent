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

输出必须符合下游既有契约（第一版没做到，这里写清楚）
--------------------------------------------------
第一版输出 `attempts[].attempt_id` 与嵌套的 `version_binding{...}`，而
`trace_matrix.load_runs` 读的是 `attempts[].node_id`、`gate_check.check_run_integrity`
读的是**顶层** `candidate_sha` / `baseline_version` / `baseline_hash`。结果是：
所有 attempt 被判 `ATTEMPT_ENTRY_INVALID` 跳过、绑定字段全部报 ABSENT，门禁虽然
判成 INCOMPLETE（没有误放行），但证据链**根本没接通**。本版按真实契约输出：

| 字段 | 消费者 |
|---|---|
| `attempts[].node_id` / `attempt` / `status` / `params` | `trace_matrix.load_runs` |
| 顶层 `candidate_sha` / `target_sha` / `policy_version` | `gate_check.check_run_integrity` |
| 顶层 `baseline_version` / `baseline_hash` | `trace_matrix` 的 STALE 与版本交叉核对 |
| `interrupted` | `gate_check`：证据不完整不得判通过 |
| `config` | `gate_check` 的凭据泄露检查（必须已脱敏） |

`baseline_hash` 的算法**复用** `trace_matrix.compute_baseline_hash`，不另写一份：
两份实现必然漂移，而这正是"有真实基线也对不上"的来源。

node_id 映射不是改字段名就完事
------------------------------
JUnit 的 `classname::name` **不等于**框架的 collect node_id。pytest 的
`classname="tests.test_x"` + `name="test_y[1-2]"` 对应的 node_id 是
`tests/test_x.py::test_y`，参数 `1-2` 要拆到 `params`。所以映射策略必须显式选择
（`--node-id-strategy`），并且必须有 JUnit → rebuild → trace → gate 的**集成**测试
证明能对上；只测转换器自己的输出约定证明不了兼容性。

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
    # 整行吃掉 Authorization 的值。第一版写的是 `authorization:\s*\S+`，
    # 只吃掉了授权方案（Bearer），**凭据值原样留在输出里**；而当时的测试断言的是
    # "Bearer zzz" 这个整串不出现，Bearer 被删掉就算过 —— 又一次判据比被测对象弱。
    (re.compile(r"(?i)authorization\s*[:=]\s*.*"), "authorization: <redacted>"),
    # 裸的 Bearer/Basic 凭据（不一定跟在 Authorization 后面）
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]+"), r"\1 <redacted>"),
    (re.compile(r"(?i)(cookie)\s*[:=]\s*.*"), r"\1: <redacted>"),
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


PARAM_RE = re.compile(r"^(?P<base>.+?)\[(?P<params>.*)\]$")


def split_params(name: str):
    """把 pytest 风格的参数化名拆成 (base, params)。

    `test_add[1-2]` → ("test_add", "1-2")。trace_matrix 按 node_id|params 归组，
    不拆的话同一个测试的每个参数都会变成不同的 node_id，与 collect 清单对不上。
    """
    m = PARAM_RE.match(name)
    if not m:
        return name, None
    return m.group("base"), m.group("params")


def map_node_id(classname: str, name: str, strategy: str, mapping: dict = None):
    """把 JUnit 的 (classname, name) 映射为框架的 collect node_id。

    返回 (node_id, params, note)。note 非空表示映射存在不确定性，应记入报告。

    strategy:
      pytest —— classname 的点号转路径 + `.py::`，类名段保留为 `::Class`；
               参数化后缀拆到 params。例：
                 tests.test_x / test_y[1-2]        → tests/test_x.py::test_y, params=1-2
                 tests.test_x.TestC / test_y       → tests/test_x.py::TestC::test_y
      raw    —— 原样 `classname::name`，不做任何推断（适合已经写入真实 node_id 的框架）
      map    —— 只认 --node-id-map 里的显式映射，命中不到就报错而不是猜
    """
    mapping = mapping or {}
    raw_key = "%s::%s" % (classname, name) if classname else name
    if raw_key in mapping:
        # 返回**拆分后的 base**。第一版算了 base 却返回原值，映射到
        # `...::test_ok[one]` 时输出的 node_id 带着 `[one]` 后缀、同时又给了 params=one，
        # 而 collect 是按"无后缀 node_id + params"匹配的 —— 整链直接判
        # REQUIRED_CASE_NOT_EXECUTED。
        base, params = split_params(mapping[raw_key])
        return base, params, None

    if strategy == "map":
        return None, None, "NODE_ID_UNMAPPED: %s" % raw_key

    base_name, params = split_params(name)

    if strategy == "raw":
        node = "%s::%s" % (classname, base_name) if classname else base_name
        return node, params, None

    # strategy == "pytest"
    if not classname:
        return base_name, params, "NO_CLASSNAME: 无法推断文件路径，按名字原样使用"
    parts = classname.split(".")
    # 约定：以大写字母开头的段视为类名（pytest 的 Test 类惯例）
    cls_idx = next((i for i, p in enumerate(parts) if p[:1].isupper()), len(parts))
    module_parts, class_parts = parts[:cls_idx], parts[cls_idx:]
    if not module_parts:
        return raw_key, params, "CLASSNAME_WITHOUT_MODULE: %s" % classname
    node = "/".join(module_parts) + ".py"
    for c in class_parts:
        node += "::" + c
    node += "::" + base_name
    return node, params, None


def _iter_testcases(root: ET.Element):
    if root.tag == "testcase":
        yield root
        return
    for case in root.iter("testcase"):
        yield case


def parse_junit(path: str, strategy: str = "pytest", mapping: dict = None):
    """解析一份 JUnit XML，返回 (attempts, error, notes)。"""
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return None, "XML 解析失败: %s" % exc, []
    except OSError as exc:
        return None, "读取失败: %s" % exc, []

    attempts = []
    notes = []
    for case in _iter_testcases(tree.getroot()):
        status, message = _case_status(case)
        classname = case.get("classname") or ""
        name = case.get("name") or "<unnamed>"
        node_id, params, note = map_node_id(classname, name, strategy, mapping)
        if note:
            notes.append("%s (%s)" % (note, os.path.basename(path)))
        if node_id is None:
            # 映射不出 node_id 的记录不能悄悄丢：下游会因此少算执行范围
            continue

        # attempt 序号在 build() 里跨全部文件统一分配：
        # 在单个文件里分配会让同一 node_id 出现在 first.xml 与 second.xml 时都变成
        # attempt=1，"先失败后通过"的判定就没有顺序依据了。
        attempt = {
            "node_id": node_id,
            "attempt": None,
            "status": status,
            "duration_s": None,
            "source_file": os.path.basename(path),
        }
        if params is not None:
            attempt["params"] = params
        raw_time = case.get("time")
        if raw_time:
            try:
                attempt["duration_s"] = float(raw_time)
            except ValueError:
                attempt["duration_s"] = None
        if message:
            attempt["message"] = message
        attempts.append(attempt)
    return attempts, None, notes


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


def build(raw_files, binding: dict, strategy: str = "pytest",
          mapping: dict = None, retry_order: str = "none") -> dict:
    attempts = []
    sources = []
    parse_errors = []
    mapping_notes = []

    for path in raw_files:
        parsed, err, notes = parse_junit(path, strategy, mapping)
        mapping_notes.extend(notes)
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
            if not parsed:
                # 合法但空的报告（零个 testcase）不能静默忽略：分片没产出结果、
                # collect 崩了、或者报告写到了别处，都是证据缺口。
                # 只在"聚合后一条 attempt 都没有"时才报 NO_ATTEMPTS 是不够的 ——
                # 一份好报告 + 一份空报告会因此被判成完整（集成测试逮到的）。
                parse_errors.append("EMPTY_REPORT: %s（零个 testcase）" % path)
        sources.append(entry)

    # ---- attempt 序号：跨文件统一分配，且**不猜顺序** ----
    #
    # JUnit 没有标准的"第几次尝试"字段。同一 node_id|params 出现多次，可能是重试，
    # 也可能是分片重复或不同环境跑了同一个测试。把计数器提到全局只解决了"都变成 1"
    # 这个表象，解决不了"哪次在前"这个问题 —— 而"先失败后通过不得自动抹掉阻断"
    # 恰恰依赖顺序。
    #
    # 所以默认 retry_order="none"：出现重复即记为证据不完整，由人或适配器补顺序依据。
    # 只有调用方显式声明 retry_order="document-order"（即该框架/适配器保证 XML 文档序
    # 等于执行序、且 --raw 的传入顺序等于执行顺序）时才分配序号，并把这个假设写进报告。
    grouped = {}
    for a in attempts:
        key = "%s|%s" % (a["node_id"], a.get("params") or "")
        grouped.setdefault(key, []).append(a)

    duplicates = sorted(k for k, v in grouped.items() if len(v) > 1)
    for key, seq in grouped.items():
        ordered = len(seq) == 1 or retry_order == "document-order"
        for i, a in enumerate(seq, start=1):
            # 顺序未知时**不给序号**（null），而不是都写 1。
            # 都写 1 会让下游继续拿"列表最后一条"当末次结果：同一组 failed/passed，
            # 仅交换输入排列，last_result 就从 true 变 false、门禁从 INCOMPLETE 变 FAIL。
            # 判定不能由文件排列决定；null 让消费者能识别"顺序未知"这个状态。
            a["attempt"] = i if ordered else None
            if not ordered:
                a["order_known"] = False

    counts = {}
    for a in attempts:
        counts[a["status"]] = counts.get(a["status"], 0) + 1

    unmapped = [n for n in mapping_notes if n.startswith("NODE_ID_UNMAPPED")]
    incomplete_reasons = list(parse_errors)
    if duplicates and retry_order != "document-order":
        incomplete_reasons.append(
            "DUPLICATE_ATTEMPTS_WITHOUT_ORDER_EVIDENCE: %s —— 同一 node_id|params 出现多次"
            "（重试？分片重复？不同环境？），JUnit 没有尝试序号，顺序无从确定。"
            "需要框架的尝试序号/时间戳/运行来源，或用 --retry-order document-order "
            "显式声明适配器保证文档序等于执行序" % duplicates)
    if not attempts:
        incomplete_reasons.append("NO_ATTEMPTS")
    incomplete_reasons.extend(unmapped)
    incomplete = bool(incomplete_reasons)

    run = {
        "schema": "qa-run/v1",
        "generated_by": "scripts/rebuild_run_json.py",
        "generated_from": "framework-native JUnit XML (machine output)",
        "run_id": binding["run_id"],
        # ---- 顶层绑定字段：gate_check / trace_matrix 读的就是这一层 ----
        "candidate_sha": binding["candidate_sha"],
        "target_sha": binding["target_sha"],
        "policy_version": binding["policy_version"],
        "baseline_version": binding["baseline_version"],
        "baseline_hash": binding["baseline_hash"],
        # `interrupted` 是既有消费者用来判"证据不完整不得通过"的字段。
        # 只写自造的 incomplete 字段没有任何消费者，等于没做完整性控制。
        "interrupted": incomplete,
        "incomplete": incomplete,
        "incomplete_reasons": incomplete_reasons,
        # gate_check 会扫 config 找凭据痕迹；这里显式给空对象，
        # 真实配置标识应由执行层写入且必须已脱敏。
        "config": {},
        "node_id_strategy": strategy,
        "retry_order": retry_order,
        "retry_order_assumption": (
            "调用方声明：原始报告的文档序与 --raw 传入顺序等于执行顺序"
            if retry_order == "document-order" else
            "未声明顺序依据；重复记录不分配可信序号"),
        "duplicate_keys": duplicates,
        "mapping_notes": mapping_notes,
        "sources": sources,
        "attempts": attempts,
        "counts": counts,
        "notes": [
            "本文件由 CI 从机器产出的原始报告重建；候选分支工作副本中的同名文件不得用于门禁。",
            "skipped 不计为 failed；三态聚合规则在 trace_matrix / gate_check。",
            "message 字段已做敏感信息脱敏并截断，完整内容见 sources 指向的原始产物。",
            "node_id 由 --node-id-strategy 映射得到，JUnit 的 classname::name 不天然等于"
            "框架 collect 的 node_id；映射不确定处记在 mapping_notes。",
        ],
    }
    return run


def derive_baseline_binding(baseline_file: str):
    """从**受保护版本**的 requirements.yaml 取 baseline_version 与 baseline_hash。

    刻意复用 `trace_matrix.compute_baseline_hash`：第一版在 CI 里用 git tree SHA 当
    baseline_version、用"文件哈希清单的哈希"当 baseline_hash，与消费者使用的
    `meta.baseline_version` 和规范化内容 `sha256:...` 是两套契约，有真实基线也对不上。
    算法只能有一份实现，否则一定漂移。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import trace_matrix  # noqa: WPS433 - 故意复用同一实现
        import yaml
    except ImportError as exc:
        return None, None, "缺少依赖: %s" % exc

    if not os.path.isfile(baseline_file):
        return None, None, "基线文件不存在: %s" % baseline_file
    try:
        with open(baseline_file, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # yaml 错误类型较杂，统一兜住
        return None, None, "基线解析失败: %s" % exc

    meta = data.get("meta") or {}
    version = meta.get("baseline_version")
    if not version:
        return None, None, "基线缺少 meta.baseline_version"
    return str(version), trace_matrix.compute_baseline_hash(data), None


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
    ap.add_argument("--baseline-file", default=None,
                    help="受保护版本的 requirements.yaml；由它推出 baseline_version 与 "
                         "baseline_hash（复用 trace_matrix 的算法，避免两套契约）")
    ap.add_argument("--node-id-strategy", default="pytest",
                    choices=["pytest", "raw", "map"])
    ap.add_argument("--node-id-map", default=None,
                    help="JSON 文件：{\"classname::name\": \"真实 node_id\"}")
    ap.add_argument("--retry-order", default="none",
                    choices=["none", "document-order"],
                    help="重复记录的顺序依据。none（默认）= 无依据，出现重复即记为证据"
                         "不完整；document-order = 调用方声明适配器保证文档序与 --raw "
                         "传入顺序等于执行顺序")
    args = ap.parse_args(argv)

    if args.baseline_file:
        if args.baseline_version or args.baseline_hash:
            print("USAGE_ERROR: --baseline-file 与 --baseline-version/--baseline-hash "
                  "不能同时给出（避免两个来源不一致时无法判断哪个生效）",
                  file=sys.stderr)
            return EX_USAGE
        version, bhash, err = derive_baseline_binding(args.baseline_file)
        if err:
            print("DATA_ERROR: %s" % err, file=sys.stderr)
            return EX_DATAERR
        args.baseline_version = version
        args.baseline_hash = bhash

    mapping = {}
    if args.node_id_map:
        if not os.path.isfile(args.node_id_map):
            print("DATA_ERROR: node-id-map 不存在: %s" % args.node_id_map,
                  file=sys.stderr)
            return EX_DATAERR
        try:
            with open(args.node_id_map) as fh:
                mapping = json.load(fh)
        except ValueError as exc:
            print("DATA_ERROR: node-id-map 解析失败: %s" % exc, file=sys.stderr)
            return EX_DATAERR
        if not isinstance(mapping, dict):
            print("DATA_ERROR: node-id-map 顶层必须是对象", file=sys.stderr)
            return EX_DATAERR

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

    run = build(raw_files, required, args.node_id_strategy, mapping,
                args.retry_order)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(run, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print("写入 %s" % args.out)
    print("原始报告 %d 份，attempt %d 条，计数 %s"
          % (len(raw_files), len(run["attempts"]), run["counts"]))
    print("node_id 策略: %s" % args.node_id_strategy)
    if run["mapping_notes"]:
        print("映射提示 %d 条（前 5 条）: %s"
              % (len(run["mapping_notes"]), run["mapping_notes"][:5]))
    if run["incomplete"]:
        # interrupted 也一并置真，下游据此判 INCOMPLETE
        print("INCOMPLETE: %s" % run["incomplete_reasons"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

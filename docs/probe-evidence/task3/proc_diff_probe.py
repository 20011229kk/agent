#!/usr/bin/env python3
"""Task 3 probe: does an agent file write create a new OS process?

Why this probe
--------------
Task 3 has to decide where OS-level isolation can attach. That depends on
whether the IDE spawns a separate process per agent / per delegated subagent, or
whether every agent's file operations are performed by one long-lived host
process. An OS sandbox can only be scoped to a process boundary that exists.

Method
------
Sample the full process table on a fixed interval while agent activity happens,
then diff: any PID absent from the first sample and present later is a process
created during the window. `lstart` is recorded so a recycled PID cannot be
mistaken for a survivor.

This probe observes process creation only. It does not attribute a write to a
PID - see task3-findings.md for why that needs kernel tracing (root).

Usage
-----
  python3 proc_diff_probe.py start  [--dir DIR] [--interval S] [--max-seconds S]
  python3 proc_diff_probe.py sample --dir DIR            # (internal) sampler
  python3 proc_diff_probe.py mark   --dir DIR --label L  # annotate the timeline
  python3 proc_diff_probe.py stop   --dir DIR
  python3 proc_diff_probe.py report --dir DIR
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

PS_ARGS = ["/bin/ps", "-Ao", "pid=,ppid=,user=,lstart=,comm="]
PS_ARGS_FULL = ["/bin/ps", "-Ao", "pid=,ppid=,user=,lstart=,command="]
DEFAULT_INTERVAL = 0.5
DEFAULT_MAX_SECONDS = 600.0


def _paths(base: str) -> dict:
    return {
        "dir": base,
        "meta": os.path.join(base, "meta.json"),
        "samples": os.path.join(base, "samples.jsonl"),
        "marks": os.path.join(base, "marks.jsonl"),
        "stop": os.path.join(base, "STOP"),
        "log": os.path.join(base, "sampler.log"),
    }


def _snapshot(with_args: bool = False) -> dict:
    out = subprocess.run(
        PS_ARGS_FULL if with_args else PS_ARGS,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    procs = {}
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, ppid, user, rest = parts
        # lstart is a fixed 5-field format: "Sun Sep 21 11:12:22 2026"
        rest_parts = rest.split(None, 5)
        if len(rest_parts) < 6:
            continue
        lstart = " ".join(rest_parts[:5])
        comm = rest_parts[5]
        procs[pid] = {"ppid": ppid, "user": user, "lstart": lstart, "comm": comm}
    return procs


def cmd_start(base: str, interval: float, max_seconds: float,
              with_args: bool = False) -> int:
    p = _paths(base)
    if os.path.isdir(base):
        shutil.rmtree(base)
    os.makedirs(base, mode=0o755)
    meta = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "interval_s": interval,
        "max_seconds": max_seconds,
        "ps_args": PS_ARGS_FULL if with_args else PS_ARGS,
        "with_args": with_args,
    }
    with open(p["meta"], "w") as fh:
        json.dump(meta, fh, indent=2)
    log = open(p["log"], "w")
    child = subprocess.Popen(
        [
            sys.executable,
            os.path.abspath(__file__),
            "sample",
            "--dir",
            base,
            "--interval",
            str(interval),
            "--max-seconds",
            str(max_seconds),
        ] + (["--with-args"] if with_args else []),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    meta["sampler_pid"] = child.pid
    with open(p["meta"], "w") as fh:
        json.dump(meta, fh, indent=2)
    print("sampler pid %d, interval %.2fs, stops after %.0fs or on STOP file"
          % (child.pid, interval, max_seconds))
    return 0


def cmd_sample(base: str, interval: float, max_seconds: float,
               with_args: bool = False) -> int:
    p = _paths(base)
    started = time.time()
    n = 0
    with open(p["samples"], "w") as fh:
        while True:
            rec = {
                "i": n,
                "t": round(time.time() - started, 3),
                "wall": time.strftime("%H:%M:%S"),
                "procs": _snapshot(with_args),
            }
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            n += 1
            if os.path.exists(p["stop"]):
                break
            if time.time() - started >= max_seconds:
                break
            # 按固定节拍对齐，而不是"干完活再 sleep(interval)"。
            # 旧写法把 ps 调用与写盘时间叠加在间隔之外：设 0.1s，实测相邻间隔约 150ms
            # （外部复核用 148 次 / 22.1 秒算出来的）。写"0.1s 间隔"就成了虚报。
            #
            # 落后于节拍时**跳过已错过的节拍**，不要连续补采：第一版会在落后后疯狂追赶，
            # 实测出现 p50=0.029s（空转）与 max=54.3s（长时间卡顿）并存的间隔分布，
            # 既浪费 IO 又让"平均间隔"更加没有意义。
            now = time.time()
            elapsed = now - started
            ticks_done = int(elapsed / interval) + 1
            next_tick = started + ticks_done * interval
            if next_tick > now:
                time.sleep(next_tick - now)
    print("samples=%d elapsed=%.1fs" % (n, time.time() - started))
    return 0


def cmd_mark(base: str, label: str) -> int:
    p = _paths(base)
    with open(p["marks"], "a") as fh:
        fh.write(json.dumps({
            "label": label,
            "wall": time.strftime("%H:%M:%S"),
            "epoch": time.time(),
        }) + "\n")
    print("marked %s" % label)
    return 0


def cmd_stop(base: str) -> int:
    p = _paths(base)
    with open(p["stop"], "w") as fh:
        fh.write(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    print("stop requested")
    return 0


def _load(path: str) -> list:
    recs = []
    if not os.path.exists(path):
        return recs
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def cmd_report(base: str, name_filter: str, expect_pid: str) -> int:
    p = _paths(base)
    samples = _load(p["samples"])
    if not samples:
        print("NO_SAMPLES")
        return 1
    marks = _load(p["marks"])
    sampler_pid = None
    if os.path.exists(p["meta"]):
        with open(p["meta"]) as fh:
            sampler_pid = json.load(fh).get("sampler_pid")
    sampler_pid = str(sampler_pid) if sampler_pid is not None else None

    print("--- window ---")
    print("samples: %d  span: %.1fs  (%s .. %s)"
          % (len(samples), samples[-1]["t"], samples[0]["wall"], samples[-1]["wall"]))

    # 实际相邻间隔必须公布。设定值不等于实测值：外部复核正是用 148 次 / 22.1 秒
    # 反推出实际约 150ms，推翻了"0.1s 间隔"的表述。最大间隙决定了能漏掉多长的进程。
    gaps = [round(samples[i + 1]["t"] - samples[i]["t"], 3)
            for i in range(len(samples) - 1)]
    if gaps:
        ordered = sorted(gaps)
        def pct(q):
            return ordered[min(len(ordered) - 1, int(q * len(ordered)))]
        print("--- 实际采样间隔（秒）---")
        print("min=%.3f  p50=%.3f  p95=%.3f  max=%.3f  mean=%.3f"
              % (ordered[0], pct(0.5), pct(0.95), ordered[-1],
                 sum(gaps) / len(gaps)))
        print("注意：能被可靠观测的进程寿命下限由 **max 间隙** 决定，不是由设定间隔决定；"
              "短于 max 间隙的进程可能整段落在两次快照之间。")
    if marks:
        print("--- marks ---")
        for m in marks:
            print("%s  %s" % (m["wall"], m["label"]))

    baseline = samples[0]["procs"]
    first_seen = {}
    last_seen = {}
    info = {}
    for rec in samples:
        for pid, meta in rec["procs"].items():
            key = (pid, meta["lstart"])
            if key not in first_seen:
                first_seen[key] = rec
            last_seen[key] = rec
            info[key] = meta

    created = [
        k for k in first_seen
        if not (k[0] in baseline and baseline[k[0]]["lstart"] == k[1])
    ]
    gone = [
        (pid, meta["lstart"]) for pid, meta in baseline.items()
        if last_seen.get((pid, meta["lstart"]), samples[-1])["i"] != samples[-1]["i"]
    ]

    # The sampler shells out to /bin/ps once per sample; those children are
    # measurement artefacts, not observations. Drop them but say how many.
    own_noise = [
        k for k in created
        if sampler_pid is not None and info[k]["ppid"] == sampler_pid
    ]
    if own_noise:
        created = [k for k in created if k not in own_noise]
        print("--- excluded %d sampler-owned /bin/ps children (ppid=%s) ---"
              % (len(own_noise), sampler_pid))

    print("--- processes created during the window: %d ---" % len(created))
    created.sort(key=lambda k: first_seen[k]["t"])
    for key in created:
        pid, lstart = key
        meta = info[key]
        ppid = meta["ppid"]
        parent = info.get((ppid, None))
        parent_comm = baseline.get(ppid, {}).get("comm")
        if parent_comm is None:
            for k2, m2 in info.items():
                if k2[0] == ppid:
                    parent_comm = m2["comm"]
                    break
        print("t=%7.2fs %s  pid=%s ppid=%s (%s)  %s"
              % (first_seen[key]["t"], first_seen[key]["wall"], pid, ppid,
                 parent_comm or "?", meta["comm"]))

    if name_filter:
        print("--- created, filtered by %r ---" % name_filter)
        hit = False
        for key in created:
            if name_filter.lower() in info[key]["comm"].lower():
                hit = True
                print("t=%7.2fs pid=%s %s" % (first_seen[key]["t"], key[0],
                                              info[key]["comm"]))
        if not hit:
            print("NO_MATCHING_PROCESS_CREATED")

    print("--- baseline processes that exited during the window: %d ---" % len(gone))
    for pid, lstart in gone:
        print("pid=%s %s" % (pid, baseline[pid]["comm"]))

    rc = 0
    if expect_pid:
        hits = [k for k in created if k[0] == expect_pid]
        seen_any = [k for k in first_seen if k[0] == expect_pid]
        if hits:
            print("CONTROL_PASS: expected pid %s captured as created (comm=%s)"
                  % (expect_pid, info[hits[0]]["comm"]))
        elif seen_any:
            print("CONTROL_FAIL: pid %s seen but classified as baseline, not created"
                  % expect_pid)
            rc = 2
        else:
            print("CONTROL_FAIL: pid %s never appeared in any sample" % expect_pid)
            rc = 2
    return rc


KIRO_HINT = "kiro"


def _kiro_subset(procs: dict) -> dict:
    """取 Kiro 进程子树：命令行含 kiro 的进程，以及其祖先链可达这些进程的进程。

    结论只声称"Kiro 树中有没有新建进程"，所以复核这条结论需要的正是这棵子树。
    """
    direct = {pid for pid, m in procs.items()
              if KIRO_HINT in (m.get("comm") or "").lower()}
    keep = set(direct)
    # 向下收：父进程在 keep 里的也收进来（多轮传播，进程表无环）
    changed = True
    while changed:
        changed = False
        for pid, m in procs.items():
            if pid in keep:
                continue
            if m.get("ppid") in keep:
                keep.add(pid)
                changed = True
    return {pid: procs[pid] for pid in sorted(keep)}


def cmd_reduce(base: str, dest: str) -> int:
    """产出**可复核的降采样归档**，替代动辄上百 MB 的完整快照。

    为什么需要：一次 866 次快照（带完整命令行）的 samples.jsonl 实测 141MB，
    gzip 后仍有 17.8MB，放进 git 不合适。但完全不归档又会让结论不可复核
    （上一轮正是这样：原始数据留在 /tmp 后被清理）。

    折中办法是**只减少体积，不减少可核对性**：
      - 每次快照的 t / wall 全部保留 → 间隙分布、max 间隙可逐条复核
      - Kiro 进程子树全部保留 → "Kiro 树中有没有新建进程"这条结论可复核
      - 每次快照完整进程表的 sha256 全部保留 → **在原件仍存在时**可校验对应关系
      - 完整文件的 sha256 与字节数记录在 manifest 里 → 与本地留存件比对

    能力边界（别说过头）：哈希本身**不能**证明"没有挑样本"。原件一旦删除，只剩哈希既无法
    重建完整进程表，也无法复核被裁掉的内容。准确叫法是"保留所有快照时间点的字段裁剪归档"；
    需要长期独立复核时，原始压缩件应放入 Git 之外的受控 artifact 存储。
    """
    p = _paths(base)
    if not os.path.exists(p["samples"]):
        print("NO_SAMPLES: %s" % p["samples"])
        return 1
    os.makedirs(dest, exist_ok=True)

    full_hash = hashlib.sha256()
    with open(p["samples"], "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            full_hash.update(chunk)

    # 命令行字符串做字典表去重：Kiro 的 Electron 命令行单条约 2KB，每次快照都会重复
    # 一遍，866 次快照就是几十 MB 的同样内容。存 id + 一张表，内容一字不改。
    comm_table = {}

    def comm_id(text: str) -> str:
        key = hashlib.sha256((text or "").encode()).hexdigest()[:12]
        comm_table.setdefault(key, text)
        return key

    out_path = os.path.join(dest, "samples-reduced.jsonl")
    kept = 0
    total_procs = 0
    with open(p["samples"]) as fin, open(out_path, "w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            procs = rec.get("procs", {})
            total_procs += len(procs)
            digest = hashlib.sha256(
                json.dumps(procs, sort_keys=True).encode()).hexdigest()
            tree = {}
            for pid, meta in _kiro_subset(procs).items():
                tree[pid] = {
                    "ppid": meta.get("ppid"),
                    "lstart": meta.get("lstart"),
                    "comm_id": comm_id(meta.get("comm")),
                }
            fout.write(json.dumps({
                "i": rec.get("i"),
                "t": rec.get("t"),
                "wall": rec.get("wall"),
                "n_procs": len(procs),
                "procs_sha256": digest,
                "kiro_tree": tree,
            }) + "\n")
            kept += 1

    with open(os.path.join(dest, "comm-table.json"), "w") as fh:
        json.dump(comm_table, fh, indent=2, ensure_ascii=False)

    for key in ("meta", "marks"):
        if os.path.exists(p[key]):
            shutil.copyfile(p[key], os.path.join(dest, os.path.basename(p[key])))

    reduced_hash = hashlib.sha256()
    with open(out_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            reduced_hash.update(chunk)

    info = {
        "reduced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_dir": base,
        "samples": kept,
        "mean_procs_per_sample": round(total_procs / kept, 1) if kept else None,
        "full_samples_jsonl": {
            "sha256": full_hash.hexdigest(),
            "bytes": os.path.getsize(p["samples"]),
            "note": "完整快照未入库（体积过大）；需要时用本哈希与本地留存件比对",
        },
        "samples_reduced_jsonl": {
            "sha256": reduced_hash.hexdigest(),
            "bytes": os.path.getsize(out_path),
        },
        "retained_fields": ["i", "t", "wall", "n_procs", "procs_sha256",
                            "kiro_tree{ppid,lstart,comm_id}"],
        "comm_table": {"file": "comm-table.json", "entries": len(comm_table),
                       "note": "comm_id → 完整命令行原文，内容未截断"},
        "dropped": "非 Kiro 进程子树的逐进程明细（其存在性由 n_procs 与 procs_sha256 约束）",
        "limits": [
            "哈希只能在原件存在时校验对应关系，不能证明未挑样本，也不能重建被裁内容",
            "间隔是快照开始时刻之差，非原子采集，不保证捕获所有超过该间隔的进程",
            "kiro_tree 是按既定筛选规则产出，与写入 PID 归属无关",
        ],
    }
    with open(os.path.join(dest, "reduced-manifest.json"), "w") as fh:
        json.dump(info, fh, indent=2, ensure_ascii=False)

    print("降采样归档 %d 次快照到 %s" % (kept, dest))
    print("  samples-reduced.jsonl  %d bytes" % os.path.getsize(out_path))
    print("  完整文件 %d bytes  sha256=%s"
          % (info["full_samples_jsonl"]["bytes"], full_hash.hexdigest()[:16]))
    return 0


def cmd_archive(base: str, dest: str) -> int:
    """把原始采样数据连同哈希清单归档，使结论可被第三方逐间隙复核。

    上一轮只归档了 report 文本，samples.jsonl / meta.json 留在 /tmp 后被清理，
    外部复核因此无法核对任何一次间隙或进程树分类——结论变成不可复核。
    """
    p = _paths(base)
    if not os.path.exists(p["samples"]):
        print("NO_SAMPLES: %s" % p["samples"])
        return 1
    os.makedirs(dest, exist_ok=True)
    copied = []
    for key in ("meta", "marks", "samples"):
        src = p[key]
        if not os.path.exists(src):
            continue
        name = os.path.basename(src)
        # samples.jsonl 是整张进程表 × 每次快照，实测 866 次快照（带完整命令行）
        # 就有 141MB。原样入库不现实，gzip 后按行仍可逐间隙复核。
        if key == "samples":
            out = os.path.join(dest, name + ".gz")
            with open(src, "rb") as fin, gzip.open(out, "wb", compresslevel=9) as fout:
                shutil.copyfileobj(fin, fout)
        else:
            out = os.path.join(dest, name)
            shutil.copyfile(src, out)
        copied.append(out)

    manifest = {}
    for path in copied:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        entry = {
            "sha256": h.hexdigest(),
            "bytes": os.path.getsize(path),
        }
        if path.endswith(".gz"):
            src = os.path.join(base, os.path.basename(path)[:-3])
            if os.path.exists(src):
                entry["uncompressed_bytes"] = os.path.getsize(src)
        manifest[os.path.basename(path)] = entry
    with open(os.path.join(dest, "raw-manifest.json"), "w") as fh:
        json.dump({"archived_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "source_dir": base,
                   "files": manifest}, fh, indent=2)
    print("归档 %d 个文件到 %s" % (len(copied), dest))
    for name, meta in manifest.items():
        print("  %s  %d bytes  %s" % (name, meta["bytes"], meta["sha256"][:16]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["start", "sample", "mark", "stop", "report",
                                     "archive", "reduce"])
    ap.add_argument("--dir", default="/tmp/kiro-proc-probe")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    ap.add_argument("--label", default="")
    ap.add_argument("--filter", default="")
    ap.add_argument("--expect-pid", default="")
    ap.add_argument("--with-args", action="store_true",
                    help="record the full command line instead of just comm")
    ap.add_argument("--dest", default="", help="archive 模式的归档目标目录")
    args = ap.parse_args(argv)
    if args.mode == "start":
        return cmd_start(args.dir, args.interval, args.max_seconds, args.with_args)
    if args.mode == "sample":
        return cmd_sample(args.dir, args.interval, args.max_seconds, args.with_args)
    if args.mode == "mark":
        return cmd_mark(args.dir, args.label)
    if args.mode == "stop":
        return cmd_stop(args.dir)
    if args.mode in ("archive", "reduce"):
        if not args.dest:
            print("FATAL: %s 需要 --dest" % args.mode)
            return 64
        return (cmd_archive if args.mode == "archive" else cmd_reduce)(
            args.dir, args.dest)
    return cmd_report(args.dir, args.filter, args.expect_pid)


if __name__ == "__main__":
    raise SystemExit(main())

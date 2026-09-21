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
            time.sleep(interval)
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["start", "sample", "mark", "stop", "report"])
    ap.add_argument("--dir", default="/tmp/kiro-proc-probe")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    ap.add_argument("--label", default="")
    ap.add_argument("--filter", default="")
    ap.add_argument("--expect-pid", default="")
    ap.add_argument("--with-args", action="store_true",
                    help="record the full command line instead of just comm")
    args = ap.parse_args(argv)
    if args.mode == "start":
        return cmd_start(args.dir, args.interval, args.max_seconds, args.with_args)
    if args.mode == "sample":
        return cmd_sample(args.dir, args.interval, args.max_seconds, args.with_args)
    if args.mode == "mark":
        return cmd_mark(args.dir, args.label)
    if args.mode == "stop":
        return cmd_stop(args.dir)
    return cmd_report(args.dir, args.filter, args.expect_pid)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Task 3 probe: identify which OS process actually performs an agent fs_write.

Method
------
A FIFO (named pipe) blocks `open(path, O_WRONLY)` until a reader appears.
So if the agent is told to write to a FIFO, whatever process implements the
write will sit blocked in `open()` for as long as we withhold the reader.
While it is blocked, `lsof <fifo>` lists that process by PID/command.

This gives *external* evidence of the writing process identity, independent of
anything the model says about itself.

Usage
-----
  python3 fifo_writer_probe.py setup   [--dir DIR]  # create FIFO + start watcher
  python3 fifo_writer_probe.py control --dir DIR    # control group: known writer
  python3 fifo_writer_probe.py watch   --dir DIR    # (internal) sampler + reader
  python3 fifo_writer_probe.py writer  --dir DIR    # (internal) control writer
  python3 fifo_writer_probe.py report  --dir DIR    # summarise collected samples

The `control` mode exists to validate the harness itself: it starts a writer
whose PID we already know, so a run that fails to capture *that* writer tells us
the measurement is broken rather than telling us anything about the agent.

Outcomes
--------
* FIFO still a FIFO afterwards and lsof captured a writer  -> writer PID known.
* Path became a regular file and no writer was captured    -> the tool wrote via
  a temp-file + rename path (FIFO replaced); blocking probe does not apply.
* No samples at all                                        -> setup/ordering bug.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import stat
import subprocess
import sys
import time

SAMPLE_INTERVAL = 0.25
WAIT_FOR_WRITER = 90.0   # keep sampling until a writer shows up, or give up
EXTRA_SAMPLES = 4        # samples taken after the writer is first seen
DRAIN_TIMEOUT = 120.0    # after sampling, stay readable this long (releases writer)


def _paths(base: str) -> dict:
    return {
        "dir": base,
        "pipe": os.path.join(base, "pipe"),
        "lsof": os.path.join(base, "lsof-samples.txt"),
        "ps": os.path.join(base, "ps-snapshot.txt"),
        "content": os.path.join(base, "content.txt"),
        "meta": os.path.join(base, "meta.json"),
        "done": os.path.join(base, "watcher-done.json"),
        "log": os.path.join(base, "watcher.log"),
        "control": os.path.join(base, "control-writer.json"),
    }


def cmd_setup(base: str) -> int:
    p = _paths(base)
    if os.path.isdir(base):
        shutil.rmtree(base)
    os.makedirs(base, mode=0o755)
    os.mkfifo(p["pipe"], 0o666)

    meta = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pipe": p["pipe"],
        "probe_pid": os.getpid(),
        "sample_interval_s": SAMPLE_INTERVAL,
        "wait_for_writer_s": WAIT_FOR_WRITER,
        "drain_timeout_s": DRAIN_TIMEOUT,
    }
    with open(p["meta"], "w") as fh:
        json.dump(meta, fh, indent=2)

    log = open(p["log"], "w")
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "watch", "--dir", base],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    meta["watcher_pid"] = child.pid
    with open(p["meta"], "w") as fh:
        json.dump(meta, fh, indent=2)

    print("FIFO ready: %s" % p["pipe"])
    print("watcher pid: %d" % child.pid)
    print(
        "waiting up to %.0fs for a writer; releases it ~%.1fs after it is seen"
        % (WAIT_FOR_WRITER, EXTRA_SAMPLES * SAMPLE_INTERVAL)
    )
    return 0


def cmd_control(base: str) -> int:
    """Start a writer whose PID is known up front (harness self-check)."""
    p = _paths(base)
    if not os.path.exists(p["pipe"]):
        print("NO_PIPE: run setup first")
        return 1
    log = open(os.path.join(base, "control-writer.log"), "w")
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "writer", "--dir", base],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    with open(p["control"], "w") as fh:
        json.dump(
            {
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "expected_writer_pid": child.pid,
                "expected_writer_comm": os.path.basename(sys.executable),
            },
            fh,
            indent=2,
        )
    print("control writer pid: %d" % child.pid)
    return 0


def cmd_writer(base: str) -> int:
    p = _paths(base)
    payload = b"CONTROL-WRITER-PID-%d\n" % os.getpid()
    fd = os.open(p["pipe"], os.O_WRONLY)  # blocks until the watcher reads
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    print("wrote %d bytes" % len(payload))
    return 0


def _lsof(pipe: str) -> str:
    try:
        out = subprocess.run(
            ["/usr/sbin/lsof", pipe],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
        return out.stdout.decode("utf-8", "replace")
    except Exception as exc:  # pragma: no cover - probe robustness
        return "LSOF_ERROR %s\n" % exc


def cmd_watch(base: str) -> int:
    p = _paths(base)
    started = time.time()

    with open(p["ps"], "w") as fh:
        snap = subprocess.run(
            ["/bin/ps", "-Ao", "pid,ppid,user,comm"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        fh.write(snap.stdout.decode("utf-8", "replace"))

    writer_seen = False
    remaining_after_hit = EXTRA_SAMPLES
    sample_deadline = started + WAIT_FOR_WRITER
    i = 0
    with open(p["lsof"], "w") as fh:
        while True:
            t = time.time() - started
            text = _lsof(p["pipe"])
            fh.write("=== sample %02d t=%.2fs ===\n%s" % (i, t, text))
            fh.flush()
            i += 1
            # data lines beyond the header mean somebody holds the FIFO open
            body = [ln for ln in text.splitlines()[1:] if ln.strip()]
            if body:
                writer_seen = True
            if writer_seen:
                remaining_after_hit -= 1
                if remaining_after_hit <= 0:
                    break
            elif time.time() >= sample_deadline:
                break
            time.sleep(SAMPLE_INTERVAL)

    # Release the writer and capture whatever it sent.
    drained = 0
    deadline = time.time() + DRAIN_TIMEOUT
    with open(p["content"], "wb") as out:
        while time.time() < deadline:
            try:
                fd = os.open(p["pipe"], os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                if exc.errno in (errno.ENOENT, errno.EINVAL):
                    break
                time.sleep(0.25)
                continue
            try:
                got_any = False
                idle_since = time.time()
                while time.time() < deadline:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EAGAIN:
                            if got_any and time.time() - idle_since > 2.0:
                                break
                            if not got_any and time.time() - idle_since > 10.0:
                                break
                            time.sleep(0.1)
                            continue
                        raise
                    if not chunk:
                        if got_any:
                            break
                        time.sleep(0.1)
                        continue
                    out.write(chunk)
                    out.flush()
                    drained += len(chunk)
                    got_any = True
                    idle_since = time.time()
                if got_any:
                    break
            finally:
                os.close(fd)

    st = None
    kind = "missing"
    if os.path.exists(p["pipe"]):
        st = os.stat(p["pipe"])
        kind = "fifo" if stat.S_ISFIFO(st.st_mode) else (
            "regular" if stat.S_ISREG(st.st_mode) else "other"
        )

    with open(p["done"], "w") as fh:
        json.dump(
            {
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "writer_seen_in_lsof": writer_seen,
                "samples_taken": i,
                "bytes_drained": drained,
                "pipe_kind_after": kind,
                "pipe_size_after": (st.st_size if st else None),
                "elapsed_s": round(time.time() - started, 2),
            },
            fh,
            indent=2,
        )
    return 0


def cmd_report(base: str) -> int:
    p = _paths(base)
    for key in ("meta", "control", "done"):
        if os.path.exists(p[key]):
            print("--- %s ---" % os.path.basename(p[key]))
            with open(p[key]) as fh:
                print(fh.read().strip())
    if not os.path.exists(p["lsof"]):
        print("NO_LSOF_SAMPLES")
        return 1
    print("--- lsof data lines (deduplicated) ---")
    seen = []
    with open(p["lsof"]) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("===") or line.startswith("COMMAND"):
                continue
            if line not in seen:
                seen.append(line)
    if not seen:
        print("NO_WRITER_CAPTURED")
    for line in seen:
        print(line)
    if os.path.exists(p["content"]):
        size = os.path.getsize(p["content"])
        print("--- content.txt (%d bytes) ---" % size)
        with open(p["content"], "rb") as fh:
            print(fh.read(400).decode("utf-8", "replace"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["setup", "control", "watch", "writer", "report"])
    ap.add_argument("--dir", default="/tmp/kiro-fifo-probe")
    args = ap.parse_args(argv)
    if args.mode == "setup":
        return cmd_setup(args.dir)
    if args.mode == "control":
        return cmd_control(args.dir)
    if args.mode == "watch":
        return cmd_watch(args.dir)
    if args.mode == "writer":
        return cmd_writer(args.dir)
    return cmd_report(args.dir)


if __name__ == "__main__":
    raise SystemExit(main())

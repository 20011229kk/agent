#!/usr/bin/env python3
"""Probe item 5: does a shell subprocess's file write go through the
fs_write capability check?

Design: positive control + experiment on the SAME execution path.
  - control    : write /tmp/kiro-probe/allowed/b.txt   -> MUST succeed
                 (proves the script actually ran, path is valid, no syntax error)
  - experiment : write <workspace>/.kiro/settings/probe-canary.txt
                 -> this exact path was just DENIED for the fs_write tool by a
                    Kiro-scope hard deny that no configuration can override.

Interpretation:
  control ok + experiment blocked -> this scenario is covered by the capability layer
  control ok + experiment written -> subprocess writes BYPASS the capability layer
  control fails                   -> probe invalid, redo
"""
import errno
import json
import os
import pathlib

CONTROL = pathlib.Path("/tmp/kiro-probe/allowed/b.txt")
EXPERIMENT = pathlib.Path("/Users/AI/test-agent/.kiro/settings/probe-canary.txt")

result = {"control": {}, "experiment": {}}


def attempt(tag, path, payload, make_parents):
    rec = {"path": str(path), "make_parents": make_parents}
    try:
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)
        rec["outcome"] = "WRITTEN"
        rec["exists_after"] = path.exists()
        rec["bytes"] = path.stat().st_size if path.exists() else None
    except OSError as exc:
        rec["outcome"] = "OSError"
        rec["errno"] = exc.errno
        rec["errno_name"] = errno.errorcode.get(exc.errno, "UNKNOWN")
        rec["strerror"] = exc.strerror
        rec["exists_after"] = path.exists()
    except Exception as exc:  # noqa: BLE001 - probe must record any failure shape
        rec["outcome"] = type(exc).__name__
        rec["detail"] = str(exc)
        rec["exists_after"] = path.exists()
    result[tag] = rec


attempt("control", CONTROL, "canary-allowed-b-MODIFIED-by-subprocess\n", False)
attempt("experiment", EXPERIMENT, "probe item5: written by python3 subprocess\n", True)

result["euid"] = os.geteuid()
result["cwd"] = os.getcwd()

out = pathlib.Path("/tmp/kiro-probe/log/item5-result.json")
out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
print(json.dumps(result, indent=2, ensure_ascii=False))

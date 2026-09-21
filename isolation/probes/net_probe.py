#!/usr/bin/env python3
"""容器内网络状态探针，输出 JSON，供 verify-isolation.sh 判定。

为什么不直接在验证脚本里写 try/except
------------------------------------
上一版 C4 的写法是 `except OSError: print("NETWORK_BLOCKED")`，外层只匹配这个字符串。
外部复核做了离线故障注入：`ConnectionRefusedError`、`TimeoutError` 同样命中 PASS。
也就是说"目标服务挂了"或"网络超时"都会被读成"隔离生效"。

所以这里把两类证据分开输出，让判定方自己区分：

1. `interfaces` —— 容器里有哪些网络接口。`--network=none` 下应当只有 `lo`。
   这是**结构性**证据，不依赖任何一次连接结果。
2. `connect` —— 具体连接尝试的 errno。只有 ENETUNREACH(101) / EHOSTUNREACH(113)
   才支持"网络不可达"；ECONNREFUSED(111) 或超时说明有网络栈，属于**未能验证**。

用法：python3 /work/isolation/probes/net_probe.py [host] [port] [timeout]
"""

from __future__ import annotations

import errno
import json
import os
import socket
import sys


def interfaces():
    d = "/sys/class/net"
    try:
        return sorted(os.listdir(d))
    except OSError as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def try_connect(host: str, port: int, timeout: float):
    result = {"host": host, "port": port, "timeout_s": timeout}
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        result.update(ok=True, errno=None, errno_name=None, exception=None)
    except OSError as exc:
        num = getattr(exc, "errno", None)
        result.update(
            ok=False,
            errno=num,
            errno_name=errno.errorcode.get(num) if num else None,
            exception=type(exc).__name__,
            message=str(exc),
        )
    return result


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    host = argv[0] if argv else "1.1.1.1"
    port = int(argv[1]) if len(argv) > 1 else 443
    timeout = float(argv[2]) if len(argv) > 2 else 3.0

    ifaces = interfaces()
    conn = try_connect(host, port, timeout)

    unreachable_errnos = {errno.ENETUNREACH, errno.EHOSTUNREACH}
    if isinstance(ifaces, list):
        only_loopback = set(ifaces) <= {"lo"}
    else:
        only_loopback = None

    if conn["ok"]:
        verdict = "REACHABLE"
    elif conn["errno"] in unreachable_errnos:
        verdict = "UNREACHABLE"
    else:
        # 拒绝、超时、DNS 失败等：不能证明隔离，也不能证明连通
        verdict = "INCONCLUSIVE"

    print(json.dumps({
        "interfaces": ifaces,
        "only_loopback": only_loopback,
        "connect": conn,
        "verdict": verdict,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

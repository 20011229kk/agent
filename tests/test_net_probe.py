"""isolation/probes/net_probe.py 的判定逻辑测试。

来源：外部复核对旧 C4 做了离线故障注入，证明 `ConnectionRefusedError` 与
`TimeoutError` 同样命中旧写法的 `NETWORK_BLOCKED` → PASS。也就是说"目标服务挂了"
或"普通超时"会被读成"隔离生效"。

这里把那次故障注入固化成回归测试：只有 ENETUNREACH / EHOSTUNREACH 才算
UNREACHABLE，其余失败原因一律 INCONCLUSIVE（未能验证），不得升级为隔离结论。
"""

import errno
import io
import json
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "isolation", "probes"))

import net_probe  # noqa: E402


def run_probe(capsys, monkeypatch, raise_exc=None, ifaces=None):
    if raise_exc is None:
        class FakeSock:
            def close(self):
                pass

        monkeypatch.setattr(net_probe.socket, "create_connection",
                            lambda *a, **k: FakeSock())
    else:
        def boom(*a, **k):
            raise raise_exc

        monkeypatch.setattr(net_probe.socket, "create_connection", boom)

    if ifaces is not None:
        monkeypatch.setattr(net_probe, "interfaces", lambda: ifaces)

    net_probe.main(["1.1.1.1", "443", "1"])
    out = capsys.readouterr().out
    return json.loads(out)


def oserror(num):
    exc = OSError(num, os.strerror(num))
    exc.errno = num
    return exc


def test_reachable_when_connection_succeeds(capsys, monkeypatch):
    data = run_probe(capsys, monkeypatch, None, ["lo", "eth0"])
    assert data["verdict"] == "REACHABLE"
    assert data["only_loopback"] is False
    assert data["connect"]["ok"] is True


def test_enetunreach_is_unreachable(capsys, monkeypatch):
    data = run_probe(capsys, monkeypatch, oserror(errno.ENETUNREACH), ["lo"])
    assert data["verdict"] == "UNREACHABLE"
    assert data["connect"]["errno"] == errno.ENETUNREACH
    assert data["connect"]["errno_name"] == "ENETUNREACH"
    assert data["only_loopback"] is True


def test_ehostunreach_is_unreachable(capsys, monkeypatch):
    data = run_probe(capsys, monkeypatch, oserror(errno.EHOSTUNREACH), ["lo"])
    assert data["verdict"] == "UNREACHABLE"


def test_connection_refused_is_inconclusive(capsys, monkeypatch):
    """故障注入 1：目标服务拒绝连接 —— 有网络栈，不能证明隔离。"""
    exc = ConnectionRefusedError(errno.ECONNREFUSED, "refused")
    exc.errno = errno.ECONNREFUSED
    data = run_probe(capsys, monkeypatch, exc, ["lo", "eth0"])
    assert data["verdict"] == "INCONCLUSIVE"
    assert data["connect"]["errno_name"] == "ECONNREFUSED"


def test_timeout_is_inconclusive(capsys, monkeypatch):
    """故障注入 2：超时 —— 可能只是网络慢或对端不响应。"""
    data = run_probe(capsys, monkeypatch, socket.timeout("timed out"),
                     ["lo", "eth0"])
    assert data["verdict"] == "INCONCLUSIVE"


def test_dns_failure_is_inconclusive(capsys, monkeypatch):
    data = run_probe(capsys, monkeypatch, socket.gaierror(-2, "Name or service not known"),
                     ["lo"])
    assert data["verdict"] == "INCONCLUSIVE"


def test_errno_none_is_inconclusive(capsys, monkeypatch):
    """errno 缺失时不得当成不可达。"""
    data = run_probe(capsys, monkeypatch, OSError("no errno"), ["lo"])
    assert data["verdict"] == "INCONCLUSIVE"
    assert data["connect"]["errno"] is None


def test_only_loopback_detection(capsys, monkeypatch):
    data = run_probe(capsys, monkeypatch, oserror(errno.ENETUNREACH), ["lo"])
    assert data["only_loopback"] is True
    data = run_probe(capsys, monkeypatch, oserror(errno.ENETUNREACH),
                     ["lo", "tap0"])
    assert data["only_loopback"] is False


def test_interface_read_error_does_not_crash(capsys, monkeypatch):
    """读不到 /sys/class/net 时报告错误，而不是假装只有 lo。"""
    monkeypatch.setattr(net_probe.os, "listdir",
                        lambda p: (_ for _ in ()).throw(OSError("nope")))
    data = run_probe(capsys, monkeypatch, oserror(errno.ENETUNREACH))
    assert isinstance(data["interfaces"], dict)
    assert data["only_loopback"] is None


@pytest.mark.parametrize("exc,ifaces,expected", [
    (None, ["lo", "eth0"], "REACHABLE"),
    (oserror(errno.ENETUNREACH), ["lo"], "UNREACHABLE"),
    (oserror(errno.EHOSTUNREACH), ["lo"], "UNREACHABLE"),
    (oserror(errno.ECONNREFUSED), ["lo", "eth0"], "INCONCLUSIVE"),
    (oserror(errno.ETIMEDOUT), ["lo", "eth0"], "INCONCLUSIVE"),
    (socket.timeout("t"), ["lo"], "INCONCLUSIVE"),
    (OSError("no errno"), ["lo"], "INCONCLUSIVE"),
])
def test_verdict_matrix_and_no_pass_language(capsys, monkeypatch, exc, ifaces,
                                             expected):
    """探针只报事实三态，判定留给 verify-isolation.sh。

    断言的是**行为**（输出里的 verdict 取值），不是源码文本——本文件上一版去扫源码里
    有没有 "NETWORK_BLOCKED" 字样，结果被探针自己解释旧缺陷的注释绊倒了，
    那是把文档当实现测。
    """
    data = run_probe(capsys, monkeypatch, exc, ifaces)
    assert data["verdict"] == expected
    # 探针输出里不得出现判定词：一刀切的 PASS/NETWORK_BLOCKED 正是旧 C4 的假通过来源
    blob = json.dumps(data)
    assert "NETWORK_BLOCKED" not in blob
    assert "PASS" not in blob

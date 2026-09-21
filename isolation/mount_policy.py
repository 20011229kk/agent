#!/usr/bin/env python3
"""按 isolation/mount-policy.yaml 计算并校验容器挂载参数。

存在理由
--------
上一版 `run-isolated.sh` 直接把调用方给的 `--rw` 变成 `-v ...:rw`，只检查目录存在。
外部复核在真实容器里实测出两条绕过（均成功、退出 0、宿主文件哈希变化）：

1. `--rw qa/baseline` —— 把受保护的覆盖率分母重新挂成可写
2. `--rw ../outside`  —— 穿越到仓库外的同级目录写入

因此挂载范围必须由策略决定。本模块是那份策略的**可单测**实现：把决策逻辑放在
Python 里，才能对每条拒绝理由写回归测试（shell 里几乎测不动）。

诚实的边界
----------
这是**护栏**，不是安全边界。策略文件与本模块都在仓库里，对宿主机有写权限的 agent
可以直接改掉它们；`item 5` 已实测 shell 子进程写入绕过能力层。它能挡住的是越权的
挂载请求，挡不住"改规则本身"。真正的强制点是受保护分支 + CODEOWNERS + CI 复核，
或者把整个会话放进容器。

用法
----
  python3 isolation/mount_policy.py plan [--rw REL]... [--image REF] [--out FILE]
  python3 isolation/mount_policy.py manifest

`plan` 成功时把 podman 参数逐行写出（每行一个参数，便于 shell 读入数组）；
失败时在 stderr 打印逐条拒绝理由并以 65 退出（EX_DATAERR），不产生任何参数。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - 环境事实：系统 python3 自带 PyYAML
    print("FATAL: 需要 PyYAML（系统 python3 已自带）", file=sys.stderr)
    raise

EX_DATAERR = 65
EX_CONFIG = 78

WORKDIR = "/work"
POLICY_FILENAME = "mount-policy.yaml"


class PolicyError(Exception):
    """策略本身不可用（缺字段、路径不存在等），与"请求被拒绝"区分开。"""


def repo_root() -> str:
    """仓库根固定由本文件位置推出，不接受调用方指定。

    这一点是刻意的：如果允许 --repo/QA_ISOLATION_REPO 指定根目录，调用方只要把根
    指到别处，`protected` 与 `allowed_rw` 的相对路径就全部失去意义。
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def policy_path() -> str:
    return os.path.join(repo_root(), "isolation", POLICY_FILENAME)


def load_policy(path: str = None) -> dict:
    path = path or policy_path()
    if not os.path.isfile(path):
        raise PolicyError("策略文件不存在: %s" % path)
    with open(path) as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise PolicyError("策略文件不是映射结构: %s" % path)
    for key in ("allowed_rw", "protected", "image", "network"):
        if key not in data:
            raise PolicyError("策略缺少必填字段 %r" % key)
    if not isinstance(data["allowed_rw"], list) or not data["allowed_rw"]:
        raise PolicyError("allowed_rw 必须是非空列表")
    if not isinstance(data["protected"], list) or not data["protected"]:
        raise PolicyError("protected 必须是非空列表")
    if not isinstance(data["image"], dict) or not data["image"].get("reference"):
        raise PolicyError("image.reference 必填")
    if not isinstance(data["network"], dict) or not data["network"].get("mode"):
        raise PolicyError("network.mode 必填")
    return data


def _norm(root: str, rel: str) -> str:
    """把仓库内相对路径规范化为绝对路径，不解析符号链接。

    这里刻意用 normpath 而不是 realpath：realpath 会跟随符号链接，
    使"这个请求指向哪里"与"这个请求写的是什么"混在一起。符号链接单独判。
    """
    return os.path.normpath(os.path.join(root, rel))


def _inside(root: str, path: str) -> bool:
    """path 是否在 root 之内（含相等）。不用字符串前缀，避免 /work 匹配 /workx。"""
    try:
        return os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) == \
            os.path.abspath(root)
    except ValueError:  # 不同盘符/不可比较
        return False


def _has_symlink_component(root: str, path: str) -> bool:
    """root..path 之间是否存在符号链接组件。

    存在就拒绝：挂载会跟随链接目标，链接指向哪里由文件系统决定，
    而策略匹配的是路径字符串。复核实测里 `--rw link-outside` 正是这一类。
    """
    root = os.path.abspath(root)
    cur = os.path.abspath(path)
    while _inside(root, cur) and cur != root:
        if os.path.islink(cur):
            return True
        cur = os.path.dirname(cur)
    return False


def validate_rw(requests, policy: dict, root: str = None):
    """校验 --rw 请求，返回 (accepted, rejections)。

    accepted: [(相对路径, 宿主绝对路径)]，按输入顺序去重
    rejections: [(原始请求, 拒绝码, 说明)]
    """
    root = root or repo_root()
    allowed = [os.path.normpath(p) for p in policy["allowed_rw"]]
    protected = [os.path.normpath(p) for p in policy["protected"]]

    accepted = []
    rejections = []
    seen = {}

    for raw in requests:
        if raw is None or raw == "":
            rejections.append((raw, "EMPTY", "空的 --rw 值"))
            continue
        if os.path.isabs(raw):
            rejections.append((raw, "ABSOLUTE_PATH",
                               "只接受仓库内相对路径，拒绝绝对路径"))
            continue

        abs_path = _norm(root, raw)

        if not _inside(root, abs_path):
            rejections.append((raw, "OUTSIDE_REPO",
                               "规范化后落在仓库之外: %s" % abs_path))
            continue
        if abs_path == os.path.abspath(root):
            rejections.append((raw, "REPO_ROOT",
                               "不允许把仓库根挂为可写（与只读基础挂载重叠）"))
            continue
        if _has_symlink_component(root, abs_path):
            rejections.append((raw, "SYMLINK_COMPONENT",
                               "路径含符号链接组件，挂载会跟随链接目标"))
            continue

        rel = os.path.relpath(abs_path, root)

        # 受保护：请求本身在受保护路径之内，或请求是某个受保护路径的父目录
        hit = None
        for prot in protected:
            prot_abs = _norm(root, prot)
            if _inside(prot_abs, abs_path):
                hit = (prot, "位于受保护路径之内")
                break
            if _inside(abs_path, prot_abs):
                hit = (prot, "是受保护路径的父目录，挂它等于把受保护路径一起挂上")
                break
        if hit:
            rejections.append((raw, "PROTECTED_PATH",
                               "%s（受保护项: %s）" % (hit[1], hit[0])))
            continue

        # 白名单：必须等于某个 allowed 项或位于其之内
        if not any(_inside(_norm(root, a), abs_path) for a in allowed):
            rejections.append((raw, "NOT_IN_ALLOWLIST",
                               "不在 allowed_rw 白名单内: %s" % rel))
            continue

        if not os.path.isdir(abs_path):
            rejections.append((raw, "MISSING_DIR",
                               "目录不存在，拒绝隐式创建: %s" % rel))
            continue

        # rw 之间不得嵌套：podman 也会报错，但这里给出明确理由
        overlap = None
        for prev_rel, prev_abs in accepted:
            if _inside(prev_abs, abs_path) or _inside(abs_path, prev_abs):
                overlap = prev_rel
                break
        if overlap is not None:
            rejections.append((raw, "OVERLAPPING_RW",
                               "与已接受的可写挂载重叠: %s" % overlap))
            continue

        if rel in seen:
            continue
        seen[rel] = True
        accepted.append((rel, abs_path))

    return accepted, rejections


def resolve_image(policy: dict, requested: str = None):
    """决定用哪个镜像，返回 (reference, rejection_or_None)。

    默认取策略里那个**经过验证的**镜像。上一版默认是 python:3.12-slim，
    只有显式设置环境变量才用 qa-executor —— 结果"构建好的镜像"与"包装器默认跑的镜像"
    根本没接上，默认镜像里 import pytest 直接 ModuleNotFoundError。
    """
    policy_ref = policy["image"]["reference"]
    allow_override = bool(policy["image"].get("allow_override", False))
    if requested in (None, "", policy_ref):
        return policy_ref, None
    if allow_override:
        return requested, None
    return None, ("IMAGE_OVERRIDE_DENIED",
                  "策略不允许覆盖镜像：请求 %r，策略 %r" % (requested, policy_ref))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root: str = None) -> dict:
    """本次执行所依赖的策略与代码的哈希，供 CI 核对"跑的是不是这套规则"。"""
    root = root or repo_root()
    items = {}
    for rel in ("isolation/mount-policy.yaml", "isolation/mount_policy.py",
                "isolation/run-isolated.sh", "isolation/Containerfile"):
        p = os.path.join(root, rel)
        items[rel] = _sha256(p) if os.path.isfile(p) else None
    return {"repo_root": root, "sha256": items}


def build_args(accepted, policy: dict, image: str, root: str = None,
               command=None) -> list:
    root = root or repo_root()
    args = ["run", "--rm", "--network=%s" % policy["network"]["mode"],
            "--workdir", WORKDIR, "-v", "%s:%s:ro" % (root, WORKDIR)]
    for rel, abs_path in accepted:
        args += ["-v", "%s:%s/%s:rw" % (abs_path, WORKDIR, rel)]
    args.append(image)
    args += list(command or [])
    return args


def cmd_plan(argv) -> int:
    ap = argparse.ArgumentParser(prog="mount_policy.py plan")
    ap.add_argument("--rw", action="append", default=[])
    ap.add_argument("--image", default=None)
    ap.add_argument("--out", default=None, help="参数输出文件，默认 stdout")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--repo", default=None,
                    help="仅用于测试；生产路径固定由本文件位置推出")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    root = args.repo or repo_root()
    try:
        policy = load_policy(args.policy)
    except PolicyError as exc:
        print("POLICY_ERROR: %s" % exc, file=sys.stderr)
        return EX_CONFIG

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("REJECTED: NO_COMMAND 缺少要在容器内执行的命令", file=sys.stderr)
        return EX_DATAERR

    image, img_rej = resolve_image(policy, args.image)
    accepted, rejections = validate_rw(args.rw, policy, root)
    if img_rej:
        rejections.append((args.image, img_rej[0], img_rej[1]))

    if rejections:
        for raw, code, why in rejections:
            print("REJECTED: %s %r —— %s" % (code, raw, why), file=sys.stderr)
        print("共 %d 条请求被拒绝，未生成任何挂载参数。" % len(rejections),
              file=sys.stderr)
        return EX_DATAERR

    out_args = build_args(accepted, policy, image, root, command)
    text = "\n".join(out_args) + "\n"
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


def cmd_image(argv) -> int:
    """打印策略指定的镜像引用或 digest。给 shell 用，避免在 shell 里内联解析 YAML。

    digest 的用途：verify-isolation.sh 在跑任何检查之前先核对"待验证的镜像是不是
    策略里那个"。不核对的话，验证结果描述的可能是另一个镜像。
    策略里没写 digest 时输出空串并以 3 退出，由调用方决定记 VOID 还是继续。
    """
    ap = argparse.ArgumentParser(prog="mount_policy.py image")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--field", default="reference",
                    choices=["reference", "digest"])
    args = ap.parse_args(argv)
    try:
        policy = load_policy(args.policy)
    except PolicyError as exc:
        print("POLICY_ERROR: %s" % exc, file=sys.stderr)
        return EX_CONFIG
    value = policy["image"].get(args.field)
    if not value:
        print("IMAGE_FIELD_UNSET: 策略未设置 image.%s" % args.field,
              file=sys.stderr)
        return 3
    sys.stdout.write(value)
    return 0


def cmd_manifest(argv) -> int:
    ap = argparse.ArgumentParser(prog="mount_policy.py manifest")
    ap.add_argument("--repo", default=None)
    args = ap.parse_args(argv)
    print(json.dumps(manifest(args.repo), indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    modes = {"plan": cmd_plan, "manifest": cmd_manifest, "image": cmd_image}
    if not argv or argv[0] not in modes:
        print(__doc__)
        return EX_DATAERR
    return modes[argv[0]](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

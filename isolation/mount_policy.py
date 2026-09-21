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
  python3 isolation/mount_policy.py run  [--rw REL]... -- <命令...>   # 校验并直接执行
  python3 isolation/mount_policy.py plan [--rw REL]... -- <命令...>   # 只打印计划
  python3 isolation/mount_policy.py image [--field reference|digest]
  python3 isolation/mount_policy.py manifest

**参数传递必须无损：为什么是 `run` 而不是"让 shell 读 plan 的输出"。**
上一版 `plan` 把 podman 参数逐行写出、由 shell 按行读回数组。外部复核实测出确定的
假成功：

    python -c "print('BEGIN')\nraise SystemExit(42)"

单行版本正确退出 42；**多行版本只打印 BEGIN 就退出 0** —— 参数内部的换行被当成了
行分隔符，第二行变成 Python 的另一个命令行参数，`raise SystemExit(42)` 根本没执行。
同理 `['', 'tail']` 里的空参数在按行读取时被跳过。对 agent 来说这意味着多行
`python -c` / `sh -c` 里的断言或清理步骤可能被静默省略，而退出码显示成功。

现在 `run` 模式在 Python 内校验后直接 `os.execvp("podman", argv)`：参数数组从
调用方的 argv 到 podman 的 argv 之间**不经过任何按行文本协议**。`plan` 仍保留给
排查与测试用，但默认输出 NUL 分隔（`--sep line` 才回到逐行，且明确标注不可用于执行）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
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


def inspect_image(ref: str):
    """一次 podman inspect 同时取回 Digest 与 Id，返回 (digest, image_id, error)。

    两个值必须在**同一次**调用里取：先按标签核对 digest、再按标签启动，中间标签可能
    被重新指向（重新构建同名 tag 即可），那是个竞态。取到 Id 后按 Id 启动，
    标签之后怎么动都与本次执行无关。
    """
    try:
        out = subprocess.run(
            ["podman", "image", "inspect", ref,
             "--format", "{{.Digest}}\t{{.Id}}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "podman 调用失败: %s" % exc
    if out.returncode != 0:
        return None, None, (out.stderr.decode("utf-8", "replace").strip()
                            or "podman inspect 退出 %d" % out.returncode)
    text = out.stdout.decode("utf-8", "replace").strip()
    parts = text.split("\t")
    if len(parts) != 2 or not parts[1]:
        return None, None, "无法解析 inspect 输出: %r" % text
    return parts[0] or None, parts[1], None


def resolve_runtime_image(policy: dict, requested: str = None,
                          inspector=inspect_image):
    """决定**实际启动**用的不可变镜像标识，返回 (image_id, info, rejection)。

    上一版只核对镜像名：`resolve_image` 返回 `image.reference`，`build_args` 原样把
    可变标签交给 podman，策略里的 `image.digest` 从未在普通执行路径上被用到。
    外部复核指出这个缺口：禁止 `QA_ISOLATION_IMAGE` 覆盖只限制了**名字**，
    同名标签被重新构建或移动之后，后续执行会静默用上另一个镜像。C0 只在验证脚本里跑，
    普通 `run-isolated.sh` 路径不跑 C0。

    现在普通执行路径也强制核对：digest 不符、镜像不存在、策略未记录 digest → 拒绝执行。
    """
    ref, rej = resolve_image(policy, requested)
    if rej:
        return None, None, rej

    expected = policy["image"].get("digest")
    if not expected:
        return None, None, ("IMAGE_DIGEST_UNSET",
                            "策略未记录 image.digest，无法确认运行时镜像身份；"
                            "请先 podman image inspect %s --format '{{.Digest}}' "
                            "并写回策略" % ref)

    digest, image_id, err = inspector(ref)
    if err:
        return None, None, ("IMAGE_NOT_FOUND",
                            "取不到镜像 %s 的身份：%s" % (ref, err))
    if digest != expected:
        return None, None, ("IMAGE_DIGEST_MISMATCH",
                            "镜像 %s 的 digest 与策略不一致：实际 %s，策略 %s"
                            % (ref, digest, expected))
    return image_id, {"reference": ref, "digest": digest, "image_id": image_id}, None


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


def _common_args(prog: str, argv):
    ap = argparse.ArgumentParser(prog=prog)
    ap.add_argument("--rw", action="append", default=[])
    ap.add_argument("--image", default=None)
    ap.add_argument("--policy", default=None)
    ap.add_argument("--repo", default=None,
                    help="仅用于测试；生产路径固定由本文件位置推出")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    return ap.parse_args(argv)


def _prepare(args, verify_image: bool, inspector=inspect_image):
    """公共校验流程，返回 (podman_argv, info, rc)。rc 非 0 时 argv 为 None。"""
    root = args.repo or repo_root()
    try:
        policy = load_policy(args.policy)
    except PolicyError as exc:
        print("POLICY_ERROR: %s" % exc, file=sys.stderr)
        return None, None, EX_CONFIG

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("REJECTED: NO_COMMAND 缺少要在容器内执行的命令", file=sys.stderr)
        return None, None, EX_DATAERR

    rejections = []
    info = {}
    if verify_image:
        # 普通执行路径也核对运行时镜像身份，并按不可变 Id 启动
        image, img_info, img_rej = resolve_runtime_image(policy, args.image,
                                                        inspector)
        if img_rej:
            rejections.append((args.image or policy["image"]["reference"],
                               img_rej[0], img_rej[1]))
        else:
            info.update(img_info)
    else:
        image, img_rej = resolve_image(policy, args.image)
        if img_rej:
            rejections.append((args.image, img_rej[0], img_rej[1]))

    accepted, rw_rejections = validate_rw(args.rw, policy, root)
    rejections.extend(rw_rejections)

    if rejections:
        for raw, code, why in rejections:
            print("REJECTED: %s %r —— %s" % (code, raw, why), file=sys.stderr)
        print("共 %d 条请求被拒绝，未生成任何挂载参数。" % len(rejections),
              file=sys.stderr)
        return None, None, EX_DATAERR

    return build_args(accepted, policy, image, root, command), info, 0


def cmd_run(argv, inspector=inspect_image, execer=None) -> int:
    """校验通过后**直接 exec podman**，参数数组不经过任何文本协议。

    这是修复"按行传参截断多行命令"的关键：调用方 argv → Python argv → podman argv，
    全程是数组，换行与空参数都原样保留。
    """
    args = _common_args("mount_policy.py run", argv)
    podman_argv, info, rc = _prepare(args, verify_image=True, inspector=inspector)
    if rc:
        return rc
    if os.environ.get("QA_ISOLATION_TRACE"):
        print("IMAGE_VERIFIED digest=%s id=%s"
              % (info.get("digest"), (info.get("image_id") or "")[:16]),
              file=sys.stderr)
    argv_full = ["podman"] + podman_argv
    if execer is not None:          # 测试注入点
        return execer(argv_full)
    os.execvp("podman", argv_full)  # 不返回
    return 70                       # pragma: no cover


def cmd_plan(argv) -> int:
    """只打印计划，供排查与测试。**输出不得再被 shell 拼回命令执行。**

    默认 NUL 分隔：逐行分隔会把参数内部的换行变成分隔符，空参数也会消失，
    这正是上一版假成功的根源。`--sep line` 仅供人读。
    """
    ap = argparse.ArgumentParser(prog="mount_policy.py plan", add_help=False)
    ap.add_argument("--out", default=None, help="参数输出文件，默认 stdout")
    ap.add_argument("--sep", default="nul", choices=["nul", "line", "json"])
    ap.add_argument("--verify-image", action="store_true",
                    help="同时核对运行时镜像身份（需要 podman）")
    known, rest = ap.parse_known_args(argv)
    args = _common_args("mount_policy.py plan", rest)

    podman_argv, _info, rc = _prepare(args, verify_image=known.verify_image)
    if rc:
        return rc

    if known.sep == "json":
        text = json.dumps(podman_argv, ensure_ascii=False) + "\n"
    elif known.sep == "line":
        text = "\n".join(podman_argv) + "\n"
    else:
        text = "\0".join(podman_argv) + "\0"

    if known.out:
        with open(known.out, "w") as fh:
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
    modes = {"run": cmd_run, "plan": cmd_plan, "manifest": cmd_manifest,
             "image": cmd_image}
    if not argv or argv[0] not in modes:
        print(__doc__)
        return EX_DATAERR
    return modes[argv[0]](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

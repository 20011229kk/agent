#!/bin/zsh
# 在容器内执行 qa-executor 的 shell 命令。挂载范围与镜像身份由策略决定，
# 不由调用参数决定。
#
# 存在理由（实测依据，见 docs/capability-matrix.md item 5）：
# agent 的 shell 子进程写入**完全绕过** Kiro 能力层，包括配置不可更改的 kiro-scope
# 硬拒绝。所以"executor 的写入范围"不能由 permissions 规则保证。
#
# 本脚本刻意保持极薄：**只做参数转交，不做任何解析与拼装。**
# 原因是上一版在这里踩了两个坑，两个都由外部复核实测出来：
#
#   1. 接受调用方给的任意 --rw（只检查目录存在）→ `--rw qa/baseline` 能把受保护的
#      覆盖率分母重新挂成可写，`--rw ../outside` 能穿越到仓库外，两条都写入成功。
#      → 挂载范围改由 isolation/mount-policy.yaml 决定，本脚本不再碰挂载参数。
#   2. 从 mount_policy.py 的输出**按行**读回 podman 参数 → 参数内部的换行被当成
#      分隔符。`python -c $'print("BEGIN")\nraise SystemExit(42)'` 只打印 BEGIN 就
#      退出 0，`raise SystemExit(42)` 压根没执行；空参数也被跳过。这是最坏的一类
#      假成功：失败逻辑没跑，退出码却是成功。
#      → 现在由 mount_policy.py run 在 Python 内校验后直接 exec podman，
#        参数数组从这里的 "$@" 一路到 podman 的 argv，中间没有任何文本协议。
#
# 诚实的边界（三条，都别省）：
# 1. 只约束**通过 shell 发起**的写入。工具级 fs_write 由 IDE 宿主进程执行，
#    不经过这里（item 8 / docs/probe-evidence/task3/task3-findings.md）。
# 2. 目前**没有机制强制**必须通过本包装器执行；直接在宿主机跑命令依然可行。
# 3. 策略文件与校验器都在仓库里，对宿主机有写权限的 agent 可以改掉它们。这是护栏，
#    不是安全边界；强制点仍是受保护分支 + CODEOWNERS + CI 复核。
#
# 用法：
#   isolation/run-isolated.sh [--rw <仓库内相对路径>]... [--print-manifest] -- <命令>
# 例（多行命令也保证原样传入）：
#   isolation/run-isolated.sh --rw qa/runs -- python -m pytest tests -q
set -u

HERE=${0:A:h}
REPO=${HERE:h}
POLICY_TOOL=$HERE/mount_policy.py

# 仓库根固定由脚本位置推出。若允许调用方改根目录，策略里的相对路径就失去意义，
# 受保护清单会整体失效 —— 所以这里是拒绝而不是接受。
if [[ -n ${QA_ISOLATION_REPO:-} ]] && [[ ${QA_ISOLATION_REPO:A} != ${REPO:A} ]]; then
  echo "FATAL: 不接受 QA_ISOLATION_REPO 覆盖仓库根（请求 ${QA_ISOLATION_REPO}，实际 ${REPO}）" >&2
  exit 78
fi

# podman machine（libkrun）只挂载宿主机 /Users，仓库必须位于其下
case "$REPO" in
  /Users/*) : ;;
  *) echo "FATAL: 仓库 $REPO 不在 /Users 下，podman machine 挂载不到" >&2; exit 64 ;;
esac

RW_ARGS=()
PRINT_MANIFEST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rw) RW_ARGS+=(--rw "$2"); shift 2 ;;
    --print-manifest) PRINT_MANIFEST=1; shift ;;
    --) shift; break ;;
    *) echo "FATAL: 未知参数 $1" >&2; exit 64 ;;
  esac
done
if [[ $# -eq 0 ]]; then
  echo "FATAL: 缺少要执行的命令（-- 之后）" >&2
  exit 64
fi

IMAGE_ARGS=()
if [[ -n ${QA_ISOLATION_IMAGE:-} ]]; then
  # 不静默忽略：交给策略判定，策略默认拒绝覆盖，理由会打印出来
  IMAGE_ARGS=(--image "$QA_ISOLATION_IMAGE")
fi

if [[ $PRINT_MANIFEST -eq 1 ]]; then
  python3 "$POLICY_TOOL" manifest >&2
fi

# "$@" 原样转交：参数内部的换行、空参数、引号、空格、反斜杠都由 argv 语义保证，
# 不经过任何逐行/分隔符协议。校验失败时 mount_policy.py 以 65/78 退出且不启动容器。
exec python3 "$POLICY_TOOL" run ${RW_ARGS[@]:-} ${IMAGE_ARGS[@]:-} -- "$@"

#!/bin/zsh
# 在容器内执行 qa-executor 的 shell 命令。挂载范围由策略决定，不由调用参数决定。
#
# 存在理由（实测依据，见 docs/capability-matrix.md item 5）：
# agent 的 shell 子进程写入**完全绕过** Kiro 能力层，包括配置不可更改的 kiro-scope
# 硬拒绝。所以"executor 的写入范围"不能由 permissions 规则保证。
#
# 上一版的缺陷（外部复核在真实容器里实测出来，两条都成功、退出 0、宿主哈希变化）：
#   --rw qa/baseline  → 把受保护的覆盖率分母重新挂成可写
#   --rw ../outside   → 穿越到仓库外的同级目录写入
# 现在所有 --rw 请求都交给 isolation/mount_policy.py 按 isolation/mount-policy.yaml
# 校验；本脚本自己不再拼任何挂载参数。
#
# 诚实的边界（三条，都别省）：
# 1. 本包装器只约束**通过 shell 发起**的写入。工具级 fs_write 由 IDE 宿主进程执行，
#    不经过这里（item 8 / docs/probe-evidence/task3/task3-findings.md）。
# 2. 目前**没有机制强制**必须通过本包装器执行；直接在宿主机跑命令依然可行。
# 3. 策略文件与校验器都在仓库里，对宿主机有写权限的 agent 可以改掉它们。这是护栏，
#    不是安全边界；强制点仍是受保护分支 + CODEOWNERS + CI 复核。
#
# 用法：
#   isolation/run-isolated.sh [--rw <仓库内相对路径>]... [--print-manifest] -- <命令>
# 例：
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

ARGFILE=$(mktemp -t qa-isolation-args)
trap 'rm -f "$ARGFILE"' EXIT

# 策略校验 + 参数生成。被拒绝时不生成任何参数，直接把理由透传给调用方。
if ! python3 "$POLICY_TOOL" plan ${RW_ARGS[@]:-} ${IMAGE_ARGS[@]:-} --out "$ARGFILE" -- "$@"; then
  echo "FATAL: 挂载策略校验未通过，未启动容器" >&2
  exit 65
fi

PODMAN_ARGS=()
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  PODMAN_ARGS+=("$line")
done < "$ARGFILE"

if [[ ${#PODMAN_ARGS[@]} -eq 0 ]]; then
  echo "FATAL: 策略未产生任何参数" >&2
  exit 70
fi

exec podman ${PODMAN_ARGS[@]}

#!/bin/zsh
# 在容器内执行 qa-executor 的 shell 命令。
#
# 存在理由（实测依据，见 docs/capability-matrix.md item 5）：
# agent 的 shell 子进程写入**完全绕过** Kiro 能力层，包括配置不可更改的 kiro-scope
# 硬拒绝。因此"executor 的写入范围"不能由 permissions 规则保证，只能由这里的挂载表
# 保证：默认整个仓库只读，只有显式列出的路径可写。
#
# 边界（不要夸大）：本包装器只约束**通过 shell 发起**的写入。工具级 fs_write 由 IDE
# 宿主进程执行，不经过这里（item 8 / docs/probe-evidence/task3/task3-findings.md）。
#
# 用法：
#   isolation/run-isolated.sh [--rw <仓库内相对路径>]... -- <要在容器内执行的命令>
# 例：
#   isolation/run-isolated.sh --rw qa/runs -- python -m pytest tests -q
set -u

IMAGE=${QA_ISOLATION_IMAGE:-docker.io/library/python:3.12-slim}
REPO=${QA_ISOLATION_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
WORKDIR=/work

# podman machine（libkrun）只挂载宿主机 /Users，仓库必须位于其下
case "$REPO" in
  /Users/*) : ;;
  *) echo "FATAL: 仓库 $REPO 不在 /Users 下，podman machine 挂载不到" >&2; exit 64 ;;
esac

RW_PATHS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rw) RW_PATHS+=("$2"); shift 2 ;;
    --) shift; break ;;
    *) echo "FATAL: 未知参数 $1" >&2; exit 64 ;;
  esac
done
if [[ $# -eq 0 ]]; then
  echo "FATAL: 缺少要执行的命令（-- 之后）" >&2
  exit 64
fi

# 注意：挂载串里的变量必须加花括号。zsh 会把 "$WORKDIR:ro" 当成历史修饰符
# ${WORKDIR:r} 再接一个字面 o，挂载点会静默变成 /worko —— 容器照样起得来，
# 只是什么都没挂上，很容易被读成"隔离生效了"。
MOUNTS=(-v "${REPO}:${WORKDIR}:ro")
for rel in ${RW_PATHS[@]:-}; do
  [[ -z "$rel" ]] && continue
  if [[ ! -d "${REPO}/${rel}" ]]; then
    echo "FATAL: 可写路径 $rel 在仓库中不存在，拒绝隐式创建" >&2
    exit 64
  fi
  MOUNTS+=(-v "${REPO}/${rel}:${WORKDIR}/${rel}:rw")
done

# --network=none：执行期不需要外网；需要拉依赖时应在镜像构建阶段完成并锁版本
exec podman run --rm \
  --network=none \
  --workdir "$WORKDIR" \
  ${MOUNTS[@]} \
  "$IMAGE" \
  "$@"

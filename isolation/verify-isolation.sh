#!/bin/zsh
# 验证 run-isolated.sh 的挂载边界是真的拦截，而不是"看起来拦住了"。
#
# 每条检查都要求三段证据：命令确实执行、容器内的拒绝原文、宿主机 canary 的前后哈希。
#
# 为什么判据要这么啰嗦：本脚本第一版只用"canary 哈希未变"判 C2 通过，结果 runner 缺
# 可执行位、五条命令全是 permission denied、什么都没跑，C2 照样报 PASS。凡"没发生坏事"
# 型判据，都必须附带"坏事确实被尝试过"的证据，否则一律作废（VOID）。
#
# 用法：isolation/verify-isolation.sh [输出文件]
set -u

REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$REPO/.tmp-isolation-verify.txt}
SANDBOX=$REPO/.tmp-isolation-check
RUN=$REPO/isolation/run-isolated.sh
IMAGE=${QA_ISOLATION_IMAGE:-docker.io/library/python:3.12-slim}

rm -rf "$SANDBOX"
mkdir -p "$SANDBOX/writable" "$SANDBOX/protected" "$SANDBOX/log"
echo "PROTECTED-CANARY-ORIGINAL" > "$SANDBOX/protected/canary.txt"

hash_of() { shasum -a 256 "$1" | awk '{print $1}'; }
PROTECTED_BEFORE=$(hash_of "$SANDBOX/protected/canary.txt")
VERSION_BEFORE=$(hash_of "$REPO/VERSION")

VOID=0
FAILED=0

run_case() {   # run_case <名字> <命令...>
  local name=$1; shift
  local log=$SANDBOX/log/$name.out
  zsh "$RUN" "$@" > "$log" 2>&1
  local rc=$?
  echo "$rc" > "$SANDBOX/log/$name.rc"
  cat "$log"
  echo "runner_rc=$rc"
}

{
echo "=== 隔离验证 $(date '+%Y-%m-%dT%H:%M:%S%z') ==="
echo "repo:   $REPO"
echo "image:  $IMAGE"
echo "canary before:  $PROTECTED_BEFORE"
echo "VERSION before: $VERSION_BEFORE"
echo

echo "--- C1 阳性对照：可写挂载内写入应当成功 ---"
run_case c1 --rw .tmp-isolation-check/writable -- \
  sh -c 'printf "WRITTEN-INSIDE-CONTAINER\n" > /work/.tmp-isolation-check/writable/from-container.txt; echo "inner_rc=$?"'
if [[ -f "$SANDBOX/writable/from-container.txt" ]] \
   && grep -q "inner_rc=0" "$SANDBOX/log/c1.out"; then
  echo "C1 宿主机可见: $(hash_of "$SANDBOX/writable/from-container.txt")"
  echo "C1 内容: $(cat "$SANDBOX/writable/from-container.txt")"
  echo "C1: PASS（挂载链路通，测量手段有效）"
else
  echo "C1: FAIL —— 阳性对照未通过，以下所有拒绝类结论作废"
  VOID=1; FAILED=1
fi
echo

echo "--- C2 只读挂载内写入应当被拒绝（需同时拿到拒绝原文） ---"
run_case c2 --rw .tmp-isolation-check/writable -- \
  sh -c 'printf "TAMPERED\n" > /work/.tmp-isolation-check/protected/canary.txt; echo "inner_rc=$?"'
PROTECTED_AFTER=$(hash_of "$SANDBOX/protected/canary.txt")
echo "canary after: $PROTECTED_AFTER"
if [[ $VOID -eq 1 ]]; then
  echo "C2: VOID（阳性对照未通过）"; FAILED=1
elif [[ "$PROTECTED_BEFORE" != "$PROTECTED_AFTER" ]]; then
  echo "C2: FAIL（canary 被改写，隔离未生效）"; FAILED=1
elif grep -qi "read-only file system" "$SANDBOX/log/c2.out"; then
  echo "C2: PASS（哈希未变 + 拿到 Read-only file system 拒绝原文）"
else
  echo "C2: VOID（哈希未变，但没有拒绝原文，无法证明写入被尝试过）"; FAILED=1
fi
echo

echo "--- C3 未挂载的宿主机路径在容器内不存在 ---"
run_case c3 -- sh -c 'ls /Users; echo "inner_rc=$?"'
if [[ $VOID -eq 1 ]]; then
  echo "C3: VOID（阳性对照未通过）"; FAILED=1
elif grep -qi "no such file or directory" "$SANDBOX/log/c3.out"; then
  echo "C3: PASS（靠不存在，不靠权限判定）"
else
  echo "C3: FAIL（容器内能看到 /Users）"; FAILED=1
fi
echo

echo "--- C4 执行期无外网（--network=none） ---"
run_case c4 -- python -c 'import socket
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3)
    print("NETWORK_REACHABLE")
except OSError as e:
    print("NETWORK_BLOCKED:", type(e).__name__, e)'
if [[ $VOID -eq 1 ]]; then
  echo "C4: VOID（阳性对照未通过）"; FAILED=1
elif grep -q "NETWORK_BLOCKED" "$SANDBOX/log/c4.out"; then
  echo "C4: PASS"
else
  echo "C4: FAIL（容器内可连外网）"; FAILED=1
fi
echo

echo "--- C5 仓库根整体只读（不止显式受保护目录） ---"
run_case c5 -- sh -c 'printf x > /work/VERSION; echo "inner_rc=$?"'
VERSION_AFTER=$(hash_of "$REPO/VERSION")
echo "VERSION after: $VERSION_AFTER"
if [[ $VOID -eq 1 ]]; then
  echo "C5: VOID（阳性对照未通过）"; FAILED=1
elif [[ "$VERSION_BEFORE" != "$VERSION_AFTER" ]]; then
  echo "C5: FAIL（仓库文件被容器改写）"; FAILED=1
elif grep -qi "read-only file system" "$SANDBOX/log/c5.out"; then
  echo "C5: PASS（哈希未变 + 拒绝原文）"
else
  echo "C5: VOID（哈希未变但无拒绝原文）"; FAILED=1
fi
echo

echo "=== 总判定 ==="
if [[ $FAILED -eq 0 ]]; then
  echo "VERDICT: PASS（5/5，含阳性对照）"
else
  echo "VERDICT: NOT_PASS（存在 FAIL 或 VOID，逐条见上）"
fi
echo "作用域：本验证只覆盖**通过 shell 发起**的写入（item 5 那条路径）。"
echo "工具级 fs_write 由 IDE 宿主进程执行，不经过本包装器（item 8）。"
echo "=== 结束 ==="
} > "$OUT" 2>&1

echo "写入 $OUT"
grep -E "^C[0-9]: |^VERDICT: " "$OUT" || true
[[ $FAILED -eq 0 ]]

#!/bin/zsh
# 验证 run-isolated.sh 的边界是真的拦截，而不是"看起来拦住了"。
#
# 判据设计原则（每一条都是被真实缺陷教出来的）：
#
# 1. 阳性对照必须先过。第一版包装器缺可执行位，5 条命令全是 permission denied，
#    而当时 C2 只看"canary 哈希未变"就报 PASS。凡"没发生坏事"型判据，必须附带
#    "坏事确实被尝试过"的证据，否则一律作废（VOID）。
# 2. 拒绝类判据必须拿到拒绝原文，不能只看结果没变。
# 3. 网络判据不能只看"连接失败"。外部复核的离线故障注入证明 ConnectionRefused 与
#    Timeout 同样会命中旧写法的 PASS。现在改为：结构性证据（只有 lo 接口）
#    + errno 限定（仅 ENETUNREACH/EHOSTUNREACH）+ 可达端点阳性对照。
# 4. 越权挂载请求必须被包装器拒绝，且这两条来自外部复核的真实绕过要长期留作回归。
#
# 用法：isolation/verify-isolation.sh [输出文件]
set -u

HERE=${0:A:h}
REPO=${HERE:h}
OUT=${1:-$REPO/.tmp-isolation-verify.txt}
SANDBOX=$REPO/.tmp-isolation-check
RUN=$HERE/run-isolated.sh
POLICY_TOOL=$HERE/mount_policy.py
NET_PROBE=/work/isolation/probes/net_probe.py
IMAGE=$(python3 "$POLICY_TOOL" image)

rm -rf "$SANDBOX"
mkdir -p "$SANDBOX/writable" "$SANDBOX/protected" "$SANDBOX/log"
echo "PROTECTED-CANARY-ORIGINAL" > "$SANDBOX/protected/canary.txt"

hash_of() { shasum -a 256 "$1" | awk '{print $1}'; }
PROTECTED_BEFORE=$(hash_of "$SANDBOX/protected/canary.txt")
VERSION_BEFORE=$(hash_of "$REPO/VERSION")

VOID=0
FAILED=0

run_case() {   # run_case <名字> <run-isolated.sh 的参数...>
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
echo "image:  $IMAGE（取自 isolation/mount-policy.yaml，不由调用方指定）"
echo "canary before:  $PROTECTED_BEFORE"
echo "VERSION before: $VERSION_BEFORE"
echo "--- 策略与代码 manifest（供 CI 核对跑的是不是这套规则）---"
python3 "$POLICY_TOOL" manifest
echo

echo "--- C0 镜像身份核对：本机镜像 digest 必须等于策略记录值 ---"
POLICY_DIGEST=$(python3 "$POLICY_TOOL" image --field digest 2>/dev/null)
ACTUAL_DIGEST=$(podman image inspect "$IMAGE" --format '{{.Digest}}' 2>/dev/null)
echo "policy digest: ${POLICY_DIGEST:-<未设置>}"
echo "actual digest: ${ACTUAL_DIGEST:-<取不到>}"
if [[ -z "$POLICY_DIGEST" ]]; then
  echo "C0: VOID（策略未记录 digest，无法确认待验证镜像身份）"; VOID=1; FAILED=1
elif [[ -z "$ACTUAL_DIGEST" ]]; then
  echo "C0: FAIL（本机没有该镜像，先 podman build -f isolation/Containerfile .）"
  VOID=1; FAILED=1
elif [[ "$POLICY_DIGEST" != "$ACTUAL_DIGEST" ]]; then
  echo "C0: FAIL（digest 不一致：验证结果描述的不是策略指定的那个镜像）"
  VOID=1; FAILED=1
else
  echo "C0: PASS"
fi
echo

echo "--- C1 阳性对照：策略白名单内的可写挂载应当写入成功 ---"
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

echo "--- C4 网络隔离：结构性证据 + errno 限定 + 同端点可达性阳性对照 ---"
# 端点不能硬编码一个了事：本机实测 1.1.1.1:443 被链路挡住（TimeoutError），
# 若直接拿它当阳性对照，结论会是"环境本身不通"而非隔离结论。
# 所以先在**默认网络**下逐个探，挑出确实可达的端点，再用**同一端点**做隔离实验。
ENDPOINTS=(${(s: :)${QA_ISOLATION_NET_ENDPOINTS:-"223.5.5.5:443 8.8.8.8:53 114.114.114.114:53 1.1.1.1:443"}})
CTRL_EP=""
echo "C4b 阳性对照（**故意绕开包装器**，同一镜像，默认网络）:"
for ep in ${ENDPOINTS[@]}; do
  h=${ep%%:*}; pt=${ep##*:}
  podman run --rm -v "$REPO:/work:ro" --workdir /work --network=bridge "$IMAGE" \
    python "$NET_PROBE" "$h" "$pt" 5 > "$SANDBOX/log/c4b-try.out" 2>&1
  echo "  尝试 $ep -> $(grep '"verdict"' "$SANDBOX/log/c4b-try.out" | tr -d ' ,\"' | cut -d: -f2)"
  if grep -q '"verdict": "REACHABLE"' "$SANDBOX/log/c4b-try.out"; then
    CTRL_EP=$ep
    cp "$SANDBOX/log/c4b-try.out" "$SANDBOX/log/c4b.out"
    break
  fi
done
if [[ -z "$CTRL_EP" ]]; then
  echo "  所有候选端点都不可达 —— 阳性对照无法建立"
  cp "$SANDBOX/log/c4b-try.out" "$SANDBOX/log/c4b.out" 2>/dev/null || true
  CTRL_EP="223.5.5.5:443"
fi
echo "  选定端点: $CTRL_EP"
cat "$SANDBOX/log/c4b.out"
echo "C4a 隔离态（策略 network.mode=none，同一端点 $CTRL_EP）:"
run_case c4a -- python "$NET_PROBE" "${CTRL_EP%%:*}" "${CTRL_EP##*:}" 3

C4_VERDICT=$(python3 - "$SANDBOX/log/c4a.out" "$SANDBOX/log/c4b.out" <<'PY'
import json, sys, re

def load(path):
    try:
        text = open(path).read()
    except OSError as exc:
        return None, "READ_ERROR %s" % exc
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None, "NO_JSON"
    try:
        return json.loads(m.group(0)), None
    except ValueError as exc:
        return None, "BAD_JSON %s" % exc

iso, err1 = load(sys.argv[1])
ctl, err2 = load(sys.argv[2])

if iso is None:
    print("VOID 隔离态探针无可解析输出: %s" % err1); raise SystemExit
if ctl is None:
    print("VOID 阳性对照无可解析输出: %s" % err2); raise SystemExit

if ctl.get("verdict") != "REACHABLE":
    # 宿主机本身没有外网时，隔离侧的"不可达"不构成隔离生效的证据
    print("VOID 阳性对照未能连通（verdict=%s, errno=%s），无法区分"
          "隔离生效与环境本身不通"
          % (ctl.get("verdict"), ctl.get("connect", {}).get("errno")))
    raise SystemExit

# 两侧必须打同一个端点，否则比较的是两件不同的事
ic, cc = iso.get("connect", {}), ctl.get("connect", {})
if (ic.get("host"), ic.get("port")) != (cc.get("host"), cc.get("port")):
    print("VOID 两侧端点不一致：隔离态 %s:%s vs 对照 %s:%s"
          % (ic.get("host"), ic.get("port"), cc.get("host"), cc.get("port")))
    raise SystemExit

if iso.get("only_loopback") is not True:
    print("FAIL 隔离态容器内存在非 lo 接口: %s" % iso.get("interfaces"))
    raise SystemExit

v = iso.get("verdict")
e = iso.get("connect", {}).get("errno")
name = iso.get("connect", {}).get("errno_name")
if v == "UNREACHABLE":
    print("PASS 仅 lo 接口 + errno %s(%s) + 同端点 %s:%s 阳性对照可连通"
          % (e, name, cc.get("host"), cc.get("port")))
elif v == "INCONCLUSIVE":
    print("VOID 连接失败原因不支持隔离结论（errno %s(%s)）：拒绝/超时不等于网络不可达"
          % (e, name))
else:
    print("FAIL 隔离态仍可连通")
PY
)
echo "C4 判定依据: $C4_VERDICT"
if [[ $VOID -eq 1 ]]; then
  echo "C4: VOID（阳性对照未通过）"; FAILED=1
elif [[ "$C4_VERDICT" == PASS* ]]; then
  echo "C4: PASS"
else
  echo "C4: ${C4_VERDICT%% *}（见上方依据）"; FAILED=1
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

echo "=== 以下为外部复核实测出的真实绕过，必须被策略拒绝（回归场景）==="

echo "--- C6 回归 R1：--rw qa/baseline 重新挂载受保护目录 ---"
BASELINE_CANARY=$REPO/qa/baseline/.granted-should-not-exist.txt
rm -f "$BASELINE_CANARY"
run_case c6 --rw qa/baseline -- \
  sh -c 'printf "GRANTED\n" > /work/qa/baseline/.granted-should-not-exist.txt; echo "inner_rc=$?"'
C6_RC=$(cat "$SANDBOX/log/c6.rc")
if [[ -e "$BASELINE_CANARY" ]]; then
  echo "C6: FAIL（受保护目录被写入：$BASELINE_CANARY）"; FAILED=1
  rm -f "$BASELINE_CANARY"
elif [[ "$C6_RC" == "65" ]] && grep -q "PROTECTED_PATH" "$SANDBOX/log/c6.out"; then
  echo "C6: PASS（退出 65 + PROTECTED_PATH 拒绝原文 + 容器未启动）"
else
  echo "C6: FAIL（未按预期拒绝：rc=$C6_RC）"; FAILED=1
fi
echo

echo "--- C7 回归 R2：--rw ../outside 父目录穿越 ---"
run_case c7 --rw ../outside -- \
  sh -c 'printf "TRAVERSAL\n" > /outside/traversal.txt; echo "inner_rc=$?"'
C7_RC=$(cat "$SANDBOX/log/c7.rc")
if [[ "$C7_RC" == "65" ]] && grep -q "OUTSIDE_REPO" "$SANDBOX/log/c7.out"; then
  echo "C7: PASS（退出 65 + OUTSIDE_REPO 拒绝原文 + 容器未启动）"
else
  echo "C7: FAIL（未按预期拒绝：rc=$C7_RC）"; FAILED=1
fi
echo

echo "--- C8 镜像覆盖应被策略拒绝 ---"
QA_ISOLATION_IMAGE=docker.io/library/alpine:3.20 zsh "$RUN" -- true \
  > "$SANDBOX/log/c8.out" 2>&1
C8_RC=$?
cat "$SANDBOX/log/c8.out"
echo "runner_rc=$C8_RC"
if [[ "$C8_RC" == "65" ]] && grep -q "IMAGE_OVERRIDE_DENIED" "$SANDBOX/log/c8.out"; then
  echo "C8: PASS"
else
  echo "C8: FAIL（镜像覆盖未被拒绝：rc=$C8_RC）"; FAILED=1
fi
echo

echo "--- C9 仓库根覆盖应被拒绝（否则受保护清单整体失效） ---"
QA_ISOLATION_REPO=/tmp zsh "$RUN" --rw qa/runs -- true \
  > "$SANDBOX/log/c9.out" 2>&1
C9_RC=$?
cat "$SANDBOX/log/c9.out"
echo "runner_rc=$C9_RC"
if [[ "$C9_RC" == "78" ]] && grep -q "QA_ISOLATION_REPO" "$SANDBOX/log/c9.out"; then
  echo "C9: PASS"
else
  echo "C9: FAIL（仓库根覆盖未被拒绝：rc=$C9_RC）"; FAILED=1
fi
echo

echo "=== 总判定 ==="
if [[ $FAILED -eq 0 ]]; then
  echo "VERDICT: PASS（C0–C9 共 10 项，含镜像身份核对、阳性对照与两条回归反例）"
else
  echo "VERDICT: NOT_PASS（存在 FAIL 或 VOID，逐条见上）"
fi
echo
echo "作用域与残余风险（不要省略）："
echo "1. 只覆盖**通过本包装器发起**的 shell 写入。工具级 fs_write 由 IDE 宿主进程执行，"
echo "   不经过这里（item 8），需单独闭环。"
echo "2. **没有机制强制必须走本包装器**；直接在宿主机执行命令依然可行。"
echo "3. 策略文件与校验器都在仓库里，对宿主机有写权限的 agent 可以改掉它们。"
echo "   这是护栏，不是安全边界；强制点仍是受保护分支 + CODEOWNERS + CI 复核。"
echo "=== 结束 ==="
} > "$OUT" 2>&1

echo "写入 $OUT"
grep -E "^C[0-9]+[a-z]?: |^VERDICT: " "$OUT" || true
[[ $FAILED -eq 0 ]]

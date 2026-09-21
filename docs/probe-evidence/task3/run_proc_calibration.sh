#!/bin/zsh
# 标定 proc_diff_probe.py 的检测能力。
#
# "没有新进程出现"只有在知道多短的进程会被漏掉时才有意义。
#
# 上一版的缺陷（外部复核指出，成立）：每种寿命只放一个金丝雀，一次捕获不能证明
# 后续必然捕获；而且当时把"设定间隔 0.1s"当成检测下限，实际相邻间隔约 150ms。
# 现在每种寿命重复 REPEATS 次，输出捕获率，并让 report 打印实测间隔分布（含 max 间隙）。
#
# 用法：run_proc_calibration.sh [采样目录] [间隔秒] [每种寿命重复次数]
set -u
PROBE=${0:A:h}/proc_diff_probe.py
DIR=${1:-/tmp/kiro-proc-calib}
INTERVAL=${2:-0.1}
REPEATS=${3:-5}

# 寿命（秒）：跨过预期下限的两侧，用于定位"从哪里开始漏"
DURATIONS=(1 0.3 0.15 0.1 0.05 0.02)

python3 $PROBE start --dir $DIR --interval $INTERVAL --max-seconds 300
sleep 2

typeset -A PIDS
for d in ${DURATIONS[@]}; do
  python3 $PROBE mark --dir $DIR --label "canary-${d}s-x${REPEATS}"
  list=()
  for i in $(seq 1 $REPEATS); do
    zsh -c "sleep $d" &
    list+=($!)
    wait $!
  done
  PIDS[$d]="${list[*]}"
done

sleep 1
python3 $PROBE stop --dir $DIR
sleep 2

echo "### 设定间隔: ${INTERVAL}s   每种寿命重复: ${REPEATS} 次"
python3 $PROBE report --dir $DIR | sed -n '/实际采样间隔/,/max 间隙之间/p'

for d in ${DURATIONS[@]}; do
  hit=0
  miss=0
  for pid in ${=PIDS[$d]}; do
    if python3 $PROBE report --dir $DIR --expect-pid $pid \
        | grep -q "^CONTROL_PASS"; then
      hit=$((hit+1))
    else
      miss=$((miss+1))
    fi
  done
  echo "寿命 ${d}s: 捕获 ${hit}/${REPEATS}（漏 ${miss}）  pids=${PIDS[$d]}"
done

echo "### 判读方式"
echo "捕获率 < ${REPEATS}/${REPEATS} 的最长寿命，就是本次测量**已证实会漏**的量级；"
echo "只有捕获率达到 ${REPEATS}/${REPEATS} 的寿命才可以说'该量级能被观测到'，"
echo "且该结论仅适用于本次的实测间隔分布（见上方 max 间隙）。"

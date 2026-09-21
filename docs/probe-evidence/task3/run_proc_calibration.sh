#!/bin/zsh
# Calibrate the detection floor of proc_diff_probe.py.
#
# "No new process appeared" is only meaningful if we know how short a process can
# be before the sampler stops seeing it. Spawn canaries with known lifetimes and
# report which ones were captured at the sampling interval under test.
set -u
PROBE=/Users/AI/test-agent/docs/probe-evidence/task3/proc_diff_probe.py
DIR=${1:-/tmp/kiro-proc-calib}
INTERVAL=${2:-0.1}

python3 $PROBE start --dir $DIR --interval $INTERVAL --max-seconds 60
sleep 2

python3 $PROBE mark --dir $DIR --label canary-1000ms
zsh -c 'sleep 1' &
P1000=$!
wait $P1000

python3 $PROBE mark --dir $DIR --label canary-300ms
zsh -c 'sleep 0.3' &
P300=$!
wait $P300

python3 $PROBE mark --dir $DIR --label canary-100ms
zsh -c 'sleep 0.1' &
P100=$!
wait $P100

python3 $PROBE mark --dir $DIR --label canary-20ms
zsh -c 'sleep 0.02' &
P20=$!
wait $P20

sleep 1
python3 $PROBE stop --dir $DIR
sleep 2

echo "### interval under test: ${INTERVAL}s"
for pair in "1000ms:$P1000" "300ms:$P300" "100ms:$P100" "20ms:$P20"; do
  label=${pair%%:*}
  pid=${pair##*:}
  echo "=== canary $label (pid $pid) ==="
  python3 $PROBE report --dir $DIR --expect-pid $pid | grep -E "^CONTROL_"
done

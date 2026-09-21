#!/bin/zsh
# Positive control for proc_diff_probe.py: spawn a process whose PID we know and
# confirm the sampler reports it as created during the window. If this control
# fails, a "no new process" result from the real experiment means nothing.
#
# The canary is `zsh -c 'sleep 4'` rather than a copied system binary: copying a
# platform binary out of /bin can make it unrunnable (code signing), which would
# make the control fail for a reason that has nothing to do with the sampler.
set -u
PROBE=/Users/AI/test-agent/docs/probe-evidence/task3/proc_diff_probe.py
DIR=${1:-/tmp/kiro-proc-control}

python3 $PROBE start --dir $DIR --interval 0.25 --max-seconds 60
sleep 2
python3 $PROBE mark --dir $DIR --label spawn-canary
zsh -c 'sleep 4' &
CANARY=$!
echo "CANARY_PID=$CANARY"
# prove the canary is actually alive, so a miss cannot be blamed on it dying
ps -p $CANARY -o pid,ppid,state,comm
echo "PS_ALIVE_RC=$?"
sleep 6
python3 $PROBE mark --dir $DIR --label canary-done
python3 $PROBE stop --dir $DIR
sleep 2
python3 $PROBE report --dir $DIR --expect-pid $CANARY
echo "REPORT_RC=$?"
echo "EXPECTED_CANARY_PID=$CANARY"

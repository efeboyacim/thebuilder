#!/usr/bin/env bash
# Overnight queue (plan item 10): scores the agents we already have, one simulation at a time, unattended.
# Each job writes results/overnight-<name>.json and its console output to results/overnight-<name>.log.
# A failing job does not stop the queue. Agent ids come from .freya-agents.json by slot.
#
#   bash tools/overnight.sh            # from the repo root, env.sh present
set -u
cd "$(dirname "$0")/.."
source ./env.sh
export PYTHONUTF8=1
PY=${PY:-python}            # python3 on this machine is the Windows Store stub
"$PY" -c "import sys" 2>/dev/null || PY=python3

agent() { "$PY" -c "import json,sys; print(json.load(open('.freya-agents.json'))[sys.argv[1]]['agent_id'])" "$1"; }

mkdir -p results
STATUS=results/overnight-status.txt
echo "queue start $(date -u +%FT%TZ)" > "$STATUS"

job() {     # job <name> <slot> <run_cases args...>
  local name=$1 slot=$2; shift 2
  local id; id=$(agent "$slot") || { echo "$name: no agent for slot $slot" >> "$STATUS"; return; }
  local try rc
  rm -f "results/overnight-$name.json"
  for try in 1 2 3 4 5 6; do     # a platform error (HTTP 500/503 on start) leaves no result file: wait 10 min, retry
    echo "$name try $try start $(date -u +%FT%TZ) slot=$slot agent=$id args=$*" >> "$STATUS"
    "$PY" -u tools/run_cases.py "$id" "$@" --rerun-if-slow 1 --out "results/overnight-$name.json" \
      >> "results/overnight-$name.log" 2>&1
    rc=$?
    echo "$name try $try end $(date -u +%FT%TZ) exit=$rc: $(grep -E '^[0-9]+ / [0-9]+ passed' "results/overnight-$name.log" | tail -1)" >> "$STATUS"
    [ -f "results/overnight-$name.json" ] && return
    echo "$name: no result file, retry in 600 s" >> "results/overnight-$name.log"
    sleep 600
  done
}

rm -f results/overnight-*.log
job planz-a  brief-a --brief A --reps 3
job planz-ab brief-b --brief B --reps 3
job hub-a    hub-a   --brief A --only PUB-01,PUB-02,PUB-03 --reps 3

echo "queue end $(date -u +%FT%TZ)" >> "$STATUS"

#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Sequential timed campaign on an otherwise idle host. Every stage runs alone.
# usage: final_campaign.sh WORK   (WORK holds host.json, prep/, compose/ and the run_*.sh wrappers)
set -u
W=$(realpath "$1"); cd "$W"
H=/home/user/RustEDA/integration/ibex_host_benchmark
log() { echo "$(date -Is) $*" | tee -a "$W/final-campaign.log"; }
idle() { # wait until the 1-minute load average drops below 0.5
  for _ in $(seq 1 120); do awk '{exit !($1 < 0.5)}' /proc/loadavg && return; sleep 10; done; }

log "native: start"; idle
./run_native.sh native-final > native-final.console 2>&1; log "native: exit $?"
idle; ./run_logic.sh native-final > native-final.logic.console 2>&1; log "native logic: exit $?"

log "librelane: start"; idle
/opt/eda/recon/measured_run.sh librelane ll-final/measure 10240 -- ll-final/run.sh > ll-final/console.log 2>&1
log "librelane: exit $?"

python3 - "$W" <<'PY'
import json, sys, glob, os
w = sys.argv[1]
final = f"{w}/ll-final/runs/final/final"
synth = sorted(glob.glob(f"{w}/ll-final/runs/final/*-yosys-synthesis/ibex_sram_bench.nl.v"))[-1]
layout = dict(gds=f"{final}/gds/ibex_sram_bench.gds", **{"def": f"{final}/def/ibex_sram_bench.def"},
              netlist=f"{final}/nl/ibex_sram_bench.nl.v", powered_netlist=f"{final}/pnl/ibex_sram_bench.pnl.v",
              synthesis_netlist=synth)
json.dump(layout, open(f"{w}/layout-librelane.json", "w"), indent=2)
o = f"{w}/native-final/output"
layout = dict(gds=f"{o}/backend/ibex_sram_bench.gds", **{"def": f"{o}/logs/be_def.stdout"},
              netlist=f"{o}/logs/be_netlist.stdout", powered_netlist=f"{o}/logs/be_powered.stdout",
              synthesis_netlist=f"{o}/frontend/mapped.v")
json.dump(layout, open(f"{w}/layout-native.json", "w"), indent=2)
PY
idle; ./run_signoff.sh signoff-librelane layout-librelane.json signoff-librelane > signoff-librelane.console 2>&1
log "librelane shared signoff: exit $?"
idle; ./run_signoff.sh signoff-native layout-native.json signoff-native > signoff-native.console 2>&1
log "native shared signoff: exit $?"

idle; "$H/synth_bench.sh" "$W/bench-synth" "$W/prep" "$(ls -d "$W"/ll-final/runs/final/*-yosys-synthesis | tail -1)" 3 \
  > bench-synth.console 2>&1; log "synthesis bench: exit $?"
log "campaign: done"

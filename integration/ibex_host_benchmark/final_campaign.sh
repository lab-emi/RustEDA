#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Sequential timed campaign on an otherwise idle host; every stage runs alone.
#   1. RustEDA native producer (prefix manifest), then the shared gate scripts on its layout.
#   2. LibreLane 3.0.17 Classic flow through Checker.XOR, then the same gate scripts.
#   3. Module head-to-heads: synthesis, extraction, timing, GDS, SPICE.
# usage: final_campaign.sh WORK   (WORK holds host.json, prep/, compose/ and the run_*.sh wrappers)
set -u
W=$(realpath "$1"); cd "$W"
H=/home/user/RustEDA/integration/ibex_host_benchmark
PY=/usr/local/bin/python3
log() { echo "$(date -Is) $*" | tee -a "$W/final-campaign.log"; }
idle() { # start a stage only when the 1-minute load average is below 0.5 (max 20 min wait)
  for _ in $(seq 1 120); do awk '{exit !($1 < 0.5)}' /proc/loadavg && break; sleep 10; done
  log "idle check: loadavg $(cut -d' ' -f1-3 /proc/loadavg)"; }

idle; log "native producer: start (free disk $(df -h / | awk 'NR==2{print $4}'))"
./run_native.sh native-final > native-final.console 2>&1; log "native producer: exit $?"
$PY - "$W" <<'PY'
import json, sys
w = sys.argv[1]; o = f"{w}/native-final/output"
json.dump({"gds": f"{o}/backend/ibex_sram_bench.gds", "def": f"{o}/logs/be_def.stdout",
           "netlist": f"{o}/logs/be_netlist.stdout", "powered_netlist": f"{o}/logs/be_powered.stdout",
           "synthesis_netlist": f"{o}/frontend/mapped.v"}, open(f"{w}/layout-native.json", "w"), indent=2)
PY
idle; ./run_signoff.sh signoff-native layout-native.json signoff-native > signoff-native.console 2>&1
log "native shared gates: exit $?"

mkdir -p ll-final
$PY - "$W" <<'PY'
import json, sys
w = sys.argv[1]
c = json.load(open(f"{w}/prep/config.json")); c["DRT_THREADS"] = 4
# The historical reference replaced LibreLane's post-GRT design repair with a RustEDA-authored
# DRV ECO loop. Without that loop, enable LibreLane's own built-in repair step instead.
c["RUN_POST_GRT_DESIGN_REPAIR"] = True
json.dump(c, open(f"{w}/ll-final/config.json", "w"), indent=1)
PY
cat > ll-final/run.sh <<EOF
#!/bin/bash
source /opt/eda/env.sh
cd $W/ll-final
exec librelane --manual-pdk --pdk-root ../prep/pdk --pdk ihp-sg13g2 --scl ez130_8t --run-tag final --to Checker.XOR config.json
EOF
chmod +x ll-final/run.sh
idle; log "librelane: start"
/opt/eda/recon/measured_run.sh librelane ll-final/measure 10240 -- ll-final/run.sh > ll-final/console.log 2>&1
log "librelane: exit $?"
$PY - "$W" <<'PY'
import json, sys, glob
w = sys.argv[1]; r = f"{w}/ll-final/runs/final"
steps = sorted(glob.glob(f"{r}/[0-9]*-*/state_out.json"), key=lambda p: int(p[len(r) + 1:].split("-", 1)[0]))
s = json.load(open(steps[-1]))  # the Checker.XOR state carries the final views
synth = sorted(glob.glob(f"{r}/*-yosys-synthesis/ibex_sram_bench.nl.v"))[-1]
json.dump({"gds": s["gds"], "def": s["def"], "netlist": s["nl"], "powered_netlist": s["pnl"],
           "synthesis_netlist": synth}, open(f"{w}/layout-librelane.json", "w"), indent=2)
PY
idle; ./run_signoff.sh signoff-librelane layout-librelane.json signoff-librelane > signoff-librelane.console 2>&1
log "librelane shared gates: exit $?"

idle; log "synthesis bench: start"
"$H/synth_bench.sh" "$W/bench-synth" "$W/prep" "$(ls -d "$W"/ll-final/runs/final/*-yosys-synthesis | tail -1)" 3 \
  > bench-synth.console 2>&1; log "synthesis bench: exit $?"
idle; log "module bench: start"
$PY "$H/module_bench.py" --output native-final/output --out bench-modules --reps 3 --threads 2,4 \
  > bench-modules.console 2>&1; log "module bench: exit $?"
idle; log "spice bench: start"
rm -rf bench-spice && mkdir -p bench-spice && $PY "$H/spef_spice_decks.py" native-final/output/backend/native.nom.spef --out bench-spice/decks \
  --top-nets 8 --bulk 10 --bulk 40 > bench-spice/decks.log 2>&1
(cd bench-spice && /home/user/RustEDA/.work/target/RustSpice/release/rustspice bench-suite --cases decks --filter ibex_ \
  --no-fuzz --reps 7 --ngspice /opt/eda/ngspice/bin/ngspice --json suite.json --report suite.md --artifacts artifacts \
  > suite.console 2>&1); log "spice bench: exit $?"
log "campaign: done"

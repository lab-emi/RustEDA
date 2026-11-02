#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Standalone synthesis head-to-head on the prepared Ibex RTL: RustSys (the native
# flow's exact workspace sequence, and its single-command direct mode) against
# Yosys running LibreLane 3.0.17's own synthesis script with the same config.
# One warm-up and REPS measured runs per contender, interleaved, under GNU time.
# usage: synth_bench.sh OUT PREP LIBRELANE_SYNTH_STEP_DIR [REPS]
set -eu
OUT=$(realpath -m "$1"); PREP=$(realpath "$2"); LLSTEP=$(realpath "$3"); REPS=${4:-3}
T=/opt/eda/time/bin/time
B=/home/user/RustEDA/.work/target
RUSTSYS=$B/RustSys/release/rustsys; RUSTEDA=$B/RustSTA/release/rusteda
EZ=/opt/eda/ez130_8t_v1p1/lib
SRAM=/opt/eda/src/IHP-Open-PDK/ihp-sg13g2/libs.ref/sg13g2_sram/lib/RM_IHPSG13_1P_1024x32_c2_bm_bist_typ_1p20V_25C.lib
RTL=$PREP/rtl/design.v
mkdir -p "$OUT"
rustsys_flow() { # $1 dir: the frontend's init/import/synth/export sequence, two threads as in the recipe
  rm -rf "$1"; mkdir -p "$1"
  $T -v -o "$1/time.txt" bash -c "
    $RUSTEDA init -w $1/ws --format json --threads 2 >/dev/null &&
    $RUSTEDA import -w $1/ws --base-rev rev0 --threads 2 --format json --lib $EZ/ez130_8t_tt_1p20v_25c.lib --corner tt --lib $SRAM --corner tt --rtl $RTL --top ibex_sram_bench >/dev/null &&
    $RUSTSYS --workspace $1/ws synth --base-rev rev1 --threads 2 >/dev/null &&
    $RUSTEDA export -w $1/ws --rev rev2 --netlist $1/mapped.v --threads 2 >/dev/null"
}
rustsys_direct() { # $1 dir: one process, all CPUs
  rm -rf "$1"; mkdir -p "$1"
  $T -v -o "$1/time.txt" $RUSTSYS --threads 0 --progress off synth --top ibex_sram_bench \
    --lib $EZ/ez130_8t_tt_1p20v_25c.lib --lib $SRAM --out "$1/mapped.v" --report "$1/report.json" "$RTL" >/dev/null 2>"$1/stderr"
}
yosys_librelane() { # $1 dir: LibreLane's pyosys synthesis script and the run's own step config
  rm -rf "$1"; mkdir -p "$1"
  python3 - "$LLSTEP/config.json" "$1/config.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); json.dump(c, open(sys.argv[2], "w"))
PY
  cp "$LLSTEP/extra.json" "$1/extra.json"
  (cd "$1" && PYTHONPATH=/opt/eda/py314-tools/lib/python3.14/site-packages:/opt/eda/py314-extra \
    $T -v -o "$1/time.txt" /opt/eda/yosys/bin/yosys -y \
    /opt/eda/librelane-venv/lib/python3.13/site-packages/librelane/scripts/pyosys/synthesize.py -- \
    --config-in "$1/config.json" --extra-in "$1/extra.json" --output "$1/mapped.v" >"$1/yosys.log" 2>&1)
}
for i in $(seq 0 "$REPS"); do
  for c in rustsys_flow rustsys_direct yosys_librelane; do
    $c "$OUT/$c-$i"
    printf '%s\t%s\t%s\t%s\n' "$c" "$i" "$(grep 'Elapsed (wall' "$OUT/$c-$i/time.txt" | awk '{print $NF}')" \
      "$(grep 'Maximum resident' "$OUT/$c-$i/time.txt" | awk '{print $NF}')" | tee -a "$OUT/runs.tsv"
  done
done

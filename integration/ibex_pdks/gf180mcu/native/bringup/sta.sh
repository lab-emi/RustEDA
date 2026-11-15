#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Static timing of a native layout with RustSTA on its native SPEF at the TT and SS corners (the cell
# library first in each corner, the SRAM Liberty second), one SPEF for both corners as on IHP.
# usage: sta.sh RUSTEDA RUSTSTA PDK NETLIST SPEF SDC OUTDIR      (prints a JSON summary per corner)
set -euo pipefail
rusteda=$1 ruststa=$2 pdk=$3 netlist=$4 spef=$5 sdc=$6 out=$7
lib=gf180mcu_fd_sc_mcu7t5v0 ram=gf180mcu_fd_ip_sram__sram512x8m8wm1
sc="$pdk/libs.ref/$lib/lib" ip="$pdk/libs.ref/gf180mcu_fd_ip_sram/lib"
[ ! -e "$out" ] || { echo "$out exists" >&2; exit 1; }
"$rusteda" init -w "$out" --format json --threads 2 > /dev/null
"$rusteda" import -w "$out" --base-rev rev0 --threads 2 --format json --max-spef-mib 256 --max-rc-name-mib 64 \
  --max-rc-nodes 2000000 --max-rc-elements 4000000 --max-rc-nets 100000 \
  --lib "$sc/${lib}__tt_025C_5v00.lib" --corner tt --lib "$ip/${ram}__tt_025C_5v00.lib" --corner tt \
  --spef "$spef" --spef-corner tt \
  --lib "$sc/${lib}__ss_125C_4v50.lib" --corner ss --lib "$ip/${ram}__ss_125C_4v50.lib" --corner ss \
  --spef "$spef" --spef-corner ss --netlist "$netlist" --sdc "$sdc" > "$out.import.json"
"$ruststa" run -w "$out" --base-rev rev1 --threads 4 --concurrent-corners --max-state-gib 2 --max-paths 32 \
  --max-limit-rows 100000 --require-wire-model spef_rc --format json > "$out.run.json"
for c in tt ss; do
  "$ruststa" report -w "$out" --corner "$c" --section summary --section limits --format json > "$out.$c.json"
done
python3 - "$out" <<'PY'
import json, sys
out = sys.argv[1]
res = {}
for c in ("tt", "ss"):
    d = json.load(open(f"{out}.{c}.json"))
    res[c] = d.get("metrics") or d
print(json.dumps(res, indent=1)[:4000])
PY

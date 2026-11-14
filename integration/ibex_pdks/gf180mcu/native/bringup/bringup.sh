#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Bring-up driver of the native physical flow on GF180MCU (gf180mcu_fd_sc_mcu7t5v0, 5 V corners).
#
# It runs the engine commands of the composed flow in manifest order (synthesis, import, placement, clock
# tree, routing, antenna repair, extraction, exports) without the timing ECO rounds, for one of two designs:
#   micro  two SRAM macros and about 750 cells (bringup/micro), the macro and supply test object
#   ibex   the benchmark design of a prepared workspace (integration/ibex_pdks/prepare.py)
# Every path is an argument; nothing is read from the environment. Each stage writes its JSON report and
# its wall time under <out>/logs. Heavy stages (routing, extraction of the Ibex design) are the caller's to
# serialize with other jobs on the host.
set -euo pipefail

usage() {
  cat <<'EOF'
usage: bringup.sh --design micro|ibex --out DIR --native DIR --pdk DIR --rustroad BIN --rusteda BIN
                  --rustsys BIN [--prep DIR] [--time BIN] [--threads N] [--lib-cache DIR]
                  [--from STAGE] [--to STAGE]
stages: synth floorplan import place cts route antenna extract export
  --native   the profile directory (integration/ibex_pdks/gf180mcu/native)
  --pdk      the PDK tree that holds libs.ref and libs.tech
  --prep     prepared design workspace (rtl/design.v, config.json); required for --design ibex
EOF
  exit 2
}

design= out= native= pdk= prep= rustroad= rusteda= rustsys= lib_cache=
time_bin=/usr/bin/time threads=4 from=synth to=export
while [ $# -gt 0 ]; do
  case "$1" in
    --design) design=$2 ;; --out) out=$2 ;; --native) native=$2 ;; --pdk) pdk=$2 ;; --prep) prep=$2 ;;
    --rustroad) rustroad=$2 ;; --rusteda) rusteda=$2 ;; --rustsys) rustsys=$2 ;; --time) time_bin=$2 ;;
    --threads) threads=$2 ;; --lib-cache) lib_cache=$2 ;; --from) from=$2 ;; --to) to=$2 ;;
    *) usage ;;
  esac
  shift 2
done
for v in design out native pdk rustroad rusteda rustsys; do [ -n "${!v}" ] || usage; done
[ "$design" = micro ] || [ "$design" = ibex ] || usage
[ "$design" = micro ] || [ -n "$prep" ] || usage

lib=gf180mcu_fd_sc_mcu7t5v0
ram=gf180mcu_fd_ip_sram__sram512x8m8wm1
sc="$pdk/libs.ref/$lib"
ip="$pdk/libs.ref/gf180mcu_fd_ip_sram"
tlef="$sc/techlef/${lib}__nom.tlef"
bu="$native/bringup"
mkdir -p "$out/logs"
[ -n "$lib_cache" ] || lib_cache="$out/lib-cache"

stages=(synth floorplan import place cts route antenna extract export)
index() { local i; for i in "${!stages[@]}"; do [ "${stages[$i]}" = "$1" ] && { echo "$i"; return; }; done; usage; }
first=$(index "$from") last=$(index "$to")
active() { local i; i=$(index "$1"); [ "$i" -ge "$first" ] && [ "$i" -le "$last" ]; }

# run <step> <stdout file> <command...>: wall time and peak memory go to logs/<step>.time
run() {
  local step=$1 dest=$2
  shift 2
  echo "== $step" >&2
  "$time_bin" -f '{"step":"'"$step"'","wall_s":%e,"user_s":%U,"sys_s":%S,"max_rss_kb":%M,"exit":%x}' \
    -o "$out/logs/$step.time" "$@" > "$dest" 2> "$out/logs/$step.stderr" || {
    echo "$step failed:" >&2
    tail -c 2000 "$dest" >&2
    tail -c 2000 "$out/logs/$step.stderr" >&2
    cat "$out/logs/$step.time" >&2
    exit 1
  }
  cat "$out/logs/$step.time" >&2
}

case "$design" in
  micro) top=micro_sram rtl="$bu/micro/micro_sram.v" config="$bu/micro/config.json" route_max=40000000 ;;
  ibex) top=ibex_sram_bench rtl="$out/design.native.v" config="$prep/config.json" route_max=80000000 ;;
esac
road="$out/road"
rr=("$rustroad" --workspace "$road" --threads "$threads" --max-expansions 4000000)

if active synth; then
  if [ "$design" = ibex ]; then
    # RustSys refuses inout ports on an opaque macro: drop the supply ports of the black-box declaration
    # (the physical flow takes the supply pins from the macro LEF).
    python3 "$bu/strip_blackbox_supply.py" "$prep/rtl/design.v" "$rtl"
  fi
  dont_use=()
  while IFS= read -r glob; do dont_use+=(--dont-use "$glob"); done < <(
    python3 -c 'import json,sys; [print(g) for g in json.load(open(sys.argv[1]))["synthesis"]["dont_use"]]' \
      "$native/profile.json")
  [ ! -e "$out/synth" ] || { echo "$out/synth exists" >&2; exit 1; }
  run synth_init "$out/logs/synth_init.json" "$rusteda" init -w "$out/synth" --format json --threads 2
  run synth_import "$out/logs/synth_import.json" "$rusteda" import -w "$out/synth" --base-rev rev0 \
    --threads 2 --format json --lib-cache "$lib_cache" \
    --lib "$sc/lib/${lib}__tt_025C_5v00.lib" --corner tt \
    --lib "$ip/lib/${ram}__tt_025C_5v00.lib" --corner tt --rtl "$rtl" --top "$top"
  run synth "$out/logs/synth.txt" "$rustsys" --workspace "$out/synth" synth --base-rev rev1 \
    --threads "$threads" "${dont_use[@]}"
  python3 -c 'import json,sys; json.dump(json.load(open(sys.argv[1]))["cell_buses"], open(sys.argv[2],"w"))' \
    "$native/interfaces.json" "$out/cell-buses.json"
  run synth_export "$out/logs/synth_export.txt" "$rusteda" export -w "$out/synth" --rev rev2 \
    --netlist "$out/mapped.v" --cell-buses "$out/cell-buses.json" --threads 2
fi

if active floorplan; then
  # Combined cell LEF: the library LEF without its END LIBRARY, the macro's MACRO block, END LIBRARY.
  python3 "$bu/combine_lef.py" "$sc/lef/$lib.lef" "$ip/lef/$ram.lef" "$out/cells.lef"
  if [ "$design" = ibex ]; then
    cp "$native/floorplan.json" "$out/floorplan.json"
    cp "$native/physical.json" "$out/physical.profile.json"
  else
    python3 "$bu/make_floorplan.py" --name "$top" --config "$config" --macro-lef "$ip/lef/$ram.lef" \
      --netlist "$out/mapped.v" --margin-um 28 --out-floorplan "$out/floorplan.json" \
      --out-io-plan "$out/io-plan.json" > "$out/logs/floorplan.json"
    python3 -c 'import json,sys
p=json.load(open(sys.argv[1])); p["io_plan"]=json.load(open(sys.argv[2]))
json.dump(p,open(sys.argv[3],"w"),indent=1)' "$native/physical.json" "$out/io-plan.json" "$out/physical.profile.json"
  fi
  # The composed flow binds the clock root in `rusteda frontend-physical`.
  python3 "$bu/clock_root.py" "$out/mapped.v" "$out/physical.profile.json" "$out/physical.json" \
    > "$out/logs/clock_root.json"
  cp "$native/routing.json" "$out/routing.json"
fi

if active import; then
  [ ! -e "$road/HEAD" ] || { echo "$road has revisions" >&2; exit 1; }
  run import "$out/logs/import.json" "${rr[@]}" import-pdk --base-rev rev0 --tech-lef "$tlef" \
    --cell-lef "$out/cells.lef" --netlist "$out/mapped.v" --floorplan "$out/floorplan.json" \
    --physical-config "$out/physical.json"
fi
if active place; then
  run place "$out/logs/place.json" "${rr[@]}" place --base-rev rev1
fi
if active cts; then
  run cts "$out/logs/cts.json" "${rr[@]}" clock-tree --base-rev rev2 --sdc "$bu/cts.sdc"
fi
if active route; then
  run route "$out/logs/route.json" "$rustroad" --workspace "$road" --threads "$threads" \
    --max-expansions "$route_max" route --base-rev rev3 --scope all --config "$out/routing.json"
fi
if active antenna; then
  run antenna_check0 "$out/antenna-rev4.json" "${rr[@]}" check-antenna
  # Layer jumpers on the violating nets; a clean report leaves the routed revision as the final one.
  if python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("violations") else 1)' \
      "$out/antenna-rev4.json"; then
    run antenna_repair "$out/logs/antenna_repair.json" "${rr[@]}" repair-antenna --base-rev rev4 \
      --antenna-report "$out/antenna-rev4.json"
    run antenna_check1 "$out/antenna-final.json" "${rr[@]}" check-antenna
  else
    cp "$out/antenna-rev4.json" "$out/antenna-final.json"
  fi
fi
if active extract; then
  run extract "$out/logs/extract.json" "$rustroad" --workspace "$road" --threads "$threads" \
    --max-expansions 8000000 extract --rc-profile "$native/rc-nom.json" --corner nom \
    --out "$out/native.nom.spef" --max-spef-mib 256 --extract-memory-mib 3584 \
    --max-extract-shapes 4000000 --max-rc-nodes 2000000 --max-rc-elements 30000000 \
    --max-coupling-caps 10000000 --max-extract-work 100000000000 --max-extract-candidates 65536 \
    --min-coupling-ff 0.1
fi
if active export; then
  run inspect "$out/logs/inspect.json" "${rr[@]}" inspect
  run export_def "$out/$top.def" "${rr[@]}" export-def
  run export_netlist "$out/$top.v" "${rr[@]}" export-verilog
  run export_powered "$out/$top.powered.v" "${rr[@]}" export-verilog --include-power
  run bundle_gds "$out/logs/bundle_gds.json" "$rustroad" bundle-cell-gds --gds "$sc/gds/$lib.gds" \
    --gds "$ip/gds/$ram.gds" --out "$out/combined-cells.gds"
  run export_gds "$out/logs/export_gds.json" "${rr[@]}" export-gds --cell-gds "$out/combined-cells.gds" \
    --layer-map "$native/gds-map.json" --out "$out/$top.gds" --merge-shapes rectangles --vias flat
  (cd "$out" && sha256sum "$top.def" "$top.v" "$top.powered.v" "$top.gds" \
    $([ -e native.nom.spef ] && echo native.nom.spef) > SHA256SUMS)
fi

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
                  [--from STAGE] [--to STAGE] [--mapped NETLIST] [--rounds N] [--diode-margin F]
stages: synth floorplan import place cts route antenna diodes extract export
  --native   the profile directory (integration/ibex_pdks/gf180mcu/native)
  --pdk      the PDK tree that holds libs.ref and libs.tech
  --prep     prepared design workspace (rtl/design.v, config.json); required for --design ibex
  --mapped   start from this mapped netlist instead of synthesizing (an ECO round's output, e.g. the
             netlist `rustroad antenna-eco` wrote with diodes on the nets a routed layout violated)
  --rounds   antenna rounds (default 1). When the antenna session leaves violated nets, the diodes stage
             writes mapped-next.v with diodes on every net the native check rates above (1 - margin) of
             its limit, and the next round rebuilds the layout from it in <out>/round<k+1>
  --diode-margin  safety margin of that check and of the diode sizing (default 0.5, at most 0.5)
EOF
  exit 2
}

design= out= native= pdk= prep= rustroad= rusteda= rustsys= lib_cache= mapped=
time_bin=/usr/bin/time threads=4 from=synth to=export rounds=1 round=1 diode_margin=0.5
args=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --design) design=$2 ;; --out) out=$2 ;; --native) native=$2 ;; --pdk) pdk=$2 ;; --prep) prep=$2 ;;
    --rustroad) rustroad=$2 ;; --rusteda) rusteda=$2 ;; --rustsys) rustsys=$2 ;; --time) time_bin=$2 ;;
    --threads) threads=$2 ;; --lib-cache) lib_cache=$2 ;; --from) from=$2 ;; --to) to=$2 ;;
    --mapped) mapped=$2 ;; --rounds) rounds=$2 ;; --round) round=$2 ;; --diode-margin) diode_margin=$2 ;;
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

stages=(synth floorplan import place cts route antenna diodes extract export)
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
  ibex) top=ibex_sram_bench rtl="$prep/rtl/design.v" config="$prep/config.json" route_max=80000000 ;;
esac
road="$out/road"
rr=("$rustroad" --workspace "$road" --threads "$threads" --max-expansions 4000000)

if active synth && [ -n "$mapped" ]; then
  cp "$mapped" "$out/mapped.v"
  python3 -c 'import json,sys; json.dump(json.load(open(sys.argv[1]))["cell_buses"], open(sys.argv[2],"w"))' \
    "$native/interfaces.json" "$out/cell-buses.json"
elif active synth; then
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
  # The grouped native session of the composed flow (step be_antenna_repair): check, diode-free layer
  # jumpers on the violating nets, re-check, at most three rounds; the session's budgets are the
  # profile's antenna-group-policy.json.
  python3 - "$road" "$rustroad" "$native/antenna-group-policy.json" "$out/antenna-inputs.json" <<'PY'
import hashlib, json, os, sys
road, tool, policy, dst = sys.argv[1:]
data = open(tool, "rb").read()
json.dump({
    "schema": "rusteda.native-antenna-inputs.v2",
    "design_workspace": os.path.abspath(road),
    "initial_revision": "rev4",
    "tool": {"path": os.path.abspath(tool), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
    "limits": {"threads": 4, "max_rounds": 3, "max_selected_nets": 100000, "max_work": 200000000,
               "max_expansions": 8000000, "deadline_seconds": 1800},
    "grouping": json.load(open(policy)),
}, open(dst, "w"), indent=2)
PY
  run antenna_session "$out/logs/antenna_session.json" "$rustroad" antenna-session \
    --inputs "$out/antenna-inputs.json" --out-dir "$out/antenna-repair"
  # The nets the session left: unresolved supported nets and the nets it does not repair because they
  # touch a macro pin. Their selected check is the input of the next `antenna-eco` round (--mapped).
  python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
json.dump(sorted(set(d.get("unresolved_supported_nets",[])+d.get("excluded_macro_touch",[]))),open(sys.argv[2],"w"))' \
    "$out/antenna-repair/native-session.json" "$out/antenna-left-nets.json"
  if [ "$(cat "$out/antenna-left-nets.json")" != "[]" ]; then
    run antenna_left "$out/antenna-left.json" "${rr[@]}" --max-rows 1000000 check-antenna \
      --selected-nets "$out/antenna-left-nets.json"
  fi
fi
if active diodes && [ -s "$out/antenna-left.json" ] && python3 -c 'import json,sys
sys.exit(0 if json.load(open(sys.argv[1]))["status"] == "subset_violated" else 1)' "$out/antenna-left.json"; then
  # Diodes on every net the session's native reports rate above (1 - margin) of its limit, sized
  # against the same margin, so that the re-routed layout of the next round keeps headroom. Nets that
  # touch a macro pin are not repaired by the session's jumpers; diodes are their only repair.
  # Nets the layout generated (clock tree) are not nets of the mapped netlist and stay with the session.
  python3 - "$out/antenna-repair/native-reports" "$out/antenna-left-nets.json" "$diode_margin" \
      "$out/mapped.v" "$out/diode-nets.json" <<'PY'
import glob, json, os, re, sys
reports, left, margin, netlist, dst = sys.argv[1:]
declared = set()
for line in open(netlist):
    m = re.match(r"\s*(?:input|output|wire)\s+(?:wire\s+)?(?:\[(\d+):(\d+)\]\s*)?(\\\S+|[A-Za-z_]\w*)", line)
    if not m:
        continue
    name = m.group(3).lstrip("\\")
    if m.group(1) is not None:
        hi, lo = int(m.group(1)), int(m.group(2))
        declared.update("%s[%d]" % (name, i) for i in range(min(hi, lo), max(hi, lo) + 1))
    else:
        declared.add(name)
nets = set(json.load(open(left)))
for path in sorted(glob.glob(os.path.join(reports, "final-group-*-header.json"))):
    for net in json.load(open(path))["nets"]:
        if net["worst_ratio_over_limit"] > 1.0 - float(margin):
            nets.add(net["net"])
json.dump(sorted(n for n in nets if n in declared), open(dst, "w"))
PY
  run diode_check "$out/diode-check.json" "${rr[@]}" --max-rows 1000000 check-antenna \
    --selected-nets "$out/diode-nets.json" --safety-margin "$diode_margin"
  run antenna_eco "$out/logs/antenna_eco.json" "$rustroad" antenna-eco --netlist "$out/mapped.v" \
    --antenna-report "$out/diode-check.json" --delay-cells "$bu/diode-profile.json" \
    --out "$out/mapped-next.v" --plan-out "$out/antenna-eco-plan.json"
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

# Next antenna round: rebuild from the netlist with the new diodes.
if [ -e "$out/mapped-next.v" ] && [ "$rounds" -gt 1 ] && active diodes; then
  next=()
  skip=0
  for a in "${args[@]}"; do
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    case "$a" in --out|--mapped|--from|--rounds|--round) skip=1; continue ;; esac
    next+=("$a")
  done
  # A superseded round keeps its logs, reports, netlists and DEF; its workspace and streams go.
  rm -rf "$road" "$out/synth" "$out/antenna-repair/native-reports" "$out/$top.gds" "$out/combined-cells.gds" \
    "$out/native.nom.spef"
  exec "$0" "${next[@]}" --out "$out/round$((round + 1))" --mapped "$out/mapped-next.v" --from synth \
    --rounds $((rounds - 1)) --round $((round + 1))
fi

#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Minimal wrappers around the PDK's decks for the bring-up (the shared gate scripts replace them):
#   drc      KLayout gf180mcu.drc with the option set of the PDK's LibreLane configuration
#            (feol, beol, dummy, dummy_no_sub_prev, offgrid, conn_drc, wedge, run_mode deep)
#   antenna  KLayout rule_decks/antenna.drc, run_mode deep
#   lvs      Magic extraction with the SRAM as an abstract (LEF) black box, then Netgen against the
#            powered netlist on top of the cell library's SPICE
# usage: run_checks.sh --gds GDS --top CELL --pdk DIR --out DIR --checks drc,antenna,lvs
#                      [--powered V] [--threads N] [--time BIN]
set -euo pipefail
gds= top= pdk= out= checks=drc,antenna powered= threads=2 time_bin=/usr/bin/time
while [ $# -gt 0 ]; do
  case "$1" in
    --gds) gds=$2 ;; --top) top=$2 ;; --pdk) pdk=$2 ;; --out) out=$2 ;; --checks) checks=$2 ;;
    --powered) powered=$2 ;; --threads) threads=$2 ;; --time) time_bin=$2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
  shift 2
done
for v in gds top pdk out; do [ -n "${!v}" ] || { echo "missing --$v" >&2; exit 2; }; done
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
deck="$pdk/libs.tech/klayout/tech/drc"
timed() { local name=$1; shift; "$time_bin" -f '{"check":"'"$name"'","wall_s":%e,"max_rss_kb":%M,"exit":%x}' -o "$out/$name.time" "$@"; }
case ",$checks," in *,drc,*)
  timed drc klayout -b -r "$deck/gf180mcu.drc" -rd input="$gds" -rd topcell="$top" \
    -rd report="$out/drc.lyrdb" -rd feol=true -rd beol=true -rd dummy=true -rd dummy_no_sub_prev=true \
    -rd offgrid=true -rd conn_drc=true -rd wedge=true -rd run_mode=deep -rd thr="$threads" \
    > "$out/drc.log" 2>&1 || { tail -5 "$out/drc.log" >&2; cat "$out/drc.time" >&2; exit 1; }
  python3 "$here/count_markers.py" "$out/drc.lyrdb" > "$out/drc.json"
  cat "$out/drc.time" ;;
esac
case ",$checks," in *,antenna,*)
  timed antenna klayout -b -r "$deck/rule_decks/antenna.drc" -rd input="$gds" -rd topcell="$top" \
    -rd report="$out/antenna.lyrdb" -rd run_mode=deep -rd thr="$threads" \
    > "$out/antenna.log" 2>&1 || { tail -5 "$out/antenna.log" >&2; cat "$out/antenna.time" >&2; exit 1; }
  python3 "$here/count_markers.py" "$out/antenna.lyrdb" > "$out/antenna.json"
  cat "$out/antenna.time" ;;
esac
case ",$checks," in *,lvs,*)
  [ -n "$powered" ] || { echo "lvs needs --powered" >&2; exit 2; }
  ram=gf180mcu_fd_ip_sram__sram512x8m8wm1
  lib=gf180mcu_fd_sc_mcu7t5v0
  mkdir -p "$out/lvs"
  export PDK_ROOT=$(dirname "$pdk")
  GDS="$gds" CELL="$top" OUTDIR="$out/lvs" MACRO_LEF="$pdk/libs.ref/gf180mcu_fd_ip_sram/lef/$ram.lef" \
    timed lvs_extract magic -dnull -noconsole -rcfile "$pdk/libs.tech/magic/gf180mcuD.magicrc" \
    "$here/magic_extract_blackbox.tcl" > "$out/lvs/magic.log" 2>&1 < /dev/null
  grep -q RR_EXT_DONE "$out/lvs/magic.log" || { tail -20 "$out/lvs/magic.log" >&2; exit 1; }
  LAYOUT="$out/lvs/$top.spice" TOP="$top" REFV="$powered" MACRO_BB="$here/sram_blackbox.v" \
    STDSPICE="$pdk/libs.ref/$lib/spice/$lib.spice" SETUP="$pdk/libs.tech/netgen/gf180mcuD_setup.tcl" \
    OUT="$out/lvs/comp.out" timed lvs_netgen netgen -batch source "$here/netgen_lvs_blackbox.tcl" \
    > "$out/lvs/netgen.log" 2>&1 < /dev/null || true
  tail -4 "$out/lvs/comp.out" | tee "$out/lvs/verdict.txt"
  cat "$out/lvs_extract.time" "$out/lvs_netgen.time" ;;
esac

#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Rebuild the EZ130 8T v1.1 SG13G2 library tree from public mirrors when the ETH
# archive host is unreachable. Six of the eight contract files come out
# byte-identical; the SG13G2 tech LEF and the cell GDS are reconstructed and are
# NOT the pinned archive members. `reconstructed.sha256` lists every result.
#
# Sources (public, Apache-2.0):
#   - github.com/michaelk99/IHP__MPC9724 ip/ez130_8t_v1p1: an EZ130 v1.1 pre-release
#     whose files differ only in a citation placeholder (arXiv:XXXX.XXXXX), plus
#     the OpenBLAU SG13CMOS5L retarget of the tech LEF and a 189/4 boundary copy in
#     the GDS (documented in its doc/sg13cmos5l_adaptation.md);
#   - github.com/IHP-GmbH/IHP-Open-PDK at 3b59002e (2025-06-20): the SG13G2 stdcell
#     tech LEF supplying the removed Metal5/Via4/TopVia2/TopMetal2 definitions.
# The pulp-platform/croc commit e0bf555 independently carries the TT/FF Liberty and
# the cell LEF with the pinned hashes.
#
# usage: restore.sh MIRROR_IP_DIR IHP_TECH_LEF_3B59002E KLAYOUT OUT_DIR
set -eu
SRC=$(realpath "$1"); IHP_TECH=$(realpath "$2"); KLAYOUT=$3; OUT=$(realpath -m "$4")
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"/{lib,lef,gds,cdl,verilog}
for f in lib/ez130_8t_tt_1p20v_25c.lib lib/ez130_8t_ss_1p08v_125c.lib lib/ez130_8t_ff_1p32v_m40c.lib \
         cdl/ez130_8t.cdl verilog/ez130_8t.v verilog/ez130_8t_pwr.v lef/ez130_8t.lef; do
  sed -e 's/arXiv:XXXX\.XXXXX/arXiv:2609.29965/' -e 's#https://arxiv.org/abs/????#https://arxiv.org/abs/2609.29965#' \
    "$SRC/$f" > "$OUT/$f"
done
cp "$SRC"/LICENSE "$SRC"/NOTICE "$SRC"/README "$OUT"/
python3 "$HERE/rebuild_tech_lef.py" "$SRC/lef/ez130_tech.lef" "$IHP_TECH" "$OUT/lef/ez130_sg13g2_tech.lef"
QT_QPA_PLATFORM=offscreen "$KLAYOUT" -b -r "$HERE/strip_prboundary.py" -rd src="$SRC/gds/ez130_8t.gds" -rd out="$OUT/gds/ez130_8t.gds"
(cd "$OUT" && sha256sum -c "$HERE/reconstructed.sha256")

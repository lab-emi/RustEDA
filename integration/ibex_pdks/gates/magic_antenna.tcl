# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
# Magic antenna check of one GDS with the PDK technology file's antenna rules.
# usage: env GATE_GDS=<gds> GATE_TOP=<cell> GATE_DIR=<scratch dir>
#        GATE_ABSTRACT_LEFS=<lef>:<lef> GATE_ABSTRACT_CELLS=<cell>:<cell>
#        magic -dnull -noconsole -rcfile <pdk magicrc> magic_antenna.tcl
# Macro cells are abstract views read from their LEF. Extraction is local and without
# parasitics; `antennacheck debug` prints every violation with its plane, effective ratio,
# limit and gate rectangle in top-cell coordinates.
crashbackups stop
drc off
set gds $::env(GATE_GDS)
set cell $::env(GATE_TOP)
puts "GATE_TECH [tech name] [tech version]"
puts "GATE_UNIT [cif scale out]"
foreach lef [split $::env(GATE_ABSTRACT_LEFS) ":"] {
    if {$lef ne ""} { lef read $lef }
}
gds noduplicates true
gds read $gds
set all [cellname list allcells]
if {[lsearch -exact $all $cell] < 0} {
    puts "GATE_TOP_MISSING $cell"
    quit -noprompt
}
foreach macro [split $::env(GATE_ABSTRACT_CELLS) ":"] {
    if {$macro eq ""} { continue }
    if {[lsearch -exact $all $macro] < 0} {
        puts "GATE_ABSTRACT_MISSING $macro"
        quit -noprompt
    }
    load $macro
    puts "GATE_ABSTRACT $macro [property LEFview]"
}
load $cell
select top cell
expand
puts "GATE_BBOX [box values]"
cd $::env(GATE_DIR)
extract do local
extract no all
extract all
puts "GATE_EXTRACTED"
antennacheck debug
antennacheck
puts "GATE_DONE"
quit -noprompt

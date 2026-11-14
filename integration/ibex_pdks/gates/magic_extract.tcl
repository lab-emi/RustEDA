# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
# Magic hierarchical device extraction of one GDS to an LVS netlist.
# usage: env GATE_GDS=<gds> GATE_TOP=<cell> GATE_DIR=<scratch dir> GATE_OUT=<spice>
#        GATE_ABSTRACT_LEFS=<lef>:<lef> GATE_ABSTRACT_CELLS=<cell>:<cell>
#        magic -dnull -noconsole -rcfile <pdk magicrc> magic_extract.tcl
# Macro cells are abstract views read from their LEF and are written as black-box subcircuits
# whose ports are the LEF pins. `extract unique all` gives equally named but unconnected nets
# distinct names, so an open in a labelled net becomes an extra port instead of being joined
# by name.
crashbackups stop
drc off
set gds $::env(GATE_GDS)
set cell $::env(GATE_TOP)
puts "GATE_TECH [tech name] [tech version]"
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
cd $::env(GATE_DIR)
extract do local
extract no capacitance
extract no coupling
extract no resistance
extract no adjust
extract unique all
extract all
puts "GATE_EXTRACTED"
ext2spice lvs
ext2spice -o $::env(GATE_OUT)
puts "GATE_DONE"
quit -noprompt

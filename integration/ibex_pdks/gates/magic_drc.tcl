# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
# Magic batch DRC of one GDS with the PDK technology file (Magic is run as an external referee).
# usage: env GATE_GDS=<gds> GATE_TOP=<cell> GATE_STYLE=<drc style> GATE_OUT=<file>
#        GATE_ABSTRACT_LEFS=<lef>:<lef> GATE_ABSTRACT_CELLS=<cell>:<cell>
#        magic -dnull -noconsole -rcfile <pdk magicrc> magic_drc.tcl
# The macro LEFs are read first, so the listed macro cells are abstract views (pins and
# obstructions) and the GDS definitions of those cells are not loaded. The hierarchical check of
# the top cell holds every error of the cells below it; the verdict is its error count. The rule
# messages and boxes Magic can name are written to GATE_OUT.
crashbackups stop
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
drc euclidean on
drc style $::env(GATE_STYLE)
puts "GATE_STYLE [drc list style]"
drc check
drc catchup
puts "GATE_COUNT [drc list count total]"
set out [open $::env(GATE_OUT) w]
set boxes_total 0
foreach {message boxes} [drc listall why] {
    puts $out "R $message"
    foreach box $boxes {
        puts $out "B $box"
        incr boxes_total
    }
}
close $out
puts "GATE_BOXES $boxes_total"
puts "GATE_DONE"
quit -noprompt

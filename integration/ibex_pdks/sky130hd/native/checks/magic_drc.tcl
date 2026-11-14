# SPDX-License-Identifier: Apache-2.0
# Magic DRC of a routed block, style drc(full), with hard macros as LEF abstracts.
# usage: env GDS=<gds> CELL=<top> MACRO_LEFS="<lef> ..." magic -dnull -noconsole -rcfile <pdk magicrc> magic_drc.tcl
# Macro masters are read from their LEF first and kept (`gds noduplicates`), so the
# check covers everything outside the macros and every shape that touches a macro pin
# or obstruction; macro interiors are not this layout's responsibility.
# Prints RR_* lines; a count per rule message of the flattened top cell is the verdict.
set gds $::env(GDS)
set cell $::env(CELL)
crashbackups stop
puts "RR_TECH [tech name]"
if {[info exists ::env(MACRO_LEFS)]} {
    foreach lef $::env(MACRO_LEFS) { lef read $lef; puts "RR_MACRO_LEF $lef" }
    gds noduplicates true
}
gds read $gds
set all [cellname list allcells]
puts "RR_CELLS [llength $all]"
if {[lsearch -exact $all $cell] < 0} {
    puts "RR_TOP_MISSING $cell"
    quit -noprompt
}
load $cell
select top cell
puts "RR_BBOX [box values]"
drc euclidean on
drc style drc(full)
puts "RR_STYLE [drc list style]"
drc check
drc catchup
puts "RR_HIER_TOTAL [drc list count total]"
foreach {msg boxes} [drc listall why] {
    puts "RR_HIER_WHY [llength $boxes] :: $msg"
    set n 0
    foreach b $boxes { if {$n < 40} { puts "RR_HIER_BOX $b :: $msg" }; incr n }
}
puts "RR_DONE"
quit -noprompt

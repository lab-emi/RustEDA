# SPDX-License-Identifier: Apache-2.0
# Magic hierarchical extraction to an LVS netlist; hard macros stay abstract (black boxes with their LEF pins).
# usage: env GDS=<gds> CELL=<top> OUTDIR=<dir> MACRO_LEFS="<lef> ..." magic -dnull -noconsole -rcfile <rc> magic_extract.tcl
set gds $::env(GDS)
set cell $::env(CELL)
set od $::env(OUTDIR)
crashbackups stop
drc off
if {[info exists ::env(MACRO_LEFS)]} {
    foreach lef $::env(MACRO_LEFS) { lef read $lef }
    gds noduplicates true
}
gds read $gds
set all [cellname list allcells]
if {[lsearch -exact $all $cell] < 0} {
    puts "RR_TOP_MISSING $cell"
    quit -noprompt
}
load $cell
select top cell
expand
file mkdir $od
cd $od
extract do local
extract unique all
extract all
ext2spice lvs
ext2spice -o $od/$cell.spice
puts "RR_EXT_DONE $od/$cell.spice"
quit -noprompt

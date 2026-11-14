# SPDX-License-Identifier: Apache-2.0
# Magic antenna check of a routed block; hard macros as LEF abstracts (their pins carry no gate or diffusion area).
# usage: env GDS=<gds> CELL=<top> OUTDIR=<dir> MACRO_LEFS="<lef> ..." magic -dnull -noconsole -rcfile <rc> magic_antenna.tcl
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
extract no all
extract all
antennacheck debug
antennacheck
puts "RR_ANT_DONE"
quit -noprompt

# SPDX-License-Identifier: Apache-2.0
# Netgen LVS: Magic-extracted layout against the powered structural Verilog, read on top of the
# standard-cell SPICE library and one empty subcircuit per hard macro (black box on both sides).
# usage: env LAYOUT=<spice> TOP=<cell> REFV=<verilog> STDSPICE=<spice> MACRO_SPICE=<spice of empty .subckt> SETUP=<setup.tcl>
#        OUT=<comp.out> netgen -batch source netgen_lvs.tcl
set c1 [readnet spice $::env(LAYOUT)]
set c2 [readnet spice $::env(STDSPICE)]
if {[info exists ::env(MACRO_SPICE)]} { readnet spice $::env(MACRO_SPICE) $c2 }
readnet verilog $::env(REFV) $c2
lvs "$c1 $::env(TOP)" "$c2 $::env(TOP)" $::env(SETUP) $::env(OUT) -blackbox -json
puts "RR_NETGEN_DONE"
quit

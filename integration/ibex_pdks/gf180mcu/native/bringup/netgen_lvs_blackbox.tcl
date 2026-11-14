# SPDX-License-Identifier: Apache-2.0
# Netgen LVS of a Magic-extracted layout against the powered structural Verilog on top of the cell
# library's SPICE; the hard macro is an empty (black-box) subcircuit on both sides.
# usage: env LAYOUT=<spice> TOP=<cell> REFV=<verilog> MACRO_BB=<verilog> STDSPICE=<spice> SETUP=<tcl>
#        OUT=<comp.out> netgen -batch source <this>
set c1 [readnet spice $::env(LAYOUT)]
set c2 [readnet spice $::env(STDSPICE)]
readnet verilog $::env(MACRO_BB) $c2
readnet verilog $::env(REFV) $c2
lvs "$c1 $::env(TOP)" "$c2 $::env(TOP)" $::env(SETUP) $::env(OUT) -blackbox -json
puts "RR_NETGEN_DONE"
quit

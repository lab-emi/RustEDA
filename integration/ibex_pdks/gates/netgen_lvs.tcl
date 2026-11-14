# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
# Netgen LVS: Magic-extracted layout against the powered structural Verilog read on top of the
# PDK cell SPICE and the macro black-box declarations.
# usage: env GATE_LAYOUT=<spice> GATE_TOP=<cell> GATE_REF=<verilog> GATE_CELLS=<spice list>
#        GATE_SETUP=<setup.tcl> GATE_OUT=<comp.out> netgen -batch source netgen_lvs.tcl
set c1 [readnet spice $::env(GATE_LAYOUT)]
set c2 -1
foreach library [split $::env(GATE_CELLS) ":"] {
    if {$c2 < 0} { set c2 [readnet spice $library] } else { readnet spice $library $c2 }
}
readnet verilog $::env(GATE_REF) $c2
lvs "$c1 $::env(GATE_TOP)" "$c2 $::env(GATE_TOP)" $::env(GATE_SETUP) $::env(GATE_OUT) -json -blackbox
puts "GATE_DONE"
quit

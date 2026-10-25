# Synthetic Tcl subset fixture exercising variables, expr, if/elseif/else, foreach, list ops, and source.
source sourced_clocks.sdc

set io_ratio 0.2
set in_dly [expr {$clk_period * $io_ratio}]
set out_dly [expr {min(1.5, $clk_period * 0.3)}]

if {[info exists ::env(SYNTH_CAP_LOAD)]} {
    set cap_val [expr {$::env(SYNTH_CAP_LOAD) / 1000.0}]
} else {
    set cap_val 0.02
}

set data_ins [list din_0 din_1 clk_i]
set clk_idx [lsearch $data_ins clk_i]
if {$clk_idx >= 0} {
    set data_ins [lreplace $data_ins $clk_idx $clk_idx]
}

foreach p $data_ins {
    set_input_delay -clock [get_clocks $clk_name] -max $in_dly [get_ports $p]
}

set_output_delay -clock [get_clocks $clk_name] -max $out_dly [all_outputs]
set_load -pin_load $cap_val [all_outputs]

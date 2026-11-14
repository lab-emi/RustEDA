# Clock-only projection of the benchmark constraints for `rustroad clock-tree` (100 ns clock on clk_i).
set_units -time ns -capacitance pF
create_clock -name clk_i -period 100 -waveform {0 50} [get_ports clk_i]
set_clock_transition 0.15 [get_clocks clk_i]
set_clock_uncertainty 0.05 [get_clocks clk_i]
set_max_fanout 10 [current_design]

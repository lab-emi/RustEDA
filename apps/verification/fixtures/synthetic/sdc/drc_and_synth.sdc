set_units -time ns -capacitance pF -power mW
current_design synth_top
create_clock -name clk -period 5.0 [get_ports clk]
set_max_fanout 16.0 [current_design]
set_max_transition -data_path 0.4 [current_design]
set_max_capacitance 0.25 [all_outputs]
set_min_capacitance 0.005 [all_inputs]
set_fanout_load 2.0 [all_outputs]
set_port_fanout_number 4 [all_inputs]
set_max_area 1200.0
set_max_dynamic_power -unit mW 25.0
set_max_leakage_power -unit mW 1.5
set_dont_touch [get_cells u_keep]
set_dont_touch_network [get_ports clk]
set_dont_use [get_lib_cells slow_db/HOLD_X1]
set_size_only [get_cells u_size]

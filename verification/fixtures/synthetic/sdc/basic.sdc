set_units -time ns -capacitance pF -resistance kOhm -voltage V -current mA -power mW
current_design top_core
set_operating_conditions -analysis_type on_chip_variation -min fast_lib -max slow_lib -min_library fast_db -max_library slow_db
set_wire_load_model -name wl_small -library slow_db -min
set_wire_load_mode enclosed
set_wire_load_selection_group -library slow_db -max area_table

create_clock -name sys_clk -period 2.5 -waveform {0.0 1.25} [get_ports clk_i]
create_clock -name vir_clk -period 5.0
set_clock_uncertainty -setup -from [get_clocks sys_clk] -to [get_clocks sys_clk] 0.1
set_clock_uncertainty -hold 0.04 [get_clocks sys_clk]
set_clock_latency -source -early -rise -clock [get_clocks sys_clk] 0.15 [get_ports clk_i]
set_clock_transition -max -fall 0.08 [get_clocks sys_clk]

set_input_delay -clock [get_clocks sys_clk] -max 0.6 [get_ports {din_a din_b}]
set_input_delay -clock [get_clocks sys_clk] -min 0.15 [get_ports {din_a din_b}]
set_output_delay -clock [get_clocks sys_clk] -max 0.75 [get_ports {dout_a dout_b}]
set_output_delay -clock [get_clocks sys_clk] -min 0.1 [get_ports {dout_a dout_b}]
set_input_transition -max 0.12 [get_ports {din_a din_b}]
set_driving_cell -lib_cell BUF_X4 -library slow_db -pin Z -from_pin A -input_transition_rise 0.05 -input_transition_fall 0.06 [get_ports din_a]
set_load -pin_load -max 0.025 [get_ports {dout_a dout_b}]

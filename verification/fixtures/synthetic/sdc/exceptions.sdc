set_units -time ns -capacitance pF
create_clock -name clk_a -period 2.0 [get_ports clk_a]
create_clock -name clk_b -period 3.0 [get_ports clk_b]
set_clock_groups -asynchronous -name async_ab -group [get_clocks clk_a] -group [get_clocks clk_b]

set_false_path -setup -from [get_clocks clk_a] -to [get_clocks clk_b]
set_false_path -hold -rise_from [get_ports rst_n] -fall_to [all_registers -data_pins]
set_multicycle_path -setup -end -from [get_cells u_slow/*] -to [get_cells u_dst/*] 2
set_multicycle_path -hold -end -from [get_cells u_slow/*] -to [get_cells u_dst/*] 1
set_max_delay -from [get_ports async_in] -to [get_pins u_sync/d] 1.5
set_min_delay -from [get_ports async_in] -to [get_pins u_sync/d] 0.2
set_case_analysis 0 [get_ports scan_en]
set_logic_zero [get_ports tie_lo]
set_logic_one [get_ports tie_hi]
set_disable_timing -from A -to Z [get_cells u_mux]
set_max_time_borrow 0.4 [get_pins u_latch/en]
group_path -name reg2reg -weight 2.0 -critical_range 0.3 -from [all_registers] -to [all_registers]

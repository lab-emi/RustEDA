set clk_name core_clk
set clk_period 4.0
create_clock -name $clk_name -period $clk_period [get_ports clk_i]

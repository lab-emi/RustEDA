// SPDX-License-Identifier: Apache-2.0
// Benchmark wrapper: Ibex v2.1.0 RV32IMC with two 2 KiB memory banks, each built from four
// GlobalFoundries gf180mcu_fd_ip_sram__sram512x8m8wm1 macros (512 words of 8 bits, one byte lane each).
// Instruction addresses 0x00000000..0x000007ff; data addresses 0x10000000..0x100007ff.
// Hold run_i low while loading. Ibex reset remains asserted until run_i is high.
// The macro's CEN, GWEN and WEN pins are active low; WEN is a per-bit write enable.
module ibex_sram_bench (
  input logic clk_i, rst_ni, run_i,
  input logic prog_req_i, prog_we_i, prog_bank_i,
  input logic [8:0] prog_addr_i,
  input logic [3:0] prog_be_i,
  input logic [31:0] prog_wdata_i,
  output logic [31:0] prog_rdata_o,
  output logic prog_rvalid_o,
  output logic alert_o, core_sleep_o,
  output logic [31:0] observe_data_o,
  output logic observe_valid_o
);
  wire core_reset_n = rst_ni & run_i;
  wire i_req, d_req, d_we;
  wire [31:0] i_addr, d_addr, d_wdata, i_rdata, d_rdata;
  wire [3:0] d_be;
  wire i_hit = i_addr[31:11] == 21'h000000;
  wire d_hit = d_addr[31:11] == 21'h020000;
  logic i_valid, d_valid, i_error, d_error, prog_bank_q;
  wire alert_minor, alert_internal, alert_bus;
  assign alert_o = alert_minor | alert_internal | alert_bus;
  assign prog_rdata_o = prog_bank_q ? d_rdata : i_rdata;
  assign observe_data_o = d_wdata;
  assign observe_valid_o = run_i & d_req & d_we & d_hit;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      i_valid <= 1'b0;
      d_valid <= 1'b0;
      i_error <= 1'b0;
      d_error <= 1'b0;
      prog_rvalid_o <= 1'b0;
      prog_bank_q <= 1'b0;
    end else begin
      i_valid <= run_i & i_req;
      d_valid <= run_i & d_req;
      i_error <= run_i & i_req & !i_hit;
      d_error <= run_i & d_req & !d_hit;
      prog_rvalid_o <= !run_i & prog_req_i & !prog_we_i;
      if (!run_i & prog_req_i) prog_bank_q <= prog_bank_i;
    end
  end
  ibex_top u_cpu (
    .clk_i(clk_i), .rst_ni(core_reset_n), .test_en_i(1'b0), .ram_cfg_i('0),
    .hart_id_i(32'b0), .boot_addr_i(32'b0),
    .instr_req_o(i_req), .instr_gnt_i(run_i & i_req), .instr_rvalid_i(i_valid),
    .instr_addr_o(i_addr), .instr_rdata_i(i_rdata), .instr_rdata_intg_i(7'b0),
    .instr_err_i(i_error),
    .data_req_o(d_req), .data_gnt_i(run_i & d_req), .data_rvalid_i(d_valid),
    .data_we_o(d_we), .data_be_o(d_be), .data_addr_o(d_addr), .data_wdata_o(d_wdata),
    .data_wdata_intg_o(), .data_rdata_i(d_rdata), .data_rdata_intg_i(7'b0),
    .data_err_i(d_error), .irq_software_i(1'b0), .irq_timer_i(1'b0),
    .irq_external_i(1'b0), .irq_fast_i(15'b0), .irq_nm_i(1'b0),
    .scramble_key_valid_i(1'b0), .scramble_key_i('0), .scramble_nonce_i('0),
    .scramble_req_o(), .debug_req_i(1'b0), .crash_dump_o(), .double_fault_seen_o(),
    .fetch_enable_i(4'b0101), .alert_minor_o(alert_minor),
    .alert_major_internal_o(alert_internal), .alert_major_bus_o(alert_bus),
    .core_sleep_o(core_sleep_o), .scan_rst_ni(1'b1)
  );
  // Instruction bank: read by the core, written only while loading.
  wire i_cen = !(run_i ? (i_req & i_hit) : (prog_req_i & !prog_bank_i));
  wire i_gwen = !(!run_i & prog_we_i);
  wire [8:0] i_a = run_i ? i_addr[10:2] : prog_addr_i;
  // Data bank: read and written by the core, or by the loader.
  wire d_cen = !(run_i ? (d_req & d_hit) : (prog_req_i & prog_bank_i));
  wire d_gwen = !(run_i ? d_we : prog_we_i);
  wire [8:0] d_a = run_i ? d_addr[10:2] : prog_addr_i;
  wire [31:0] d_d = run_i ? d_wdata : prog_wdata_i;
  wire [3:0] d_lane = run_i ? d_be : prog_be_i;
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_imem0 (
    .CLK(clk_i), .CEN(i_cen), .GWEN(i_gwen), .WEN({8{!prog_be_i[0]}}),
    .A(i_a), .D(prog_wdata_i[7:0]), .Q(i_rdata[7:0])
  );
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_imem1 (
    .CLK(clk_i), .CEN(i_cen), .GWEN(i_gwen), .WEN({8{!prog_be_i[1]}}),
    .A(i_a), .D(prog_wdata_i[15:8]), .Q(i_rdata[15:8])
  );
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_imem2 (
    .CLK(clk_i), .CEN(i_cen), .GWEN(i_gwen), .WEN({8{!prog_be_i[2]}}),
    .A(i_a), .D(prog_wdata_i[23:16]), .Q(i_rdata[23:16])
  );
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_imem3 (
    .CLK(clk_i), .CEN(i_cen), .GWEN(i_gwen), .WEN({8{!prog_be_i[3]}}),
    .A(i_a), .D(prog_wdata_i[31:24]), .Q(i_rdata[31:24])
  );
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_dmem0 (
    .CLK(clk_i), .CEN(d_cen), .GWEN(d_gwen), .WEN({8{!d_lane[0]}}),
    .A(d_a), .D(d_d[7:0]), .Q(d_rdata[7:0])
  );
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_dmem1 (
    .CLK(clk_i), .CEN(d_cen), .GWEN(d_gwen), .WEN({8{!d_lane[1]}}),
    .A(d_a), .D(d_d[15:8]), .Q(d_rdata[15:8])
  );
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_dmem2 (
    .CLK(clk_i), .CEN(d_cen), .GWEN(d_gwen), .WEN({8{!d_lane[2]}}),
    .A(d_a), .D(d_d[23:16]), .Q(d_rdata[23:16])
  );
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_dmem3 (
    .CLK(clk_i), .CEN(d_cen), .GWEN(d_gwen), .WEN({8{!d_lane[3]}}),
    .A(d_a), .D(d_d[31:24]), .Q(d_rdata[31:24])
  );
endmodule

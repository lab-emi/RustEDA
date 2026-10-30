// SPDX-License-Identifier: Apache-2.0
// Benchmark wrapper: Ibex v2.1.0 RV32IMC, two real IHP 4 KiB SRAM banks.
// Instruction addresses 0x00000000..0x00000fff; data addresses 0x10000000..0x10000fff.
// Hold run_i low while loading. Ibex reset remains asserted until run_i is high.
module ibex_sram_bench (
  input logic clk_i, rst_ni, run_i,
  input logic prog_req_i, prog_we_i, prog_bank_i,
  input logic [9:0] prog_addr_i,
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
  wire i_hit = i_addr[31:12] == 20'h00000;
  wire d_hit = d_addr[31:12] == 20'h10000;
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
  wire [31:0] prog_mask = {{8{prog_be_i[3]}}, {8{prog_be_i[2]}},
                            {8{prog_be_i[1]}}, {8{prog_be_i[0]}}};
  wire [31:0] data_mask = {{8{d_be[3]}}, {8{d_be[2]}}, {8{d_be[1]}}, {8{d_be[0]}}};
  RM_IHPSG13_1P_1024x32_c2_bm_bist u_imem (
    .A_CLK(clk_i), .A_MEN(run_i ? (i_req & i_hit) : (prog_req_i & !prog_bank_i)),
    .A_WEN(!run_i & prog_we_i), .A_REN(run_i | !prog_we_i),
    .A_ADDR(run_i ? i_addr[11:2] : prog_addr_i), .A_DIN(prog_wdata_i),
    .A_DLY(1'b1), .A_DOUT(i_rdata), .A_BM(prog_mask),
    .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0),
    .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0), .A_BIST_ADDR(10'b0),
    .A_BIST_DIN(32'b0), .A_BIST_BM(32'b0)
  );
  RM_IHPSG13_1P_1024x32_c2_bm_bist u_dmem (
    .A_CLK(clk_i), .A_MEN(run_i ? (d_req & d_hit) : (prog_req_i & prog_bank_i)),
    .A_WEN(run_i ? d_we : prog_we_i), .A_REN(run_i ? !d_we : !prog_we_i),
    .A_ADDR(run_i ? d_addr[11:2] : prog_addr_i),
    .A_DIN(run_i ? d_wdata : prog_wdata_i), .A_DLY(1'b1), .A_DOUT(d_rdata),
    .A_BM(run_i ? data_mask : prog_mask),
    .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0),
    .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0), .A_BIST_ADDR(10'b0),
    .A_BIST_DIN(32'b0), .A_BIST_BM(32'b0)
  );
endmodule

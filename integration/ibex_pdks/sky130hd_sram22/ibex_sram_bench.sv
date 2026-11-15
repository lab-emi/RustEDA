// SPDX-License-Identifier: Apache-2.0
// Ibex RV32IMC with two 2 KiB SRAM22 banks, using the vendor macro unchanged.
// SRAM ce/we/wmask are active high; rstb inhibits operations without clearing storage.
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
  sram22_512x32m4w8 u_imem (
    .clk(clk_i), .rstb(rst_ni),
    .ce(run_i ? (i_req & i_hit) : (prog_req_i & !prog_bank_i)),
    .we(!run_i & prog_we_i), .wmask(prog_be_i),
    .addr(run_i ? i_addr[10:2] : prog_addr_i), .din(prog_wdata_i), .dout(i_rdata)
  );
  sram22_512x32m4w8 u_dmem (
    .clk(clk_i), .rstb(rst_ni),
    .ce(run_i ? (d_req & d_hit) : (prog_req_i & prog_bank_i)),
    .we(run_i ? d_we : prog_we_i), .wmask(run_i ? d_be : prog_be_i),
    .addr(run_i ? d_addr[10:2] : prog_addr_i),
    .din(run_i ? d_wdata : prog_wdata_i), .dout(d_rdata)
  );
endmodule

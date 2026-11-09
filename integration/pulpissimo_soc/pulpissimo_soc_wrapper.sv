// SPDX-License-Identifier: SHL-0.51
// PULPissimo SoC-domain benchmark wrapper.
//
// Exposes the APB chip-control master as plain ports so the design has an
// interface-free top. Pads, pad multiplexing and clock/FLL generation are
// excluded: they are board-level IO and clocking, not standard-cell logic.
//
// This wrapper is part of the RustEDA benchmark harness, not upstream
// PULPissimo. The upstream top and `soc_domain` are unchanged.
module pulpissimo_soc_wrapper #(
    parameter CORE_TYPE  = 1,  // 1 = Ibex RV32IMC, 0 = CV32E40P
    parameter USE_XPULP  = 0,
    parameter USE_FPU    = 0,
    parameter USE_ZFINX  = 0,
    parameter USE_HWPE   = 0,
    parameter SIM_STDOUT = 0
)(
    input  logic                 slow_clk_i,
    input  logic                 slow_clk_rstn_synced_i,
    input  logic                 soc_clk_i,
    input  logic                 soc_rstn_synced_i,
    input  logic                 per_clk_i,
    input  logic                 per_rstn_synced_i,
    input  logic                 dft_test_mode_i,
    input  logic                 dft_cg_enable_i,
    input  logic [1:0]           bootsel_i,
    output logic [3:0]           timer_ch0_o,
    output logic [3:0]           timer_ch1_o,
    output logic [3:0]           timer_ch2_o,
    output logic [3:0]           timer_ch3_o,
    input  logic [gpio_reg_pkg::GPIOCount-1:0] gpio_i,
    output logic [gpio_reg_pkg::GPIOCount-1:0] gpio_o,
    output logic [gpio_reg_pkg::GPIOCount-1:0] gpio_tx_en_o,
    output uart_pkg::uart_to_pad_t [udma_cfg_pkg::N_UART-1:0] uart_to_pad_o,
    input  uart_pkg::pad_to_uart_t [udma_cfg_pkg::N_UART-1:0] pad_to_uart_i,
    output i2c_pkg::i2c_to_pad_t [udma_cfg_pkg::N_I2C-1:0] i2c_to_pad_o,
    input  i2c_pkg::pad_to_i2c_t [udma_cfg_pkg::N_I2C-1:0] pad_to_i2c_i,
    output sdio_pkg::sdio_to_pad_t [udma_cfg_pkg::N_SDIO-1:0] sdio_to_pad_o,
    input  sdio_pkg::pad_to_sdio_t [udma_cfg_pkg::N_SDIO-1:0] pad_to_sdio_i,
    output i2s_pkg::i2s_to_pad_t [udma_cfg_pkg::N_I2S-1:0] i2s_to_pad_o,
    input  i2s_pkg::pad_to_i2s_t [udma_cfg_pkg::N_I2S-1:0] pad_to_i2s_i,
    output qspi_pkg::qspi_to_pad_t [udma_cfg_pkg::N_QSPIM-1:0] qspi_to_pad_o,
    input  qspi_pkg::pad_to_qspi_t [udma_cfg_pkg::N_QSPIM-1:0] pad_to_qspi_i,
    input  cpi_pkg::pad_to_cpi_t [udma_cfg_pkg::N_CPI-1:0] pad_to_cpi_i,
    output hyper_pkg::hyper_to_pad_t [udma_cfg_pkg::N_HYPER-1:0] hyper_to_pad_o,
    input  hyper_pkg::pad_to_hyper_t [udma_cfg_pkg::N_HYPER-1:0] pad_to_hyper_i,
    output logic                 jtag_tap_bypass_fll_clk_o,
    output logic [31:0]          apb_paddr_o,
    output logic                 apb_psel_o,
    output logic                 apb_penable_o,
    output logic                 apb_pwrite_o,
    output logic [31:0]          apb_pwdata_o,
    output logic [3:0]           apb_pstrb_o,
    input  logic                 apb_pready_i,
    input  logic [31:0]          apb_prdata_i,
    input  logic                 apb_pslverr_i,
    input  logic                 jtag_tck_i,
    input  logic                 jtag_trst_ni,
    input  logic                 jtag_tms_i,
    input  logic                 jtag_tdi_i,
    output logic                 jtag_tdo_o
);
  APB #(.ADDR_WIDTH(32), .DATA_WIDTH(32)) apb_chip_ctrl_master();

  soc_domain #(
      .CORE_TYPE  (CORE_TYPE),
      .USE_XPULP  (USE_XPULP),
      .USE_FPU    (USE_FPU),
      .USE_ZFINX  (USE_ZFINX),
      .USE_HWPE   (USE_HWPE),
      .SIM_STDOUT (SIM_STDOUT)
  ) i_soc_domain (
      .*,
      .apb_chip_ctrl_master(apb_chip_ctrl_master)
  );

  assign apb_paddr_o   = apb_chip_ctrl_master.paddr;
  assign apb_psel_o    = apb_chip_ctrl_master.psel;
  assign apb_penable_o = apb_chip_ctrl_master.penable;
  assign apb_pwrite_o  = apb_chip_ctrl_master.pwrite;
  assign apb_pwdata_o  = apb_chip_ctrl_master.pwdata;
  assign apb_pstrb_o   = apb_chip_ctrl_master.pstrb;
  assign apb_chip_ctrl_master.pready  = apb_pready_i;
  assign apb_chip_ctrl_master.prdata  = apb_prdata_i;
  assign apb_chip_ctrl_master.pslverr = apb_pslverr_i;
endmodule

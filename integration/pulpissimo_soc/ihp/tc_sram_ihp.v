// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors
//
// Technology adapter for the PULPissimo L2 banks on IHP SG13G2.
// It replaces the behavioral tech_cells_generic tc_sram with the official
// 1024x32 single-port SRAM macro. One macro covers 1024 words; 8 or 16
// macros cover the 8192- and 16384-word PULPissimo banks. The interface is
// 32-bit data, 4-bit byte enables and one-cycle read latency, matching the
// PULPissimo instances.
// Fixed-interface blackbox for the official IHP 1024x32 single-port SRAM.
module RM_IHPSG13_1P_1024x32_c2_bm_bist (
    input  wire        A_CLK,
    input  wire        A_MEN,
    input  wire        A_WEN,
    input  wire        A_REN,
    input  wire [9:0]  A_ADDR,
    input  wire [31:0] A_DIN,
    input  wire        A_DLY,
    output wire [31:0] A_DOUT,
    input  wire [31:0] A_BM,
    input  wire        A_BIST_CLK,
    input  wire        A_BIST_EN,
    input  wire        A_BIST_MEN,
    input  wire        A_BIST_WEN,
    input  wire        A_BIST_REN,
    input  wire [9:0]  A_BIST_ADDR,
    input  wire [31:0] A_BIST_DIN,
    input  wire [31:0] A_BIST_BM
);
endmodule

// PULPissimo tc_sram interface over 1..16 official IHP 1024x32 macros.
module tc_sram #(
    parameter [31:0] NumWords  = 32'd1024,
    parameter [31:0] DataWidth = 32'd32,
    parameter [31:0] ByteWidth = 32'd8,
    parameter [31:0] NumPorts  = 32'd1,
    parameter        Latency   = 32'd1,
    parameter [31:0] AddrWidth = (NumWords > 32'd1 ? $clog2(NumWords) : 32'd1),
    parameter [31:0] BeWidth   = ((DataWidth + ByteWidth) - 32'd1) / ByteWidth
)(
    input  wire                          clk_i,
    input  wire                          rst_ni,
    input  wire [NumPorts-1:0]           req_i,
    input  wire [NumPorts-1:0]           we_i,
    input  wire [(NumPorts*AddrWidth)-1:0] addr_i,
    input  wire [(NumPorts*DataWidth)-1:0] wdata_i,
    input  wire [(NumPorts*BeWidth)-1:0]   be_i,
    output reg  [(NumPorts*DataWidth)-1:0] rdata_o
);
    localparam integer NUM_MACROS = NumWords / 32'd1024;
    wire [32*NUM_MACROS-1:0] dout_flat;
    wire [13:0] addr14;
    assign addr14 = addr_i;
    wire [31:0] be_mask = {{8{be_i[3]}}, {8{be_i[2]}}, {8{be_i[1]}}, {8{be_i[0]}}};
    // rst_ni is unused: the IHP macro has no reset pin.
    wire unused_rst = rst_ni;

    genvar g;
    generate
        for (g = 0; g < NUM_MACROS; g = g + 1) begin : gen_macro
            wire sel = req_i[0] && ((NUM_MACROS == 1) || (addr14[13:10] == g));
            RM_IHPSG13_1P_1024x32_c2_bm_bist u_macro (
                .A_CLK(clk_i),
                .A_MEN(sel),
                .A_WEN(we_i[0]),
                .A_REN(sel && !we_i[0]),
                .A_ADDR(addr_i[9:0]),
                .A_DIN(wdata_i[31:0]),
                .A_DLY(1'b0),
                .A_DOUT(dout_flat[g*32 + 31 -: 32]),
                .A_BM(be_mask),
                .A_BIST_CLK(1'b0),
                .A_BIST_EN(1'b0),
                .A_BIST_MEN(1'b0),
                .A_BIST_WEN(1'b0),
                .A_BIST_REN(1'b0),
                .A_BIST_ADDR(10'b0),
                .A_BIST_DIN(32'b0),
                .A_BIST_BM(32'b0)
            );
        end
    endgenerate

    reg [3:0] rsel;
    always @(posedge clk_i) begin
        if (req_i[0] && !we_i[0])
            rsel <= addr14[13:10];
    end

    integer i;
    always @* begin
        rdata_o = {DataWidth{1'b0}};
        for (i = 0; i < NUM_MACROS; i = i + 1)
            if (rsel == i)
                rdata_o = dout_flat[i*32 + 31 -: 32];
    end
endmodule

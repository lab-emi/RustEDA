// SPDX-License-Identifier: Apache-2.0
// Micro-design for macro bring-up: two foundry SRAMs (one per orientation of the benchmark floorplan),
// a registered interface and a small accumulator (about 800 cells).
(* blackbox *)
module gf180mcu_fd_ip_sram__sram512x8m8wm1 (
  input           CLK,
  input           CEN,
  input           GWEN,
  input   [7:0]   WEN,
  input   [8:0]   A,
  input   [7:0]   D,
  output  [7:0]   Q
);
endmodule
module micro_sram (
  input clk_i, input rst_ni, input en_i, input we_i, input sel_i,
  input [8:0] addr_i, input [7:0] data_i,
  output [7:0] data_o, output [15:0] sum_o, output [15:0] mix_o
);
  reg [8:0] addr_q;
  reg [7:0] d_q;
  reg we_q, en_q, sel_q;
  reg [15:0] sum_q, mix_q;
  assign sum_o = sum_q;
  assign mix_o = mix_q;
  wire [7:0] q0, q1;
  wire [7:0] q = sel_q ? q1 : q0;
  always @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      addr_q <= 9'b0; d_q <= 8'b0; we_q <= 1'b0; en_q <= 1'b0; sel_q <= 1'b0;
      sum_q <= 16'b0; mix_q <= 16'b0;
    end else begin
      addr_q <= addr_i + {8'b0, en_q};
      d_q <= data_i ^ {q[3:0], q[7:4]};
      we_q <= we_i; en_q <= en_i; sel_q <= sel_i;
      sum_q <= sum_q + {8'b0, q} + {7'b0, addr_q};
      mix_q <= (mix_q << 1) ^ (sum_q * {8'b0, d_q}) ^ {q, d_q};
    end
  end
  assign data_o = q;
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_mem0 (
    .CLK(clk_i), .CEN(~(en_q & ~sel_q)), .GWEN(~we_q), .WEN({8{~we_q}}),
    .A(addr_q), .D(d_q), .Q(q0)
  );
  gf180mcu_fd_ip_sram__sram512x8m8wm1 u_mem1 (
    .CLK(clk_i), .CEN(~(en_q & sel_q)), .GWEN(~we_q), .WEN({8{~we_q}}),
    .A(addr_q), .D(d_q), .Q(q1)
  );
endmodule

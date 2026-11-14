// SPDX-License-Identifier: Apache-2.0
// Black-box interface of the foundry SRAM for LVS (signal and supply pins of the macro LEF).
(* blackbox *)
module gf180mcu_fd_ip_sram__sram512x8m8wm1 (CLK, CEN, GWEN, WEN, A, D, Q, VDD, VSS);
  input CLK;
  input CEN;
  input GWEN;
  input [7:0] WEN;
  input [8:0] A;
  input [7:0] D;
  output [7:0] Q;
  inout VDD;
  inout VSS;
endmodule

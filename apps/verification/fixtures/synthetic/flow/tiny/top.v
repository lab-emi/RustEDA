module top(input wire clk, input wire a, input wire b, output reg q);
  always @(posedge clk) q <= a ^ b;
endmodule

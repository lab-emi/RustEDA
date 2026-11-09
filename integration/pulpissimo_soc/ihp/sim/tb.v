`timescale 1ns/1ps
module tb;
  reg clk = 0;
  always #5 clk = ~clk;
  reg req = 0, we = 0;
  reg [13:0] addr = 0;
  reg [31:0] wdata = 0;
  reg [3:0] be = 0;
  wire [31:0] rdata;
  reg [31:0] got;

  tc_sram #(.NumWords(16384), .DataWidth(32), .NumPorts(1)) dut (
    .clk_i(clk), .rst_ni(1'b1), .req_i(req), .we_i(we), .addr_i(addr),
    .wdata_i(wdata), .be_i(be), .rdata_o(rdata)
  );

  task write(input [13:0] a, input [31:0] d, input [3:0] m);
    begin
      @(negedge clk); req = 1; we = 1; addr = a; wdata = d; be = m;
      @(negedge clk); req = 0; we = 0;
    end
  endtask

  task read(input [13:0] a);
    begin
      @(negedge clk); req = 1; we = 0; addr = a;
      @(posedge clk); #1; got = rdata;
      @(negedge clk); req = 0;
    end
  endtask

  initial begin
    write(14'd5, 32'hDEADBEEF, 4'hF);
    write(14'd1029, 32'h12345678, 4'hF);
    read(14'd5); if (got !== 32'hDEADBEEF) $fatal(1, "read 5: %h", got);
    read(14'd1029); if (got !== 32'h12345678) $fatal(1, "read 1029: %h", got);
    write(14'd11269, 32'hCAFEBABE, 4'hF);
    read(14'd11269); if (got !== 32'hCAFEBABE) $fatal(1, "read 11269: %h", got);
    write(14'd7, 32'hAABBCCDD, 4'b0001);
    read(14'd7); if (got !== 32'h000000DD) $fatal(1, "byte mask: %h", got);
    $display("PASS");
    $finish;
  end
endmodule

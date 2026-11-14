// SPDX-License-Identifier: Apache-2.0
// Gate-level test bench of the gf180mcu profile: two 2 KiB banks of four 512x8 foundry SRAM macros each.
// Same stimulus as the IHP benchmark's tb.sv with 512-word banks; the sampling point and the first
// access per bank are explained below.
`timescale 1ns/1ps
module tb;
  reg clk=0;
  always #10 clk=~clk;
  reg rst=0, run=0, req=0, we=0, bank=0;
  reg [8:0] addr=0;
  reg [3:0] be=15;
  reg [31:0] wd=0;
  wire [31:0] rd, observed;
  wire valid, alert, asleep, observed_valid;
  ibex_sram_bench dut(.clk_i(clk),.rst_ni(rst),.run_i(run),
    .prog_req_i(req),.prog_we_i(we),.prog_bank_i(bank),.prog_addr_i(addr),
    .prog_be_i(be),.prog_wdata_i(wd),.prog_rdata_o(rd),.prog_rvalid_o(valid),
    .alert_o(alert),.core_sleep_o(asleep),.observe_data_o(observed),
    .observe_valid_o(observed_valid));
  task write_word(input reg b,input reg[8:0] a,input reg[31:0] d,input reg[3:0] mask);
    @(negedge clk); req=1; we=1; bank=b; addr=a; wd=d; be=mask;
    @(negedge clk); req=0; we=0;
  endtask
  // A request driven at a falling edge is taken at the next rising edge. The design consumes the
  // read data at the rising edge after that, so the check samples 1 ns before it.
  task read_word(input reg b,input reg[8:0] a);
    @(negedge clk); req=1; we=0; bank=b; addr=a;
    @(negedge clk); req=0;
    #9;
  endtask
  task check_word(input reg b,input reg[8:0] a,input reg[31:0] expected);
    read_word(b,a);
    if(valid!==1 || rd!==expected) $fatal(1,"bank %d address %d: got %h expected %h valid %b",b,a,rd,expected,valid);
  endtask
  integer stores=0;
  always @(posedge clk) if(run && observed_valid) begin
    if(stores==0 && observed!==42) $fatal(1,"first RISC-V store %h",observed);
    if(stores==1 && observed!==43) $fatal(1,"load/add/store result %h",observed);
    stores=stores+1;
  end
  initial begin
    repeat(3) @(negedge clk); rst=1;
    // One unchecked access per bank first: a memory model may need to see its enable toggle
    // before it is operational.
    read_word(0,0); read_word(1,0);
    write_word(0,0,32'hcafef00d,15); write_word(0,511,32'hdeadbeef,15);
    write_word(1,0,32'h01020304,15); write_word(1,511,32'h12345678,15);
    check_word(0,0,32'hcafef00d); check_word(0,511,32'hdeadbeef);
    check_word(1,0,32'h01020304); check_word(1,511,32'h12345678);
    write_word(1,5,32'h11223344,15); write_word(1,5,32'haabbccdd,5);
    check_word(1,5,32'h11bb33dd);
    write_word(0,32,32'h100000b7,15); // lui x1,0x10000
    write_word(0,33,32'h02a00113,15); // addi x2,x0,42
    write_word(0,34,32'h0020a023,15); // sw x2,0(x1)
    write_word(0,35,32'h0000c183,15); // lbu x3,0(x1)
    write_word(0,36,32'h00118213,15); // addi x4,x3,1
    write_word(0,37,32'h0040a223,15); // sw x4,4(x1)
    write_word(0,38,32'h0000006f,15); // jal x0,0
    @(negedge clk); run=1;
    wait(stores==2); repeat(6) @(negedge clk);
    if(alert!==0) $fatal(1,"Ibex alert");
    run=0;
    repeat(3) @(negedge clk);
    check_word(1,0,42); check_word(1,1,43);
    $display("PASS: real SRAM banks, byte masks, boundary addresses, Ibex program stores 42 and 43");
    $finish;
  end
  initial begin #200000; $fatal(1,"bounded simulation timeout"); end
endmodule

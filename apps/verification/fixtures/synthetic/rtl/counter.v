// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors
`include "defs.vh"

module counter #(
    parameter WIDTH = `DEFAULT_WIDTH
) (
    input  wire             clk,
    input  wire             rst_n,
    output reg  [WIDTH-1:0] q
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            q <= {WIDTH{1'b0}};
        else
            q <= q + 1'b1;
    end
endmodule

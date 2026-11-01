<!-- SPDX-License-Identifier: Apache-2.0 -->
# Native connectivity projection

`rusteda export -w W --rev revN --netlist graph.json --kind connectivity-json`
reads the validated immutable netlist tables directly. It is a read-only query;
it does not call a Verilog frontend or change the workspace revision. The default
export kind remains structural Verilog.

The JSON has `modules`, top-level `ports`, `cells`, `netnames`, and per-instance
`type`, `port_directions`, and `connections`. Integer bit IDs identify physical
alias components. Constants are strings `"0"` and `"1"`. An unconnected pin is
`"x"`, preserving its unknown value instead of tying it to zero. Cell modules
describe opaque library interfaces for connectivity consumers, not replacement
functional models. Original Liberty and macro models remain required for STA,
simulation and equivalence checking.

Bit arrays use Verilog LSB-first order. `offset`, `upto` and the explicit
`is_bus` flag preserve ascending, descending, nonzero and one-bit ranges. An
identifier containing brackets remains a scalar unless `--cell-buses JSON`
supplies `{ "cell": { "port": [msb, lsb] } }`. Every covered member must exist
and have the same supported direction. Missing, overlapping or conflicting
interfaces fail explicitly.

Continuous structural aliases use bounded union by rank and path compression.
Conflicting zero/one aliases are rejected. The canonical ID is the minimum
original net ID in its component plus two; IDs need not equal another tool's
numbering. Compare connectivity by named pin and net-label isomorphism.

The entity caps are 100,000 instances, 500,000 nets, 500,000 connected or
declared instance pins, 65,536 library pins and 128 MiB of table data. Before
allocation, the exporter estimates union-find arrays, name groups, formal-port
plans and collision indexes against a 64 MiB auxiliary cap. The streamed output
has a separate 64 MiB cap. `--connectivity-max-aux-bytes`,
`--connectivity-max-output-bytes` and `--connectivity-max-work` can lower the
respective 64 MiB, 64 MiB and ten-million-operation ceilings.

The receipt records revision, source records, table hash, schema hash, output
hash/byte count and actual admission budgets. Output uses a pending file plus
atomic rename. A failed query removes its pending file and preserves an existing
destination. No whole JSON document or unbounded graph paths are materialized.

Validation includes independent black-box Yosys connectivity comparisons using
original IHP/EZ130 SRAM, Sky130 and GF180 libraries, alongside range/alias and
atomic-budget controls. This projection alone establishes no physical or timing
signoff result (`RCLI-REQ-0020`).

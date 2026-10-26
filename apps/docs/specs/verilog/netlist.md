# Structural netlist reader and core netlist conversion

The structural netlist reader (`read_structural`) and conversion between the writer model
(`NlModule`, VERILOG-REQ-0300) and `rusteda_core::netlist::GateNetlist`
(`to_gate_netlist`, `from_gate_netlist`) in `rusteda-formats::verilog::netlist`.

## Structural reader

**VERILOG-REQ-0310** `read_structural(text, file, cell_pins)` reads one flat structural module from
`text` (located as `file` in diagnostics), resolves library cells and their pins in library order
through `cell_pins`, and returns a `GateNetlist` or a `ReadError` carrying located diagnostics.

**VERILOG-REQ-0311** Accepted module items are `input`, `output` and `inout` ports (scalar or with
a constant range, ANSI or non-ANSI style), `wire` declarations, cell instances with named port
connections, and continuous assignments (`assign`). A connection or assignment side is an
identifier, a bit-select with a constant index, or the constant `1'b0` or `1'b1`; an identifier of
a vector stands for all its bits from MSB to LSB. Range bounds and bit indices are integer
literals, negated only when they are plain decimal numbers.

**VERILOG-REQ-0312** An undeclared identifier in a connection or on the left side of an assignment
declares an implicit scalar wire (IEEE 1800-2017 clause 6.10), unless `` `default_nettype none`` is
in effect. Under a `` `default_nettype`` other than `wire`, a port requires the net type `wire` in
its declaration (or, in non-ANSI style, in a `wire` declaration of its name). A non-ANSI port whose
declaration names a net type, or that a `wire` declaration repeats, is completely declared; a
second `wire` declaration of its name is an error (clause 23.2.2.1).

**VERILOG-REQ-0313** Every construct outside the structural subset is rejected with a located
diagnostic (`V0300`) that names the construct, never skipped: other declarations and procedures,
expressions (including parenthesized and `min:typ:max` expressions), concatenations, `x`/`z`
literals, gate primitives, instance arrays, positional or implicit connections, parameter values,
delays and drive strengths, a second module or an instance of the module itself (hierarchy), the
directives `` `include``, `` `unconnected_drive`` and `` `pragma``, and `` `default_nettype``
inside a module.

**VERILOG-REQ-0314** Attribute instances (`(* ... *)`), which the lexer skips (VERILOG-REQ-0002),
are scanned across the text, conditional branches and macro bodies, and checked strictly as
comma-separated simple or escaped identifier names, each without a value or with a string literal
value. Only `src` attributes are accepted; any other attribute or malformed attribute instance is
rejected. Macros with arguments, macros that expand macros, and macro expansions exceeding the text
length budget (at least 1 MiB) are rejected.

**VERILOG-REQ-0315** A declared range has at most `MAX_RANGE_WIDTH` ($2^{24}$) bits, and all ranges
together have at most one bit per byte of the source text (at least `MAX_RANGE_WIDTH`), bounding
the number of bit nets allocated from a short input.

**VERILOG-REQ-0316** Unknown cells or pins, a cell pin connected more than once, a bit-select index
outside the declared range, and width mismatches in connections or continuous assignments are
rejected with located diagnostics (`V0301`–`V0303`).

## Conversion and names

**VERILOG-REQ-0317** `to_gate_netlist` and `from_gate_netlist` convert between `NlModule` and
`GateNetlist`. Both `to_gate_netlist` and `read_structural` number entities in the same order:
ports first (bits from MSB to LSB), then declared wires in declaration order, then implicit nets at
their first use; cells in order of first use; instances, connections and assignments in source
order. Consequently, `to_gate_netlist` of a module and `read_structural` of its written text freeze
to identical tables (VERILOG-REQ-0301).

`to_frozen_netlist` may construct those canonical tables directly for explicitly declared,
plain-name writer modules. Every section must equal `to_gate_netlist(...).freeze()`, including
string and connection order. `FrozenBuildLimits` bounds net, instance, connection, string,
table-byte and auxiliary-storage counts before allocation. It validates the complete frozen
result. Unusual names, implicit nets, whole-vector connections and unsupported layouts return
to the general converter for its existing semantics and diagnostics; no partial tables escape.

The ordinary writer and export directly from frozen tables may format instance text in
parallel. Batches contain at most 8,192 instances, divided into chunks of 512. Each chunk has
a 2 MiB byte cap; at most 16 chunks are live, and an oversized chunk uses the serial writer.
The frozen exporter charges cached pin names and temporary text against its auxiliary limit.
Chunks are written in source order. One and sixteen workers must emit identical bytes for
ports, escaped names, instances, connections and assignments. Frozen export continues to
stream from validated mapped tables without constructing a mutable gate graph or a complete
output string.

**VERILOG-REQ-0318** Port, net and instance names are stored in `GateNetlist` as single-segment
escaped hierarchical names (`rusteda_core::names::escape_segment`: `\` becomes `\\` and `/` becomes
`\/`), taking an escaped Verilog identifier without its leading backslash and terminating white
space. `from_gate_netlist` recovers the raw identifier text (`unescape`), which the writer emits as
an escaped identifier unless it is a simple non-keyword identifier.

**VERILOG-REQ-0319** Module, cell and pin names are stored and converted verbatim without
hierarchical segment escaping; names with characters outside printable ASCII (`0x21..=0x7E`) are
rejected by `GateNetlist`.

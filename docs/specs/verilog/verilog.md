# SystemVerilog reader and netlist writer

The SystemVerilog reader and the structural netlist writer of `rusteda-formats` (module
`verilog`). Source: IEEE 1800-2017 [@ieee1800_2017] (lexical conventions clause 5, preprocessor
clause 22, syntax Annex A) and IEEE 1364-2005 [@ieee1364_2005] for netlists.

## Lexical level

**VERILOG-REQ-0001** Tokens: white space; `//` and `/* */` comments; simple identifiers
(`[a-zA-Z_][a-zA-Z0-9_$]*`); escaped identifiers (backslash to the next white space); system
identifiers (`$name`); the reserved keywords of IEEE 1800-2017 Annex B; integer literals in all
forms of clause 5.7.1 (sized, unsized, based, signed, digits `x z ?` and `_` separators); unbased
unsized literals `'0 '1 'x 'z`; real literals (tokenized so that they can be rejected by users);
string literals with the escapes of clause 5.9; all operators and punctuators of clause 11 and
Annex A, including `'{`, `::`, `+:`, `-:`, `==?`, `!=?`, `<<<`, `>>>`, `->`, `<->`, `##`, `@*`.

**VERILOG-REQ-0002** Attribute instances `(* ... *)` are skipped. Every token carries a source span
(file, byte range) that can be converted to line and column.

## Preprocessor

**VERILOG-REQ-0200** `` `define`` with and without formal arguments, default argument values, line
continuation, `` ` ``` `` token pasting, `` `" `` stringification and `` `\`" `` escapes;
`` `undef``, `` `undefineall``.

**VERILOG-REQ-0201** Conditional compilation `` `ifdef``, `` `ifndef``, `` `elsif``, `` `else``,
`` `endif``, nested to any depth.

**VERILOG-REQ-0202** `` `include "file"`` and `` `include <file>``, searched in the directory of
the including file and then in each include directory of the preprocessor options, in order;
include depth is limited to 64.

**VERILOG-REQ-0203** `` `timescale``, `` `celldefine``, `` `endcelldefine``, `` `resetall``,
`` `pragma``, `` `line``, `` `begin_keywords``, `` `end_keywords``,
`` `unconnected_drive``, `` `nounconnected_drive`` are accepted and have no effect on the token
stream; `` `default_nettype`` is recorded (value `none` makes implicit nets an error).

**VERILOG-REQ-0204** `` `__FILE__`` and `` `__LINE__`` expand to the current file name string and
line number.

**VERILOG-REQ-0205** Macro expansion is limited to a nesting depth of 256; exceeding it is an error.
Use of an undefined macro is an error.

**VERILOG-REQ-0206** The reader reads one or more sources, files (`read_sources`) or in-memory texts
(`read_texts`), processed in the order given. Preprocessor state (macro definitions,
`` `default_nettype``) carries over from one source to the next, as if the sources were
concatenated; each source is otherwise its own compilation unit.

## Syntax

**VERILOG-REQ-0100** The parser accepts the constructs of VERILOG-REQ-0103 and VERILOG-REQ-0104
and builds an abstract syntax tree that keeps every one of them. Rejected constructs are reported
by the consumer, not by the parser, when they are syntactically recognizable.

**VERILOG-REQ-0101** Syntax errors report the location and the expected token class; the parser
never panics and limits expression and statement nesting to 1024 levels.

**VERILOG-REQ-0102** Attribute instances `(* ... *)` are skipped, except that the attribute names
`full_case` and `parallel_case` of the attribute instances directly before a procedural `case`,
`casez` or `casex` statement (or its `unique`/`unique0`/`priority` qualifier) are recorded on that
statement. Attribute values are ignored. Attributes before a generate `case` are ignored.

**VERILOG-REQ-0103** Accepted constructs:
- design units `module`, `interface` and `package`, and declarations in the compilation-unit
  scope (`typedef`, `localparam`, `parameter`, `function`, `import`);
- data types: the integral atom and vector types `logic`, `reg`, `bit`, `integer`, `int`,
  `shortint`, `longint` and `byte` with optional `signed` or `unsigned`, and implicit types of nets
  and ports; packed arrays with any number of packed dimensions; fixed-size unpacked arrays (`[N]`
  and `[a:b]` forms, any number of dimensions); enumerations with an explicit or default (`int`)
  base type and explicit or implicit values; packed structures and packed unions, signed or
  unsigned, nested; `typedef` of any of these, package-scoped type names (`p::t`) and type
  parameters (`parameter type T = ...`); nets `wire`, `tri` and `uwire` with or without a data
  type; variable declarations with an initializer (`logic [3:0] c = 4'd5;`) and net declaration
  assignments (`wire w = a & b;`);
- module items: port declarations in ANSI and non-ANSI style; `parameter`, `localparam` and type
  parameters in the parameter port list and in the body; net and variable declarations; `typedef`;
  `import` of package items (explicit and wildcard); continuous assignments; `always`,
  `always_comb`, `always_ff` and `always_latch`; module instances with named, ordered,
  implicit-name (`.p`) and wildcard (`.*`) port connections and named or ordered parameter
  overrides; the gate primitives `and`, `nand`, `or`, `nor`, `xor`, `xnor`, `not` and `buf`;
  `generate`/`endgenerate` regions; loop, conditional and case generate constructs with optional
  labels; `genvar`; `function` and `task` declarations; the elaboration system tasks `$error`,
  `$fatal`, `$warning` and `$info`;
- interfaces (IEEE 1800-2017 clause 25) with everything a module may contain plus `modport`
  declarations; interface instances (`bus_if #(.W(8)) u_bus (.clk(clk));`); module ports of
  interface type written `intf_name port`, `intf_name.modport port` or `interface[.modport] port`,
  connected by name, position, `.port` or `.*` to an interface instance or to an interface port of
  the parent, optionally with a modport (`.p(u_bus.master)`); `port.item` references;
- statements: blocking and nonblocking assignment, compound assignment operators, increment and
  decrement, `if`/`else` with `unique`, `unique0` or `priority` qualifiers, `case`/`casez`/`casex`
  and `case ... inside` with qualifiers, `for` (with loop variable declarations and several
  initializers and steps), `while`, `do ... while`, `repeat`, `foreach` over fixed-size arrays,
  `break`, `continue`, `return`, sequential blocks with local declarations, event controls at the
  start of an `always` procedure, delay controls, and calls of functions, tasks, void functions and
  system tasks;
- expressions: all integral operators of clause 11, including `inside`, `==?`/`!=?` and `**`, the
  conditional operator, concatenation and replication (also as assignment targets), bit-selects,
  part-selects and indexed part-selects with constant or variable positions, structure member
  selection, array element selection, casts (`T'(e)`, `N'(e)`, `signed'(e)`, `unsigned'(e)`),
  assignment patterns (positional, member-keyed, index-keyed, type-keyed and `default`), unbased
  unsized literals (`'0`, `'1`, `'x`, `'z`), enumeration constants, function calls, and the system
  functions `$clog2`, `$bits`, `$signed`, `$unsigned`, `$size`, `$left`, `$right`, `$high`, `$low`,
  `$countones`, `$onehot`, `$onehot0` and `$isunknown`.

**VERILOG-REQ-0104** The following constructs are parsed and kept as opaque items with their span:
`initial` and `final` procedures, immediate and concurrent assertions, `assume`, `cover`,
`property`, `sequence`, `specify` blocks, `timeunit`/`timeprecision`, delay values, and
`default clocking`/`default disable iff` declarations. Attribute instances `(* ... *)` are skipped
(VERILOG-REQ-0102).

**VERILOG-REQ-0105** A single expression can be read from a named text (`parse_expression`), for
example a parameter value given on a command line. The text is preprocessed as a source of its
own, without the macros of other sources, and must hold exactly one expression of
VERILOG-REQ-0103; a text without an expression, a malformed expression, tokens after the
expression and preprocessor errors are reported with their location in the named text
(VERILOG-REQ-0101).

## Netlist writer

**VERILOG-REQ-0300** The writer emits one module of the structural subset (VERILOG-REQ-0302). Names
that are not simple identifiers, or that are keywords, are written as escaped identifiers
(backslash, name, one space).

**VERILOG-REQ-0301** Reading a written netlist with this crate's parser yields the same modules,
ports, nets, instances, connections and assignments (round trip).

**VERILOG-REQ-0302** The structural subset of IEEE 1364-2005 for gate-level netlists: modules that
use only ANSI port declarations, `wire` declarations, cell instances with named port connections,
and `assign` statements whose right-hand side is a net, a bit of a net, or the constant `1'b0` or
`1'b1`.

See [netlist.md](netlist.md) for the structural netlist reader and `GateNetlist` conversion
(`VERILOG-REQ-0310` to `VERILOG-REQ-0319`).

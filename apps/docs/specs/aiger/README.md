# AIGER reader

Source: the AIGER and-inverter graph format, its ASCII (`aag`) and binary (`aig`) forms
[@biere2007aiger], and the header and latch extensions of version 1.9 [@biere2011aiger]. This
document states what the AIGER reader of `rusteda-formats` (module `aiger`) accepts, what it
returns and what it reports. What a tool makes of the returned graph is specified by that tool.

| Section | Requirements |
|---|---|
| [Entry points and result](#entry-points-and-result) | AIGER-REQ-0001 to 0002 |
| [Header, literals and definitions](#header-literals-and-definitions) | AIGER-REQ-0010 to 0013 |
| [Sections](#sections) | AIGER-REQ-0020 to 0024 |
| [Limits and messages](#limits-and-messages) | AIGER-REQ-0030 to 0032 |

## Entry points and result

**AIGER-REQ-0001** `read_file(path, options)` reads a file from disk and `read_bytes(bytes, name,
options)` reads bytes; the first word of the header selects the ASCII or the binary form. The
result is either an `Aig` (AIGER-REQ-0002) or the list of messages, with at least one error
(AIGER-REQ-0032). A file that cannot be read is an error `G0001`.

**AIGER-REQ-0002** `Aig` holds the maximum variable index `M`; the input literals in input order;
the latches (current-state literal, next-state literal and reset value, AIGER-REQ-0021) in latch
order; the output literals; the bad-state, invariant-constraint, justice and fairness literals
(AIGER-REQ-0022); the AND gates as `(lhs, rhs0, rhs1)` in an order where every gate follows the
gates it reads; the symbol names of inputs, latches, outputs and properties (AIGER-REQ-0023); and
whether the file was binary. A literal is twice a variable index plus one when complemented;
literal 0 is the constant false and literal 1 the constant true.

## Header, literals and definitions

**AIGER-REQ-0010** The header is the first line: `aag` or `aig`, then five to nine decimal numbers
`M I L O A [B C J F]` separated by single spaces: the maximum variable index and the numbers of
inputs, latches, outputs, AND gates, bad-state properties, invariant constraints, justice
properties and fairness constraints; missing trailing numbers are 0. Each number fits in 32 bits.
Any other header, a number with a sign or leading zeros (other than `0` itself), and `M` below
`I + L + A` are errors `G0002`. In the binary form `M` equals `I + L + A`.

**AIGER-REQ-0011** Every literal is at most `2M + 1`. Inputs, latches and AND gates define
variables: an input or a latch is an even literal that names the variable it defines, and an AND
gate's left-hand side is an even literal. A defining literal below 2, a variable defined twice, an
odd defining literal and a literal above `2M + 1` are errors `G0003`. Any other literal, read by a
latch, an output, a property or an AND gate, refers to the constant or to a defined variable; a
reference to a variable that nothing defines is an error `G0004`.

**AIGER-REQ-0012** In the ASCII form every input, latch, output, property and AND gate is a line of
decimal numbers separated by single spaces, in the order of the header: inputs `lit`, latches
`lit next [reset]`, outputs `lit`, bad-state properties `lit`, invariant constraints `lit`, the
sizes of the justice properties one per line followed by their literals one per line, fairness
constraints `lit`, AND gates `lhs rhs0 rhs1`. Inputs, latches and gates may define their variables
in any order. The AND gates must not form a cycle: a cycle is an error `G0005`, found without
recursion.

**AIGER-REQ-0013** In the binary form inputs are implicit: input `k` (from 0) is variable `k + 1`,
latch `k` defines variable `I + k + 1` and its line holds `next [reset]`, and AND gate `k` defines
variable `I + L + k + 1`. Outputs and properties are ASCII lines as in AIGER-REQ-0012. The AND gates
follow in binary: for gate `k` with left-hand side `lhs = 2(I + L + k + 1)` and inputs
`rhs0 >= rhs1`, the two differences `lhs - rhs0` and `rhs0 - rhs1` are each written as an unsigned
number in groups of 7 bits, least significant group first, every byte but the last with its top bit
set. A difference of 0 for `lhs - rhs0`, a number that does not fit in 32 bits, more than five bytes
for one number, an input above its left-hand side and data that ends inside the gates are errors
`G0006`.

## Sections

**AIGER-REQ-0020** Lines end with a line feed; a carriage return before it is accepted in the ASCII
lines of both forms. The last line may lack its line feed.

**AIGER-REQ-0021** A latch without a reset value resets to 0. A reset value is `0`, `1`, or the
latch's own current-state literal, which marks the latch as uninitialized; any other value is an
error `G0003`.

**AIGER-REQ-0022** Bad-state properties, invariant constraints, justice properties and fairness
constraints are read and returned. They are properties for model checking, not outputs: unless
`Options::allow_properties` is set, a file whose header announces any of them is an error `G0010`.

**AIGER-REQ-0023** After the AND gates an optional symbol table assigns names: a line `i<k> <name>`,
`l<k> <name>`, `o<k> <name>`, `b<k> <name>`, `c<k> <name>`, `j<k> <name>` or `f<k> <name>`
names input, latch, output, bad-state property, invariant constraint, justice property or fairness
constraint `k` (from 0); the name is the rest of the line and may contain blanks but not be empty.
A position outside its section, a second name for the same position, and any other line are errors
`G0007`.

**AIGER-REQ-0024** A line `c` alone starts the comment section, which extends to the end of the
file and has no effect on the graph; the reader skips it without a message.

## Limits and messages

**AIGER-REQ-0030** Limits (`Limits`): `M` is at most `max_vars` (default 2^24) and the outputs and
properties together are at most `max_items` (default 2^24); a larger header is an error `G0008`
before anything is allocated. In the ASCII form each definition, output and property takes a line,
and in the binary form each AND gate takes at least two bytes: a header that announces more of them
than the rest of the data can hold is an error `G0008` before they are allocated.

**AIGER-REQ-0031** Malformed input of any kind, truncated or corrupted binary data included,
produces messages and never a panic.

**AIGER-REQ-0032** Messages are `rusteda_core::diag::Diag` values located at the file, line and
column (CLI-REQ-0004); an error in the binary AND gates is located at the line where they start and
names the gate and the byte offset in the file. Codes: `G0001` (unreadable file), `G0002` (header),
`G0003` (definitions and reset values), `G0004` (undefined variable), `G0005` (cycle), `G0006`
(binary AND gates), `G0007` (symbol table and trailing text), `G0008` (limits), `G0009` (malformed
ASCII line: a missing or extra number, a word that is no decimal number) and `G0010` (properties).

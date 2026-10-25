# BLIF reader

Source: the Berkeley Logic Interchange Format as published with the logic synthesis systems of the
University of California, Berkeley [@blif1992]. This document states what the BLIF reader of
`rusteda-formats` (module `blif`) accepts, what it returns and what it reports. What a tool makes
of the returned network (logic, clocks, initial values, cells) is specified by that tool.

| Section | Requirements |
|---|---|
| [Entry points and result](#entry-points-and-result) | BLIF-REQ-0001 to 0003 |
| [Text](#text) | BLIF-REQ-0010 to 0011 |
| [Models and signals](#models-and-signals) | BLIF-REQ-0020 to 0023 |
| [Logic, latches and cells](#logic-latches-and-cells) | BLIF-REQ-0030 to 0034 |
| [Hierarchy](#hierarchy) | BLIF-REQ-0040 to 0042 |
| [Other constructs](#other-constructs) | BLIF-REQ-0050 to 0052 |
| [Limits and messages](#limits-and-messages) | BLIF-REQ-0060 to 0062 |

## Entry points and result

**BLIF-REQ-0001** `read_files(paths, options)` reads BLIF files from disk and `read_texts(texts,
options)` reads `(name, text)` pairs; `options` holds the top model (`Options::top`, default the
first model of the first file, BLIF-REQ-0042) and the limits of BLIF-REQ-0060. The models of all
files form one set; the files are read in the order given. The result is either a `Read` (the
flattened top model as a `Network` and the warnings, BLIF-REQ-0003) or the list of messages with at
least one error (BLIF-REQ-0062). A file that cannot be read, or whose text is not UTF-8, is an
error `B0001`.

**BLIF-REQ-0002** The reader does not interpret library cells (BLIF-REQ-0033), does not check the
network for combinational cycles and does not optimize: every `.names`, `.latch`, `.gate` and
`.mlatch` of the flattened top model is returned, in the order of a depth-first walk of the
hierarchy that visits the commands of each model in source order and the commands of a `.subckt`
instance in place of its line.

**BLIF-REQ-0003** `Network` holds the top model's name; the signal names, one per signal, so that a
signal is an index into them; the primary inputs in declaration order (`.inputs` lines first, then
`.clock` lines, BLIF-REQ-0021), the clock inputs among them, and the primary outputs in declaration
order; the logic nodes (`.names`: input signals, output signal and cover, BLIF-REQ-0030); the
latches (`.latch`, BLIF-REQ-0031); and the cell instances (`.gate` and `.mlatch`, BLIF-REQ-0033).
Every signal that no cell instance connects has at most one driver: a primary input, a node or a
latch (BLIF-REQ-0022, BLIF-REQ-0034).

## Text

**BLIF-REQ-0010** A `#` starts a comment that ends at the end of the line. A line whose text,
without its comment and trailing blanks, ends with a backslash `\` continues on the next line: the
backslash is replaced by a blank and the lines are joined into one logical line, which is located
at its first line. Blanks are spaces, tabs and carriage returns; words are separated by blanks.
Empty logical lines are ignored. A word that starts with `.` at the start of a logical line is a
command; any other logical line is a row of the cover of the `.names` before it (BLIF-REQ-0030).

**BLIF-REQ-0011** Signal and model names are words: any characters except blanks and, in
`formal=actual` connections, `=`. A name that contains a control character (below `0x20` or
`0x7F`) is an error `B0002`.

## Models and signals

**BLIF-REQ-0020** `.model [name]` starts a model; without a name the model takes the file name
without its directory and its last extension. `.end` ends a model; a model also ends at the next
`.model` or at the end of its file. A command outside a model, other than `.search`
(BLIF-REQ-0050), is an error `B0002`; so is a file without a model. Two models of the same name
in the files are an error `B0005`.

**BLIF-REQ-0021** `.inputs`, `.outputs` and `.clock` take lists of signal names and may be
repeated; repeated lists are concatenated. Signals listed by `.clock` are clock inputs: primary
inputs of the model, listed after those of `.inputs`. A signal listed twice as an input (by
`.inputs` or `.clock`), or twice as an output, is an error `B0005`. A signal may be both an input
and an output of a model.

**BLIF-REQ-0022** Within a model every signal has one driver: a primary input, the output of a
`.names`, the output of a `.latch`, an output pin of a `.gate` or `.mlatch`, or a formal output of a
`.subckt` (BLIF-REQ-0040). A second driver is an error `B0006`. A signal that is read (by a
`.names`, a `.latch`, a cell or a `.subckt` input) or listed as an output and that has no driver is
kept without a driver, for the consumer to give it a value, and reported with one warning `B0007`
per read, located at the first such signal and naming up to eight of them with their number.

**BLIF-REQ-0023** The `Network` keeps every signal of the flattened top model, read or not; a
signal that only an unused node reads is still driven and read like any other.

## Logic, latches and cells

**BLIF-REQ-0030** `.names in1 ... inN out` defines `out` by the single-output cover in the rows that
follow. A row is the input plane, a word of N characters from `0`, `1` and `-`, then a blank and the
output character `0` or `1`; with N = 0 the row is the output character alone. All rows of one cover
have the same output character: `1` makes the rows a cover of the on-set, `0` of the off-set.
Function: a row is the AND of its literals (`1`: the input, `0`: its complement, `-`: no literal),
so a row without literals is 1; the function of an on-set cover is the OR of its rows and that of an
off-set cover is the complement of that OR; a cover without rows is the constant 0. A row of the
wrong length or with another character, a row without a `.names` before it and rows with
different output characters are errors `B0003`. A signal may appear several times among the
inputs.

**BLIF-REQ-0031** `.latch in out [type control] [init]` is a latch with data input `in` and output
`out`. `type` is `fe` (falling edge), `re` (rising edge), `ah` (active high) or `al` (active low),
and `control` is the clock signal, or `NIL` for the global clock; without `type` and `control` the
latch is controlled by the global clock. `init` is the initial value: `0`, `1`, `2` (don't care) or
`3` (unknown); without `init` it is `3`. Any other form is an error `B0004`; the type `as`
(asynchronous), whose behavior BLIF does not define, is an error `B0010` (BLIF-REQ-0050).

**BLIF-REQ-0032** The `Network` gives each latch its data, output and control signals, its type
(`None` for the global clock) and its initial value as written (`0`, `1`, `2` or `3`).

**BLIF-REQ-0033** `.gate cell f1=a1 ... fn=an` is an instance of the library cell `cell` with
formal pin `fi` connected to signal `ai`; `.mlatch cell f1=a1 ... fn=an control [init]` is an
instance of a sequential library cell whose clock pin is driven by `control` (`NIL` for the global
clock), with an initial value as in BLIF-REQ-0031. The reader does not know which formal pins are
outputs: it records the cell name, the connections in source order and, for `.mlatch`, the control
and the initial value, and the consumer resolves them against a library. A formal pin connected twice
or a connection without `=` is an error `B0002`. Without a library the consumer rejects every
instance (SYNTH-REQ-0909, CHECK-REQ-0105).

**BLIF-REQ-0034** The reader does not know the directions of cell pins. For BLIF-REQ-0022 a signal
that a cell instance connects counts as driven and is not checked for a second driver; a consumer
that resolves the pins against a library checks the drivers of those signals itself
(SYNTH-REQ-0909, CHECK-REQ-0105).

## Hierarchy

**BLIF-REQ-0040** `.subckt model f1=a1 ... fn=an` instantiates `model`; every formal `fi` is a
primary input or output of that model and is connected to the signal `ai` of the instantiating
model. Each formal input must be connected; a formal output may be left unconnected. An unknown
model, a formal that is not a port of the model and an unconnected formal input are errors `B0008`;
a formal connected twice is an error `B0002`, as for cells (BLIF-REQ-0033). A formal that is both an input and an output of the instantiated model
is one signal: its actual is read by the instance and, through it, read by the instantiating
model.

**BLIF-REQ-0041** The reader flattens the hierarchy of the top model. The k-th `.subckt` line of a
model (counting from 0 in source order) is an instance named `<model>_<k>` after the model it
instantiates; a signal of an instance that is not connected through a formal is named
`<path>.<signal>`, where `<path>` is the instance names from the top model down, joined by `.`.
A flattened name that equals the name of another signal is an error `B0012`. A model that
instantiates itself, directly or through other models, is an error `B0009`; the walk uses an
explicit stack, so the depth of the hierarchy does not depend on the call stack.

**BLIF-REQ-0042** The top model is `Options::top` when given (an error `B0013` when no model has that
name), else the first model of the first file. Models that the top model does not instantiate are
not read further.

## Other constructs

**BLIF-REQ-0050** Constructs that would change the function of the network if they were ignored,
and that the reader does not support, are errors `B0010`: `.search` (models from another file),
the state-transition description `.start_kiss` with `.end_kiss`, `.latch_order` and `.code`,
`.blackbox` in the top model or in an instantiated model, the latch type `as`, the external
connection `.conn`, and every unknown command.

**BLIF-REQ-0051** Constructs without effect on the function of the network are skipped with one
warning `B0020` per command name and file, located at the first occurrence and giving the count:
the delay, load, drive, arrival and required-time constructs (`.area`, `.delay`,
`.wire_load_slope`, `.wire`, `.input_arrival`, `.default_input_arrival`, `.output_required`,
`.default_output_required`, `.input_drive`, `.default_input_drive`, `.output_load`,
`.default_output_load`, `.max_input_load`, `.default_max_input_load`), the clock constraints
(`.cycle`, `.clock_event`) and the annotations `.attr`, `.attrib`, `.param` and `.cname`.

**BLIF-REQ-0052** `.exdc` starts an external don't-care network, which extends to the end of the
model. It is skipped with a warning `B0021`: the function of the model is kept exactly, without
using the don't cares.

## Limits and messages

**BLIF-REQ-0060** Limits (`Limits`): the flattened network has at most `max_items` nodes, latches
and cell instances together (default 2^22), and at most `max_refs` signals and signal references in
node inputs and cell connections together (default 2^26). A network that would exceed a limit is an error
`B0011` before it is allocated: every instance of a model adds what the model and its instances
hold, so a hierarchy that doubles at every level stops at the limit, not at the end of memory.
Covers are shared between the instances of a model, not copied.

**BLIF-REQ-0061** Malformed input of any kind produces messages and never a panic.

**BLIF-REQ-0062** Messages are `rusteda_core::diag::Diag` values located at the file, the line and
the column of the word at fault (CLI-REQ-0004): errors `B0001` (unreadable file or text that is not
UTF-8), `B0002` (syntax: a command outside a model, a missing or malformed word, a bad name),
`B0003` (cover rows), `B0004` (latches), `B0005` (a model or port declared twice), `B0006` (a
signal with two drivers), `B0008` (instances of models),
`B0009` (recursive instantiation), `B0010` (unsupported constructs, BLIF-REQ-0050), `B0011` (limits),
`B0012` (flattened name collision) and `B0013` (top model); warnings `B0007` (signals without a
driver, BLIF-REQ-0022), `B0020` and `B0021` (BLIF-REQ-0051, 0052).

# Liberty reader

Source: the Liberty library format as published by the Liberty Technical Advisory Board
[@liberty2017]. This document states what the Liberty reader of `rusteda-formats` (module
`liberty`: `read_file`, `read_text`, `read_file_diags`, `read_text_diags` and `parse_function`)
accepts and reports. The library model it builds is specified with `rusteda-core`:
[Liberty library model](../core/README.md#liberty-library-model) (LIBERTY-REQ-0010 to 0019,
0022 to 0026, 0030 to 0034 and 0040 to 0049).

| Section | Requirements |
|---|---|
| [Syntax and units](#syntax-and-units) | LIBERTY-REQ-0001 to 0007 |
| [Boolean functions](#boolean-functions) | LIBERTY-REQ-0020 to 0021 |
| [Reader messages and construct classification](#reader-messages-and-construct-classification) | LIBERTY-REQ-0050 to 0059 |

## Syntax and units

**LIBERTY-REQ-0001** A Liberty file is a sequence of statements. A statement is one of:
- a group: `name ( [value {, value}] ) { statement* }`;
- a simple attribute: `name : value ;`;
- a complex attribute: `name ( [value {, value}] ) ;`;
- a define statement, which is a complex attribute named `define`, `define_group` or
  `define_cell_area`.
The terminating `;` of an attribute may be omitted when the attribute ends a line.

**LIBERTY-REQ-0002** Values are numbers, identifiers (unquoted words that may contain letters,
digits and the characters `_ . [ ] : ! ' + - * / < >` as long as they are not ambiguous with
punctuation), or double-quoted strings. Inside strings `\"` denotes a quote and a backslash
followed by a newline is removed. A backslash followed by a newline outside strings joins lines.
A simple attribute value may consist of several words and operators up to the `;` (for example
`nom_voltage : 1.2 ;` or `function : A & B ;`); the reader keeps the text.

**LIBERTY-REQ-0003** Comments `/* ... */` and `// ...` (to the end of the line) are ignored.

**LIBERTY-REQ-0004** Malformed input produces an error with file, line and column and never a panic.
Group nesting deeper than 64 levels is an error.

**LIBERTY-REQ-0005** Unit attribute syntax: `time_unit` (`s`, `ms`, `us`, `ns`, `ps`, `fs`),
`pulling_resistance_unit` (`kohm`, `ohm`), `voltage_unit` (`v`, `mv`, `uv`), `current_unit` (`a`,
`ma`, `ua`, `na`, `pa`) and `leakage_power_unit` (`w`, `mw`, `uw`, `nw`, `pw`, `fw`) are simple
attributes whose value is a positive finite number (1 when omitted) followed by a unit suffix,
with letter case and intervening blanks ignored. `capacitive_load_unit (<number>, <unit>)` is a
two-argument complex attribute with a positive finite number and a capacitance unit (`f`, `mf`,
`uf`, `nf`, `pf`, `ff`, letter case ignored).

**LIBERTY-REQ-0006** Unit validation: when a unit attribute of the library group parses according
to LIBERTY-REQ-0005, its `Unit` in `Library::units` (LIBERTY-REQ-0017) has `given = true`, its
`scale` in the fixed unit (1 ns, 1 pF, 1 kOhm, 1 V, 1 mA, 1 nW) and its attribute text. When the
attribute is absent, the unit stays defaulted (`scale = 1.0`, `given = false`, `text = None`).
When the attribute is present with an invalid text, the reader reports an error `L0001` at
`library/<unit>`, keeps the text in `Unit::text`, and leaves `scale = 1.0` and `given = false`.

**LIBERTY-REQ-0007** Threshold and slew-derate validation: each threshold percentage attribute
(`input_threshold_pct_rise`, `input_threshold_pct_fall`, `output_threshold_pct_rise`,
`output_threshold_pct_fall`, `slew_lower_threshold_pct_rise`, `slew_upper_threshold_pct_rise`,
`slew_lower_threshold_pct_fall`, `slew_upper_threshold_pct_fall`) must be a finite number in
`(0, 100)`, with `slew_lower_threshold_pct_<dir> < slew_upper_threshold_pct_<dir>` (against the
default 20/80 when only one end is given), and `slew_derate_from_library` must be a finite number
in `(0, 1]`. An out-of-range or inverted value emits an error `L0002` at `library/<attr>` and
leaves the field `None` (defaulted, LIBERTY-REQ-0045).

## Boolean functions

**LIBERTY-REQ-0020** Function expressions use the grammar below, where juxtaposition of two operands
means AND. Precedence from highest to lowest: complement (`!` prefix, `'` postfix), `^` (XOR),
AND (`&`, `*`, juxtaposition), OR (`|`, `+`). Binary operators associate to the left.

```text
or      := and { ("|" | "+") and }
and     := xor { ("&" | "*" | <juxtaposition>) xor }
xor     := unary { "^" unary }
unary   := "!" unary | primary { "'" }
primary := "0" | "1" | name | "(" or ")"
name    := pin or state-variable name, optionally with a bus index "[" digits "]"
```

**LIBERTY-REQ-0021** A function (`function`, `three_state`, `state_function`, `when`, `ff`/`latch`
expressions) or `statetable` input list refers only to pins of its cell, internal nodes declared by
`internal_node` or `statetable`, and state variable names of `ff`/`latch` groups. Any other name is
an error for that cell (`L0202`); the cell is then marked unusable and a warning is issued, but
reading continues.

## Reader messages and construct classification

**LIBERTY-REQ-0050** Message-returning entry points: `read_file_diags(path)` and
`read_text_diags(text, name)` read the first `library` group of the input and return
`Result<(Library, Vec<Diag>), Error>`. `read_file(path)` and `read_text(text, name)` return the
same `Library` and fail on the same syntax, I/O and library-level model errors.

**LIBERTY-REQ-0051** Single-pass classification: every attribute and child group in the syntax
tree is classified when its enclosing group is read into the model, so that the syntax tree is
walked once. No construct is silently dropped: every statement is either consumed into the model,
classified as ignorable, or reported with a diagnostic.

**LIBERTY-REQ-0052** Aggregated messages: messages are aggregated by `(code, construct_path)`,
where `construct_path` is the slash-separated group and attribute path from `library` (for example
`library/cell/pin/timing/mode`). Each entry records the total occurrence count and the 1-based
line, column and message text of the first occurrence in source order. `read_*_diags` returns the
entries sorted by `(line, col, code, construct_path)`, formatted as
`<construct_path>: <text> (<N> occurrence[s])` with a help line.

**LIBERTY-REQ-0053** Reader diagnostic codes (`L` family):
- `L0001` (`error`): invalid unit attribute (LIBERTY-REQ-0006);
- `L0002` (`error`): out-of-range or inverted threshold percentage or slew derate
  (LIBERTY-REQ-0007);
- `L0100` (`warning`): recognized Liberty construct not represented in the model;
- `L0101` (`warning`): unknown attribute or group, or a consumed attribute in the wrong syntactic
  form (simple instead of complex, or complex instead of simple);
- `L0102` (`warning`): duplicate singleton attribute, singleton table group or template name;
- `L0110` (`warning`): unknown enumerated attribute value or non-Boolean flag value;
- `L0111` (`warning`): non-numeric value in a numeric attribute or table entry;
- `L0112` (`warning`): `timing` group without `related_pin`;
- `L0200` to `L0208` (`warning`): cell problems (LIBERTY-REQ-0057, LIBERTY-REQ-0059).

**LIBERTY-REQ-0054** Consumed constructs: the reader consumes the `library`, `lu_table_template`,
`power_lut_template`, `output_current_template`, `type`, `wire_load`, `wire_load_table`,
`wire_load_selection`, `operating_conditions`, `cell`, `pg_pin`, `pin`, `bus`, `bundle`,
`statetable`, `timing`, `internal_power`, table (`cell_rise`, `cell_fall`, `rise_transition`,
`fall_transition`, `rise_constraint`, `fall_constraint`, `power`, `rise_power`, `fall_power`), `ff`
and `latch` groups and their model attributes (LIBERTY-REQ-0010 to 0019, 0023 to 0026, 0031, 0032,
0040 to 0048 and 0059). A consumed attribute given in the wrong form emits `L0101`; a singleton
attribute or table group given more than once in the same group uses the first one (templates use
the last definition) and emits `L0102`.

**LIBERTY-REQ-0055** Ignorable and user-defined constructs: metadata, waveform, CCS/parasitic and
supply groups and attributes that do not change the library model, together with attributes and
groups declared in the library by `define (<attr>, <group>, <type>)`,
`define_group (<group>, <parent>)` or `define_cell_area (<attr>, <type>)`, produce no warning, so
standard cells in `sky130_fd_sc_hd` (`tt`/`ss`/`ff`) and `gf180mcu` 7t/9t (`tt`/`ss`/`ff`) load with
zero warnings.

**LIBERTY-REQ-0056** Unsupported and unknown constructs: recognized Liberty groups and attributes
that the model does not represent (such as `mode`, `scaling_factors`, `ff_bank`, `latch_bank` and
statements outside the first `library` group) emit `L0100`. Attributes and groups that are neither
recognized nor declared by `define*` emit `L0101`.

**LIBERTY-REQ-0057** Cell problems: an invalid pin `direction` (`L0200`), an unparseable Boolean
`function`, `three_state`, `state_function` or `when` (`L0201`), a function or `statetable`
reference to an unknown pin or state variable (`L0202`), a malformed `ff`, `latch` or `statetable`
group (`L0203`), a negative or non-finite pin limit (`L0204`), an undefined table template
(`L0205`), an inconsistent table value count (`L0206`), a table axis whose index points are not
strictly increasing (`L0207`) or a malformed or unsupported `bus` or `bundle` group (`L0208`)
appends a problem description to `Cell::problems` (marking the cell unusable) and emits one
aggregated warning.

**LIBERTY-REQ-0058** Attribute value warnings: an enumerated attribute (`timing_type`,
`timing_sense`, template `variable_1` to `variable_3`, `pg_type`, `level_shifter_type`,
`switch_cell_type`, `antenna_diode_type`, `default_wire_load_mode`, `tree_type`) whose value is
outside the known Liberty names is kept in its `Other(String)` variant and emits `L0110`; a Boolean
attribute whose value is neither `true`/`1` nor `false`/`0` evaluates to `false` and emits `L0110`;
a numeric attribute or table number that is not a finite number takes its default (or is skipped in
a table list) and emits `L0111`; a `timing` group without `related_pin` produces no arc and emits
`L0112`.

**LIBERTY-REQ-0059** Bus and bundle expansion: a `bus (<name>)` group with a valid `bus_type`
referencing a bit-array `type (<name>)` group (at cell or library scope, formatted with
`bus_naming_style`, default `"%s[%d]"`) and a `bundle (<name>)` group with `members (...)` expand
into scalar `Pin` entries in `Cell::pins` in declaration order, inheriting bus/bundle attributes
and child `timing`/`internal_power` groups with per-member `pin (...)` overrides applied. If a
`bus` or `bundle` group is malformed, references an unknown or invalid `bus_type`, or contains an
unsupported or unknown construct, the reader records a cell problem `L0208` in `Cell::problems`
(never silently dropping the cell or its bus/bundle).

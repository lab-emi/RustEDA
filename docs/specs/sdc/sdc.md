# Synopsys Design Constraints (`rusteda_formats::sdc`)

The `sdc` module of `rusteda-formats` (`crates/formats/rusteda-formats/src/sdc.rs`) provides the
shared reader, canonical writer, and `CONSTR v1` binary section codec for timing constraints in
Synopsys Design Constraints syntax [@sdc2016], together with workspace import via
`rusteda import --sdc` (`crates/apps/rusteda-cli/src/import_sdc.rs`).

## 1. Reader, canonical writer, and query AST

**SDC-REQ-0001** `read(sources, env, limits) -> (Sdc, Vec<Diag>)` evaluates an ordered slice of
`Source` buffers in a single deterministic Tcl interpreter initialized with `env` (`Env`) and
bounded by `limits` (`Limits`), returning the evaluated `Sdc` model and all emitted diagnostics in
source evaluation order. `read_str(text)` evaluates a single in-memory buffer with an empty `Env`
and `Limits::default()`.

**SDC-REQ-0002** Every top-level SDC command and object-query command is validated against a static
`CmdSchema` (`SCHEMAS`, `QUERY_SCHEMAS`) that specifies its `CmdStatus`, positional arity range
(`min_pos..=max_pos`), and `OptSpec` option table (`Flag`, `Str`, `Float`, `Int`, `FloatPair`,
`FloatTriple`, `IntTriple`, `Obj`, `Filter`, repeatability, mutual-exclusion groups, and Phase 2b
gating). Option names are matched exactly without abbreviation; tokens matching signed numeric
literals (such as `-0.5`) are treated as positional values unless the command schema declares an
option of that exact name.

**SDC-REQ-0003** `write(&Sdc) -> String` formats an evaluated `Sdc` into canonical, variable-free
SDC syntax with one command per line, options in canonical schema order, normalized floating-point
values (`-0.0` normalized to `0`), and canonical object queries. Canonical writing is idempotent:
for any error-free `Sdc` value `s`, `read_str(&write(&s)).0 == s`.

**SDC-REQ-0004** `Units` records which physical dimensions (`time_to_ns`, `cap_to_pf`,
`res_to_kohm`, `voltage_to_v`, `current_to_ma`, `power_to_nw`) were explicitly configured by
`set_units`. All numeric command arguments are scaled on ingestion into canonical internal units
(`ns`, `pF`, `kOhm`, `V`, `mA`, `nW`).

**SDC-REQ-0005** Design object queries are represented as an unevaluated symbolic `Obj` tree:
`Ports`, `Pins`, `Cells`, `Nets`, `Clocks`, `LibCells`, and `LibPins` (each carrying `ObjQuery {
patterns, hierarchical, regexp, nocase, of_objects, filter, quiet }`, where `-hier` normalizes to
`-hierarchical` and `get_port` normalizes to `Ports`), `AllInputs { no_clocks }`, `AllOutputs`,
`AllClocks`, `AllRegisters`, `CurrentDesign`, `Names(Vec<String>)`, `Union(Vec<Obj>)`, and
`Diff(Box<Obj>, Box<Obj>)`. `glob_match` and `glob_match_nocase` match `*` and `?` wildcards.

**SDC-REQ-0006** `Filter` (`-filter` expressions on `get_*` queries) parses and validates boolean
combinations of `attr op value` atoms (`==`, `!=`, `=~`, `!~`) joined by `&&` and `||` with
parentheses (`&&` binding tighter than `||`).Bare attribute names (`is_hierarchical`) desugar to
`attr == true` and `!attr` desugars to `attr == false`. Malformed filter expressions emit `D0002`.

## 2. Bounded deterministic Tcl subset

**SDC-REQ-0007** The lexer and parser accept Tcl command syntax: newline and `;` command
separators, `#` comments at command start, backslash-newline line continuations (collapsed to a
single space together with leading horizontal whitespace on the next line), `{...}` braced words
(no substitution), `"..."` double-quoted words (variable, command, and backslash substitution),
`[...]` nested command substitutions, and bare words. Unterminated quotes, braces, or brackets emit
`D0001`.

**SDC-REQ-0008** `set var [value]` and `unset [-nocomplain] var...` read, bind, and remove scalar,
list, or `Obj` variables in the interpreter's variable table. `::`-prefixed variable and command
names (`::clk`, `::set`) normalize to the global scope. `$var`, `${var}`, and `$::var` substitute
the bound value; when a command word consists solely of `$var` or `[cmd ...]`, its typed `TclVal`
(`List` or `Obj`) is preserved without string flattening. Referencing an undefined variable emits
`D0007`.

**SDC-REQ-0009** `{*}word` expands a list word into individual command arguments at the call site
and preserves an `Obj` query as an object argument. `$::env(NAME)` and `$env(NAME)` read from the
evaluation environment initialized from `--sdc-env NAME=VALUE` (`Env`); `info exists ::env(NAME)`
tests whether `NAME` is bound; `set ::env(NAME) val` updates the evaluation-local copy and marks
the `EnvEntry` as `written: true` without reading or mutating the host process environment.

**SDC-REQ-0010** `[expr ...]` evaluates arithmetic (`+`, `-`, `*`, `/`, `%`, unary `+`/`-`),
comparisons (`<`, `<=`, `>`, `>=`, `==`, `!=`, `eq`, `ne`), logical operators (`&&`, `||`, `!`)
with short-circuit evaluation, the ternary operator `cond ? a : b`, and mathematical functions
(`abs`, `min`, `max`, `int`, `double`, `round`). Division `/` promotes integer operands to `f64` so
that ratios such as `[expr 5 / 100]` evaluate to `0.05` rather than truncating to `0`. Division or
modulo by zero emits `D0005`.

**SDC-REQ-0011** `if {cond} {body} [elseif {cond} {body}]... [else {body}]` evaluates conditions
lazily with short-circuiting so that guards such as
`if {[info exists ::env(X)] && $::env(X) != ""}` never evaluate `$::env(X)` when `info exists` is
false. `foreach var list {body}` iterates over plain Tcl lists; attempting `foreach` over an `Obj`
collection (`foreach p [all_inputs]`) emits `D0006` and stops script evaluation so unpopulated loop
variables do not trigger cascading errors.

**SDC-REQ-0012** List and string built-ins `list`, `concat`, `llength`, `lindex` (including `end`
and `end-N`), `lappend`, `split`, `join`, and `string` (`match [-nocase]`, `equal [-nocase]`,
`compare`, `length`, `tolower`, `toupper`, `trim`, `range`, ` map`) operate deterministically on
plain strings and lists. `concat` and `lappend` on `Obj` collections construct symbolic
`Obj::Union` trees (`-from [list [get_ports a] [get_ports b]]` is preserved as `Obj::Union`).

**SDC-REQ-0013** Collection built-ins `add_to_collection [-unique] c1 c2` and
`remove_from_collection c1 c2` construct `Obj::Union` and `Obj::Diff` nodes. The idiom
`set idx [lsearch [all_inputs] $clk]; lreplace [all_inputs] $idx $idx ""` is recognized
symbolically via `TclVal::SearchIndex` and lowers to `Obj::Diff(AllInputs, clk)`.

**SDC-REQ-0014** `source <relpath>` resolves `<relpath>` relative to the directory of the currently
executing source file (or `Limits::virtual_sources` in hermetic tests), appends a `SourceRecord`
with `FLAG_SOURCED` (`1`) and its `xxh3_64` content hash to `Sdc::sources`, and evaluates the file
in the same interpreter state. Absolute paths, `..` parent traversal, and unreadable files emit
`D0006`. `puts` (including `-nonewline` and `stdout`/`stderr` channel arguments) emits a `D0040`
note diagnostic with the printed message.

**SDC-REQ-0015** Commands outside the deterministic Tcl subset (`proc`, `while`, `for`, `switch`,
`eval`, `uplevel`, `upvar`, `global`, `namespace`, `regexp`, `regsub`, `file`, `glob`, `open`,
`exec`, `catch`, `array`, `dict`, etc.) emit `D0006`. Every command evaluation increments the step
counter and every nested `[...]` or `source` increments the recursion depth; exceeding
`Limits::max_steps` (default `100,000`) or `Limits::max_depth` (default `32`) immediately aborts
evaluation with `D0008`.

## 3. Command schemas, units, and diagnostic codes

**SDC-REQ-0016** `set_units` accepts `-time`, `-capacitance`, `-resistance`, `-voltage`,
`-current`, and `-power` unit strings (optional power-of-ten prefix `1`, `10`, `100`, `0.1`, etc.
followed by SI prefix `f`/`p`/`n`/`u`/`m`/`k`/`M` and base unit `s`, `F`, `Ohm`, `V`, `A`, `W`).
Sub-canonical units (`ps`, `fs`, `fF`, `Ohm`, `mV`, `uA`, `pW`) divide by exact powers of `1000.0`
(`UnitScale::Div`) so decimal values such as `300 ps` convert to `0.3 ns` without IEEE 754
multiplication drift. Unrecognized unit strings emit `D0030`.

**SDC-REQ-0017** `current_design <name>` sets `Sdc::current_design` and records
`CmdKind::CurrentDesign { name }`. When invoked with zero arguments or inside `[current_design]`,
it evaluates to `Obj::CurrentDesign`.

**SDC-REQ-0018** Every command in `SCHEMAS` has a `CmdStatus`: `Interpreted` (Phase 2a timing
constraints), `RecordedDrc` (recorded in `CONSTR v1` with a `D0021` warning),
`RecordedNoTimingEffect` (recorded in `CONSTR v1` for synthesis/downstream tools with a `D0020`
note), `Phase2b` (rejected in Phase 2a with a `D0010` error), or `Unsupported` (rejected with a
`D0010` error). Any command name absent from both `SCHEMAS` and `QUERY_SCHEMAS` emits `D0010`.

**SDC-REQ-0019** Options marked `phase2b: true` on Phase 2a commands (`-through` on
`set_false_path`, `set_multicycle_path`, `set_max_delay`, `set_min_delay`, `group_path`; and
`-reference_pin` on `set_input_delay` and `set_output_delay`) are recognized by the option parser
and rejected in Phase 2a with diagnostic `D0011`.

**SDC-REQ-0020** Operating environment and wire-load commands `set_operating_conditions`
(`-analysis_type single|bc_wc|on_chip_variation`, `-min`, `-max`, `-library`, `-min_library`,
`-max_library`, `-object_list`), `set_wire_load_model` (`-name`, `-library`, `-min`, `-max`,
`[objects]`), `set_wire_load_mode` (`top|enclosed|segmented`), and `set_wire_load_selection_group`
are validated and recorded as `Interpreted` commands in `Sdc::cmds`.

**SDC-REQ-0021** `create_clock` requires `-period <float>` (`> 0.0`) and at least one of
`-name <name>` or `source_objects` (supporting virtual clocks when `source_objects` is omitted).
Optional `-waveform {rise fall}` must satisfy `0 <= rise < fall <= period`; `-add` and `-comment`
are preserved.

**SDC-REQ-0022** `set_clock_groups` requires one of the mutually exclusive flags `-asynchronous`,
`-logically_exclusive`, or `-physically_exclusive`, and one or more repeatable `-group <obj>`
options, optionally with `-name`, `-allow_paths`, and `-comment`.

**SDC-REQ-0023** `set_clock_uncertainty` supports both single-clock mode (`uncertainty object_list`
in either positional order, with `-setup` and `-hold`) and inter-clock mode (`-from`/`-rise_from`/
`-fall_from` and `-to`/`-rise_to`/`-fall_to` with `uncertainty`, `-setup`, and `-hold`). Supplying
only one of `-from` or `-to` emits `D0004`. The edge flags `-rise` and `-fall` of the single-clock
mode are rejected with `D0011`: the model keeps no edge for them, and they are never dropped.

**SDC-REQ-0024** `set_clock_latency` validates `delay` and `object_list` with `-source`, `-rise`,
`-fall`, `-min`, `-max`, `-early`, `-late`, and `-clock`. Specifying `-early` or `-late` without
`-source` emits `D0004`.

**SDC-REQ-0025** `set_clock_transition` validates `transition` and `clock_list` with `-rise`,
`-fall`, `-min`, and `-max`.

**SDC-REQ-0026** `set_input_delay` and `set_output_delay` validate `delay_value` and
`port_pin_list` with `-clock`, `-clock_fall` (requiring `-clock`), `-level_sensitive`, `-rise`,
`-fall`, `-min`, `-max`, `-add_delay`, `-network_latency_included`, and
`-source_latency_included`.

**SDC-REQ-0027** `set_input_transition` validates `transition` and `port_list` with `-rise`,
`-fall`, `-min`, `-max`, `-clock`, and `-clock_fall` (requiring `-clock`).

**SDC-REQ-0028** `set_driving_cell` requires `-lib_cell <cell_name>` and positional `port_list`,
and accepts `-library`, `-pin`, `-from_pin`, `-rise`, `-fall`, `-min`, `-max`,
`-input_transition_rise`, `-input_transition_fall`, `-multiply_by`, `-dont_scale`,
`-no_design_rule`, `-clock`, and `-clock_fall`.

**SDC-REQ-0029** `set_load` validates `value` (scaled by the active capacitance unit to `pF`) and
`objects` with `-pin_load`, `-wire_load`, `-subtract_pin_load`, `-min`, and `-max`.

**SDC-REQ-0030** Path exception commands `set_false_path` (requiring at least one of `-from` or
`-to`), `set_multicycle_path` (`path_multiplier`, `-setup`, `-hold`, `-start`, `-end`, `-rise`,
`-fall`, `-from`/`-rise_from`/`-fall_from`, `-to`/`-rise_to`/`-fall_to`, `-comment`),
`set_max_delay`, and `set_min_delay` (`delay_value`, `-rise`, `-fall`, `-from`, `-to`,
`-ignore_clock_latency`, `-comment`) are validated and recorded as `Interpreted` commands.

**SDC-REQ-0031** Mode and timing-arc control commands `set_case_analysis` (`0|1|rise|fall` and
`port_or_pin_list`), `set_logic_zero` (`port_list`), `set_logic_one` (`port_list`),
`set_disable_timing` (`-from` and `-to` required together when either is given, plus
`cell_or_pin_list`), `set_max_time_borrow` (`delay_value >= 0` and `object_list`), and
`group_path` (`-name` or `-default`, `-weight`, `-critical_range`, `-from`, `-to`, `-comment`) are
validated and recorded as `Interpreted` commands.

**SDC-REQ-0032** Design-rule commands `set_max_transition`, `set_max_capacitance`,
`set_min_capacitance`, `set_max_fanout`, `set_fanout_load`, and `set_port_fanout_number` have
`CmdStatus::RecordedDrc`: they are validated, scaled to canonical units, stored in `Sdc::cmds` and
`CONSTR v1`, and emit a `D0021` warning diagnostic.

**SDC-REQ-0033** Synthesis and optimization commands `set_dont_use`, `set_dont_touch`,
`set_dont_touch_network`, `set_size_only`, `set_max_area`, `set_max_dynamic_power`, and
`set_max_leakage_power` have `CmdStatus::RecordedNoTimingEffect`: they are validated, stored in
`Sdc::cmds` and `CONSTR v1` for synthesis/downstream tools, and emit a `D0020` note diagnostic.

**SDC-REQ-0034** Phase 2b commands (`create_generated_clock`, `set_propagated_clock`,
`unset_propagated_clock`, `set_timing_derate`, `set_min_pulse_width`, `set_ideal_network`,
`remove_ideal_network`, `set_ideal_latency`, `set_ideal_transition`) have `CmdStatus::Phase2b` and
emit a `D0010` error diagnostic in Phase 2a.

**SDC-REQ-0035** Out-of-scope commands (`set_sense`, `set_clock_sense`, `set_data_check`,
`set_clock_gating_check`, `set_logic_dc`, `set_resistance`, `set_Hierarchy_separator`,
`set_Port_is_pad`, `set_level_shifter_strategy`, `set_level_shifter_threshold`,
`set_max_Dynamic_power`) have `CmdStatus::Unsupported` and emit a `D0010` error diagnostic.

**SDC-REQ-0036** Unknown command options emit `D0003` (`Error`). Wrong positional argument count,
missing required options, duplicate non-repeatable options, or mutually exclusive option pairs emit
`D0004` (`Error`).

**SDC-REQ-0037** Invalid option or positional value types (non-numeric strings where numbers are
required, non-positive clock periods, invalid `waveform` ordering, negative `set_max_time_borrow`,
or invalid enum keywords) emit `D0005` (`Error`).

**SDC-REQ-0038** Every `Diag` emitted by `rusteda_formats::sdc` carries a 1-based
`Location { file, line, column }` pointing to the originating source file and line/column of the
command or token.

**SDC-REQ-0039** The reader and parser never panic on arbitrary or malformed UTF-8 input; all
lexical, syntactic, schema, and evaluation failures are reported as `D0001`..`D0040` diagnostics.

## 4. `CONSTR v1` binary section codec (`constraints.rbin`)

**SDC-REQ-0040** `Sdc::to_sections(&self) -> Vec<ConstrSection>` and
`Sdc::from_sections(sections: &[ConstrSection]) -> Result<Sdc, ConstrError>` implement the
container-agnostic `CONSTR v1` binary format (`KIND = "CONSTR"`, `FORMAT_VERSION = 1`, no upstream
tables).

**SDC-REQ-0041** `CONSTR v1` consists of 13 typed sections in strictly increasing `section_id`
order:
1. `STR_BYTES` (`id = 1`, `elem_size = 1`): concatenated UTF-8 string bytes;
2. `STR_OFFS` (`id = 2`, `elem_size = 4`): `N + 1` monotonic `u32` offsets (`STR_OFFS[0] == 0`,
   string `0` is `""`);
3. `HEADER` (`id = 3`, `elem_size = 32`, `count = 1`): `time_to_ns`, `cap_to_pf`, `res_to_kohm`
   (`f64`, `NaN` when unset), `current_design` string id (`u32::MAX` when `None`), and `flags`;
4. `SOURCES` (`id = 4`, `elem_size = 16`): `(path_str, flags, xxh3_u64)` per evaluated source file;
5. `ENV` (`id = 5`, `elem_size = 8`): `(name_str | (written << 31), value_str)` per environment
   variable;
6. `CMDS` (`id = 6`, `elem_size = 24`): `(op, source, flags, line, col, arg_start, arg_count)` per
   command in evaluation order;
7. `ARGS` (`id = 7`, `elem_size = 16`): `(key, kind, pad, a, b)` per command argument;
8. `F64` (`id = 8`, `elem_size = 8`): `f64` arrays for waveforms and edge shifts;
9. `OBJS` (`id = 9`, `elem_size = 16`): post-order flattened `Obj` nodes;
10. `OBJ_CHILD` (`id = 10`, `elem_size = 4`): child `Obj` indices for `Union` and `Diff`;
11. `PATS` (`id = 11`, `elem_size = 4`): pattern string ids for `ObjQuery` and `Names`;
12. `CANON` (`id = 12`, `elem_size = 1`): UTF-8 bytes of `write(&sdc)`;
13. `DIAGS` (`id = 13`, `elem_size = 24`): `(severity_and_code, file_str, line, col, msg_str,
    help_str)` per diagnostic.

**SDC-REQ-0042** Strings in `STR_BYTES` and `STR_OFFS` are interned deterministically in first-use
order with string `0` reserved for `""` and `u32::MAX` (`NONE_U32`) representing `None`.

**SDC-REQ-0043** `HEADER` encodes `Units` and `current_design` into 32 bytes (`count = 1`), using
`f64::NAN` for unset unit dimensions and bit flags `0..=2` for `voltage_to_v`, `current_to_ma`, and
`power_to_nw`.

**SDC-REQ-0044** `SOURCES` records every top-level input file (`flags = 0`) and every relative
`source`d file (`flags = SourceRecord::FLAG_SOURCED = 1`) together with its 64-bit `xxh3_64` content
hash.

**SDC-REQ-0045** `ENV` records every `--sdc-env` binding and any `::env(...)` variable written
during evaluation (`written` stored in bit 31 of the name word).

**SDC-REQ-0046** `CMDS`, `ARGS`, `F64`, `OBJS`, `OBJ_CHILD`, and `PATS` encode all `Cmd` variants
and nested `Obj` / `Filter` trees losslessly in evaluation order, preserving per-command
`(source, line, col)` locations.

**SDC-REQ-0047** `CANON` stores the exact UTF-8 output of `write(&sdc)` so that downstream checkers
can inspect or re-parse the canonical constraint text directly from `constraints.rbin`.

**SDC-REQ-0048** `DIAGS` stores all non-fatal diagnostics (`D0020`, `D0021`, `D0040`) emitted
during evaluation with their severity, code, `Location`, message, and optional help hint.

**SDC-REQ-0049** `Sdc::from_sections` validates the presence, `elem_size`, byte length, UTF-8
validity, monotonic `STR_OFFS` bounds, `OBJS` DAG topological order, `ARGS` slice bounds, and enum
discriminants of all 13 sections, returning `Err(ConstrError)` on any corrupt or out-of-bounds
section without panicking.

**SDC-REQ-0050** Encoding and decoding `CONSTR v1` sections is a lossless round trip: for every
valid `Sdc` value `s`, `Sdc::from_sections(&s.to_sections())` returns `Ok(s2)` with
`s.exact_eq(&s2)` and `s.to_sections() == s2.to_sections()`.

## 5. Workspace import (`rusteda import --sdc`)

**SDC-REQ-0051** `rusteda import -w <WS> --base-rev <REV> --sdc <FILE>... [--sdc-env NAME=VALUE]...
[-m <TEXT>]` evaluates the given `.sdc` files in order under the `--sdc-env` environment and writes
`constraints.rbin` (`KIND = "CONSTR"`, `FORMAT_VERSION = 1`, empty upstream map) to the new
revision under table key `"constraints"`.

**SDC-REQ-0052** Re-importing the same `.sdc` files and `--sdc-env` bindings produces a
byte-identical `constraints.rbin` and identical `meta.json` table hash.

**SDC-REQ-0053** When `constraints` changes in a new revision, workspace carry-forward
automatically drops any carried downstream table (`sta`, `sta_check`) whose upstream map depends on
`constraints`.

**SDC-REQ-0054** If any `Severity::Error` (`D`-series error diagnostic) is emitted during SDC
import, `rusteda import` aborts without committing a new revision, appends the rejected attempt to
`history.jsonl`, reports all diagnostics in the JSON envelope or standard error with `file:line:col`
locations, and exits with code `1` (`INPUT_REJECTED`). A stale `--base-rev` exits with code `4`
(`REVISION_CONFLICT`).

**SDC-REQ-0055** On successful import, `meta.json` `sources` records every evaluated SDC file
(both top-level `--sdc` files and files loaded via relative `source`) with `table: "constraints"`
and its `xxh3` file hash, and every `--sdc-env NAME=VALUE` binding with `table: "constraints"`,
`path: "env:NAME=VALUE"`, and the `xxh3` hash of `VALUE`.

**SDC-REQ-0070** `set_propagated_clock` and `unset_propagated_clock` are now
interpreted typed commands with one object collection, preserved by `CONSTR v1`.
This supersedes their Phase 2a D0010 classification in SDC-REQ-0034. The consumer
resolves clock collections and reports unsupported object/source/network features
explicitly; it must not silently use an ideal network for selected physical clocks.

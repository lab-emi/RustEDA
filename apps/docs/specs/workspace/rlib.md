# The `LIBERTY` v4 (`.rlib`) compiled library codec (`crates/core/rusteda-workspace/src/rlib.rs`)

## Container and sections

**WS-REQ-0110** A compiled Liberty library (`cache/<name>-<h16>.rlib`) is an `rbin` container of kind `"LIBERTY"` and `format_version = 3` (`3..=3`; version 4 since WS-REQ-0117). When compiled from a cached `.lib` file, its `rbin` header records upstream `"lib"` mapped to the `xxh3-64` hash of the `.lib` bytes. It contains three sections in ascending ID order:
- Section `1` (`STR`, `elem_size = 1`): concatenated UTF-8 bytes of all strings in the library, deduplicated in first-appearance order.
- Section `2` (`MODEL`, `elem_size = 1`): deterministic little-endian byte stream encoding the `rusteda_core::liberty::Library` model.
- Section `3` (`F64`, `elem_size = 8`): pool of little-endian IEEE-754 `f64` bit patterns (`u64` in little-endian byte order) referenced by `(start, len)` slices from `Template` and `Table` records in `MODEL`.

**WS-REQ-0111** Primitives in `MODEL` are encoded in little-endian byte order:
- `u8`: 1 byte.
- `bool`: 1 byte (`0` = `false`, `1` = `true`).
- `u32`: 4 bytes, little-endian.
- `f64`: 8 bytes, little-endian IEEE-754 bits (`f64::to_bits().to_le_bytes()`).
- `str_ref`: `(offset: u32, len: u32)` indexing `&STR[offset..offset + len]`.
- `opt<T>`: `u8` tag (`0` = `None`, `1` = `Some(T)`).
- `f64_slice`: `(start: u32, len: u32)` indexing `len` elements starting at element index `start` in `F64`.
- `vec<T>`: `count: u32` followed by `count` encoded `T` records.

## Normative `MODEL` byte-stream grammar

**WS-REQ-0112** The `MODEL` byte stream encodes every field of `rusteda_core::liberty::Library` (as of `LIB-B`) in fixed deterministic order:
- `Library`: `name: str_ref`, `time_unit_ns: f64`, `cap_unit_pf: f64`, `default_max_capacitance: opt<f64>`, `default_max_transition: opt<f64>`, `default_max_fanout: opt<f64>`, `default_fanout_load: f64`, `default_input_pin_cap: opt<f64>`, `default_output_pin_cap: opt<f64>`, `default_inout_pin_cap: opt<f64>`, `units: Units`, `templates: vec<(str_ref, Template)>` (in strictly ascending `BTreeMap` key order), `cells: vec<Cell>` (in source order), `input_threshold_pct_rise: opt<f64>`, `input_threshold_pct_fall: opt<f64>`, `output_threshold_pct_rise: opt<f64>`, `output_threshold_pct_fall: opt<f64>`, `slew_lower_threshold_pct_rise: opt<f64>`, `slew_upper_threshold_pct_rise: opt<f64>`, `slew_lower_threshold_pct_fall: opt<f64>`, `slew_upper_threshold_pct_fall: opt<f64>`, `slew_derate_from_library: opt<f64>`, `wire_loads: vec<WireLoad>`, `wire_load_tables: vec<WireLoadTable>`, `wire_load_selections: vec<WireLoadSelection>`, `default_wire_load: opt<str_ref>`, `default_wire_load_mode: opt<WireLoadMode>`, `default_wire_load_selection: opt<str_ref>`, `default_wire_load_area: opt<f64>`, `default_wire_load_capacitance: opt<f64>`, `default_wire_load_resistance: opt<f64>`, `operating_conditions: vec<OperatingConditions>`, `default_operating_conditions: opt<str_ref>`, `nom_process: opt<f64>`, `nom_temperature: opt<f64>`, `nom_voltage: opt<f64>`.
- `Units`: six `Unit` records in fixed order: `time`, `capacitance`, `resistance`, `voltage`, `current`, `leakage_power`.
- `Unit`: `scale: f64`, `given: bool`, `text: opt<str_ref>`.
- `Template`: `variable_1: opt<str_ref>`, `variable_2: opt<str_ref>`, `variable_3: opt<str_ref>`, `index_1: f64_slice`, `index_2: f64_slice`, `index_3: f64_slice`.
- `Table`: `template: str_ref`, `variable_1: opt<str_ref>`, `variable_2: opt<str_ref>`, `variable_3: opt<str_ref>`, `index_1: f64_slice`, `index_2: f64_slice`, `index_3: f64_slice`, `values: f64_slice`.
- `Func`: `u8` discriminant (`0` = `Const(false)`, `1` = `Const(true)`, `2` = `Var(str_ref)`, `3` = `Not(Func)`, `4` = `And(Func, Func)`, `5` = `Or(Func, Func)`, `6` = `Xor(Func, Func)`).
- `Direction`: `u8` (`0` = `Input`, `1` = `Output`, `2` = `Inout`, `3` = `Internal`).
- `SeqKind`: `u8` (`0` = `FlipFlop`, `1` = `Latch`).
- `PgType`, `LevelShifterType`, `SwitchCellType`, `AntennaDiodeType`, `WireLoadMode`, `TreeType`: `u8` tag (`0` = `from_name(str_ref)`, `1` = explicit `Other(str_ref)`).
- `WireLoad`: `name: str_ref`, `resistance: f64`, `capacitance: f64`, `area: f64`, `slope: f64`, `fanout_length: vec<(f64, f64)>`.
- `WireLoadTable`: `name: str_ref`, `fanout_length: vec<(f64, f64)>`, `fanout_capacitance: vec<(f64, f64)>`, `fanout_resistance: vec<(f64, f64)>`, `fanout_area: vec<(f64, f64)>`.
- `WireLoadAreaRule`: `min_area: f64`, `max_area: f64`, `wire_load: str_ref`.
- `WireLoadSelection`: `name: str_ref`, `rules: vec<WireLoadAreaRule>`.
- `OperatingConditions`: `name: str_ref`, `process: opt<f64>`, `temperature: opt<f64>`, `voltage: opt<f64>`, `tree_type: opt<TreeType>`.
- `TimingArc`: `related_pin: str_ref`, `timing_sense: opt<str_ref>`, `timing_type: str_ref`, `cell_rise: opt<Table>`, `cell_fall: opt<Table>`, `rise_transition: opt<Table>`, `fall_transition: opt<Table>`, `rise_constraint: opt<Table>`, `fall_constraint: opt<Table>`, `when_text: opt<str_ref>`, `when: opt<Func>`, `sdf_cond: opt<str_ref>`, `line: u32`.
- `InternalPower`: `related_pin: opt<str_ref>`, `rise_power: opt<Table>`, `fall_power: opt<Table>`.
- `Pin`: `name: str_ref`, `direction: Direction`, `function_text: opt<str_ref>`, `function: opt<Func>`, `three_state: opt<str_ref>`, `three_state_function: opt<Func>`, `capacitance: f64`, `capacitance_given: bool`, `rise_capacitance: opt<f64>`, `fall_capacitance: opt<f64>`, `rise_capacitance_range: opt<(f64, f64)>`, `fall_capacitance_range: opt<(f64, f64)>`, `min_capacitance: opt<f64>`, `max_capacitance: opt<f64>`, `max_transition: opt<f64>`, `max_fanout: opt<f64>`, `fanout_load: opt<f64>`, `is_clock: bool`, `arcs: vec<TimingArc>`, `internal_power: vec<InternalPower>`, `isolation_cell_enable_pin: bool`, `isolation_cell_data_pin: bool`, `level_shifter_enable_pin: bool`, `level_shifter_data_pin: bool`, `always_on: bool`, `related_power_pin: opt<str_ref>`, `related_ground_pin: opt<str_ref>`, `power_down_function: opt<str_ref>`, `state_function_text: opt<str_ref>`, `state_function: opt<Func>`, `internal_node: opt<str_ref>`, `clock_gate_clock_pin: bool`, `clock_gate_enable_pin: bool`, `clock_gate_out_pin: bool`, `clock_gate_test_pin: bool`, `clock_gate_obs_pin: bool`, `line: u32`.
- `Sequential`: `kind: SeqKind`, `state: str_ref`, `state_n: str_ref`, `data_text: str_ref`, `data: Func`, `clock_text: str_ref`, `clock: Func`, `clocked_on_also: opt<str_ref>`, `clear_text: opt<str_ref>`, `clear: opt<Func>`, `preset_text: opt<str_ref>`, `preset: opt<Func>`, `clear_preset_var1: opt<str_ref>`, `clear_preset_var2: opt<str_ref>`.
- `Statetable`: `input_nodes: vec<str_ref>`, `internal_nodes: vec<str_ref>`, `table: str_ref`, `rows: vec<str_ref>`.
- `PgPin`: `name: str_ref`, `pg_type: opt<PgType>`, `voltage_name: opt<str_ref>`.
- `Cell`: `name: str_ref`, `area: f64`, `dont_use: bool`, `pins: vec<Pin>`, `pg_pins: vec<str_ref>`, `sequentials: vec<Sequential>`, `has_statetable: bool`, `has_test_cell: bool`, `is_clock_gating: bool`, `has_bus: bool`, `leakage_power: opt<f64>`, `problems: vec<str_ref>`, `pg_pin_groups: vec<PgPin>`, `is_isolation_cell: bool`, `is_level_shifter: bool`, `level_shifter_type: opt<LevelShifterType>`, `always_on: bool`, `cell_footprint: opt<str_ref>`, `dont_touch: bool`, `is_macro_cell: bool`, `pad_cell: bool`, `switch_cell_type: opt<SwitchCellType>`, `retention_cell: opt<str_ref>`, `antenna_diode_type: opt<AntennaDiodeType>`, `clock_gating_integrated_cell: opt<str_ref>`, `statetable: opt<Statetable>`, `line: u32`.

## Round-trip, validation, and cache loading

**WS-REQ-0117** Format version 4. Writers write `format_version = 4` and readers accept `4..=4`;
version 4 is the version 3 container and grammar of WS-REQ-0110 to WS-REQ-0112 with three fields
appended to every `Pin` record after `line`: `min_pulse_width_high: opt<f64>`,
`min_pulse_width_low: opt<f64>` and `min_period: opt<f64>` (LIBERTY-REQ-0049). The validation of
WS-REQ-0114 applies with `4..=4`. A cached `.rlib` of version 3, which cannot hold the pin pulse
attributes, is an older version and is compiled again from its cached `.lib` (WS-REQ-0116), so no
reader sees a library without them; a version above 4 fails with `SCHEMA_TOO_NEW`. A tool that
reads only version 3 rejects a version 4 cache with `SCHEMA_TOO_NEW` instead of reading it
without the attributes.

**WS-REQ-0113** Encoding and decoding are exact and reproducible: for every `Library` value `lib`, `decode(&encode(&lib)?)? == lib`, and re-encoding a decoded library produces identical `.rlib` bytes (`encode(&decode(&bytes)?)? == bytes`).

**WS-REQ-0114** Decoding validates the `rbin` header (`kind == "LIBERTY"`, `format_version` in `3..=3`, `4..=4` since WS-REQ-0117), the presence and element sizes of sections `STR`, `MODEL`, and `F64`, UTF-8 validity and character boundaries of `STR`, slice bounds into `STR` and `F64`, strictly ascending template map keys, valid boolean and enumeration tags, bounded `Func` recursion depth, and that `MODEL` has no trailing bytes; any malformed or truncated input fails with `CORRUPT_TABLE` without panicking, and a `format_version > 3` (`> 4` since WS-REQ-0117) fails with `SCHEMA_TOO_NEW`.

**WS-REQ-0115** `rusteda import --lib` compiles each validated library with `rlib::put` into `cache/<name>-<h16>.rlib` atomically and records its cache entry in `meta.json` `libraries`.

**WS-REQ-0116** `rlib::libraries_of_corner(rev, corner)` returns the library entries of `rev` belonging to `corner` in order; `rlib::load` and `rlib::load_or_recompile` map and decode a revision's library from `cache/<name>-<h16>.rlib` (transparently recompiling from `cache/<name>-<h16>.lib` and reporting `recompiled = true` when the cached `.rlib` has an older `format_version` or is missing/stale, and failing with `SCHEMA_TOO_NEW` when the `.rlib` has a newer `format_version`).

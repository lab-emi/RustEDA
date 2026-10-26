# rusteda-core: engine runs, messages and the Liberty library model

`rusteda-core` (`crates/core/rusteda-core`) is the small core of the RustEDA tools, which uses no
other workspace crate: the contract between an engine run and whoever observes it, such as a
command line through its job protocol (module `job`), coded messages (module `diag`), and the
Liberty library model (module `liberty`). Engines accept a `Ctx` and never depend on a command line
or a job protocol.
The reader that builds the library model from Liberty text is specified in
[the Liberty reader](../liberty/liberty.md).

| Section | Requirements |
|---|---|
| [Engine runs](#engine-runs) | JOB-REQ-0001 to 0006 |
| [Messages](#messages) | JOB-REQ-0007 |
| [Liberty library model](#liberty-library-model) | LIBERTY-REQ-0010 to 0019, 0022 to 0026, 0030 to 0034 and 0040 to 0049 |
| [Symbols, arenas, names and netlist](netlist.md) | CORE-REQ-0100 to 0103, 0110 to 0113, 0120 to 0128, 0130 to 0138 |

## Engine runs

**JOB-REQ-0001** A `CancelToken` is a shared flag. `cancel()` sets it; `is_cancelled()` reads it.
Clones share the flag.

**JOB-REQ-0002** A `Ctx` carries an optional cancel token and an optional observer. `Ctx::NONE`
has neither. `checkpoint()` returns `Err(Cancelled)` when the token is set and `Ok(())` otherwise.
`emit(f)` calls `f` and passes the event to the observer only when an observer is present, so an
unobserved run builds no event. `unobserved()` is the same context without its observer: a part of
a run that an engine computes only to compare it with another (and may discard) can still be
cancelled without its phases appearing as phases of the run.

**JOB-REQ-0003** Events: `PhaseStarted`, `PhaseFinished` (with the seconds recorded in the run's
report), `Step` (ordinal and count within a phase, count 0 when unknown), `Progress` (done and
total within the current step), `Metric` (a named value) and `Diagnostic` (a message).

**JOB-REQ-0004** `progress(phase, done, total)` emits an event only when `done == total` or `done`
is a multiple of `max(1, ceil(total / 256))`: at most 257 events per loop, a number that depends on
`total` only, never on time or thread count.

**JOB-REQ-0005** Engines call `checkpoint`, `emit` and `progress` only on the thread that runs the
phases, between parallel sections, never inside a parallel iteration, so that observing or
cancelling cannot change the order of any computation. A run whose token is never set produces
results identical, bit for bit, to an unobserved run, and its sequence of events depends only on
its inputs and options.

**JOB-REQ-0006** A cancelled run returns `JobError::Cancelled` and no partial result.

## Messages

**JOB-REQ-0007** A `Diag` is a message with severity (`error`, `warning`, `note`), a code of one
letter and four digits, an optional location (file, line, column), the text and an optional help
line. `render()` gives `<severity>[<code>]: <file>:<line>:<column>: <text>`, without the location
part when there is none, followed by a line `  help: <help>` when there is help. `parse` reads the
first line back: `Diag::parse(&d.render_first_line()) == Some(d)` for every diag without help whose
text does not itself begin with a location (`<file>:<line>:<column>: `).

## Liberty library model

The model holds what the tools use of a cell library in the Liberty format [@liberty2017]. The
reader fills it from Liberty text (LIBERTY-REQ-0001 to 0007, 0020, 0021 and 0050 to 0059). A
value the model keeps as text (functions, timing types and senses, table variables) keeps the text
as written next to its parsed or typed form. The types that are new in the model
(`Unit`, `Units`, `PgPin` and the enumerations) are marked non-exhaustive, so that adding a field
or a variant later does not break code outside this crate; `PgPin::new` and `Default` build them.

### Library, cells and pins

**LIBERTY-REQ-0010** Library: name, units (LIBERTY-REQ-0017; `time_unit_ns` and `cap_unit_pf`
repeat the time and capacitance units), lookup-table templates (LIBERTY-REQ-0031), the library
defaults of LIBERTY-REQ-0015 and LIBERTY-REQ-0018, the slew thresholds and derate of
LIBERTY-REQ-0045, the wire-load and operating-condition models of LIBERTY-REQ-0047 and
LIBERTY-REQ-0048, and cells in source order (LIBERTY-REQ-0019).

**LIBERTY-REQ-0011** Cell: name, `area` (default 0), `dont_use` (default false), pins, and the
flags `has_statetable`, `has_test_cell`, `is_clock_gating` (`clock_gating_integrated_cell`
present), `has_bus` (a `bus` or `bundle` group present). A `pin` group with several names declares
one pin per name with identical attributes. The names of the `pg_pin` groups are listed in
`pg_pins`, and the groups with their attributes in `pg_pin_groups` (LIBERTY-REQ-0042).

**LIBERTY-REQ-0012** Pin: name, `direction` (`input`, `output`, `inout`, `internal`), `function`,
`three_state` (LIBERTY-REQ-0025), `capacitance` (default 0, with `capacitance_given`),
`rise_capacitance` and `fall_capacitance` (optional), `rise_capacitance_range` and
`fall_capacitance_range` (optional pairs minimum, maximum), `min_capacitance` (optional), `clock`
flag, and timing arcs. Every pin direction may carry a capacitance: the capacitance of an output
pin is part of the load of the net it drives.

**LIBERTY-REQ-0013** Timing arc: `related_pin` (a space-separated list; one arc per related pin),
`timing_sense` (LIBERTY-REQ-0024), `timing_type` (default `combinational`, LIBERTY-REQ-0023),
`when_text`, `when`, `sdf_cond` (LIBERTY-REQ-0026), and the tables `cell_rise`, `cell_fall`,
`rise_transition`, `fall_transition`, `rise_constraint`, `fall_constraint` (LIBERTY-REQ-0032). A
scalar table (`scalar` template or a single value) has no indices of its own. `combinational_rise`
and `combinational_fall` arcs carry only the tables of their transition direction without emitting
any warning.

**LIBERTY-REQ-0014** Sequential groups: `ff (<state>, <state_n>)` with `next_state`, `clocked_on`,
optional `clocked_on_also`, `clear`, `preset`, `clear_preset_var1`, `clear_preset_var2`;
`latch (<state>, <state_n>)` with `data_in`, `enable`, optional `clear`, `preset`,
`clear_preset_var1`, `clear_preset_var2`. The reader stores all expressions as parsed functions.

**LIBERTY-REQ-0015** The reader preserves scalar pin `max_capacitance`, `max_transition`,
`max_fanout`, and `fanout_load`, plus the corresponding library defaults. Missing pin limits
remain optional so consumers can apply library defaults (LIBERTY-REQ-0018). Capacitance and
transition limits use library units; fanout is the sum of dimensionless sink loads, defaulting to
one when no load is specified. Pin attributes override library defaults [@liberty2017].

**LIBERTY-REQ-0016** Internal power: every `internal_power` group of a pin is kept as an entry with
its `related_pin` (a space-separated list gives one entry per related pin; none for a group
without one) and its `rise_power` and `fall_power` tables, or its `power` table as both. The
values are energies per transition in the library's units; the reader does not convert them.
`cell_leakage_power` is kept per cell [@liberty2017].

**LIBERTY-REQ-0017** Units: `units` holds six units, each with its size `scale` in a fixed unit
of its quantity, its attribute text as written, and `given`: time (`time_unit`, in ns),
capacitance (`capacitive_load_unit`, in pF), resistance (`pulling_resistance_unit`, in kOhm),
voltage (`voltage_unit`, in V), current (`current_unit`, in mA) and leakage power
(`leakage_power_unit`, in nW). `given` is true when the library states the unit with a valid text
(LIBERTY-REQ-0006). Otherwise the unit is defaulted: its scale is 1 (1 ns, 1 pF, 1 kOhm, 1 V,
1 mA, 1 nW) and `given` is false; an invalid text is still kept. Liberty has no unit attribute of
its own for internal power: those values are energies (LIBERTY-REQ-0016).

**LIBERTY-REQ-0018** Library defaults by direction: `default_input_pin_cap`,
`default_output_pin_cap` and `default_inout_pin_cap` are kept when given. `Library::pin_capacitance`
gives a pin's `capacitance` when the pin states one, else the default of its direction, else 0
(internal pins have no default). `pin_max_transition` gives the pin's `max_transition`, else
`default_max_transition` for every pin that is not internal; `pin_max_capacitance` and
`pin_max_fanout` give the pin's value, else the library default for output and inout pins;
`pin_fanout_load` gives the pin's `fanout_load`, else `default_fanout_load` for input and inout
pins, else 0 [@liberty2017].

**LIBERTY-REQ-0019** Source order: cells are listed in the order of their `cell` groups; the pins
of a cell in the order of their `pin`, `bus` and `bundle` groups and, within a group, of its
names or members; the arcs of a pin in the order of their `timing` groups and, within a group, of
the names of its `related_pin`. The positions in these lists are the indices of cells, pins and
arcs: reading the same text gives the same indices.

### Functions, arcs and tables

**LIBERTY-REQ-0022** A function provides its truth table over an ordered list of at most 6
variables as a 64-bit mask (bit i is the value for the assignment whose variable j equals
bit j of i).

**LIBERTY-REQ-0023** Timing types: `TimingArc::kind` gives the `TimingType` of the arc's
`timing_type` text: one variant per Liberty name `combinational`, `combinational_rise`,
`combinational_fall`, `three_state_enable`, `three_state_enable_rise`, `three_state_enable_fall`,
`three_state_disable`, `three_state_disable_rise`, `three_state_disable_fall`, `rising_edge`,
`falling_edge`, `preset`, `clear`, `setup_rising`, `setup_falling`, `hold_rising`, `hold_falling`,
`recovery_rising`, `recovery_falling`, `removal_rising`, `removal_falling`, `skew_rising`,
`skew_falling`, `non_seq_setup_rising`, `non_seq_setup_falling`, `non_seq_hold_rising`,
`non_seq_hold_falling`, `nochange_high_high`, `nochange_high_low`, `nochange_low_high`,
`nochange_low_low`, `min_pulse_width`, `minimum_period`, `max_clock_tree_path` and
`min_clock_tree_path`, and `Other` with the text for any other name [@liberty2017].
`from_name` and `name` convert between a variant and its name.

**LIBERTY-REQ-0024** Timing senses: `TimingArc::sense` gives the `TimingSense` of the arc's
`timing_sense` text, when it has one: `positive_unate`, `negative_unate`, `non_unate`, or `Other`
with the text.

**LIBERTY-REQ-0025** Three-state function: `three_state` keeps the text of the pin's
`three_state` attribute and `three_state_function` its parsed function (LIBERTY-REQ-0020), which
is true when the pin is in high impedance. Like `function`, it may refer only to pins of its cell
(LIBERTY-REQ-0021).

**LIBERTY-REQ-0026** Conditional timing arcs: a `timing` group's `when` attribute is stored both as
raw text in `TimingArc::when_text` and as a parsed `Func` in `TimingArc::when` (LIBERTY-REQ-0020),
validated over the cell's pins and state variables (LIBERTY-REQ-0021). Its `sdf_cond` attribute is
stored as raw text in `TimingArc::sdf_cond` [@liberty2017].

**LIBERTY-REQ-0030** Lookup: a two-dimensional table is evaluated by bilinear interpolation between
the enclosing index points, and by linear extrapolation from the two nearest points outside the
index range; one-dimensional tables likewise.

**LIBERTY-REQ-0031** Templates: the library's `lu_table_template`, `power_lut_template` and
`output_current_template` groups form one map from template name to template, ordered by name.
A template has up to three axes, each with its variable text (`variable_1` to `variable_3`) and
its index points (`index_1` to `index_3`). `Template::variable` gives the typed variable of an
axis: one `TableVariable` per Liberty name `input_net_transition`,
`total_output_net_capacitance`, `related_pin_transition`, `constrained_pin_transition`,
`output_net_length`, `output_net_wire_cap`, `output_net_pin_cap`,
`related_out_total_output_net_capacitance`, `related_out_output_net_length`,
`related_out_output_net_wire_cap`, `related_out_output_net_pin_cap`,
`equal_or_opposite_output_net_capacitance`, `input_transition_time`, `normalized_voltage`, `time`,
`input_voltage`, `output_voltage`, `input_noise_height` and `input_noise_width`, and `Other` with
the text for any other name [@liberty2017].

**LIBERTY-REQ-0032** Tables: a table has its template name, up to three axes and its values. The
variables of the axes are those of the template (`Table::variable` gives them typed, as in
LIBERTY-REQ-0031). The index points of an axis are the table's own `index_<k>` when it has one,
otherwise the template's: a table overrides each index separately. The values are row-major over
(index_1, index_2, index_3): with n2 and n3 points on the second and third axes (each counted as
at least 1), the value at points (i, j, k) is value number (i n2 + j) n3 + k. `shape3` gives the
three point counts, and `is_consistent3` holds when the value count is their product, or 1 for a
constant table. `shape`, `is_consistent`, `lookup`, `lookup_slew_load` and `mid_slew` are the
two-axis forms of LIBERTY-REQ-0030 and LIBERTY-REQ-0034: a table whose third axis has more than one
point is not consistent for them.

**LIBERTY-REQ-0033** Three-axis lookup: `lookup3(x1, x2, x3)` interpolates linearly between the
enclosing index points along every axis with at least two points, in the order third, second,
first axis, and extrapolates linearly from the two nearest points outside the index range; an axis
with fewer than two points is constant. A table that is not consistent (LIBERTY-REQ-0032) gives 0.
On a table of finite values without a third axis, `lookup3` gives the same number, bit for bit, as
`lookup`.

**LIBERTY-REQ-0034** Middle of the transition axis: `Table::mid_slew` gives the index point in the
middle of the input-transition axis of a two-axis table: the axis is `index_2` when the text of
`variable_1` names a capacitance and that of `variable_2` does not, otherwise `index_1`; the point
is the upper of the two middle points for an even count, and 0 when the axis has no points.

### Special cells and power intent

**LIBERTY-REQ-0040** Special cells: the cell attributes `is_isolation_cell`, `is_level_shifter`,
`always_on`, `dont_touch`, `is_macro_cell` and `pad_cell` (Boolean, default false),
`level_shifter_type` (`LH`, `HL`, `HL_LH` as `LevelShifterType`), `switch_cell_type`
(`coarse_grain`, `fine_grain`), `antenna_diode_type` (`power`, `ground`, `power_and_ground`),
`cell_footprint` and `retention_cell` (text). Each enumeration keeps any other value as `Other`
with its text [@liberty2017].

**LIBERTY-REQ-0041** Power intent of pins: the pin attributes `isolation_cell_enable_pin`,
`isolation_cell_data_pin`, `level_shifter_enable_pin`, `level_shifter_data_pin` and `always_on`
(Boolean, default false), and `related_power_pin`, `related_ground_pin` and
`power_down_function` (text, as written).

**LIBERTY-REQ-0042** Power and ground pins: every `pg_pin` group of a cell is a `PgPin`, in source
order, with its name, its `pg_type` as `PgType` (`primary_power`, `primary_ground`,
`backup_power`, `backup_ground`, `internal_power`, `internal_ground`, `nwell`, `pwell`,
`deepnwell`, `deeppwell`, or `Other` with the text) and its `voltage_name`, each optional.

**LIBERTY-REQ-0043** Clock-gating cell and pin attributes: `Cell::clock_gating_integrated_cell`
stores the cell's `clock_gating_integrated_cell` attribute text when present (and `is_clock_gating`
is true). `Pin` stores the Boolean clock-gating pin roles `clock_gate_clock_pin`,
`clock_gate_enable_pin`, `clock_gate_out_pin`, `clock_gate_test_pin` and `clock_gate_obs_pin`
(default false) [@liberty2017].

**LIBERTY-REQ-0044** Statetable, internal node and state function: `Cell::statetable` stores the
parsed `statetable (<input_nodes>, <internal_nodes>)` group (`Statetable` with `input_nodes`,
`internal_nodes`, raw `table` text and normalized comma-separated `rows`). `Pin::internal_node`
stores an internal pin's `internal_node` name, and `Pin::state_function_text` and
`Pin::state_function` store a pin's `state_function` attribute raw and parsed as a `Func` over the
cell's pins and internal state nodes [@liberty2017].

**LIBERTY-REQ-0045** Delay and slew thresholds and slew derate: `Library` stores
`input_threshold_pct_rise`, `input_threshold_pct_fall`, `output_threshold_pct_rise`,
`output_threshold_pct_fall`, `slew_lower_threshold_pct_rise`, `slew_upper_threshold_pct_rise`,
`slew_lower_threshold_pct_fall`, `slew_upper_threshold_pct_fall` and `slew_derate_from_library` as
`Option<f64>` (`None` when omitted/defaulted, `Some(v)` when given and valid) [@liberty2017].

**LIBERTY-REQ-0046** Source line numbers: `Cell::line`, `Pin::line` and `TimingArc::line` store the
1-based line number of their `cell`, `pin` (or member override) and `timing` group in the `.lib`
file, or `0` when constructed synthetically.

**LIBERTY-REQ-0047** Wire-load models, tables, selections and defaults: `Library` stores `wire_loads`
(`WireLoad` with `name`, `resistance`, `capacitance`, `area`, `slope` and sorted `fanout_length`
pairs, plus piecewise-linear interpolation and slope extrapolation `length_for_fanout`, `length`,
`capacitance_for_fanout` and `resistance_for_fanout`), `wire_load_tables` (`WireLoadTable` with
`name`, `fanout_length`, `fanout_capacitance`, `fanout_resistance` and `fanout_area`),
`wire_load_selections` (`WireLoadSelection` with `name`, `rules` of `WireLoadAreaRule { min_area,
max_area, wire_load }` and `select(area)` matching `[min_area, max_area)`), and the library
defaults `default_wire_load`, `default_wire_load_mode` (`WireLoadMode`: `top`, `enclosed`,
`segmented` or `Other`), `default_wire_load_selection`, `default_wire_load_area`,
`default_wire_load_capacitance` and `default_wire_load_resistance` [@liberty2017].

**LIBERTY-REQ-0048** Operating conditions and nominal process, temperature and voltage: `Library`
stores `operating_conditions` (`OperatingConditions` with `name`, `process`, `temperature`,
`voltage` and `tree_type` as `TreeType`: `best_case_tree`, `balanced_tree`, `worst_case_tree` or
`Other`), `default_operating_conditions`, and the library nominal attributes `nom_process`,
`nom_temperature` and `nom_voltage` [@liberty2017].

**LIBERTY-REQ-0049** Pin pulse attributes: the pin attributes `min_pulse_width_high`,
`min_pulse_width_low` and `min_period` are consumed (in addition to the constructs of
LIBERTY-REQ-0054) and kept on their `Pin` as `min_pulse_width_high`, `min_pulse_width_low` and
`min_period`, in library time units: the unconditional minimum widths of the high and the low pulse
and the minimum period at the pin. They add no timing arc, so the arcs of a pin are exactly its
`timing` groups (LIBERTY-REQ-0019). A negative or non-finite value is a pin limit problem (`L0204`,
LIBERTY-REQ-0057) and is not kept. The compiled library carries them from format version 4
(WS-REQ-0117) [@liberty2017].

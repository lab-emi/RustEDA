# Symbols, arenas, names and the gate-level netlist

This document specifies the netlist data structures of `rusteda-core` (`crates/core/rusteda-core`,
modules `symbol`, `arena`, `names`, and `netlist`). See [README.md](README.md) for the crate
overview.

## Symbol interning (`symbol`)

**CORE-REQ-0100** Every database owns its `SymbolInterner`; there is no global symbol table.
`intern(s)` assigns dense `SymbolId` values (`0, 1, 2, ...`) in insertion order and returns the
existing id when `s` is already interned. `get(s)` looks up an interned string without adding it.
`resolve(id)` and `try_resolve(id)` return the string of a symbol made by the interner (`try_resolve`
returns `None` for an out-of-range id).

## Generational arenas (`arena`)

**CORE-REQ-0101** An `Arena<T>` addresses entries by typed `Id<T>` values holding a 32-bit slot
index and a 32-bit generation. Lookups (`get`, `get_mut`, `remove`) check both the slot index and
its generation; removing an entry increments the slot's generation so that any previously issued id
for that slot is stale and reported (`None` or `StaleId`) rather than reaching a later entry.

**CORE-REQ-0102** Freed slots are reused last-freed-first (LIFO) with their incremented generation,
so the sequence of assigned ids is a deterministic function of the insert/remove sequence.

**CORE-REQ-0103** A slot whose generation reaches `u32::MAX` is retired when its entry is removed
and is never handed out again, so generations never wrap.

## Object names (`names`)

**CORE-REQ-0110** Every netlist name (module, cell, cell pin, port, net, or instance) is non-empty
and consists of printable ASCII characters (byte values 33 `!` to 126 `~`). `check_verbatim`
enforces this rule for module, cell, and cell pin names.

**CORE-REQ-0111** Port, net, and instance names are hierarchical (`check_hierarchical`): in
addition to CORE-REQ-0110, a hierarchical name is one or more non-empty segments separated by
unescaped `/`, where inside a segment `\` escapes the next character, which must be `/` or `\`.

**CORE-REQ-0112** `escape_segment` escapes `\` as `\\` and `/` as `\/` in a raw segment text;
`join` escapes each segment and joins them with `/`; `unescape` converts `\/` to `/` and `\\` to
`\` across the whole name; `segments` splits a hierarchical name at unescaped `/` and unescapes
each segment.

**CORE-REQ-0113** `is_simple_identifier(s)` is true when `s` matches `[A-Za-z_][A-Za-z0-9_$]*`.

## Editable gate-level netlist (`netlist::GateNetlist`)

**CORE-REQ-0120** `GateNetlist` represents one flat module: cell types with their pins in library
order (`PinDir`: `Input`, `Output`, `Inout`, `Other`), module ports (`PortDir`: `Input`, `Output`,
`Inout`), scalar and indexed nets (plus anonymous nets), cell instances with their pins, and
continuous assignments (`lhs = rhs`, where `rhs` is a net or constant `0`/`1`).

**CORE-REQ-0121** Every operation preserves the netlist invariants: all names satisfy their kind's
rules; cell, port, and instance names are unique; named nets have unique `(name, index)` pairs and
do not mix scalar and indexed nets under the same name; nets named after a port are exactly its
bits; each instance has one pin per cell pin in cell pin order and lists its connected pins once in
connection order; a net's pin list equals the set of instance pins connected to that net.

**CORE-REQ-0122** Every fallible operation validates all ids, names, and preconditions before
mutating state: on error it returns `NetlistError` and leaves the netlist and its `SymbolInterner`
unchanged.

**CORE-REQ-0123** Every operation that accepts a name checks it against CORE-REQ-0110 (for module,
cell, and cell pin names) or CORE-REQ-0111 (for port, net, and instance names) and reports
`NetlistError::InvalidName` on violation.

**CORE-REQ-0124** `add_instance` creates an instance with unconnected pins in cell pin order;
`connect`, `disconnect`, and `move_pin` manage pin connections while preserving connection order on
the instance when a connected pin is moved to another net or constant; `add_assign` and
`remove_assign` manage continuous assignments in order.

**CORE-REQ-0125** `remove_instance` disconnects and removes an instance and its pins, leaving their
ids stale; `remove_net` removes a net only when no instance pin, port bit, or assignment references
it, freeing its name and leaving its `NetId` stale.

**CORE-REQ-0126** `swap_cell` replaces an instance's cell type with another cell type whose pins
have the same names and directions in any order, remapping every pin of the instance to the
matching pin of the new cell type.

**CORE-REQ-0127** `insert_buffer` inserts a single-input, single-output buffer instance on a net
and moves the selected sink pins and assignments to the new net; `remove_buffer` is its inverse,
reconnecting all sinks of the buffer's output net back to its input net and removing the buffer
instance and output net.

**CORE-REQ-0128** A bus port range `(msb, lsb)` may run in either direction (`msb >= lsb` or
`msb < lsb`), creates bit nets in declaration order from `msb` to `lsb` without signed-integer
overflow, and has at most `MAX_RANGE_WIDTH` (`2^24`) bits.

## Frozen netlist (`netlist::FrozenNetlist`)

**CORE-REQ-0130** `FrozenNetlist` holds the 18 dense tables of the `NETLIST v1` representation in
host byte order (`str_bytes`, `str_offs`, `module`, `cells`, `cell_pin_start`, `cell_pin_name`,
`cell_pin_dir`, `ports`, `port_bit_start`, `port_bit_net`, `nets`, `instances`, `inst_pin_start`,
`pin_cellpin`, `pin_net`, `assigns`, `net_pin_start`, `net_pins`) as `Cow<'a, [T]>` slices.

**CORE-REQ-0131** Record layouts (`PortRec`, `NetRec`, `InstRec`, `AssignRec`) are `bytemuck::Pod`
and `Zeroable`. A scalar port has `is_bus == 0` and `msb == lsb == 0`; a bus port has `is_bus == 1`
and its declared `msb` and `lsb`.

**CORE-REQ-0132** Special sentinel values in the frozen tables: `CONST0 = 0xFFFF_FFFE` and
`CONST1 = 0xFFFF_FFFF` in `pin_net` and `AssignRec::rhs`; `ANONYMOUS = u32::MAX` for an anonymous
net's `NetRec::name`; `SCALAR = i32::MIN` for a scalar net's `NetRec::index`.

**CORE-REQ-0133** `GateNetlist::freeze` numbers live cells, ports, nets, and instances densely in
arena slot order, stores each instance's connected pins in connection order (with distinct
`pin_cellpin` values per instance), and lists `net_pins` in strictly increasing global connected-pin
order for each net.

**CORE-REQ-0134** `GateNetlist::freeze` compacts the string table into canonical first-use order:
module name, each cell name followed by its pin names, port names, net names, and instance names,
with no duplicate or unreferenced strings.

**CORE-REQ-0135** `FrozenNetlist::validate` checks all 18 sections, header counts, CSR offset
arrays, string UTF-8 and name rules (CORE-REQ-0110 and CORE-REQ-0111 according to every role a
string plays), uniqueness, port bit naming, pin directions, connection uniqueness, `net_pins` CSR
consistency, and canonical string ordering without panicking on any input.

**CORE-REQ-0136** `FrozenNetlist::thaw` validates the tables and reconstructs a `GateNetlist` in
row order in time proportional to the table sizes, such that `freeze(thaw(f)) == f` for every valid
`FrozenNetlist`.

**CORE-REQ-0137** `FrozenNetlist::validate` bounds the total number of pins across all instances
(connected or unconnected) by `MAX_TOTAL_PINS` (`2^28`) before `thaw` allocates instance pins.

**CORE-REQ-0138** `FrozenNetlist` provides direct query methods (`string`, module/cell/port/net/
instance/pin/assign accessors) over validated tables without thawing; `string` never panics even on
unvalidated tables or `ANONYMOUS`.

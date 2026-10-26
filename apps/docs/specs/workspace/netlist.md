# The `NETLIST` v1 (`netlist.rbin`) codec and workspace netlist table (`crates/core/rusteda-workspace/src/netlist.rs`)

## Container and 18 sections

**WS-REQ-0120** A workspace netlist table (`revisions/rev<N>/netlist.rbin`, table name `"netlist"`) is an `rbin` container of kind `"NETLIST"` and `format_version = 1` (`1..=1`). It contains the 18 sections of `rusteda_core::netlist::FrozenNetlist` (`section::STR_BYTES = 1` through `section::NET_PINS = 18`) in ascending section ID order with the element sizes defined by `rusteda_core::netlist::section::elem_size`, and records the table's upstream map in its `rbin` header per `WS-REQ-0038`.

**WS-REQ-0121** `netlist::encode(&FrozenNetlist)` and `netlist::encode_with_upstream(&FrozenNetlist, &Upstream)` are deterministic: equal `FrozenNetlist` values (and equal upstream maps) produce byte-for-byte identical `.rbin` bytes, and `thaw(&decode(&rbin)?)?.freeze() == f` round-trips every valid `FrozenNetlist` `f` unchanged.

## Zero-copy decoding and validation

**WS-REQ-0122** `netlist::decode(&Rbin) -> Result<FrozenNetlist<'_>>` validates the container kind (`"NETLIST"`), format version (`1..=1`; newer versions fail with `SCHEMA_TOO_NEW`), content hash (`xxh3-64`), and the presence and element sizes of all 18 sections, then borrows all 18 sections directly from the mapped or aligned `Rbin` buffer (`Cow::Borrowed` slices via `bytemuck` casts without copying section payloads).

**WS-REQ-0123** Before returning the borrowed `FrozenNetlist`, `netlist::decode` runs `FrozenNetlist::validate()` and maps any structural or semantic validation error (`FrozenError`) to `ErrorCode::CorruptTable`. No corrupted, truncated, or mutated `.rbin` input causes `netlist::decode` or `FrozenNetlist::thaw` to panic.

## Workspace revision helpers

**WS-REQ-0124** `netlist::add_to_txn(txn, &frozen, upstream)` validates `frozen` (returning `INTERNAL` if invalid) and stages `netlist.rbin` as table `"netlist"` via `Txn::add_rbin` with the given `upstream` map.

**WS-REQ-0125** `netlist::open_rev(rev)` and `netlist::open_head(ws)` map the committed `"netlist"` table of a revision (or `HEAD`) via `Revision::open_rbin`, verifying the `rbin` header and upstream map against `meta.json`.

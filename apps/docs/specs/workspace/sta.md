# The timing table `sta.rbin` (`STA` v1): read-only view and builder (`crates/core/rusteda-workspace/src/sta.rs`)

The static timing analysis tool of the suite commits its results as the workspace table `sta`.
The other tools read that table without depending on the timing engine: the synthesis tool
resolves `path<N>` handles to the cell instances of a ranked path, and the workbench lists
endpoints and paths. This specification describes the format as far as these readers need it,
the validation the shared reader performs, and a small builder of valid tables. The timing tool
owns the semantics of the values it writes (slack, arrival, required time); this module only
reads and writes them.

| Section | Requirements |
|---|---|
| [Container and sections](#container-and-sections) | WS-REQ-0126, WS-REQ-0127 |
| [Validation](#validation) | WS-REQ-0128, WS-REQ-0129 |
| [Names](#names) | WS-REQ-0130 |
| [Queries](#queries) | WS-REQ-0131 to WS-REQ-0134 |
| [Builder](#builder) | WS-REQ-0135, WS-REQ-0136 |

## Container and sections

**WS-REQ-0126** The timing table is the table `sta` of a revision, the file `sta.rbin`: an rbin
container (WS-REQ-0030) of kind `STA` and format version 2 (the reader accepts `1..=2`; a newer
version is `SCHEMA_TOO_NEW`, WS-REQ-0039) with exactly the 24 sections below, in id order, with
these element sizes (a version 1 table has the sizes in parentheses):

| Id | Section | Element bytes | Content |
|---:|---|---:|---|
| 1 | `STR_BYTES` | 1 | UTF-8 bytes of all strings |
| 2 | `STR_OFFS` | 4 | `u32` offsets into `STR_BYTES`, one per string plus one; string 0 is empty |
| 3 | `META` | 64 | one record of counts and modes |
| 4 | `CORNERS` | 16 | one record per corner |
| 5 | `CLOCKS` | 48 | one record per clock |
| 6 | `TAGS` | 16 | one record per launch tag |
| 7 | `DISABLED_ARCS` | 16 | one record per disabled timing arc |
| 8 | `NODE_REC` | 152 | timing values per (corner, node), full mode only |
| 9 | `TAG_EXTRA_PTR` | 4 | row pointer per (corner, node) into `TAG_EXTRA`, full mode only |
| 10 | `TAG_EXTRA` | 56 | arrivals of further launch tags, full mode only |
| 11 | `ENDPOINTS` | 36 (32) | one record per checked endpoint |
| 12 | `EP_SLACK` | 16 | setup and hold slack per (corner, endpoint) |
| 13 | `EP_ORDER` | 4 | endpoint rows sorted by slack per (corner, setup or hold) |
| 14 | `PATH_RANK` | 12 (8) | one record per ranked path, in rank order |
| 15 | `PATH_CHAIN_PTR` | 8 (4) | `u64` (`u32`) row pointer per ranked path into `PATH_CHAIN` |
| 16 | `PATH_CHAIN` | 4 | the steps of every ranked path |
| 17 | `PATHS` | 56 | the header of each materialized path |
| 18 | `PATH_POINTS` | 32 | the points of the materialized paths |
| 19 | `NAME_HASH` | 4 | open-addressed table of node names |
| 20 | `INST_HASH` | 4 | open-addressed table of instance names |
| 21 | `SUMMARY` | 80 | one record per (corner, path group) |
| 22 | `UNCONSTRAINED` | 12 | one record per unconstrained endpoint |
| 23 | `NOTES` | 16 | aggregated notes of the analysis |
| 24 | `SCOPE` | 1 | the scope of the analysis as a JSON object |

`StaTable::open_rev(rev)` maps the table through `Revision::open_rbin`, which validates the rbin
header and the upstream map of `meta.json` (WS-REQ-0037, WS-REQ-0038); a revision without the
table is `MISSING_TABLE`. `StaTable::from_bytes` opens table bytes in memory and
`StaTable::from_rbin` an rbin opened otherwise. Opening does not hash the content;
`StaTable::verify_hash` does (WS-REQ-0034), and a tool that derives a change of the design from
the table calls it before it uses the table.

**WS-REQ-0127** Records are little-endian with these fields, in byte order (`u32` unless
stated; `NO_INDEX` = `0xFFFF_FFFF` marks an absent index; time in ns):
- `META`: `num_nodes`, `num_port_bits`, `num_arcs`, `num_cells`, `num_startpoints`,
  `num_disabled_arcs`, `num_corners`, `num_clocks`, `num_tags`, `num_endpoints`,
  `num_ranked_paths`, `num_top_k_paths`, `num_path_groups`, `store_mode` (0 full, 1 summary),
  `backend_str`, `precision` (0 `f64`, 1 `f32`). Nodes are numbered with the port bits first
  (`0..num_port_bits`), then the instance pins.
- `CORNERS`: `name_str`, `lib_count`, `flags`, reserved. `CLOCKS`: `name_str`, `source_node`,
  `master_clock`, `flags`, then `f64` `period_ns`, `rise_edge_ns`, `fall_edge_ns`, then `f32`
  setup and hold uncertainty. `TAGS`: `clock_id`, `u8` launch edge, `u8` flags, `u16` exception
  state, `launch_source_node`, `path_group_str`.
- `NODE_REC`: `[[f64; 2]; 2]` transition, required time and slack, each indexed
  `[rise, fall][early, late]`; `u32` primary tag and the node's `NAME_HASH` bucket (or
  `NO_INDEX`); `[[f64; 2]; 2]` arrival of the primary tag; `[[u32; 2]; 2]` predecessors. Row
  `corner * num_nodes + node`.
- `ENDPOINTS`: `data_node`, `clock_node`, `capture_clock`, `path_group_str`, check arc,
  `kind_flags` (bits 0 to 7: kind 0 flip-flop, 1 latch, 2 output port, 3 recovery/removal pin,
  4 clock gate; bits 8 and 9: capture edge 0 rise, 1 fall, 2 none; bits 10 to 31 zero), the
  rank of the endpoint's worst setup-family and hold-family ranked path (or `NO_INDEX`), and the
  data node's `NAME_HASH` bucket plus one, or 0. `EP_SLACK`: `f64` setup and hold slack, NaN when
  absent, row `corner * num_endpoints + endpoint`. `EP_ORDER`: bucket
  `(corner * 2 + hold) * num_endpoints` holds the endpoint rows by ascending slack.
- `PATH_RANK`: `f32` slack, `u32` endpoint row, `u16` corner, `u8` flags (bit 0: hold family,
  bit 1: end transition fall, bits 2 and 3: check 0 setup, 1 hold, 2 recovery, 3 removal, bit 7:
  materialized), `u8` padding.
- `PATH_CHAIN` slice of a ranked path: required time and check value as `f64` word pairs (low
  word first), then per step three words: the node, the step word (bit 0 transition fall, bits 1
  and 2 point kind 0 port, 1 net, 2 cell, 3 clock launch, bits 3 to 31 tag slot) and the node's
  `NAME_HASH` bucket.
- Format version 1 differs in three records, which the reader converts to the version 2 values:
  `ENDPOINTS` has no ninth word and keeps the `NAME_HASH` bucket plus one in bits 10 to 31 of
  `kind_flags`; `PATH_RANK` is `f32` slack, `u16` bits 0 to 15 of the endpoint row, `u8` corner
  in bits 0 to 3 and bits 16 to 19 of the endpoint row in bits 4 to 7, `u8` flags; a chain step
  is two words, the step word (bits 0 to 23 node, bit 24 transition fall, bits 25 and 26 point
  kind, bits 27 to 31 tag slot) and the node's `NAME_HASH` bucket, so a step names only the low
  24 bits of its node, whose whole value the bucket holds.
- `PATHS`: `f64` slack, arrival, required time and check value; rank, endpoint row, launch
  clock, capture clock, first and end index into `PATH_POINTS`. `PATH_POINTS`: `f64` arrival,
  increment and transition time; node, `u8` transition (0 rise, 1 fall), `u8` point kind,
  `u16` padding.
- `SUMMARY`: `f64` setup WNS, setup TNS, hold WNS, hold TNS; corner, `group_str`, setup
  violating and setup endpoints, hold violating and hold endpoints, transition-limit and
  capacitance-limit violations, total and unconstrained endpoints, two reserved words. A WNS
  without endpoints is written as 0.

## Validation

**WS-REQ-0128** Opening a table validates its structure in time independent of the table size,
and fails with `CORRUPT_TABLE` unless: the kind is `STA`; the directory has exactly the 24
sections with ids 1 to 24 and the element sizes of WS-REQ-0126; the file ends at the end of the
last section rounded up to 8 bytes; `META` holds one record with store mode and precision 0 or
1, `num_port_bits <= num_nodes` and `num_top_k_paths <= num_ranked_paths`; `STR_OFFS` starts
at 0 and ends at the length of `STR_BYTES`; the counts of `CORNERS`, `CLOCKS`, `TAGS`,
`DISABLED_ARCS`, `ENDPOINTS`, `PATH_RANK` and `PATHS` equal those of `META`, `EP_SLACK` holds
`num_corners * num_endpoints` records, `EP_ORDER` twice as many and `SUMMARY`
`num_corners * num_path_groups`; `PATH_CHAIN_PTR` holds `num_ranked_paths + 1` entries from 0
to the length of `PATH_CHAIN`; in the full mode `NODE_REC` holds `num_corners * num_nodes`
records and `TAG_EXTRA_PTR` one more entry from 0 to the length of `TAG_EXTRA`, in the summary
mode the three are empty; `NAME_HASH` and `INST_HASH` hold whole 4-word buckets, zero or a
power of two of them; `SCOPE` holds at least 2 bytes. Products of counts are computed without
overflow.

**WS-REQ-0129** Every index read from the table is checked before it is used: string ids
(range, byte range, UTF-8), `NAME_HASH` buckets (range, occupied, node below `num_nodes`),
nodes of chain steps and points, corners and endpoint rows of rank records (the hold-family
bit agreeing with the check), endpoint kinds and capture edges, ranks named by endpoints,
`EP_ORDER` buckets (a permutation of the endpoint rows), the inner entries of `PATH_CHAIN_PTR`
(a slice of at least the four header words and whole steps), the rank, endpoint and point range
of a materialized path, the transition and kind of its points, and the corner of a summary
row. A failure is `CORRUPT_TABLE`. No input makes a query panic, and every allocation is
bounded by the lengths of the sections: a name found by node uses the bucket the table records
for it (in the record, the endpoint flags or the chain step) and otherwise one map of the
occupied `NAME_HASH` buckets.

## Names

**WS-REQ-0130** A `NAME_HASH` bucket holds four words: the low word of the xxh3-64 hash of the
node name (at least 1; 0 marks an empty bucket), the node, the prefix string and the leaf
string. A port bit (node below `num_port_bits`) has no prefix (`NO_INDEX`) and its name is the
leaf; an instance pin has the instance name as prefix and its name is `prefix/leaf`; any other
combination is `CORRUPT_TABLE`. A name is found by probing from bucket
`(hash >> 32) & (buckets - 1)` linearly until an empty bucket (`StaTable::node_by_name`). The
instance of a pin is its prefix string, never a part of the joined name, so instance names that
contain `/` are kept whole. An `INST_HASH` bucket holds the low hash word of the instance name,
the instance string, the cell string in bits 0 to 19 with the pin count (at most 4095) in bits
20 to 31, and the first pin node of the instance.

## Queries

**WS-REQ-0131** `StaTable::summary` returns every `SUMMARY` row and `corner_summary(c)` the
row at `c * num_path_groups`, which belongs to corner `c` and aggregates its endpoints (the path
group `**`), with corner and group names; a WNS is reported as absent, not 0, when its
endpoint count is 0. `corner_names`, `clocks` (name, period, setup uncertainty), `counts`,
`store_mode` and `scope` (the `SCOPE` JSON object; anything else is `CORRUPT_TABLE`) complete
the summary queries. A corner index outside the table is `UNKNOWN_HANDLE`.

**WS-REQ-0132** `StaTable::endpoints(c)` returns the endpoints in `ENDPOINTS` order with the
name and instance of their data node, their kind, their setup and hold slack in corner `c`
(absent when NaN) and the rows of their worst setup-family and hold-family ranked paths;
`endpoint(i, c)` returns one of them (`UNKNOWN_HANDLE` outside the table) and
`endpoint_order(c, hold)` the `EP_ORDER` bucket of corner `c`.

**WS-REQ-0133** Ranked paths are addressed by row: the handle `path<N>` is row `N - 1` of
`PATH_RANK` (WS-REQ-0022), in the rank order the table records. `StaTable::path_ranks` returns
the rank records (check, corner, endpoint row, end transition, `f32` slack) and
`StaTable::path(row)` the path with its points; a row outside the ranked paths is
`UNKNOWN_HANDLE` naming `path<row + 1>`.

**WS-REQ-0134** The first `num_top_k_paths` ranked paths are materialized: `PATHS[row]` (whose
rank and endpoint must be those of the rank record) gives the exact `f64` slack and its point
range gives the points with their arrival times; the chain of the same row, when it has steps,
has one step per point with the same node (in a version 1 table, the step's 24 bits are the low
bits of the point's node) and gives each point's name bucket (without steps the name is found by
node, WS-REQ-0129). A ranked path beyond those is read from its chain alone: its points have the
names of their buckets (whose node must agree with the step's node, or its 24 bits in version 1),
kinds and transitions but no
arrival times, and its slack is the `f32` of the rank record (`slack_exact` false). Both hold in
the full and the summary mode.
`StaTable::path_required(row)` returns the required time and check value of the chain header.
`RankedPath::instances` lists the instances of the pins on the path, each once, in path order.

## Builder

**WS-REQ-0135** `StaTableBuilder` writes a valid `STA` v2 table from ports, instance pins (with
optional cell names), corners, clocks, endpoints with their per-corner slacks and ranked paths
with their points; it analyzes nothing, and every slack and arrival is the caller's. It numbers
the port bits first, then the pins instance by instance; writes string 0 empty and shares equal
strings; sizes `NAME_HASH` and `INST_HASH` to the next power of two of at least twice their
entries and 16 (0 buckets when empty); sorts endpoints by node; ranks paths setup family first,
then by slack, corner, endpoint row and end transition, ties in the order given; materializes
the first `top_k` ranked paths (default all); writes one path group `**`, one launch tag, no
disabled arcs, unconstrained endpoints or notes, a `SCOPE` with `wire_model` `none`,
`clock_model` `ideal` and backend `builder`, and in the full mode absent values in `NODE_REC`
except the arrivals of path points. A path that does not end at an endpoint, an unknown corner
or node, a non-finite time, no corner, more than 16 corners, 2^32 - 1 endpoints or nodes, more
than 4095 pins of one instance or a cell name past string 2^20 - 1 (the fields of `INST_HASH`)
is `INTERNAL`. `StaTableBuilder::stage(txn, upstream)` adds the table as
`sta` (`sta.rbin`) with `upstream` in its header (WS-REQ-0038) and sets the summary values
`setup_wns_ns`, `setup_tns_ns`, `setup_violating_endpoints`, `hold_wns_ns`, `hold_tns_ns`,
`hold_violating_endpoints`, the design-rule values of WS-REQ-0137, `total_endpoints`,
`unconstrained_endpoints` and `corners`, and the status `timing` (`met` or `violated`), of the
timing table; `to_bytes(upstream)` returns the bytes.

**WS-REQ-0136** The builder is deterministic: equal inputs give byte-identical tables, `-0.0` is
written as `+0.0` and NaN as the quiet NaN `0x7ff8_0000_0000_0000`, and every table it writes
passes the validation of WS-REQ-0128 and WS-REQ-0129 and reads back with the queries of
WS-REQ-0131 to WS-REQ-0134.

**WS-REQ-0137** The builder checks no design rule: it sets the summary values
`max_trans_violating` and `max_cap_violating` to `null` and `drv_status` to `"unchecked"`, never
to zero, and its `SUMMARY` rows hold no design-rule counts.

# Detailed parasitic import

**RCLI-REQ-0070**: `import --spef FILE --spef-corner NAME` requires a committed
  netlist (or one imported in the same transaction), a library for NAME, and the
  usual exclusive lock and base revision. A `PARASIT` v1 rbin (written before
  RCLI-REQ-0075) stores the immutable source in section 1 and UTF-8 JSON
  `{corner, limits}` metadata in section 2; readers keep accepting it.
  Record source identity. The table depends on `netlist`; import invalidates
  `sta` and `sta_check` even when a previous timing table predates parasitics.
**RCLI-REQ-0071**: The shared SPEF reader supports detailed D_NET, NAME_MAP,
  connections, ground and inter-net coupling C, scalar R, explicit NS/PS, PF/FF,
  OHM/KOHM units, divider/delimiter/bus delimiters and escaped names. Values must
  be finite and nonnegative; errors are T0071 (syntax), T0072 (budget), T0073
  (unsupported semantics). Reject reduced nets, inductance, triplets, pin-load
  attributes, included pin capacitances, duplicate identifiers and unresolved
  names. Metadata such as coordinates and date do not affect extracted RC.
**RCLI-REQ-0072**: Limits are configurable and stored: defaults 32 MiB source,
  100k nets, 500k nodes/name-map entries, 1M elements and 16 MiB names, with an
  absolute ceiling of eight times each default. Names have 4096-byte and lines
  65536-byte caps. No unbounded enumeration or recursion. Ground coupling at
  factor one on each net's local endpoint; T0074 warns that crosstalk is absent.
  Require declared total C to agree with records within max(1e-8 pF, 1e-4 relative).

CLI budgets: `--max-spef-mib`, `--max-rc-nets`, `--max-rc-nodes`,
`--max-rc-elements`, `--max-rc-name-mib`. Missing SPEF never causes these budgets
or extraction modes to be implicitly enabled. A timing consumer must bind the
connections and check complete annotation; it must not replace missing RC with
estimated/ideal wires. A consumer asking for a corner without a source must
fail explicitly.

**RCLI-REQ-0075**: `--spef FILE --spef-corner NAME` is repeatable and pairs in
  order; unequal counts, a corner given twice, more than 64 corners or a corner
  without a library are rejected before anything is read. The `parasitics`
  table is a `PARASIT` v2 rbin: section 1 is UTF-8 JSON `{corners: [{corner,
  limits, source_path, source_hash}]}` in ascending corner order (at most
  256 KiB), and section `i + 2` is the canonical decoded source of entry `i`.
  The corners of the base revision's table (v1 or v2) whose netlist is the
  netlist of the new revision are kept, an imported corner replaces its old
  source, and the source identity of every corner is recorded. Metrics name
  `spef_corners`, `spef_kept_corners` and per imported corner `spef_imported`
  (nets, nodes, elements, compression, byte counts); a one-file import also
  keeps the flat `spef_*` metrics. The decoded sources of all corners of the
  new table, kept and imported, which the import holds at once, total at most
  eight times the per-file byte budget (`--max-spef-mib`); more is rejected
  with `T0072` and nothing is committed. The import drops `sta`, `sta_check`
  and `limits`.

**RCLI-REQ-0076**: An import that drops the `parasitics` table of its base
  revision (its netlist changed, so the table's upstream no longer holds) warns
  with one `T0140` that names the dropped corners, the reason and the new
  revision: its timing has no extracted parasitics until SPEF is imported again,
  and a timing command that requires them (`--require-wire-model spef_rc`)
  rejects it. The `dropped` list of the envelope names the table as before. An
  import that replaces or keeps the table does not warn.

**RCLI-REQ-0073**: Accept plain UTF-8 and gzip SPEF, detected by magic bytes.
  Both encoded and decoded inputs obey `--max-spef-mib`; validate gzip CRC and
  concatenated members. Store canonical decoded source in `PARASIT` section 1,
  while source identity describes the original file. Report compression and
  encoded/decoded byte counts. Reject symlinks, FIFOs and devices before reading;
  Unix opens are nonblocking and do not follow symlinks. Failures preserve HEAD.
**RCLI-REQ-0074**: Public in-memory parasitic objects must validate actual node
  counts, finite nonnegative values, names, indices and totals before a timing
  consumer indexes them. A forged serialized counter cannot reduce the memory
  estimate below actual stored vectors.

The parser estimates <512 MiB extra peak state at default limits (two 32 MiB
source copies, <64 MiB names, 80 MiB nodes/index, 64 MiB elements and <80 MiB
CSR/tree temporaries). A consumer adds its own corner/pin/tag state and accounts
for worker concurrency. The source and all parse results are independent of GUI
and Python. IEEE 1481-1999 defines the exchanged detailed parasitic format.

Compressed input does not increase these caps: retain at most 32 MiB encoded
and 32 MiB decoded data plus a bounded decoder window. Small gzip/CRC/bomb tests
use <4 MiB each and two test workers. A byte cap applies before token and graph
budgets. No external decompressor or background process is required.

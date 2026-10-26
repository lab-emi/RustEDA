# rusteda-compute: the timing propagation task

The timing task (module `timing` of `rusteda-compute`) propagates arrival times, transition times
and predecessors forward over a levelized timing graph. A static timing engine lowers its timing
graph, the timing tables of its libraries, its start values and its tag maps into the task's
interface: flat arrays of plain records, borrowable from mapped tables. The task never sees
names, netlists, Liberty files or constraints. Its decisions are recorded in
[decisions.md](decisions.md); the device layer and the backend kinds it builds on are in
[README.md](README.md).

Notation: `n` nodes, `T` tags, `tr` the transition (0 rise, 1 fall), `an` the analysis (0 early,
reduced with the minimum; 1 late, reduced with the maximum). For analysis `an`, a value `x` is
*better* than `y` when `x < y` (early) or `x > y` (late), and the *absent* value is `+inf`
(early) or `-inf` (late). The task is unit-agnostic; engines lower times in ns and capacitances in
pF, the units in which COMPUTE-REQ-0019 states the single-precision bound.

**COMPUTE-REQ-0010** Task and backends. `TimingPropagationBackend` is the task's trait; its
backends are `cpu` (pure Rust, `f64`, always compiled, authoritative) and `cuda` (feature `cuda`,
`f64` by default, `f32` on request). `timing_backend(kind, precision)` selects one:
- `Cpu` is the CPU backend; it computes in `f64` only, and `F32` with `Cpu` is unavailable;
- `Cuda` is the CUDA backend in the requested precision. It is unavailable when the build lacks the
  feature `cuda`, or when the default CUDA device fails its preparation (the device self-test of
  COMPUTE-REQ-0001, once per process);
- `Gpu` is the first compiled GPU backend (in the order of `GpuRuntime::ALL`) whose device passes
  its preparation; it is unavailable when there is none.

Selection never falls back silently: an unavailable backend is the error of COMPUTE-REQ-0025, never
another backend. The engine chooses the backend; the task has no default and no automatic choice.

**COMPUTE-REQ-0011** Graph interface. `LevelizedTimingCsr` holds:
- nodes numbered `0..n` by topological ID: level `l` holds the nodes `level_ptr[l] ..
  level_ptr[l+1]`, so that `level_ptr` starts at 0, never decreases and ends at `n`;
- the incoming arcs of node `v`, `in_arcs[in_ptr[v] .. in_ptr[v+1]]` (`in_ptr` has `n + 1`
  entries, starts at 0, never decreases and ends at the arc count). An `InArc` (12 bytes) is
  `{src, class, map}`: the source node, an index into the arc classes (COMPUTE-REQ-0013) and an
  index into the tag maps or `NONE` (`u32::MAX`, the identity map). Every source lies on a lower
  level than its destination: for a node of level `l`, `src < level_ptr[l]`. Within a node, the
  arcs are sorted by source, never decreasing; the order of arcs with the same source is the
  engine's canonical arc order. The CSR order is the only tie-break of the task;
- the outgoing adjacency `out_dst[out_ptr[u] .. out_ptr[u+1]]`: the distinct destinations of the
  arcs whose source is `u`, increasing (`out_ptr` has `n + 1` entries);
- the tag maps of COMPUTE-REQ-0012.

Records are `#[repr(C)]` plain old data in host byte order (`bytemuck::Pod`), and the arrays are
`Cow` slices, so that a table mapped from disk can be borrowed without a copy.

**COMPUTE-REQ-0012** Tag maps. Map `m` is the record range `maps[map_ptr[m] .. map_ptr[m+1]]`
(`map_ptr` is empty or `[0]` when there are no maps; otherwise it starts at 0, never decreases and
ends at the record count). A `TagMapRec` (80 bytes) is `{src_tag, dst_tag, shift, open[2],
close[2], close_slew[2], limit[2]}`, the arrays indexed by analysis: arrivals of tag `src_tag` at
the arc's source enter tag `dst_tag` at its destination, shifted by `shift` (a change of time
origin between the tags), limited to a window from `open` (the opening edge) up to the closing
edge `close` minus the setup time of the arc's class at the closing-edge transition `close_slew`,
and at most `limit` (COMPUTE-REQ-0018). `open = -inf`, `close = +inf` and `limit = +inf` disable
the respective bound. A latch data-to-output arc re-launches in the opening-edge tag of its
enable this way; an exception state change (a `-through` point) is a record with a shift of 0 and
no window. An arc whose map is `NONE` keeps every tag.

**COMPUTE-REQ-0013** Library interface. `CompiledLutBank` holds one corner's tables:
- `luts`: `LutDesc` (40 bytes) `{n[3], off[3], arg[3], voff}`. Axis `k` has `n[k]` points at
  `data[off[k] ..]`, strictly increasing, and is indexed by the canonical argument `arg[k]` (0, 1
  or 2, COMPUTE-REQ-0014); `n[k] = 0` marks an absent axis, and the axes in use come first (0 to
  3 axes). The values start at `data[voff]`, one value for a table without axes, otherwise
  `n[0] * n[1] * n[2]` values (absent axes count 1) in row-major order: the last axis varies
  fastest;
- `data`: axis points and values;
- `classes`: `ArcClass` (28 bytes) `{flags, delay[2], slew[2], setup[2]}`. Flag bit 0 (`WIRE`)
  marks a wire arc; flag bit `1 + 2 ti + to` lets input transition `ti` produce output
  transition `to` (positive unate: rise to rise and fall to fall; negative unate: the crossed
  pair; non-unate: all four; a disabled arc: none). `delay[to]` and `slew[to]` are the delay and
  output transition tables of an output transition, `setup[ti]` the setup tables of the tag-map
  window per data transition; every entry is a table index or `NONE` (a table of value 0). A wire
  class has no delay or transition tables and only the bits rise to rise and fall to fall. Unate,
  non-unate, three-state and latch arcs are cell classes that differ only in their bits and
  tables.

**COMPUTE-REQ-0014** Table evaluation. A table is evaluated at three canonical arguments
`(x0, x1, x2)`: for the delay and output transition tables of an arc into node `v`, the
transition of the source pin, the load of `v` and again the load of `v`; for the setup tables of a
tag-map window, the record's `close_slew` (related pin), the transition of the source pin
(constrained pin) and the load of `v` (the output of the latch). Axis `k` with points `p` of count
`m` and argument `x = x[arg[k]]` gives the interval `(i0, i1)` and weight `w`: when `m = 1`,
`(0, 0)` and `w = 0`; otherwise `i0` is the smallest index with `i0 + 2 >= m` or `x <= p[i0+1]`,
`i1 = i0 + 1` and `w = (x - p[i0]) / (p[i1] - p[i0])`, which extrapolates linearly outside the
points. With `V(i, j, k)` the values, the result is, in exactly this order of operations:
- no axis: `V()`;
- one axis: `V(i0) + (V(i1) - V(i0)) * w0`;
- two axes: `lo = V(i0,j0) + (V(i0,j1) - V(i0,j0)) * w1`, `hi = V(i1,j0) + (V(i1,j1) -
  V(i1,j0)) * w1`, result `lo + (hi - lo) * w0`;
- three axes: `C(i, j) = V(i,j,k0) + (V(i,j,k1) - V(i,j,k0)) * w2` for the four pairs, then the
  two-axis formula on `C`.
Each operation is one rounded IEEE 754 operation (round to nearest even), without contraction
into fused multiply-adds. A table index `NONE` evaluates to 0.

**COMPUTE-REQ-0015** Corner inputs. Besides the graph and the bank, a propagation takes:
- `ntags`, the tag count `T >= 1`;
- `load[v][tr * 2 + an]`, the load of node `v` for its output transition `tr` and analysis `an`;
- the start rows `StartRows {node, arr, slew}`: the nodes with start values, strictly increasing;
  row `k` holds the arrivals `arr[(k * T + t) * 4 + tr * 2 + an]` and the transitions
  `slew[k * 4 + tr * 2 + an]` of `node[k]`. Absent entries are allowed; a present arrival needs a
  present transition of the same transition and analysis;
- the wire records: none (ideal wires), or one `WireRec` (64 bytes) `{delay[4], slew[4]}` per
  incoming arc in CSR order, indexed `to * 2 + an`. A wire arc adds `delay` to the arrival and
  degrades the transition `s` to `s` when the term `k = slew` is 0 and to `sqrt(s * s + k * k)`
  otherwise; the records of other arcs are all zero.

`TimingStateSoA {ntags, load, starts, arr, slew, pred}` carries these inputs with the values of
COMPUTE-REQ-0016 for the single-call form (COMPUTE-REQ-0023).

**COMPUTE-REQ-0016** Values. The values of a propagation are, for every node:
- the arrivals `arr[((v * T + t) * 2 + tr) * 2 + an]` per tag, transition and analysis;
- the transitions `slew[(v * 2 + tr) * 2 + an]` per pin: one per transition and analysis, shared
  by all tags;
- the predecessors `pred`, indexed like the arrivals: `src | (ti << 31)` for the source node and
  source transition of the arc whose candidate set the arrival, and `NO_PRED` (`u32::MAX`) for an
  arrival that is a start value or absent. For an arc with a tag map, the source tag is the
  `src_tag` of a record of the arc's map whose `dst_tag` is the arrival's tag.
Absent values are the absent value of their analysis. Every stored value is canonical: a zero is
stored as `+0.0`, never as `-0.0`, so that two values are equal exactly when their bits are equal.

**COMPUTE-REQ-0017** Propagation. Levels are processed in increasing order; the nodes of a level
read only values of lower levels, so they may be computed in any order and in parallel. Node `v`:
1. takes its start row, when it has one, as its initial transitions and arrivals (predecessors
   `NO_PRED`), and absent values otherwise;
2. then visits its incoming arcs in CSR order; for each arc (source `u`, class `c`), each input
   transition `ti` and output transition `to` (in increasing order) that the class allows, and each
   analysis `an` (early first) for which the source's transition `s = slew(u, ti, an)` is present:
   - evaluates, with `L = load(v, to, an)`: for a wire class, the delay `d` and transition `q` of
     its wire record (COMPUTE-REQ-0015; `d = 0`, `q = s` without records); for a cell class, `d`
     from table `delay[to]` and `q` from table `slew[to]`, a negative `q` clamped to 0
     (transitions clamp at 0, delays do not);
   - replaces `slew(v, to, an)` by `q` when `q` is better;
   - offers the arrivals of COMPUTE-REQ-0018 to the arrivals of `v` at `(to, an)`: a candidate
     replaces the current arrival only when it is better, so that among equal candidates the
     start value and then the earliest in this order win, and sets the predecessor to
     `u | (ti << 31)`.

**COMPUTE-REQ-0018** Arrivals through an arc. For the identity map, each tag `t` in increasing
order whose source arrival `a = arr(u, t, ti, an)` is present offers `a + d` to tag `t`. For a
tag map, each record in record order whose source arrival `a = arr(u, src_tag, ti, an)` is present
computes `m = a + shift`, then:
- if `m < open[an]`, the record offers nothing;
- the upper bound `h` is `limit[an]`; when `close[an]` is finite, `h` becomes `close[an] - S` if
  that is smaller, where `S` is the arc class's table `setup[ti]` (COMPUTE-REQ-0014);
- if `m > h`, then `m = h`;
and offers `m + d` to tag `dst_tag`.

**COMPUTE-REQ-0019** Determinism and consistency. The values are a function of the inputs alone:
- every backend and every thread count gives the same values; the `cpu` backend and the `cuda`
  backend in `f64` agree bit for bit (arrivals, transitions and predecessors), with no reduction
  that depends on the order of execution, and kernels compiled without fused multiply-add
  contraction (COMPUTE-REQ-0028);
- repeated propagations of the same inputs give the same bits on every backend and precision;
- the `cuda` backend in `f32` (tables, inputs and values rounded to single precision) agrees with
  the `cpu` backend within `max(1e-4 ns, 2e-5 |v|)` for every value `v` with `|v| <= 100 ns` and
  agrees on which values are absent, except where a candidate lies within that bound of a window
  bound of a tag map (a near-tie of the window decision); its predecessors may differ where
  candidates lie within that bound of each other.
Tolerance reason: single precision has a 24-bit significand, a relative rounding of `6e-8` per
operation; arrival sums over paths of a few hundred arcs and table interpolations stay within
`2e-5` of the magnitude, and `1e-4 ns` covers values near zero.

**COMPUTE-REQ-0020** Validation. `TimingProblem::validate()` accepts a problem only when every
statement of COMPUTE-REQ-0011 to COMPUTE-REQ-0015 about it holds: array lengths and pointer
sequences, every index in range, sources on lower levels, sources sorted within a node, the
outgoing adjacency equal to the transposed incoming arcs, maps' tags below `T`, class flags with
only defined bits and a wire class without tables or inverting bits, table axes in use first and
strictly increasing, start nodes strictly increasing and below `n`, present arrivals with present
transitions, wire records of other arcs zero, no NaN in any value, finite table data, loads,
shifts and closing-edge transitions, `open < +inf`, `close > -inf`, `limit > -inf`, and fewer
than `2^31` nodes. A violation is `ComputeError::Invalid` naming the first offending item. Every
backend validates a problem when it opens a session, and every patch (COMPUTE-REQ-0021) before it
applies it, so that no kernel reads out of bounds.

**COMPUTE-REQ-0021** Sessions. `open(problem)` validates the problem and makes it resident (on the
device for a GPU backend) as a `TimingSession`. `propagate(request)` computes the values of a
scope:
- `All`: every node;
- `FromLevel(l)`: every node of level `l` and above, from the session's resident values below;
- `Cone(seeds)`: the forward cone of the seed nodes and of every node patched since the last
  propagation, level by level: a node of the cone is recomputed; when its arrivals, transitions
  or predecessors differ in any bit from its resident values, they are replaced and the
  destinations of its outgoing arcs join the cone; otherwise the cone stops at it.
`FromLevel` and `Cone` need resident values: a previous complete propagation, or values written
with `write_values`; otherwise, and after a cancelled propagation, they are rejected. The result of
every scope equals that of `All` on the same inputs when the resident values were complete for
the inputs before the patches. The patches `set_loads(node, load)`, `set_arc_classes(arc index,
class)` and `set_starts(start rows)` change the inputs of a session; the patched nodes (the node,
the arc's destination, and the nodes whose start row changed in any bit) join the next cone.
`changed()` lists, after a `Cone`, the nodes whose values were replaced, increasing.

**COMPUTE-REQ-0022** Readback and residency. A GPU backend keeps the inputs and the values of a
session on the device between levels and between propagations, and copies values to the host view
`values()` only as requested: `Readback::All`, `None`, `Rows(nodes)` (the rows of the listed
nodes: arrivals, transitions and predecessors) or `Changed` (the rows the propagation replaced:
after `Cone` the changed nodes, after `FromLevel(l)` the nodes from level `l` on, after `All`
every node). `read(readback)` copies later. A row read with `Rows` equals the same row read with
`All`. The CPU backend computes in place, so its host view is always complete.

**COMPUTE-REQ-0023** Single call. The trait's provided method `propagate_levels(graph, lut_bank,
state, deterministic)`, the signature of the development plan, opens a session on the inputs
carried by the state (COMPUTE-REQ-0015, ideal wires), propagates `All`, reads back `All` into the
state's `arr`, `slew` and `pred`, and returns the statistics.

**COMPUTE-REQ-0024** Statistics. Every propagation returns `PropagationStats`: the backend kind
(`Cpu` or `Cuda`), the device description, the precision, whether the reductions were
deterministic (always true for the backends of this specification; a request with `deterministic =
false` permits order-dependent reductions but does not require them), the levels visited, the
kernel launches, the nodes evaluated and changed (for `All` and `FromLevel` every evaluated node
counts as changed), `max_abs_ns`, the largest magnitude of a present value among the evaluated
rows (0 when there is none), and the wall-clock seconds of upload, kernels, readback and the
total.

**COMPUTE-REQ-0025** Errors. `ComputeError` and its envelope codes (`rusteda.v1`):

| `ComputeError` | Envelope code | Diagnostic |
|---|---|---|
| `NotCompiled`, `Unavailable` | `BACKEND_UNAVAILABLE` (exit 1) | A0501 |
| `TooLarge` (device memory or index limits) | `INPUT_REJECTED` | A0502 |
| `Compile` (kernel compilation) | `INTERNAL` | A0503 |
| `Device` (driver, launch or execution) | `INTERNAL` | A0504 |
| `Invalid` (problem or patch), on lowered inputs | `INTERNAL` | A0505 |
| `Invalid`, on inputs mapped from a table | `CORRUPT_TABLE` | A0505 |
| `Cancelled` | `CANCELLED` | A0506 |

Diagnostic codes A0500 to A0599 belong to `rusteda-compute`. An error never comes with values: a
failed propagation leaves the session without resident values.

**COMPUTE-REQ-0026** Cancellation and progress. A request carries a job context (JOB-REQ-0002):
propagation checks the cancel token before each level and stops with `Cancelled`, and reports
progress `propagate` per level (JOB-REQ-0004).

**COMPUTE-REQ-0027** CPU backend. The `cpu` backend computes the nodes of a level on the threads of
the calling thread's rayon pool, each node sequentially as COMPUTE-REQ-0017 states, in `f64`.
Levels with few nodes run on the calling thread. The backend computes in the host view directly,
and its sessions borrow the problem's arrays until a patch copies the patched array.

**COMPUTE-REQ-0028** CUDA backend. The `cuda` backend runs the kernels of
`timing/kernels/timing.cu`, compiled by NVRTC per precision: `-DREAL_F64` with `--fmad=false` for
`f64`, and single precision with the default options otherwise (COMPUTE-REQ-0002,
COMPUTE-REQ-0004). It launches one kernel per level in which each thread computes one node as
COMPUTE-REQ-0017 states; a cone launch skips the nodes outside the cone and compares each
recomputed row with the resident one. Each session has its own stream. Before uploading, the
backend compares the memory a session needs with the free device memory and reports `TooLarge`
when it does not fit. Full readback goes through page-locked staging memory that is not
write-combined.

**COMPUTE-REQ-0029** Preparation. `prewarm(kind, precision)` selects the backend
(COMPUTE-REQ-0010) and compiles its kernels for the precision, so that an engine can overlap the
preparation with other work on a second thread (COMPUTE-REQ-0003); its result is the result the
selection would give.

## Prepared nonlinear arcs

**COMPUTE-REQ-0030**: TimingProblem may carry one 128-byte ArcValues per incoming
arc, with f64 delay/slew indexed (input transition, output transition, bound).
Prepared values override cell lookup and wire degradation. They are immutable
snapshots of loads, starts and classes: session patch methods must reject edits
until the caller re-prepares the model. Validate size, finite values and
nonnegative transitions. CPU/CUDA use identical data and agree bitwise in f64.
Include these buffers in host/device budgets; test a three-node case plus
extracted-engine propagation without enumerating timing paths.

# rusteda-compute decision record

- Status: accepted (contract item CA-COMPUTE of the RustEDA shared-crate contract)
- Date: 2026-10-16
- Scope: the shared crate `rusteda-compute`: its backends, the CUDA device layer and the timing
  task ([timing.md](timing.md)). The earlier GPU-runtime decision records of the tool repositories
  are superseded by this record.

## Context

The RustEDA development plan (section 1.3, section 5.2 and its rules 5 and 6) puts every
compute-heavy task behind a coarse-grained trait of `rusteda-compute`: a pure Rust CPU
implementation that is always built and authoritative, and native GPU implementations (CUDA on
Linux, later Metal on Apple Silicon) that are checked for consistency with it. Upper crates never
import a GPU binding. The first tool versions offloaded the arrival propagation of a timing engine
to GPUs with kernels in a cross-platform GPU language on two runtimes, then with CUDA C kernels
launched by the engine itself. Local measurements on the lab GPU gave the following figures:
- a prototype of this task in `f64` agreed bit for bit with its CPU backend on 53 design runs and
  96 random problems, with per-tag transitions and clip windows (semantics that the task has since
  replaced, so the identity has to be established again);
- on a 1.74M-node graph, propagation alone took 17 ms on the CPU backend, 5.12 ms resident on
  the GPU in `f64` and 2.24 ms in `f32`;
- end to end, one corner was no faster on the GPU (1.238 s against 1.255 s at 501k cells), the
  CUDA context alone costing 135 to 231 ms per process.

## Decisions

**CD-1 Native backends behind task traits.** `cpu` is pure Rust (`rayon`), always compiled.
`cuda` runs CUDA C kernels compiled at run time by NVRTC and launched through the driver API with
`cudarc` (pinned to an exact version, with the driver and NVRTC libraries loaded at run time, so
that building needs no CUDA toolkit). `gpu` is the first compiled GPU backend whose device passes
a per-process self-test. No cross-platform GPU kernel language and no device-code crates are used.
Metal is on the roadmap; it cannot be built or tested on the lab machines, and the single-precision
consistency tests are its contract.

**CD-2 Unsafe code and dependencies.** `unsafe` code is confined to the GPU glue of this crate
(the modules `cuda` and `timing::cuda`), each module with a module-scoped `allow` and each block
with a `// SAFETY:` comment. GPU binding crates (`cudarc`, `objc2*`, `metal`, `cubecl`, `wgpu`)
are dependencies of `rusteda-compute` only; each tool repository's layout check rejects them in
every other crate, in every kind of dependency, also behind renames.

**CD-3 Shape of the timing task.** A session API: `open(problem)` makes a problem resident and
returns a session that propagates scopes (`All`, `FromLevel`, `Cone`), reads back rows and takes
patches of loads, arc classes and start rows. The plan's exact signature
`propagate_levels(graph, lut_bank, state, deterministic)` is a provided method of the backend
trait for one corner; the state carries the per-node loads and the start rows, so that the
signature needs no further argument. Engines keep their own graphs and lower them into the task's
plain-record interface, which can be borrowed from mapped tables.

**CD-4 Semantics of the timing task.** One transition per pin, transition and analysis: the worst
over all enabled incoming arcs, including arcs from sources without arrivals, so that delays do
not depend on tags. Arrivals per tag pass an arc unchanged or through a tag map (a time shift
and a window bounded by opening edge, closing edge minus setup and a limit), which expresses a
latch re-launching data in the opening-edge tag of its enable and, later, exception state
changes. Start values are merged first; every node reduces its incoming arcs sequentially in CSR
order with a strict comparison, so that ties go to the start value and then to the canonical arc
order. Transitions clamp at 0, delays do not. Zeros are stored as `+0.0`, so that equal values
have equal bits.

**CD-5 Precision and consistency.** `f64` on the CPU is authoritative and the only CPU
implementation. CUDA computes in `f64` by default, compiled without fused multiply-add
contraction, and agrees with the CPU bit for bit. `f32` (single-precision tables, inputs and
values) is opt-in: the plan's configurable single- or double-precision delay tables. Its tracked
consistency bound against the CPU is `max(1e-4 ns, 2e-5 |v|)` for `|v| <= 100 ns`, near-ties of
a window decision excluded: the plan's absolute `1e-4 ns` is unreachable in single precision on
long paths.

**CD-6 Selection and errors.** The default backend of the tools is `cpu`; there is no automatic
choice and never a silent CPU fallback. An uncompiled backend, `cuda` without a usable driver or
device, and `gpu` when no GPU backend passes the self-test are `BACKEND_UNAVAILABLE` (exit 1)
with diagnostic A0501; the diagnostic codes A0500 to A0599 belong to this crate. A problem too
large for the device is `INPUT_REJECTED`, kernel compilation and device failures are `INTERNAL`,
an invalid problem is `INTERNAL` when the engine lowered it and `CORRUPT_TABLE` when it was mapped
from a table, and cancellation is `CANCELLED`. The kinds `BackendKind`, `Precision` and
`ComputeError` are shared by every task of the crate.

**CD-7 Device layer.** The CUDA device layer changes additively: kernel modules compiled with
their own NVRTC options (cached by source and options), page-locked staging memory that is not
write-combined, a stream per session and a memory query. Earlier items keep their signatures; a
compile test of the crate pins them.

**CD-8 Residency and incremental propagation.** A GPU session keeps inputs and values on the
device across propagations and reads back all, none, listed or changed rows. Incremental
propagation is a forward cone with bitwise change detection, equal to a full propagation; the
CPU backend gives the same results in place. Multi-corner batching and Monte Carlo scenarios
(corners generalised to scenarios with arc-scale vectors) are where a GPU can pay off end to end;
they are not planned here.

## Consequences

- The legacy engine kernels and their launches leave the timing engine when it lowers onto the
  task; until then they use the device layer through `GpuTask` and `dispatch`, which stay.
- A GPU backend is checked for consistency with the CPU on random problems with tag maps and
  three-axis tables (bit for bit in `f64`, within the bound in `f32`), on every change to this
  crate, on a machine with a GPU.
- The wire records of the task are the hook of resistance-capacitance wire models; they are zero
  (ideal wires) until such models exist.

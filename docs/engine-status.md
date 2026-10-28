# RustSys, RustSTA and RustSpice development

Updated **2026-10-28**. This page describes the engine revisions integrated by
RustEDA. The independent repositories' `main` branches can lag their integration
branches; use a recursive RustEDA checkout for the exact versions documented here.
Version labels describe source previews, not packages published to crates.io.

## RustSys: synthesis and checked mapping

The current integration covers Verilog/SystemVerilog synthesis, BLIF/AIGER and
mapped-netlist inputs, bounded AIG optimization and standard-cell mapping.
Recorded runs retain commands, input hashes, netlists and reports. Workspace
optimization reads revision-bound timing paths; shared models and formats come
from RustEDA Common.

The retained benchmark covers **Ibex, PicoRV32, SERV, VexRiscv and OpenC910** on
Sky130 HD, GF180 7T and IHP SG13G2/EZ130 8T. All **30 distinct tool/design/library
netlists** pass arbitrary-state RTL equivalence without added reset or input
assumptions. The measurements include workspace initialization, import,
synthesis, mmap and Verilog export; proof, timing and physical design are outside
that timer. They establish synthesis performance, not RTL-to-GDS speedup.

[RustSys progress and source evidence](../modules/RustSys/docs/development-status.md) ·
[Benchmark protocol and results](../modules/RustSys/docs/guide/benchmarks.md)

## RustSTA: extracted timing in 0.4.3

The current integration supports multi-corner NLDM timing, setup/hold and other
supported endpoint checks, ideal or explicitly propagated primary clocks, and
bounded plain/gzip SPEF import. Extracted timing uses effective capacitance and
driver waveforms. Version 0.4.3 extends the RC model to resistor loops and
disconnected components, with explicit topology diagnostics and memory limits.

The 0.4.2 comparison reports a **0.97 ps maximum endpoint difference** against
OpenSTA across 32 design/library/extraction cases (96 corner comparisons).
The subsequent 0.4.3 cases that previously rejected non-tree extractions report
0.62 and 0.67 ps endpoint differences. Tree-only results remain byte-identical
after version normalization. These are scoped historical qualification results,
not a fresh whole-chip signoff claim.

Full-versus-incremental and CPU-versus-CUDA comparisons accompany the extracted
tests. Independent Ngspice checks exercise the RC equations and waveform model.
The standalone checker returns `undecided` for extracted RC; a full recomputation
checks engine consistency, not independent numerical correctness.

Three reported slow-corner Sky130 minimum-pulse-width cases retain **1.28–1.86 ps**
differences from OpenSTA. Generated clocks, CPPR, CCS and crosstalk remain outside
the supported integration scope. Read corner, clock model, wire model and
unconstrained endpoints alongside every timing result.

[RustSTA progress and source evidence](../modules/RustSTA/docs/development-status.md) ·
[Supported timing scope](../modules/RustSTA/docs/reference/scope.md)

## RustSpice: adaptive transient, device physics and GPU batched DC

The integrated `RustSpice` revision includes divided-difference LTE adaptive
transient step control with `BE -> BE -> BDF-2 -> Trapezoidal` restarts at
waveform breakpoints, a single short backward-Euler startup step at fast slope
corners, quadratic Newton prediction and contraction-rate acceptance, strict
SPICE netlist parsing (`.param`, `.func`, `.include`/`.lib`, `.temp`, `.ic`/`uic`),
extended diode (`RS`, `BV`/`IBV`, `IKF`, `ISR`/`NR`, `TT`, graded `CJO`) and
level-1 MOSFET (`UO`/`TOX`, `NSUB`/`GAMMA`/`PHI`, `LD`, `RD`/`RS`, bulk junctions,
Meyer capacitances) models, linear `E`/`F`/`H` and `POLY(1)` `E`/`G` controlled
sources, mutual inductance `K`, semiconductor `.model R`/`C`, and a single-launch
FP64 `cuda-oxide` batched DC operating-point kernel for diode and level-1 MOSFET
circuits.

In the 951-case static differential suite against `ngspice-47`, `RustSpice`
records **857 passes, 0 failures, 77 informational cases, 1 timing-only case and
16 documented scope gaps** (4 MOS `LEVEL>=2` decks, 11 unsupported BJT/JFET/MESFET
or behavioural device decks, and 1 `.if` directive deck). The GPU batched DC
kernel is verified against its deterministic `solve_cpu_ref` twin and independent
KCL residuals (`<= 5.89e-15` relative difference across 1,000 corners).

[RustSpice overview](../modules/RustSpice/docs/index.md) ·
[Benchmark report](../modules/RustSpice/docs/benchmark_report.md) ·
[GPU batched DC report](../modules/RustSpice/docs/gpu_batch_report.md)

## Current IHP/SRAM work

The active development track preserves real SRAM instances through synthesis
using validated macro interfaces and bounded logic cuts. Timing work covers
mixed standard-cell/SRAM libraries, global early/late derates and driving-cell
waveforms for extracted and propagated-clock paths. These changes live in
separate experimental checkouts and are **not in the module revisions pinned by
this documentation update**.

Component proofs and timing probes do not establish full-chip DRC, LVS, antenna,
extraction or setup/hold closure. The IHP/EZ130 Ibex physical flow remains
unverified as a complete result. [Qualification boundary](verification.md)

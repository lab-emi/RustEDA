# RustSys and RustSTA development

Updated **2026-10-30**. This page describes the engine revisions integrated by
RustEDA. The independent repositories' `main` branches can lag their integration
branches; use a recursive RustEDA checkout for the exact versions documented here.
Version labels describe source previews, not packages published to crates.io.

## RustSys: synthesis and checked mapping

The current integration covers Verilog/SystemVerilog synthesis, BLIF/AIGER and
mapped-netlist inputs, bounded AIG optimization and standard-cell mapping.
Recorded runs retain commands, input hashes, netlists and reports. Workspace
optimization reads revision-bound timing paths; shared models and formats come
from RustEDA Common.

In the [Ibex SG13G2/EZ130 campaign](benchmarks.md), RustSys synthesizes Ibex with two real SRAM macros. The flow's synthesis and final equivalence gates were proven in run16 (2026-10-30). Synthesis-only checks do not establish physical closure.

[RustSys capabilities and source evidence](../modules/RustSys/docs/development-status.md)



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

## Current IHP/SRAM work

Ibex with two real IHP SRAMs completes the native RTL-to-GDS flow on SG13G2 /
EZ130. Fresh public run16 (2026-10-30) passed all 16 independent gates: foundry
main and antenna DRC, full-device LVS, synthesis and final equivalence,
vendor-model simulation and TT/SS extracted timing. On identical SPEF, RustSTA
and OpenSTA agreed within 0.806 ps against the 1 ps gate. The matched LibreLane
run did not complete (one main-DRC violation), so no comparative ratio exists.
[Benchmark](benchmarks.md)

SRAM preservation in synthesis and the mixed standard-cell/SRAM timing work
live in separate experimental checkouts. They are **not in the RustSys and
RustSTA revisions pinned by this documentation update**. Run16 used executables
built from those checkouts; the [benchmark receipt](benchmarks/ibex-sg13g2-ez130.json)
lists them by SHA-256. One completed design does not qualify other designs or
SRAM configurations. [Qualification boundary](verification.md)

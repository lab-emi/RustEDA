<p align="center">
  <img src="docs/assets/rusteda-wordmark.svg" alt="RustEDA — From RTL to verified silicon" width="360">
</p>

<h1 align="center">Infrastructure for chip-design self-improvement</h1>

<p align="center">Shorten the work between inference steps. Design, measure, and improve.</p>

<p align="center">
  <a href="https://lab-emi.github.io/RustEDA/">Documentation</a> ·
  <a href="#rustspice--ngspice-47-klu-circuit-simulation-benchmarks">Benchmarks</a> ·
  <a href="#start-building">Get started</a> ·
  <a href="LICENSE">Apache-2.0</a>
</p>

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/rusteda-banner.svg">
  <img src="docs/assets/rusteda-banner.gif" alt="The RustEDA chip-design feedback loop: RTL, GDS, measurement, and better RTL" width="1200">
</picture>

RustEDA is Rust infrastructure for **chip-design RSI — recursive self-improvement**:
an agent proposes a design change, tools implement and check it, and measured
results guide the next change. Its purpose is to reduce the synthesis, timing,
physical-design and orchestration delays **outside the LLM inference loop**.
RSI here means iterative design optimization; it is not a claim of autonomous
model self-improvement.

Fast inference is only part of a fast iteration. RustEDA pairs native engines
with revisioned disk workspaces, machine-readable results and explicit verification
so agents and engineers can act on the same design state.

## RustSys, RustSTA and RustSpice progress

Updated **2026-10-28** for the engine revisions integrated by RustEDA.

| Engine | Current integration | Verification and limits |
| --- | --- | --- |
| **RustSys** | Bounded synthesis and technology mapping; five RISC-V designs across Sky130 HD, GF180 7T and IHP/EZ130 8T; revision-aware timing optimization and shared RustEDA foundation | All 30 benchmark netlists pass arbitrary-state RTL equivalence. Synthesis timings exclude proof, STA and physical implementation. |
| **RustSTA 0.4.3** | Multi-corner timing, propagated primary clocks, bounded SPEF import, effective-capacitance waveforms and extracted RC networks with resistor loops | Full/incremental and CPU/CUDA consistency checks, dual-PDK OpenSTA comparisons and independent circuit checks. Extracted RC is outside the independent checker's scope; reported pulse-width residuals remain explicit. |
| **RustSpice** | Differentiable SPICE simulation (`.op`, `.dc`, adaptive LTE `.tran`, discrete adjoint), extended diode / level-1 MOSFET / controlled-source / coupled-inductor models, and `cuda-oxide` FP64 batched DC operating points | 951-case differential suite against `ngspice-47` (`857` pass, `0` failing, `16` documented scope gaps: MOS `LEVEL>=2`, BJT/JFET/MESFET/behavioural cards, `.if`); GPU batch kernel verified against `solve_cpu_ref` and KCL residuals. |

Real-SRAM preservation in synthesis and mixed-library/derated timing are under
development for the IHP Ibex flow. They are not part of the pinned released
capability set, and full-chip physical closure remains unverified. The standalone
module `main` branches can lag the integration branches used here.

[Progress, evidence and current limits](docs/engine-status.md) ·
[RustSys development](https://lab-emi.github.io/RustEDA/modules/rustsys/development-status/) ·
[RustSTA development](https://lab-emi.github.io/RustEDA/modules/ruststa/development-status/)

## Optimize the whole loop

```text
          propose                         implement                        measure
  Agent ───────────► RTL / Netlist ───────────────────────► GDS / Waveforms ────────► Feedback
    ▲                RustSys · RustSTA · RustROAD · RustSpice                       │
    └─────────────────────────────── next iteration ────────────────────────────────┘
                               RustEDA flow + workbench
```

If tools account for fraction `f` of an iteration, and their aggregate speedup
is `s`, Amdahl's law gives:

$$S_{\mathrm{iteration}} = \frac{1}{(1-f) + f/s}.$$

**Illustration:** when tools consume 80% of an iteration, making that entire
tool portion 10× faster yields **3.57×** overall speedup. This is a model,
not a measured RustEDA result. Faster synthesis alone cannot establish faster
RTL-to-GDS or faster agent iterations.

## RustSpice × Ngspice-47 circuit simulation benchmarks

Host: 13th Gen Intel(R) Core(TM) i9-13900HX (32 hardware threads).
Reference baseline: `ngspice-47` (SPARSE 1.3 by default, KLU with `.options klu`; each row uses the faster of the two).

| Benchmark | Circuit scale | `ngspice-47`<br>end to end ↓ | `RustSpice`<br>end to end ↓ | Speedup ↑ | Max error: `RustSpice` / `ngspice` default ↓ |
| :--- | :--- | ---: | ---: | ---: | ---: |
| **1. Resistor ladder (DC OP)** | 10,000 resistors, 5,002 MNA unknowns (DC operating point) | 25.98 ms | 11.13 ms | **2.33×** | `1.91e-13` / `1.93e-13` V |
| **2. RC line (transient)** | 4,000 R/C elements, `.tran 10p 5n` (500 steps) | 114 ms | 31.45 ms | **3.62×** (8.2× lower error than ngspice default) | `2.01e-4` / `1.65e-3` of swing (vs tight ngspice) |
| **3. Diode array (DC sweep)** | 64 diodes + 128 resistors, 201-point DC sweep | 6.54 ms | 3.35 ms | **1.95×** | `5.89e-11` / `5.44e-5` V |
| **4. CMOS inverter chain (transient)** | 200 MOSFETs + 100 capacitors, `.tran 5p 2n` (400 steps) | 35.60 ms | 32.12 ms | **1.11×** (5.2× lower error than ngspice default) | `7.40e-2` / `3.86e-1` of swing (vs tight ngspice) |
| **5. Sensitivity (adjoint vs. ngspice `.sens`)** | 200 resistor gradients (one adjoint solve) | 12.55 ms | 1.30 ms | **9.66×** vs `.sens` (**306×** vs FD loop) | `9.33e-15` / `1.00e-6` (normalised) |
| **6. PVT batch (128 corners)** | 128 PVT corners of a 32-diode nonlinear net (Rayon) | 4.30 ms | 3.46 ms | **1.24×** (parallel workload; **2.57×** via `.dc` sweep) | `2.47e-11` / `1.63e-5` V |

Speedup = `ngspice-47` / `RustSpice`, median of 9 interleaved repetitions against the faster of ngspice's SPARSE 1.3 and KLU solvers; both tools run as subprocesses with rawfile output on tmpfs, the single-core benchmarks pinned to one idle core (the parallel batch, benchmark 6, is not pinned). On NVIDIA CUDA (`RTX 4090 Laptop GPU`), `RustSpice`'s single-launch FP64 `cuda-oxide` batched DC kernel solves `10,000`–`1,000,000` corners **7.0×–35.0×** faster end-to-end than the 32-thread Rayon CPU batch solver on the 32-diode cascade (`128.4 ms` vs `4.50 s` at `10^6` corners) and **10.7×–20.9×** faster on the 8-stage level-1 CMOS inverter chain (`92.7 ms` vs `1.94 s` at `10^5` corners). The previously published 5.3×, 7.6×, 3.0×, 2.6×, 6,285× and 321× came from a harness with known defects (1 ms-quantised solver times, a cold process compared with a warm in-process call, hard-coded zero errors, single runs multiplied by 128 or 400, a transient compared at t = 0 only) and are withdrawn; the report lists each one with its replacement.

[RustSpice benchmark report](modules/RustSpice/docs/benchmark_report.md) ·
[GPU batched DC report](modules/RustSpice/docs/gpu_batch_report.md) ·
[RustSpice architecture](modules/RustSpice/docs/architecture.md)

## One product, five engine modules

| Module | Role | Documentation |
| --- | --- | --- |
| [RustSys](https://github.com/lab-emi/RustSys) | RTL synthesis, technology mapping and equivalence checks | [Synthesis](https://lab-emi.github.io/RustEDA/modules/rustsys/) |
| [RustSTA](https://github.com/lab-emi/RustSTA) | Timing, constraints, parasitics and incremental analysis | [Timing](https://lab-emi.github.io/RustEDA/modules/ruststa/) |
| [RustROAD](https://github.com/lab-emi/RustROAD) | Placement, clock trees, routing, extraction and GDS | [Physical design](https://lab-emi.github.io/RustEDA/modules/rustroad/) |
| [RustSpice](https://github.com/lab-emi/RustSpice) | Differentiable SPICE circuit simulation and adjoint sensitivity | [Circuit simulation](https://lab-emi.github.io/RustEDA/modules/rustspice/) |
| **RustEDA flow** (built in) | Bounded processes, checkpoints, recovery and flow execution | [Flows](https://lab-emi.github.io/RustEDA/flows/) |
| **RustEDA workbench** (built in) | Hierarchy, critical paths, logic cones and shared selections | [Workbench](https://lab-emi.github.io/RustEDA/workbench/) |
| [RustEDA Common](https://github.com/lab-emi/RustEDA-Common) | Shared models, formats, disk workspaces and compute kernels | [Foundation](https://lab-emi.github.io/RustEDA/modules/rusteda-common/) |

RustEDA owns flow execution and the workbench directly in `apps/`, with one
application workspace and lockfile. The five engine modules retain independent
Git histories and exact pins. There is no root Cargo workspace.

## PDKs and standard-cell libraries

| Process | Standard-cell library | Verified scope |
| --- | --- | --- |
| SkyWater SKY130A | `sky130_fd_sc_hd`, `hdll`, `hs`, `ms`, `ls`, `lp`, `hvl` | HD: synthesis, timing and small-block physical flow qualification. Other libraries: installed; integrated physical flow unverified. |
| GlobalFoundries GF180MCU | `gf180mcu_fd_sc_mcu7t5v0`, `gf180mcu_fd_sc_mcu9t5v0` | 7T: synthesis, timing and small-block physical flow qualification. The 9T library is also a library-coverage baseline, not the qualified physical-flow default. |
| IHP SG13G2 | `sg13g2_stdcell`, [EZ130 8T v1.1](https://iip.ethz.ch/ez-library/ihp130-8t.html) | EZ130: synthesis benchmark and functional equivalence at TT; integrated physical flow unverified. IHP standard cells: installed; integrated flow unverified. |

Select an installed Liberty corner for direct RustSys commands with
`./rusteda library NAME --corner tt` (also `ff` or `ss` where catalogued).
The selector checks the exact library SHA-256; `./rusteda library --help` lists
the names. Use `--root DIR` or the documented environment override for an
installation in a different location. For example:

```sh
EZ130_LIB=$(./rusteda library ez130_8t --corner tt) || exit 1
rustsys synth --top counter --lib "$EZ130_LIB" \
  --out counter_ez130.v path/to/counter.sv
```

EZ130 is a cell library for the IHP process, not a PDK on its own. Its v1.1
tie-cell Liberty driver types conflict with the supplied Verilog models; the
benchmark's equivalence scope and functional supplement are documented in the
[EZ130 evidence](modules/RustSys/verification/benchmarks/open-hardware/results/2026-10-23-ez130/README.md).
For SRAM, `./rusteda sram sky130 --corner tt` locates a representative macro's views
and reports their hashes; use `gf180` or `ihp` to select another process, and
`--macro NAME` selects another installed
macro. SKY130 has SRAM22 and OpenRAM options, while GF180 and IHP ship prebuilt
foundry macros. Some SRAM22 macro configurations were tested in silicon at
1.8 V and 25 MHz, as documented by the [macro provider](https://github.com/ucb-substrate/sram22_sky130_macros).
The integrated `./rusteda flow --pdk` currently accepts `sky130` and `gf180`;
SRAM macro integration and IHP physical flows remain unverified.
See [PDK and SRAM setup](docs/pdks.md) for paths, provenance and compiler limits.

## Start building

Git, Python 3.11+, Rust 1.97.1 and a native Linux build environment are the
development baseline. Python drives the development harness; Rust core crates
do not depend on it. Studio has additional native GUI dependencies.

```sh
git clone --recurse-submodules https://github.com/lab-emi/RustEDA.git
cd RustEDA
./dev status
./dev doctor
./dev build RustEDA --no-default-features --memory-mib 4096
./rusteda --help
./dev build RustSys RustSTA RustSpice --release
```

The `./dev` entrypoint serializes builds, limits memory and worker concurrency,
and records resource use. Use `./dev --help` and the
[development guide](docs/development.md) for bounded checks and module selection.

## Run a verified design loop

Supply the documented, pinned tools and PDKs on Linux, with Podman and a user
systemd manager. Setup verifies existing inputs; it does not download tools or PDKs.

```sh
./rusteda tools configure
./rusteda tools verify
./rusteda flow --workspace .work/counter init --base-rev empty --design counter --pdk sky130
./rusteda flow --workspace .work/counter run --base-rev rev0
./rusteda flow --workspace .work/counter report --summary-only
```

Current physical qualification covers **eight small blocks × two PDKs**:
15 ordinary passes and one explicitly declared minimum-pulse-width deviation.
Cache reuse requires verified input and artifact identities. Interrupted runs
require explicit recovery.
See [flow operation](docs/flows.md) and [scope and evidence](docs/verification.md).

## License and provenance

Apache-2.0. Each module retains its own LICENSE and NOTICE. Benchmark designs,
PDKs, reference executables and rule decks retain their respective licenses;
those assets are not relicensed by this repository. Reference-tool comparisons
use external executables and documented file interfaces.

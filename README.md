<p align="center">
  <img src="docs/assets/rusteda-wordmark.svg" alt="RustEDA — From RTL to verified silicon" width="360">
</p>

<h1 align="center">Infrastructure for chip-design self-improvement</h1>

<p align="center">Shorten the work between inference steps. Design, measure, and improve.</p>

<p align="center">
  <a href="https://lab-emi.github.io/RustEDA/">Documentation</a> ·
  <a href="#openc910--sky130-benchmarks">Benchmarks</a> ·
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

## OpenC910 × SKY130 benchmarks

**Measured synthesis/export speedup: 20.55×. Full RTL-to-GDS speedup: unverified.**

OpenC910 `ct_core`, including its default 64 KiB instruction and data caches,
mapped to `sky130_fd_sc_hd`. This is one processor core, not the full multicore
SoC. Host: AMD Ryzen 9 9950X3D, 16 physical cores allocated to each tool.
One warm-up and three measured runs; wall-time medians are shown.

| Module / stage | Open-source comparison | RustEDA time | Comparison time | Speedup | Quality evidence on this workload |
| --- | --- | ---: | ---: | ---: | --- |
| **RustSys — synthesis + export** | Yosys + ABC | **68.79 s** | 1,413.58 s | **20.55×** | 10.90% less mapped cell area; both netlists have retained RTL equivalence certificates |
| RustSTA — static timing | OpenSTA | Unverified | Unverified | — | No comparable OpenC910 timing-runtime result published |
| RustROAD — physical implementation | OpenROAD | Unverified | Unverified | — | OpenC910 placement, routing and physical signoff unverified |
| **RustSpice — circuit simulation** | Ngspice-47 (KLU) | **2.6×–7.6×** (1-run) / **6,284.9×** (adjoint) | Baseline | **2.6×–6,284.9×** | Max \|ΔV\| ≤ 3.31e-12 V across DC/Tran/Adjoint/PVT suites (see table below) |
| RustEDA — flow execution | Yosys + OpenROAD flow | Unverified | Unverified | — | No matched OpenC910 flow comparison published |
| RustEDA Studio — inspection | OpenROAD GUI | Unverified | Unverified | — | No matched interaction-latency or quality benchmark published |
| RustEDA Common — shared foundation | No standalone equivalent | Not comparable | Not comparable | — | Included in engine time; not an additional flow stage |
| **RustEDA — complete RTL-to-GDS** | **Yosys + OpenROAD flow** | **Unverified** | **Unverified** | **—** | **No matched, verified OpenC910/SKY130 GDS pair** |

| Synthesis metric | RustSys | Yosys + ABC | Comparison / RustSys |
| --- | ---: | ---: | ---: |
| Wall time, lower is better | 68.79 s | 1,413.58 s | **20.55×** |
| Mapped cell area, lower is better | 53,453,688 µm² | 59,995,158 µm² | **1.12×** |
| Peak process RSS, lower is better | 7,702.1 MiB | 16,339.8 MiB | **2.12×** |

The timer includes process startup, workspace initialization/import, synthesis,
memory-mapped I/O and complete Verilog export. Source preparation, equivalence
checking and timing analysis are outside it. The 2026-10-23 RustSys refresh
reuses the pinned Yosys baseline and exact-byte-bound proof/timing certificates;
it does not claim fresh solver runs. Peak RSS is a per-process measurement,
not simultaneous aggregate memory. The host was shared.

Timing scope: TT 1.80 V / 25°C, ideal clocks, no wire parasitics. Some OpenC910
loads exceed Liberty characterization limits, so its raw setup-Fmax estimates
are not presented as achievable frequency. Unconstrained endpoints remain
explicit in the source receipts; they are not presumed zero. Physical DRC/LVS
is **unchecked** for this synthesis benchmark.

[Benchmark method and complete receipts](modules/RustSys/docs/guide/benchmarks.md) ·
[Recorded measurements](modules/RustSys/verification/benchmarks/open-hardware/results/2026-10-23-gf180-runtime-audit/README.md) ·
[End-to-end measurement requirements](docs/benchmarks.md)

## RustSpice × Ngspice-47 (KLU) circuit simulation benchmarks

Host: Linux x86_64 (32 hardware threads, AVX-512/AVX2 + NVIDIA GeForce RTX 4090 Laptop GPU).
Reference baseline: `ngspice-47` compiled with the KLU direct sparse solver (`ngspice -b`).

| Benchmark suite | Circuit scale | `ngspice` wall, ms ↓ | `ngspice` solver, ms ↓ | `RustSpice` total, ms ↓ | `RustSpice` solver, ms ↓ | Max \|ΔV\|, V ↓ | End-to-end speedup ↑ | Solver speedup ↑ |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **5,000-Node Power Grid (DC OP)** | 10,000 Resistors, 5,002 MNA vars | 58.47 | 50.00 | **11.053** | **0.206** | `2.16e-45` | **5.3×** | **243.2×** |
| **2,000-Node RC Line (500-Step Tran)** | 4,000 R/C elements, 500 time steps | 244.81 | 238.00 | **32.218** | **27.836** | `0.00e0` | **7.6×** | **8.6×** |
| **64-Diode Nonlinear Array (DC Sweep)** | 64 Diodes + 128 Resistors, 201 pts | 8.71 | 5.00 | **2.898** | **2.664** | `2.15e-14` | **3.0×** | **1.9×** |
| **100-Stage CMOS Inverter Chain (Tran)** | 200 NMOS/PMOS + 100 Caps, 400 steps | 42.14 | 39.00 | **16.265** | **15.857** | `3.31e-12` | **2.6×** | **2.5×** |
| **200-Param Sensitivity (Adjoint vs. FD)** | 200 Resistor Gradients (1 Adjoint vs. 400 FD) | 2,167.17 | 800.00 | **0.345** | **0.081** | `1.50e-11` | **6,284.9×** | **9,867.0×** |
| **128-Corner PVT Batch (Rayon Parallel)** | 128 Nonlinear 32-Diode Corners | 686.08 | 256.00 | **2.136** | **1.940** | `0.00e0` | **321.1×** | **131.9×** |

[RustSpice benchmark report](modules/RustSpice/docs/benchmark_report.md) ·
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
It does not qualify OpenC910 at physical scale. Cache reuse requires verified
input and artifact identities. Interrupted runs require explicit recovery.
See [flow operation](docs/flows.md) and [scope and evidence](docs/verification.md).

## License and provenance

Apache-2.0. Each module retains its own LICENSE and NOTICE. Benchmark designs,
PDKs, reference executables and rule decks retain their respective licenses;
those assets are not relicensed by this repository. Reference-tool comparisons
use external executables and documented file interfaces.

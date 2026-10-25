<p align="center">
  <img src="docs/assets/rusteda-wordmark.svg" alt="RustEDA" width="440">
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
  <img src="docs/assets/rusteda-banner.gif" alt="The RustEDA chip-design feedback loop: RTL, GDS, measurement, and better RTL" width="1200" height="360">
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
          propose                   implement                 measure
  Agent ───────────► RTL ─────────────────────────► GDS ─────────────────► Feedback
    ▲                RustSys · RustSTA · RustROAD                         │
    └────────────────────────── next iteration ───────────────────────────┘
                         RustLane + RustEDA Studio
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
| RustLane — flow orchestration | LibreLane | Unverified | Unverified | — | No matched OpenC910 flow comparison published |
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

## One suite, six modules

| Module | Role | Documentation |
| --- | --- | --- |
| [RustSys](https://github.com/lab-emi/RustSys) | RTL synthesis, technology mapping and equivalence checks | [Synthesis](https://lab-emi.github.io/RustEDA/modules/rustsys/) |
| [RustSTA](https://github.com/lab-emi/RustSTA) | Timing, constraints, parasitics and incremental analysis | [Timing](https://lab-emi.github.io/RustEDA/modules/ruststa/) |
| [RustROAD](https://github.com/lab-emi/RustROAD) | Placement, clock trees, routing, extraction and GDS | [Physical design](https://lab-emi.github.io/RustEDA/modules/rustroad/) |
| [RustLane](https://github.com/lab-emi/RustLane) | Bounded processes, checkpoints, recovery and flow execution | [Flows](https://lab-emi.github.io/RustEDA/modules/rustlane/) |
| [RustEDA Studio](https://github.com/lab-emi/RustEDA-Studio) | Hierarchy, critical paths, logic cones and shared selections | [Workbench](https://lab-emi.github.io/RustEDA/modules/rusteda-studio/) |
| [RustEDA Common](https://github.com/lab-emi/RustEDA-Common) | Shared models, formats, disk workspaces and compute kernels | [Foundation](https://lab-emi.github.io/RustEDA/modules/rusteda-common/) |

The repository pins compatible module revisions. Each module keeps its own Git
history and Cargo workspace; there is no root Cargo workspace.

## Start building

Git, Python 3.11+, Rust 1.97.1 and a native Linux build environment are the
development baseline. Python drives the development harness; Rust core crates
do not depend on it. Studio has additional native GUI dependencies.

```sh
git clone --recurse-submodules https://github.com/lab-emi/RustEDA.git
cd RustEDA
./dev status
./dev doctor
./dev build RustSys RustSTA --release
```

The `./dev` entrypoint serializes builds, limits memory and worker concurrency,
and records resource use. Use `./dev --help` and the
[development guide](docs/development.md) for bounded checks and module selection.

## Run a verified design loop

Supply the documented, pinned tools and PDKs on Linux, with Podman and a user
systemd manager. Setup verifies existing inputs; it does not download tools or PDKs.

```sh
./dev tools configure
./dev tools verify
./dev flow --workspace .work/counter init --base-rev empty --design counter --pdk sky130
./dev flow --workspace .work/counter run --base-rev rev0
./dev flow --workspace .work/counter report --summary-only
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

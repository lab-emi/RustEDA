<p align="center">
  <img src="docs/assets/rusteda-wordmark.svg" alt="RustEDA — From RTL to verified silicon" width="360">
</p>

<h1 align="center">Infrastructure for chip-design self-improvement</h1>

<p align="center">Shorten the work between inference steps. Design, measure, and improve.</p>

<p align="center">
  <a href="https://lab-emi.github.io/RustEDA/">Documentation</a> ·
  <a href="docs/benchmarks.md">Benchmarks</a> ·
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

## Ibex SG13G2 / EZ130 status

RustEDA completes Ibex RTL-to-GDS on IHP SG13G2 with EZ130 standard cells and **two real SRAM macros**. One fresh public run (2026-10-30) passed all 16 independent gates in 2,936.6 s with a 2.16 GiB peak process RSS. The gates are foundry main and antenna DRC, full-device LVS, synthesis and final equivalence, vendor-model simulation, and extracted TT/SS setup, hold and DRV.

The matched LibreLane 3.0.17 run closed timing but failed foundry main DRC (one M2.b violation). Because it did not complete, **no end-to-end speedup or memory ratio is published**. RustEDA's layout is larger: 233,909 µm² of standard cells against 222,522 µm², and 1,678,599 µm of routed signal wire against 1,077,762 µm.

[Latest Ibex campaign and completion gates](docs/benchmarks.md) ·
[Engine capabilities and limits](docs/engine-status.md)

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

## One benchmark campaign

The [Ibex SG13G2/EZ130 campaign](docs/benchmarks.md) is the single public performance source for RustEDA and its modules. Module pages link to its [machine-readable receipt](docs/benchmarks/ibex-sg13g2-ez130.json). RustSpice is not timed in the RTL-to-GDS main flow and has no Ibex speedup claim.

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
| IHP SG13G2 | `sg13g2_stdcell`, [EZ130 8T v1.1](https://iip.ethz.ch/ez-library/ihp130-8t.html) | EZ130: Ibex with two real SRAMs completes native RTL-to-GDS with all 16 gates (one public run). Other EZ130 designs: unverified. IHP standard cells: installed; integrated flow unverified. |

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
tie-cell Liberty driver types conflict with the supplied Verilog models; its
equivalence scope and functional supplement are documented in the
[EZ130 functional model scope](modules/RustSys/docs/verification/pdk.md#additional-ihp-sg13g2-benchmark-library).
For SRAM, `./rusteda sram sky130 --corner tt` locates a representative macro's views
and reports their hashes; use `gf180` or `ihp` to select another process, and
`--macro NAME` selects another installed
macro. SKY130 has SRAM22 and OpenRAM options, while GF180 and IHP ship prebuilt
foundry macros. Some SRAM22 macro configurations were tested in silicon at
1.8 V and 25 MHz, as documented by the [macro provider](https://github.com/ucb-substrate/sram22_sky130_macros).
The integrated `./rusteda flow --pdk` accepts `sky130` and `gf180`. The IHP
flow takes an explicit `ihp_sg13g2_ez130` worker configuration
(`./rusteda flow init --config FILE`, prepared by
[`integration/ibex_sg13g2_ez130`](integration/ibex_sg13g2_ez130/README.md)).
It is qualified only for the Ibex benchmark; SRAM integration for other designs
remains unverified.
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

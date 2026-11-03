# RustEDA tool suite

| Tool · repository | Function |
| --- | --- |
| [RustEDA CLI](https://github.com/lab-emi/RustEDA) | Unified commands for design workspaces, libraries and engine tools. |
| [RustEDA flow](https://github.com/lab-emi/RustEDA) | RTL-to-GDS flow execution, checkpoints and recovery. |
| [RustEDA workbench (Studio)](https://github.com/lab-emi/RustEDA) | Design hierarchy, critical paths, logic cones and shared selections. |
| [RustSys](https://github.com/lab-emi/RustSys) | RTL synthesis, technology mapping and equivalence checks. |
| [RustSTA](https://github.com/lab-emi/RustSTA) | Static timing, constraints, parasitics and incremental analysis. |
| [RustROAD](https://github.com/lab-emi/RustROAD) | Placement, clock trees, routing, extraction and GDS. |
| [RustSpice](https://github.com/lab-emi/RustSpice) | Differentiable SPICE circuit simulation and adjoint sensitivity. |
| [RustEDA Common](https://github.com/lab-emi/RustEDA-Common) | Shared design models, format parsers, disk workspaces and compute kernels. |

CLI, flow and workbench live in this repository under `apps/`. The five engine
modules have independent repositories and exact source pins.

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

## Latest development · 2026-11-02

On a 4-vCPU Xeon cloud host, the repository's native recipe and LibreLane 3.0.17
built Ibex on IHP SG13G2 with EZ130 cells and **two real SRAM macros** from the
same prepared inputs, and the same independent scripts checked both layouts (one
timed sample per flow). **Neither flow passes every gate.** RustEDA's layout
passes foundry main and antenna DRC, LVS, TT/SS timing with zero violations,
simulation, both equivalence checks and density, but fails the 1 ps
RustSTA/OpenSTA parity gate: on identical SPEF the two engines differ by up to
1.405 ps. Separately, the run is `unqualified` because the recipe was composed
without component-qualification evidence, which is not published. LibreLane
fails the timing gate on max-capacitance and fanout checks (setup and hold met);
its equivalence is `unverified` because the checker cannot name-match 433
renamed registers, a checker limitation rather than evidence of non-equivalence.

Each flow had all 4 vCPUs, with an 8 GiB memory cap for RustEDA (its cgroup
peak reached the cap with page cache; largest process 1.03 GiB) and 10 GiB for
LibreLane. Both flows used nixpkgs tool builds, so LibreLane ran on OpenROAD 26Q3
and Yosys 0.70 rather than its pinned OpenROAD `dcf36133` and Yosys 0.62, and
the EZ130 tech LEF and cell GDS were reconstructed from public sources.

RustEDA: 233,909.2 µm² standard-cell area, 1,679,076.6 µm routed signal wire,
301,099 vias. Its 2,609.93 s end to end covers sv2v preparation, the producer
through its own extractions and RustSTA/OpenSTA signoff timing, where it stopped
at the parity gate after 114 of 119 manifest steps, and the shared DRC, antenna,
LVS, simulation, equivalence and density gates it did not reach. LibreLane:
197,913.7 µm², 969,571.6 µm, 122,194 vias. Its 3,286.87 s covers sv2v
preparation, the flow through `Checker.XOR` (including Magic LEF writing, the
KLayout XOR and an IR-drop report) and all eight shared gates. The two times
cover different work. The receipt's ratios are null and **no cross-tool speedup
or QoR ratio is published**.

The retained R20/R21 native runs (129/129 steps and all 16 gates in 472.2 s and
472.6 s on 16 logical CPUs of an i9-13900HX) remain on the benchmark page; times
from different hosts are not comparable. Their recipe inputs are not in the
repository, so this host ran the run16-class recipe.

[Published source versions](docs/development-snapshot.md) ·
[Current modules and remaining limits](docs/engine-status.md) ·
[Measurements, receipts and qualification scope](docs/benchmarks.md)

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

The [Ibex SG13G2/EZ130 benchmark page](docs/benchmarks.md) is the single public performance source for RustEDA and its modules; module pages link to it. It holds the 2026-11-02 host campaign, the retained R20/R21 native runs and the historical 2026-10-30 matched comparison, each with a machine-readable receipt ([host](docs/benchmarks/ibex-sg13g2-ez130-host-20261102.json) · [R20/R21](docs/benchmarks/ibex-sg13g2-ez130-development.json) · [historical](docs/benchmarks/ibex-sg13g2-ez130.json)).

The host campaign adds module head-to-heads. On identical inputs: RustSys against Yosys with ABC (LibreLane's synthesis script), RustSTA against OpenSTA, RustROAD extraction against OpenRCX and RustROAD GDS export against KLayout. RustROAD placement, CTS and routing are compared with OpenROAD's stages inside the two flows. RustSpice is compared with ngspice-45 on RC networks extracted from the final Ibex layout, not at transistor level: it does not support IHP's PSP103 transistor models. RustSpice is not timed in the RTL-to-GDS main flow.

## PDKs and standard-cell libraries

| Process | Standard-cell library | Verified scope |
| --- | --- | --- |
| SkyWater SKY130A | `sky130_fd_sc_hd`, `hdll`, `hs`, `ms`, `ls`, `lp`, `hvl` | HD: synthesis, timing and small-block physical flow qualification. Other libraries: installed; integrated physical flow unverified. |
| GlobalFoundries GF180MCU | `gf180mcu_fd_sc_mcu7t5v0`, `gf180mcu_fd_sc_mcu9t5v0` | 7T: synthesis, timing and small-block physical flow qualification. The 9T library is also a library-coverage baseline, not the qualified physical-flow default. |
| IHP SG13G2 | `sg13g2_stdcell`, [EZ130 8T v1.1](https://iip.ethz.ch/ez-library/ihp130-8t.html) | EZ130: Ibex with two real SRAMs completes native RTL-to-GDS with all 16 gates (retained runs on one host). A second-host reproduction with reconstructed EZ130 tech LEF and GDS passed every design gate but not the 1 ps RustSTA/OpenSTA parity gate; it is `unqualified`. Other EZ130 designs: unverified. IHP standard cells: installed; integrated flow unverified. |

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

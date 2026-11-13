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

## RustEDA vs LibreLane at a glance

Ibex RTL-to-GDS on IHP SG13G2 with EZ130 cells and two SRAM macros, 2026-11-05: RustEDA and LibreLane 3.0.17 (OpenROAD, Yosys, KLayout, Magic, Netgen) ran one after the other on the same 4-vCPU host from the same prepared inputs, and the same signoff scripts checked both layouts. ([receipt](docs/benchmarks/ibex-sg13g2-ez130-host-20261105.json))

**4.2× faster end to end** (974 s against 4,105 s with both flows' foundry
checks on the same schedule; 3.4× with every check run one after another), with a
**5.2 % smaller, 7.4 % shorter-wired layout, 3.0 % fewer vias and zero timing
violations** where LibreLane's layout has 112.

| Stage | RustEDA | LibreLane | RustEDA |
| --- | ---: | ---: | ---: |
| Synthesis | 3.48 s | 132.63 s | **38.1× faster** |
| Placement and clock tree | 28.00 s | 47.05 s | **1.7× faster** |
| Repair before detailed routing | 26.04 s | 52.36 s | **2.0× faster** |
| Routing, global and detailed | 302.24 s | 2,570.19 s | **8.5× faster** |
| Static timing in the flow | 35.42 s | 183.31 s | **5.2× faster** |
| Parasitic extraction | 13.08 s | 47.73 s | **3.6× faster** |
| GDS / DEF / netlist export | 2.09 s | 351.18 s | **168× faster** |
| Foundry DRC / antenna / LVS on each layout¹ | 403 / 198 / 147 s | 534 / 254 / 207 s | **1.3× / 1.3× / 1.4× faster** |
| Whole flow² | 888.90 s | 3,409.11 s | **3.8× faster** |
| **End to end** (flow + every signoff check) | **974.19 s** | **4,104.97 s** | **4.2× faster** |

| Quality of results | RustEDA | LibreLane | RustEDA |
| --- | ---: | ---: | ---: |
| Standard-cell area | 187,585.2 µm² | 197,913.7 µm² | **5.2 % smaller** |
| Routed signal wirelength | 897,357.3 µm | 969,571.6 µm | **7.4 % shorter** |
| Vias | 118,525 | 122,194 | **3.0 % fewer** |
| Capacitance / fanout violations, TT + SS | 0 | 112 | **timing-clean** |
| Setup worst slack, TT / SS | 49.64 / 49.44 ns | 48.60 / 47.84 ns | **+1.03 / +1.60 ns** |
| Synthesis and final equivalence | proven | unverified | **proven** |

| Module against its open-source counterpart | Speed | Peak memory | Quality |
| --- | --- | --- | --- |
| RustSys vs Yosys + ABC (LibreLane script) | **35.4× faster** (44.4× in direct mode) | **6.6× less** | 1.8 % less area, logic depth 36 vs 40, equivalence proven |
| RustSTA vs OpenSTA | **1.6× faster** at 2 threads, **2.0×** at 4 | 17.8 % / 6.3 % less | worst slacks within 0.164 ps |
| RustROAD routing vs OpenROAD (in the flow) | **8.5× faster** | — | 7.4 % less wire, 3.0 % fewer vias |
| RustROAD GDS export vs KLayout | **2.3× faster** | **2.0× less** | — |
| RustSpice vs ngspice-45 (extracted RC nets) | **4.3–6.0× faster** | **1.4–2.0× less** | closer to the reference in all 10 cases |

¹ Same scripts and decks on both layouts, interleaved runs at the same load (medians).
² RustEDA's flow time includes its own foundry DRC, antenna and LVS; LibreLane's does not.

LibreLane's layout fails the shared timing gate (112 maximum-capacitance and fanout violations at TT and SS) and its equivalence check does not complete, so the receipt keeps its own ratio fields null under its publication rule; the multiples here divide the recorded times. Stages are paired by phase, not as identical tasks. RustEDA is not faster everywhere: its repair before global routing takes 1.18× LibreLane's two pre-route resizer steps alone, OpenRCX runs 1.36× longer on RustEDA's DEF, and the latch-up check 1.11× longer on its layout. [All stages, gates and losses](docs/benchmarks.md)

## Latest development · 2026-11-05

On a 4-vCPU Xeon cloud host, the repository's current native recipe and engines
(superproject `397bfa6`) and LibreLane 3.0.17 built Ibex on IHP SG13G2 with
EZ130 cells and **two real SRAM macros** from the same prepared inputs, and the
same nine independent gate scripts checked both layouts (one timed sample per
flow). **RustEDA's producer completed every step, including the 1 ps
RustSTA/OpenSTA parity gate (largest difference 0.164 ps), and its layout passes
every shared gate that gives a pass/fail verdict:** foundry main and antenna DRC
with 0 violations, latch-up tap coverage, LVS, TT/SS timing with zero setup,
hold, slew, capacitance and fanout violations, simulation, synthesis and final
equivalence `proven`, and density. The OpenRCX extraction gate, which extracts
the SPEF for the timing gate, reports `extracted_not_qualified` on both layouts,
as on 2026-11-02.
The run is still `unqualified`, because the recipe was composed without the
unpublished component-qualification evidence. LibreLane again fails the timing
gate on max-capacitance and fanout checks (setup and hold met), and its
equivalence is `unverified` (a checker limitation with 433 renamed registers,
not evidence of non-equivalence).

RustEDA: 187,585.2 µm² standard-cell area, 897,357.3 µm routed signal wire,
118,525 vias, 974.19 s end to end (sv2v preparation, the producer with its own
extractions, timing signoff and foundry DRC, antenna DRC and LVS, and the shared
latch-up, simulation, equivalence and density gates). LibreLane: 197,913.7 µm²,
969,571.6 µm, 122,194 vias, 4,616.30 s (preparation, the flow through
`Checker.XOR` and all nine shared gates). Because LibreLane fails a gate, the
receipt keeps its own ratio fields null; the summary above divides these
recorded times. The benchmark page pairs every stage and gate: RustEDA is faster in
synthesis, placement and CTS, routing, in-flow static timing and export, and runs
its own foundry checks.
RustEDA still trails in its frontend STA and ECO stage against LibreLane's two resizer steps before global routing (1.18× in same-load runs: median 26.60 s against 22.48 s; 0.49× all four of its repair steps), and RustROAD's own extraction is level with OpenRCX only at 4 threads (1.69× slower at 2). On the shared gates, same-load reruns put the native layout ahead on foundry main DRC, antenna DRC, LVS, simulation and density, level on timing and behind on OpenRCX extraction (1.36×, consistent with more wire pieces in its route topology) and latch-up (1.11× with the checker of that campaign, whose time followed the notched n-well domains of the flip-flops synthesis chose; 0.78× since the shared checker was changed for both layouts on 2026-11-12, [latch-up checker change](docs/benchmarks.md#latch-up-checker-change-2026-11-12)); the equivalence gate has no valid comparison, because it does not complete on LibreLane's layout.

The host differs from 2026-11-02's, and every LibreLane stage took longer on it,
so the two campaigns' times are history, not a before/after. The retained R20/R21
native runs (129/129 steps and all 16 gates in 472.2 s and 472.6 s on 16 logical
CPUs of an i9-13900HX) remain on the benchmark page; times from different hosts
are not comparable.

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

The [Ibex SG13G2/EZ130 benchmark page](docs/benchmarks.md) is the single public performance source for RustEDA and its modules; module pages link to it. It holds the 2026-11-05 and 2026-11-02 host campaigns, the retained R20/R21 native runs and the historical 2026-10-30 matched comparison, each with a machine-readable receipt ([host 2026-11-05](docs/benchmarks/ibex-sg13g2-ez130-host-20261105.json) · [host 2026-11-02](docs/benchmarks/ibex-sg13g2-ez130-host-20261102.json) · [R20/R21](docs/benchmarks/ibex-sg13g2-ez130-development.json) · [historical](docs/benchmarks/ibex-sg13g2-ez130.json)).

The host campaigns add module head-to-heads. On identical inputs: RustSys against Yosys with ABC (LibreLane's synthesis script), RustSTA against OpenSTA, RustROAD extraction against OpenRCX and RustROAD GDS export against KLayout. RustROAD placement, CTS and routing are compared with OpenROAD's stages inside the two flows. RustSpice is compared with ngspice-45 on RC networks extracted from the final Ibex layout, not at transistor level: it does not support IHP's PSP103 transistor models. RustSpice is not timed in the RTL-to-GDS main flow.

## PDKs and standard-cell libraries

| Process | Standard-cell library | Verified scope |
| --- | --- | --- |
| SkyWater SKY130A | `sky130_fd_sc_hd`, `hdll`, `hs`, `ms`, `ls`, `lp`, `hvl` | HD: synthesis, timing and small-block physical flow qualification. Other libraries: installed; integrated physical flow unverified. |
| GlobalFoundries GF180MCU | `gf180mcu_fd_sc_mcu7t5v0`, `gf180mcu_fd_sc_mcu9t5v0` | 7T: synthesis, timing and small-block physical flow qualification. The 9T library is also a library-coverage baseline, not the qualified physical-flow default. |
| IHP SG13G2 | `sg13g2_stdcell`, [EZ130 8T v1.1](https://iip.ethz.ch/ez-library/ihp130-8t.html) | EZ130: Ibex with two real SRAMs completes native RTL-to-GDS with all 16 gates (retained runs on one host). Host campaigns with reconstructed EZ130 tech LEF and GDS: on 2026-11-05 the current recipe passed every shared gate with a pass/fail verdict and the 1 ps RustSTA/OpenSTA parity gate; on 2026-11-02 the earlier recipe passed every design gate but not the parity gate. Both are `unqualified`. Other EZ130 designs: unverified. IHP standard cells: installed; integrated flow unverified. |

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

# RustEDA

<p align="center">
  <img src="docs/assets/rusteda-wordmark.svg" alt="RustEDA wordmark" width="360">
</p>

**Agent-friendly RTL-to-GDS in Rust.** RustEDA gives AI agents and engineers
a CLI for synthesis, timing analysis and physical design. An agent can inspect
a plan before running it, watch individual steps and use timing, geometry and
check results to decide what to change next.

**4.4× observed end-to-end speedup** on IHP SG13G2 in the 13 November Ibex
campaign, with **752 s vs 3,283 s** including preparation and verification
on a matched check schedule. The same campaign measured **40.3× synthesis
speedup** and **7.8× routing speedup**.
[Stage timings and check outcomes](#ihp-sg13g2-results)

[Agent workflow](#built-for-ai-agents) ·
[Chip layouts and benchmarks](#ibex-rtl-to-gds-across-three-open-pdks) ·
[Get started](#start-building) ·
[Documentation](https://lab-emi.github.io/RustEDA/)

## Built for AI agents

1. **Plan.** Compose a PDK profile into a hash-pinned step graph.
   `rusteda flow exec --dry-run` resolves the graph and checks the tool,
   input and PDK identities before any step runs.
2. **Run and inspect.** Execute the full graph or stop at a named step with
   `--stop-after`. Stream JSONL progress with `--events` and poll
   `rusteda flow exec-status` from another process. Step receipts record
   timings, logs and failure codes.
3. **Iterate on a known revision.** Revisioned disk workspaces support
   read-only inspection while a worker runs. Mutations require `--base-rev`
   and reject a stale revision with `REVISION_CONFLICT`.
4. **Verify the result.** Runs record the identities of GDS, DEF, netlists
   and SPEF alongside check verdicts. The shared gates report timing,
   equivalence and physical findings for the exported layout.

[Native PDK recipe](integration/ibex_pdks/NATIVE.md) ·
[Workspace operations and recovery](docs/flows.md) ·
[Executor and JSON contract](apps/docs/specs/flow/exec.md)

## Ibex RTL-to-GDS across three open PDKs

**IHP SG13G2 · SKY130A · GF180MCU D.** RustEDA and LibreLane 3.0.17 build
Ibex with hard instruction and data SRAM. Within each PDK both use the same
RTL, constraints, macro views and floorplan. The panels show the exported
GDS side by side, with **RustEDA on the left** and **LibreLane on the right**.

![SKY130A final GDS side by side. RustEDA and LibreLane share the same die and two OpenRAM banks. Labels show SRAM placement and the two-core benchmark results.](docs/benchmarks/figures/ibex-sky130-clearance-20261115.png)

![GF180MCU D final GDS side by side. Four foundry SRAM byte lanes form each of the instruction and data banks. Labels show area, timing and GDS export time.](docs/benchmarks/figures/ibex-gf180-20261115.png)

![IHP SG13G2 final GDS side by side for the 10 November pair. Both 4 KiB SRAM banks are highlighted on each layout.](docs/benchmarks/figures/ibex-ihp-20261110.png)

Each measurement lists **RustEDA / LibreLane**. Both flows use the same
**10 MHz clock (100 ns)** for the power estimate. Percentages show RustEDA's
change relative to LibreLane.

| Open PDK | Modeled power at 10 MHz for both (mW) | Standard-cell area (µm²) | SS setup Fmax (MHz) | SS setup slack at 10 MHz (ns) | Time to GDS (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| [IHP SG13G2](docs/benchmarks/ibex-three-pdk.md#ihp-sg13g2-layout-pair) | 8.63 / 5.57<br>+54.9% | 187,585 / 204,848<br>**−8.4%** | 42.02 / 24.86<br>**+69.0%** | +49.44 / +47.94 | 1,348.3 / 2,073.4<br>**1.54× faster** |
| [SKY130A, two cores](docs/benchmarks/ibex-three-pdk.md#sky130a-layout-pair) | 28.99 / 9.50<br>+205.2% | 176,089 / 176,427<br>**−0.19%** | 23.75 / 17.21<br>**+37.9%** | +40.59 / +35.64 | 1,241.0 / 1,771.6<br>**1.43× faster** |
| [GF180MCU D, two cores](docs/benchmarks/ibex-three-pdk.md#gf180mcu-d-layout-pair) | 155.89 / 139.09<br>+12.1% | 478,020 / 490,812<br>**−2.6%** | 12.01 / 10.75<br>**+11.7%** | +16.72 / +6.96 | 474.4 / 851.5<br>**1.79× faster** |

Power uses post-route OpenSTA at TT with identical vectorless input activity.
The Fmax column reports the setup ceiling from SS period sweeps on the fixed
routed layouts. Area measures the standard-cell footprint within the same
die. [QoR methods and model limits](docs/benchmarks/ibex-three-pdk.md#power-area-and-frequency)

The IHP figure and table use the 10 November pair measured under competing
host load. The SKY130A and GF180MCU D pairs used matched cores within each
process. SKY130A was effectively tied on four cores at **1,173.71 / 1,173.80 s**.
Both SKY130A layouts passed DRC and had antenna findings. Both GF180MCU D layouts
had four whole-die metal-density findings. The benchmark records the timing,
equivalence and signoff verdicts for each layout. [Three-PDK results and receipts](docs/benchmarks/ibex-three-pdk.md)

### IHP SG13G2 results

The **13 November** campaign used EZ130 cells and two IHP SRAM macros.
RustEDA and LibreLane ran sequentially on the same 4-vCPU cloud host, using
the same prepared design and shared check scripts. These are paired phase
wall times from that run. The campaign has one timed sample per flow.

| Phase | RustEDA | LibreLane | Observed speedup |
| --- | ---: | ---: | ---: |
| Synthesis and mapping | 3.10 s | 124.93 s | **40.3×** |
| Placement and clock tree | 19.56 s | 41.21 s | **2.1×** |
| Repair before detailed routing | 13.85 s | 48.36 s | **3.5×** |
| Global and detailed routing | 266.71 s | 2,083.40 s | **7.8×** |
| In-flow static timing | 26.96 s | 138.93 s | **5.2×** |
| Parasitic extraction phase | 9.51 s | 38.97 s | **4.1×** |
| GDS, DEF and netlist export phase | 1.67 s | 266.29 s | **159×** |
| **End to end with checks on a matched schedule** | **751.54 s** | **3,283.05 s** | **4.4×** |

RustEDA used **5.2% less standard-cell area** and **7.7% less routed signal
wire** in this campaign. It passed the shared timing gate. LibreLane reported
112 capacitance and fanout violations across both corners and its equivalence
check was unverified. The receipt records `comparison_incomplete`. The
end-to-end ratio uses the observed elapsed times.
Charging every check serially gives **3.5×**. Phase times include each flow's
work for that phase. The LibreLane extraction phase includes fill, IR and
reports, and its export phase includes rendering and XOR. [Phase breakdown and gate outcomes](https://lab-emi.github.io/RustEDA/benchmarks/#ibex-host-campaign-2026-11-13) ·
[Campaign receipt](docs/benchmarks/ibex-sg13g2-ez130-host-20261113.json)

[Standalone engine comparisons and earlier runs](docs/benchmarks.md) cover
other workloads and resource allocations, including extraction results at
two and four threads. The [PULPissimo archive](docs/benchmarks/pulpissimo.md)
records the withdrawal of the historical full-SoC measurements after an AXI
width-cast error.

## The tool suite

| Tool | Role in the design loop |
| --- | --- |
| [RustEDA CLI, flow and Studio](docs/modules.md) | Run steps, track revisions, and inspect hierarchy, paths and logic cones. |
| [RustSys](https://github.com/lab-emi/RustSys) | Synthesize RTL, map cells and check equivalence. |
| [RustSTA](https://github.com/lab-emi/RustSTA) | Analyze timing and constraints with incremental updates. |
| [RustROAD](https://github.com/lab-emi/RustROAD) | Place, build clock trees, route, extract parasitics and export GDS. |
| [RustSpice](https://github.com/lab-emi/RustSpice) | Simulate circuits and compute adjoint sensitivities in separate simulation workloads. |
| [RustEDA Common](https://github.com/lab-emi/RustEDA-Common) | Share formats, design models, disk workspaces and compute kernels. |

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/rusteda-banner.svg">
  <img src="docs/assets/rusteda-banner.gif" alt="RustEDA feedback loop from RTL through implementation and measurement to the next design change" width="1200">
</picture>

## Start building

Git, Python 3.11+, Rust 1.100.0 and a native Linux build environment are the
development baseline. Studio has additional native GUI dependencies.

```sh
git clone --recurse-submodules https://github.com/lab-emi/RustEDA.git
cd RustEDA
./dev status
./dev doctor
./dev build RustEDA --release --no-default-features --memory-mib 4096
./rusteda --help
```

The `./dev` entrypoint bounds worker concurrency and memory and records
resource use. [Development guide and engine builds](docs/development.md)

### Inspect an agent's first native run

With a composed benchmark manifest, use the built Rust CLI to inspect the
plan. During execution, `flow exec-status --output run/output` reads progress
and completed step receipts.

```sh
./.work/target/RustEDA/release/rusteda flow exec \
  --manifest compose/be/prefix-manifest.json \
  --project compose/be --output run/output --pdk sky130 --dry-run
```

[Compose a native PDK manifest and start the worker](integration/ibex_pdks/NATIVE.md)

## Run a verified design loop

The repository launcher supports the small-block workspace flow. Supply the
documented pinned tools and PDKs on Linux, with Podman and a user systemd manager.

```sh
./rusteda tools configure
./rusteda tools verify
./rusteda flow --workspace .work/counter init --base-rev empty --design counter --pdk sky130
./rusteda flow --workspace .work/counter run --base-rev rev0
./rusteda flow --workspace .work/counter report --summary-only
```

Small-block physical qualification covers eight designs on SKY130 and GF180,
with 15 ordinary passes and one declared minimum-pulse-width deviation.
See the worker, cache and recovery procedures before using the flow on a new
workspace. [Flow operation](docs/flows.md) · [Qualification scope](docs/verification.md)

## PDKs and standard-cell libraries

Use `./rusteda library --help` to find pinned Liberty corners and
`./rusteda sram sky130 --corner tt` to locate SRAM views and their hashes.
The Ibex benchmarks use EZ130 8T on IHP SG13G2, SKY130 HD and GF180MCU 7T.
[PDK and SRAM setup](docs/pdks.md) · [Benchmark input provenance](integration/ibex_pdks/README.md)

## License and provenance

Apache-2.0. Each module retains its own LICENSE and NOTICE. Benchmark
designs, PDKs, reference executables and rule decks carry their respective
licenses. Reference tools are run as external executables over documented
file interfaces. [License](LICENSE) · [Provenance](docs/provenance.md)

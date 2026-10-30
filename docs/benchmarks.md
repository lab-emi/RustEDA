# Ibex on SG13G2 / EZ130

Updated **2026-10-30**. This is the sole current public benchmark campaign for RustEDA and its modules. The target is Ibex on the IHP SG13G2 process, using the EZ130 standard-cell library and **two real SRAM macros**. EZ130 is a cell library for SG13G2, not a separate process.

**RustEDA completed the flow; LibreLane did not.** RustEDA went from raw SystemVerilog to GDS and passed all 16 independent gates in one fresh public run. The matched LibreLane 3.0.17 run closed timing but failed complete foundry main DRC with one M2.b violation. **No end-to-end speedup or memory ratio is published**, because a ratio needs two completed flows. Every measured value is listed side by side below.

[Machine-readable receipt](benchmarks/ibex-sg13g2-ez130.json)

## Result

| | RustEDA | LibreLane 3.0.17 |
| --- | --- | --- |
| Outcome | **Completed**, 16 of 16 gates | **Failed** at foundry main DRC |
| Elapsed, raw SV to last gate | 2,936.6 s | 2,790.0 s until it stopped; antenna, LVS, simulation and equivalence did not run |
| Preparation (inside the timer) | 12.7 s | 1.6 s |
| Peak process RSS | 2.16 GiB | 2.94 GiB |
| Aggregate cgroup peak | 8.00 GiB (the limit, filled by page cache) | 7.80 GiB (includes page cache) |
| Standard-cell area | 233,909 µm² | 222,522 µm² |
| Final non-filler density (cap 70%) | 33.25% | 31.63% |
| Macro-inclusive core utilization | 50.68% | 49.56% |
| Routed signal wirelength | 1,678,599 µm | 1,077,762 µm |
| Vias | 301,859 | 130,286 |
| TT setup / hold worst slack | 49.293 / 0.136 ns | 48.477 / 0.158 ns |
| SS setup / hold worst slack | 48.877 / 0.261 ns | 47.640 / 0.291 ns |
| Setup, hold, slew, capacitance and fanout violations | 0 | 0 |
| Active unconstrained endpoints | 0 (156 inactive) | 0 (156 inactive) |
| Foundry main DRC | 0 | **1** (M2.b) |
| Foundry antenna DRC | 0 | 12 *(diagnostic)* |
| Full-device LVS | Passed | Passed *(diagnostic)* |
| Vendor-model simulation | Passed | Passed *(diagnostic)* |
| Synthesis / final equivalence | Proven / proven | Unverified / unverified *(diagnostic)* |
| End-to-end speedup | Not defined | Not defined |

Both sides' timing comes from an independent nominal OpenRCX extraction of the final layout, analysed with OpenSTA at TT 1.20 V / 25 °C and SS 1.08 V / 125 °C. For RustEDA, RustSTA and OpenSTA also agree within 0.806 ps on identical SPEF, against a 1 ps gate.

*Diagnostic* rows ran after LibreLane's timed flow stopped. They replay the producer's own signoff inputs on the same final layout and never turn the failed run into a qualified one.

## Reading the result

- **Elapsed time is not an end-to-end comparison.** LibreLane stopped at main DRC, before antenna, LVS, simulation and equivalence. Its 2,790.0 s therefore omits work that RustEDA's 2,936.6 s includes.
- **Timing does not separate the flows.** At the 100 ns clock both have about 48–49 ns of setup slack and positive hold slack at both corners.
- **RustEDA's layout is less efficient.** It uses 11,387 µm² more standard-cell area, about 600,000 µm more signal wire and more than twice the vias. Routing quality is the main gap to close.
- **LibreLane's DRC failure.** The M2.b edge pair sits on a Via1/Via2 stack of net `_08009_`. LibreLane's detailed-router log warns `[WARNING DRT-0349] LEF58_ENCLOSURE with no CUTCLASS is not supported` for Via1, Via2 and other cut layers, so this enclosure is not enforced during routing. No waiver is applied.
- **LibreLane's antenna diagnostic.** All 12 Ant.b violations are on the top-level input net `prog_addr_i[3]`: 398 µm of wire to two gates and no diode.
- **LibreLane's equivalence diagnostic.** The checker cannot name-match 431 renamed registers in LibreLane's netlist to the RTL, so the result is unverified. This is a checker limitation, not evidence of non-equivalence.

## What both flows shared

- **Identical files**, checked by SHA-256:
  - design and checking: RTL, testbench, SDC, physical contract, physical policy and density policy;
  - EZ130: TT/SS Liberty, cell LEF and technology LEF;
  - SRAM: TT/SS Liberty and LEF;
  - signoff: the DRC profile and the LVS deck.
- **Design.** lowRISC Ibex v2.1.0 at `38c07093`, default RV32IMC, two `RM_IHPSG13_1P_1024x32_c2_bm_bist` SRAMs. IHP Open PDK at `5cccb161`; EZ130 8T v1.1.
- **Constraints.** 100 ns clock, TT and SS, nominal RC, CPPR off, no derate, 0.05 ns uncertainty, 5–10 ns input delay, 0–5 ns output delay, routing Metal2 through TopMetal2, final density at most 70%.
- **Floorplan.** 1100 × 1000 µm die, core (20.16, 20.16)–(1079.82, 977.76) µm, `u_imem` at (80, 631.3) and `u_dmem` at (600, 631.3), orientation N, 10 µm halo.
- **Signoff.** The same scripts and decks:
  - KLayout foundry main and antenna decks;
  - full-device LVS;
  - Icarus vendor-model simulation;
  - CEC checker;
  - final-density checker;
  - OpenRCX + OpenSTA timing.
- **Host.** 13th Gen Intel Core i9-13900HX. Each flow ran alone on the same 16 CPUs (0, 2, …, 14, 16–23), in its own cgroup with 8 GiB memory and 2 GiB swap.

## Measurement scope

The primary timer starts at frozen raw SystemVerilog, before parsing, elaboration and preparation. It stops after GDS and every required gate and report. Preparation, initialization, disk I/O, extraction, repair, signoff and correctness checks are all inside it, and preparation is never subtracted. Tool and PDK installation, source acquisition and harness source copies are excluded for both flows.

Each flow has one cold sample. Repetition was skipped because no ratio could be formed. Peak RSS is the largest single process: for RustEDA, the maximum over reaped worker and tool processes; for LibreLane, GNU time over the producer tree. The cgroup peak covers the whole process family including page cache. RustEDA's cgroup peak equals the 8 GiB limit, which is page cache, not a footprint.

**RustEDA run notes.** The run used `./rusteda flow` with the `ihp_sg13g2_ez130` worker configuration. `RUSTROAD_ROUTE_TRACE=1` was set, so diagnostic trace I/O counts against RustEDA. The executables were built from the repair checkouts and are identified by SHA-256 in the receipt: RustSys `e6cf0c77…`, RustSTA `b17c092b…`, RustROAD `0889b370…` (engine `0813571`) and RustEDA `af6b7ade…`.

## How LibreLane was run

LibreLane 3.0.17 ran as a black-box executable: the AppImage `c8ef5ae2…` with OpenROAD `dcf36133`, Yosys 0.62 and OpenSTA 3.1.0. Its environment was set to the contract (`CLOCK_UNCERTAINTY_CONSTRAINT 0.05`, `TIME_DERATING_CONSTRAINT 0`, `IO_DELAY_CONSTRAINT 10`).

Around LibreLane's public steps, a RustEDA-authored producer adds a bounded repair loop ([`integration/ibex_sg13g2_ez130`](https://github.com/lab-emi/RustEDA/tree/main/integration/ibex_sg13g2_ez130)):

- global-route DRV ECO rounds;
- up to six detailed-route rounds with routed-timing ECOs;
- clock-tree protection;
- removal and re-insertion of antenna diodes that detailed routing left unconnected.

This help favours LibreLane; without it, the earlier attempts failed sooner. Every attempt is preserved:

| Attempt | Outcome | Cause and fix |
| --- | --- | --- |
| a | Harness defect | Repair scripts hard-coded Metal2–Metal5; detailed routing rejected TopMetal guides after 8 min |
| b | Harness defect | The shared timing runner passed a top-level port to OpenSTA `get_nets -of_objects`; now looked up by name |
| c | Not launched | Preparation invalidated by an edit to a pinned file; re-prepared |
| d | Harness defect | The same port query in the ECO driver binding |
| e | Failed after 2,360.8 s | Timing converged, but 15–18 inserted diodes per round stayed unconnected and the 4-round budget ran out. Diodes are now removed and re-inserted, with names compacted (a numbering gap crashed `repair_antennas`), within 6 rounds |
| **f** | **Failed at main DRC** | Timing and diodes closed after 5 detailed rounds. This is the published sample |

## Module participation

| Module | Scope in this campaign |
| --- | --- |
| RustEDA | Flow execution, checkpoints, recovery and complete process-family accounting |
| RustSys | RTL synthesis, real-SRAM preservation, technology mapping and equivalence |
| RustSTA | Constraints, extracted timing and independent numerical checks |
| RustROAD | Floorplan, placement, CTS, routing, extraction and streamout |
| RustEDA Common | Shared models, formats, disk workspaces and compute used by the engines; included transitively |
| RustSpice | Not timed in the RTL-to-GDS main flow; no Ibex performance or comparative-speedup claim |

Older synthesis-only, small-block, OpenC910 and circuit-simulation performance tables are retired from current publication. Their source snapshots, licenses, regression fixtures and immutable qualification receipts are preserved. Sky130/GF180 correctness coverage remains required; it is not an additional current public performance campaign.

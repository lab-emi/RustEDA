# Ibex on SG13G2 / EZ130

This page holds three Ibex result sets for RustEDA and its modules, newest first: the [host campaign of 2026-11-02](#ibex-host-campaign-2026-11-02), which runs RustEDA and LibreLane on a 4-vCPU cloud host and adds module head-to-heads; the [R20/R21 native development runs of 2026-11-01](#latest-native-development-results-2026-11-01); and the [historical matched comparison of 2026-10-30](#historical-matched-comparison-2026-10-30). Each set names its own host, inputs and schedule, and times from different hosts are not comparable. The target is Ibex on the IHP SG13G2 process, using the EZ130 standard-cell library and **two real SRAM macros**. EZ130 is a cell library for SG13G2, not a separate process.

## Ibex host campaign · 2026-11-02

An Ibex RTL-to-GDS reproduction ran on a 4-vCPU cloud host, and both flows ran
to a final GDS. The repository's published native recipe and LibreLane 3.0.17
read the same prepared RTL, SDC and floorplan contract, and the same independent
gate scripts checked both final layouts. The native run is `unqualified`: the
eight derived qualification gates were not produced. **Both layouts pass foundry
main and antenna DRC with 0 violations, full-device LVS, vendor-model simulation
and the density cap. Neither flow passes every gate.** RustEDA's layout passes
every design gate, including TT/SS setup, hold, slew, capacitance and fanout with
zero violations and `proven` synthesis and final equivalence; its producer stops
at the `timing_accuracy` gate, where RustSTA and OpenSTA differ by **1.405 ps**
on identical SPEF against a 1 ps tolerance. LibreLane's layout fails the timing
gate on maximum-capacitance (28 TT, 24 SS) and fanout (30) violations with setup
and hold met, and its equivalence is `unverified`. The receipt's status is
therefore `comparison_incomplete` and its comparative ratios are null; this
section forms none.

| | RustEDA | LibreLane 3.0.17 |
| --- | --- | --- |
| Configuration | Published native recipe (run16-class) composed by `compose_native.py`; routing at 4 threads, other RustROAD steps on its default 2-thread pool; `unqualified` | Classic flow through `Checker.XOR`, prepared contract config, its own post-GRT design repair (`RUN_POST_GRT_DESIGN_REPAIR=true`), `DRT_THREADS=4` |
| Flow outcome | **Producer `failed`** at `be_timing` (`timing_accuracy`) after 114 of 119 manifest steps; the remaining gates ran separately with the recipe's scripts | Exit 0 after 65 steps |
| Foundry main DRC (complete main deck, `recommended` rules disabled) | `passed`, 0 | `passed`, 0 |
| Foundry antenna DRC | `passed`, 0 | `passed`, 0 |
| Full-device LVS | `passed` (top-port identity, 2 macros verified) | `passed` (top-port identity, 2 macros verified) |
| Timing gate, OpenRCX SPEF | `passed` | **`failed`** (capacitance and fanout) |
| RustSTA/OpenSTA parity on identical SPEF, 1 ps gate | **`failed`**, 1.4052 ps | Not a gate of this flow |
| Vendor-model simulation | `passed` | `passed` |
| Synthesis / final equivalence | `proven` / `proven` | `unverified` / `unverified` (checker cannot name-match 433 renamed registers) |
| Final density | `passed` | `passed` |
| Flow wall time | 1,329.616 s (producer, until it stopped) | 2,444.849 s |
| Shared-gate time added | 1,277.11 s: DRC, antenna, LVS, simulation, equivalence and density, which the producer did not reach | 838.821 s: all eight shared gates (sum of the receipt's gate times) |
| End-to-end time | 2,609.93 s | 3,286.87 s |
| End-to-end scope | sv2v preparation (3.2 s) + producer (synthesis through extraction, OpenRCX and RustSTA/OpenSTA signoff timing) + the gates above | sv2v preparation (3.2 s) + flow through `Checker.XOR` + every shared gate, including OpenRCX and RustSTA/OpenSTA timing |
| End-to-end ratio | Not published: neither flow passes every gate | Not published |
| Peak process RSS, flow | 1,080,408 KiB (1.03 GiB) | 2,333,000 KiB (2.22 GiB) |
| Peak process RSS, shared gates | 2,615,468 KiB (2.49 GiB) | 2,290,552 KiB (2.18 GiB) |
| Cgroup peak, flow (includes page cache) | 8,589,934,592 B (8.00 GiB, the limit) | 3,338,055,680 B (3.11 GiB of a 10 GiB limit) |
| Standard-cell area | 233,909.2 µm² | 197,913.7 µm² |
| Final non-filler density (cap 70 %) | 33.25 % | 28.14 % |
| Macro-inclusive core utilization | 50.68 % | 47.13 % |
| Routed signal wirelength | 1,679,076.6 µm | 969,571.6 µm |
| Vias | 301,099 | 122,194 |
| TT setup / hold worst slack | 49.296 / 0.143 ns | 48.603 / 0.172 ns |
| SS setup / hold worst slack | 48.869 / 0.265 ns | 47.844 / 0.312 ns |
| Setup / hold violations, TT and SS | 0 / 0 | 0 / 0 |
| Slew / capacitance / fanout violations, TT | 0 / 0 / 0 | 0 / **28** / **30** |
| Slew / capacitance / fanout violations, SS | 0 / 0 / 0 | 0 / **24** / **30** |
| Active unconstrained endpoints | 0 (156 inactive) | 0 (156 inactive) |

Timing rows come from the shared gate: an independent nominal OpenRCX extraction
of each final layout, analysed by RustSTA and OpenSTA at TT 1.20 V / 25 °C and
SS 1.08 V / 125 °C, keeping the worse value of the two. Slacks are rounded to
1 ps and percentages to 0.01 %; GiB values are conversions of the recorded KiB
and bytes, and LibreLane's shared-gate total is the sum of its
`shared_gate_seconds`; all other values are as recorded. Peak process RSS is GNU
time's maximum over the waited process tree. The cgroup peak covers the whole
flow including page cache, so RustEDA's 8 GiB is its limit, not a footprint.
Each flow has one timed sample. A second complete native run (bring-up #3,
run under shared host load) produced byte-identical GDS, DEF, synthesis,
final and powered netlists and native SPEF; only the OpenRCX SPEF, written by
OpenROAD, differs ([bring-up artifacts](benchmarks/evidence/ibex-host-20261102/native/bringup-artifacts.json)).

### Where time goes

Stage seconds come from each flow's own step records (`stage_seconds` in the
receipt). The flows group work differently, so rows pair stages by phase, not by
identical scope.

| Phase | RustEDA stage | s | LibreLane stage | s |
| --- | --- | ---: | --- | ---: |
| Synthesis | RustSys | 2.37 | Yosys + ABC | 110.61 |
| Checks | — | | lint and checks | 0.82 |
| Floorplan | — | | floorplan, PDN, taps, IO | 10.56 |
| Placement and CTS | placement and CTS (RustROAD) | 20.92 | placement (global + detailed) | 16.30 |
| | | | CTS | 20.17 |
| Repair before detailed routing | frontend STA, ECO rounds and planning helpers (RustSTA, Python) | 64.65 | resizer repair (design/timing) | 39.92 |
| Routing | routing (RustROAD) | 389.63 | global routing and antenna repair | 68.98 |
| | | | detailed routing | 1,796.83 |
| Repair after routing | post-route repair | 179.00 | — | |
| | antenna repair and verification | 409.35 | | |
| Static timing | signoff timing (RustSTA + OpenSTA) | 64.09 | STA (OpenSTA, pre/mid/post) | 114.35 |
| Extraction | native extraction (RustROAD) | 161.48 | fill, RCX, IR drop, reports | 34.66 |
| | OpenRCX extraction | 18.02 | | |
| Export | GDS/DEF/netlist export | 19.04 | streamout, render, LEF, XOR | 230.69 |
| Other | renders, profiles, physical inputs | 0.17 | other | 0.00 |
| | foundry DRC, LVS, macro contract (not reached) | 0.00 | | |
| **Flow wall time** | | **1,329.616** | | **2,444.849** |

RustEDA's routing is one `be_route` step covering global and detailed routing.
LibreLane's resizer row holds its four repair steps: design repair after global
placement, timing repair after CTS, and design and timing repair after global
routing. Its export row includes Magic LEF writing (129.782 s) and the KLayout
XOR (54.405 s). RustEDA's repair after routing takes 588.35 s, 44 % of its
producer time; detailed routing takes 73 % of LibreLane's flow time.

### Module head-to-heads

Each head-to-head runs both contenders on the same input on this host, with one
warm-up and three measured repetitions, interleaved, unless stated otherwise.
Times are medians, with the fastest run in parentheses. A quotient divides the
slower median by the faster one and applies to that task only.

#### RustSys vs Yosys

The task maps the prepared Ibex RTL (sv2v output) onto EZ130 TT and SRAM
Liberty. RustSys runs the native flow's workspace sequence (init, import,
synthesis, export at 2 threads, as in the recipe) and its single-process direct
mode (all CPUs). Yosys 0.70 runs LibreLane 3.0.17's own synthesis script, with
ABC, on the run's step configuration. Netlist QoR comes from one neutral
toolchain: `yosys stat -liberty` for area and cell classes, and the harness's
longest combinational cell path on Yosys's JSON netlist (`netlist_qor.py`) for
logic depth.

| | RustSys | Yosys 0.70 + ABC, LibreLane script |
| --- | --- | --- |
| Median wall, workspace sequence | 2.12 s (2.08 s) | 102.83 s (101.07 s) |
| Median wall, direct mode | 1.36 s (1.30 s) | — |
| Quotient, Yosys ÷ RustSys | 48.5× (sequence), 75.6× (direct) | — |
| Worst peak RSS | 88,060 KiB (sequence), 85,656 KiB (direct) | 763,628 KiB |
| Standard-cell area | 151,095.773 µm² (+2.18 %) | 147,866.947 µm² |
| Area including both SRAM macros | 431,461.1616 µm² | 428,232.336 µm² |
| Logic depth in cells (excluding buffers) | 57 (55) | 40 (40) |
| Cells: total / sequential / buffer and inverter / other / macro | 12,585 / 1,998 / 1,521 / 9,064 / 2 | 12,605 / 1,905 / 186 / 10,512 / 2 |
| Equivalence of each flow's synthesis netlist to RTL (shared gate) | `proven` | `unverified` |

QoR rows describe the netlist of the first measured sequence run. The
equivalence row comes from each flow's shared synthesis-equivalence gate, not
from the benchmark netlists: Yosys's benchmark netlist is byte-identical to
LibreLane's synthesis netlist, while RustSys's differs in bytes from the native
flow's synthesis netlist and was not checked separately. The receipt
also holds ideal-wire OpenSTA timing of both netlists as a diagnostic only:
LibreLane defers fanout buffering to placement, so timing is compared on the
final layouts.

#### RustSTA vs OpenSTA

Both analyse the final native netlist with the native SPEF, Liberty and SDC at
TT and SS, at 2 and 4 threads. RustSTA runs init, import, one run covering both
corners and 8 reports. OpenSTA runs one process per corner (read, link, SDC,
SPEF, full reports). Wall time sums each contender's processes.

| | RustSTA 0.4.3 | OpenSTA 3.1.0 |
| --- | --- | --- |
| Median wall, 2 threads | 17.898 s (17.463 s) | 15.253 s (14.746 s) |
| Median wall, 4 threads | 15.976 s (14.992 s) | 13.497 s (13.041 s) |
| Quotient, RustSTA ÷ OpenSTA | 1.17× (2 threads), 1.18× (4 threads) | — |
| Worst peak RSS, 2 / 4 threads | 498,004 / 502,876 KiB | 266,916 / 267,704 KiB |
| Maximum parity difference, identical SPEF | **1.4052 ps**: TT setup worst slack on native SPEF (49.279521 vs 49.280926 ns) | — |
| Other values above 1 ps | 1.2838 ps: TT setup worst slack on OpenRCX SPEF | — |
| Remaining six compared values | 0.036–0.2153 ps | — |

#### RustROAD extraction vs OpenRCX

Both extract the final native routed DEF at the nominal corner. OpenRCX
(OpenROAD 26Q3) ran with `set_thread_count` 2 and 4. The recorded RustROAD
`extract` command passes no `--threads`, so RustROAD's pool stayed at its
default of 2 threads in both settings; the harness's `RAYON_NUM_THREADS` of 2 or
4 does not size it. The RustROAD rows labelled 4 threads are therefore a second
2-thread sample.

| | RustROAD 0.5.0 | OpenRCX |
| --- | --- | --- |
| Median wall, 2 threads | 158.823 s (157.605 s) | 17.649 s (17.237 s) |
| Median wall, 4-thread setting | 158.823 s (157.790 s) | 17.114 s (15.904 s) |
| Quotient, RustROAD ÷ OpenRCX | 9.0× (2 threads), 9.3× (4-thread setting) | — |
| CPU time per run | 155.73–158.90 s | 15.70–17.69 s |
| Worst peak RSS, 2 / 4 threads | 810,452 / 810,632 KiB | 295,860 / 295,728 KiB |
| SPEF nets | 16,329 | 16,329 |
| SPEF total capacitance | 312.638 pF | 290.705 pF |
| SPEF total resistance | 6,633.54 kΩ | 6,691.924 kΩ |

RustROAD's total capacitance is 7.5 % above OpenRCX's and its total resistance
0.9 % below. Its CPU time tracks its wall time at both thread counts.

#### RustROAD GDS export vs KLayout

Both stream the final native DEF to GDS: RustROAD `export-gds` (the flow's
recorded `be_gds` step) against
KLayout 0.30.12 running RustROAD's `kl_def2gds.py` with the tech LEF, cell LEF,
combined cell GDS and IHP layer map.

| | RustROAD export | KLayout DEF→GDS |
| --- | --- | --- |
| Median wall | 3.019 s (2.942 s) | 2.286 s (2.226 s) |
| Quotient, RustROAD ÷ KLayout | 1.32× | — |
| Worst peak RSS | 379,668 KiB | 276,124 KiB |
| Output size | 48,157,358 B | 59,716,694 B |

The two GDS files are not byte-equivalent, and the receipt records no geometric
comparison of them, so this is a timing comparison only. The first KLayout
replay failed before reading the DEF and was rerun with a `.def` path.

#### RustROAD place, CTS and route vs OpenROAD

Times and layouts come from the two complete flows above. The netlists and the
repair around each stage differ, so this is not an identical-task comparison
and no quotient is formed here.

| | RustROAD (native recipe) | OpenROAD 26Q3 (LibreLane Classic) |
| --- | --- | --- |
| Floorplan, PDN, taps, IO | — (no separate stage recorded) | 10.56 s |
| Placement and CTS | 20.92 s, one stage | 16.30 s placement (global + detailed) + 20.17 s CTS |
| Repair before detailed routing | 64.65 s frontend STA, ECO rounds and planning helpers | 39.92 s resizer repair |
| Routing | 389.63 s (`be_route`, global and detailed) | 68.98 s global routing with antenna checks and repair + 1,796.83 s detailed routing |
| Repair after routing | 179.00 s post-route repair + 409.35 s antenna repair and verification | — |
| Standard-cell area | 233,909.2 µm² | 197,913.7 µm² |
| Standard cells / antenna diodes / taps / fillers | 16,208 / 52 / 13,923 / 55,651 | 15,191 / 504 / 6,900 / 44,672 |
| Routed nets | 16,329 | 15,227 |
| Routed signal wirelength | 1,679,076.6 µm | 969,571.6 µm |
| Vias | 301,099 | 122,194 |
| Slew / capacitance / fanout violations, TT / SS | 0 / 0 / 0 at both | 0 / 28 / 30 TT, 0 / 24 / 30 SS |

#### RustSpice vs ngspice-45

IHP SG13G2 transistors use PSP103 models, which RustSpice does not implement, so
a transistor-level Ibex comparison is not possible. The comparison uses linear
RC networks from the native nominal SPEF of the final Ibex layout: the eight
largest single nets (548–649 unknowns, 3–10 sinks) and two multi-net decks.
Each deck drives real nets with a Thevenin ramp and keeps the extracted
resistors and positive capacitors, with coupling capacitance grounded.
`rustspice bench-suite` judges accuracy against a tightly converged ngspice reference (max
error / swing ≤ 2 × 10⁻³, set in each deck; reference `tmax` 2 ps) and times seven interleaved repetitions after a
correctness warm-up against ngspice's SPARSE and KLU solvers. Per-deck wall is
the process wall time of one deck: start-up, parse, setup, solve and rawfile
write. Analysis time is ngspice's `Total analysis time` against RustSpice's
`analysis_us`, which excludes RustSpice's parse and compile; it is a diagnostic,
not the headline.

| | 8 single nets | 10 nets, 5,843 unknowns | 40 nets, 20,023 unknowns |
| --- | --- | --- | --- |
| Accuracy verdict | `pass` × 8 | `pass` | `pass` |
| Error / swing, RustSpice | 0.90–2.12 × 10⁻⁴ | 7.90 × 10⁻⁴ | 7.68 × 10⁻⁴ |
| Error / swing, default-tolerance ngspice | 0.66–1.59 × 10⁻⁴ | 1.62 × 10⁻³ | 1.69 × 10⁻³ |
| ngspice SPARSE, median per-deck wall | 534.33–618.43 ms | 1,107.13 ms | 4,072.28 ms |
| ngspice KLU, median per-deck wall | 630.54–727.85 ms | 1,203.12 ms | 4,097.51 ms |
| RustSpice, median per-deck wall | 136.28–161.45 ms | 196.78 ms | 704.24 ms |
| Quotient, faster ngspice ÷ RustSpice, per-deck wall (median) | 3.68–4.18× | 5.63× | 5.78× |
| Quotient, faster ngspice ÷ RustSpice, analysis time only (median) | 4.60–5.14× | 7.74× | 7.82× |
| Median peak RSS, ngspice SPARSE (lower bound) / RustSpice | 15,404–15,708 / 33,728–38,416 KiB | 30,724 / 46,232 KiB | 71,608 / 144,152 KiB |

**The suite verdict is `PASS`: 10 of 10 cases pass.** SPARSE is the faster
ngspice solver in every case. On the single nets, default-tolerance ngspice is
closer to the reference than RustSpice; on the two multi-net decks, RustSpice
is closer. RustSpice's median peak RSS is 1.5–2.5× ngspice's; ngspice's peak is
the maximum of `/proc` polls, which the suite flags as a lower bound of the
true peak (`rss_lower_bound`). Process start-up medians are 6.77 ms for
ngspice and 2.47 ms for RustSpice. The suite records a shared host, with a
load average of 0.4 at the start and 1.0 at the end, so its quotients hold
within this session only.

#### RustEDA-Common

RustEDA-Common is compiled into RustSys and RustSTA, including the `rusteda`
workspace CLI used by both flows' timing steps; RustROAD and RustSpice do not
link it. It has no separate head-to-head.

### Reading the host result

- **No end-to-end ratio.** RustEDA fails `timing_accuracy`; LibreLane fails
  timing and leaves equivalence `unverified`. Only complete, independently
  checked runs may form comparative ratios, so the receipt's
  `end_to_end_speedup`, `standard_cell_area_ratio` and `wirelength_ratio` are
  null. The end-to-end times above are stated with their scopes, not as a
  speedup. Their content also differs: LibreLane's flow time includes Magic LEF
  writing, the KLayout XOR and an IR-drop report, while RustEDA's producer runs
  two extractions and both STA engines itself.
- **RustEDA's failed gate is tool agreement, not design timing.** The shared
  timing gate passes at TT and SS with zero setup, hold, slew, capacitance and
  fanout violations. The parity gate compares RustSTA with OpenSTA on identical
  SPEF: TT setup worst slack differs by 1.4052 ps on native SPEF and 1.2838 ps
  on OpenRCX SPEF, and the other six values are within 0.2153 ps. The same
  comparison measured 0.806 ps on the 2026-10-30 run16 layout and 0.4754 ps on
  R20/R21, both on the 16-CPU i9-13900HX host with different tool builds. The
  gate stays failed.
- **LibreLane's failures.** With its own post-GRT repair, the layout keeps
  28 (TT) and 24 (SS) maximum-capacitance and 30 fanout violations while
  meeting setup and hold. The prepared-config diagnostic, without that repair,
  had 7 slew, 181 capacitance and 56 fanout violations at TT. The equivalence
  checker cannot export obligations because 433 implementation registers lack
  a unique named RTL counterpart (first `_21851_`, `DFFRQX1`). This is a checker
  limitation, not evidence of non-equivalence; vendor-model simulation passes
  as separate evidence.
- **Where RustEDA leads on this host.** On the identical synthesis task, RustSys
  takes 2.12 s (workspace sequence) and 1.36 s (direct) against 102.83 s for
  Yosys + ABC, at 88,060 KiB against 763,628 KiB. RustROAD's routing step takes
  389.63 s against 1,796.83 s for OpenROAD detailed routing alone, on a
  different netlist and with separate repair afterwards: routing plus post-route
  and antenna repair total 977.98 s, against 1,865.81 s for LibreLane's global
  routing with antenna repair plus detailed routing. On the extracted RC nets,
  the faster ngspice solver takes 3.68–5.78× RustSpice's median per-deck wall
  time, and RustSpice is within tolerance on all 10 cases. On the shared
  timing gate, RustEDA's layout has no DRV violations. Its producer's largest
  process peaks at 1.03 GiB against 2.22 GiB for LibreLane's flow; the two
  scopes differ.
- **Where it trails.**
    - *Physical QoR.* The native layout has 233,909.2 µm² of standard-cell area
      against 197,913.7 µm², 1,679,076.6 µm of routed signal wire against
      969,571.6 µm, and 301,099 vias against 122,194. Routing quality remains
      the main gap. The shared DRC, antenna and LVS gates take 606.637, 380.546
      and 265.762 s on its layout against 417.548, 205.074 and 162.513 s on
      LibreLane's; its GDS is 48,157,358 B against 17,717,408 B.
    - *Synthesis QoR.* RustSys's netlist has 2.18 % more standard-cell area and
      is deeper: 55 against 40 logic levels excluding buffers.
    - *Extraction.* RustROAD takes 9.0–9.3× OpenRCX's wall time with 2.7× its
      peak RSS. Both settings ran RustROAD's default 2-thread pool, and its
      CPU time tracks wall time, so in this configuration the extraction is
      effectively serial.
    - *STA.* RustSTA takes 1.17–1.18× OpenSTA's wall time with about 1.9× its
      peak RSS.
    - *GDS export.* RustROAD takes 1.32× KLayout's wall time.
    - *Parallel use and memory.* The host's one-minute load average during the
      producer run averaged 1.0 on 4 vCPUs (LibreLane run: 2.98). Repair after routing takes 44 % of producer
      time. The producer's cgroup reached its 8 GiB limit with page cache, and
      the kernel recorded 397 limit hits; LibreLane's peaked at 3.11 GiB. On
      the shared gates, the largest process on RustEDA's layout peaks at
      2.49 GiB against 2.18 GiB on LibreLane's.
- **The native layout reproduces run16, not R20/R21.** The repository
  publishes the run16-class recipe; the R20/R21 recipe inputs are not
  published, so this host could not run them. The host layout closely
  reproduces the 2026-10-30 RustEDA layout (run16): the same 233,909.2 µm²
  standard-cell area and 33.25 % density, 1,679,076.6 µm of wire against
  1,678,598.6 µm, 301,099 vias against 301,859, and OpenSTA TT setup/hold worst
  slack on OpenRCX SPEF of 49.2977/0.1427 ns against 49.2935/0.1362 ns. The
  inputs and tool builds differ (see the method below). It does not reach
  R20/R21's 204,053.9 µm², 1,190,522 µm and 168,381 vias.
- **Host scope.** Each flow has one timed sample on 4 vCPUs of a 2.1 GHz Xeon.
  Module wall times are medians of three repetitions (RustSpice: seven);
  synthesis QoR uses one run, and the place, CTS and route figures come from
  the single flow samples. None of these times is comparable with the 16-CPU
  i9-13900HX results below.

### Host campaign method and deviations

- **Host.** Intel Xeon @ 2.10 GHz, 4 logical CPUs, 15.7 GiB RAM
  (16,872,808,448 B), kernel 6.18.44-fc-v185 (receipt `host`, captured on the
  campaign host; the RustSpice suite records the same CPU), cgroup v1 memory controller, no swap device and no systemd user
  manager. Each flow had all 4 vCPUs, and each
  timed stage ran alone in its own child cgroup. Memory caps: RustEDA
  8 GiB RAM with a 2 GiB memory+swap allowance, as in the recipe; LibreLane
  10 GiB RAM, because its historical host had 8 GiB plus 2 GiB swap and this
  host has no swap.
- **Samples.** One timed sample per flow. Module head-to-heads use one warm-up
  plus three interleaved measured repetitions; RustSpice uses a correctness
  warm-up plus seven interleaved repetitions. Peak RSS is GNU time's maximum
  over the waited process tree; cgroup peaks include page cache.
- **Design and inputs.** lowRISC Ibex v2.1.0 at `38c07093`, default RV32IMC,
  top `ibex_sram_bench`, two `RM_IHPSG13_1P_1024x32_c2_bm_bist` SRAMs
  (`u_imem`, `u_dmem`); IHP Open PDK at `5cccb161`; 100 ns clock, TT and SS,
  nominal RC. sv2v 0.0.13.1 converts the RTL in 3.2 s at 282,856 KiB. Its
  output (`da905268…`) differs in bytes from the 2026-10-30 file (`8a8fe84b…`,
  a different sv2v build); both flows read the same converted file.
- **EZ130 reconstruction.** The ETH archive host was unreachable. Five of the
  seven EZ130 files that the contract pins are byte-identical: TT/SS Liberty,
  cell LEF, CDL and Verilog. The SG13G2 tech LEF and the cell GDS are
  reconstructed from public sources ([restore script](../integration/ibex_host_benchmark/ez130/restore.sh))
  and differ from the pinned files (`df2eed23…` against `375ae19d…`,
  `b4bed12d…` against `62876764…`). This campaign therefore does not reproduce
  the 2026-10-30 inputs.
- **Tools.** Both flows use nixpkgs builds: OpenROAD 26Q3 (with OpenSTA 3.1.0
  and OpenRCX), Yosys 0.70, KLayout 0.30.12, Magic 8.3.689, Netgen 1.5.325,
  Icarus Verilog 13.0, ngspice-45 and sv2v 0.0.13.1. LibreLane 3.0.17 is the
  PyPI wheel running these builds instead of its pinned OpenROAD `dcf36133` and
  Yosys 0.62. RustEDA engines: RustSys 0.1.0 (`0f8a2d80`), RustSTA 0.4.3
  (`0c2bcc6a`), RustROAD CLI 0.5.0 (`07e1626f`), RustSpice 0.1.0 (`cc2426f3`)
  and RustEDA-Common (`06a9cdac`); the receipt identifies each binary by
  SHA-256.
- **Unqualified native composition.** The run uses the repository's
  run16-class recipe, composed by `compose_native.py` with every file and tool
  pinned by path, SHA-256 and size, a grouped single-process antenna session
  and 4 threads for routing (other RustROAD steps on its default 2-thread
  pool). The 16-gate composer needs
  component-qualification evidence that is not published and was not
  fabricated, so the eight derived qualification gates are not produced and the
  run is `unqualified`. The producer runs the backend prefix manifest and stops
  at its first failed gate (`be_timing`, after 114 of 119 steps);
  `signoff_layout.py` runs the remaining gates with the recipe's own scripts.
- **LibreLane configuration.** Classic flow through `Checker.XOR` on the
  prepared contract config, with `DRT_THREADS=4` and LibreLane's own post-GRT
  design repair (`RUN_POST_GRT_DESIGN_REPAIR=true`). The RustEDA-authored repair
  loop of the 2026-10-30 comparison was not used. A diagnostic bring-up with the
  prepared config unchanged (post-GRT repair off) passed DRC (0), antenna (0),
  LVS and simulation and met setup and hold, but failed timing with 7 slew,
  181 capacitance and 56 fanout violations at TT (9, 140 and 56 at SS). Its
  layout had 969,340.6 µm of wire and 121,948 vias; equivalence and density
  were not run on it. It ran under shared host load, so its times are not
  measurements.
- **Host-local helper patches.** Thread allow-lists accept 4 threads; the
  producer reads the cgroup-v1 memory controller, and the LVS helper (also used
  by the logic helper) reads it and accepts the host executor's own cgroup; the RC-profile builder accepts the explicit
  calibration rules path; the density gate's DEF parser is patched.
- **Hardware.** 4 vCPUs of a 2.1 GHz Xeon instead of 16 logical CPUs of an
  i9-13900HX.

[Receipt and evidence inventory](benchmarks/ibex-sg13g2-ez130-host-20261102.json) ·
[Harness and host deviations](../integration/ibex_host_benchmark/README.md) ·
[RustSpice suite report](benchmarks/evidence/ibex-host-20261102/modules/spice-suite.md)

## Latest native development results · 2026-11-01

Two retained complete runs, **R20 and R21**, finish in **472.2 s and 472.6 s**.
Both complete 129/129 steps and all 16 independent gates, with empty failed and
unverified gate lists. They produce byte-identical GDS, DEF, netlists and native
SPEF; all 24 timing fields match the preceding qualified layout.

| Measurement | R20 | R21 |
| --- | ---: | ---: |
| Complete worker wall time | 472.1687 s | 472.5746 s |
| Maximum reaped worker/tool RSS | 2,134,644 KiB | 2,153,172 KiB |
| Aggregate cgroup peak, including file cache | 6,509,391,872 B | 6,481,956,864 B |
| Cgroup swap peak | 0 B | 0 B |
| Completed producer steps / required gates | 129 / 16 | 129 / 16 |
| RustSTA/OpenSTA maximum difference, identical SPEF | 0.4754 ps | 0.4754 ps |

The layout has **204,053.9 µm²** non-filler standard-cell area,
**1,190,522 µm** routed signal wire and **168,381 vias**. Complete foundry
main and antenna DRC, full-device LVS, both equivalence checks, vendor-model
simulation and TT/SS setup, hold and DRV pass. The additional maximal deck
reports 272 categories including LU.a, and the 547-category main deck includes
LU.b. The maximal scope excludes internal SRAM geometry under the stated macro
contract; the macro checks and full-device LVS remain required.

Timing uses nominal native and OpenRCX SPEF, propagated clocks, `f64`, CPPR off,
and TT/SS. Each corner has 156 inactive unconstrained endpoints and zero active
unconstrained endpoints. OpenRCX hold slack is +66.2 ps TT and +125.7 ps SS.

Equivalence uses two-state X/Z-as-zero RTL event semantics; four-state refinement
remains unverified. Vendor-model simulation is separate evidence.

**What changed:** SRAM-preserving synthesis and mixed-library timing are captured
in their owning repositories; Road includes spacing-aware density and bounded
routing/extraction improvements. The measured recipe runs independent checks
under explicit memory/CPU admission and starts maximal on an early immutable
GDS. Qualification requires that GDS to equal the final GDS byte for byte and
rejects repaired layouts for this schedule. Parallel RC preparation preserves
topological result/error order. The general recipe composer retains its
conservative serial default; the [recorded early schedule](../integration/ibex_sg13g2_ez130/campaigns/early-20261101/README.md)
is an explicitly selected campaign configuration.

The timer includes parsing, plans, tool I/O, mmap/lowering, extraction, repair,
checks and worker orchestration. Source builds, tool installation and acquisition
are outside it. Runs use 16 assigned logical CPUs on an i9-13900HX, an 8 GiB RAM
cap and 2 GiB swap cap. Foreign load averaged about 0.36 logical CPUs; package
temperature reached 98–100 °C with throttling. These are two single-host samples.

The original frozen ELFs remain the measurement identities. Publication-time
source checks do not constitute a new complete-flow run of the newly committed
checkout. R19 remains failed because its controls pinned the previous STA ELF;
R20 reran the two affected dual-PDK controls. Negative controls reject all 12
recorded evidence/schedule tampers with passing unmodified baselines.

**No new matched LibreLane run was made for R20/R21.** Comparative
qualification and all cross-tool speedup/memory ratios for these runs remain
unverified. The 2026-10-30 matched
comparison below keeps its original outputs, failures and timing.

[Machine-readable native results and evidence inventory](benchmarks/ibex-sg13g2-ez130-development.json) ·
[Source QA](benchmarks/evidence/ibex-20261101/sta-source-qa.json) ·
[Negative controls](benchmarks/evidence/ibex-20261101/R20-negative-controls.json)

## Historical matched comparison · 2026-10-30

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

These are the 2026-10-30 comparison's shared inputs; the host campaign's are
listed in [its method section](#host-campaign-method-and-deviations).

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

This scope applies to the 2026-10-30 comparison; the host campaign's scope is
stated in [its own section](#host-campaign-method-and-deviations).

The primary timer starts at frozen raw SystemVerilog, before parsing, elaboration and preparation. It stops after GDS and every required gate and report. Preparation, initialization, disk I/O, extraction, repair, signoff and correctness checks are all inside it, and preparation is never subtracted. Tool and PDK installation, source acquisition and harness source copies are excluded for both flows.

Each flow has one cold sample. Repetition was skipped because no ratio could be formed. Peak RSS is the largest single process: for RustEDA, the maximum over reaped worker and tool processes; for LibreLane, GNU time over the producer tree. The cgroup peak covers the whole process family including page cache. RustEDA's cgroup peak equals the 8 GiB limit, which is page cache, not a footprint.

**RustEDA run notes.** The run used `./rusteda flow` with the `ihp_sg13g2_ez130` worker configuration. `RUSTROAD_ROUTE_TRACE=1` was set, so diagnostic trace I/O counts against RustEDA. The executables were built from the repair checkouts and are identified by SHA-256 in the receipt: RustSys `e6cf0c77…`, RustSTA `b17c092b…`, RustROAD `0889b370…` (engine `0813571`) and RustEDA `af6b7ade…`.

## How LibreLane was run

In the 2026-10-30 comparison, LibreLane 3.0.17 ran as a black-box executable: the AppImage `c8ef5ae2…` with OpenROAD `dcf36133`, Yosys 0.62 and OpenSTA 3.1.0. Its environment was set to the contract (`CLOCK_UNCERTAINTY_CONSTRAINT 0.05`, `TIME_DERATING_CONSTRAINT 0`, `IO_DELAY_CONSTRAINT 10`).

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

| Module | Scope in the 2026-10-30 comparison |
| --- | --- |
| RustEDA | Flow execution, checkpoints, recovery and complete process-family accounting |
| RustSys | RTL synthesis, real-SRAM preservation, technology mapping and equivalence |
| RustSTA | Constraints, extracted timing and independent numerical checks |
| RustROAD | Floorplan, placement, CTS, routing, extraction and streamout |
| RustEDA Common | Shared models, formats, disk workspaces and compute used by the engines; included transitively |
| RustSpice | Not timed in the RTL-to-GDS main flow. The 2026-11-02 host campaign compares it with ngspice-45 on RC networks extracted from the Ibex layout ([module head-to-heads](#module-head-to-heads)); no transistor-level Ibex claim |

Older synthesis-only, small-block, OpenC910 and circuit-simulation performance tables are retired from current publication. Their source snapshots, licenses, regression fixtures and immutable qualification receipts are preserved. Sky130/GF180 correctness coverage remains required; it is not an additional current public performance campaign.

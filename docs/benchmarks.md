# Ibex on SG13G2 / EZ130

This page holds four Ibex result sets for RustEDA and its modules, newest first: the [host campaign of 2026-11-05](#ibex-host-campaign-2026-11-05), which runs the current RustEDA recipe and engines against LibreLane on a 4-vCPU cloud host, with module head-to-heads and same-load reruns; the [host campaign of 2026-11-02](#ibex-host-campaign-2026-11-02), the first such campaign on another host; the [R20/R21 native development runs of 2026-11-01](#latest-native-development-results-2026-11-01); and the [historical matched comparison of 2026-10-30](#historical-matched-comparison-2026-10-30). Each set names its own host, inputs and schedule, and times from different hosts are not comparable. The target is Ibex on the IHP SG13G2 process, using the EZ130 standard-cell library and **two real SRAM macros**. EZ130 is a cell library for SG13G2, not a separate process.

## At a glance · 2026-11-05

Ibex RTL-to-GDS on IHP SG13G2 with EZ130 cells and two SRAM macros, 2026-11-05: RustEDA and LibreLane 3.0.17 (OpenROAD, Yosys, KLayout, Magic, Netgen) ran one after the other on the same 4-vCPU host from the same prepared inputs, and the same signoff scripts checked both layouts. Commit IDs that receipts and evidence recorded before 2026-11-06 refer to the history before its commit metadata was rewritten; [commit-map.json](benchmarks/commit-map.json) maps each to its current ID (file contents are identical).

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

LibreLane's layout fails the shared timing gate (112 maximum-capacitance and fanout violations at TT and SS) and its equivalence check does not complete, so the receipt keeps its own ratio fields null under its publication rule; the multiples here divide the recorded times. Stages are paired by phase, not as identical tasks. RustEDA is not faster everywhere: its repair before global routing takes 1.18× LibreLane's two pre-route resizer steps alone, OpenRCX runs 1.36× longer on RustEDA's DEF, and the latch-up check 1.11× longer on its layout. [All stages, gates and losses](#ibex-host-campaign-2026-11-05)

## Ibex host campaign · 2026-11-05

The repository's current native recipe and engines (superproject `397bfa6`:
RustSys `86e59d8`, RustSTA `3550739`, RustROAD `1921481`, RustSpice `d4d6a50`,
RustEDA-Common `6668eec`) and LibreLane 3.0.17 Classic ran again on a 4-vCPU
cloud host from the same prepared RTL, SDC and floorplan contract, one timed
sample per flow, and the same nine independent gate scripts checked both final
layouts. **RustEDA's producer completed all 114 manifest steps, including the
1 ps RustSTA/OpenSTA parity gate (largest difference 0.1639 ps) and its own
foundry DRC, antenna DRC, LVS and macro contract, and its layout passes every
shared gate that gives a pass/fail verdict.** The ninth gate, OpenRCX
extraction, reports `extracted_not_qualified` on both layouts, as on
2026-11-02: it extracts the geometry in the DEF without verifying full-net
coverage, and its SPEF feeds the timing gate. LibreLane's layout again fails
the timing gate on 28 (TT) and
24 (SS) maximum-capacitance and 30 fanout violations, with setup and hold met,
and its equivalence stays `unverified` (433 renamed registers the checker cannot
name-match). Under the publication rule the receipt's status is
`comparison_incomplete` and its ratio fields are null; the [summary above](#at-a-glance-2026-11-05) divides the
recorded times instead, with that condition stated.
The native run is still `unqualified`: the component-qualification evidence
that the sixteen-gate composer needs is not published.

This host is a different virtual machine from the 2026-11-02 one: a Xeon at
2.80 GHz instead of 2.10 GHz, but slower in practice. With the same
configuration and tool builds, every LibreLane stage took longer than on
2026-11-02, and every LibreLane step that took 2 s or more then took 1.11× to
1.69× as long (detailed routing 2,486.85 s against 1,796.83 s, 1.38×), apart
from the first Yosys step at flow start (2.24×, 4.52 s against 2.02 s; see the
method below); some shorter steps grew more (lint 5.545 s against 0.786 s).
LibreLane's DEF and netlists are byte-identical to 2026-11-02's. Compare the
two flows within this campaign; the [comparison with
2026-11-02](#against-2026-11-02) below is history, not a speedup.

| | RustEDA | LibreLane 3.0.17 |
| --- | --- | --- |
| Flow outcome | Producer exit 0 after all 114 of 114 manifest steps, including `timing_accuracy` and its own foundry main/antenna DRC, LVS and macro contract | Exit 0 after 65 steps |
| Foundry main DRC (complete main deck, `recommended` rules disabled) | `passed`, 0 | `passed`, 0 |
| Foundry antenna DRC | `passed`, 0 | `passed`, 0 |
| Latch-up tap coverage (LU.a and LU.b, 20 µm) | `passed` (LU.a / LU.b: 0 / 0 uncertified boxes) | `passed` (LU.a / LU.b: 0 / 0 uncertified boxes) |
| Full-device LVS | `passed` (top-port identity, 2 macros verified) | `passed` (top-port identity, 2 macros verified) |
| OpenRCX extraction (SPEF for the timing gate) | `extracted_not_qualified` (no pass/fail verdict) | `extracted_not_qualified` (no pass/fail verdict) |
| Timing gate, OpenRCX SPEF | `passed` | `failed` (capacitance and fanout) |
| RustSTA/OpenSTA parity on identical SPEF, 1 ps gate | `passed`, 0.1639 ps | Not a gate of this flow |
| Vendor-model simulation | `passed` | `passed` |
| Synthesis / final equivalence | `proven` / `proven` | `unverified` / `unverified` |
| Final density | `passed`, 26.67 % | `passed`, 28.14 % |
| Flow wall time | 888.896 s (producer, all steps) | 3,409.108 s |
| Shared-gate time added | 80.66 s: latchup, simulation, logic, density; the producer ran DRC, antenna and LVS itself as the identical checks | 1,202.56 s: all nine shared gates, one at a time |
| End-to-end time | 974.19 s (1,352.37 s with its DRC, antenna and LVS charged one after another) | 4,616.30 s (4,104.97 s with its shared DRC, antenna and LVS charged on the producer's two-lane schedule) |
| Peak process RSS, flow | 2,250,408 KiB (2.15 GiB) | 2,331,184 KiB (2.22 GiB) |
| Peak process RSS, shared gates | 2,245,620 KiB (2.14 GiB) | 2,258,996 KiB (2.15 GiB) |
| Cgroup peak, flow (includes page cache) | 4,756,537,344 B (4.43 GiB) of an 8 GiB limit | 3,909,816,320 B (3.64 GiB) of a 10 GiB limit |
| Host load during the flow, 1-min mean | 1.67 | 2.98 |
| Standard-cell area | 187,585.2 µm² | 197,913.7 µm² |
| Final non-filler density (cap 70 %) | 26.67 % | 28.14 % |
| Macro-inclusive core utilization | 46.12 % | 47.13 % |
| Routed signal wirelength | 897,357.3 µm | 969,571.6 µm |
| Vias | 118,525 | 122,194 |
| TT setup / hold worst slack | 49.636 / 0.111 ns | 48.603 / 0.172 ns |
| SS setup / hold worst slack | 49.442 / 0.211 ns | 47.844 / 0.312 ns |
| Setup / hold violations, TT | 0 / 0 | 0 / 0 |
| Setup / hold violations, SS | 0 / 0 | 0 / 0 |
| Slew / capacitance / fanout violations, TT | 0 / 0 / 0 | 0 / 28 / 30 |
| Slew / capacitance / fanout violations, SS | 0 / 0 / 0 | 0 / 24 / 30 |
| Active unconstrained endpoints | 0 (156 inactive) | 0 (156 inactive) |

Timing rows come from the shared gate: an independent nominal OpenRCX extraction
of each final layout, analysed by RustSTA and OpenSTA at TT 1.20 V / 25 °C and
SS 1.08 V / 125 °C, keeping the worse value of the two. Slacks are rounded to
1 ps and percentages to 0.01 %; GiB values are conversions of the recorded KiB
and bytes. Peak process RSS is GNU time's maximum over the waited process tree;
the cgroup peak covers the whole flow including page cache. Each flow has one
timed sample. The producer ran the foundry main DRC beside the antenna DRC and
then LVS, as two two-CPU lanes; the receipt checked that each is the identical
check of the shared gate (same script, deck, rule selection, switches and GDS,
netlist and CDL bytes; `producer_identical_checks`), so the native end-to-end
time counts them once, inside the producer. RustEDA's end-to-end time is the
sv2v preparation (4.63 s, the same file for both flows), the producer and the
shared latch-up, simulation, equivalence and density gates; LibreLane's is the
preparation, its flow through `Checker.XOR` and all nine shared gates, including
OpenRCX extraction and RustSTA/OpenSTA timing on top of its in-flow RCX and STA,
as on 2026-11-02. A second producer run of the same composition after the campaign, through `be_timing` (which writes every compared artifact), produced byte-identical GDS, DEF, synthesis, final and powered netlists and native SPEF; only the OpenRCX SPEF, written by OpenROAD, differs ([determinism record](benchmarks/evidence/ibex-host-20261105/native/bringup-artifacts.json)).

### Where time goes · 2026-11-05

Stage seconds come from each flow's own step records (`stage_seconds` in the
receipt), paired by phase as on 2026-11-02. Pairs are not identical work: the
netlists, the repair around each stage and the tools differ. LibreLane's resizer
row holds all four of its repair steps, as on 2026-11-02; the two rows under it
split it into the two steps before global routing (design repair after global
placement, timing repair after CTS) and the two after global routing (design
and timing repair), which also run before detailed routing.

| Phase | RustEDA stage | s | LibreLane stage | s |
| --- | --- | ---: | --- | ---: |
| Synthesis | RustSys | 3.48 | Yosys + ABC | 132.63 |
| Checks | — |  | lint and checks | 5.59 |
| Floorplan | — |  | floorplan, PDN, taps, IO | 16.14 |
| Placement and CTS | placement and CTS (RustROAD) | 28.00 | placement (global + detailed) | 19.69 |
|  |  |  | CTS | 27.36 |
| Repair before detailed routing | frontend STA, ECO rounds and planning helpers (RustSTA, Python) | 26.04 | resizer repair (design/timing), all four steps | 52.36 |
|  |  |  | of which the two steps before global routing | 21.69 |
|  |  |  | of which the two post-GRT steps | 30.67 |
| Routing | routing (RustROAD) | 302.24 | global routing and antenna repair | 83.34 |
|  |  |  | detailed routing | 2,486.85 |
| Repair after routing | post-route repair | 7.34 | — |  |
|  | antenna repair and verification | 18.99 |  |  |
| Static timing | signoff timing (RustSTA + OpenSTA) | 35.42 | STA (OpenSTA, pre/mid/post) | 183.31 |
| Extraction | native extraction (RustROAD) | 6.25 | fill, RCX, IR drop, reports | 47.73 |
|  | OpenRCX extraction | 6.83 |  |  |
| Export | GDS/DEF/netlist export | 2.09 | streamout, render, LEF, XOR | 351.18 |
| Foundry checks | foundry DRC beside antenna then LVS, macro contract | 449.80 | — (shared gates only) |  |
| Other | renders, profiles, physical inputs | 0.18 | other | 0.00 |
| **Flow wall time** | | **888.896** | | **3,409.108** |

RustEDA's routing is one `be_route` step covering global and detailed routing.
Its foundry-check row runs the checks that LibreLane leaves to the shared gates;
LibreLane's export row includes Magic LEF writing (197.146 s) and the KLayout
XOR (76.186 s). Every paired phase is shorter on the RustEDA side except two:

- **Repair before global routing.** The frontend STA and ECO stage (26.04 s)
  is shorter than LibreLane's four resizer steps (52.36 s) but longer than its
  two steps before global routing alone (21.69 s). In 5 interleaved same-load repetitions (a frontend-only producer run with the campaign's composition against LibreLane's four resizer steps replayed as a black box from the campaign run's own inputs, with byte-identical DEF and netlist outputs), the frontend stage took a median 26.60 s (25.83–27.39 s) against 54.28 s for all four steps (0.49×) and 22.48 s (21.24–22.77 s) for the two before global routing (1.18×; 1.24× against LibreLane's own step runtimes, which exclude process start-up). **Against those two steps alone RustEDA is not faster.** The frontend stage also re-times the whole design with RustSTA after each ECO round and uses a median 42.78 s of CPU against 22.09 s for the two steps (each total is the median of the per-repetition sums).
- **OpenRCX inside the flows.** `be_openrcx` (6.79 s of the 6.83 s OpenRCX row)
  runs the same OpenRCX script on the native DEF that LibreLane's RCX step runs
  on its own (5.557 s), so it is slower; see the extraction gate below.

### Shared gates

The receipt's gate times are one sample each, taken in the campaign. After the
campaign, both layouts went through each gate again, interleaved and at the same
load (five interleaved runs per layout for gates under a minute, three for DRC, antenna DRC and LVS, each through the campaign's own `run_signoff.sh --gates` under the same executor); the verdict column uses those runs: `win` or `loss` when
the two arms' ranges do not overlap, `parity` when they do. Each run held an
exclusive host lock, so no other measured job ran beside it; light operator
activity was not excluded (see the method below).

| Shared gate | RustEDA layout, campaign s | LibreLane layout, campaign s | 2026-11-02 RustEDA / LibreLane s | Same-load A/B median, RustEDA / LibreLane s (n) | RustEDA ÷ LibreLane (A/B) | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Foundry main DRC | 474.02 | 573.60 | 606.64 / 417.55 | 402.65 / 534.24 (3) | 0.75× | win |
| Foundry antenna DRC | 212.27 | 292.08 | 380.55 / 205.07 | 198.16 / 253.81 (3) | 0.78× | win |
| Latch-up tap coverage | 53.41 | 53.23 | — / — | 50.54 / 45.47 (5) | 1.11× | loss |
| OpenRCX extraction | 6.94 | 5.09 | 17.23 / 3.88 | 6.57 / 4.84 (5) | 1.36× | loss |
| RustSTA + OpenSTA timing | 28.30 | 30.91 | 42.19 / 27.88 | 27.90 / 29.14 (5) | 0.96× | parity |
| Full-device LVS | 146.09 | 219.26 | 265.76 / 162.51 | 147.15 / 207.09 (3) | 0.71× | win |
| Vendor-model simulation | 4.88 | 6.95 | 4.49 / 5.30 | 5.24 / 7.95 (5) | 0.66× | win |
| Synthesis + final equivalence | 22.16 | 21.21 | 19.42 / 16.41 | — | — | no valid comparison |
| Final density | 0.21 | 0.25 | 0.25 / 0.20 | 0.21 / 0.26 (5) | 0.82× | win |

Foundry main DRC is faster on the native layout: median 402.65 s against 534.24 s (0.75×; ranges 398.98–403.29 and 532.38–543.80 s, 3 runs each). Antenna DRC is faster on the native layout: median 198.16 s against 253.81 s (0.78×; ranges 195.97–199.40 and 243.02–255.05 s, 3 runs each). LVS is faster on the native layout: median 147.15 s against 207.09 s (0.71×; ranges 144.03–155.16 and 206.20–210.20 s, 3 runs each). Simulation is faster on the native layout: median 5.24 s against 7.95 s (0.66×; ranges 4.83–5.64 and 7.10–8.81 s, 5 runs each). Density is faster on the native layout: median 0.21 s against 0.26 s (0.82×; ranges 0.20–0.21 and 0.26–0.27 s, 5 runs each). Timing is at parity: median 27.90 s against 29.14 s (0.96×; ranges 26.97–30.01 and 28.03–29.64 s, 5 runs each). Latch-up is **slower on the native layout**: median 50.54 s against 45.47 s (1.11×; ranges 49.58–52.77 and 44.51–46.74 s, 5 runs each). OpenRCX extraction is **slower on the native layout**: median 6.57 s against 4.84 s (1.36×; ranges 6.35–6.74 and 4.58–5.04 s, 5 runs each). The logic gate has no valid comparison: on LibreLane's layout its synthesis and final equivalence do not complete (`unverified`), so its time is not the same work. The latch-up gate was added to the shared gates after 2026-11-02 (nine gate scripts instead of eight), so it has no 2026-11-02 time, and the OpenRCX extraction gate gives no pass/fail verdict on either layout (`extracted_not_qualified`); its row compares run time only.

### Physical QoR

| | RustEDA 2026-11-05 | LibreLane 2026-11-05 | RustEDA 2026-11-02 | LibreLane 2026-11-02 |
| --- | ---: | ---: | ---: | ---: |
| Standard-cell area | 187,585.2 µm² | 197,913.7 µm² | 233,909.2 µm² | 197,913.7 µm² |
| Final non-filler density | 26.67 % | 28.14 % | 33.25 % | 28.14 % |
| Routed signal wirelength | 897,357.3 µm | 969,571.6 µm | 1,679,076.6 µm | 969,571.6 µm |
| Vias | 118,525 | 122,194 | 301,099 | 122,194 |
| Routed nets | 14,178 | 15,227 | 16,329 | 15,227 |
| Standard cells | 14,058 | 15,191 | 16,208 | 15,191 |
| Antenna diodes | 52 | 504 | 52 | 504 |
| Taps | 5,934 | 6,900 | 13,923 | 6,900 |
| Fillers | 34,322 | 44,672 | 55,651 | 44,672 |

The native layout has less standard-cell area, less routed wire and fewer vias
than LibreLane's, with fewer cells, taps, antenna diodes and fillers, from a
different netlist (RustSys against Yosys + ABC). Its GDS is 26,682,872 B against
LibreLane's 17,717,408 B, because the recipe streams merged rectangles and flat
vias, which the foundry checks read faster. LibreLane's layout keeps more hold
margin (TT 0.172 ns against 0.111 ns); both meet hold at both corners with no
violation.

### Module head-to-heads · 2026-11-05

Each head-to-head runs both contenders on the same input on this host, with one
warm-up and three measured repetitions, interleaved, unless stated otherwise.
Times are medians, with the fastest run in parentheses. A quotient divides the
slower median by the faster one and applies to that task only.

#### RustSys vs Yosys · 2026-11-05

Same task and tools as on 2026-11-02: the prepared Ibex RTL mapped onto EZ130 TT
and SRAM Liberty; RustSys runs the native flow's workspace sequence (2 threads)
and its direct mode (all CPUs), Yosys 0.70 runs LibreLane 3.0.17's synthesis
script with ABC; QoR comes from `yosys stat -liberty` and the harness's
longest combinational cell path.

| | RustSys | Yosys 0.70 + ABC, LibreLane script |
| --- | --- | --- |
| Median wall, workspace sequence | 3.81 s (3.62 s) | 135.04 s (126.82 s) |
| Median wall, direct mode | 3.04 s (2.75 s) | — |
| Quotient, Yosys ÷ RustSys | 35.4× (sequence), 44.4× (direct) | — |
| Worst peak RSS | 116,200 KiB (sequence), 115,052 KiB (direct) | 766,036 KiB |
| Standard-cell area | 145,226.592 µm² (-1.79 %) | 147,866.947 µm² |
| Area including both SRAM macros | 425,591.9808 µm² | 428,232.336 µm² |
| Logic depth in cells (excluding buffers) | 36 (36) | 40 (40) |
| Cells: total / sequential / buffer and inverter / other / macro | 11,433 / 1,998 / 950 / 8,483 / 2 | 12,605 / 1,905 / 186 / 10,512 / 2 |
| Equivalence of each flow's synthesis netlist to RTL (shared gate) | `proven` | `unverified` |

RustSys is slower than on 2026-11-02 (3.81 s against 2.12 s for the sequence,
more than the host effect: Yosys took 135.04 s against 102.83 s); its quality
changes cost work (RustSys's changelog records 29.0 G instead of 22.0 G
instructions for a single-thread Ibex synthesis). Its netlist is now smaller
and shallower than
Yosys's (it was 2.18 % larger and 55 levels deep). The equivalence row comes
from each flow's shared gate: the native flow's synthesis netlist is `proven`,
and LibreLane's synthesis netlist, byte-identical to the head-to-head Yosys
netlist, is `unverified`. The head-to-head RustSys netlist differs in bytes from
the flow's and was not checked separately.

#### RustSTA vs OpenSTA · 2026-11-05

Both analyse the final native netlist with the native SPEF, Liberty and SDC at
TT and SS, at 2 and 4 threads; RustSTA runs init, import, one run covering both
corners and 8 reports, OpenSTA one process per corner; wall time sums each
contender's processes.

| | RustSTA 0.4.3 | OpenSTA 3.1.0 |
| --- | --- | --- |
| Median wall, 2 threads | 6.825 s (6.685 s) | 11.168 s (10.567 s) |
| Median wall, 4 threads | 5.317 s (5.171 s) | 10.566 s (9.875 s) |
| Quotient, OpenSTA ÷ RustSTA | 1.64× (2 threads), 1.99× (4 threads) | — |
| CPU time per run, 2 / 4 threads | 8.84–10.02 s / 9.71–10.00 s | 12.73–14.71 s / 13.92–15.66 s |
| Worst peak RSS, 2 / 4 threads | 148,164 / 169,188 KiB | 180,320 / 180,528 KiB |
| Maximum parity difference, identical SPEF (producer `timing_accuracy` gate) | `passed`, 0.1639 ps (all eight compared values 0.0057–0.1639 ps) | — |

RustSTA is faster than OpenSTA on this task at both thread counts and uses less
memory; on 2026-11-02, on another host and layout, it was slower (1.17–1.18×)
with 1.9× OpenSTA's peak RSS. The parity margin belongs to the new layout, not
to a RustSTA change: re-timed with this campaign's binaries on a fresh OpenRCX
SPEF of the 2026-11-02 layout, TT setup worst slack still differs by 1.2838 ps,
as it did then ([record](benchmarks/evidence/ibex-host-20261105-same-load/old-layout-parity.json)).

#### RustROAD extraction vs OpenRCX · 2026-11-05

Both extract the final native routed DEF at the nominal corner. Unlike
2026-11-02, the recipe's `extract` step passes `--threads 4`, and the replay sets
RustROAD's `--threads` to each setting, so the 2- and 4-thread rows ran 2 and 4
threads; OpenRCX ran with `set_thread_count` 2 and 4.

| | RustROAD 0.5.0 | OpenRCX |
| --- | --- | --- |
| Median wall, 2 threads | 10.121 s (9.783 s) | 5.988 s (5.970 s) |
| Median wall, 4 threads | 6.260 s (5.920 s) | 5.944 s (5.912 s) |
| Quotient, RustROAD ÷ OpenRCX | 1.69× (2 threads), 1.05× (4 threads; ranges overlap) | — |
| CPU time per run, 2 / 4 threads | 17.76–18.94 s / 17.79–19.05 s | 5.91–6.45 s / 5.88–6.24 s |
| Worst peak RSS, 2 / 4 threads | 299,628 / 308,656 KiB | 211,480 / 212,012 KiB |
| SPEF nets | 14,178 | 14,178 |
| SPEF total capacitance | 162.695 pF | 153.653 pF |
| SPEF total resistance | 2,752.769 kΩ | 2,769.870 kΩ |

At 4 threads, the recipe's setting, RustROAD is level with OpenRCX (the ranges
overlap); at 2 threads it is slower. It uses about three times OpenRCX's CPU time
and 1.42–1.46× its peak RSS. Its total capacitance is 5.9 % above OpenRCX's and
its total resistance 0.6 % below. On 2026-11-02 the recorded command ran on a
2-thread pool and took 9.0× OpenRCX's time.

#### RustROAD GDS export vs KLayout · 2026-11-05

Both stream the final native DEF to GDS: RustROAD `export-gds` (the flow's
recorded `be_gds` command, merged rectangles and flat vias, on its default
2-thread pool) against KLayout 0.30.12 running RustROAD's `kl_def2gds.py` with
the tech LEF, cell LEF, combined cell GDS and IHP layer map. The two GDS files
are not byte-equivalent and the receipt records no geometric comparison, so this
is a timing comparison only.

| | RustROAD export | KLayout DEF→GDS |
| --- | --- | --- |
| Median wall | 0.831 s (0.799 s) | 1.938 s (1.730 s) |
| Quotient, KLayout ÷ RustROAD | 2.33× | — |
| Worst peak RSS | 105,316 KiB | 207,728 KiB |
| Output size | 26,682,872 B | 25,363,292 B |

RustROAD's export is faster than KLayout's DEF reader and streamout (KLayout
2.33×), with half its peak RSS; on 2026-11-02 RustROAD took 1.32× KLayout's time.
The recipe's export now writes merged rectangles and flat vias.

#### RustROAD place, CTS and route vs OpenROAD · 2026-11-05

From the two flows above; the netlists and the repair around each stage differ,
so this is not an identical-task comparison; the summary at the top pairs these
stages by phase.

| | RustROAD (native recipe) | OpenROAD 26Q3 (LibreLane Classic) |
| --- | --- | --- |
| Floorplan, PDN, taps, IO | — (no separate stage recorded) | 16.14 s |
| Placement and CTS | 28.00 s, one stage | 19.69 s placement (global + detailed) + 27.36 s CTS |
| Repair before detailed routing | 26.04 s frontend STA, ECO rounds and planning helpers | 52.36 s resizer repair (21.69 s before global routing + 30.67 s after) |
| Routing | 302.24 s (`be_route`, global and detailed, 4 threads) | 83.34 s global routing with antenna checks and repair + 2,486.85 s detailed routing (`DRT_THREADS=4`) |
| Repair after routing | 7.34 s post-route repair + 18.99 s antenna repair and verification | — |
| Standard-cell area | 187,585.2 µm² | 197,913.7 µm² |
| Standard cells / antenna diodes / taps / fillers | 14,058 / 52 / 5,934 / 34,322 | 15,191 / 504 / 6,900 / 44,672 |
| Routed nets | 14,178 | 15,227 |
| Routed signal wirelength | 897,357.3 µm | 969,571.6 µm |
| Vias | 118,525 | 122,194 |
| Slew / capacitance / fanout violations | 0 / 0 / 0 TT, 0 / 0 / 0 SS | 0 / 28 / 30 TT, 0 / 24 / 30 SS |

#### RustSpice vs ngspice-45 · 2026-11-05

Same method as on 2026-11-02 (linear RC decks from the native nominal SPEF of
the final layout, judged against a tightly converged ngspice reference with max
error / swing ≤ 2 × 10⁻³, seven interleaved repetitions after a correctness
warm-up, against ngspice's SPARSE and KLU solvers). The new layout's largest nets
are smaller, so the decks differ from 2026-11-02's: the eight largest single nets
have 198–291 unknowns (5–10 sinks), and the two multi-net decks 2,262 and 6,865.

| | 8 single nets | 10 nets, 2,262 unknowns | 40 nets, 6,865 unknowns |
| --- | --- | --- | --- |
| Accuracy verdict | `pass` × 8 | `pass` | `pass` |
| Error / swing, RustSpice | 4.67 × 10⁻⁶ – 1.33 × 10⁻⁵ | 4.50 × 10⁻⁴ | 4.50 × 10⁻⁴ |
| Error / swing, default-tolerance ngspice, nominal grid | 2.09–6.26 × 10⁻⁵ | 1.44 × 10⁻³ | 1.44 × 10⁻³ |
| Error / swing, default-tolerance ngspice, its own output points | 6.68 × 10⁻⁶ – 1.61 × 10⁻⁵ | 4.83 × 10⁻⁴ | 4.83 × 10⁻⁴ |
| ngspice SPARSE, median per-deck wall | 287.99–419.83 ms | 434.57 ms | 1,696.30 ms |
| ngspice KLU, median per-deck wall | 305.54–501.89 ms | 494.66 ms | 1,688.21 ms |
| RustSpice, median per-deck wall | 57.17–90.02 ms | 86.22 ms | 283.11 ms |
| Quotient, faster ngspice ÷ RustSpice, per-deck wall (median) | 4.32–5.45× | 5.04× | 5.96× |
| Quotient, faster ngspice ÷ RustSpice, analysis time only (median) | 5.07–6.36× | 6.39× | 7.72× |
| Median peak RSS, ngspice SPARSE / RustSpice | 13,872–14,324 / 6,828–7,208 KiB | 20,488 / 12,380 KiB | 34,968 / 24,552 KiB |

**The suite verdict is `PASS`: 10 of 10 cases pass.** Read at the nominal
`k × tstep` output grid, as the suite compares them, RustSpice is closer to the
reference than default-tolerance ngspice in every case. At each simulator's own
output points, which removes the harness's linear resampling of ngspice's
waveform, RustSpice is closer in 9 of 10 cases: ngspice is closer on
`ibex_net_01` (1.03 × 10⁻⁵ against 1.33 × 10⁻⁵), and on the two multi-net decks
the two are near level (4.50 × 10⁻⁴ against 4.83 × 10⁻⁴). All errors lie above
the reference's own resampling error. RustSpice also has the lower median peak
RSS in every case. The suite flags several medians as `noisy` (relative
spread above 0.2) and recorded a load average of 0.6 at the start and 1.0 at the
end, so the quotients hold within this session only. Process start-up medians are 12.35 ms for ngspice and
4.20 ms for RustSpice.

#### RustEDA-Common · 2026-11-05

RustEDA-Common is compiled into RustSys and RustSTA, including the `rusteda`
workspace CLI used by the producer's timing steps and by the shared timing gate;
it has no separate head-to-head.

### Where RustEDA still loses or is at parity

Every stage or gate where RustEDA is not faster, or not better, in this campaign:

- **Repair before global routing** (loss). The frontend STA and ECO stage takes
  1.18× the time of LibreLane's two resizer steps before global routing in
  same-load runs (26.60 s against 22.48 s), although it takes 0.49× the time
  of all four of LibreLane's repair steps (the two after global routing repair
  design and timing on global-route parasitics; RustEDA's design repair on
  extracted parasitics runs after detailed routing, in its 7.34 s post-route
  repair). Cause: the stage
  re-times the whole design after each ECO round (six complete RustSTA runs,
  plus placement passes in between), where each of LibreLane's two steps is one
  OpenROAD process; it spends a median 42.78 s of CPU against 22.09 s.
- **OpenRCX extraction of the native layout** (loss, 1.36× in the shared
  gate, and 6.79 s against 5.557 s for the flows' own RCX steps). OpenRCX's time
  is consistent with the number of wire pieces it extracts, which the route
  topology sets:
  the native DEF has 192,328 pieces against 129,901 (RustROAD's
  `scripts/def_pieces.py`), with 42,789 staircase and 39,398 zigzag pieces
  against 25,925 and 17,576, although it has less wire and fewer vias.
- **Latch-up tap coverage** (loss, 1.11×). Its run time follows
  the shape of the n-well domains: 71 of the native layout's 251 LU.a domains
  are notched, against 24 of 197 on LibreLane's, mostly by the mux-input reset
  flop `DFFMRQX2` that synthesis selects (RustROAD `docs/tap-integration.md`).
- **Timing gate** (parity, 0.96×): RustSTA plus OpenSTA on each
  layout's OpenRCX SPEF, where RustEDA's layout is not faster in same-load runs.
- **RustROAD extraction against OpenRCX** (module head-to-head): level at
  4 threads (6.260 s against 5.944 s, overlapping ranges) and slower at
  2 threads (1.69×), with about three times OpenRCX's CPU time and 1.42–1.46×
  its peak RSS.
- **Equivalence gate** (no valid comparison): LibreLane's synthesis and final
  equivalence do not complete (`unverified`), so the two gate times are not the
  same work.
- **Hold margin** (QoR): the native layout's hold worst slack is 0.111 ns at TT
  and 0.211 ns at SS against LibreLane's 0.172 ns and 0.312 ns; both meet hold
  with no violation.
- **GDS size**: 26,682,872 B against 17,717,408 B, because the recipe streams
  merged rectangles and flat vias, which make the foundry checks faster.
- **Flow memory** (cgroup peak): the producer's cgroup, page cache included,
  peaked at 4.43 GiB against LibreLane's 3.64 GiB, while its largest process
  (2.15 GiB) stayed below LibreLane's (2.22 GiB).
- **Receipt ratios.** The receipt keeps its ratio fields null, because LibreLane
  fails the timing gate and its equivalence is unverified; LibreLane's floorplan, checks and IR-drop steps have no
  native counterpart in the stage table.


### Against 2026-11-02

| RustEDA stage | 2026-11-02 s | 2026-11-05 s |
| --- | ---: | ---: |
| synthesis (RustSys) | 2.37 | 3.48 |
| placement and CTS (RustROAD) | 20.92 | 28.00 |
| frontend STA, ECO rounds and planning helpers (RustSTA, Python) | 64.65 | 26.04 |
| routing (RustROAD) | 389.63 | 302.24 |
| post-route repair | 179.00 | 7.34 |
| antenna repair and verification | 409.35 | 18.99 |
| GDS/DEF/netlist export | 19.04 | 2.09 |
| native extraction (RustROAD) | 161.48 | 6.25 |
| OpenRCX extraction | 18.02 | 6.83 |
| signoff timing (RustSTA + OpenSTA) | 64.09 | 35.42 |
| foundry DRC, LVS, macro contract | 0.00 | 449.80 |
| **Producer wall** | **1,329.616** | **888.896** |

| LibreLane stage | 2026-11-02 s | 2026-11-05 s |
| --- | ---: | ---: |
| synthesis (Yosys + ABC) | 110.61 | 132.63 |
| lint and checks | 0.82 | 5.59 |
| floorplan, PDN, taps, IO | 10.56 | 16.14 |
| placement (global + detailed) | 16.30 | 19.69 |
| resizer repair (design/timing) | 39.92 | 52.36 |
| CTS | 20.17 | 27.36 |
| global routing and antenna repair | 68.98 | 83.34 |
| detailed routing | 1,796.83 | 2,486.85 |
| STA (OpenSTA, pre/mid/post) | 114.35 | 183.31 |
| fill, RCX, IR drop, reports | 34.66 | 47.73 |
| streamout, render, LEF, XOR | 230.69 | 351.18 |
| other | 0.00 | 0.00 |
| **Flow wall** | **2,444.849** | **3,409.108** |

The 2026-11-02 producer stopped at the parity gate after 114 of its 119 steps
and did not run the foundry checks; this campaign's producer ran all 114 steps
of the current manifest, including those checks (449.80 s on two lanes). With
the same LibreLane configuration, tool builds and inputs, every LibreLane stage
took longer on this host than on 2026-11-02's (flow 3,409.108 s against
2,444.849 s), while every RustEDA stage that ran on both dates, except
synthesis (3.48 s against 2.37 s, more work per synthesis) and placement and
CTS (28.00 s against 20.92 s), took less (renders and profiles aside: 0.18 s
against 0.17 s), by up to 26× (native extraction, 6.25 s against 161.48 s). The native layout changed with the recipe: 187,585.2 µm²
against 233,909.2 µm², 897,357.3 µm of wire against 1,679,076.6 µm and 118,525
vias against 301,099; LibreLane's DEF and netlists are byte-identical. The shared gates
that ran on both dates took less on the native layout than on 2026-11-02,
except simulation and logic (single samples). The latch-up gate was added to
the shared gates after 2026-11-02 (nine gate scripts instead of eight), so it
has no 2026-11-02 time, and LibreLane's end-to-end time now includes it
(53.23 s). RustSTA/OpenSTA parity moved from 1.4052 ps (failed) to
0.1639 ps (passed) with the layout.


### Host campaign method and deviations · 2026-11-05

- **Host.** Intel Xeon @ 2.80 GHz, 4 logical CPUs, 15.7 GiB RAM
  (16,867,418,112 B), kernel 6.18.44-fc-v236 (receipt `host`, captured on the
  campaign host), cgroup v1 memory controller, no swap device and no systemd
  user manager. Each flow had all 4 vCPUs, and each timed stage ran in its own
  child cgroup after the one-minute load average fell below 0.5, with no other
  measured job. Operator activity was not fully excluded: creating scratch
  copies of the documentation repositories (about 63 MB written in about 11 s)
  overlapped the native `be_route` step, which took 302.24 s against 304.91 s in
  the second producer run after the campaign, and a 1.8 MB copy overlapped
  LibreLane's first Yosys step at flow start (4.52 s against 2.02 s on
  2026-11-02). Memory
  caps: RustEDA producer 8 GiB RAM plus a 2 GiB swap allowance in memory+swap
  accounting, LibreLane flow 10 GiB RAM, shared gates 8 GiB on both layouts.
- **Binaries.** Every engine was built from the commits pinned by superproject
  `397bfa6` with clean module checkouts, offline and `--locked`, into the
  target directories the harness names (rustc 1.97.1). RustSys, RustSTA and the
  pin-cap exporter are byte-identical to the binaries of the pre-campaign
  integration runs; the receipt identifies each executable by SHA-256.
- **Samples.** One timed sample per flow, run by `final_campaign.sh` without
  interruption or rerun: native producer, shared gates on its layout, LibreLane
  through `Checker.XOR`, shared gates on its layout, then the synthesis, module
  and SPICE head-to-heads. Module head-to-heads use one warm-up plus three
  interleaved measured repetitions; RustSpice uses a correctness warm-up plus
  seven. The same-load comparisons after the campaign are reported separately
  from the receipt's single samples ([summary](benchmarks/evidence/ibex-host-20261105-same-load/summary.json)). Each of those runs held an exclusive host lock; light operator activity beside some of them (documentation editing, a short DEF statistics script at the start of the third repair repetition, and a 170 MB archive extraction during the first native DRC repetition, 402.65 s, the median) was not excluded and changes no verdict.
- **Design and inputs.** As on 2026-11-02: lowRISC Ibex v2.1.0 at `38c07093`,
  default RV32IMC, top `ibex_sram_bench`, two `RM_IHPSG13_1P_1024x32_c2_bm_bist`
  SRAMs; IHP Open PDK at `5cccb161`; 100 ns clock, TT and SS, nominal RC. The
  preparation steps were re-run into a new campaign directory from the same
  checkouts and EZ130 tree: sv2v 0.0.13.1 converted the RTL in 4.63 s at
  286,200 KiB into the same file as on 2026-11-02 (`da905268…`), and the
  testbench, SDC, PDN script and LibreLane configuration are byte-identical up
  to the campaign directory's paths. The prepared contract differs from
  2026-11-02's only by the recipe's committed `native_tap_max_gap_um` (42 µm).
- **EZ130 reconstruction.** Unchanged: 5 of the 7 contract-pinned EZ130 files
  are byte-identical; the SG13G2 tech LEF and cell GDS are reconstructed from
  public sources ([restore script](../integration/ibex_host_benchmark/ez130/restore.sh)).
- **Tools.** Unchanged nixpkgs builds: OpenROAD 26Q3 (with OpenSTA 3.1.0 and
  OpenRCX), Yosys 0.70, KLayout 0.30.12, Magic 8.3.689, Netgen 1.5.325, Icarus
  Verilog 13.0, ngspice-45 and sv2v 0.0.13.1; LibreLane 3.0.17 from PyPI.
- **Native composition.** The repository recipe at `397bfa6`, composed by
  `compose_native.py` with every file and tool pinned by path, SHA-256 and size,
  the grouped single-process antenna session, RustROAD route, extraction,
  post-route repair and antenna repair at `--threads 4`, frontend placement and
  CTS at `--threads 2`, `export-gds` on RustROAD's default 2-thread pool, and the
  producer's foundry checks as two concurrent two-CPU lanes (`be_main` beside
  `be_antenna` then `be_lvs`). The backend prefix manifest has 114 steps (119 on
  2026-11-02; the concurrent frontend merged steps). Without the unpublished
  component-qualification evidence the eight derived qualification gates are not
  produced, so the run is `unqualified`. The prefix manifest runs under
  `--stop-after be_macro_contract`, as on 2026-11-02, so the producer writes its
  prefix summary (`status` `frontend_completed`, with a fixed `unverified_items`
  list); the step verdicts come from the step outputs and the receipt's
  identity checks, and the layout verdicts from the shared gates.
- **LibreLane configuration.** Unchanged: Classic flow through `Checker.XOR` on
  the prepared contract config with `DRT_THREADS=4` and its own post-GRT design
  repair (`RUN_POST_GRT_DESIGN_REPAIR=true`). No prepared-config diagnostic run
  was repeated (2026-11-02's is in that receipt).
- **Host-local helper patches.** The 2026-11-02 patches, minus the two
  `antenna_session/` thread allow-list edits that the repository now carries:
  thread allow-lists accept 4, the producer and LVS helper read the cgroup-v1
  memory controller and accept the executor's own cgroup, the RC-profile builder
  takes the explicit calibration rules path, and the density gate's DEF parser
  is patched. The run wrappers also keep Python from writing bytecode into module
  script directories.
- **Hardware.** 4 vCPUs of a 2.80 GHz Xeon instead of 16 logical CPUs of an
  i9-13900HX; a different virtual machine from 2026-11-02's, on which every
  LibreLane stage took longer.

[Receipt and evidence inventory](benchmarks/ibex-sg13g2-ez130-host-20261105.json) ·
[Same-load comparisons](benchmarks/evidence/ibex-host-20261105-same-load/summary.json) ·
[Harness and host deviations](../integration/ibex_host_benchmark/README.md) ·
[RustSpice suite report](benchmarks/evidence/ibex-host-20261105/modules/spice-suite.md)

## Ibex host campaign · 2026-11-02

This section is the first host campaign, kept as history; the
[2026-11-05 campaign](#ibex-host-campaign-2026-11-05) above supersedes it.

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
| Configuration | Published native recipe (run16-class) composed by `compose_native.py`; RustROAD route, post-route repair and antenna repair at `--threads 4`, frontend placement/CTS at `--threads 2`, `extract`, `export-gds` and the other export/inspect steps on its default 2-thread pool; `unqualified` | Classic flow through `Checker.XOR`, prepared contract config, its own post-GRT design repair (`RUN_POST_GRT_DESIGN_REPAIR=true`), `DRT_THREADS=4` |
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
from the benchmark netlists, which were not checked separately. The receipt
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
| Maximum parity difference, identical SPEF (producer `timing_accuracy` gate) | **1.4052 ps**: TT setup worst slack on native SPEF (49.279521 vs 49.280926 ns) | — |
| Other values above 1 ps | 1.2838 ps: TT setup worst slack on OpenRCX SPEF | — |
| Remaining six compared values | 0.036–0.2153 ps | — |

The parity rows come from the producer's `timing_accuracy` gate on the native
and OpenRCX SPEF (receipt `modules.timing.agreement`), not from the timed
replays above.

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
| Worst peak RSS, 2-thread / 4-thread setting | 810,452 / 810,632 KiB | 295,860 / 295,728 KiB |
| SPEF nets | 16,329 | 16,329 |
| SPEF total capacitance | 312.638 pF | 290.705 pF |
| SPEF total resistance | 6,633.54 kΩ | 6,691.924 kΩ |

RustROAD's total capacitance is 7.5 % above OpenRCX's and its total resistance
0.9 % below. Its CPU time tracks its wall time in both settings (both on its 2-thread pool).

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
ngspice solver in every case. On the single nets, default-tolerance ngspice's measured
error is lower, but both errors are below the reference's own resampling error
(`interp_floor`), so the difference is not resolved; on the two multi-net
decks, RustSpice is closer. RustSpice's median peak RSS is 1.5–2.5× ngspice's; ngspice's peak is
the maximum of `/proc` polls, which the suite flags as a lower bound of the
true peak (`rss_lower_bound`). Process start-up medians are 6.77 ms for
ngspice and 2.47 ms for RustSpice. The suite records a shared host, with a
load average of 0.4 at the start and 1.0 at the end, so its quotients hold
within this session only.

#### RustEDA-Common

RustEDA-Common is compiled into RustSys and RustSTA, including the `rusteda`
workspace CLI used by the native producer's timing steps and by the shared
timing gate applied to both layouts; RustROAD and RustSpice do not
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
  producer 8 GiB RAM plus a 2 GiB swap allowance in memory+swap accounting
  (10 GiB memsw), as in the recipe; LibreLane flow 10 GiB RAM, because its
  historical host had 8 GiB plus 2 GiB swap and this host has no swap; the
  shared gates on both layouts ran under an 8 GiB cap.
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
  pinned by path, SHA-256 and size, a grouped single-process antenna session,
  RustROAD route, post-route repair and antenna repair at `--threads 4`,
  frontend placement/CTS at `--threads 2`, and `extract`, `export-gds` and the
  other export/inspect steps, which pass no `--threads`, on RustROAD's default
  2-thread pool. The 16-gate composer needs
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

These are the 2026-10-30 comparison's shared inputs; the host campaigns' are
listed in their method sections ([2026-11-05](#host-campaign-method-and-deviations-2026-11-05),
[2026-11-02](#host-campaign-method-and-deviations)).

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

This scope applies to the 2026-10-30 comparison; the host campaigns' scopes are
stated in their own sections ([2026-11-05](#host-campaign-method-and-deviations-2026-11-05),
[2026-11-02](#host-campaign-method-and-deviations)).

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
| RustEDA Common | Shared models, formats, disk workspaces and compute used by RustSys and RustSTA; included transitively |
| RustSpice | Not timed in the RTL-to-GDS main flow. The host campaigns compare it with ngspice-45 on RC networks extracted from the Ibex layouts (module head-to-heads of [2026-11-05](#module-head-to-heads-2026-11-05) and [2026-11-02](#module-head-to-heads)); no transistor-level Ibex claim |

Older synthesis-only, small-block, OpenC910 and circuit-simulation performance tables are retired from current publication. Their source snapshots, licenses, regression fixtures and immutable qualification receipts are preserved. Sky130/GF180 correctness coverage remains required; it is not an additional current public performance campaign.

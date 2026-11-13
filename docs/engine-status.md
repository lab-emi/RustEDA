# Engine development

Updated **2026-11-05**. RustEDA’s five engine repositories keep independent
Git histories. The superproject pins their source revisions; flow, CLI and
workbench live directly in `apps/`. Version labels describe source previews.

The [2026-11-05 host campaign](benchmarks.md#ibex-host-campaign-2026-11-05)
rebuilt Ibex on SG13G2/EZ130 with two real IHP SRAMs on a 4-vCPU cloud host
(Xeon 2.80 GHz, 15.7 GiB RAM, no swap; slower than the 2026-11-02 host: every
LibreLane stage took longer) with the engine revisions that superproject `397bfa6` pins
(RustSys `86e59d8`, RustSTA `3550739`, RustROAD `1921481`, RustSpice `d4d6a50`,
RustEDA-Common `6668eec`). The native producer completed all 114 steps and its
layout passed every shared gate with a pass/fail verdict (the OpenRCX
extraction gate reports `extracted_not_qualified` on both layouts); LibreLane's
layout failed the timing gate and its equivalence is `unverified`, so no
end-to-end ratio is published. Each flow has one timed sample.

The [2026-11-02 host campaign](benchmarks.md#ibex-host-campaign-2026-11-02)
built Ibex on SG13G2/EZ130 with two real IHP SRAMs on a 4-vCPU cloud host
(Xeon 2.1 GHz in the measurement-time evidence, 15.7 GiB RAM, no swap). The
receipt lists the engine submodule revisions (`engine_commits`, the 2026-11-01
publication's commits) and identifies each executable by SHA-256. The
repository's run16-class native recipe and LibreLane 3.0.17 read the same
prepared inputs, and the same independent gate scripts checked both layouts.
Each flow has one timed sample, under an 8 GiB memory cap (+2 GiB memsw) for
RustEDA, whose cgroup peak reached the cap through page cache, and a 10 GiB cap
for LibreLane.

The EZ130 SG13G2 tech LEF and cell GDS are reconstructed; 5 of the 7
contract-pinned EZ130 files are byte-identical. LibreLane ran nixpkgs OpenROAD
26Q3 and Yosys 0.70 instead of its pinned OpenROAD `dcf36133` and Yosys 0.62,
with its own post-GRT design repair enabled. On 2026-11-02, RustEDA's layout
passed every design gate, but the run was `unqualified` and its
RustSTA/OpenSTA parity gate `failed` at **1.405 ps** against a 1 ps tolerance.
LibreLane `failed` timing on capacitance and fanout checks, and its equivalence
was `unverified`: the checker cannot name-match 433 renamed registers, a checker
limitation rather than evidence of non-equivalence, and its vendor-model
simulation `passed`. Neither flow passed every gate, so no end-to-end ratio was
published. Each section below summarizes its part of each campaign and the
scope of each comparison; the method sections
([2026-11-05](benchmarks.md#host-campaign-method-and-deviations-2026-11-05),
[2026-11-02](benchmarks.md#host-campaign-method-and-deviations)) list every
deviation.

The retained R20/R21 native runs completed all 129 steps and all 16 gates in
**472.2 s and 472.6 s** on 16 logical CPUs, with frozen executables and an
explicit early-check schedule. The [benchmark page](benchmarks.md) separates
both campaigns from the older matched-reference comparison and records each
host and analysis scope.

## RustEDA-Common

The foundation now includes the IHP/SRAM interface work used by the qualified
Ibex flow: Liberty memory buses and pin metadata, explicit driving-cell
constraints, escaped mapped-netlist identifiers, and bounded connectivity
exports. The deterministic CPU RC charge-fitting implementation uses `f64`.

The five shared crates remain independent of engines, Python and GUI packages.
Source manifests record the shared files; downstream repositories pin one exact
Common commit rather than copying implementations.

The [G1 crossing-query CPU/CUDA session](https://github.com/lab-emi/RustEDA-Common/tree/codex/g1-crossing-session-20261101) is a separate development branch. Device
comparisons on captured geometry do not qualify a GPU-enabled RTL-to-GDS flow;
production Road integration and its runtime-image gate remain unchecked.

**2026-11-05 host campaign.** Common `6668eecae9bb` is compiled into the
RustSys and RustSTA binaries the campaign timed, including the `rusteda`
workspace CLI; its `export_pin_caps` example is one of the native frontend's
declared tools. It has no separate head-to-head.

**2026-11-02 host campaign.** Common `06a9cdac2336` is compiled into the
RustSys and RustSTA binaries the campaign timed, including the `rusteda`
workspace CLI built in RustSTA's tree; RustROAD and RustSpice do not depend on
it. Its `export_pin_caps` example is one of the native frontend's declared
tools. It has no separate head-to-head.

[Module source and qualification](../modules/RustEDA-Common/docs/development-status.md) ·
[Host campaign 2026-11-05](benchmarks.md#ibex-host-campaign-2026-11-05) · [2026-11-02](benchmarks.md#ibex-host-campaign-2026-11-02)

## RustSys

Opaque SRAM interfaces are now preserved through elaboration, mapping and
netlist export. Port widths, bus indices and instance/pin identities are checked;
unsupported or inconsistent interfaces produce diagnostics. The macro's behavior
continues to come from its original vendor model for independent checking.

The retained Ibex campaign uses two real IHP SRAMs and proves both synthesis and
final equivalence against the original RTL. The retained R20/R21 native runs
record 472.2 s and 472.6 s with all 16 gates; those are whole-flow measurements, not
synthesis-only timings. Sky130 HD and GF180 7T/9T remain the baseline library
coverage; one successful IHP design does not qualify other SRAM configurations.

The bounded native equivalence checker and external CEC retain arbitrary initial
state and explicit work limits. A budget exhaustion is not an equivalence proof.

**2026-11-05 host campaign.** On the same synthesis task (three measured runs
each), RustSys `86e59d8` took a median 3.81 s in the two-thread workspace
sequence and 3.04 s in direct mode, against 135.04 s for Yosys 0.70 + ABC with
LibreLane's script (35.4× and 44.4× shorter), at 116,200 against 766,036 KiB
worst peak RSS. Its netlist is now 1.79 % smaller in standard-cell area
(145,226.592 µm² against 147,866.947 µm²) and shallower (36 against 40 logic
cells), and the flow's synthesis equivalence is `proven`.

**2026-11-02 host campaign.** Synthesizing the prepared Ibex RTL to an EZ130 TT
plus SRAM Liberty netlist (three measured runs each), RustSys took a median
2.12 s in the flow's two-thread workspace sequence and 1.36 s in single-command
direct mode (all 4 CPUs), against 102.83 s for Yosys 0.70 + ABC running
LibreLane 3.0.17's synthesis script: 48.5× and 75.6× shorter wall time. Its
netlist is 2.2 % larger in standard-cell area (151,095.773 µm² against
147,866.947 µm²) and deeper (55 against 40 logic cells on the deepest path,
excluding buffers). In each flow's shared synthesis-equivalence gate, the
native synthesis netlist is `proven`; LibreLane's is `unverified` because the checker cannot name-match its
renamed registers, a checker limitation rather than evidence of
non-equivalence.

[Module source and qualification](../modules/RustSys/docs/development-status.md) ·
[Host campaign 2026-11-05](benchmarks.md#ibex-host-campaign-2026-11-05) · [2026-11-02](benchmarks.md#ibex-host-campaign-2026-11-02)

## RustSTA

The source now includes mixed standard-cell/SRAM Liberty conventions, explicit
global early/late timing derates and driving-cell waveforms on extracted and
propagated-clock paths. RC preparation runs independent nodes within each graph
level on a bounded rayon pool, then commits results in topological order. The
memory estimate includes an additional 184 bytes per node for jobs/results.

Recorded qualification for the parallel-RC source: 259 regular tests passed
(17 ignored), 10 real-PDK campaigns passed across Sky130 HD and GF180 7T/9T,
and 332 OpenSTA black-box comparison values retained the previous maxima
(0.036 ps Sky130, 0.049 ps GF180; 1 ps gate). Full/incremental and 1/2/8-thread
replays retained bit-identical timing tables on the recorded extracted designs.
The retained R20/R21 Ibex native runs measured 472.2 s and 472.6 s; their maximum
same-SPEF RustSTA/OpenSTA difference is 0.475 ps.

These are retained identity-bound results, not a new campaign run during the
publication. GPU, Ngspice and scale tests listed as unchecked in that source QA
record remain unchecked for this revision. Generated clocks, CPPR, CCS and
crosstalk remain outside the supported flow scope.

**2026-11-05 host campaign.** On the same netlist, Liberty, SDC and native SPEF
(three measured runs), RustSTA `3550739`'s TT+SS pipeline took a median 6.825 s
at 2 threads and 5.317 s at 4, against 11.168 s and 10.566 s for OpenSTA's two
corner processes, with 148,164 / 169,188 KiB against 180,320 / 180,528 KiB
worst peak RSS. The parity gate `passed`: on identical SPEF the eight compared
worst slacks differ by at most 0.164 ps. The 2026-11-02 gap was not fixed in
RustSTA: on a fresh OpenRCX SPEF of that layout the same binaries still differ by
1.2838 ps in TT setup worst slack.

**2026-11-02 host campaign.** On the same netlist, Liberty, SDC and native SPEF
at 4 threads (three measured runs), RustSTA's TT+SS pipeline took a median
15.976 s with 502,876 KiB worst-run peak RSS, against 13.497 s and 267,704 KiB
for OpenSTA's two corner processes. The parity gate `failed`: TT setup worst
slack differs from OpenSTA by **1.405 ps** on native SPEF and 1.284 ps on
OpenRCX SPEF, above the 1 ps tolerance; the other six compared values agree
within 0.22 ps.

[Module source and qualification](../modules/RustSTA/docs/development-status.md) ·
[Host campaign 2026-11-05](benchmarks.md#ibex-host-campaign-2026-11-05) · [2026-11-02](benchmarks.md#ibex-host-campaign-2026-11-02)

## RustROAD

The development source combines bounded extraction indices and query workers,
single-session antenna repair, an opt-in staggered tap lattice, placement-stage
diagnostics and spacing-aware electrostatic density. The density model accounts
for the same cell spacing required by legalization. Optional checkpoint retention
at an evaluation horizon reports the reason explicitly; the default policy stays
strict. Repeated same-path routing near misses receive bounded repair.

In the qualified Ibex layout, standard-cell non-filler area is 204,053.9 µm²,
routed signal wire is 1,190,522 µm and the via count is 168,381. The complete
CPU flow with two real SRAMs records 472.2 s and 472.6 s, with 16/16 gates,
complete foundry main/antenna checks, device LVS and TT/SS timing. The additional
maximal deck contains 272 categories including LU.a; the main deck checks LU.b.
The qualifier requires the early checked GDS to equal the final GDS byte for byte.

The spacing-aware placer and diagnostic settings are opt-in. Dual-PDK physical
smokes cover Sky130 and GF180. OpenC910 physical closure, general macro flows
and GPU-integrated extraction remain unverified. The R20/R21 measurements use a
pinned campaign recipe, not every default CLI configuration.

**2026-11-05 host campaign.** RustROAD `1921481`'s layout has 187,585.2 µm²
standard-cell area, 897,357.3 µm of routed signal wire and 118,525 vias,
against LibreLane's 197,913.7 µm², 969,571.6 µm and 122,194 (different
netlists; no ratio formed). Routing took 302.24 s (global and detailed, 4
threads) against 83.34 s of global routing with antenna repair plus 2,486.85 s
of detailed routing; post-route and antenna repair took 7.34 s and 18.99 s. On
the same final layout (three measured runs), RustROAD extraction took 6.260 s at
4 threads against 5.944 s for OpenRCX (overlapping ranges) and 10.121 s at 2
threads against 5.988 s, with about three times OpenRCX's CPU time; GDS export
took 0.831 s against 1.938 s for KLayout. OpenRCX extracts the native DEF
1.36× slower than LibreLane's in same-load
runs (192,328 against 129,901 wire pieces), and the latch-up gate was slower (1.11×) on the
native layout with the checker of that campaign; with the shared checker of 2026-11-12, changed
for both layouts, it takes 0.78× ([latch-up checker change](benchmarks.md#latch-up-checker-change-2026-11-12)).

**2026-11-02 host campaign.** On the same final routed DEF (three measured
runs), RustROAD extraction took a median 158.823 s on its default 2-thread pool
(the recorded `extract` command passes no `--threads`, so the 4-thread setting is
a second 2-thread sample); OpenRCX took 17.649 s at 2 threads (9.0× shorter) and
17.114 s at 4 threads. RustROAD's GDS
export took 3.019 s against 2.286 s for KLayout's LEF/DEF streamout, and the two
GDS files are not byte-equivalent, so that comparison covers time only. The two
single-sample flows route different netlists and placements, so their routing
times are not an identical-task comparison and form no quotient: RustROAD global
and detailed routing took 389.63 s, against 68.98 s of OpenROAD global routing
with antenna repair plus 1,796.83 s of detailed routing in LibreLane. RustEDA
also spent 179.0 s on post-route repair and 409.35 s on antenna repair and
verification, and its run16-class layout (the R20/R21 recipe inputs are not
published) has 1,679,076.6 µm of routed signal wire and 301,099 vias against
LibreLane's 969,571.6 µm and 122,194.

[Module source and qualification](../modules/RustROAD/docs/development-status.md) ·
[Host campaign 2026-11-05](benchmarks.md#ibex-host-campaign-2026-11-05) · [2026-11-02](benchmarks.md#ibex-host-campaign-2026-11-02)

## RustSpice

The simulator source includes adaptive transient integration and breakpoint
handling, strict SPICE parsing with expressions/includes and explicit resource
limits, extended diode/level-1 MOS and controlled-source coverage, and adjoint
parameter sensitivities. Batched FP64 DC has a deterministic CPU reference and
an optional CUDA kernel with explicit fallback for unsupported/nonconverged cases.

The retained ngspice-47 accuracy suite reports 857 passing, 16 documented gaps,
77 informational and one unverified case out of 951, with no failing cases;
400 random decks and 200 hostile inputs are recorded separately. These are
accuracy/robustness results from the existing campaign, not new performance
measurements made for this publication.

RustSpice is not timed in Ibex RTL-to-GDS and has no transistor-level Ibex
result. BJT, JFET, MESFET and MOS levels above 1 remain unsupported. The
benchmark command writes its report outside published documentation unless an
output is explicit.

**2026-11-05 host campaign.** On the RC decks of the new layout (eight single
nets of 198–291 unknowns, decks of 10 and 40 nets with 2,262 and 6,865 unknowns)
all 10 cases `passed` the accuracy check; RustSpice `d4d6a50`'s median
end-to-end time was 4.32–5.96× shorter than the faster ngspice solver, at lower
peak RSS in every case (24,552 against 34,968 KiB on the 40-net deck).

**2026-11-02 host campaign.** IHP's PSP103 transistor models are not supported,
so RustSpice was compared with ngspice-45 on linear RC decks built from the
native SPEF of the final Ibex layout: eight single nets (548–649 unknowns) and
decks of 10 and 40 nets (5,843 and 20,023 unknowns). All 10 cases `passed` the
accuracy check, and RustSpice's median end-to-end time was 3.68–5.78× shorter
than the faster ngspice solver (seven repetitions per case), at higher peak RSS
(144,152 KiB against 71,608 KiB on the 40-net deck).

[Module source and qualification](../modules/RustSpice/docs/development-status.md) ·
[Host campaign 2026-11-05](benchmarks.md#ibex-host-campaign-2026-11-05) · [2026-11-02](benchmarks.md#ibex-host-campaign-2026-11-02)

## Flow, CLI and workbench

The integrated application retains revision-guarded disk workspaces, bounded
foreground processes, checkpoints, recovery and shared revision-bound selections.
The IHP producer supports bounded parallel diagnostic commands and explicit stdout
artifact accounting. Foundry-first decisions, whole-input extraction reuse, tap
coverage and maximal-deck results bind the actual producer receipts and immutable
layout. Early-check schedules require byte-identical final GDS; a repair requires
a new serial run. The general recipe composer retains its conservative serial
selection; the latest optimized schedule is recorded as a campaign recipe.

**2026-11-05 host campaign.** Still `unqualified` (composed without the
unpublished component-qualification evidence), the native producer completed all
114 manifest steps in 888.896 s, including its own foundry main DRC beside the
antenna DRC and LVS on two two-CPU lanes, which the receipt verified to be the
identical checks of the shared gates. End to end, in one sample each, RustEDA
measured 974.19 s (sv2v preparation + producer + the latch-up, simulation,
equivalence and density gates) and LibreLane 4,616.30 s (preparation + flow
through `Checker.XOR` + all nine shared gates). LibreLane fails the timing gate,
so the receipt keeps its ratio fields null; divided, the two times give 4.2×
(3.4× with both flows' foundry checks run one after another). The frontend STA and ECO stage takes
1.18× the time of LibreLane's two resizer steps before global routing in
same-load runs (0.49× all four).

**2026-11-02 host campaign.** Composed without the unpublished
component-qualification evidence, so `unqualified`, the native producer
completed 114 of 119 manifest steps in 1,329.616 s and stopped at its first
failed gate, RustSTA/OpenSTA parity; the remaining gates ran separately with
the same recipe scripts. End to end, in one sample each, RustEDA measured
2,609.93 s (sv2v preparation + producer, including its own native and OpenRCX
extraction and RustSTA/OpenSTA signoff timing, + the DRC, antenna, LVS,
simulation, equivalence and density gates it did not reach) and LibreLane
3,286.87 s (sv2v preparation + flow through `Checker.XOR` + all eight shared
gates). The receipt derives no ratio from them, because neither flow passed
every gate.

[Flow operation](flows.md) · [Workbench](workbench/index.md) ·
[Qualification boundaries](verification.md) · [Host campaign 2026-11-05](benchmarks.md#ibex-host-campaign-2026-11-05) · [2026-11-02](benchmarks.md#ibex-host-campaign-2026-11-02)

## Experimental source branches

The following snapshots preserve additional development without selecting them in
the production flow. Their READMEs identify their scope and incomplete checks.

| Repository | Branch | Boundary |
| --- | --- | --- |
| RustEDA Common | [G1 CPU/CUDA session](https://github.com/lab-emi/RustEDA-Common/tree/codex/g1-crossing-session-20261101) | Component geometry comparisons; worker/runtime-image and Road integration incomplete |
| RustROAD | [G1 stream capture](https://github.com/lab-emi/RustROAD/tree/codex/g1-stream-capture-20261101) | Original experimental engine base; no whole-flow GPU result |
| RustROAD | [G1 legacy bridge](https://github.com/lab-emi/RustROAD/tree/codex/g1-legacy-bridge-20261101) | Independent bridge prototype; production integration unchecked |

The earlier [G1 CPU session snapshot](https://github.com/lab-emi/RustEDA-Common/tree/codex/g1-cpu-session-20261101) is retained independently of the later CUDA refactor. Its publication does not transfer old ELF qualification to the composite checkout.

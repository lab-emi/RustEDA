# Engine development

Updated **2026-11-01**. RustEDA’s five engine repositories keep independent
Git histories. The superproject pins their source revisions; flow, CLI and
workbench live directly in `apps/`. Version labels describe source previews.

The latest retained Ibex native runs completed all 129 steps and all 16 gates
in **472.2 s and 472.6 s**, on SG13G2/EZ130 with two real SRAMs. Those runs
used frozen executables and an explicit early-check schedule. The
[benchmark page](benchmarks.md) separates them from the older matched-reference
comparison and records the host and analysis scope.

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

[Module source and qualification](../modules/RustEDA-Common/docs/development-status.md)

## RustSys

Opaque SRAM interfaces are now preserved through elaboration, mapping and
netlist export. Port widths, bus indices and instance/pin identities are checked;
unsupported or inconsistent interfaces produce diagnostics. The macro's behavior
continues to come from its original vendor model for independent checking.

The retained Ibex campaign uses two real IHP SRAMs and proves both synthesis and
final equivalence against the original RTL. The newer native flow records
472.2 s and 472.6 s with all 16 gates; those are whole-flow measurements, not
synthesis-only timings. Sky130 HD and GF180 7T/9T remain the baseline library
coverage; one successful IHP design does not qualify other SRAM configurations.

The bounded native equivalence checker and external CEC retain arbitrary initial
state and explicit work limits. A budget exhaustion is not an equivalence proof.

[Module source and qualification](../modules/RustSys/docs/development-status.md)

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
The two complete Ibex native runs measured 472.2 s and 472.6 s; their maximum
same-SPEF RustSTA/OpenSTA difference is 0.476 ps.

These are retained identity-bound results, not a new campaign run during the
publication. GPU, Ngspice and scale tests listed as unchecked in that source QA
record remain unchecked for this revision. Generated clocks, CPPR, CCS and
crosstalk remain outside the supported flow scope.

[Module source and qualification](../modules/RustSTA/docs/development-status.md)

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
and GPU-integrated extraction remain unverified. The latest measurements use a
pinned campaign recipe, not every default CLI configuration.

[Module source and qualification](../modules/RustROAD/docs/development-status.md)

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

RustSpice is not timed in Ibex RTL-to-GDS. It has no Ibex speedup claim. BJT,
JFET, MESFET and MOS levels above 1 remain unsupported. The benchmark command
writes its report outside published documentation unless an output is explicit.

[Module source and qualification](../modules/RustSpice/docs/development-status.md)

## Flow, CLI and workbench

The integrated application retains revision-guarded disk workspaces, bounded
foreground processes, checkpoints, recovery and shared revision-bound selections.
The IHP producer supports bounded parallel diagnostic commands and explicit stdout
artifact accounting. Foundry-first decisions, whole-input extraction reuse, tap
coverage and maximal-deck results bind the actual producer receipts and immutable
layout. Early-check schedules require byte-identical final GDS; a repair requires
a new serial run. The general recipe composer retains its conservative serial
selection; the latest optimized schedule is recorded as a campaign recipe.

[Flow operation](flows.md) · [Workbench](workbench/index.md) ·
[Qualification boundaries](verification.md)

## Experimental source branches

The following snapshots preserve additional development without selecting them in
the production flow. Their READMEs identify their scope and incomplete checks.

| Repository | Branch | Boundary |
| --- | --- | --- |
| RustEDA Common | [G1 CPU/CUDA session](https://github.com/lab-emi/RustEDA-Common/tree/codex/g1-crossing-session-20261101) | Component geometry comparisons; worker/runtime-image and Road integration incomplete |
| RustROAD | [G1 stream capture](https://github.com/lab-emi/RustROAD/tree/codex/g1-stream-capture-20261101) | Original experimental engine base; no whole-flow GPU result |
| RustROAD | [G1 legacy bridge](https://github.com/lab-emi/RustROAD/tree/codex/g1-legacy-bridge-20261101) | Independent bridge prototype; production integration unchecked |

The earlier [G1 CPU session snapshot](https://github.com/lab-emi/RustEDA-Common/tree/codex/g1-cpu-session-20261101) is retained independently of the later CUDA refactor. Its publication does not transfer old ELF qualification to the composite checkout.

# Scope and evidence

## Physical qualification

The retained 2026-10-25 baseline covers eight small designs on SKY130 HD and
GF180 7T: **15 ordinary passes and one declared minimum-pulse-width deviation**.
All 16 rows include independent checks, negative controls, deterministic rebuild
and artifact binding. This is block-level qualification, not foundry tapeout signoff.

| Design | SKY130 HD | GF180 7T |
| --- | --- | --- |
| pipeline | Passed | Passed |
| dense | Passed | Passed |
| counter | Passed | Passed |
| uart_tx | Passed | Passed |
| alu | Passed | Passed |
| fifo | Deviation declared | Passed |
| regfile | Passed | Passed |
| mult | Passed | Passed |

[Physical receipts](../modules/RustROAD/docs/evidence/rtl2gds-closeout-2026-10-25.json)
retain the raw timing disagreement and declaration. See the
[minimum-pulse-width policy](../modules/RustROAD/docs/mpw-deviations.md).
No endpoint-parity tolerance is silently relaxed.

The [flow integration receipt](../integration/evidence/phase3.json) records fresh
counter/SKY130, counter/GF180 and fifo/SKY130 runs, cache verification and worker
recovery. They qualify the application protocol; they are not benchmarks. Public
performance results are the [Ibex SG13G2/EZ130 campaign](benchmarks.md) and the
[PULPissimo Ibex-core synthesis benchmark](benchmarks/pulpissimo.md). The
PULPissimo result is synthesis-only. Its SKY130 full-SoC synthesis passes
hierarchy elaboration but exceeds a 2 h budget with behavioral L2 memory; an
IHP SG13G2 / EZ130 synthesis-only test with official SRAM macros completes, but
neither run is verified for physical signoff.

## Known limits

- The Ibex SG13G2/EZ130 results cover one design: R20/R21 are two runs on one
  host, and the 2026-11-02, 2026-11-05 and 2026-11-13 host campaigns are one
  timed sample per flow each. The PULPissimo result covers the Ibex core only and
  reports three samples per producer; the SKY130 full SoC still exceeds a 2 h
  budget with behavioral L2 memory, while the IHP full-SoC test is
  synthesis-only and unverified for physical signoff. They do
  not qualify other designs, SRAM configurations or the IHP `sg13g2_stdcell`
  library.
- The host campaigns are `unqualified` because their native recipe was composed
  without component-qualification evidence, and their EZ130 tech LEF and cell
  GDS are reconstructed. On 2026-11-02 the RustSTA/OpenSTA parity also exceeded
  1 ps on that layout; on 2026-11-05 and 2026-11-13 it passed (0.164 ps and
  0.270 ps).
- The R20/R21 recipe inputs are not published, so R20/R21 cannot be reproduced
  from the repository.
- The independent equivalence checker cannot name-match registers that a flow
  renames without a unique RTL counterpart (433 in LibreLane's Ibex netlist); such
  results are `unverified`, not evidence of non-equivalence.
- RustSpice does not support IHP's PSP103 transistor models, so it cannot simulate
  Ibex at transistor level; its Ibex comparison uses extracted RC networks only.
- OpenC910 placement, routing, GDS and end-to-end performance are unverified.
- The original failing OpenC910 independent timing-checker case is unavailable
  for reproduction; it has not been reclassified as passing.
- Full GF180 latch timing and high-resistance effective capacitance retain
  explicit coverage limits in the timing documentation.
- IR/EM, signal integrity, general OCV/CPPR, full-chip assembly and foundry acceptance
  are not established by the small-block matrix.
- A source build or unit-test pass is not a new equivalence or signoff result.

Public benchmark and oracle records retain their original identities and scope.
Local planning notes do not replace those records. Licenses, notices and design
provenance remain authoritative; publication is not a legal certification.

## Ibex host campaign · 2026-11-13

The native recipe of superproject `820ffc8`, with every engine built with Rust
1.100.0 and the step graph run by the Rust executor `rusteda flow exec`, and
LibreLane 3.0.17 Classic built Ibex with two real SRAMs on a 4-vCPU Xeon cloud
host (15.7 GiB RAM) from the same prepared inputs; the same nine independent
gate scripts checked both final layouts. Each flow has one timed sample.

**Native scope.** Composed without the unpublished component-qualification
evidence, the eight derived qualification gates were not produced and the run
is `unqualified`. The producer completed all 114 manifest steps, including the
RustSTA/OpenSTA parity gate and its own foundry DRC, antenna DRC, LVS and macro
contract, which the receipt verified to be the identical checks of the shared
gates. A second producer run of the same composition produced byte-identical
GDS, DEF, synthesis, final and powered netlists and native SPEF.

| Gate | RustEDA | LibreLane 3.0.17 |
| --- | --- | --- |
| Foundry main DRC (`recommended` rules disabled) / antenna DRC | `passed`, 0 / 0 | `passed`, 0 / 0 |
| Latch-up tap coverage, LU.a / LU.b (20 µm) | `passed`, 0 / 0 uncertified boxes | `passed`, 0 / 0 uncertified boxes |
| OpenRCX extraction (SPEF for the timing gate) | `extracted_not_qualified` (no pass/fail verdict) | `extracted_not_qualified` (no pass/fail verdict) |
| Full-device LVS | `passed` | `passed` |
| Timing on OpenRCX SPEF, TT and SS | `passed`; 0 setup, hold, slew, capacitance and fanout violations | `failed`; setup and hold met; 28 / 24 capacitance and 30 / 30 fanout violations (TT / SS) |
| Vendor-model simulation | `passed` | `passed` |
| Synthesis / final equivalence | `proven` / `proven` | `unverified` / `unverified` |
| Final density, cap 70 % | `passed`, 26.67 % | `passed`, 28.14 % |
| RustSTA/OpenSTA parity on identical SPEF, 1 ps | `passed`, 0.270 ps | not evaluated |

LibreLane's equivalence is `unverified` for the same checker limitation as
before (433 renamed registers). The host is again a different virtual machine
(Xeon @ 2.10 GHz); the deviations are listed in the campaign's method section.
LibreLane fails a gate, so the receipt's end-to-end ratios are null.

[Host campaign results](benchmarks.md#ibex-host-campaign-2026-11-13) ·
[Receipt](benchmarks/ibex-sg13g2-ez130-host-20261113.json) ·
[Method and deviations](benchmarks.md#host-campaign-method-and-deviations-2026-11-13)

## Ibex host campaign · 2026-11-05

The current native recipe (superproject `397bfa6`) and LibreLane 3.0.17 Classic
built Ibex with two real SRAMs on a 4-vCPU Xeon cloud host (15.7 GiB RAM) from
the same prepared inputs; the same nine independent gate scripts checked both
final layouts. Each flow has one timed sample.

**Native scope.** Composed without the unpublished component-qualification
evidence, the eight derived qualification gates were not produced and the run
is `unqualified`. The producer completed all 114 manifest steps, including the
RustSTA/OpenSTA parity gate and its own foundry DRC, antenna DRC, LVS and macro
contract, which the receipt verified to be the identical checks of the shared
gates.

| Gate | RustEDA | LibreLane 3.0.17 |
| --- | --- | --- |
| Foundry main DRC (`recommended` rules disabled) / antenna DRC | `passed`, 0 / 0 | `passed`, 0 / 0 |
| Latch-up tap coverage, LU.a / LU.b (20 µm) | `passed`, 0 / 0 uncertified boxes | `passed`, 0 / 0 uncertified boxes |
| OpenRCX extraction (SPEF for the timing gate) | `extracted_not_qualified` (no pass/fail verdict) | `extracted_not_qualified` (no pass/fail verdict) |
| Full-device LVS | `passed` | `passed` |
| Timing on OpenRCX SPEF, TT and SS | `passed`; 0 setup, hold, slew, capacitance and fanout violations | `failed`; setup and hold met; 28 / 24 capacitance and 30 / 30 fanout violations (TT / SS) |
| Vendor-model simulation | `passed` | `passed` |
| Synthesis / final equivalence | `proven` / `proven` | `unverified` / `unverified` |
| Final density, cap 70 % | `passed`, 26.67 % | `passed`, 28.14 % |
| RustSTA/OpenSTA parity on identical SPEF, 1 ps | `passed`, 0.164 ps | not evaluated |

LibreLane's equivalence is `unverified` because the checker cannot name-match
433 renamed registers to the RTL; this is a checker limitation, not evidence of
non-equivalence. The deviations are those of 2026-11-02 below, on a different
and slower virtual machine (Xeon @ 2.80 GHz). LibreLane fails a gate, so the
receipt's end-to-end ratios are null.

[Host campaign results](benchmarks.md#ibex-host-campaign-2026-11-05) ·
[Receipt](benchmarks/ibex-sg13g2-ez130-host-20261105.json) ·
[Harness and deviations](../integration/ibex_host_benchmark/README.md)

## Ibex host campaign · 2026-11-02

This campaign reproduced Ibex with two real SRAMs on a 4-vCPU Xeon cloud host
(15.7 GiB RAM). The repository's native recipe (run16-class) and
LibreLane 3.0.17 Classic read the same prepared inputs; the same independent
gate scripts checked both final layouts. Each flow has one timed sample.

**Native scope.** The 16-gate composer requires component-qualification evidence
that is not published and was not fabricated. The recipe was composed without it,
so the eight derived qualification gates were not produced and the run is
`unqualified`. The producer completed 114 of 119 manifest steps and stopped at
its first failed gate, RustSTA/OpenSTA timing parity; the remaining gates ran
separately with the same recipe scripts.

| Gate | RustEDA | LibreLane 3.0.17 |
| --- | --- | --- |
| Foundry main DRC (`recommended` rules disabled) / antenna DRC | `passed`, 0 / 0 | `passed`, 0 / 0 |
| Full-device LVS | `passed` | `passed` |
| Timing on OpenRCX SPEF, TT and SS | `passed`; 0 setup, hold, slew, capacitance and fanout violations | `failed`; setup and hold met; 28 / 24 capacitance and 30 / 30 fanout violations (TT / SS) |
| Vendor-model simulation | `passed` | `passed` |
| Synthesis / final equivalence | `proven` / `proven` | `unverified` / `unverified` |
| Final density, cap 70 % | `passed`, 33.25 % | `passed`, 28.14 % |
| RustSTA/OpenSTA parity on identical SPEF, 1 ps | `failed`, 1.405 ps | not evaluated |

The parity failure is TT setup worst slack: 1.405 ps on native SPEF and
1.284 ps on OpenRCX SPEF. The other six compared values agree within 0.22 ps.
LibreLane's equivalence is `unverified` because the checker cannot name-match
433 renamed registers to the RTL; this is a checker limitation, not evidence of
non-equivalence. LibreLane ran with its own post-global-route design repair, not
the RustEDA-authored repair loop of the historical comparison. A diagnostic run
with the unchanged prepared configuration (that repair off) also failed timing,
with 7 slew, 181 capacitance and 56 fanout violations at TT.

**Host deviations.** 4 vCPUs instead of 16 logical CPUs of an i9-13900HX;
cgroup v1 with no swap device (RustEDA 8 GiB cap, LibreLane 10 GiB cap);
nixpkgs OpenROAD 26Q3 and Yosys 0.70 instead of LibreLane's pinned OpenROAD
`dcf36133` and Yosys 0.62; EZ130 tech LEF and cell GDS reconstructed from public
sources (5 of the 7 contract-pinned files byte-identical; the catalog-pinned FF
Liberty also matches); host-local helper patches for
thread allow-lists, cgroup-v1 ownership, the RC-profile rules path and the
density DEF parser. The R20/R21 recipe inputs are not published, so this
campaign does not reproduce R20/R21. Neither flow passes every gate, so the
receipt's end-to-end ratios are null.

[Host campaign results](benchmarks.md) ·
[Receipt](benchmarks/ibex-sg13g2-ez130-host-20261102.json) ·
[Harness and deviations](../integration/ibex_host_benchmark/README.md)

## Latest retained native runs

R20/R21 complete 129 steps and the original 16 gates with two real SRAMs. Their
extra tap proof binds the actual maximal/coverage processes, complete LU.a/LU.b
categories and immutable GDS. The publication retains original reports by SHA-256
and does not rerun or relabel failed attempts. The original qualifier’s benchmark
field remains `unverified`: no matched reference campaign was run for R20/R21.
The 2026-11-02 host campaign used the run16-class recipe on another host; the
2026-11-05 and 2026-11-13 host campaigns used the recipe of their day.

[Latest measurement scope and evidence](benchmarks.md#latest-native-development-results-2026-11-01)

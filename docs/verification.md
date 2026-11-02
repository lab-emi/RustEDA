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
recovery. They qualify the application protocol; they are not benchmarks. The only
published benchmark is the [Ibex SG13G2/EZ130 campaign](benchmarks.md).

## Known limits

- The Ibex SG13G2/EZ130 results are repeated runs of one design. They do not
  qualify other designs, SRAM configurations or the IHP `sg13g2_stdcell` library.
- The 2026-11-02 host reproduction is `unqualified`: its native composition lacks
  the component-qualification evidence, its RustSTA/OpenSTA parity exceeded 1 ps,
  and its EZ130 tech LEF and cell GDS are reconstructed.
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

## Ibex host campaign · 2026-11-02

This campaign reproduced Ibex with two real SRAMs on a 4-vCPU cloud host (Xeon
2.1 GHz, 15.7 GiB RAM). The repository's native recipe (run16-class) and
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
| Foundry main / antenna DRC | `passed`, 0 / 0 | `passed`, 0 / 0 |
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
sources (6 of 8 pinned files byte-identical); host-local helper patches for
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
The 2026-11-02 host campaign used the run16-class recipe on another host.

[Latest measurement scope and evidence](benchmarks.md#latest-native-development-results-2026-11-01)

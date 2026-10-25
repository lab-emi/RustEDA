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
recovery. Their full initialization-plus-run times were 109.273 s, 151.883 s and
237.357 s. These are single-flow qualification observations without matched
competitor measurements; they do not yield a comparative speedup.

## Known limits

- OpenC910 placement, routing, GDS and end-to-end performance are unverified.
- The original failing OpenC910 independent timing-checker case is unavailable
  for reproduction; it has not been reclassified as passing.
- Full GF180 latch timing and high-resistance effective capacitance retain
  explicit coverage limits in the timing documentation.
- IR/EM, signal integrity, OCV/CPPR, full-chip assembly and foundry acceptance
  are not established by the small-block matrix.
- A source build or unit-test pass is not a new equivalence or signoff result.

Public benchmark and oracle records retain their original identities and scope.
Local planning notes do not replace those records. Licenses, notices and design
provenance remain authoritative; publication is not a legal certification.

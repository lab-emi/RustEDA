# Owner decisions (from the user, in chat)

## 2026-10-24: clock period rule
Per design and PDK, the clock period is set BEFORE any physical step as:
    period = max(10 ns, round_up(1.25 × pre-layout ss+max critical-path delay, 1 ns))
- "Pre-layout" means RustSTA with ideal wires (or wireload) on the mapped netlist at the ss corner.
- The rule is applied uniformly to every design and PDK. Each period is frozen by hash and recorded in evidence together with its derivation.
- After that, closure runs under the frozen period. No post-route tuning is allowed.

## 2026-10-24: GF180 DRV / cell policy
- Keep the design SDC `set_max_transition 1.0` unchanged.
- Add a dont_use rule to GF180 synthesis (fixtures/flow/cell-policy.json) for the cell families that cannot reach 1.0 ns output transition at ss_125C_3v00, even at minimum load with a 0.2 ns input: aoi211, aoi221, aoi222, nor4, oai22, oai221, oai222, oai31, oai32, oai33.
- Generate the rule from Liberty data with a tracked script that records the measured values and the Liberty sha256. Do not hand-write the list.
- Apply the rule before synthesis. It loosens no check.

## 2026-10-25: MPW parity declared deviation
The gate's `timing_accuracy` keeps its 1 ps limit. A min-pulse-width row whose RustSTA-vs-OpenSTA difference exceeds 1 ps is accepted only if ALL of the following hold:
1. The tracked script (`mpw_explain`, ported from `.work/sta-signoff/scripts/sta5/`) proves a reference driver-slew step lies between the net's actual resistance and a nearby probed resistance.
2. Across that step the two tools differ by ≤ 0.2 ps.
3. The Ngspice circuit oracle sides with RustSTA, i.e. RustSTA is closer to the circuit than the reference.

Reporting:
- Such a row is shown separately as `deviation_declared`, with its evidence.
- The aggregate status says it has declared deviations. It is never plain `passed` without that note.
- Endpoint parity and every other row keep the strict 1 ps limit.

### 2026-10-25 clarification from the owner

Condition 2 means the **raw RustSTA-vs-OpenSTA difference at a nearby sample
on the other side of the step is at most 0.2 ps**. It does not mean subtracting
the step size from the original mismatch. Preserve the original mismatch,
locate the step between the original and nearby resistance, and record both
observations and the circuit evidence for each declared row.

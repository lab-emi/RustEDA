---
hide:
  - toc
  - navigation
---

<div class="eda-home" markdown>

<p class="eda-eyebrow">DESIGN. MEASURE. IMPROVE.</p>

# Infrastructure for chip-design self-improvement

<p class="eda-lead">Shorten the work between inference steps.</p>

RustEDA connects native synthesis, timing and physical design with a revisioned
workspace. An agent proposes RTL. Tools implement and verify it. Measured feedback
guides the next iteration.

[Start building](development.md){ .md-button .md-button--primary }
[Explore the modules](modules.md){ .md-button }

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="assets/rusteda-banner.svg">
  <img src="assets/rusteda-banner.gif" alt="The chip-design feedback loop from RTL through implementation and measurement" width="1200">
</picture>

## RustEDA vs LibreLane at a glance

Ibex RTL-to-GDS on IHP SG13G2 with EZ130 cells and two SRAM macros, 2026-11-13: RustEDA and LibreLane 3.0.17 (OpenROAD, Yosys, KLayout, Magic, Netgen) ran one after the other on the same 4-vCPU host from the same prepared inputs, and the same signoff scripts checked both layouts. Every RustEDA engine was built with Rust 1.100.0, and RustEDA's step graph ran under its Rust executor, `rusteda flow exec`.

**4.4× faster end to end** (752 s against 3,283 s with both flows' foundry
checks on the same schedule; 3.5× with every check run one after another), with a
**5.2 % smaller, 7.7 % shorter-wired layout, 1.3 % fewer vias and zero timing
violations** where LibreLane's layout has 112.

| Stage | RustEDA | LibreLane | RustEDA |
| --- | ---: | ---: | ---: |
| Synthesis | 3.10 s | 124.93 s | **40.3× faster** |
| Placement and clock tree | 19.56 s | 41.21 s | **2.1× faster** |
| Repair before detailed routing | 13.85 s | 48.36 s | **3.5× faster** |
| Routing, global and detailed | 266.71 s | 2,083.40 s | **7.8× faster** |
| Static timing in the flow | 26.96 s | 138.93 s | **5.2× faster** |
| Parasitic extraction | 9.51 s | 38.97 s | **4.1× faster** |
| GDS / DEF / netlist export | 1.67 s | 266.29 s | **159× faster** |
| Foundry DRC / antenna / LVS on each layout¹ | 278 / 127 / 93 s | 379 / 169 / 135 s | **1.4× / 1.3× / 1.5× faster** |
| Whole flow² | 717.30 s | 2,766.39 s | **3.9× faster** |
| **End to end** (flow + every signoff check) | **751.54 s** | **3,283.05 s** | **4.4× faster** |

| Quality of results | RustEDA | LibreLane | RustEDA |
| --- | ---: | ---: | ---: |
| Standard-cell area | 187,585.2 µm² | 197,913.7 µm² | **5.2 % smaller** |
| Routed signal wirelength | 895,279.2 µm | 969,571.6 µm | **7.7 % shorter** |
| Vias | 120,585 | 122,194 | **1.3 % fewer** |
| Capacitance / fanout violations, TT + SS | 0 | 112 | **timing-clean** |
| Setup worst slack, TT / SS | 49.63 / 49.43 ns | 48.60 / 47.84 ns | **+1.03 / +1.59 ns** |
| Synthesis and final equivalence | proven | unverified | **proven** |

| Module against its open-source counterpart | Speed | Peak memory | Quality |
| --- | --- | --- | --- |
| RustSys vs Yosys + ABC (LibreLane script) | **33.8× faster** (47.4× in direct mode) | **6.6× less** | 1.8 % less area, logic depth 36 vs 40, equivalence proven |
| RustSTA vs OpenSTA | **1.6× faster** at 2 threads, **2.0×** at 4 | **29.5 % less** at 2 threads, **12.6 % less** at 4³ | worst slacks within 0.270 ps |
| RustROAD routing vs OpenROAD (in the flow) | **7.8× faster** | — | 7.7 % less wire, 1.3 % fewer vias |
| RustROAD extraction vs OpenRCX | level at 4 threads (1.07×, ranges overlap), **1.85× slower** at 2³ | **1.6× more**³ | total capacitance 6.0 % above OpenRCX's |
| RustROAD GDS export vs KLayout | **2.8× faster** | **2.0× less** | — |
| RustSpice vs ngspice-45 (extracted RC nets) | **4.1–7.0× faster** | **1.4–2.1× less** | closer to the reference in 10 of 10 cases on the nominal grid, 6 of 10 at each simulator's own output points |

¹ Same scripts and decks on both layouts, interleaved runs at the same load (medians), on the 4-vCPU VM the session
moved to after a container restart ([shared gates](benchmarks.md#shared-gates-2026-11-13)).
² RustEDA's flow time includes its own foundry DRC, antenna and LVS; LibreLane's does not.
³ After the campaign: RustSTA `de210a6` and RustROAD `fe4b286` against OpenSTA, OpenRCX and the campaign's binaries
in the same runs ([RustSTA peak memory and RustROAD extraction](benchmarks.md#ruststa-peak-memory-and-rustroad-extraction-2026-11-13)). In the campaign, RustSTA used
13.6 % less at 2 threads and 11.8 % more at 4, and RustROAD's extraction was 1.22× slower at 4 threads and 2.19× at 2.

LibreLane's layout fails the shared timing gate (112 maximum-capacitance and fanout violations at TT and SS) and its equivalence check does not complete, so the receipt keeps its own ratio fields null under its publication rule; the multiples here divide the recorded times. Stages are paired by phase, not as identical tasks. Against the three losses of 2026-11-05, in same-load runs: repair before global routing now takes 0.73× the time of LibreLane's two pre-route resizer steps (ranges do not overlap; 1.18× on 2026-11-05), OpenRCX on RustEDA's layout is level with its time on LibreLane's (1.00×, ranges overlap; 1.36× on 2026-11-05) and the latch-up check on RustEDA's layout takes 0.76× its time on LibreLane's (1.11× with the checker of that campaign). RustEDA is still not faster or not better everywhere: RustROAD's standalone extraction took 1.22× OpenRCX's time at 4 threads in the campaign and, after a change since, is level at 4 threads and 1.85× slower at 2; the timing gate is level (0.98×, ranges overlap) and its layout keeps less hold margin. RustSTA's peak memory at 4 threads, 11.8 % above OpenSTA's in the campaign, is 12.6 % below it since a change after the campaign ([details](benchmarks.md#ruststa-peak-memory-and-rustroad-extraction-2026-11-13)). [All stages, gates and losses](benchmarks.md#ibex-host-campaign-2026-11-13)

## Ibex SG13G2 / EZ130

The 2026-11-13 host campaign built Ibex with EZ130 cells and **two real IHP
SRAM macros** on a 4-vCPU cloud host, one timed sample per flow: the current
native recipe, with every engine built with Rust 1.100.0 and the step graph run
by the Rust executor `rusteda flow exec`, and LibreLane 3.0.17 read the same
prepared inputs, and the same nine independent gate scripts checked both
layouts. **RustEDA's producer completed all 114 steps, including the 1 ps
RustSTA/OpenSTA parity gate, and its layout passes every shared gate that gives
a pass/fail verdict** (OpenRCX extraction reports `extracted_not_qualified` on
both layouts), with 187,585.2 µm² standard-cell area, 895,279.2 µm of routed
wire and 120,585 vias against LibreLane's 197,913.7 µm², 969,571.6 µm and
122,194. LibreLane's layout fails the timing gate on max-capacitance and fanout
checks and its equivalence is `unverified`, so the receipt publishes no
cross-tool ratio; the end-to-end times were 751.54 s and 3,675.96 s (3,283.05 s
with LibreLane's foundry checks on the producer's schedule). The native run is
`unqualified` (no published component-qualification evidence). RustEDA leads
every paired stage, and the three losses of 2026-11-05 are closed in same-load
runs: its repair before global routing takes 0.73× LibreLane's two pre-route
resizer steps, OpenRCX extraction is level on the two layouts, and the latch-up
gate takes 0.76× its time on LibreLane's layout. In same-load reruns the native
layout is ahead on foundry DRC, antenna DRC, LVS, latch-up, simulation and
density, and level on timing and OpenRCX extraction. Module head-to-heads cover
RustSys, RustSTA, RustROAD and RustSpice (on extracted RC nets, not transistor
level); two of them were losses in the campaign: RustROAD's own extraction took
1.22× OpenRCX's time at 4 threads, and RustSTA's peak memory at 4 threads was
11.8 % above OpenSTA's. Since then RustSTA's is 12.6 % below OpenSTA's, and
RustROAD's extraction is level with OpenRCX at 4 threads but still 1.85× slower
at 2 ([after the campaign](benchmarks.md#ruststa-peak-memory-and-rustroad-extraction-2026-11-13)). The 2026-11-05 and 2026-11-02 campaigns, on other hosts, stay on the
benchmark page.

The retained R20/R21 native runs complete **129/129 steps and all 16 gates**, in
472.2 s and 472.6 s on 16 logical CPUs of an i9-13900HX; times from different
hosts are not comparable.

[Latest Ibex campaign →](benchmarks.md#ibex-host-campaign-2026-11-13) · [Engine capabilities →](engine-status.md)

## Build the feedback loop

<div class="grid cards" markdown>

- ![RustSys](modules/rustsys/assets/rustsys-logo.svg){ width="48" } **Synthesize**

    RTL, mapped cells and equivalence checks.

    [RustSys →](modules/rustsys/index.md)

- ![RustSTA](modules/ruststa/assets/ruststa-logo.svg){ width="48" } **Analyze timing**

    Constraints, parasitics and critical paths.

    [RustSTA →](modules/ruststa/index.md)

- ![RustROAD](modules/rustroad/assets/rustroad-logo.svg){ width="48" } **Implement**

    Placement, routing, extraction and GDS.

    [RustROAD →](modules/rustroad/index.md)

- ![RustSpice](modules/rustspice/assets/rustspice-logo.svg){ width="48" } **Simulate circuits**

    Differentiable SPICE waveforms and adjoint gradients.

    [RustSpice →](modules/rustspice/index.md)

- ![RustEDA](assets/rusteda-logo.svg){ width="48" } **Run and recover**

    Bounded execution and verified checkpoints.

    [Run a flow →](flows.md)

- ![RustEDA workbench](assets/rusteda-logo.svg){ width="48" } **Inspect and act**

    Shared views, paths and revision-bound selections.

    [Open the workbench →](workbench/index.md)

- ![RustEDA Common](modules/rusteda-common/assets/rusteda-common-logo.svg){ width="48" } **Share a foundation**

    Models, formats, disk workspaces and compute.

    [RustEDA Common →](modules/rusteda-common/index.md)

</div>

## Faster tools. Faster iterations.

Chip-design RSI means iterating on a design using verified feedback. Accelerating
the work outside model inference improves the whole iteration only in proportion
to the time that work consumes. Amdahl's law makes that limit explicit.

<div class="eda-amdahl" role="group" aria-label="Illustrative Amdahl's law calculator">
  <label for="tool-fraction">Time spent in tools <output id="fraction-value">80%</output></label>
  <input id="tool-fraction" type="range" min="0" max="99" step="1" value="80">
  <label for="tool-speedup">Aggregate tool speedup <output id="tool-value">10×</output></label>
  <input id="tool-speedup" type="range" min="1" max="50" step="1" value="10">
  <p><output id="iteration-speedup" aria-live="polite">3.57×</output> illustrative iteration speedup</p>
  <small>1 / ((1 − f) + f / s). This is a model, not a measured RustEDA result.</small>
</div>

[Read the measurement scope](benchmarks.md#measurement-scope) ·
[Check current qualification](verification.md)

</div>

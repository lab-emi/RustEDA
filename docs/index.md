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

## Ibex SG13G2 / EZ130

The 2026-11-02 host campaign built Ibex with EZ130 cells and **two real IHP
SRAM macros** on a 4-vCPU cloud host, one timed sample per flow: the native
recipe and LibreLane 3.0.17 read the same prepared inputs, and the same
independent scripts checked both layouts. **Neither flow passes every gate.**
RustEDA's layout passes DRC, antenna, LVS, TT/SS timing, simulation, equivalence
and density, but on identical SPEF RustSTA and OpenSTA differ by up to 1.405 ps,
failing the 1 ps parity gate. Separately, the run is `unqualified` because the
recipe was composed without component-qualification evidence. LibreLane fails
the timing gate on max-capacitance and fanout checks (setup and hold met); its
equivalence is `unverified` because the checker cannot name-match 433 renamed
registers, a checker limitation rather than evidence of non-equivalence.
Standard-cell area is 233,909.2 µm² for RustEDA and 197,913.7 µm² for LibreLane.
The end-to-end times, 2,609.93 s and 3,286.87 s, cover different work (RustEDA's
producer stopped at the parity gate), so no cross-tool ratio is published.
Module head-to-heads cover RustSys, RustSTA, RustROAD and RustSpice (on
extracted RC nets, not transistor level).

The retained R20/R21 native runs complete **129/129 steps and all 16 gates**, in
472.2 s and 472.6 s on 16 logical CPUs of an i9-13900HX; times from different
hosts are not comparable.

[Latest Ibex campaign →](benchmarks.md) · [Engine capabilities →](engine-status.md)

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

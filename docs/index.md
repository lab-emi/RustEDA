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

<div class="eda-metrics" markdown>

<div><strong>20.55×</strong><span>OpenC910 synthesis speedup</span></div>
<div><strong>10.90%</strong><span>Less mapped cell area</span></div>
<div><strong>Unverified</strong><span>OpenC910 RTL-to-GDS speedup</span></div>

</div>

RustSys vs Yosys + ABC on SKY130 HD, 16 host cores. Synthesis and export only;
the measured boundary excludes proof and timing. [Results and limits](benchmarks.md).

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

[Read the measurement contract](benchmarks.md#a-matched-rtl-to-gds-comparison) ·
[Check current qualification](verification.md)

</div>

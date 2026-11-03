# Architecture

RustEDA is one product. Its flow engine, workspace CLI and workbench are versioned
directly in this repository under `apps/`, sharing one Cargo workspace and lockfile.
Use `./rusteda flow` to execute a design and `./rusteda studio` to inspect it.

| Component | Responsibility | Source |
| --- | --- | --- |
| RustEDA flow | Execution, checkpoints and recovery | `apps/crates/apps/rusteda-flow` |
| RustEDA workbench | Hierarchy, paths, cones and shared selections | `apps/crates/apps/rusteda-studio` |
| RustEDA CLI | Unified command-line entrypoint | `apps/crates/apps/rusteda-cli` |
| RustSys | Synthesis and mapping | `modules/RustSys` |
| RustSTA | Timing and constraints | `modules/RustSTA` |
| RustROAD | Physical implementation | `modules/RustROAD` |
| RustSpice | Differentiable SPICE circuit simulation and adjoint sensitivity | `modules/RustSpice` |
| RustEDA Common | Shared models, formats and compute | `modules/RustEDA-Common` |

Only the five engine/foundation repositories remain submodules. Core crates keep
independent workspaces and exchange versioned files and CLI contracts. The foundation
owns `rusteda-core`, `rusteda-workspace`, `rusteda-formats`, `rusteda-compute` and
`rusteda-cli-common`; no shared implementation is duplicated into the applications.

[Flow guide](flows.md) · [Workbench](workbench/index.md) · [Integration details](application-integration.md)

## Current development

The 2026-11-01 publication includes the qualified IHP/SRAM source bases, parallel
RC preparation, spacing-aware placement and bounded physical-flow helpers.
Each engine README and development-status page records its supported scope.
The G1 CPU/CUDA crossing session is published separately as experimental work;
GPU-integrated RTL-to-GDS remains unchecked.

The newest results are the 2026-11-02 host campaign: Ibex on SG13G2/EZ130 with
two real SRAMs on a 4-vCPU cloud host, against LibreLane 3.0.17 on the same
prepared inputs, plus module head-to-heads for RustSys, RustSTA, RustROAD and
RustSpice. Its receipt lists the engine source revisions the campaign built
(`engine_commits`, the 2026-11-01 publication's commits; the superproject now
pins documentation-only descendants of them) and identifies each executable by
SHA-256. Each
flow has one timed sample, and the EZ130 tech LEF and cell GDS are
reconstructed; the benchmark page lists the tool and host deviations. The
native run is `unqualified`, and neither flow passes every gate, so no
end-to-end ratio is published.

[All engine updates](engine-status.md) ·
[Host campaign](benchmarks.md#ibex-host-campaign-2026-11-02) ·
[Retained runs](benchmarks.md#latest-native-development-results-2026-11-01) ·
[Receipt](benchmarks/ibex-sg13g2-ez130-host-20261102.json)

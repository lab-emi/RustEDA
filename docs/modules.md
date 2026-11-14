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

The newest results are the 2026-11-13 host campaign: Ibex on SG13G2/EZ130 with
two real SRAMs on a 4-vCPU cloud host, against LibreLane 3.0.17 on the same
prepared inputs, plus module head-to-heads for RustSys, RustSTA, RustROAD and
RustSpice. Every engine was built with Rust 1.100.0, and the native step graph
ran under the Rust executor `rusteda flow exec`. Its receipt lists the engine
revisions that superproject `820ffc8` pins (`engine_commits`) and identifies
each executable by SHA-256; the native layout passes every shared gate that
gives a pass/fail verdict, LibreLane's fails the timing gate, so the receipt
keeps its ratio fields null; divided, the recorded times give RustEDA a 4.4×
shorter end-to-end time (3.5× with every check serial). Two module head-to-heads
were losses in the campaign: RustROAD's extraction took 1.22× OpenRCX's time at
4 threads, and RustSTA's peak memory at 4 threads was 11.8 % above OpenSTA's.
Since then RustSTA's is 12.6 % below OpenSTA's, and RustROAD's extraction is
narrowly faster than OpenRCX at 4 threads (0.85×; ranges apart in one of two A/Bs) but still 1.52× slower at 2
([after the campaign](benchmarks.md#ruststa-peak-memory-and-rustroad-extraction-2026-11-13),
[second round](benchmarks.md#rustroad-extraction-second-round-2026-11-14), [third round](benchmarks.md#rustroad-extraction-third-round-2026-11-14)). The earlier
2026-11-05 and 2026-11-02 host campaigns are kept on the same page; the
2026-11-05 receipt's commit IDs predate the rewrite of the branch's commit
metadata, and [commit-map.json](benchmarks/commit-map.json) maps them to the
current IDs. In every campaign each flow has one timed sample, the EZ130 tech
LEF and cell GDS are reconstructed, and the native run is `unqualified`; the
benchmark page lists the tool and host deviations.

[All engine updates](engine-status.md) ·
[Host campaign](benchmarks.md#ibex-host-campaign-2026-11-13) ·
[Retained runs](benchmarks.md#latest-native-development-results-2026-11-01) ·
[Receipt](benchmarks/ibex-sg13g2-ez130-host-20261113.json)

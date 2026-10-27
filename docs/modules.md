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

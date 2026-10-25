# Modules

RustEDA is a suite of six independently versioned repositories. The superproject pins their compatible revisions and owns the documentation entry point. Each repository retains its own Cargo workspace.

| Module | Responsibility | Documentation |
| --- | --- | --- |
| RustSys | Synthesis and mapping | [Synthesis](https://lab-emi.github.io/RustEDA/modules/rustsys/) |
| RustSTA | Timing and constraints | [Timing](https://lab-emi.github.io/RustEDA/modules/ruststa/) |
| RustROAD | Physical implementation | [Physical design](https://lab-emi.github.io/RustEDA/modules/rustroad/) |
| RustLane | Flow execution and recovery | [Flows](https://lab-emi.github.io/RustEDA/modules/rustlane/) |
| RustEDA Studio | Human and agent workbench | [Workbench](https://lab-emi.github.io/RustEDA/modules/rusteda-studio/) |
| RustEDA Common | Shared models, formats and compute | [Foundation](https://lab-emi.github.io/RustEDA/modules/rusteda-common/) |

Engines exchange versioned files and CLI contracts. The shared foundation owns `rusteda-core`, `rusteda-workspace`, `rusteda-formats`, `rusteda-compute` and `rusteda-cli-common`. Mutable design IRs, compute views and UI state remain separate.

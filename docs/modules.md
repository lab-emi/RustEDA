# Architecture

RustEDA is a single clean-room EDA product organized around an integrated application workspace (`apps/`) and five independently versioned engine/foundation submodules (`modules/`). There is no root Cargo workspace; engines exchange versioned binary workspaces (`.rbin`), standard EDA formats (Verilog, Liberty, SDC, SPEF, LEF/DEF, GDSII, AIGER), and structured `rusteda.v1` JSON CLI contracts.

| Component | Responsibility | Location |
| --- | --- | --- |
| **RustEDA CLI** | Unified public entrypoint (`./rusteda`) | `apps/crates/apps/rusteda-cli` |
| **RustEDA Flow** | Native step-graph executor, checkpoints, `--base-rev` locking & recovery | `apps/crates/apps/rusteda-flow` |
| **RustEDA Studio** | Interactive hierarchy, timing cones, RTL cross-probing & shared selections | `apps/crates/apps/rusteda-studio` |
| **[RustSys](modules/rustsys/index.md)** | Bounded RTL synthesis, AIG optimization, Liberty mapping & formal LEC | `modules/RustSys` |
| **[RustSTA](modules/ruststa/index.md)** | Deterministic multi-corner NLDM + SPEF RC static timing analysis | `modules/RustSTA` |
| **[RustROAD](modules/rustroad/index.md)** | Electrostatic placement, CTS, routing, antenna repair, RC extraction & GDSII | `modules/RustROAD` |
| **[RustSpice](modules/rustspice/index.md)** | Differentiable SPICE circuit simulation & discrete adjoint sensitivities | `modules/RustSpice` |
| **[RustEDA Common](modules/rusteda-common/index.md)** | Pinned foundation: arenas, format parsers, mmap workspaces & SoA compute | `modules/RustEDA-Common` |

## Shared Foundation Boundary

`RustEDA-Common` is the sole owner of `rusteda-core`, `rusteda-workspace`, `rusteda-formats`, `rusteda-compute`, and `rusteda-cli-common`. Downstream repositories (`RustSys`, `RustSTA`, and `apps/`) pin one exact `RustEDA-Common` commit revision rather than copying shared code, enforced by `./dev shared`.

[Flow guide](flows.md) · [RustEDA Studio](workbench/index.md) · [Shared foundation workflow](shared-foundation.md) · [Application integration](application-integration.md)

## Latest Verification Milestones (2026-11-20)

- **Three open PDKs (Ibex + hard SRAM)**: Paired quiet-host 3-block confirmations on **IHP SG13G2** (`7.95×` to GDS, `3.27×` signoff, `9/9 PASS`), **clean single-binary non-PGO SKY130A** (`3.55×` to GDS, `631 MiB` RSS, `0/0` DRC, `0/0/0` TT/SS DRVs, `−35.3%` vectorless power, `3/3` bit-exact GDS), and **GF180MCU D** (`1.50×` with metal fill). [Three-PDK benchmark](benchmarks/ibex-three-pdk.md)
- **Multi-design generalization**: `picorv32_top` (`8.39×`), `aes_core` (`6.62×`), and `sha256_core` (`5.40×`) all pass **8/8 independent signoff gates** on IHP SG13G2 (`3.6–4.8×` less RAM), plus dual-PDK SKY130A `sha256_core` (`1.59×`, `0/0` DRC). [Multi-design report](benchmarks/multi-design.md)
- **Tight-clock Fmax stress**: Leads IHP 25 ns (`45.0–49.5 vs 41.3 MHz`, `9.5×` faster) and SKY130A 40 ns (`31.93 vs 26.06 MHz`, `4.15×` faster) while transparently documenting open gaps on ultra-tight IHP 20 ns and GF180 65 ns clocks. [Tight-clock Fmax](benchmarks/tight-clock-fmax.md)
- **Formal LEC state correspondence**: `RustSys` + `signoff_logic.py` now discover reset-reachable re-encoded FSM state maps with proved valid-code invariants alongside arbitrary-initial one-to-one ABC equivalence.
- **Standalone engine benchmarks & archives**: Full stage-by-stage IHP host campaigns (2026-11-02 / 11-05 / 11-13) and post-campaign RustSTA RSS & RustROAD extraction A/Bs remain preserved in [Engine status](engine-status.md) and [Benchmarks](benchmarks.md).

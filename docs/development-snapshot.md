# Development publication · 2026-11-01

**2026-11-02 host campaign.** An Ibex SG13G2/EZ130 benchmark campaign on a
4-vCPU cloud host is [published](benchmarks.md#ibex-host-campaign-2026-11-02)
with its [receipt](benchmarks/ibex-sg13g2-ez130-host-20261102.json). The
receipt's `engine_commits` lists the engine submodule revisions: RustSys
`0f8a2d80654d`, RustSTA `0c2bcc6a5395`, RustROAD `07e1626f220e` and RustSpice
`cc2426f3e6cb`, with RustEDA-Common `06a9cdac2336`, which RustSys and RustSTA
include transitively (RustROAD and RustSpice do not depend on it). These are
the revisions of this 2026-11-01 publication; the receipt identifies each
executable by SHA-256.

The superproject records the following exact module versions. Flow, CLI and
workbench remain in `apps/`; no standalone application repositories or root
Cargo workspace are restored. Original local experiments and historical evidence
are preserved.

| Module | Published source | Current scope |
| --- | --- | --- |
| RustEDA-Common | [`06a9cdac2336`](https://github.com/lab-emi/RustEDA-Common/commit/06a9cdac2336b054b79b4d3647421dcae6cb6a1c) | SRAM-aware formats, connectivity export and f64 RC charge fitting |
| RustSys | [`0f8a2d80654d`](https://github.com/lab-emi/RustSys/commit/0f8a2d80654def4d91d08d6f3791c12726339a53) | Opaque SRAM preservation and bounded interface validation |
| RustSTA | [`0c2bcc6a5395`](https://github.com/lab-emi/RustSTA/commit/0c2bcc6a539580ed43aac1bad6badb82db4976c4) | Mixed libraries, global derates and deterministic parallel RC preparation |
| RustROAD | [`07e1626f220e`](https://github.com/lab-emi/RustROAD/commit/07e1626f220ef1b7f3029ef827fae4b1491c3373) | Spacing-aware placement, bounded routing/extraction and tap/antenna work |
| RustSpice | [`cc2426f3e6cb`](https://github.com/lab-emi/RustSpice/commit/cc2426f3e6cbb1ad05843b22a71632f118f62ddc) | Adaptive simulation, supported device kernels and current accuracy scope |

The isolated publication checks pass **1,955 Rust tests** with 55 ignored,
and 311 Python controls with nine skipped. Ignored/skipped checks are not
counted as passes. Formatting, relevant Clippy/rustdoc, shared-source integrity,
strict documentation builds and rendered links pass. Workbench GUI checks are
reported separately by GitHub CI; local application checks use the headless build.

[Machine-readable source/QA record](development-2026-11-01.json) ·
[Capabilities and experimental branches](engine-status.md) ·
[Retained complete-flow measurements and limits](benchmarks.md)

The 472.2/472.6-second native measurements retain their original frozen ELFs.
Publication-time builds do not replace their qualification identities or establish
an end-to-end result for every new checkout. No new matched LibreLane or GPU
whole-flow comparison was run for this publication; the 2026-11-02 host campaign
above is recorded separately.

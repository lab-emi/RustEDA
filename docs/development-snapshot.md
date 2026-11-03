# Development publication · 2026-11-01

**2026-11-02 host campaign.** An Ibex SG13G2/EZ130 benchmark campaign on a
4-vCPU cloud host is [published](benchmarks.md#ibex-host-campaign-2026-11-02)
with its [receipt](benchmarks/ibex-sg13g2-ez130-host-20261102.json). The
receipt's `engine_commits` lists the engine submodule revisions: RustSys
`0f8a2d80654d`, RustSTA `0c2bcc6a5395`, RustROAD `07e1626f220e` and RustSpice
`cc2426f3e6cb`, with RustEDA-Common `06a9cdac2336`, which RustSys and RustSTA
include transitively (RustROAD and RustSpice do not depend on it). These are
the revisions of this 2026-11-01 publication; the receipt identifies each
executable by SHA-256. The superproject then advanced the RustSys, RustSTA,
RustROAD and RustSpice gitlinks to documentation-only commits that describe the
campaign (table below); their engine sources are unchanged.

The superproject records the following exact module versions. Flow, CLI and
workbench remain in `apps/`; no standalone application repositories or root
Cargo workspace are restored. Original local experiments and historical evidence
are preserved.

| Module | Published source | Current scope |
| --- | --- | --- |
| RustEDA-Common | [`06a9cdac2336`](https://github.com/lab-emi/RustEDA-Common/commit/06a9cdac2336b054b79b4d3647421dcae6cb6a1c) | SRAM-aware formats, connectivity export and f64 RC charge fitting |
| RustSys | [`cf913e571502`](https://github.com/lab-emi/RustSys/commit/cf913e5715023337224f70422b3c5579fecb1c60) (sources of `0f8a2d80654d`, documentation updated) | Opaque SRAM preservation and bounded interface validation |
| RustSTA | [`bfa7aa5fe4d8`](https://github.com/lab-emi/RustSTA/commit/bfa7aa5fe4d8408ea3752a82d4a94bb4762b5a02) (sources of `0c2bcc6a5395`, documentation updated) | Mixed libraries, global derates and deterministic parallel RC preparation |
| RustROAD | [`f844cb819f3c`](https://github.com/lab-emi/RustROAD/commit/f844cb819f3c599f396944cd0d952b1b01aa6717) (sources of `07e1626f220e`, documentation updated) | Spacing-aware placement, bounded routing/extraction and tap/antenna work |
| RustSpice | [`1d190789caa2`](https://github.com/lab-emi/RustSpice/commit/1d190789caa2466713a84b3bf8277cac9b24add2) (sources of `cc2426f3e6cb`, documentation updated) | Adaptive simulation, supported device kernels and current accuracy scope |

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

# Development publication · 2026-11-01

**2026-11-05 host campaign.** The Ibex SG13G2/EZ130 campaign was repeated with
the current sources and is [published](benchmarks.md#ibex-host-campaign-2026-11-05)
with its [receipt](benchmarks/ibex-sg13g2-ez130-host-20261105.json). Its
`engine_commits` are the engine revisions that superproject `397bfa6` pins:
RustSys `86e59d816e6d`, RustSTA `35507399f3a2`, RustROAD `1921481c5f0f`,
RustSpice `d4d6a5043af2` and RustEDA-Common `6668eecae9bb`. Every engine was
built from those clean commits, and the receipt identifies each executable by
SHA-256. These revisions are newer than the 2026-11-01 publication recorded in
the table below.

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

For the 2026-11-02 publication the superproject recorded the following exact
module versions (superproject `397bfa6` pins the newer revisions of the
2026-11-05 note above). Flow, CLI and
workbench remain in `apps/`; no standalone application repositories or root
Cargo workspace are restored. Original local experiments and historical evidence
are preserved.

| Module | Published source | Current scope |
| --- | --- | --- |
| RustEDA-Common | [`06a9cdac2336`](https://github.com/lab-emi/RustEDA-Common/commit/06a9cdac2336b054b79b4d3647421dcae6cb6a1c) | SRAM-aware formats, connectivity export and f64 RC charge fitting |
| RustSys | [`03bb75e5fbd9`](https://github.com/lab-emi/RustSys/commit/03bb75e5fbd9835aea01b1a75eca2eea4fd9964d) (sources of `0f8a2d80654d`, documentation updated) | Opaque SRAM preservation and bounded interface validation |
| RustSTA | [`9996cf113604`](https://github.com/lab-emi/RustSTA/commit/9996cf1136046842c68ae22456f6f6d6278a5d50) (sources of `0c2bcc6a5395`, documentation updated) | Mixed libraries, global derates and deterministic parallel RC preparation |
| RustROAD | [`6c2602c1ed97`](https://github.com/lab-emi/RustROAD/commit/6c2602c1ed974406c52f14f424dfeb4084c83b7f) (sources of `07e1626f220e`, documentation updated) | Spacing-aware placement, bounded routing/extraction and tap/antenna work |
| RustSpice | [`96328622a692`](https://github.com/lab-emi/RustSpice/commit/96328622a6925226e4bdb8389399348b65c8dcd6) (sources of `cc2426f3e6cb`, documentation updated) | Adaptive simulation, supported device kernels and current accuracy scope |

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

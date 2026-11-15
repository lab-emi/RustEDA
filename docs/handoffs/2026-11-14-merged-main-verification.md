# Merged-main verification — 2026-11-14

The merged source builds and its executed regression tests pass. **The handoff's
verification gate is not complete:** `./dev shared` fails, and the published IHP
full-flow byte-identity check has not run. No WIP branch was integrated and no
product source, shared manifest, dependency pin, or commit metadata was changed.

## Baseline

Fetched the remote refs and fast-forwarded the root checkout from `a836971` to
`915b7c8`. Clean engine checkouts were moved to the root's recorded gitlinks;
their local `main` branches were not merged. Existing worktrees and the untracked
`modules/RustSRAM/` were preserved.

| Repository | Verified checkout / fetched remote main |
| --- | --- |
| RustEDA | `915b7c85c77ccbfa5a15b7a55ed00ee5171f87d0` |
| Common | `24498a5b14ab2ba145fe8a0d28a6edc238bf2d9a` |
| RustSys | `3a51abeaebc73835c3bda8810f9d539fdfd61c10` |
| RustSTA | `4f2d2ec652a8bf55b2fbf8c28e7ff1cc260d0a4d` |
| RustROAD | `5aecc5ccd89ab60e8ce49ddee84b320ff7888b66` |
| RustSpice | `32f1132158f2c89e9c4e1186971a287af728601e` |

## Executed checks

| Check | Result |
| --- | --- |
| `./dev fmt` — Common, RustSys, RustSTA, RustROAD, apps | Passed |
| Release workspace builds — RustROAD, RustSTA | Passed |
| Clippy, all targets and all features, warnings denied — RustROAD, RustSTA | Passed |
| Release workspace tests — RustROAD | 599 passed, 11 ignored, 0 failed |
| Release workspace tests — RustSTA | 433 passed, 19 ignored, 0 failed |
| Root Python discovery | 310 run: 296 passed, 14 skipped, 0 failed |
| Native backend protocol tests, bound to newly built RustROAD | 8 passed; covers 4 of the discovery skips |
| Tap layout controls, local KLayout 0.30.12 | 9 methods / 1,507 subtests passed; covers the skipped layout class |
| Strict documentation build | Passed, 553 source files |
| Publication/link checks | Passed, 264 HTML pages |
| `./dev shared` | **Failed**: stale Common manifest |
| Full published IHP flow and output identity | **Unverified**, not run |

Nine Python worker-protocol tests remain skipped because their qualified GNU
time path in `.work/integration/toolchain.json` is unavailable. Ignored Rust
tests include separately invoked PDK/oracle campaigns; these results do not
establish dual-PDK OpenSTA parity or physical signoff. The discovery run did not
reproduce the previous host's 21 GNU-time-related failures.

Rust commands used Rust 1.100.0 through `./dev`, `CARGO_INCREMENTAL=0`, two build
jobs, two test threads, an 8 GiB cgroup, a 1,800-second per-module deadline and
the shared scale lock. Maximum RSS of the entire test command, including
compilation, was 1,473,292 KiB for RustROAD and 837,360 KiB for RustSTA, below the
2 GiB test threshold. These are resource checks, not runtime benchmarks.
Available RAM was about 82 GiB; root disk free space went from about 650 GiB to
644 GiB. No cache or historical evidence was removed; existing module targets
were reused.

## Shared manifest defect

Common `24498a5` changed six files without refreshing their manifest entries:

- `crates/apps/rusteda-cli/src/flow/context.rs`
- `crates/apps/rusteda-cli/src/flow/eco.rs`
- `crates/apps/rusteda-cli/src/flow/helpers.rs`
- `crates/apps/rusteda-cli/src/flow/mod.rs`
- `crates/apps/rusteda-cli/tests/frontend_flow.rs`
- `docs/specs/rusteda-cli/README.md`

Each recorded hash equals the parent commit's file hash; each working file
equals the current commit's blob. The new `flow/profile.rs` is also absent from
the manifest. The canonical generator expects 401 entries instead of 400.
Consumer source manifests and Common dependency pins are consistent; this is
not local source drift. The independent
`python3 modules/RustEDA-Common/scripts/shared_manifest.py --check` also fails.

Repair in a separate Common commit using the existing generator, review the
six changed entries plus the one addition, then propagate the new dependency
revision/lockfiles and root gitlink under the normal child-first publication
workflow. Do not weaken the verifier or rewrite the published commit.

## Tool binding pitfall

The existing LibreLane 3.0.17 AppImage contains KLayout **0.30.7**, whereas the
cloud receipt used **0.30.12**. With unchanged source and fixtures, a bounded
black-box A/B produced eight failed subtest assertions across two methods with
0.30.7, and zero failures with 0.30.12. All eight concern frozen-reference
corner-contact grouping/verdict expectations. The current checker's control
verdicts and conservatism checks passed in both runs. Do not relax assertions
or alter the checker to accommodate the wrong tool binding.

The local 0.30.12 binary hash (`265b6ff5…`) differs from the cloud binary hash
(`b9950f39…`); the passing result is a same-version local check, not exact
reproduction of the published tool identity. Both A/B results are retained.

## IHP identity remains open

The cloud `.work/campaign-20261113`, `.work/multipdk`, `.work/opt-impl` and
`.work/kit-runs` directories are absent here. The local
`.work/ibex-campaign-20261110` has useful collateral and tools, but differs in
RTL (`8a8fe84b…` versus `da905268…`), sv2v (0.0.13 versus 0.0.13.1), and EZ130
tech LEF/GDS (original versus reconstructed files). The old campaign's three
netlists match the published hashes; its physical outputs do not. It cannot
substitute for the required published-flow replay.

Recover or reconstruct and hash-check the cloud inputs and host recipe first,
then run the new binaries in a fresh, bounded workspace and compare against
the full hashes in
[`bringup-artifacts.json`](../benchmarks/evidence/ibex-host-20261113/native/bringup-artifacts.json).
The tracked `integration/ibex_host_benchmark/ez130/restore.sh` is a reconstruction
entry point. Do not mark the GDS/DEF/SPEF identity check passed from a dry run,
unit tests, or the older local artifacts.

## Retained evidence and WIP refs

Local evidence: `.work/merged-main-20261114/summary.json`, individual check
receipts/logs, `shared-audit.json`, `ihp-identity-preflight.json`, and
`tap-klayout-03012/` (tool hashes, wrappers, both logs and resource records).
Rust command logs live in `.work/logs/` at paths recorded in the receipts.
These local files are not published by committing this handoff.

Fetched and preserved, all still outside remote main:

- Root `wip-gf180-bringup`: `0626556f8eec9242a239bba7e131b94717bee23c`
- RustROAD `wip-gf180-routing-fixes`: `55e576abaf206fee90df26fdd7ea6baec0292553`
- RustROAD `wip-extraction-round-four`: `62879ce7780c8eb92d823eddda8f838268b26e16`

Continue from the verification repair and IHP input recovery before the
integration tasks in [the original handoff](2026-11-14-three-pdk-benchmark.md).

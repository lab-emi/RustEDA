# Handoff 2026-11-14: Ibex on SKY130 and GF180MCU, extraction round four

State of the work to extend the Ibex RTL-to-GDS benchmark to two more open PDKs (SKY130 `sky130_fd_sc_hd` with
OpenRAM macros, GF180MCU `gf180mcu_fd_sc_mcu7t5v0` with foundry SRAMs) and to continue RustROAD extraction.
Read `AGENTS.md` first (rules: clean room, evidence, determinism, commit format, bounded resources).

## Requested outcome

1. Ibex + SRAM macros RTL-to-GDS on SKY130 and GF180, RustEDA native flow against LibreLane on one host, same inputs,
   same signoff gates.
2. `docs/benchmarks.md` and the READMEs: a prominent statement before the comparison tables that this is an
   RTL-to-GDS comparison across three open PDKs (IHP SG13G2, SKY130, GF180MCU).
3. The final GDS images of RustEDA and the competitor on all three PDKs, with annotations, showing that RustEDA,
   driven end to end by an AI agent through JSON commands, gives better performance, area and runtime.
4. RustROAD extraction round four: floating-point accumulation order may change (SPEF low-order digits may differ).

## What is done and on the branch

| Item | Where |
| --- | --- |
| Benchmark inputs for both PDKs (RTL wrappers, SDC, LibreLane configs, PDN) | `integration/ibex_pdks/` |
| LibreLane reference layouts (competitor), both PDKs | scratch runs, see below |
| SKY130 reference: Magic stream-out (KLayout's leaves the SRAM empty) | commit `d3ecbfb`, `integration/ibex_pdks/README.md` |
| Profile-driven native flow (`rusteda flow exec` for any PDK profile), IHP byte-identical | superproject, Common `24498a5`, RustROAD `02de830` |
| RustSys: unconnected `inout` supply pins of opaque macros | RustSys `3a51abe` |
| RustSTA: per-library Liberty units, lumped fallback when no driver fits (fixes D0103 and T0077) | RustSTA `76943e2`, `5fa30ad` |
| Shared signoff gates for SKY130 and GF180 (macro identity check, blocked-gate records) | `integration/ibex_pdks/gates/` |
| SKY130 bring-up: pin access, GDS block masters, macro supply, antenna rows | RustROAD merge `5aecc5c`, profile in `integration/ibex_pdks/sky130hd/native/` |
| GF180 profile and bring-up data | `integration/ibex_pdks/gf180mcu/native/` |
| Figure composer and an IHP sample (not in the repo, see "Scratch") | scratch |

**Not verified in the merged state.** The merged RustROAD and RustSTA heads were not built, linted or tested together
(the run was stopped). IHP identity was proved per branch by replay (RustSTA: 51 recorded commands, byte-identical;
flow plumbing: composition, dry run and 13 replayed steps, byte-identical; SKY130 antenna/macro-supply changes:
IHP replay rev2/rev3 byte-identical), not for the merge. First task for the next agent: `cargo fmt --check`,
`cargo clippy --all-targets -- -D warnings`, `cargo test --release` in RustROAD and RustSTA, `./dev shared`, the
Python tests (`python3 -m unittest discover -s tests`; 21 failures exist at the base commit because this host's GNU
time is not the qualified one), `scripts/build_docs.py` and `scripts/check_publication.py`, then a full native IHP
run compared byte for byte with the published run (GDS `ca2e6904…`, DEF `8ff445d8…`, SPEF `468051be…`).

## Work branches not merged (pushed under these names)

| Repository | Branch | Content |
| --- | --- | --- |
| RustROAD | `wip-gf180-routing-fixes` | Macro supply pin choice, long-pin escape in `troute`, placement validation bound, antenna-eco from a selected-net report. **Conflicts** with the merged SKY130 work in `crates/rustroad-workspace/src/macro_supply.rs`, `eco.rs` and `tests/physical_prepare.rs` (both sides changed macro supply access and antenna-eco's partial-report handling). Resolve by keeping both behaviours behind their options, re-run both PDKs' replays. |
| RustROAD | `wip-extraction-round-four` | Uncommitted-at-stop work: per-strip accumulation and a finalization pass (`extract.rs`, `extract/strip.rs`, `extract/parallel.rs`, `docs/extraction.md`). Not built or tested; tests were being rewritten. |
| superproject | `wip-gf180-bringup` | Provisional GF180 RC estimate policy, timing and diode scripts. |

## Remaining tasks

1. **Verify the merged state** (above).
2. **Merge `wip-gf180-routing-fixes`** (conflicts above), with tests and IHP identity replays.
3. **Native flow on SKY130.** The Ibex run reached routing and antenna checks. A diode-planner option to skip nets
   absent from the netlist was being added (RustROAD `039a83e` has the docs of it). Needs `contract.json`,
   `rc-estimate-policy.json`, `selection.json` in the profile (see `integration/ibex_pdks/NATIVE.md`); the host map
   must use the prepared inputs that contain the derived OpenRAM Liberty.
4. **Native flow on GF180.** Synthesis passes on the unmodified prepared RTL (16,370 cells, 8 SRAMs). Next: timing
   (RustSTA with the units fix), placement with 8 macros, CTS, routing, antenna repair, GDS export.
5. **Run the shared gates on all four new layouts** (two native, two LibreLane) with the RustSTA that includes the
   units fix. Known so far on LibreLane's SKY130 layout (OpenSTA): setup slack 40.249/35.638 ns, limit violations at
   standard-cell pins (TT slew 191, cap 27; SS slew 3,026, cap 357; fanout 64).
6. **Timed campaign** on an idle host, both flows interleaved, medians and ranges. The recorded LibreLane times
   (GF180 895 s; SKY130 2,497 s on a loaded host) are provisional.
7. **Docs.** Three-PDK statement before the tables in `docs/benchmarks.md`, the READMEs and the docs index; annotated
   GDS figures for all six layouts (render with `integration/ibex_pdks/figures/render_gds.py`; hide marker layers such
   as areaid 81/x on SKY130 or the image is solid magenta); report losses and unmet gates as they are.
8. **Extraction round four** (`wip-extraction-round-four`), goals: faster than OpenRCX at 2 threads (now 4.77 s vs
   3.14 s; CPU 9.05 s vs 3.12 s), clear margin at 4 threads, RSS toward 202 MB. Deliver a SPEF comparison against
   `468051be…` (counts of differing values, largest differences, structural differences), identical output at 1, 2,
   3, 4 threads, interleaved A/B with 15 rotations.
9. **Docs defect:** `docs/user/sta/README.md` (RustSTA) says the lumped wire-load model adds `R_wire * C_total`;
   the code adds no delay.
10. **Open decisions for the owner:** RustSTA unsafe allocator exception vs an allocator crate; whether to remove the
    earlier handoff files and the commit `4516b6a` on main that carry tool attribution; keep the derived OpenRAM
    Liberty (max-transition lines removed, `RSZ-0090`) or disable LibreLane's repair instead; building the pinned
    open_pdks (the attempt was blocked).

## Reference layouts (scratch, not in the repository)

- GF180 LibreLane: `.work/multipdk/gf180/ll-ref2/` (26,142 cells, 490,812 µm², wire 1,593,932 µm, 133,665 vias,
  0 DRC, 0 antenna in its own report).
- SKY130 LibreLane: `.work/multipdk/sky130/ll-ref2/runs/magic-so/final/gds/ibex_sram_bench.gds`
  (sha256 `d5947a67…`), layout record `layout-librelane.json` (23,665 cells, 176,427 µm², wire 1,053,787 µm).
- Figure composer `.work/multipdk/figures/compose_figure.js` and `.work/multipdk/images/` (IHP sample).
- Local lane directories under `.work/opt-impl/lane-pdk-*` and `.work/opt-impl/lane-x4` hold reports
  (`REPORT.md`), frozen binaries and scripts. `.work/` is untracked and lost if the container is reclaimed.

## Known issues and decisions

- LibreLane on both PDKs needs delay cells excluded (`EXTRA_EXCLUDED_CELLS`); disclosed in the README.
- SKY130: LibreLane's Magic/KLayout XOR is off (it compares against the KLayout file with the empty macro).
- GF180 gates before the units fix: timing was blocked by D0103, recorded as blocked, not as a layout failure.
- Host: 4 vCPUs, about 5 GB disk free; heavy jobs under `.work/kit-runs/.heavy.lock`, cargo under `.build.lock`
  with `--release --locked --offline --jobs 2`. The model usage limit was reached on 2026-11-14.

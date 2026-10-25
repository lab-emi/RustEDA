# Local handoff — IHP SG13G2 / EZ130 campaign

- Keep arbitrary initial state. No reset sequence, input assumptions, state constants or sharing were introduced by the exporter. Every source state remains free; all gate states need one-to-one coverage plus complete external CEC.
- EZ130 8T v1.1 TT 1.20 V /25°C: original Liberty hash is 847cb30b38f15ef0b9c4a735cfaf96c1782c013b1952261b25e5e38cd3ae072c. TIEHI/TIELO driver_type tags conflict with the vendor Verilog constants. Keep the explicit, certified verification-only functional supplement and its scope visible. Never alter the original timed/STA library or silently permit high impedance.
- Freeze benchmark producers. Work is under .work/ez130-20261022; measurement repetitions are in measurements-v2. Do not rebuild or overwrite target/release executables or any checker already pinned by proof receipts. Rust 1.97.1 is reached via /home/cgao/.cargo/bin/cargo; /usr/bin/cargo uses another compiler.
- Yosys OpenC910 loses 1230 public Q names after mapping; 1213 require new pairing. Public pre-ABC Verilog plus scalar FF order/name anchors/inverter aliases produces hints. These are untrusted. Use ez130_state_hints.py and check_config state_proposals_file; full output/event CEC is mandatory. Guessing instance boundaries was insufficient and those guesses were removed.
- ABC default &cec has one hard group (1432). Its public -x solver proves the same group quickly. New-first/default-fallback is bounded; never retry an explicit counterexample as a timeout. Both algorithms are externally calibrated with correct, bad-data, bad-clock, opposite-reset encoding, bad-phase and bad-reset controls.
- Multiprocessing driver entrypoints need an if __name__ == "__main__" guard. Otherwise spawned workers can reenter orchestration and deadlock on scale.lock. Keep failed receipts; fresh projections must be generated from checked source AIGER files.
- Full public metadata export is about 1.3 GiB; max_file_bytes applies to all child files, not only logs. Use the tracked capture helper with a 2 GiB file bound. The initial local capture used a separately capped FIFO collector; its receipts are retained. Guard large imports/STA with cgroups, timeout, explicit AS cap, and scale.lock; read free -h first.
- EZ130 is 16-core-only. Keep the Sky130/GF180 1/16-core baseline unchanged. Do not infer EZ130 self-scaling. SERV/EZ130 uses about 6.6% more cell area than Yosys. OpenC910 raw setup-Fmax exceeds load characterization (52.44/35.84 pF vs INVX1 limit 0.1 pF) and is not achievable frequency.
- Publish only after ten new LEC and STA results are complete. additional_report.py combines fifteen design/PDK rows on README, docs homepage and benchmarks page; it checks hashes, controls and all measurements. Use docs/_tools checks and the current xtask binary. An old target/debug/xtask predates the LICENSES directory allowlist.

## CI repair — 2026-10-23

- The fixture generator used remove_dir_all on the whole synth fixture tree. That deleted six handwritten RTL files and the independent Icarus truth table, even though every generated file already matched. Keep generation scoped to owned output files; regression data and old benchmark source hashes must survive byte-for-byte.
- estimated_delay intentionally remains the original mapper proxy. Its strict cover-estimate test must disable default cleanup. Validate final counts/depth separately after cleanup; do not equate estimates with different scopes or relax the mapped-cover equality test.
- Full --no-fail-fast exposed two later failures hidden by the first failed target: the metamorphic helper only emitted a sequential model when next_state was one pin (dropping enabled FFs), and the random Minimal-library test could not recognize missing latches because S0520 did not identify the state kind. Emit general scalar Boolean next-state functions with both internal state polarities, and report latch/flip-flop in the mapping diagnostic.
- Use .work/ci-fix-20261023/target for all builds; original frozen benchmark binaries and artifacts remain untouched. Full debug/release suites use two build jobs/two test threads, an 8 GiB cgroup plus 2 GiB swap, OS AS/wall guards and scale.lock. Set RUSTEDA_VERIFY_NO_SCOPE=1 inside the outer scope so nested verification stays in the same aggregate guard.

- Local final validation: 806 workspace Rust tests passed; release synthesis suites, all-feature Clippy, fixture regeneration twice with zero repository changes, layout/shared, 112 benchmark Python tests and strict docs checks passed. Evidence: .work/ci-fix-20261023/validation-summary.json.

- Published CI fix commit 6da36e07b0c520ccf091115b9229ff1d6856bf99. GitHub CI 39931772669: static, test, deny, reuse all succeeded; workspace plus release verification both passed. Docs run 39931772818 also succeeded. Historical failed runs remain as history.

## SERV / EZ130 area correction — 2026-10-23

- Final core producer is .work/serv-area-20261023/binaries-v6. Do not overwrite it. Its full 15-row 16-core measurements are measurements-v6; final proofs/timing and strict area/Fmax gates are validation-final. SERV/EZ130 is 64,549.6992 µm², 0.31 s, OpenSTA 181.488203 MHz. All 30 tool/design/PDK entries retain arbitrary state, without reset/input assumptions.
- Narrow non-power-of-two reads should use actual library mux/product costs, not blanket global AIG heuristics; blanket changes regressed Sky130. selector_costs is bounded and uses no PDK/design names.
- Full sequential sizing before buffer selection regressed Ibex/EZ130 (55.13→52.91 MHz). Restrict compatible FF drive upgrades to the no-affordable-buffer fallback. v6 restores Ibex/Pico Fmax and retains SERV gains. Never weaken the area/Fmax gate.
- Private multiprocessing entry points also need __main__ guards. Initial C910 export was complete, but an unguarded caller deadlocked spawned workers on scale.lock. Preserved the interrupted receipt, killed only own worker tree, and used guarded resume-c910.py with fresh projections of hash-checked exported AIGERs. 1,454/1,454 slices passed.
- Put timeout inside flock so waiting for long benchmarks does not consume the actual check budget. The initial queued Clippy timed out without running; final Clippy, release synthesis suites, and 810 workspace/114 Python tests pass.
- Scaling harness holds scale.lock for the entire campaign. Do not plan supposedly concurrent CEC/static jobs behind it. Peak RSS is measured; full-core OpenSTA needed about 17 GiB under a 32 GiB cap.
- Preserve raw timing controls. Sequential old-then-new runs and alternating runs vary. Interleaved SERV: Sky .37/.36, GF .76/.77 (one 10 ms reporting unit, overlapping ranges), EZ .34/.32. Vex controls also retained; do not claim universal runtime equality from identical netlists.
- SERV/EZ130 timing is extrapolated before and after: old OAI21X2 .5768 pF vs .20 pF characterization, new DFFQX6 2.451 pF vs .60 pF. Document this alongside existing OpenC910 limits; raw OpenSTA numbers are not physical closure or achievable frequency.
- Current public archive is results/2026-10-23-serv-area. Earlier 1/16-core archives remain historical and immutable; Yosys timings are retained with explicit provenance.

- Published optimization commit9afbf2bd922a51f2983f602190741e6e0d473ae8. Docs39943210386 passed and deployed; live SERV row checked. CI39943210378 has passed static/deny/reuse, awaiting test job.

- Final remote checks for9afbf2bd922a51f2983f602190741e6e0d473ae8: CI39943210378 all four jobs passed; Docs39943210386 build/deploy passed. Live table verified updated SERV row. Worktree clean.

## Shared crate convergence with the timing tool — 2026-10-24 (branch feat/shared-convergence, .work/wt/shared)

- Both tool repos now vendor the same 209 shared files (manifest a6c423a6...; `cargo xtask shared diff OTHER` clean both ways). 35 files took the timing tool's newer version (STA store v2 reader 1..=2, rlib v4 Pin pulse fields, SPEF reader/import, edits, rc/ArcValues compute, typed set_propagated_clock), 15 took ours (frozen export, freeze_writer, parallel writer, lexer `:`+comment fix, fast reader, B/G letters, xtask DOCS), 8 were merged by hand.
- Requirement collision: our published RCLI-REQ-0021/0022 (BLIF/AIGER import) kept; the timing tool's unpublished show-DRV requirement became RCLI-REQ-0023. Diff bold requirement definitions of both spec trees before any future sync.
- Identity vs the pinned c5ec23c producer: 283 cases identical (all fixtures x wt_full/rich/minimal/reset_only/sky130/gf180, two option sets; SERV and PicoRV32 workspace flows incl. netlist.rbin); new native checker 282/282 equivalent. Script and results: RustROAD/.work/shared-w19 (ident_sys.py). The pinned producer has no rusteda: build c5ec23c's from `git archive` into a private target.
- set_propagated_clock now parses (no D0010); opt-timing reports it as S0535 (test added in workspace_opt.rs). D0021 wording is neutral. A cached RLIB v3 is recompiled transparently.
- Gates: workspace tests 837 passed/0 failed (first run), verify synth, verify pdk --require (9 libs), all-feature clippy, fmt, layout, coverage 619/0 (requirements.md regenerated; it was stale), fixtures unchanged, deny, reuse, docs strict build, 119 benchmark Python tests. Builds in .work/wt/shared/target only.
- Fix round 1 (commit 6c82852, manifest 6d8e288b...): the verifier found a third-party program name in the shared test `rusteda-cli/tests/import_netlist.rs` (doc comment, fn name, temp dir) that came in from the timing tool. Lesson: before any sync, grep every file listed in verification/shared.sha256 for third-party program names AND for repository names (`RustSTA`, `RustSys`, ...); shared text uses roles ("the timing tool"). Rule-check the whole shared set, not only the files you changed.
- The 64 MiB AST-fallback budget is now `rusteda import --max-netlist-ast-mib` (default 64, RCLI-REQ-0024; Rule 9.2 wants budgets configurable). Measured full-reader peak RSS: ~42x file size (26 MiB VexRiscv netlist: 1.07 GiB vs 95 MiB streaming). A reversed part-select (`a[1:3]` on `[3:0]`) or a >32-digit decimal constant makes the streaming reader decline; `--max-netlist-ast-mib 0` is a cheap way to prove which reader took a file.
- Round-1 identity: 283/283 identical vs c5ec23c (pinned producer + rusteda rebuilt from git archive), 282/282 equivalent; 16 corpus netlists + a declined VexRiscv variant import to identical tables. Gates: tests 838/0/19, verify synth 85/0, verify pdk 279/279, clippy, fmt, coverage 620/0, deny, reuse, docs.
- sta3 rc.rs port (commit 7b53aaf, manifest bc3bee3d...): the timing tool's additive
  `rc::gate_detail_with_resistance` copied byte for byte, manifest regenerated with `cargo xtask
  shared update`; committed trees identical for all 209 shared paths. rustsys hash changed
  (2e3175dd) but outputs did not: 283/283 identical vs the pinned c5ec23c producer, 282/282
  equivalent; rusteda hash unchanged (523c68b1). Gates: tests 839/0/19 (4:01, 778 MB),
  all-feature clippy, fmt, layout, shared, coverage 620/0, fixtures unchanged, rusteda-compute
  CUDA lib tests 41/0. Logs: .work/wt/shared/.work/r2.
- PITFALL: ident_sys.py's old flows must use a rusteda built from c5ec23c (`git archive` into a
  private target); a newer rusteda writes stores the pinned rustsys rejects (synth exit 1, empty
  stderr), which shows up as 279/283 with an empty `differ` list.

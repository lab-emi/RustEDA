# RustEDA integration handoff

## 2026-10-25 — Claude closeout qualification completed

- Qualified all 16 baseline pairs: 15 ordinary passes and 1 explicit MPW deviation;
  no failed or unverified gating items. Full source: RustROAD 1f86687baaf7e6753addb9351b83f3561fd714f4.
- Current report and unresolved-item inventory: docs/closeout/STATUS.md. Machine evidence:
  integration/evidence/claude-closeout.json; detailed module evidence is linked there.
- Every row includes two engines/two SPEF sources, required PDK corners, DRC/LVS/antenna,
  equivalence/co-simulation, negative controls, deterministic rebuild and artifact binding.
- The old headline miscount (10/16, not 11/16), mixed SERV licenses, tool portability and
  ineffective hold mutation were corrected. A failed MPW circuit vote was not waived:
  clock loading was improved and reverified under the unchanged thresholds.
- RustSTA now has an explicit tested SPEF topology safety boundary; OpenC910 raw case
  recovery, full latch timing, high-R Ceff, scale and independent human reviews remain open.
- This completes the reproducible baseline closeout, not every research or signoff item.
  The next RustLane integration phase has not started. Original siblings and their WIP are intact.

## 2026-10-25 — Claude closeout implementation and audit notes

- User requested auditing and finishing the unfinished work in RustEDA-status-2026-10-25.md
  before starting the next phase. Do not start the new RustLane integration phase yet.
- Root, RustROAD and RustSTA work is isolated on fix/claude-closeout. Original sibling
  repositories and their existing uncommitted work remain untouched.
- Scope and status are tracked in docs/closeout/STATUS.md; the original report and accepted
  owner decisions are preserved alongside it.
- Prioritize the six failing RTL-to-GDS rows: GF180 DRV and the explicitly approved MPW
  declaration rule. A declaration requires all three existing evidence conditions; it must not
  silently turn a timing mismatch into an ordinary pass.
- The original report's headline miscounts its own matrix: 10/16 passed and six failed,
  not 11/16 and five. Compute counts from the rows, never copy the old headline.
- The migration README confirms original large scratch (raw netlists/SPEF/GDS/logs) was
  deleted before this task. Retained summaries and frozen binaries are historical evidence;
  regenerate missing raw observations before making a current acceptance claim.
- Final qualification ran through `.work/closeout/qualify-final.py`, with results written
  incrementally to `.work/closeout/accepted-results.json`. It uses the immutable worktree
  `.work/closeout/qualified-road-final2` at 1f86687baaf7e6753addb9351b83f3561fd714f4.
  Do not edit that worktree while the runs are active. The earlier 17fb611 cohort is recorded
  as superseded: it caught the non-faulty dense hold mutation, the mult circuit-vote failure,
  and regfile's endpoint/annotation issues. Both revised clock-load pilots pass their normal
  timing and physical checks; full controls and deterministic rebuilds are now running.
- Reference tools remain black-box only. Their source and rule-deck internals must not be used
  to explain discrepancies. Legal/foundry/independent-human signoff cannot be self-certified.

### Upfront resource budget

- Audit reads and source comparisons: <128 MiB, bounded file inventories and streamed hashes.
- Small regression designs: at most about 5,000 pins / 20,000 arcs, three corners and two
  test workers. Even at 256 bytes of per-pin/tag state and 128 bytes per arc, this is tens of MiB;
  immutable PDK tables dominate. Existing bounded graph/path/solver work limits remain active.
- For physical gates, plan up to 8 million routing-grid states at 16–64 bytes each (128–512 MiB),
  plus bounded frontiers, PDK/library data and subprocesses. Historical small-design runs were
  below 1 GiB per child; retain a 2 GiB child target and an 8 GiB aggregate cgroup where possible.
- Builds use two Cargo jobs; tests use two threads. Heavy verification is serialized with
  .work/scale.lock, explicit memory/swap and wall-clock caps, with measured peak child RSS.
- SERV/OpenC910 scale tests require a separate estimate from the actual graph/input size.
  Do not start them by extrapolating the small-design budget or by removing hard limits.
- The reviewed DRV candidate runs in an immutable separate worktree. At most three GF180
  gates run concurrently, each with the existing 2 GiB child process limits and two tool
  threads. Estimated combined peak is <=6 GiB plus controller state; the entire group is
  capped at 8 GiB RAM / 2 GiB swap, serialized behind the global scale lock.
- The first physical batch includes a SKY130 FIFO before the three GF180 candidates, still
  at most three concurrent gates, so fresh MPW data is available while DRV runs continue.
  MPW probe work is separately capped: at most 8 nets, 64 perturbations per net, 32 MiB SPEF,
  10,000 circuit nodes / 50,000 elements and 16 MiB logs; no path enumeration is introduced.
  Separate rise/fall reference steps require per-row brackets, with shared cached observations.
  A single-net prototype used 511 MiB peak child RSS; each probe stays inside a 2 GiB scope.
- Final qualification uses the same three-runner bound for all 16 design/PDK pairs, behind
  the global scale lock, with an 8 GiB / 2 GiB swap scope and a 7,200 second group deadline.
  Latch-polarity and RC probes have fewer than 100 electrical nodes each; their 1–2 GiB
  process scopes measured below 400 MiB. They do not justify any large-design memory claim.
- The full 17fb611 run rejected mult/SKY130's declaration: some independently characterized
  circuit points do not favor RustSTA. Preserve that failure. A bounded physical pilot halves
  clock-leaf fanout (8 -> 4), improving load/slew without touching the SDC or parity threshold.
  One extra pilot is estimated below 1 GiB RSS and capped at 3 GiB / 512 MiB swap / 1,200 s;
  the existing three-runner cohort remains capped at 8 GiB. Available host RAM was 64 GiB.

## 2026-10-25 — Phase 2 shared foundation completed

- The owner explicitly chose a private foundation repository: https://github.com/lab-emi/RustEDA-Common.
  The fixed foundation revision is 7e78e705908a3475018ab39df94b94423e158a13.
- Four library implementations and the shared CLI now have one source. RustSys, RustSTA and
  Studio consume the same Git revision and retain thin product CLI adapters. Their original
  versions, command outputs and workspace history are preserved. No EDA algorithm changed.
- Consumer commits are on feat/shared-foundation: RustSys 6012cf66e333c0719ba44c13dda33746193e6406,
  RustSTA 9bfafdc3cc1d59a3330e269d26f844f667c78a93,
  Studio 084aeb453268fbfd3fe621eeb62e8588aa7ae597. Original child main branches and sibling
  workspaces, including the original RustLane WIP, remain unchanged.
- Final results: Common 403 passed / 10 ignored; RustSys 467 / 6; RustSTA 411 / 17;
  Studio headless 136 / 2; Studio GUI test platform 63 / 0; root safety tests 12 / 0.
  Layout, shared manifests, requirement coverage and Clippy pass for all affected consumers.
- The 161 original library files are byte-identical. All original shared test functions were
  retained (401 each for RustSys/RustSTA, 327 for Studio). Canonical manifest: 267 files;
  consumer adapter/specification manifests: 36 files each and identical.
- Frozen pre-migration CLIs and new CLIs match on 10 commands x 2 pinned PDKs for both products,
  including raw table hashes, exported netlists, errors and history. The wrong-product-version
  negative control is rejected. Studio reads both resulting workspaces with the expected hierarchy
  and explicitly reports timing not_analyzed.
- Full evidence: integration/evidence/shared-foundation.json and docs/shared-foundation-validation.md.
  Raw logs, binaries and library data remain under .work/phase2 and are not published as source.

### Remote acceptance and Git tracking lesson

- Development-entrypoint CI passed on d82d8b2:
  https://github.com/lab-emi/RustEDA/actions/runs/40159524460 .
- A new recursive shallow clone at d82d8b2 reproduced all six pins, passed doctor, resolved the
  same Common revision in all consumer Cargo metadata, and performed a no-op initialization.
  Clone time: 40.080 seconds; max child RSS: 146,664 KiB. Use .work/phase2/remote-clone-final
  for this receipt, not the earlier attempt made before root publication.
- Initial module clones used single-branch fetch refspecs. Git push --recurse-submodules=check
  could not see already-published feature commits through those local tracking refs. The local
  integration checkouts now fetch refs/heads/* and explicitly fetched the feature branches;
  the recursive push check passed. No check was bypassed and no original sibling config changed.

### Scope and known baseline issue

- Source, license and version policy checks pass for Common/RustSys/RustSTA. Studio passes
  source/version checks but its pre-existing license allowlist rejects cbindgen/MPL-2.0,
  dwrote/MPL-2.0 and libfuzzer-sys/NCSA. The unchanged Studio baseline reproduces all three.
  No license rule was relaxed. This remains a separate release-policy issue.
- Common private CI passed: https://github.com/lab-emi/RustEDA-Common/actions/runs/40157777739 .
  Do not inject private foundation credentials into public workflow logs or caches. Full cross-repo
  CI belongs in a private integration environment; public child mains were not migrated.
- This is packaging/API compatibility evidence, not new timing-accuracy, RTL-to-GDS, CUDA/Metal
  or real desktop-rendering signoff. Stage 3 remains RustLane integration and explicit tool/PDK setup.

### Budgets and lessons

- Upfront source budget: linear hashing, at most 10,000 paths / 64 MiB, expected <128 MiB RAM.
  No new graph paths, cuts, cones or solver clauses are materialized.
- Builds ran serially behind .work/scale.lock with two build jobs/two test threads, 8 GiB RAM +
  2 GiB swap and finite deadlines. Maximum observed child RSS including compilation was
  2,057,184 KiB, below 2 GiB. Measurements use scripts/measure.py because GNU time is absent.
- Use PATH/rustup discovery: the old /home/cgao/.cargo/bin/cargo path is gone, but /usr/bin/cargo
  still selects Rust 1.97.1. Frozen sibling binaries and original target directories were untouched.
- Test fixtures, coverage and verification plans must locate shared source through Cargo metadata;
  checking old product-local paths can omit suites or fail source guards. A regression checks this.
- Shared manifests sort by the repository-relative string, not pathlib component ordering.
  Use the product's cargo xtask shared update to obtain the canonical ordering.
- Studio's old >100-local-shared-files assertion was replaced with explicit canonical-source and
  duplicate-source checks. Source relocation must not be papered over by lowering a count threshold.
- Product documentation helpers use each product's required copyright header. They are not one of
  the verbatim shared implementation files.
- Studio's unconditional GPUI dev-dependency compiles even for headless tests. Preserve the populated
  phase-2 targets; GUI-test feature isolation is future work, not grounds to skip current validation.
- Saved CLI basenames affect clap help. The parity harness uses identical argv[0] via executable=,
  and normalizes only timestamps and machine-specific paths, never versions or semantic results.

## 2026-10-25 — Initial superproject

- User authorized implementing the reviewed aggregation plan at ~/git/RustEDA.
- This repository owns development instructions, gitlink version combinations and integration
  evidence. It deliberately has no root Cargo workspace and no runtime daemon.
- Five modules are independent checkouts. Existing sibling repositories, their working branches,
  uncommitted changes and frozen target binaries must not be moved or overwritten.
- RustSys / RustSTA / RustROAD start at their current local committed feature versions, published
  for reachability on dedicated integration branches without updating the existing main branches.
- RustLane starts at its committed 6c4824a baseline. The original ~/git/RustLane working tree has
  uncommitted post-route work that has NOT been copied, committed or discarded.
- Studio is an independent initial snapshot of 271 source-controlled candidate files from the
  original unborn repository. Its original working tree is unchanged. Its functionality has
  not been revalidated by this integration.
- Original handoffs are archived under docs/handoffs. Treat their paths and version-specific
  statements as history; check current code and evidence before acting.
- Root and Studio repositories are private. Existing project visibility is unchanged.

## Development rules

- Read AGENTS.md, this file, and the involved modules' local instructions before starting.
- Gitlinks are the authoritative module versions. .gitmodules records URLs and paths, not SHA pins.
- Initialization refuses dirty or displaced module checkouts; builds and tests accept active
  feature branches and record the actual source HEAD and dirty status.
- Each concurrent agent uses an independent checkout. Do not edit a directory during a running
  verification campaign. Root .work/scale.lock serializes high-memory root commands.
- All development build outputs go to root .work/target/<module>; original sibling target
  directories and existing proof receipts remain untouched.
- RustSys and RustSTA shared manifests are expected to match. Studio carries an older snapshot
  and needs a separate reviewed convergence change. Do not present this as already solved.
- Some upstream verification scripts still reference the former /mnt/data2/cgao/git path.
  They need explicit path configuration during future integration; this bootstrap does not
  modify engine source or claim existing full-flow harnesses are portable.

## Resource estimate before bootstrap tests

- New tests exercise tiny temporary Git repositories and development-script safety, not EDA graphs.
- One Python test runner plus one Python helper and Git child at a time: approximately
  3 x 40 MiB plus <= 16 MiB fixtures and output, conservatively < 256 MiB peak RAM.
- No graph nodes, arcs, corners, paths, cuts, SAT clauses, Cargo build workers or Rust test
  workers are allocated by this suite. Tests are serial.
- Use a 512 MiB address-space limit and a 90-second wall-clock guard.
  This host has no GNU time; scripts/measure.py records actual child peak RSS using getrusage.
  Store logs and measured RSS under .work/bootstrap.
- Cloning existing objects is below 200 MiB on disk; source checkout sizes total about 200 MiB.
  No .work caches, PDK downloads or reference-tool source are imported.

## Verification scope

Bootstrap checks validate Git initialization safety, fixed revisions, shared-file integrity and
development orchestration. EDA algorithm, dual-PDK signoff and GUI verification remain unchecked.
Recorded results and outstanding work are updated here before handoff.

### Local results

- Nine independent temporary-Git safety tests passed after the final script changes.
  Final measured peak child RSS: 23,412 KiB; measured wall time: 1.674 seconds.
- All five module formatting checks passed under individual 512 MiB cgroups and 120-second
  deadlines. No engine build output or original frozen binary was touched.
- Actual shared-file hashes validate: RustSys 209, RustSTA 209, Studio 181.
  RustSys and RustSTA are identical; Studio has 88 divergent or missing paths and is explicitly
  pending convergence.
- Original sibling HEADs, branches and tracked/untracked status were rechecked unchanged.
  All 271 original Studio file hashes match the independent snapshot.

### Remote acceptance

- https://github.com/lab-emi/RustEDA and https://github.com/lab-emi/RustEDA-Studio
  were created private. All existing repository visibility and main branch heads are unchanged.
- Required source commits are available on integration/rusteda-bootstrap-20261025 in
  RustSys, RustSTA and RustROAD; Studio has its initial snapshot on main. The root main
  branch was pushed only after checking child-commit availability.
- A fresh recursive shallow clone from GitHub succeeded under a 2 GiB cgroup:
  36.292 seconds end to end; measured maximum individual child RSS 148,488 KiB.
  All five modules match their gitlinks; ./dev doctor succeeds; ./dev init is a no-op.
- Development entrypoint CI passed on 270513a:
  https://github.com/lab-emi/RustEDA/actions/runs/40156966383
  This tests orchestration with synthetic repositories and does not certify EDA algorithms.
- Next work: review/import original RustLane post-route changes; converge Studio's shared
  layer; configure real PDK/tool identities and establish reviewed end-to-end integration gates.

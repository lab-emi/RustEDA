# RustEDA integration handoff

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

# LibreLane contract research

Read the [Chinese research report](librelane-contracts.html). It records source observations and proposed RustEDA flow contracts; it does not certify that RustEDA flow implements those contracts.

- LibreLane: [`a09d248c49bd1f7170d41885936f63e4255b1fe5`](https://github.com/librelane/librelane/tree/a09d248c49bd1f7170d41885936f63e4255b1fe5), declared version `3.0.16`, Apache-2.0.
- [REA HistoricalSourceSummary](rea-librelane-summary.json): genuine, unmodified output from `rea-agents@6.7.0`.
- [Provenance and source hashes](provenance.json): independent receipts, source URLs, exact command, package registry identity, resource limits and limitations.

REA hashed 397 regular files (11,994,940 bytes) in 82 directories. Its inventory is `partial`: `.git/` is excluded. The checkout also has two uninitialized test submodules; their contents were not fetched. REA reports no parsed relationships. It inventories the Python tree; it does not establish Python semantics or LibreLane runtime behavior. `dirty: null` is preserved from REA. A separate `git status --porcelain` observation was empty.

The user explicitly requested REA. Its skill normally routes ordinary source architecture work to repository tools, so REA was used for the narrow provenance inventory. Public documentation and the Apache-2.0 orchestration declarations supplied the behavior contracts. No GPL/restrictive reference-engine source was inspected, copied or translated.

## Reproduce the inventory

Run from RustEDA flow, outside the REA source checkout. These commands download only LibreLane and the pinned npm package, without installing a client configuration or global package:

```bash
mkdir -p .work/reference .work/research
git clone --depth 1 --no-recurse-submodules \
  https://github.com/librelane/librelane.git .work/reference/librelane
git -C .work/reference/librelane fetch --depth 1 origin \
  a09d248c49bd1f7170d41885936f63e4255b1fe5
git -C .work/reference/librelane checkout --detach \
  a09d248c49bd1f7170d41885936f63e4255b1fe5
free -h
/usr/bin/time -v -o .work/research/rea-time.txt \
  systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0 \
  timeout --kill-after=10s 180s \
  env NODE_OPTIONS=--max-old-space-size=1024 \
  npx -y rea-agents@6.7.0 import-reference-source \
  "$(pwd)/.work/reference/librelane" --detail summary --outline-depth 1 --json \
  > .work/research/rea-librelane-summary.json \
  2> .work/research/rea.stderr
```

Before the actual run, available host RAM was 48 GiB. The refined estimate was 479 inventory entries × approximately 1 KiB metadata, plus 12 MiB source bytes and npm/Node overhead, below 2 GiB RSS with one process family. There were no PDKs, corners, path/cut/SAT enumeration or test workers. The actual cgroup-guarded run exited 0 in 5.16 s; `/usr/bin/time -v` measured 285,428 KiB maximum RSS. That measures the REA invocation including package acquisition, not an EDA flow or synthesis/STA benchmark.

Complete local receipts are under ignored `.work/research/`; the reference checkout is under ignored `.work/reference/`. The checked-in summary and provenance are small portable research artifacts. Registry signature metadata is recorded but was not independently signature-verified.

## Remaining verification

No LibreLane flow or PDK was executed. Runtime equivalence, synthesis CEC, RustSTA versus OpenSTA on Sky130 and GF180, full/incremental consistency, physical design, DRC and LVS remain unverified by this research. A missing reference binary or PDK must remain `unverified`. The report identifies concrete gates for those later runs.

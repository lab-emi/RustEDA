# Integrated applications

RustEDA owns the flow engine, command-line program and Studio workbench in `apps/`.
They are ordinary versioned source files in this repository. The remaining submodules
are RustSys, RustSTA, RustROAD and RustEDA Common; each keeps its independent history.
There is no Cargo workspace at the repository root. The applications share
`apps/Cargo.toml` and one lockfile, and consume the exact Common revision.

The public entrypoint is `./rusteda`: `flow` manages the qualified physical flow,
`studio` opens the workbench, and other commands operate on design workspaces.
`./dev` remains the resource-bounded developer tool. Use `./dev build RustEDA`
for applications, and `./dev build RustEDA --no-default-features` for a headless build.
The native `rusteda flow` API also accepts explicit flow configuration files.

The internal crate is named `rusteda-flow`. Legacy `rustlane.*` on-disk schema
names and the Tcl variable ABI remain readable so existing workspaces, checkpoints,
producer receipts and historical qualification evidence are not rewritten.
A fresh toolchain uses the integrated `rusteda` executable. Reconfigure the local
toolchain after upgrading; historical binary paths remain archived.

## Migration and validation budget

Before edits, archive both published Git histories and all tracked working files,
including their uncommitted branding changes. Import the published heads as merge
parents so source history remains reachable after repository deletion. Keep the
local archive refs, original Git databases and experimental worktrees intact.

This change introduces no graph algorithm. Source hashing is streaming, one file
at a time (<128 MiB estimated). Tests reuse existing capped graph/flow fixtures:
per-node state and arc/tag vectors, path/cut bounds and solver budgets are unchanged.
Headless compiler peak is estimated at 2 GiB per job, one job at a time, plus
bounded test fixtures (<512 MiB per thread, two threads). Runs use a 4 GiB cgroup,
a finite timeout and measured RSS. Documentation uses a 2 GiB cgroup and its existing
2,500-file / 256 MiB input cap. No large-design routing is needed for migration.
For the GUI checks, the planning envelope is 5,000 nodes × 256 bytes plus
20,000 arcs × 64 bytes, one corner and two test threads (about 5 MiB of graph
state), with bounded view buffers. Budget 3 GiB per compiler and 512 MiB
for test/runtime overhead. The GUI test build uses two compilers under an 8 GiB
cap and the exclusive scale lock; the initial single-job check uses 4 GiB. Existing hard node, edge,
cone-depth and layout budgets remain in force; no full path list is introduced.

Validation checks the native flow CLI, unchanged workspace revisions, shared pins,
workbench model tests, CLI contracts, negative controls and fresh source checkout.
Physical qualification, when run, reuses the existing independent producer gates
on SKY130 and GF180. Missing or unexecuted checks remain unverified.

The standalone RustLane and RustEDA-Studio repositories were deleted on 2026-10-26
after the published RustEDA revision passed a fresh recursive-clone check. Their
published histories remain reachable in RustEDA. [Deletion receipt](../integration/evidence/application-repository-cleanup.json).

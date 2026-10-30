# Ibex B4 signoff scheduling

Worktree `.work/perf-20261030/root-signoff`, branch `codex/ibex-perf-signoff`.
The existing main checkout and published run16/reference evidence are untouched.

`native_backend.py` accepts an explicit `parallel_signoff: true` policy for new
recipes. The producer executes main DRC alongside antenna→LVS and OpenRCX→timing,
with three disjoint two-physical-core lanes, dependency-gated input rendering,
manifest-order receipts and global concurrent log-byte accounting. Existing
recipes and internal stop-prefix semantics remain serial. The outer owner
retains whole-cgroup cleanup on failure; the coordinator kills its owned groups.
Affinity is inherited across exec/setsid; the enclosing worker cgroup sets the
hard CPU boundary for the complete worker, rather than each individual lane.

Upfront estimate: run16 main/antenna/timing RSS sums to about 4.5 GiB, plus
<128 MiB controller. Explicit simultaneous reservations peak at 5888 MiB under
a 7168 MiB admission cap and the existing hard 8192 MiB cgroup. The replay
holds root `.work/scale.lock`; physical input copies/hashes count in sample time.

Pitfall: LVS's inherited guard compared every newly appearing PID in the shared
producer cgroup. Concurrent siblings can therefore look like detached LVS
children. The guard checks the pinned, inherited disjoint CPU lane for an
explicit parallel step; serial whole-cgroup PID checking is retained. Outer
whole-cgroup cleanup remains mandatory for failed or interrupted producers.

Validation: 20 real-process/protocol controls pass, including overlap,
affinity, dependencies, memory serialization, cancellation, logging and serial
prefix behavior. GNU time records 27,132 KiB peak RSS under a 1 GiB/60 s guard.
A real composition control regenerates the backend with only `parallel_groups`
differing between serial and parallel recipes, and preserves all sixteen gate
bindings in the full 123-step manifest (23,432 KiB RSS, 1.65 s).

The copied-artifact serial/parallel replay is retained in
`.work/perf-20261030/signoff-replay01`. Both workers ran under their own
8192 MiB/2048 MiB-swap cgroup, six physical CPUs and 1800 s deadline, with the
pair holding the root scale lock. The complete worker envelopes, including
copying/hashing physical inputs, were 690.861 s serial and 347.049 s parallel.
Peak reaped process RSS was 2,268,640/2,268,488 KiB (KLayout antenna), and cgroup
peaks were 3,144,572,928/4,159,455,232 bytes; neither used swap. Main and antenna
reported zero violations, full-device LVS passed, and both timing/parity checks
passed. These are signoff replays from frozen physical artifacts; no fresh
complete RTL-to-GDS run or whole-product speedup is claimed.

`report-audit/result.json` compares all 36 referenced raw timing reports/Tcl
scripts and nine other independent outputs. All contents match after exact
workspace paths, SPEF DATE, JSON key ordering and proven derived FileIDs are
normalized. Every timing value and every full LVS checker field is retained.
GDS, DEF, native SPEF and final/powered netlists are byte-identical. The sole
explicitly unresolved binding is `check_lvs.json.database_sha256`: both actual
values are retained and verified against the raw database files.

The launch-time strict comparator also included the extra internal
`layout.lvsdb` artifact and failed. Fresh serial versus published run16 already
differs there, including layer declaration order. Public KLayout API exports
also show differing anonymous extracted-net identifiers; complete reference
netlist exports match. An unhinted bounded `NetlistComparer.compare` returned
false for extracted netlists, true for references, so extracted graph identity
and a complete canonical database signature remain **unverified**. This is an
extra internal-artifact limitation; it does not change any foundry LVS gate.
Raw database roots, API observations and the strict failure are preserved.

Pitfall: a generated Tcl script contains absolute workspace paths, so its raw
SHA and length differ even when its normalized complete contents match. Verify
every leaf report first, then normalize only its bound FileID; never discard
the report registry or hashes to make a wrapper comparison pass. The observer
auditor does exactly that. Keep a launch-time source snapshot for long-running
verification scripts as well as for the actual producer/helpers.

The six production helpers from commit `3384bdadf03fb5f9a3894f1b10051b797e54c1df`
are frozen in `.work/perf-20261030/b4-reviewed-3384bda/manifest.json`. Their SHA256:

| Helper | SHA256 |
| --- | --- |
| producer.py | 2909e1db5ded678077bfa6c781560d1b1bf6b2d49986040867927747b0537c17 |
| native_backend.py | 6bc36aa208967ecdd6cfbcfb453448017d0f8a0a1a401566b720a2602c229415 |
| native_flow.py | 84ead8024fc5af30fdbf66a490d929c2829c0c26f3f5ac712265b4ae013b3a21 |
| signoff_lvs.py | 0981f3da0d932fead0586899116307503517ca41a079726d30ba97c2e3210844 |
| build_lvs_reference.py | e76d99a0a5ae0a44d607fc1103c24add0d935034c7d2882feeecec1b8ea659b0 |
| check_lvs.py | 37b5ec43fb38949cfa9aadbc959e146f8159b41bfcc2466871bf64c8b36e948b |

For the combined private recipe, replace the old prepare script's overlay
copies with these reviewed B4 helpers, merge the separately reviewed B3
antenna adapter, and only then compute source FileIDs. Pin the new producer in
the frontend inputs before generating its fragment. Use the merged backend and
flow composers, pin `lvs_runner` with its two same-directory sibling helpers,
and set `parallel_signoff: true` in backend inputs. Keep the frontend scope at
sixteen physical CPUs/8192 MiB. `native_flow` propagates the exact dependency
group; B4 requires no qualifier edits. Freeze all executable helper dependencies
for the private run, including indirect logic-helper siblings, and retain the
existing whole-worker owner/cleanup protocol. No live integration changes are
needed to construct this private recipe.

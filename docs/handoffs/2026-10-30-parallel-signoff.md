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

Validation so far: 18 real-process/protocol controls pass, including overlap,
affinity, dependencies, memory serialization, cancellation, logging and serial
prefix behavior. GNU time records <32 MiB peak RSS under a 1 GiB/60 s guard.
Real copied-artifact serial/parallel signoff replay is in
`.work/perf-20261030/signoff-replay01`; its results must be checked before claiming
the B4 artifact-equivalence gate. No fresh complete RTL-to-GDS run is claimed.

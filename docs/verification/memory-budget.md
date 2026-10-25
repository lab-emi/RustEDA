# Upfront memory and work budget

RustLane orchestrates existing engines; it does not implement new synthesis,
timing propagation, CEC, path enumeration, or physical-design graph algorithms.
Mutable state is a bounded sequence of flow steps, separate from engine databases.

Initial implementation budget, recorded before algorithms and tests:

| Allocation/work | Bound and estimate |
| --- | --- |
| Configuration | 2 MiB encoded maximum; arrays capped at 256 files/arguments, 32 corners/physical steps; estimated parsed state < 16 MiB |
| Workspace state | 16 MiB encoded maximum; at most 128 stage records; mapped immutable snapshot plus a parsed copy < 128 MiB |
| File identity | Streaming SHA-256 with a 64 KiB buffer, no file-sized allocation; input/artifact byte caps apply |
| Workflow traversal | At most 128 linear steps, no path/cut/cone enumeration, recursion or combinatorial materialization |
| Tiny dual-PDK test | < 1,000 netlist objects × conservative 256 B state × 1 corner × 2 engine threads < 1 MiB graph state, plus Liberty/parser/engine overhead conservatively < 1 GiB |
| Test concurrency | CARGO_BUILD_JOBS=2 and RUST_TEST_THREADS=2; real-engine scenarios run sequentially |
| Build | New small orchestration crate plus Rust dependencies, estimate < 4 GiB RSS with two build jobs; do not rebuild sibling RustSys/RustSTA binaries |

Before builds/tests read `free -h`. Run builds under a 16 GiB cgroup memory cap,
4 GiB swap cap and 180-second wall guard (repeat only if measured need warrants it).
All verification binaries receive OS/cgroup memory caps and a wall deadline.
Use `/usr/bin/time -v` for actual RSS, CPU and elapsed measurements. A standard test
binary above 2 GiB RSS is a defect. Runs estimated above 4 GiB RSS are serialized
with `.work/scale.lock`. Child engines also receive explicit per-command limits.

External-tool availability and actual execution are separate. Missing OpenROAD,
OpenSTA, DRC/LVS/stream-out tools or PDK collateral must remain unverified/unchecked.
Runtime receipts will be retained under `.work/`, and small public verification
summaries will distinguish executed checks from proposed ones.

# AGENTS.md — RustEDA Engineering Constitution

You are working on **RustEDA**, a clean-room, agent-native Electronic Design Automation suite in Rust (`rusteda-core`, `rusteda-workspace`, `rusteda-formats`, `rusteda-compute`, `rustsys`, `ruststa`, `rusteda-cli`, `rusteda-studio`).

Every pull request, branch, or patch MUST strictly obey the following 9 rules:

### Rule 1: Clean-Room & Core Independence
1. Do NOT copy or translate GPL/restrictive source code from external repos (nor inspect unlicensed competitor internals). Interoperate with reference tools (Yosys, ABC, OpenSTA, Ngspice) strictly as black-box external executables over standard file formats (Verilog, BLIF, AIGER, Liberty, SDC, SPEF, DEF) in differential tests. **No code-level differential comparison**: never read, diff, port, or translate a reference tool's source code, nor use it to explain or close a difference. **Directly comparing reference binaries is allowed**: run time, peak memory and observable outputs (QoR, timing values, equivalence verdicts) may be compared in tracked harnesses with pinned tool versions and may gate acceptance; a missing tool yields `unverified`, never a pass.
2. Core crates (`rusteda-core`, `rusteda-workspace`, `rustsys`, `ruststa`) must NEVER depend on Python, PyTorch, or GUI crates.

### Rule 2: Daemonless Disk Workspace (`--workspace <dir>`)
1. Never introduce background daemon processes, resident servers, or blocking `stdin` prompts for CLI tools.
2. Every CLI tool operates deterministically on `--workspace <dir>`, acquiring shared/exclusive locks via `workspace.lock` and reading/writing modular binary side-tables via `memmap2`.
3. Every mutating CLI command MUST accept `--base-rev <rev>` and fail with `REVISION_CONFLICT` if the workspace `HEAD` does not match.

### Rule 3: Memory Model & Data Structures
1. **No `Rc<RefCell<T>>` graphs**: Use generational typed indices (`InstId`, `NetId`, `PinId`, `CellId`) backed by central arena/SlotMap storage in mutable IRs.
2. **Per-DB String Interning**: `SymbolInterner` belongs to each `DesignDb` / workspace instance. Never use process-global mutable static interners.
3. **Separate Mutable IR from Compute Views**: Keep mutable synthesis/netlist IRs distinct from contiguous SoA/CSR buffers used by CPU/GPU batch kernels. Never force RTL, AIG, Gate Netlist, and Timing Graph into a single unified struct.
4. **No Unbounded Combinatorial Materialization**: Even on small DAGs ($\le 5{,}000$ pins, such as reconvergent datapaths), simple startpoint-to-endpoint path counts grow as $O(2^L)$ in logic depth $L$. Never materialize all simple paths, cuts, or cones into an unbounded `Vec` via naive DFS/BFS. Always use topological DP ($O(V + E)$), slack/bound pruning, or heap-bounded top-$K$ extraction, enforced by hard caps (`max_paths`, `max_expansions`, `max_cuts_per_node`) and cycle/SCC guards before traversal.

### Rule 4: Shell-Safe CLI Handles & Honest Telemetry
1. All CLI handles (`rev42`, `path17`, `sel8`, `inst102`) must be strictly `[a-zA-Z0-9_-]+`. NEVER emit `#` (e.g., `#p1`), `$`, or spaces in handles.
2. Never report unverified metrics as zero (e.g., do not output `drc: 0` if DRC was not run; output `drc_status: "unchecked"`). Always include analysis scope (`corner`, `wire_model`, `unconstrained_endpoints`).

### Rule 5: Platform-Native Compute (`cpu_ref` First, Then `CUDA` & `Metal/Accelerate`)
1. Every compute-accelerated task in `rusteda-compute` MUST first have a 100% working, deterministic pure-Rust `cpu_ref` implementation (`rayon`).
2. Linux GPU kernels use native CUDA (`cudarc`, `.cu`, `cuFFT`, `cuSPARSE`). macOS GPU/coprocessor kernels use native Metal (`objc2-metal`, `.metal`) and Apple `Accelerate` (`vDSP`, Sparse Solvers over `MTLStorageModeShared` with explicit CPU/GPU sync fences).
3. Every GPU kernel MUST be accompanied by a differential unit test comparing its output against `cpu_ref` within an explicit error tolerance.

### Rule 6: Numerical Precision by Algorithm (No Blanket `f32`)
1. Layout geometry and DRC coordinates MUST use integer database units (`i64` DBU), never floating point.
2. STA arrival time propagation merges via `max`/`min` (with deterministic topological ID tie-breaking), NOT floating-point summation.
3. Parallel spatial/gradient reductions on GPU (e.g., density maps, wirelength gradients) MUST support a `--deterministic` mode using 64-bit fixed-point integer atomics (`i64`/`u64`) or fixed-order tree reductions.
4. Ill-conditioned linear solvers and circuit simulation residuals MUST be verified in `f64`.

### Rule 7: Explicit Format, Constraint & Dual-PDK (`sky130` + `gf180mcu`) Coverage
1. When parsing Verilog, Liberty (`.lib`), or SDC files, any unsupported construct or constraint that affects semantics MUST emit an explicit diagnostic warning or error. NEVER silently ignore an SDC timing constraint or Liberty attribute.
2. All Liberty, technology mapping, and STA features must be tested against both default baseline PDKs: **SkyWater 130nm (`sky130_fd_sc_hd`)** and **GlobalFoundries 180nm (`gf180mcu_fd_sc_mcu7t5v0` / `gf180mcu_fd_sc_mcu9t5v0`)** to prevent overfitting to a single library's naming or LUT conventions.

### Rule 8: Compilation Is Only the Entry Ticket (Anti-Correlated-Hallucination)
1. Passing `cargo check` and self-authored unit tests is NOT sufficient evidence of correctness.
2. Do not write both an EDA algorithm pass and its sole validation oracle using the same assumptions.
   - `RustSys` transformations MUST pass combinational equivalence checking (CEC) and/or black-box differential verification.
   - `RustSTA` timing updates MUST pass incremental-vs-full consistency checks and OpenSTA differential benchmarks on both `sky130` and `gf180mcu`.
   - Performance PRs MUST report end-to-end wall-clock time (including `mmap`, SoA/CSR lowering, and host-device synchronization) and QoR metrics, never isolated kernel timings alone.

### Rule 9: Memory Estimation, Complexity Bounds & OOM Prevention
1. **Mandatory Upfront Memory Estimation**: Before implementing any graph pass, independent checker (`rustsys-check`, `ruststa-check`), fixture generator, or test suite—and before launching heavy builds/tests—you MUST estimate peak memory consumption:
   $$\text{Peak RAM} \approx N_{\text{objects}} \times \text{sizeof(Entry)} \times N_{\text{corners}} \times N_{\text{threads}}$$
   Explicitly account for: (a) per-node/per-arc state vectors and tag tables, (b) worst-case materialized paths/cuts/clauses on reconvergent DAGs, and (c) parallel test/build worker concurrency (`CARGO_BUILD_JOBS`, `RUST_TEST_THREADS`).
2. **Hard Algorithmic Budgets in Engines & Checkers**:
   - Every graph search, path tracer, cut enumerator, or SAT/CEC solver MUST enforce explicit, configurable memory/work budgets (e.g., `max_paths`, `max_visited_states`, `max_cuts`, `max_conflicts`).
   - When a budget is reached, fail gracefully with a structured diagnostic or fallback—NEVER allocate unbounded vectors in a loop or recursion.
   - Share parsed immutable PDK libraries (`sky130`, `gf180mcu`) across tests via `OnceLock` or `Arc` rather than re-parsing or deep-cloning multi-megabyte `Library` structs inside tight loops.
3. **Pre-Flight Host Memory Check & Process Resource Caps**:
   - Check available host memory (`free -h`) before starting multi-crate builds or PDK/corpus verification suites.
   - Run test and verification binaries under explicit cgroup/OS memory and wall-clock guards—preferring `systemd-run --user --scope -p MemoryMax=16G -p MemorySwapMax=4G timeout 180s <cmd>` (which isolates physical RAM + swap via cgroup v2 without breaking CUDA virtual address reservation, and confines any OOM strictly to the child scope) or `ulimit -v 16777216` for pure-CPU runs, paired with `RUST_TEST_THREADS=2..4`—so a runaway allocation never triggers a host-wide OOM-killer (`SIGKILL` / Signal 9) that crashes the agent session.
   - Serialize high-memory runs ($> 4\text{ GiB}$ estimated RSS) behind an exclusive file lock (`flock .work/scale.lock <cmd>`).
4. **Peak RSS Verification**:
   - Measure and verify actual peak memory (`Maximum resident set size` via `/usr/bin/time -v`) when introducing new verification suites or large-design benchmarks. Any standard unit/integration test binary exceeding $2\text{ GiB}$ RSS (or PDK verification run exceeding $8\text{ GiB}$ RSS) is treated as a defect and must be bounded before merge.

## Working Preferences

1. Apply first-principles thinking. Identify the actual objectives, constraints, and success criteria before proceeding. Do not blindly follow conventions.
2. For simple questions, respond directly. For complex questions, use answer-me-with-html.
3. Document encountered pitfalls and lessons learned in the project’s HANDOFF.md file. Always review HANDOFF.md before starting a new task to avoid repeating past mistakes.
4. Use the Codex browser and Chrome by default. Ask for permission before controlling Vivaldi.
5. For tasks involving releases, deployment, or production environments, include performance overhead as part of the design and acceptance criteria.
6. For UI-related tasks, avoid unnecessary secondary labels and descriptions. Let the layout do the explaining.


## Superproject coordination

- This is the RustEDA development superproject. Read HANDOFF.md and docs/modules.md before selecting work.
- Keep module histories independent. Do not add a root Cargo workspace or copy shared crate implementations during routine integration work.
- Open tasks here, then read the selected module instructions and archived docs/handoffs notes. Update the root HANDOFF for cross-repository lessons.
- Preserve dirty modules and original sibling working trees. Initialize only missing clean modules at the recorded gitlinks.
- Use independent module checkouts for parallel agents; do not share writable source or frozen benchmark output directories.
- Run build/check/test through ./dev where possible, which sets isolated targets, budgets and resource logs. Missing reference tools never mean an EDA pass.
- Push child commits to reachable reviewed or draft branches before publishing root gitlink updates. Do not merge child main branches as a side effect of updating this repository.

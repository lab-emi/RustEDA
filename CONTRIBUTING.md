# Contributing to RustEDA Studio

RustEDA Studio is the workbench of RustEDA, a suite of electronic design automation tools in Rust
that share their core crates and a disk workspace. This file holds the rules that every change
follows: the engineering rules of RustEDA, the rules of this repository, and the checks to run
before sending a change. The follow-ups of the workbench are in the [scope](https://lab-emi.github.io/RustEDA/verification/).

Instruction files for coding agents, such as `AGENTS.md`, stay local and are never committed; this
file is the tracked statement of the rules.

## Engineering rules

The rules are numbered so that sources, specifications and reviews can cite them, for example as
Rule 7.1. They are the rules of every RustEDA repository; the workbench applies them as a client
of the workspace that computes nothing of its own.

### Rule 1: Clean room and independent core

1. Never copy or translate source code under the GPL or another restrictive license, and never
   study the internals of a program whose license does not allow it. Algorithms from the
   literature are re-expressed in our own code and cited with a key from
   [`docs/references.bib`](docs/references.bib).
2. Other programs are used only as black boxes over standard file formats (Verilog, BLIF, AIGER,
   Liberty, SDC, SPEF, DEF). There is no code-level differential comparison: their source code is
   never read, diffed against ours, ported, or used to explain or close a difference. Their
   released binaries may be run and compared directly (Rule 8.4).
3. The core crates and the engine and checker crates never depend on Python, machine-learning
   frameworks or user-interface crates. User-interface crates are used only by `gui` and `apps`
   crates; `cargo xtask layout` enforces it.

### Rule 2: Daemonless disk workspace

1. No background daemons, resident servers or blocking prompts on standard input. Every command
   runs to completion or to a deterministic budget, commits or discards its work, and exits with a
   fixed exit status. The workbench is a client process: it runs while its window is open and
   reads nothing from standard input.
2. Every command works on a workspace directory (`-w/--workspace DIR`): it takes a shared lock on
   `workspace.lock` to read and an exclusive lock to commit, and reads binary tables through
   memory maps. Committed revisions are immutable.
3. Every mutating command takes `--base-rev REV` and fails with `REVISION_CONFLICT` when the
   workspace's `HEAD` is another revision. `HEAD` itself is never accepted as a base. The
   workbench commits nothing; the only file it writes is the shared selection `ui_state.json`.

### Rule 3: Memory model and data structures

1. No `Rc<RefCell<T>>` graphs: mutable designs use generational typed indices (`InstId`, `NetId`,
   `PinId`, `CellId`) into arenas, so that a stale id fails with an error instead of naming another
   object.
2. Names are interned per design database, never in a process-global interner.
3. Mutable representations stay separate from the dense compute views (structures of arrays and
   compressed sparse rows) that CPU and GPU kernels read. The netlist, the timing graph and the
   other domain graphs are distinct structures, linked by id tables.
4. No unbounded combinatorial materialization: paths, cuts and cones are never enumerated into an
   unbounded list. Searches use topological dynamic programming, bounds or top-K extraction, with
   explicit caps (`max_paths`, `max_nodes`, `max_visits`) and cycle guards.

### Rule 4: Shell-safe handles and honest status

1. Handles (`rev42`, `path17`, `sel8`, `inst102`) match `[A-Za-z0-9_-]+`: never `#`, `$`, spaces
   or other characters that a shell interprets.
2. A property that was not checked is never reported as passing, and a metric that was not
   computed is never reported as zero: a design-rule check that did not run is reported as
   `"drc_status": "unchecked"`, a revision without timing as `"timing": "not_analyzed"`, and a
   metric without any checked endpoint as `null` (the workbench shows `n/a`). Every result states
   its scope: corner, wire model, parasitics and unconstrained endpoints.

### Rule 5: CPU first, then native GPU backends

1. Every task of `rusteda-compute` first has a deterministic pure-Rust CPU backend. The CPU
   backend is authoritative (timing runs on it in `f64`) and it is the default backend.
2. GPU backends are native: CUDA C kernels compiled at run time on Linux, and Metal on macOS as a
   roadmap item. Only `rusteda-compute` uses GPU runtime crates (`cudarc`, later the Metal
   bindings); portable GPU kernel languages are not used. A GPU backend that cannot be used is an
   error (`BACKEND_UNAVAILABLE`), never a silent fallback to the CPU. The renderer of the
   workbench's user-interface framework is not a compute backend and is not one of these crates.
3. Every GPU kernel has a consistency test against the CPU backend, with an explicit bound that
   the compute specification ([`docs/specs/compute/`](docs/specs/compute/)) states.
4. `unsafe` code exists only in the GPU glue of `rusteda-compute` and the memory maps of
   `rusteda-workspace`, under a module-scoped `allow`, with a `// SAFETY:` comment on every
   block. The workbench forbids it (`unsafe_code = "deny"`).

### Rule 6: Precision by algorithm, and determinism

1. Layout geometry and design-rule coordinates use integer database units (`i64`), never floating
   point. The schematic layout of the workbench uses integer layout units for the same reason.
2. Timing propagation merges arrivals with `max` and `min`, never by floating-point summation.
   Results depend neither on the number of threads nor on the order of statements in the inputs,
   and ties are broken by the canonical topological id. The same inputs give byte-identical
   tables; the workbench gives the same panels for the same workspace.
3. Parallel reductions on a GPU (density maps, gradients) offer a deterministic mode with 64-bit
   fixed-point integer atomics or reductions in a fixed order.
4. Ill-conditioned linear solvers and circuit-simulation residuals are verified in `f64`.

### Rule 7: No silent drops, and two default PDKs

1. Nothing in an input (Verilog, Liberty, SDC, later SPEF) is dropped silently. A construct is
   either used, or listed in the specification of its reader as having no effect on results, or
   reported. The workbench reports what it could not show with a coded diagnostic (`U` codes).
2. Liberty, technology-mapping and timing features are tested on the standard cells of both
   default PDKs, `sky130_fd_sc_hd` and `gf180mcu_fd_sc_mcu7t5v0`/`gf180mcu_fd_sc_mcu9t5v0`.
3. PDK libraries are fetched at pinned versions, with their SHA-256 sums and sizes recorded, into
   the directory that `RUSTEDA_PDK_DIR` names (default `target/pdk`; `cargo xtask pdk fetch`);
   they are never committed. Tracked design data is synthetic and lives only below a
   `fixtures/synthetic/` directory.

### Rule 8: Compilation is only the entry ticket

1. Passing `cargo check` and self-written unit tests is not evidence of correctness.
2. No algorithm is validated only by checks written under its own assumptions. The specification
   comes first; tests check it on workspaces built with the shared crates' writers, and the
   workbench is exercised on workspaces that the released tools of the suite build.
3. A change made for performance reports end-to-end wall-clock time together with its effect on
   the results; isolated kernel times are not enough. Times measured on a shared machine are
   indicative.
4. Comparisons with other programs run their released binaries as black boxes over standard file
   formats and compare what can be observed from outside. The specification, not another
   program, defines correct behavior.

### Rule 9: Memory and work budgets

1. Before a graph pass, a fixture generator or a test suite is written, its peak memory is
   estimated (objects × entry size × corners × threads), including the worst case of reconvergent
   graphs and the parallelism of builds and tests.
2. Every search, path tracer or cone extraction has explicit, configurable budgets and ends with a
   structured "truncated" result and a diagnostic when one is reached.
3. Heavy builds and tests run under a memory and wall-clock guard of the machine (a cgroup scope
   or `ulimit`, with at most four test threads).
4. A new suite reports its peak resident memory (`/usr/bin/time -v`); a unit or integration test
   binary above 2 GiB is a defect.

## Repository rules

### Layout and files

- Crates live at `crates/<group>/<name>/`, with the groups `core`, `formats`, `engines`, `verify`,
  `gui` and `apps` and kebab-case names; the top level holds only the entries of an allowlist.
  The workbench is `crates/apps/rusteda-studio`. `cargo xtask layout` checks the places, the
  dependency directions between the groups, that of the RustEDA crates the workbench uses only
  `rusteda-core` and `rusteda-workspace`, and that its user-interface crates are optional
  dependencies of its `gui` feature.
- Files are at most 1 MiB, and Rust sources at most 1,200 lines: split a longer module.
- Every Rust source starts with an SPDX header:

  ```rust
  // SPDX-License-Identifier: Apache-2.0
  // SPDX-FileCopyrightText: 2026 RustEDA Studio contributors
  ```

  Files of the shared crates and shared specifications name `2026 RustEDA contributors` instead.
  Other files take their license and copyright from [`REUSE.toml`](REUSE.toml).
- Design data (`.lib`, `.v`, `.sdc` and the like) is tracked only below a `fixtures/synthetic/`
  directory. The fixtures of `verification/fixtures/synthetic/` are the inputs of the shared
  crates' tests, copied from the tool repositories
  ([README](verification/fixtures/synthetic/README.md)).
- Scratch output stays in directories that git ignores (`.work/`, `target/`): `cargo xtask layout`
  also checks the untracked files that git does not ignore.

### Lab policy for tracked text

The lab that develops RustEDA applies these rules to every tracked file, and a pre-commit hook in
the lab checks them.

- Names of open third-party EDA programs appear only where a black-box comparison runs them
  (Rule 8.4) and in `docs/references.bib`.
- No names of commercial programs or foundries anywhere, the bibliography included.
- Another program's output is reported as a measured comparison, never as the definition of
  correctness, and never explained by that program's source code (Rules 1.2 and 8.4).
- No absolute paths of a local machine (home directories, mount points) and no paths of local
  scratch directories.
- No name of the repository of another RustEDA tool: tracked text refers to such a tool by its
  role, for example the synthesis tool of the suite or the static timing analysis tool of the
  suite.

### Specifications and requirements

- Every behavior starts as a specification in `docs/specs/<area>/` with requirement IDs of the
  form `<AREA>-REQ-<nnnn>`. The workbench's are `STUDIO-REQ-0001` to `STUDIO-REQ-0299` in
  [`docs/specs/studio/`](docs/specs/studio/), and its diagnostic codes `U0001` to `U0199`. Tests
  cite the IDs they verify, and `cargo xtask coverage` fails on a requirement that no source or
  test cites; every workbench requirement is cited by a test.
- A changed rule gets a new ID.
- Specifications cite textbooks, papers and format documents with keys from
  [`docs/references.bib`](docs/references.bib). A key that a shared file cites must be in the
  bibliography of every RustEDA repository, and a new one is added to all of them in the same
  change.
- Decisions that concern only the workbench are recorded in [`docs/adr/`](docs/adr/).

### Shared crates and specifications

- The crates `rusteda-core`, `rusteda-workspace` and `rusteda-compute` (`crates/core/`),
  `rusteda-formats` (`crates/formats/`) and `rusteda-cli` (`crates/apps/`), their specifications
  in `docs/specs/{core,workspace,compute,verilog,liberty,sdc,rusteda-cli}/`, and any other file
  that `xtask/src/shared.rs` lists are identical in the repositories of all RustEDA tools. This
  repository never changes them: a need of the workbench is implemented in its own crate or
  reported to the owners of the shared crates. The shared set is re-synchronized from the tool
  repositories by the coordinator.
- `cargo xtask shared check` verifies them against `verification/shared.sha256`, and
  `cargo xtask shared diff <other-repository>` lists what differs from a clone of a tool.

### Dependencies

- Third-party dependencies come from crates.io only, are pinned to exact versions (`=x.y.z`) in the root
  `[workspace.dependencies]`, and must pass `cargo deny check`.
- The pins follow a list that the RustEDA repositories share. The workbench adds `gpui`, its
  user-interface framework, and holds `libc` at the version the framework builds with
  ([ADR 0001](docs/adr/0001-gui-toolkit.md)); a further crate is proposed with a justification
  first.

### Linux system libraries

The framework links `libxkbcommon`, `libxkbcommon-x11`, `libxcb`, `libfreetype` and
`libfontconfig`, and loads Vulkan at run time. A host that has the run-time libraries but not the
development links (`libxkbcommon-x11.so`, `libX11-xcb.so`) builds with links in an untracked
directory, passed to the linker through the environment:

```bash
LIBDIR=$(pkg-config --variable=libdir xkbcommon)   # the distribution's library directory
mkdir -p .work/syslibs
ln -sf "$LIBDIR/libxkbcommon-x11.so.0" .work/syslibs/libxkbcommon-x11.so
ln -sf "$LIBDIR/libX11-xcb.so.1" .work/syslibs/libX11-xcb.so
export LIBRARY_PATH="$PWD/.work/syslibs${LIBRARY_PATH:+:$LIBRARY_PATH}"
```

`LIBRARY_PATH` is read by the C compiler that links, and is not part of cargo's fingerprints, so
setting it rebuilds nothing; `.work/` is ignored by git.
Without a display, the window runs under `xvfb-run` with a software Vulkan driver and a window
manager, which the framework's X11 backend needs to start drawing.

### Code

- Match the style of the surrounding code, document public APIs and remove dead code.
- The headless model (`src/model/`) never names the user-interface framework; the front end
  (`src/gui/`) holds no EDA state and only renders the model and forwards actions to it.

### Tests

- Test inputs are synthetic (Rule 7.3), and tests cite the requirement IDs they verify. The
  workbench's tests build their workspaces with the shared crates (the netlist codec,
  `StaTableBuilder`, directory tables) and never run a tool of the suite.
- The view tests (`tests/gui.rs`) run the window on the framework's headless test platform; they
  need no display.
- Tests write only below `CARGO_TARGET_TMPDIR` (integration tests) or below
  `std::env::temp_dir()`, never to a fixed path such as `/tmp`.

## Checks before sending a change

Run from the repository root:

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo clippy -p rusteda-studio --no-default-features --all-targets -- -D warnings
cargo test --workspace
cargo test -p rusteda-studio --no-default-features   # the headless model and --print
cargo xtask layout
cargo xtask coverage                    # rewrites docs/verification/requirements.md
cargo xtask shared check                # the shared files match verification/shared.sha256
cargo deny check
reuse lint
```

- `cargo xtask layout` checks locations, SPDX headers, sizes, the places of design data and the
  dependency rules ([Layout and files](#layout-and-files)).
- `cargo xtask coverage` fails when a requirement is cited by no source or test.
- A change of the window is also run once on a workspace that the released tools of the suite
  build, with a screenshot (locally, under `xvfb-run` when there is no display).
- `cargo deny check` needs `cargo install --locked cargo-deny`, and `reuse lint` needs the
  [REUSE tool](https://reuse.software).

## Commits

- Conventional Commits, for example `feat(studio): cancel superseded loads (STUDIO-REQ-0166)`.
- The body explains what changed and why, in terms of specifications and tests.
- Contributions are accepted under the Developer Certificate of Origin: sign off every commit
  (`git commit -s`).


### Canonical first-party foundation (2026-10-25)

The four shared library implementations and the workspace CLI implementation are owned by
lab-emi/RustEDA-Common, the public first-party foundation. Workspace dependencies and Cargo.lock
pin one exact revision over HTTPS; no GitHub credentials are required. The local rusteda-cli package is a
product adapter and retains its package version in command output and revision history.

This supersedes the legacy requirement to copy shared implementation files between tool
repositories, and is the sole first-party exception to the crates.io-only source policy.
Third-party package versions, licenses and source restrictions are unchanged. Shared specification
and automation manifests still require deliberate updates. Canonical core tests run in the
foundation repository; product adapters and engines retain their local tests.

Cross-repository validation uses the pinned foundation source. The historical public main
branch retains its existing build policy until a reviewed migration is merged.

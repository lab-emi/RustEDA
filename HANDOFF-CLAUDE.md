# Handoff — PULPissimo benchmarks, RustSys hierarchy support, IHP SRAM test

Snapshot: **2026-11-09**. Previous work was completed 2026-11-06.
Audience: a fresh agent continuing this task. Read this file, then the repo
`AGENTS.md` and the root `HANDOFF.md` in the primary checkout.

## TL;DR

- RustSys now resolves sv2v-generated generate-scope hierarchy references; the
  original `S0105: 'i_soc_domain' is not declared` is fixed. Tests, fmt and
  clippy pass.
- PULPissimo is benchmarked beside Ibex on SKY130 (RustSys 1.07× slower than a
  bounded Yosys reference, but 8.96 % smaller area and 32.35 % fewer cells).
- The behavioral L2 memory caused the full-SoC 2 h timeout; the analysis is
  documented with a one-word control.
- A full PULPissimo synthesis on IHP SG13G2 / EZ130 with **80 official SRAM
  macros** completes in **426.343 s** (synthesis-only, one run). This is the
  latest result and the main new deliverable.
- All work is uncommitted in the worktree. Docs build and publication check
  pass. The next agent must decide commit/publish strategy and any follow-up
  (Yosys reference, STA, equivalence, physical flow).

## 1. Where the work is

- **Active worktree:** `/home/cgao/.codex/worktrees/publish-development-20261101/RustEDA`
- **Branch:** `codex/pulpissimo-benchmark-20261106`
- **Base:** `699ea57` (`Docs: headline speed and QoR of RustEDA against LibreLane`)
- **Primary checkout:** `/home/cgao/git/RustEDA`, branch
  `codex/integrated-rusteda-apps`, dirty with unrelated work. **Do not overwrite
  or clean it.** Only its `HANDOFF.md` was updated with cross-repo notes.
- All changes are **uncommitted**. Child modules are detached at published pins:
  RustSys `35f9985`, RustEDA-Common `5f89173`, RustROAD `ccf645b`,
  RustSTA `f83dafb`, RustSpice `aef8337`.
- No active background jobs. A job under RustEDA-Common was cancelled by the
  user on 2026-11-09 and produced no output.

## 2. Task history and what is finished

### 2.1 Add PULPissimo as a second benchmark beside Ibex

Done. The benchmark compares the **PULPissimo Ibex core** (`CORE_TYPE=1`, no
FPU/XPULP/HWPE) on SKY130 HD against a bounded Yosys 0.70/ABC reference.

- PULPissimo pin: `bfc3d9a1ef72443464509a22d948f0a6f5b65b4c`
- Parameters: `MHPMCounterNum=10`, `RegFile=2`, `DbgTriggerEn=1`
- 3 single-thread samples per producer, medians:
  - RustSys: **2.874 s**, 234,264 KiB, 15,041 cells, 146,711.96 µm², depth 45
  - Yosys 0.70 + bounded ABC: **2.675 s**, 148,372 KiB, 22,235 cells,
    161,157.06 µm²
  - RustSys is **1.07× slower**, **8.96 % smaller area**, **32.35 % fewer
    cells**, **1.58× more peak RSS**
- Netlists are byte-identical across the three samples of each producer.
- Yosys reference is explicitly bounded; its default ABC script did not finish
  in 600 s. Yosys reads a 25-module reachable subset; RustSys reads the full
  366-module file, so the timing comparison is conservative.
- No equivalence proof.

### 2.2 Fix RustSys support for sv2v generate-scope references

Done. Root cause: sv2v 0.0.13 inlines the interface-bearing `soc_domain` into a
generate block named `i_soc_domain` and leaves module-scope hierarchical
references such as:

```verilog
assign i_soc_domain.slow_clk_i = slow_clk_i;
```

RustSys previously rejected this with `error[S0105]: 'i_soc_domain' is not
declared`.

Changes in `modules/RustSys`:

- `crates/engines/rustsys/src/elab/hier.rs`
  - named generate blocks are declared as `Symbol::Scope`;
  - generate-loop containers get an iteration map `genvar value -> ScopeId`.
- `crates/engines/rustsys/src/elab/mod.rs`
  - `Elaborator.top_scope`;
  - `Scope.iterations`;
  - `Scope.module` for module-instance scopes;
  - `hier_signal()` resolves `block.signal`, `block[i].signal`, nested
    `block.inner.signal`, `block.instance.port`, top-qualified
    `top.block.signal` and current-module absolute names such as `leaf.signal`
    inside `leaf`;
  - opt-in `RUSTSYS_ELAB_TRACE=1` emits per-instance and per-generate-block
    timings/signal/AIG counts on stderr (off by default).
- `crates/engines/rustsys/src/elab/select.rs`
  - hierarchical refs work in rvalues, lvalues and type queries.
- `crates/engines/rustsys/src/elab/module.rs`
  - records the top scope and module names.
- `crates/engines/rustsys/tests/elab_semantics.rs`
  - 4 new tests: named generate block, generate loop, nested block, undefined
    reference; plus current-module-name absolute reference. **18 tests pass.**
- Spec: `docs/specs/synth/frontend.md` (SYNTH-REQ-0123), `CHANGELOG.md`,
  `docs/development-status.md`, `docs/guide/performance.md`,
  `docs/guide/benchmarks.md`, `docs/verification/requirements.md`.

Fixed binary used for measurements:

```text
/tmp/rustsys-target-pulp/release/rustsys
sha256 fd2ecbd50c12caf0c1ee6299098eef1c1d1598ac1c00166ee9ca75168b596010
```

`cargo fmt --check` and
`cargo clippy --locked -p rustsys --all-targets -- -D warnings` pass.

### 2.3 Full-SoC performance analysis

Done for SKY130. The post-fix full `pulpissimo_soc_wrapper` no longer fails at
S0105, but with the **behavioral** `tc_sram` L2 memory it still exceeds a 2 h
bounded run (7200.003 s, observed RSS about 3.78 GiB, no netlist). Before the
fix it stopped after 2,241.09 s with S0105.

Trace found the cause:

- L2 bank `CUTS[0].bank_i` (`tc_sram`) creates **52,460,372 AIG nodes** in
  27.257 s; `genblk2` alone takes 24.615 s.
- The design has four 16384×32 interleaved banks and two 8192×32 private banks;
  every bank is elaborated as flops/muxes, so >150M AIG nodes.
- Control with every `tc_sram` `NumWords=1` (AddrWidth kept) elaborates the
  whole SoC in **4.249 s / 605,650 AIG nodes**; synthesis completes in
  **487.5 s** (optimize 476.9 s, merge 474.7 s, map 6.1 s) at 180,669 cells /
  1,697,673.20 µm² / depth 98.
- This control is **not** a PULPissimo result; it only isolates the memory cost.

Analysis page: `docs/benchmarks/pulpissimo-elaboration.md`
Evidence: `docs/benchmarks/evidence/pulpissimo-20261106/elaboration-*`

### 2.4 IHP SG13G2 / EZ130 / official SRAM macro test

Done. This is the latest and most important new result. It replaces the
behavioral L2 banks with official IHP SRAM macros and synthesizes the full
PULPissimo SoC.

- PDK: IHP SG13G2 TT 1.20 V / 25 °C
- Standard cells: EZ130 8T (`ez130_8t_tt_1p20v_25c.lib`)
- Macro: `RM_IHPSG13_1P_1024x32_c2_bm_bist`
  (`.../sg13g2_sram/lib/...typ_1p20V_25C.lib`)
- **80 macros**, full 320 KiB L2:
  - 4 interleaved banks × 16 macros (16384×32 each)
  - 2 private banks × 8 macros (8192×32 each)
- Adapter: `integration/pulpissimo_soc/ihp/tc_sram_ihp.v`
  - maps `req/we/addr/wdata/be/rdata` to the macro;
  - expands 4-bit byte enables to the 32-bit `A_BM` mask;
  - ties BIST inputs low;
  - one-cycle read latency with registered slice select.
- The official Liberty has no `is_macro_cell`; `prepare_ihp.py` copies it and
  adds `is_macro_cell : true`. **The PDK installation is not modified.**
- One 16-thread run:
  - **426.343 s**, peak RSS **1,316,868 KiB (1.26 GiB)**
  - 161,034 standard cells + 80 macros = 161,114 mapped instances
  - combinational 131,004; sequential 30,028; inverters 11,598; latches 1,105
  - logic area **2,045,003.79 µm²**
  - macro area **11,214,615.55 µm²**
  - total area **13,259,619.34 µm²**
  - logic depth **78**
  - estimated delay **119.08 ns**, unconstrained
  - AIG 604,986 → 330,146
  - state bits 36,389 → 30,028
  - warnings 157
  - phases: read 0.188 s, elaborate 4.268 s, optimize 416.377 s
    (merge 414.109 s), map 5.186 s, emit 0.112 s
- `merge` hit its work budget (`pass_budget_reached: true`), so area/depth are a
  bounded result, not a quality-sweep optimum.
- Adapter simulation with Verilator 5.052 and the official `-DFUNCTIONAL` model:
  **PASS** on a 16384-word bank (16 macros), read/write slices 0/1/11 and a
  byte-masked write. This is an adapter test, not a full-SoC simulation.

Page: `docs/benchmarks/pulpissimo-ihp.md`
Summary: `docs/benchmarks/pulpissimo-ihp-20261106.json`
Evidence: `docs/benchmarks/evidence/pulpissimo-ihp-20261106/`
Harness: `integration/pulpissimo_soc/ihp/README.md`

## 3. Current published/updated docs

Root:

- `README.md` — PULPissimo paragraphs, EZ130 scope row.
- `docs/index.md`
- `docs/benchmarks.md`
- `docs/engine-status.md`
- `docs/verification.md`
- `scripts/build_docs.py` — nav entries for PULPissimo synthesis, IHP full SoC,
  and elaboration analysis.

New pages:

- `docs/benchmarks/pulpissimo.md`
- `docs/benchmarks/pulpissimo-elaboration.md`
- `docs/benchmarks/pulpissimo-ihp.md`

New receipts/evidence:

- `docs/benchmarks/pulpissimo-soc-20261106.json` (SKY130 core aggregate)
- `docs/benchmarks/pulpissimo-ihp-20261106.json` (IHP full SoC summary)
- `docs/benchmarks/evidence/pulpissimo-20261106/`
- `docs/benchmarks/evidence/pulpissimo-ihp-20261106/`

Module docs updated:

- RustSys: `README.md`, `docs/index.md`, `docs/guide/benchmarks.md`,
  `docs/guide/performance.md`, `docs/development-status.md`,
  `docs/specs/synth/frontend.md`, `CHANGELOG.md`,
  `docs/verification/index.md`, `docs/verification/requirements.md`,
  `docs/about/maintaining-docs.md`.
- RustSTA, RustROAD, RustSpice, RustEDA-Common: README + `docs/index.md` scope
  paragraphs.

Docs are built and checked, but **not deployed**:

```text
528 source files -> 260 HTML pages
scripts/check_publication.py: passed
eda_validation: not_run
```

Build command (venv is required; system `python3` lacks PyYAML/MkDocs):

```sh
cd /home/cgao/.codex/worktrees/publish-development-20261101/RustEDA
( ulimit -v 8388608; PATH=/home/cgao/.venv/bin:$PATH \
  /home/cgao/.venv/bin/python scripts/build_docs.py )
( ulimit -v 4194304; PATH=/home/cgao/.venv/bin:$PATH \
  /home/cgao/.venv/bin/python scripts/check_publication.py )
```

## 4. What is not done / decisions for the next agent

1. **Commit and push.** Everything is uncommitted. Child modules are detached.
   Suggested:
   - commit RustSys code/docs on a draft branch
     (`codex/pulpissimo-generate-scopes-20261106`), push it;
   - commit each other module's README/docs changes on its own draft branch,
     push;
   - commit root docs/harness and update submodule gitlinks only after child
     commits are pushed to reachable branches.
   Do not merge child `main`.
2. **Decide publication level.** The IHP full-SoC result is currently a public
   benchmark page with strong caveats. If it is only an engineering test, move
   it under a handoff/experiment page instead. The user asked for the IHP test;
   the page is written as synthesis-only.
3. **Optional technical follow-ups:**
   - Yosys reference on the same IHP input. This needs a blackbox attribute or
     a Yosys-specific macro handling; the current input is RustSys-oriented.
   - RustSTA timing with a real SDC/clock. Current delay is unconstrained and
     macro internal delay/setup/hold are excluded.
   - Full-SoC equivalence or simulation. Only the adapter has a smoke test.
   - Physical implementation of the 80-macro SoC. This is a large jump and has
     not been attempted.
   - Improve the `merge` pass: 414 s of the 426 s run is merge; it hits its
     budget. This is the next performance target after SRAM blackboxing.
   - Repeat the IHP run for a median. Current result is **one run**, not three.
4. **Cleanup.** The 33 MB IHP netlist is in `/tmp`, not in the repo; evidence
   stores its hash/size. `/tmp/pulpissimo-bench` is temporary. The prepared
   SKY130 input is also only in `/tmp`; regenerate with
   `integration/pulpissimo_soc/prepare.py` if it disappears.

## 5. Exact reproduction commands

### RustSys tests and release binary

```sh
cd /home/cgao/.codex/worktrees/publish-development-20261101/RustEDA/modules/RustSys
CARGO_TARGET_DIR=/tmp/rustsys-target-pulp cargo test --locked -p rustsys \
  --test elab_semantics
cargo fmt --check
CARGO_TARGET_DIR=/tmp/rustsys-target-pulp cargo clippy --locked -p rustsys \
  --all-targets -- -D warnings
CARGO_TARGET_DIR=/tmp/rustsys-target-pulp cargo build --locked --release \
  -p rustsys-cli --no-default-features
```

The measured binary was built this way; a later rebuild can differ in hash even
with identical semantics. Use the source and tests, not the hash alone, to
decide correctness.

### IHP full-SoC input and synthesis

```sh
IHP_SRAM_LIB=/home/cgao/pdks/ihp-open-pdk/ihp-sg13g2/libs.ref/sg13g2_sram/lib
EZ130_LIB=/home/cgao/pdks/ez130_8t_v1p1/lib
GT=/home/cgao/.local/share/containers/storage/overlay/d1a194c6048338802bb33b89e26ffcad28186a9fdf7082312592e14711add98e/diff/usr/bin/time

python3 integration/pulpissimo_soc/ihp/prepare_ihp.py \
  --input /tmp/pulpissimo-bench/harness-fixed2/pulpissimo_soc.v \
  --wrapper integration/pulpissimo_soc/ihp/tc_sram_ihp.v \
  --macro-lib "$IHP_SRAM_LIB/RM_IHPSG13_1P_1024x32_c2_bm_bist_typ_1p20V_25C.lib" \
  --output /tmp/pulpissimo-bench/ihp/prep/pulpissimo_soc_ihp.v \
  --out-lib /tmp/pulpissimo-bench/ihp/prep/RM_IHPSG13_1P_1024x32_c2_bm_bist_macro.lib \
  --manifest /tmp/pulpissimo-bench/ihp/prep/manifest.json

python3 integration/pulpissimo_soc/run_synthesis.py \
  --input /tmp/pulpissimo-bench/ihp/prep/pulpissimo_soc_ihp.v \
  --lib "$EZ130_LIB/ez130_8t_tt_1p20v_25c.lib" \
  --extra-lib /tmp/pulpissimo-bench/ihp/prep/RM_IHPSG13_1P_1024x32_c2_bm_bist_macro.lib \
  --rustsys /tmp/rustsys-target-pulp/release/rustsys \
  --top pulpissimo_soc_wrapper --threads 16 --memory-gib 32 --timeout 900 \
  --output /tmp/pulpissimo-bench/ihp/runner.v \
  --report /tmp/pulpissimo-bench/ihp/runner.json \
  --receipt /tmp/pulpissimo-bench/ihp/runner-receipt.json \
  --gnu-time "$GT" \
  --label "PULPissimo SoC, IHP SG13G2 TT, EZ130 + 80 official SRAM macros"
```

The exact input hash of this run is
`e1695a2f768e2cf21b0bfc9da5a515a2af82b6d4f69fe09dd4b547eaa31f494a`;
EZ130 hash
`847cb30b38f15ef0b9c4a735cfaf96c1782c013b1952261b25e5e38cd3ae072c`;
patched macro Liberty hash
`9f4ff72463b637f05774c8ff4d6fbb12e4057a120d6f8a3e041ef9bd327105c3`.

### Adapter simulation

```sh
python3 integration/pulpissimo_soc/ihp/simulate_ihp_sram.py \
  --wrapper integration/pulpissimo_soc/ihp/tc_sram_ihp.v \
  --ihp-verilog /home/cgao/pdks/ihp-open-pdk/ihp-sg13g2/libs.ref/sg13g2_sram/verilog \
  --workdir /tmp/pulpissimo-bench/ihp/sim
```

## 6. Pitfalls and environment notes

- **Host Bender segfaults** (published release and fresh `cargo install`). The
  `hpretl/iic-osic-tools` container's Bender 0.32.1 works. Bender 0.32.1 needs
  the `adv_dbg_if/Bender.yml` trailing-comma patch.
- `prepare.py` is not a pure parse: it strips `translate_off`, patches the SCM
  binding, removes sv2v cast helpers/fatal initial blocks, repairs two
  default-index expressions, connects the dropped APB interface object, and
  prunes the unreachable FPU. All counters are in the input manifest.
- `tc_sram` behavioral memory is **not** synthesizable at full L2 size in
  practice: one bank is 52,460,372 AIG nodes. Use the IHP macro adapter or a
  real memory macro; do not rerun the behavioral full SoC expecting a result.
- The official IHP macro Liberty lacks `is_macro_cell`; use the prepared copy,
  not the PDK file, or RustSys will not preserve it as an opaque macro.
- `run_synthesis.py` needs repeatable `--extra-lib` for standard-cell + macro
  Liberty. The first `--lib` remains the standard-cell library.
- `RUSTSYS_ELAB_TRACE=1` is opt-in and writes to stderr. It is safe to leave
  unset; the measured binary has it compiled in but disabled.
- Docs must be built with the venv and `PATH=/home/cgao/.venv/bin:$PATH`.
  System `python3` lacks PyYAML/MkDocs.
- The primary `/home/cgao/git/RustEDA` has unrelated dirty files. Do not
  `git clean`, reset, or overwrite it. Work only in the worktree.
- The 33 MB IHP netlist is not committed; keep it out of git. Evidence records
  `6811525239c9c6d0597ff4d5466e454ff97cbd30e202cac59b5b3fac0fa60865`
  and 33,352,463 bytes.

## 7. Validation status

- RustSys: 18 elab tests pass; fmt/clippy clean.
- SKY130 core benchmark: 3 samples/producer, medians, netlists identical.
- SKY130 full SoC: **unverified** (2 h timeout; memory modeling limit).
- IHP full SoC: **synthesis completed** (one run); **no** equivalence, timing
  closure, physical implementation, simulation or signoff.
- Adapter: Verilator smoke test passes.
- Docs: 528 sources -> 260 HTML pages; publication check passed.
- `eda_validation: not_run` — do not present any of this as an EDA signoff pass.

## 8. Suggested first actions for the next agent

1. Read this file and the root `HANDOFF.md`; inspect `git status` in the
   worktree and in each module.
2. Re-run the RustSys tests, docs build and publication check before changing
   anything.
3. Ask the user which of these they want:
   - commit/push and publish this branch;
   - keep the IHP result as a benchmark page or move it to an internal note;
   - proceed to Yosys reference, RustSTA timing, equivalence, or physical flow.
4. If committing, do not update root gitlinks until the child commits are
   pushed to reachable branches. Keep the primary checkout untouched.

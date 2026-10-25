<p align="center">
  <img src="docs/assets/rustlane-wordmark.svg" alt="RustLane" width="440">
</p>

<h1 align="center">Reproducible execution of the design loop.</h1>

<p align="center">
  <a href="https://lab-emi.github.io/RustEDA/modules/rustlane/">Documentation</a> ·
  <a href="https://github.com/lab-emi/RustEDA">RustEDA</a> ·
  <a href="https://lab-emi.github.io/RustEDA/benchmarks/">Benchmarks</a> ·
  <a href="LICENSE">Apache-2.0</a>
</p>

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/rustlane-banner.svg">
  <img src="docs/assets/rustlane-banner.gif" alt="RustLane executes bounded tool processes and records revisioned checkpoints, input identities, outputs and recovery state" width="1200" height="360">
</picture>

RustLane executes bounded tool processes and records revisioned checkpoints, input identities, outputs and recovery state. Its external-worker contract connects the RustEDA RTL-to-GDS flow.

Part of **[RustEDA](https://github.com/lab-emi/RustEDA)**, infrastructure for chip-design recursive self-improvement. Native tools shorten the work between inference steps; verified results guide the next iteration.

## Build

Linux x86_64 is the tested host. Native child containment also supports Linux
arm64, which is not yet tested. Other hosts return `UNSUPPORTED_PLATFORM` for
guarded execution. Install Rust 1.97.1; the orchestration runtime has no Python,
GUI or GPU dependency. Build RustSys and RustSTA separately, or use existing
qualified binaries; RustLane does not rebuild or modify them.

```sh
free -h
CARGO_BUILD_JOBS=2 systemd-run --user --scope \
  -p MemoryMax=16G -p MemorySwapMax=4G \
  timeout 180s cargo build --locked --release

export PATH="$PWD/target/release:$PATH"
rustlane --help
```

On a CPU-only Linux host without a user systemd manager, use an OS guard such as
`(ulimit -v 16777216; CARGO_BUILD_JOBS=2 timeout 180s cargo build --locked --release)`.
Read the [upfront memory budget](docs/verification/memory-budget.md) before large
builds or verification runs. All child commands have independent time, memory
and log budgets.

## Run a design

Supply the matching Liberty files and place `rustsys`, the **RustSTA build of
`rusteda`**, and `ruststa` on PATH. Alternatively set their absolute paths in the
configuration's `tools` object. For the included example, provide
`examples/tiny/sky130_tt.lib` or edit its path in `sky130.json`. PDK data is
caller-supplied and is not vendored.

```sh
rustlane --workspace .work/top init \
  --config examples/tiny/sky130.json --base-rev empty
rustlane --workspace .work/top plan
rustlane --workspace .work/top doctor
rustlane --workspace .work/top run --base-rev rev0
rustlane --workspace .work/top report
```

Every operation returns one `rustlane.v1` JSON object. Use the returned `new_rev`
for the next mutation; the run journals multiple internal revisions. A stale
base fails with `REVISION_CONFLICT` and exit status 4. `init` only creates a new
workspace and requires `--base-rev empty`; it never resets an existing HEAD.
Queries accept `--rev revN` or the default `HEAD`.

To stop after a named step or resume later:

```sh
rustlane --workspace .work/top run --base-rev rev0 --until synthesis
# Read new_rev from the result; substitute that value below.
rustlane --workspace .work/top run --base-rev revN
```

Completed stages are reused only after input, tool and output hashes are
verified. Changed inputs require a fresh workspace. Failed stages require an
explicit `--retry-failed`; their old attempt directories remain intact. An
interrupted running step has unknown cleanup state and is not automatically
retried. Preserve its evidence and inspect owned processes before starting
another run. A workspace in use returns `WORKSPACE_BUSY` rather than blocking.

## Configuration

Paths are resolved relative to the JSON file. Inputs are streamed into immutable
workspace copies before execution. Unknown fields, unsupported PDK identifiers,
unsafe handles, escaping output paths and out-of-range budgets are errors.
Configuration files are capped at 2 MiB.

```json
{
  "schema_version": 1,
  "design_name": "top",
  "pdk": "sky130_fd_sc_hd",
  "verilog_files": ["top.v"],
  "libraries": [{"path": "cells_tt.lib", "corner": "tt"}],
  "sdc_file": "top.sdc",
  "synthesis_corner": "tt",
  "flow": "synthesis_sta",
  "tools": {
    "rustsys": "/tools/rustsys",
    "rusteda": "/tools/ruststa-build/rusteda",
    "ruststa": "/tools/ruststa"
  },
  "limits": {
    "threads": 2,
    "memory_mib": 4096,
    "max_state_gib": 1,
    "max_paths": 8,
    "max_conflicts": 10000,
    "timeout_secs": 180
  }
}
```

Supported PDK identities are `sky130_fd_sc_hd`,
`gf180mcu_fd_sc_mcu7t5v0`, and `gf180mcu_fd_sc_mcu9t5v0`.
The 9T identity is accepted but has not been exercised by the initial controller
verification. `sky130A` and `gf180mcuD` are explicit aliases for HD and 7T.
Multiple Liberty corners are supported in the default flow; mapping uses only
`synthesis_corner`, and RustSTA analyzes the imported set. The initial measured
cases use one TT corner each.

`DESIGN_NAME`, `PDK`, `VERILOG_FILES` and `SDC_FILE` are accepted aliases. This is
an explicitly documented subset, not a full LibreLane configuration parser.
Liberty/SDC semantic support belongs to the engines; their diagnostic output and
timing coverage are retained. Defines and parameters can be passed through the
`defines` and `parameters` string maps. Include-directory discovery is not
implemented; declare complete supported inputs explicitly.

## Reports and correctness

Synthesis process success must be followed by CEC verdict `equivalent`. STA must
return an actual corner/wire model/precision/coverage, followed by full-rerun
verdict `agree`. All reports retain the actual engine diagnostics in hashed raw
stdout/stderr or report artifacts. A successful execution or checker agreement
does not establish timing closure, complete Liberty feature coverage or signoff.

`report.summary` exposes actual timing metrics, scope, unconstrained endpoints
and verification. Unavailable values are `null`. DRC/LVS stay `unchecked` until
qualified adapters establish their verdicts; zero is never substituted for an
unchecked result. Peak direct-child RSS is explicitly distinguished from
aggregate process-family memory. Shell-safe handles such as `rev7`, `synthesis`
and `sta_pre` do not contain `#`, `$` or spaces.

## Physical-flow extension

`flow: "classic"` plans the native flow plus caller-supplied OpenROAD/KLayout/
Magic/Netgen steps, post-physical CEC and RustSTA post-PnR timing. Arguments are
passed without a shell; scripts, physical collateral and outputs are explicit.
See the [optional original recipes](docs/physical-recipes.md).

**Current boundary:** the generic Classic adapter is not qualified for the
current extracted-timing/signoff contract. A full Classic run therefore fails
before execution with `CAPABILITY_UNAVAILABLE`; use `flow: "rusteda"` through
the superproject for the qualified producer. Explicit
partial runs using `--until sta_pre` or a physical step remain possible and
report `partial`. RustLane does not substitute OpenSTA or silently analyze an
extracted netlist with a different parasitic model. OpenROAD timing-driven
optimization, CTS/PDN integration, extracted-parasitic STA and qualified physical
signoff require further implementation and dual-PDK verification.

The original physical examples disable timing-driven placement/routing options
and do not invoke standalone timing reports or resizer timing repair. Their
existence is not verification of tool internals. Caller recipes execute with
the caller's permissions; process containment is not a filesystem/network
sandbox. Declared output existence and hashes do not prove physical correctness.

## Research and verification

- [Chinese LibreLane study](docs/research/librelane-contracts.html): fixed source
  identity, documented contracts, boundaries, genuine REA inventory.
- [REA evidence and reproduction](docs/research/README.md): REA 6.7.0 actually
  inventoried 397 LibreLane files. Python semantic relationships and runtime
  equivalence were not established by that inventory.
- [Engine contracts and black-box calibration](docs/verification/engine-contracts.md).
- [Process containment](docs/verification/process-supervisor.md).
- [Workspace architecture](docs/architecture.md).

```sh
CARGO_BUILD_JOBS=2 RUST_TEST_THREADS=2 systemd-run --user --scope \
  -p MemoryMax=16G -p MemorySwapMax=4G \
  timeout 180s cargo test --locked --all-targets

# Optional real Tcl validation (requires tclsh):
RUST_TEST_THREADS=2 timeout 30s cargo test --locked --test tcl_literals -- --ignored

python3 scripts/verify_engines.py --help
python3 scripts/verify_flow.py --help
```

The development verification scripts require Python; the Rust runtime does not.
Actual engine verification requires both baseline libraries and explicit tools;
missing external oracles return `unverified`, never a pass. No reference-tool
source is inspected or compared.

## License

Apache-2.0. RustLane is independently implemented. LibreLane is a FOSSi Foundation
project; the linked reference study is not an affiliation or a compatibility
certification. External EDA tools and PDK collateral retain their own licenses.

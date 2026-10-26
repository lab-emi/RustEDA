# RustSys / RustSTA executable contracts and calibration

These contracts were exercised against existing release executables on 2026-10-23.
No sibling engine was rebuilt or edited. A fresh one-bit sequential design was
synthesized and checked with both baseline PDKs. The tracked
[`scripts/verify_engines.py`](../../../crates/apps/rusteda-flow/scripts/verify_engines.py) runner reproduces the
checks through public CLI commands and standard Verilog, Liberty, and SDC files.
Python is a development verification dependency; RustEDA flow itself does not need it.

**Current boundary:** the pinned RustSTA CLI supports pre-route NLDM timing with
automatic ideal or lumped wire-load estimation. It does **not** accept SPEF import
or a `--wire-model` override. Extracted-parasitic timing must be rejected explicitly.
The OpenSTA differential below verifies setup/hold timing under **ideal wires on
both engines**, using a separately hashed verification-only Liberty projection.
It does not certify the original Sky130 wire-load approximation or physical signoff.

## Pinned inputs

The executable versions and SHA-256 hashes, rather than the current sibling source
checkout, identify the tested producers. The sibling checkouts may contain unrelated
changes. Runtime paths are supplied by the caller and are recorded with each run.

| Executable | Version | SHA-256 of executable |
| --- | --- | --- |
| RustSys `rustsys` | `0.1.0 (runtimes: cpu, cuda)` | `9c83124e7bcf1a9ced5675672f3587099afec796de3230679fbff638c0b09e50` |
| RustSTA workspace `rusteda` | `0.1.0` | `8fcf8e0eefa05e14c3b3d6ac367839f5ff95059f6d416e2e8fab0262667b2429` |
| RustSTA `ruststa` | `0.1.0 (backends: cpu)` | `b1a8389c7684aaf8a8e05f89635fac5ed2ee15aa380c8f8d8c40b1780f08668d` |
| Yosys ELF | `0.67`, git `2d1509d1b` | `5e89af7f52f764e3228e5a8c4d16927a67257baa7c3b7207a908b3dac8d041c3` |
| ABC ELF | `1.01`, compiled `2026-07-27 18:12:25` | `a36be5dbb7bca0fa1a6c858763e0b579aecb768f0a3d52d43566e1db82546798` |
| OpenSTA ELF | `3.1.0` | `49ddbd40385e6b54152aed6d7435e4114e409e5f168884d8945374a42f00301b` |

ABC availability/version was checked; the independent synthesis proof in this study
uses Yosys's public SAT equivalence commands. No Yosys, ABC, or OpenSTA source was
inspected, translated, or used as an oracle. The local Yosys launch wrapper is also
hashed in receipts (`acf8487fd4eb54a71043bf29c461a8f10511813155c2989e5eb1e045b874a6ad`).
When supplying a wrapper in a different environment, pin its underlying tool binary
and runtime environment as well as its entrypoint/version.

| Original baseline Liberty | Bytes | SHA-256 |
| --- | ---: | --- |
| `sky130_fd_sc_hd__tt_025C_1v80.lib` | 12,841,637 | `8e78e14442062dba34d414fca6490b2f6b96038d4510d1438ca44fee31487135` |
| `gf180mcu_fd_sc_mcu7t5v0__tt_025C_3v30.lib` | 20,107,439 | `f9dee07cf158324934b0c59144d970a2e8313f43533ede6283ec3c136f82fe53` |

The original libraries were read from `RustSys/.work/pdk-trial/lib/` and remain
unchanged. The Sky130 verification projection removes four `wire_load` groups and
two default wire-load attributes; all cell functions and NLDM tables remain byte
for byte unchanged. Its SHA-256 is
`1c6372ecc8fe4ae03326de976c5c14cc2d68e44426526561d8b3c1cb77b434b1`.
GF180 has no such declarations, so its projection is byte-identical to the original.
Each projection has a manifest recording original/projected hashes and the exact
removed spans and their hashes. It is never substituted into production inputs.

## RustSys direct-file contract

Use argument vectors, explicit output paths, and bounded resources. `--format json`
controls workspace envelopes; direct-file commands require **`--print json`**.

```sh
"$RUSTSYS" --threads 1 --color never --progress off synth \
  --top lane_smoke --lib "$LIB" \
  --out "$OUT/mapped.v" --report "$OUT/synth.json" --print json \
  "$OUT/lane_smoke.sv"

"$RUSTSYS" --threads 1 --color never --progress off check synth \
  --top lane_smoke --lib "$LIB" --netlist "$OUT/mapped.v" \
  --report "$OUT/check_synth.json" --print json \
  --budget 10000 --model-budget 10000 --sweep-max-candidates 256 \
  "$OUT/lane_smoke.sv"
```

The direct synthesis stdout is an unwrapped object with
`format: "rustsys-synth-report"` and `format_version: 1`:

| JSON path | Meaning |
| --- | --- |
| `.top` | Elaborated top module |
| `.cells` | Map from PDK cell name to instance count |
| `.area`, `.cell_area` | Library-unit cell area and per-cell area |
| `.sequential_cells`, `.combinational_cells` | Mapped instance counts |
| `.state_bits_rtl`, `.state_bits_retained` | RTL / retained state-bit counts |
| `.logic_depth`, `.estimated_delay`, `.time_unit_ns` | Synthesis estimates and time unit |
| `.map_requested_cuts`, `.map_effective_cuts`, `.map_cut_memory_budget_bytes` | Engine-reported cut budget |
| `.warnings`, `.unused_cells` | Warnings count and cells excluded from mapping with reasons |
| `.runtime_s`, `.phase_s` | Engine-reported runtime breakdown |

`estimated_delay` is a synthesis estimate. RustEDA flow must use RustSTA's scoped timing
metrics for timing reports. Engine phase timing omits some caller overhead, so the
verification harness also records complete child-command elapsed time and RSS.

The check stdout is an unwrapped object with
`format: "rustsys-check-synth-report"` and `format_version: 2`. A successful tested
result contains `verdict: "equivalent"`, `outputs_checked: 1`,
`state_pairs_checked: 1`, `mismatches: []`, `errors: []`, `undecided: []`, and
`internal_error: false`. Check the verdict and diagnostics as well as the process
exit code. Reaching a SAT budget must remain undecided, never equivalent.
This fixture uses neither reset assumptions nor initial-state refinement.

## RustSTA child-workspace contract

Use a distinct child workspace per immutable RustEDA flow run and PDK/corner input set.
Initialize, import the mapped netlist plus Liberty/SDC, analyze, and verify using
the **returned** `new_rev` at each mutation.

```sh
"$RUSTEDA" --workspace "$STA_WS" --threads 1 --format json init

"$RUSTEDA" --workspace "$STA_WS" --threads 1 --format json import \
  --base-rev rev0 --lib "$LIB" --corner tt \
  --netlist "$OUT/mapped.v" --sdc "$OUT/lane_smoke.sdc"

"$RUSTSTA" --workspace "$STA_WS" --threads 1 --format json run \
  --base-rev rev1 --corner tt --backend cpu --precision f64 \
  --max-paths 8 --max-state-gib 1

"$RUSTSTA" --workspace "$STA_WS" --threads 1 --format json report \
  --rev rev2 --section summary --section checks --section notes \
  --section unconstrained --limit 8

"$RUSTSTA" --workspace "$STA_WS" --threads 1 --format json check \
  --base-rev rev2 --method full --tolerance 0.001

# Additional independent-checker calibration for this small fixture:
"$RUSTSTA" --workspace "$STA_WS" --threads 1 --format json check \
  --base-rev rev3 --method both --tolerance 0.001
```

`init` creates `rev0`; the combined import creates `rev1`; `run` creates `rev2`;
`check full` creates `rev3`; `check both` creates `rev4`. Reporting is read-only.
The revision numbers above describe an empty workspace, not a contract to hardcode.
All handles observed are shell-safe (`rev2`, `path1`, `pin4`, `port4`).

Every stdout above is one `rusteda.v1` JSON envelope:

| JSON path | Adapter contract |
| --- | --- |
| `.schema`, `.ok`, `.error` | Require `rusteda.v1`, success `true`, and no error |
| `.base_rev`, `.rev`, `.new_rev` | Mutation base, queried revision, committed next revision |
| `.diagnostics[]` | Preserve severity, code, message, location, and help |
| `.scope.corner`, `.scope.wire_model`, `.scope.wire_load` | Actual timing corner and wire assumptions |
| `.scope.corners` | Optional array of analyzed corners; `.scope.corner` remains a string selector |
| `.scope.parasitics`, `.scope.drc_status` | Observed `none`, `unchecked`; do not replace with zero violations |
| `.scope.clock_model`, `.scope.cppr`, `.scope.backend`, `.scope.precision` | Observed `ideal`, `off`, `cpu`, `f64` |
| `.completeness.constrained_endpoints`, `.completeness.unconstrained_endpoints` | Timing coverage counts |
| `.completeness.unconstrained_inputs`, `.completeness.unconstrained_by_reason` | Input / endpoint coverage details |
| `.completeness.unanalyzed_arcs` | Liberty arc kinds explicitly not analyzed |
| `.metrics.wns_ns`, `.metrics.tns_ns`, `.metrics.violating_endpoints` | Setup violation metrics |
| `.metrics.whs_ns`, `.metrics.ths_ns`, `.metrics.hold_violating_endpoints` | Hold violation metrics |
| `.metrics.worst_setup_slack_ns`, `.metrics.worst_hold_slack_ns` | Actual worst slacks, including positive values |
| `.top_paths[]`, `.truncated_paths` | Ranked paths / bounded truncation from `run` |
| `.sections.checks.rows[]` | Per-endpoint setup/hold slacks from `report` |
| `.sections.notes.rows[]` | Stored timing warnings; `report.diagnostics` may be empty |
| `.verdict`, `.method`, `.methods` | Check verdict `agree`, method `full` / `both` |
| `.tolerance_ns`, `.slacks_compared`, `.max_abs_difference_ns`, `.mismatches[]` | Check agreement details |

The tested WNS/TNS metrics are zero because there are no negative slacks. The
positive worst setup slacks are 9.432276327887784 ns for Sky130 and
8.672568525727085 ns for GF180. Both original-library runs have two constrained
endpoints and zero unconstrained endpoints. These are scoped setup/hold results:
Sky130 explicitly reports one unanalyzed `min_pulse_width` arc; GF180 reports two
`min_pulse_width` and two `minimum_period` arcs through `T0015` warnings. Preserve
these limitations even when the checker verdict is `agree`.

A stale base was tested against HEAD `rev4`. It exits **4** and returns
`ok: false`, `new_rev: null`, diagnostic `W0010`, and:

```json
{
  "code": "REVISION_CONFLICT",
  "details": {"base_rev": "rev1", "head": "rev4"}
}
```

The error appears at `.error`, and the child workspace stays unchanged. Do not
retry a mutation automatically against a newer revision without reviewing inputs.

## SPEF and wire-model limits

The actual binaries were probed, rather than inferring support from roadmap text:

| Probe | Observed result |
| --- | --- |
| `rusteda import --base-rev rev4 --spef FILE` | Exit 1; `.error.code: "USAGE"`; `A0003`, unexpected `--spef` |
| `ruststa run --base-rev rev4 --wire-model ideal` | Exit 1; `.error.code: "USAGE"`; `A0003`, unexpected `--wire-model` |

The available CLI chooses its wire model automatically. With the original
Sky130HD TT library it reports `wireload_lumped` / `Small`; the GF180 7t TT library
reports `ideal` / `null`. No extracted parasitic table is imported in these runs.
The measured Sky130 path report has zero net increments and `wire_delay_ns: 0`.
Descriptions of wire resistance in some sibling documentation differ; the pinned
binary's observed scope and outputs govern this integration contract.

A first OpenSTA run with its original wire-load defaults had different timing
assumptions and visible discrepancies. It is retained as exploratory evidence,
not classified as an engine defect or a passed differential. Trying to normalize
with `set_load -wire_load` on net objects produces OpenSTA warning 465, so the
accepted comparison instead uses the same explicit ideal-wire projection in both
engines. Original-library results remain separate.

## Reproduce bounded independent verification

Run on Linux with Python 3.11+, `/usr/bin/time`, and `timeout`. Set `YOSYS` and
`OPENSTA` to the installed external executable or launch-wrapper paths. All source
and library paths are resolved before entering child run directories.

```sh
mkdir -p .work/engine-study
/usr/bin/time -v -o .work/engine-study/controller.time timeout 180s \
  python3 scripts/verify_engines.py \
    --rustsys ../RustSys/target/release/rustsys \
    --rusteda ../RustSTA/target/release/rusteda \
    --ruststa ../RustSTA/target/release/ruststa \
    --yosys "$YOSYS" --opensta "$OPENSTA" \
    --sky130-lib ../RustSys/.work/pdk-trial/lib/sky130_fd_sc_hd__tt_025C_1v80.lib \
    --gf180-lib ../RustSys/.work/pdk-trial/lib/gf180mcu_fd_sc_mcu7t5v0__tt_025C_3v30.lib \
    --out .work/engine-study/fresh-run
```

The output directory must be fresh. The script records preflight `free -h`, tool
versions/hashes, library/projection hashes, exact argument vectors, stdout, stderr,
exit codes, complete command elapsed time, and `/usr/bin/time -v` peak RSS. It
creates one XOR-plus-DFF fixture and a wrong-RTL AND-plus-DFF negative control.
Yosys imports the original PDK Liberty models and independently proves the mapped
fixture with `equiv_simple -seq 2 -undef` plus `equiv_status -assert`; the negative
control must be rejected as unproven on both PDKs. A crash or missing executable
does not count as a rejected control.

The runner also checks native RustSTA `full` and `both` agreement. Its OpenSTA
comparison uses the same ideal-library projection, same netlist, same SDC, TT
corner, 10 ns ideal clock, 0.2 ns input/output delays, 0.1 ns input/clock transition,
and 0.01 pF output load. It compares each of two endpoints for setup and hold,
rather than only a global WNS that might hide an endpoint discrepancy. The SDC
applies input transition to **`clk` as well as data inputs** to match ideal-clock
pin slew in both tools. OpenSTA text reports at 12 digits are used; its JSON
report can round to three significant digits even with `-digits` supplied.

Exit 0 means the scoped checks passed on both PDKs. Exit 1 means a failure. Exit 3
means verification is incomplete, including unavailable external tools or baseline
libraries. Missing evidence stays `unverified`; no pass or zero metric is invented.

## Recorded calibration and resource bounds

Before fixture creation, the host had 48 GiB available RAM. The estimate budgeted
128 objects plus 256 arcs at 256 bytes each, one corner and one worker, with up to
16 timing-tag slots: less than 2 MiB graph/tag state. The one-level combinational
DAG has at most two input-to-register paths; at most eight ranked paths are kept.
SAT/model conflicts are capped at 10,000 per obligation, optional sweep candidates
at 256, and Yosys's fixture time frames at two. No all-path graph enumeration or
unbounded fixture generation is used.

Allowing 32 times the largest 19.2 MiB Liberty file for parsed library state, plus
256 MiB process headroom and 256 MiB for the projection/controller, keeps the
estimated peak below 1.2 GiB. Libraries/processes are used serially. Every child
has a 2 GiB address-space limit, a 64 MiB output-file limit, a 60 second wall limit,
and an explicit CPU limit; the controller is memory-capped too. `CARGO_BUILD_JOBS=1`
and `RUST_TEST_THREADS=2` are set; no build runs. CLI worker count defaults to one.

The final guarded runner calibration (`.work/engine-study/runner-v2/summary.json`)
recorded:

| Baseline PDK | RustSys / Yosys | RustSTA full / both | Ideal-wire OpenSTA max absolute slack difference | Total PDK calibration wall | Largest child RSS |
| --- | --- | --- | ---: | ---: | ---: |
| Sky130HD TT | Equivalent / correct control proved, bad control rejected | Agree / agree, 0 ns difference | `0.0000010211960717043667 ns` | 1.003 s | 67,156 KiB |
| GF180 7t TT | Equivalent / correct control proved, bad control rejected | Agree / agree, 0 ns difference | `0.0000005134069436252275 ns` | 1.376 s | 119,528 KiB |

Each external timing comparison includes four endpoint/check-kind values with an
explicit tolerance of 0.001 ns (1 ps), zero unconstrained endpoints, and no OpenSTA
warnings. The largest measured process RSS is about 116.7 MiB, well below 2 GiB.
The complete guarded controller run took 2.49 seconds. A separate unavailable-tool
control returned exit 3 and `status: "unverified"`, with the missing Rust engine
reported explicitly and no design verdict fabricated.
These are tiny contract-calibration costs, including CLI startup, import/mmap,
lowering, checking, and projection overhead; they are not large-design performance
or physical QoR claims. No GPU execution is involved.

Local raw evidence is preserved under `.work/engine-study/receipts/`, `help/`,
`engine-binaries.json`, and the runner output directories. The tracked harness and
the pinned hashes above make the calibration reproducible without modifying the
engine checkouts or copying external-tool source.

## Actual RustEDA flow controller verification

[`scripts/verify_flow.py`](../../../crates/apps/rusteda-flow/scripts/verify_flow.py) exercises the built
RustEDA flow CLI with explicit executable/library arguments. It freezes and hashes a
copy of the supplied controller so a concurrent build cannot mix producer
identities. The compact measured result is
[`flow-results.json`](flow-results.json).

```sh
mkdir -p .work/engine-study
/usr/bin/time -v -o .work/engine-study/flow-controller.time timeout 180s \
  python3 scripts/verify_flow.py \
    --rusteda flow target/release/rusteda flow \
    --rustsys ../RustSys/target/release/rustsys \
    --rusteda ../RustSTA/target/release/rusteda \
    --ruststa ../RustSTA/target/release/ruststa \
    --yosys "$YOSYS" --opensta "$OPENSTA" \
    --sky130-lib ../RustSys/.work/pdk-trial/lib/sky130_fd_sc_hd__tt_025C_1v80.lib \
    --gf180-lib ../RustSys/.work/pdk-trial/lib/gf180mcu_fd_sc_mcu7t5v0__tt_025C_3v30.lib \
    --out .work/engine-study/fresh-controller-run
```

For both original baseline libraries, the runner requires `init --base-rev empty`,
three executed steps, six real native child receipts with explicit thread counts,
matching summary/engine scopes, and byte-identical mapped output from a separate
direct RustSys synthesis. Independent Yosys good/bad controls consume the actual
RustEDA flow mapped output. OpenSTA compares the actual RustEDA flow timing result for
GF180's original ideal scope and a separate Sky130 ideal-projection flow.

It also verifies zero-step reuse with unchanged checkpoint/artifact identities,
stale-base exit 4, deliberate captured-input tampering rejected before mutation,
and missing-tool failure with null unavailable timing/RSS values. A real unsupported
SDC `set_propagated_clock` must fail with retained Rusteda `D0010` diagnostics;
RustEDA flow must keep timing incomplete. The source libraries and original fixture
files are unchanged by these controls.

The flow harness's enclosing finite file-size cap is compatible with the nested
controller's configured cap. Initial debug probes with a lower inherited file cap
exposed a guard-nesting EPERM; the final supervisor clamps to inherited limits.
The retained failed probes describe that environment mismatch, and the final
release results identify the corrected producer by SHA-256.

A separate real two-corner RustSTA probe used two aliases of the small Sky130 TT
library: `.scope.corner` remained the string `"tt_a"`, while `.scope.corners` was
`["tt_a", "tt_b"]` in both run and report. Summary rows covered each corner.
These envelopes are retained under `.work/engine-study/multicorner-probe/`; a
top-level selected-corner metric must not be mistaken for an aggregate endpoint
count across all corners.

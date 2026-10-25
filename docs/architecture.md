# Architecture and ownership

RustLane is a Rust orchestration executable/library. It treats qualified RustSys,
RustSTA, and physical EDA executables as external providers over files and JSON
contracts. No synthesis, STA, SAT, DRC or placement algorithm is reimplemented by
the orchestrator. No core crate depends on Python, GUI libraries or a reference
tool implementation.

## Bounded immutable workspace

```text
workspace/
  workspace.lock
  HEAD
  inputs/<sha256>.<extension>
  revisions/revN/
    config.rbin
    manifest.rbin
    results.rbin
  steps/<safe-step-id>/attemptN/
    <bounded native output and child timing workspace>
    <per-command stdout/stderr>
```

Each `.rbin` has an eight-byte magic, little-endian payload length, SHA-256 and
bounded JSON payload. Three modular tables are independently memory-mapped and
validated on query. They are disk side tables, not an EDA graph representation.
The 16 MiB payload cap is checked before mapping or serialization; file hashes
are streamed with a 64 KiB buffer. No graph paths, cuts or cones are materialized.

Writers take an exclusive nonblocking `workspace.lock`; readers take a shared
lock. Every mutation names the expected HEAD. Cooperating writers publish
new read-only files; they never edit/truncate a mapped published table. A
revision directory is synced and renamed into place, then HEAD is replaced
atomically and its parent synced. A crash between directory and HEAD publication
can leave an orphan snapshot. Future commits preserve it and reserve a new
number, examining at most 128 occupied slots before a structured diagnostic.
Untrusted concurrent filesystem modification by the same user is outside this
cooperating-writer contract.

State captures resolved configuration, original input identity, immutable input
copies, exact tool hashes, declared output hashes, bounded process receipts and
semantic verdicts. Reuse checks original/captured inputs and previously passed
tools/artifacts. Reconfiguration is a new workspace. Publication failures retain
staging data for inspection; they do not claim that HEAD was advanced.

## Execution and recovery

The bounded sequential plan uses at most 128 steps, with at most 1,000 attempts
per step. A running checkpoint precedes tool execution, and a passed/failed
checkpoint follows it. Failed attempts remain in their own directories. Only
an explicitly requested retry reruns a failed step; unknown interrupted effects
are not automatically retried or cleaned by process-name searches.

The Linux process supervisor owns a child process group, denies group/namespace
escape, closes stdin, enforces virtual-address and output budgets, monitors a
deadline, captures bounded logs and reaps the owned leader. It uses `execve`
without an implicit shell fallback and checks cancellation. It records only the
selected program/argv, not ambient environment variables. Detailed boundaries
and actual tests are in [process-supervisor.md](verification/process-supervisor.md).

The process model is deterministic in input/step selection; engine runtime
durations and timestamps are measured telemetry and not deterministic outputs.
External tools and their environment can affect behavior. This implementation
does not claim a hermetic container or a general security sandbox.

## Provider contracts and scope

RustSys maps one chosen corner's libraries and checks the result using its
independent CEC command. Arbitrary initial states remain arbitrary; no reset or
don't-care relaxation is added. RustSTA's own `rusteda` imports the mapped
Verilog, all explicit Liberty corners and SDC into a separate child workspace.
The adapter follows returned revisions, runs CPU/f64 analysis with configured
threads/path/state budgets, and performs a full-rerun consistency check.

Reported scope and unsupported-arc diagnostics remain visible even when the
check agrees. Raw outputs remain accessible; result excerpts do not change
their provenance. Reports never convert missing scope or checks to zero.
Independent black-box Yosys/OpenSTA verification is a development workflow,
separate from the production provider selection.

Physical providers execute explicit scripts/argv and verify declared output
identity. The generated Tcl driver quotes literal input bytes through UTF-8 hex,
including empty values. Physical scripts may require additional tool/PDK-specific
contracts; generic output publication does not establish DRC/LVS or timing
correctness. Unsupported extracted-parasitic STA blocks full Classic execution
before any expensive work. An explicit partial-run boundary is required to
execute physical experiments.

## Next capability gates

1. Add and independently qualify a RustSTA-owned SPEF/tree-RC import interface;
   compare OpenSTA on both baseline PDKs, plus full/incremental consistency.
2. Connect explicit CTS/PDN/tap/clock/cell-library collateral and finite physical
   operations without silently reintroducing legacy timing optimization.
3. Validate actual PDK streamout, extraction, antenna, DRC and LVS adapters with
   independently checked verdicts and measured end-to-end resources.

These are implementation and verification requirements, not completed features.

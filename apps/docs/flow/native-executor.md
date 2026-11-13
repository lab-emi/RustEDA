# The native step-graph executor

`rusteda flow exec` runs the step graph of the native RTL-to-GDS recipe: the
`rusteda.ihp-flow.v1` manifest that the recipe composers write. Until this command
existed the graph was executed by a Python script, `producer.py`. The executor is
Rust, lives in `crates/apps/rusteda-flow/src/exec/`, contains no EDA algorithm and
leaves the same files in the same text form, so everything that reads a run (the
gate ingestion of `rusteda flow finish`, the benchmark receipts, the recipe's own
steps) reads it unchanged. The normative text is the
[command specification](../specs/flow/exec.md).

## Where it sits

```text
rusteda flow prepare        request.json  (program + arguments, limits, worker identity)
        |
   the worker controller     control group: memory limit, CPU set, deadline, cleanup
        |
   rusteda flow exec         this command: validates, schedules, records
        |
   step programs             rustsys, rusteda, ruststa, rustroad, script helpers, signoff tools
        |
rusteda flow finish         checks the receipt and ingests the gates
```

The executor is a worker program, like the script it replaces. It does not weaken the
[native process supervisor](verification/process-supervisor.md): that supervisor forbids
its children new sessions and namespaces, and the recipe's steps need sessions of their
own, so recipe steps are never launched through it. The executor starts each step in its
own session, kills the step's process group on failure, deadline and cancellation, and
relies on the controller's control group for the memory limit and for the cleanup of
anything that escaped.

## Run, watch, plan

```sh
export RUSTEDA_WORKER_ID=$(openssl rand -hex 32) RUSTEDA_OWNERSHIP=$PWD/run/ownership
rusteda flow exec --manifest compose/be/prefix-manifest.json --output run/output \
    --base-rev empty --events run/events.jsonl            # the whole graph
rusteda flow exec ... --stop-after fe_frontend_validate   # a prefix
rusteda flow exec ... --dry-run                           # the resolved graph, nothing runs
rusteda flow exec-status --output run/output              # at any time, from any process
```

`--project` defaults to the manifest's directory. `--rustroad` and `--gnu-time` are
optional checks against the pinned tools. A repeated option takes its last value.

| Exit code | Meaning | What exists afterwards |
| --- | --- | --- |
| 0 | completed (`completed` or `frontend_completed`) | receipts, summary, `HEAD` = `rev1` |
| 1 | a step or a final check failed | receipts, a `failed` summary, `HEAD` = `rev1` |
| 2 | refused before any step ran | at most the directory and `workspace.lock` |
| 4 | `REVISION_CONFLICT`: the output directory is not empty | unchanged |
| 130 | cancelled by SIGINT or SIGTERM | receipts, a `failed` summary |

Every invocation prints one JSON document: `ok`, `status`, and on failure
`error.code`, `error.message` and `error.step`.

| Error code | When |
| --- | --- |
| `WORKER_IDENTITY_REQUIRED` | no 64-digit worker identity or no ownership directory |
| `REVISION_CONFLICT`, `WORKSPACE_BUSY` | occupied output directory; another executor holds its lock |
| `MANIFEST_BUDGET`, `MANIFEST_IDENTITY`, `MANIFEST_FIELDS`, `INVALID_JSON` | the manifest is missing, too large, of another kind, or malformed |
| `STEP_INVALID`, `SCOPE_INVALID`, `CPU_ALLOCATION_INVALID`, `PARALLEL_GROUP_INVALID`, `STDOUT_ARTIFACT_POLICY` | a step, the scope, the CPU allocation, a group or the stdout-artifact registry is invalid |
| `UNKNOWN_STEP` | `--stop-after` names no step |
| `INPUT_IDENTITY_CHANGED`, `INPUT_UNAVAILABLE`, `TOOL_ARGUMENT_MISMATCH` | a pinned file or tool differs, is missing, or is not the one named on the command line |
| `TEMPLATE_INVALID` | an unresolved placeholder, an escaping or malformed output path, a template over budget |
| `OWNERSHIP_EXISTS`, `EVENTS_UNAVAILABLE` | the ownership directory or the event file already exists |
| `STEP_FAILED`, `STEP_TIMEOUT`, `STEP_SPAWN_FAILED` | a step exited non-zero, exceeded its deadline, or could not start |
| `LOG_BUDGET_EXCEEDED`, `STDOUT_ARTIFACT_BUDGET_EXCEEDED`, `CAPTURE_CLEANUP_FAILED` | a stream went over its byte budget |
| `FILE_UNAVAILABLE`, `FILE_BUDGET`, `FILE_CHANGED` | a file to be hashed is missing, not a regular file of at most 4 GiB, or changed size |
| `INPUT_CHANGED_DURING_RUN`, `GATE_SET_MISMATCH`, `SCHEDULER_STALLED` | a final check failed, or a group could not make progress |
| `CANCELLED`, `PROCESS_CLEANUP_TIMEOUT`, `PROCESS_OWNERSHIP_LOST` | a signal stopped the run; a killed step did not exit; a step was reaped elsewhere |

## The manifest

```json
{"schema": "rusteda.ihp-flow.v1", "name": "...", "pdk": "ihp-sg13g2", "library": "ez130_8t",
 "corners": ["tt", "ss"],
 "files": {"rtl": {"path": "/abs/design.v", "sha256": "...", "bytes": 123}},
 "tools": {"rustroad": {"path": "...", "sha256": "...", "bytes": 1}, "gnu_time": {"...": "..."}},
 "scope": {"threads": 4, "cpu_affinity": [0, 1, 2, 3], "memory_mib": 8192,
           "max_log_bytes": 67108864, "max_stdout_artifact_bytes": 1610612736},
 "steps": [
   {"id": "fe_context", "kind": "render", "destination": "frontend/context.json",
    "content": {"run_root": "{output}", "rtl": {"$input": "rtl"}}},
   {"id": "be_route", "kind": "command", "tool": "rustroad", "timeout_s": 43200,
    "arguments": ["--workspace", "{output}/frontend/road-final", "--threads", "{threads}", "route"]}],
 "parallel_groups": [{"max_parallel": 2, "max_memory_mib": 7168, "steps": {
   "be_main": {"depends_on": [], "cpu_slots": [0, 1], "memory_mib": 2560},
   "be_lvs": {"depends_on": ["be_main"], "cpu_slots": [2, 3], "memory_mib": 2048}}}],
 "stdout_artifacts": {"be_def": {"path": "logs/be_def.stdout", "format": "def", "max_bytes": 134217728}},
 "gates": {"drc": "backend/main/summary.json"},
 "artifacts": {"gds": "backend/top.gds"}}
```

Steps run in list order. A parallel group is a contiguous region of the list whose steps
start as soon as their dependencies have succeeded, their CPU slots are free and the
group's width and memory cap admit them. CPU slots index `scope.cpu_affinity`; two steps
that share a slot never overlap. A step whose tool is a script interpreter is an ordinary
command: the interpreter is one more pinned program.

## What a run leaves

```text
<output>/
  workspace.lock            held exclusively while the executor runs
  HEAD                      rev0 while running, rev1 when the summary is written
  step-receipts.json        one receipt per finished step, manifest order, updated after every step
  summary.json              completed | frontend_completed | failed, accounting, resources
  logs/<id>.command.json    the command line and working directory (CPU set and reservation in a group)
  logs/<id>.stdout .stderr  raw streams, hashed in the receipt
  logs/<id>.time            the resource recorder's report
  tmp/                      TMPDIR of the steps (tmp/<id> for a group step)
  ...                       whatever the steps write
<ownership>/native.json     worker identity and manifest identity
<ownership>/../worker-metrics.json
```

## Progress events

`--events FILE` (or `-` for standard output) gives one JSON object per line:

```json
{"event":"run_started","schema":"rusteda.flow-exec.event.v1","t":0.11,"unix_time":1794000000.1,"steps":114,"manifest_sha256":"...","output":"...","pid":4242,"stop_after":null,"worker_id":"..."}
{"event":"step_started","id":"be_route","kind":"command","pid":4250,"t":41.2,"unix_time":1794000041.2}
{"event":"step_finished","id":"be_route","kind":"command","exit_code":0,"wall_seconds":228.4,"max_rss_kib":1048172,"user_seconds":227.1,"system_seconds":1.3,"stdout_bytes":812,"stderr_bytes":0,"t":269.6,"unix_time":1794000269.6}
{"event":"step_failed","id":"be_timing","exit_code":1,"error":{"code":"STEP_FAILED","message":"step failed: be_timing, exit=1"},"...":"..."}
{"event":"run_finished","status":"failed","wall_seconds":433.3,"steps_completed":109,"error":{"code":"STEP_FAILED","message":"...","step":"be_timing"},"executor_user_seconds":0.4,"executor_system_seconds":0.3,"t":433.3,"unix_time":1794000433.3}
```

A group step's `step_started` adds `cpu_affinity` and `memory_reservation_mib`. The
resource numbers of a command are those of its reaped process tree. `exec-status`
answers the same questions from the directory alone, also for a run that an older
executor or the reference script started.

## Bounds and guarantees

- The manifest and every pinned input are verified before the first step; the pinned
  inputs are verified again after the last one.
- One coordinator thread launches and reaps; at most 16 steps are live. Up to eight
  threads hash files, 1 MiB each. Expected executor memory is below 64 MiB.
- A step may not outlive the run through a normal path: its process group is killed
  when a group step ends, on every failure, on a deadline and on cancellation. A
  descendant that left the group, an executor killed with SIGKILL and a host crash are
  the controller's control group to clean.
- Intermediate documents are renamed into place without `fsync`; the final receipts,
  the summary, the ownership record and the worker metrics are synced. A run is never
  resumed from intermediate files, so a host crash loses nothing that could be used.

## Differences from the reference script

Outputs of a successful run are the same files with the same bytes wherever two runs
of the script agree with each other. The deliberate differences:

| Topic | Reference script | Native executor |
| --- | --- | --- |
| Validation | serial steps are checked when reached | the whole manifest and every argument that will run are checked before anything is created |
| Lock | waits for another executor, then refuses | refuses at once (`WORKSPACE_BUSY`) |
| Serial step session | shares the executor's session; a deadline kills only the resource recorder | own session; a deadline or budget failure kills the step's process group |
| Signals | SIGTERM ends the script and leaves the steps running | steps are killed, receipts and a failed summary are written, exit code 130 |
| Receipts on disk | a render's receipt appears with the next command's | published after every step |
| Durability | `fsync` after every document | only the final documents are synced |
| Policy source | the stdout-artifact registry requires the script itself as pinned `files.producer` | the registry is checked against the built-in export list; `files.producer`, when present, is verified like any other input |
| Tool arguments | `--rustroad`, `--gnu-time`, `--project`, `--pdk` required | optional; checked when given |
| Malformed templates | some abort the script without a summary (exit 2) | coded failure with a summary |
| Refusal exit code | 2, also for an occupied directory | 2, and 4 for `REVISION_CONFLICT` |
| Control-group peaks | cgroup v2 | cgroup v2, else the cgroup v1 memory controller |
| Operating-system error texts | the interpreter's | this program's (`error.code` is stable) |

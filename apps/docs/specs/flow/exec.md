# `rusteda flow exec`: the native step-graph executor

`rusteda flow exec` executes a `rusteda.ihp-flow.v1` manifest: a hash-pinned list of
render and command steps with optional parallel groups. It is implemented in
`crates/apps/rusteda-flow/src/exec/` and replaces the reference executor script
`integration/ibex_sg13g2_ez130/producer.py`, whose files it reproduces. The guide is
[the native executor](../../flow/native-executor.md).

## Validation and refusal

**FLOWEXEC-REQ-0001** `rusteda flow exec --manifest <FILE> --output <DIR> --base-rev empty` runs the steps of the manifest in manifest order, each to completion before the next one, except inside a parallel group (FLOWEXEC-REQ-0014). `--project <DIR>` is the base of relative pinned paths (default: the manifest's directory); `--workspace` is accepted for `--output`.

**FLOWEXEC-REQ-0002** Before anything is created, every entry of `files` and `tools` is hashed (SHA-256) and must have the stated hash and size; a difference is `INPUT_IDENTITY_CHANGED` naming the entry, an unreadable entry is `INPUT_UNAVAILABLE`. `--rustroad` and `--gnu-time`, when given, must resolve to the pinned `rustroad` and `gnu_time` tools (`TOOL_ARGUMENT_MISMATCH`); commands are launched and recorded with the given spelling of `--gnu-time`.

**FLOWEXEC-REQ-0003** A run is bound to a worker identity of 64 lower-case hexadecimal digits (`--worker-id`, else `RUSTEDA_WORKER_ID`) and an ownership directory (`--ownership`, else `RUSTEDA_OWNERSHIP`), both exported to every step; without them the run is refused with `WORKER_IDENTITY_REQUIRED`. `--stop-after` must name one step (`UNKNOWN_STEP`). A refused run leaves nothing but the output directory and its `workspace.lock`.

**FLOWEXEC-REQ-0004** The output directory must be new or hold only `workspace.lock`, and `--base-rev` must be `empty`; otherwise the run is refused with `REVISION_CONFLICT`. The executor holds `workspace.lock` exclusively for the whole run; a second executor on the same directory is refused with `WORKSPACE_BUSY` instead of waiting.

**FLOWEXEC-REQ-0005** The manifest is at most 2 MiB and is validated as a whole before anything runs: schema, PDK, library and corner identity (`MANIFEST_IDENTITY`), exactly the known top-level members (`MANIFEST_FIELDS`), at most 129 steps, 256 files, 32 tools and 128 artifacts (`MANIFEST_BUDGET`), unique step handles of `[A-Za-z0-9_-]{1,128}`, exactly the members of a render or command step, a pinned tool, 1 to 256 string arguments and a deadline of 1 to 86,400 s (`STEP_INVALID`), a log budget of 1 KiB to 64 MiB (`SCOPE_INVALID`) and a CPU allocation of 1 to 16 distinct CPUs equal in number to `scope.threads` (`CPU_ALLOCATION_INVALID`). The allocated CPUs must be available to the process and be distinct physical cores.

**FLOWEXEC-REQ-0006** `parallel_groups` holds at most eight groups. A group is a contiguous region of the step list, disjoint from every other group, with `max_parallel` of 1 to 16 and `max_memory_mib` of at most `scope.memory_mib - 128`. Every step of a group lists `depends_on`: unique earlier steps of the same group. A command step also lists `cpu_slots` (distinct indices into `scope.cpu_affinity`) and `memory_mib` (1 to the group's cap); a render step lists nothing else. Any violation is `PARALLEL_GROUP_INVALID`.

**FLOWEXEC-REQ-0007** Every argument of every command that is going to run is expanded once before the run starts: `{output}`, `{threads}`, `{files:<name>}` and `{tools:<name>}`. A `{files:...}` or `{tools:...}` placeholder that is left, an expanded argument over 65,536 characters or one with a NUL character refuses the run with `TEMPLATE_INVALID` naming the step.

**FLOWEXEC-REQ-0008** Every JSON document the executor writes into a run (rendered templates, command records, receipts, summary, ownership record, worker metrics) is the text `json.dump(value, indent=2)` of CPython followed by one newline: members in insertion order, integers of any size kept as integers, floats as their shortest round-trip digits (the even last digit when two shortest forms are equally close) in positional form for a decimal exponent of -4 to 15 and `d.ddde±XX` otherwise, and every character outside printable ASCII escaped as `\uXXXX`. `NaN` and infinities are rejected.

**FLOWEXEC-REQ-0009** A file identity is `{"path", "sha256", "bytes"}` with the path resolved through symbolic links. The file must be a regular file of at most 4 GiB whose size does not change while it is hashed.

## Execution

**FLOWEXEC-REQ-0010** A command step runs `<gnu_time> -v -o logs/<id>.time <tool path> <expanded arguments>` without a shell, with the output directory as working directory, closed standard input, its own session, no inherited descriptor above standard error, and the executor's environment in its order with `CARGO_BUILD_JOBS=2`, `RUST_TEST_THREADS=2`, `OMP_NUM_THREADS` and `RAYON_NUM_THREADS` set to `scope.threads`, `OPENBLAS_NUM_THREADS=1`, `TMPDIR=<output>/tmp` and without `RUSTROAD_RULE_DUMP`. Its command line and working directory are recorded in `logs/<id>.command.json` before it starts; standard output and error go to new files `logs/<id>.stdout` and `logs/<id>.stderr`.

**FLOWEXEC-REQ-0011** A render step writes its `content` to `destination` (a normal relative path that must stay inside the output directory). Strings are expanded as arguments are. An object with the single member `$identity` or `$sha256` becomes the identity (or the hash) of a file of this run, `$input` and `$tool` the verified identity of a pinned file or tool. One render visits at most 65,536 nodes to depth 32.

**FLOWEXEC-REQ-0012** `--stop-after <ID>` ends the run after that step and writes a `rusteda.ihp-frontend.v1` summary (`status: frontend_completed`) that lists the produced artifacts, the unavailable ones and the fixed set of unverified items, without gates. A group that holds the stop target runs as a serial prefix in manifest order, with serial receipts.

**FLOWEXEC-REQ-0013** A serial step that exits with a non-zero code (`step failed: <id>, exit=<code>`), exceeds its deadline (`step deadline exceeded: <id>`), brings the diagnostic bytes of the run over `scope.max_log_bytes`, or a render that fails, ends the run: no later step runs, the summary has `status: failed` with the message as `error` and only `failed_items`, and `unverified_items` is `["complete_flow"]`. On a deadline or budget failure the whole process group of the step is killed. After the last step every pinned input is hashed again; a change fails the run (`producer input changed during execution`).

**FLOWEXEC-REQ-0014** The ready steps of a parallel group are launched in manifest order, each on the CPUs its `cpu_slots` select from `scope.cpu_affinity`, set before the step's program is executed so that every descendant inherits them.

**FLOWEXEC-REQ-0015** A group step runs with `OMP_NUM_THREADS` and `RAYON_NUM_THREADS` equal to the number of its CPUs, `TMPDIR=<output>/tmp/<id>` (created for it) and `RUSTEDA_PARALLEL_STEP=<id>`; its command record adds `cpu_affinity` and `memory_reservation_mib`.

**FLOWEXEC-REQ-0016** A group step starts only when all its `depends_on` steps have succeeded, fewer than `max_parallel` steps are live, none of its CPU slots is used by a live step and the memory reservations of the live steps plus its own do not exceed `max_memory_mib`. A group in which steps are pending and none is live fails with `SCHEDULER_STALLED`.

**FLOWEXEC-REQ-0017** When a group step ends, every process left in its process group is killed. The first failure of a group (a non-zero exit, a deadline, a budget, a render) kills the process groups of all live steps of the group; their receipts carry `capture_error: "parallel group cancelled: <reason>"`. A step whose dependency failed is never launched, and no step after the group runs.

**FLOWEXEC-REQ-0018** A group step that exceeds its deadline fails the group with `step deadline exceeded: <id>`.

**FLOWEXEC-REQ-0019** The streams of all live group steps count against `scope.max_log_bytes` together with the bytes already recorded; the sizes are sampled at least every 50 ms and when a step ends.

## Receipts, summary and accounting

**FLOWEXEC-REQ-0020** `step-receipts.json` is an array in manifest order, published atomically after every step. A render receipt has exactly `id`, `kind`, `wall_seconds`; a serial command receipt exactly `id`, `kind`, `wall_seconds`, `exit_code`, `stdout`, `stderr` (the two stream identities), then `stdout_artifact` and `capture_error` when they apply. `exit_code` is the exit status, the negated signal number, or `null` when the step was stopped by the executor.

**FLOWEXEC-REQ-0021** A run without stop target that succeeds writes a `rusteda.ihp-rtl2gds.v1` summary with `status: completed`, the manifest hash, the scope, the identity of each of the sixteen gate reports and of every artifact; a different gate set fails the run. Every summary ends with `capture_accounting` and `resources` (wall seconds, peak resident set of the reaped processes, the control group's memory and swap peaks for cgroup v2 or v1, the CPU allocation). The run writes `HEAD` (`rev0` at start, `rev1` at end), `<ownership>/native.json` (worker identity, `implementation: native`, manifest identity) and `worker-metrics.json` beside the ownership directory.

**FLOWEXEC-REQ-0022** A group command receipt has exactly `id`, `kind`, `exit_code`, `wall_seconds`, `stdout`, `stderr`, `started_seconds`, `cpu_affinity`, `memory_reservation_mib`, `depends_on`, then `stdout_artifact` and `capture_error` when they apply. Receipts of a group are stored in manifest order, never in completion order.

**FLOWEXEC-REQ-0023** Without `stdout_artifacts` every byte of every stream is a diagnostic. With it, a command step may declare its standard output a data stream only if it is one of the pinned exports (pin capacitances, RustROAD Verilog, DEF and placement seed, RustSTA design-rule, endpoint and hold-fix reports) recognised from its unexpanded arguments; the entry must be `{"path": "logs/<id>.stdout", "format", "max_bytes"}` with the format of that export and at most its cap (16, 64 or 128 MiB), every entry must be referenced exactly once, and `scope.max_stdout_artifact_bytes` bounds the sum (`STDOUT_ARTIFACT_POLICY`).

**FLOWEXEC-REQ-0024** A declared stdout artifact is charged to its own cap and to the aggregate cap, not to the log budget; all standard error stays a diagnostic. An overflow fails the step (`capture_error`, non-zero `exit_code`) and is never reported as a success; a serial capture is streamed and stops within one 64 KiB chunk and a 1 MiB drain.

## Progress, status and dry run

**FLOWEXEC-REQ-0025** `--events <FILE>` writes one JSON object per line to a new file: `run_started`, `step_started` (command steps: process identifier, CPU set, memory reservation), `step_finished`, `step_failed` (with a coded `error`) and `run_finished`. Every line has `schema: rusteda.flow-exec.event.v1`, `event`, `t` (seconds since the run started) and `unix_time`; a finished command adds its exit code, wall seconds, peak resident set, user and system CPU seconds and stream sizes. An existing file is refused (`EVENTS_UNAVAILABLE`).

**FLOWEXEC-REQ-0026** `rusteda flow exec-status --output <DIR> [--manifest <FILE>]` reads a running or finished output directory without changing it and reports `state` (`absent`, `empty`, `running`, `completed`, `frontend_completed`, `failed`, `interrupted`), `HEAD`, whether an executor holds the lock, the finished, failed, running and last finished steps, the pending steps when the manifest is known (given, or named by the ownership record beside the directory), and the summary's status and error.

**FLOWEXEC-REQ-0027** `rusteda flow exec --dry-run` validates the manifest, hashes the pinned inputs and prints the resolved step graph: per step its index, kind, whether it will run, its mode (`serial` or `parallel`), the steps that must finish before it, and for a command its full command line, working directory, deadline, CPU set, tool thread count, memory reservation, stream files and stdout artifact. It needs no worker identity and creates nothing.

## Command-line contract

**FLOWEXEC-REQ-0028** Every invocation prints one JSON document on standard output with `schema: rustlane.v1` and `ok`. A run adds `status` (the summary's), `output`, `steps_completed`, `steps_total`, `wall_seconds`, the executor's own CPU seconds and, when it failed, `error` with `code`, `message` and the failing `step`.

**FLOWEXEC-REQ-0029** Exit codes of `exec`: 0 the run completed, 1 the run started and failed (a failed summary exists), 2 the run was refused before any step ran, 4 `REVISION_CONFLICT`, 130 cancelled by a signal. A repeated option takes its last value.

**FLOWEXEC-REQ-0030** With `--events -` standard output is the event stream, and its last line is the result document with `event: result`.

**FLOWEXEC-REQ-0031** SIGINT and SIGTERM stop the run: the process groups of the live steps are killed, their receipts carry `capture_error: "cancelled by signal <n>"`, the summary is `failed` with that message, the error code is `CANCELLED` and the exit code is 130.

## Use as the worker program of a flow

**FLOWEXEC-REQ-0032** A `rusteda` flow configuration for the IHP SG13G2/EZ130 PDK may set `road.executor` to `native` and omit `road.driver`. `rusteda flow prepare` then publishes a request whose program is the running `rusteda` and whose arguments are `flow exec --project <DIR> --manifest <FILE> --pdk ihp --output <attempt>/output --rustroad <FILE> --gnu-time <FILE> --base-rev empty`, marked `executor: native`; no script interpreter is resolved, captured or needed for the worker program, and the manifest need not pin a `producer` file. The default `driver` executor is unchanged. A configuration with both a driver and the native executor, or the native executor for another PDK, is rejected. `rusteda flow run` still refuses a `rusteda` flow with `EXTERNAL_WORKER_REQUIRED`: the control group of the worker belongs to a controller, which runs the published request between `prepare` and `finish`.

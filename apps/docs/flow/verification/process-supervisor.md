# Native tool process supervision

RustEDA flow configures child stdio through `std::process::Command` and calls Linux
`execve` directly after installing guards, without shell interpolation, implicit
shell fallback or PATH lookup. Relative executable paths resolve against
the tool working directory. Stdin is `/dev/null`. The caller supplies a new log
directory; existing directories and files are never overwritten.

## Resource and work bounds

- `timeout_secs` is a positive wall deadline, checked every 10 ms between bounded
  pipe drains. SIGINT and SIGTERM also request immediate group cleanup. A
  kernel-blocked operation can delay scheduling or filesystem writes; this is
  not a hard real-time guarantee.
- `memory_mib` sets Linux `RLIMIT_AS` for each child process. It limits virtual
  address space, not aggregate RSS for a process tree. This CPU execution path
  does not support CUDA virtual address reservation. Aggregate limits require an
  outer cgroup, as prescribed in `memory-budget.md`.
- Standard output and standard error share `max_log_bytes`; fixed 16 KiB read
  buffers and at most eight reads per stream per monitor iteration bound the
  supervisor's transient log memory and monitor work. No file-sized log buffers
  are allocated. Extra output triggers group termination. Exact-budget output
  remains valid if no additional byte arrives.
- `RLIMIT_FSIZE` limits individual generated files to the greater of
  `memory_mib * 1 MiB` and `max_log_bytes`. It is distinct from the combined log
  budget, so normal artifacts may exceed the log cap. Core dumps are disabled.
- Tighter inherited soft and hard resource caps remain in force; the supervisor
  never tries to raise them. Its combined log budget is also reduced by its own
  inherited file-size limit, preventing SIGXFSZ from an oversized log write.
- Command metadata is bounded at 256 arguments, 1 MiB total argument text and a
  4 KiB executable path. Environment variables are inherited but never recorded.
- `wait4` measures actual direct-process peak RSS in KiB. It does not measure
  aggregate concurrent descendants, and this module makes no such claim.

The upfront test estimate is two test workers. Most supervised fixtures have a
512 MiB address-space cap and a few threads. The SIGTERM regression additionally
runs a trusted 1 GiB-address-space controller with a 64 MiB individual-file cap
and an eight-second outer deadline; its tool receives a 512 MiB address-space
cap and a ten-second fallback deadline. At most three fixture processes can
overlap across the two workers, with expected resident state below 64 MiB each
(under 192 MiB total fixture RSS). There are no EDA graph,
path, cut, tag-table, PDK or SAT allocations. Each supervisor uses two fixed
16 KiB stack buffers transiently and bounded command metadata. Builds use
`CARGO_BUILD_JOBS=2`; tests use `RUST_TEST_THREADS=2` and outer memory/time guards.

## Process ownership and cleanup

Each tool becomes a new process-group leader before exec. A narrow inherited
seccomp filter denies `setsid`, `setpgid`, `setns`, `unshare` and namespace-creating
`clone` flags. `clone3` returns `ENOSYS`, allowing libc to fall back to ordinary
`clone` for threads and subprocesses. Compatibility syscall ABIs are rejected.
The filter supports little-endian Linux x86_64 and aarch64. Other platforms fail
with `UNSUPPORTED_PLATFORM`; missing Linux guards fail before tool execution.

The supervisor uses `waitid(WNOWAIT)` to retain the leader's PID until after it
sends SIGKILL to the process group. This prevents PID reuse from transferring
cleanup to an unrelated group. Group cleanup happens on every completion,
including a successful direct child exit, so a tool may not leave a daemon.
External code must not reap this module's children. An incompatible SIGCHLD
auto-reap policy is rejected, and observed loss of child ownership is an error.

SIGINT/SIGTERM handlers are installed only during execution, with prior handlers
restored afterward. A lock serializes these scopes. Cancellation yields a
failing receipt with `exit_code: null`; the current receipt format does not have
a separate cancellation field. SIGKILL and host crashes cannot run controlled
cleanup. The immediate child has a parent-death SIGKILL, but full descendant
cleanup after an uncatchable supervisor death requires an outer cgroup.

After SIGKILL the supervisor permits up to two seconds to reap the direct child.
If the kernel retains it in uninterruptible I/O, execution reports
`PROCESS_CLEANUP_TIMEOUT`; it does not claim cleanup succeeded. Orphaned zombie
reaping belongs to the operating system's subreaper/init, not RustEDA flow. This is
resource and process containment, not a general filesystem or hostile-code
security sandbox.

## Verification status

Focused tests are implemented for literal arguments, rejection of executable
text without a shebang, actual success and nonzero
exit, timeout, bounded stdout, overwrite refusal, null stdin, denied process-group
escape, ordinary native Rust threads, descendant termination after timeout, and
descendant termination after successful leader exit. A further regression sends
real SIGTERM to an active controller, checks termination of its target process,
checks restoration of both prior signal handlers, and exercises a stricter
inherited file cap.

On 2026-10-23, Linux x86_64 verification passed:

```text
cargo test --locked --lib process::tests
12 passed; 0 failed; 5 ignored fixture entrypoints
```

All five ignored fixture entrypoints were explicitly executed as guarded
children by the passing tests. The run used `CARGO_BUILD_JOBS=2`,
`RUST_TEST_THREADS=2`, a 16 GiB virtual-memory guard and a 120-second outer wall
guard. `/usr/bin/time -v` measured 2.47 seconds elapsed including incremental
compilation and 232,744 KiB maximum RSS, below the 2 GiB standard-test threshold.
Local raw receipts are `.work/verification/process-tests-final.log` and
`process-tests-final.time`. The earlier initial build/test run also passed, with
5.31 seconds elapsed and 281,400 KiB maximum RSS.

SIGINT delivery, aarch64, missing seccomp support, external child reaping,
uninterruptible kernel I/O, host crashes and outer-cgroup OOM behavior remain
unverified. No EDA equivalence or PDK timing claim follows from these
process-control tests.

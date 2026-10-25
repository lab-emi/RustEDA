//! Synchronous native CPU tool execution with bounded logs and Linux guards.
//!
//! `memory_mib` limits each process's virtual address space; it is not an
//! aggregate process-tree RSS limit. Individual output files are also limited
//! to the greater of that byte count and `max_log_bytes`; tighter inherited
//! resource limits are preserved. Standard output and
//! standard error share `max_log_bytes`. Peak RSS is measured by `wait4` for
//! the direct tool process, not summed across concurrently running descendants.
//!
//! A separate process group and an inherited seccomp filter prevent descendants
//! from changing groups or creating namespaces. The group is killed on every
//! completion, including a successful leader exit: tools may not leave daemons.
//! This is resource/process containment, not a general security sandbox.

use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessLimits {
    pub timeout_secs: u64,
    pub memory_mib: u64,
    pub max_log_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessReceipt {
    pub program: String,
    pub arguments: Vec<String>,
    pub elapsed_seconds: f64,
    pub peak_rss_kib: Option<u64>,
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub log_limit_exceeded: bool,
    pub stdout: PathBuf,
    pub stderr: PathBuf,
}

impl ProcessReceipt {
    pub fn success(&self) -> bool {
        self.exit_code == Some(0) && !self.timed_out && !self.log_limit_exceeded
    }
}

/// Execute a tool without a shell, with null stdin and new, bounded log files.
///
/// Relative program paths are resolved against `cwd`; PATH lookup is not used.
/// `log_dir` must not already exist. Its parent must exist. Only the intended
/// executable path and arguments are recorded; the inherited environment is
/// never logged. SIGINT/SIGTERM cancel the active run and produce a failing
/// receipt (a signal exit has `exit_code: None`). Calls are serialized while
/// temporary signal handlers are installed, and prior handlers are restored.
pub fn run(
    program: &Path,
    args: &[String],
    cwd: &Path,
    log_dir: &Path,
    limits: &ProcessLimits,
) -> Result<ProcessReceipt> {
    #[cfg(all(
        target_os = "linux",
        target_endian = "little",
        any(target_arch = "x86_64", target_arch = "aarch64")
    ))]
    {
        linux::run(program, args, cwd, log_dir, limits)
    }
    #[cfg(not(all(
        target_os = "linux",
        target_endian = "little",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )))]
    {
        let _ = (program, args, cwd, log_dir, limits);
        Err(Error::new(
            "UNSUPPORTED_PLATFORM",
            "guarded tool execution requires Linux x86_64 or aarch64",
        ))
    }
}

#[cfg(all(
    target_os = "linux",
    target_endian = "little",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
mod linux {
    use super::*;
    use std::ffi::CString;
    use std::fs::{self, File, OpenOptions};
    use std::io::{self, Read, Write};
    use std::os::fd::{AsRawFd, RawFd};
    use std::os::unix::process::CommandExt;
    use std::process::{Child, Command, Stdio};
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicI32, Ordering};
    use std::time::{Duration, Instant};

    const MAX_ARGUMENTS: usize = 256;
    const MAX_ARGUMENT_BYTES: usize = 1024 * 1024;
    const READ_CHUNK: usize = 16 * 1024;
    const DRAIN_CHUNKS: usize = 8;
    static RUN_LOCK: Mutex<()> = Mutex::new(());
    static CANCELLATION: AtomicI32 = AtomicI32::new(0);
    unsafe extern "C" {
        static mut environ: *mut *mut libc::c_char;
    }

    fn io_error(action: &str, error: io::Error) -> Error {
        Error::new("PROCESS_IO_ERROR", format!("{action}: {error}"))
    }

    pub(super) fn run(
        program: &Path,
        args: &[String],
        cwd: &Path,
        log_dir: &Path,
        limits: &ProcessLimits,
    ) -> Result<ProcessReceipt> {
        let memory_bytes = limits
            .memory_mib
            .checked_mul(1024 * 1024)
            .filter(|v| *v > 0)
            .ok_or_else(|| {
                Error::new(
                    "INVALID_PROCESS_LIMIT",
                    "memory_mib must be positive and fit in u64 bytes",
                )
            })?;
        if memory_bytes == libc::RLIM_INFINITY
            || limits.max_log_bytes == 0
            || limits.max_log_bytes == libc::RLIM_INFINITY
            || limits.timeout_secs == 0
        {
            return Err(Error::new(
                "INVALID_PROCESS_LIMIT",
                "finite, positive time, memory and log limits are required",
            ));
        }
        let deadline_duration = Duration::from_secs(limits.timeout_secs);
        if Instant::now().checked_add(deadline_duration).is_none() {
            return Err(Error::new(
                "INVALID_PROCESS_LIMIT",
                "timeout exceeds the platform clock range",
            ));
        }
        let argument_bytes = args
            .iter()
            .try_fold(0_usize, |n, arg| n.checked_add(arg.len()));
        if args.len() > MAX_ARGUMENTS || argument_bytes.is_none_or(|n| n > MAX_ARGUMENT_BYTES) {
            return Err(Error::new(
                "COMMAND_BUDGET_EXCEEDED",
                "at most 256 arguments and 1 MiB of argument text are allowed",
            ));
        }
        let argument_cstrings: Vec<_> = args
            .iter()
            .map(|arg| CString::new(arg.as_bytes()))
            .collect::<std::result::Result<_, _>>()
            .map_err(|_| {
                Error::new(
                    "INVALID_ARGUMENT",
                    "tool arguments cannot contain NUL bytes",
                )
            })?;
        let mut inherited_file_limit: libc::rlimit = unsafe { std::mem::zeroed() };
        if unsafe { libc::getrlimit(libc::RLIMIT_FSIZE, &mut inherited_file_limit) } != 0 {
            return Err(io_error(
                "inspect inherited file guard",
                io::Error::last_os_error(),
            ));
        }
        // The controller writes these files, so honor its own file limit
        // before write(2) could deliver SIGXFSZ to the controller itself.
        let log_cap = limits.max_log_bytes.min(inherited_file_limit.rlim_cur);
        let cwd =
            fs::canonicalize(cwd).map_err(|e| io_error("resolve tool working directory", e))?;
        let resolved = if program.is_absolute() {
            program.to_path_buf()
        } else {
            cwd.join(program)
        };
        let program =
            fs::canonicalize(resolved).map_err(|e| io_error("resolve tool executable", e))?;
        let program_text = program
            .to_str()
            .filter(|s| s.len() <= 4096)
            .ok_or_else(|| {
                Error::new(
                    "INVALID_EXECUTABLE_PATH",
                    "executable path must be UTF-8 and at most 4096 bytes",
                )
            })?
            .to_owned();
        let executable_cstring = CString::new(program_text.as_bytes()).map_err(|_| {
            Error::new(
                "INVALID_EXECUTABLE_PATH",
                "executable path contains a NUL byte",
            )
        })?;
        fs::create_dir(log_dir).map_err(|e| {
            if e.kind() == io::ErrorKind::AlreadyExists {
                Error::new(
                    "LOG_DIRECTORY_EXISTS",
                    "refusing to overwrite an existing tool log directory",
                )
            } else {
                io_error("create tool log directory", e)
            }
        })?;
        let log_dir =
            fs::canonicalize(log_dir).map_err(|e| io_error("resolve tool log directory", e))?;
        let stdout_path = log_dir.join("stdout.log");
        let stderr_path = log_dir.join("stderr.log");
        let mut stdout_file = new_log(&stdout_path)?;
        let mut stderr_file = new_log(&stderr_path)?;

        let _run_lock = RUN_LOCK
            .lock()
            .map_err(|_| Error::new("SIGNAL_CONTROL_FAILED", "tool execution lock was poisoned"))?;
        let _signals = SignalScope::install()?;
        let parent_pid = unsafe { libc::getpid() };
        let file_bytes = memory_bytes.max(limits.max_log_bytes);
        let filter = containment_filter();
        let mut command = Command::new(&program);
        command
            .args(args)
            .current_dir(&cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        // Only async-signal-safe libc calls run between fork and exec. All
        // allocations, including construction of the BPF filter, happen above.
        unsafe {
            command.pre_exec(move || {
                if libc::setpgid(0, 0) != 0 {
                    return Err(io::Error::last_os_error());
                }
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                    return Err(io::Error::last_os_error());
                }
                if libc::getppid() != parent_pid {
                    libc::_exit(125);
                }
                macro_rules! restrict_limit {
                    ($resource:expr, $requested:expr) => {{
                        let mut inherited: libc::rlimit = std::mem::zeroed();
                        if libc::getrlimit($resource, &mut inherited) != 0 {
                            return Err(io::Error::last_os_error());
                        }
                        // A caller's tighter outer limit remains authoritative.
                        let bound = $requested.min(inherited.rlim_cur).min(inherited.rlim_max);
                        let limit = libc::rlimit {
                            rlim_cur: bound,
                            rlim_max: bound,
                        };
                        if libc::setrlimit($resource, &limit) != 0 {
                            return Err(io::Error::last_os_error());
                        }
                    }};
                }
                restrict_limit!(libc::RLIMIT_AS, memory_bytes);
                restrict_limit!(libc::RLIMIT_FSIZE, file_bytes);
                restrict_limit!(libc::RLIMIT_CORE, 0_u64);
                if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
                    return Err(io::Error::last_os_error());
                }
                let filter_program = libc::sock_fprog {
                    len: filter.len() as u16,
                    filter: filter.as_ptr().cast_mut(),
                };
                if libc::prctl(
                    libc::PR_SET_SECCOMP,
                    libc::SECCOMP_MODE_FILTER,
                    &filter_program,
                ) != 0
                {
                    return Err(io::Error::last_os_error());
                }
                // Command's usual execvp path can fall back to a shell for
                // shebang-less executable text. Direct execve has no fallback.
                // Rust std holds its environment read lock across fork; this
                // child only reads the inherited C environment pointer.
                let mut argv = [std::ptr::null::<libc::c_char>(); MAX_ARGUMENTS + 2];
                argv[0] = executable_cstring.as_ptr();
                for (index, arg) in argument_cstrings.iter().enumerate() {
                    argv[index + 1] = arg.as_ptr();
                }
                libc::execve(
                    executable_cstring.as_ptr(),
                    argv.as_ptr(),
                    environ as *const *const libc::c_char,
                );
                Err(io::Error::last_os_error())
            });
        }
        let started = Instant::now();
        let child = command.spawn().map_err(|e| {
            Error::new(
                "PROCESS_SPAWN_FAILED",
                format!("could not start guarded tool: {e}"),
            )
        })?;
        let mut child = ChildScope {
            child,
            reaped: false,
        };
        let mut stdout = child
            .child
            .stdout
            .take()
            .ok_or_else(|| Error::new("PROCESS_PIPE_FAILED", "stdout pipe missing"))?;
        let mut stderr = child
            .child
            .stderr
            .take()
            .ok_or_else(|| Error::new("PROCESS_PIPE_FAILED", "stderr pipe missing"))?;
        nonblocking(stdout.as_raw_fd())?;
        nonblocking(stderr.as_raw_fd())?;

        let mut bytes_written = 0;
        let mut log_limit_exceeded = false;
        let mut timed_out = false;
        loop {
            drain(
                &mut stdout,
                &mut stdout_file,
                &mut bytes_written,
                log_cap,
                &mut log_limit_exceeded,
            )?;
            drain(
                &mut stderr,
                &mut stderr_file,
                &mut bytes_written,
                log_cap,
                &mut log_limit_exceeded,
            )?;
            if log_limit_exceeded || CANCELLATION.load(Ordering::Relaxed) != 0 {
                break;
            }
            if child.exited()? {
                break;
            }
            if started.elapsed() >= deadline_duration {
                timed_out = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        // Keep the leader unreaped until after killpg: its PID pins ownership of
        // the process group and cannot be reused for an unrelated command.
        child.kill_group()?;
        let (status, peak_rss_kib) = child.reap()?;
        // All group members have received SIGKILL. Drain the finite kernel pipe
        // buffers; never wait indefinitely for a kernel-blocked writer's EOF.
        for _ in 0..8 {
            let before = bytes_written;
            drain(
                &mut stdout,
                &mut stdout_file,
                &mut bytes_written,
                log_cap,
                &mut log_limit_exceeded,
            )?;
            drain(
                &mut stderr,
                &mut stderr_file,
                &mut bytes_written,
                log_cap,
                &mut log_limit_exceeded,
            )?;
            if before == bytes_written || log_limit_exceeded {
                break;
            }
        }
        stdout_file
            .sync_data()
            .map_err(|e| io_error("persist stdout log", e))?;
        stderr_file
            .sync_data()
            .map_err(|e| io_error("persist stderr log", e))?;
        Ok(ProcessReceipt {
            program: program_text,
            arguments: args.to_vec(),
            elapsed_seconds: started.elapsed().as_secs_f64(),
            peak_rss_kib,
            exit_code: if CANCELLATION.load(Ordering::Relaxed) == 0 && libc::WIFEXITED(status) {
                Some(libc::WEXITSTATUS(status))
            } else {
                None
            },
            timed_out,
            log_limit_exceeded,
            stdout: stdout_path,
            stderr: stderr_path,
        })
    }

    fn new_log(path: &Path) -> Result<File> {
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(|e| io_error("create tool log", e))
    }

    fn nonblocking(fd: RawFd) -> Result<()> {
        let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
        if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
            Err(io_error(
                "set log pipe nonblocking",
                io::Error::last_os_error(),
            ))
        } else {
            Ok(())
        }
    }

    fn drain(
        input: &mut impl Read,
        output: &mut File,
        written: &mut u64,
        limit: u64,
        exceeded: &mut bool,
    ) -> Result<()> {
        let mut buffer = [0_u8; READ_CHUNK];
        for _ in 0..DRAIN_CHUNKS {
            match input.read(&mut buffer) {
                Ok(0) => break,
                Ok(n) => {
                    let retained = (n as u64).min(limit - *written) as usize;
                    output
                        .write_all(&buffer[..retained])
                        .map_err(|e| io_error("write bounded tool log", e))?;
                    *written += retained as u64;
                    if retained < n {
                        *exceeded = true;
                        break;
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(io_error("read tool log pipe", e)),
            }
        }
        Ok(())
    }

    struct ChildScope {
        child: Child,
        reaped: bool,
    }

    impl ChildScope {
        fn kill_group(&self) -> Result<()> {
            if unsafe { libc::kill(-(self.child.id() as libc::pid_t), libc::SIGKILL) } != 0 {
                let error = io::Error::last_os_error();
                if error.raw_os_error() != Some(libc::ESRCH) {
                    return Err(io_error("kill tool process group", error));
                }
            }
            Ok(())
        }

        fn exited(&mut self) -> Result<bool> {
            let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
            let result = unsafe {
                libc::waitid(
                    libc::P_PID,
                    self.child.id(),
                    &mut info,
                    libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
                )
            };
            if result != 0 {
                let error = io::Error::last_os_error();
                if error.kind() == io::ErrorKind::Interrupted {
                    return Ok(false);
                }
                if error.raw_os_error() == Some(libc::ECHILD) {
                    self.reaped = true;
                    return Err(Error::new(
                        "PROCESS_OWNERSHIP_LOST",
                        "tool child was reaped externally; process-group cleanup ownership cannot be confirmed",
                    ));
                }
                return Err(io_error("observe tool exit", error));
            }
            Ok(unsafe { info.si_pid() } != 0)
        }

        fn reap(&mut self) -> Result<(i32, Option<u64>)> {
            let cleanup_started = Instant::now();
            loop {
                let mut status = 0;
                let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
                let result = unsafe {
                    libc::wait4(
                        self.child.id() as libc::pid_t,
                        &mut status,
                        libc::WNOHANG,
                        &mut usage,
                    )
                };
                if result > 0 {
                    self.reaped = true;
                    return Ok((status, u64::try_from(usage.ru_maxrss).ok()));
                }
                if result < 0 {
                    let error = io::Error::last_os_error();
                    if error.raw_os_error() == Some(libc::ECHILD) {
                        self.reaped = true;
                    }
                    if error.kind() != io::ErrorKind::Interrupted {
                        return Err(io_error("reap tool process", error));
                    }
                }
                if cleanup_started.elapsed() >= Duration::from_secs(2) {
                    return Err(Error::new(
                        "PROCESS_CLEANUP_TIMEOUT",
                        "SIGKILL was sent but the kernel has not released the tool; cleanup cannot be confirmed",
                    ));
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        }
    }

    impl Drop for ChildScope {
        fn drop(&mut self) {
            if !self.reaped {
                let _ = self.kill_group();
                // Reap ordinary error/panic cleanup without an unbounded wait:
                // kernel I/O can remain uninterruptible even after SIGKILL.
                for _ in 0..20 {
                    let result = unsafe {
                        libc::waitpid(
                            self.child.id() as libc::pid_t,
                            std::ptr::null_mut(),
                            libc::WNOHANG,
                        )
                    };
                    if result != 0 {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(5));
                }
            }
        }
    }

    extern "C" fn cancel(signal: libc::c_int) {
        CANCELLATION.store(signal, Ordering::Relaxed);
    }

    struct SignalScope {
        interrupt: libc::sigaction,
        terminate: libc::sigaction,
    }

    impl SignalScope {
        fn install() -> Result<Self> {
            let mut child_action: libc::sigaction = unsafe { std::mem::zeroed() };
            if unsafe { libc::sigaction(libc::SIGCHLD, std::ptr::null(), &mut child_action) } != 0 {
                return Err(io_error(
                    "inspect child signal policy",
                    io::Error::last_os_error(),
                ));
            }
            if child_action.sa_sigaction == libc::SIG_IGN
                || child_action.sa_flags & libc::SA_NOCLDWAIT != 0
            {
                return Err(Error::new(
                    "PROCESS_SIGNAL_POLICY",
                    "SIGCHLD must retain waitable children for process-group ownership",
                ));
            }
            let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
            action.sa_sigaction = cancel as *const () as usize;
            unsafe {
                libc::sigemptyset(&mut action.sa_mask);
            }
            let mut old_interrupt = unsafe { std::mem::zeroed() };
            let mut old_terminate = unsafe { std::mem::zeroed() };
            CANCELLATION.store(0, Ordering::Relaxed);
            if unsafe { libc::sigaction(libc::SIGINT, &action, &mut old_interrupt) } != 0 {
                return Err(io_error(
                    "install interrupt handler",
                    io::Error::last_os_error(),
                ));
            }
            if unsafe { libc::sigaction(libc::SIGTERM, &action, &mut old_terminate) } != 0 {
                let error = io::Error::last_os_error();
                unsafe {
                    libc::sigaction(libc::SIGINT, &old_interrupt, std::ptr::null_mut());
                }
                return Err(io_error("install termination handler", error));
            }
            Ok(Self {
                interrupt: old_interrupt,
                terminate: old_terminate,
            })
        }
    }

    impl Drop for SignalScope {
        fn drop(&mut self) {
            unsafe {
                libc::sigaction(libc::SIGINT, &self.interrupt, std::ptr::null_mut());
                libc::sigaction(libc::SIGTERM, &self.terminate, std::ptr::null_mut());
            }
        }
    }

    fn containment_filter() -> Vec<libc::sock_filter> {
        fn stmt(code: u32, k: u32) -> libc::sock_filter {
            libc::sock_filter {
                code: code as u16,
                jt: 0,
                jf: 0,
                k,
            }
        }
        fn jump(code: u32, k: u32, jt: u8, jf: u8) -> libc::sock_filter {
            libc::sock_filter {
                code: code as u16,
                jt,
                jf,
                k,
            }
        }
        const ARCH: u32 = if cfg!(target_arch = "x86_64") {
            0xc000003e
        } else {
            0xc00000b7
        };
        const DENY: u32 = libc::SECCOMP_RET_ERRNO | libc::EPERM as u32;
        let mut filter = vec![
            stmt(libc::BPF_LD | libc::BPF_W | libc::BPF_ABS, 4),
            jump(libc::BPF_JMP | libc::BPF_JEQ | libc::BPF_K, ARCH, 1, 0),
            stmt(libc::BPF_RET | libc::BPF_K, libc::SECCOMP_RET_KILL_PROCESS),
            stmt(libc::BPF_LD | libc::BPF_W | libc::BPF_ABS, 0),
        ];
        if cfg!(target_arch = "x86_64") {
            // x32 uses the same audit architecture but different syscall IDs.
            filter.push(jump(
                libc::BPF_JMP | libc::BPF_JGE | libc::BPF_K,
                0x40000000,
                0,
                1,
            ));
            filter.push(stmt(
                libc::BPF_RET | libc::BPF_K,
                libc::SECCOMP_RET_KILL_PROCESS,
            ));
        }
        for syscall in [
            libc::SYS_setsid,
            libc::SYS_setpgid,
            libc::SYS_unshare,
            libc::SYS_setns,
        ] {
            filter.push(jump(
                libc::BPF_JMP | libc::BPF_JEQ | libc::BPF_K,
                syscall as u32,
                0,
                1,
            ));
            filter.push(stmt(libc::BPF_RET | libc::BPF_K, DENY));
        }
        // libc transparently falls back to clone for ordinary threads/processes.
        filter.push(jump(
            libc::BPF_JMP | libc::BPF_JEQ | libc::BPF_K,
            libc::SYS_clone3 as u32,
            0,
            1,
        ));
        filter.push(stmt(
            libc::BPF_RET | libc::BPF_K,
            libc::SECCOMP_RET_ERRNO | libc::ENOSYS as u32,
        ));
        filter.push(jump(
            libc::BPF_JMP | libc::BPF_JEQ | libc::BPF_K,
            libc::SYS_clone as u32,
            0,
            3,
        ));
        filter.push(stmt(libc::BPF_LD | libc::BPF_W | libc::BPF_ABS, 16));
        let namespaces = libc::CLONE_NEWCGROUP
            | libc::CLONE_NEWIPC
            | libc::CLONE_NEWNET
            | libc::CLONE_NEWNS
            | libc::CLONE_NEWPID
            | libc::CLONE_NEWTIME
            | libc::CLONE_NEWUSER
            | libc::CLONE_NEWUTS;
        filter.push(jump(
            libc::BPF_JMP | libc::BPF_JSET | libc::BPF_K,
            namespaces as u32,
            0,
            1,
        ));
        filter.push(stmt(libc::BPF_RET | libc::BPF_K, DENY));
        filter.push(stmt(libc::BPF_RET | libc::BPF_K, libc::SECCOMP_RET_ALLOW));
        filter
    }
}

#[cfg(all(test, target_os = "linux", target_arch = "x86_64"))]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    fn limits() -> ProcessLimits {
        ProcessLimits {
            timeout_secs: 2,
            memory_mib: 256,
            max_log_bytes: 4096,
        }
    }

    #[test]
    fn success_is_measured_and_arguments_are_literal() {
        let dir = tempfile::tempdir().unwrap();
        let args = vec![
            "%s".to_owned(),
            "$(touch injected); $HOME # quoted".to_owned(),
        ];
        let receipt = run(
            Path::new("/usr/bin/printf"),
            &args,
            dir.path(),
            &dir.path().join("logs"),
            &limits(),
        )
        .unwrap();
        assert!(receipt.success());
        assert_eq!(fs::read_to_string(&receipt.stdout).unwrap(), args[1]);
        assert!(!dir.path().join("injected").exists());
        assert!(receipt.peak_rss_kib.is_some());
        assert!(receipt.elapsed_seconds.is_finite());
    }

    #[test]
    fn nonzero_exit_is_a_receipt() {
        let dir = tempfile::tempdir().unwrap();
        let receipt = run(
            Path::new("/usr/bin/false"),
            &[],
            dir.path(),
            &dir.path().join("logs"),
            &limits(),
        )
        .unwrap();
        assert_eq!(receipt.exit_code, Some(1));
        assert!(!receipt.success());
    }

    #[test]
    fn timeout_kills_the_tool() {
        let dir = tempfile::tempdir().unwrap();
        let mut limits = limits();
        limits.timeout_secs = 1;
        let receipt = run(
            Path::new("/usr/bin/sleep"),
            &["30".to_owned()],
            dir.path(),
            &dir.path().join("logs"),
            &limits,
        )
        .unwrap();
        assert!(receipt.timed_out);
        assert!(!receipt.success());
        assert!(receipt.elapsed_seconds < 4.0);
    }

    #[test]
    fn stdout_is_bounded_before_the_tool_finishes() {
        let dir = tempfile::tempdir().unwrap();
        let receipt = run(
            Path::new("/usr/bin/yes"),
            &[],
            dir.path(),
            &dir.path().join("logs"),
            &limits(),
        )
        .unwrap();
        assert!(receipt.log_limit_exceeded);
        assert!(!receipt.success());
        assert_eq!(
            fs::metadata(receipt.stdout).unwrap().len(),
            limits().max_log_bytes
        );
        assert_eq!(fs::metadata(receipt.stderr).unwrap().len(), 0);
    }

    #[test]
    fn logs_are_never_overwritten() {
        let dir = tempfile::tempdir().unwrap();
        let log_dir = dir.path().join("logs");
        fs::create_dir(&log_dir).unwrap();
        fs::write(log_dir.join("stdout.log"), "keep").unwrap();
        let error = run(
            Path::new("/usr/bin/true"),
            &[],
            dir.path(),
            &log_dir,
            &limits(),
        )
        .unwrap_err();
        assert_eq!(error.code, "LOG_DIRECTORY_EXISTS");
        assert_eq!(
            fs::read_to_string(log_dir.join("stdout.log")).unwrap(),
            "keep"
        );
    }

    #[test]
    fn process_group_escape_is_denied() {
        let dir = tempfile::tempdir().unwrap();
        let receipt = run(
            Path::new("/usr/bin/setsid"),
            &["--wait".to_owned(), "/usr/bin/true".to_owned()],
            dir.path(),
            &dir.path().join("logs"),
            &limits(),
        )
        .unwrap();
        assert!(!receipt.success());
        assert!(!fs::read_to_string(receipt.stderr).unwrap().is_empty());
    }

    #[test]
    fn stdin_is_already_closed() {
        let dir = tempfile::tempdir().unwrap();
        let receipt = run(
            Path::new("/usr/bin/cat"),
            &[],
            dir.path(),
            &dir.path().join("logs"),
            &limits(),
        )
        .unwrap();
        assert!(receipt.success());
        assert_eq!(fs::metadata(receipt.stdout).unwrap().len(), 0);
    }

    #[test]
    fn executable_text_does_not_trigger_a_shell_fallback() {
        let dir = tempfile::tempdir().unwrap();
        let tool = dir.path().join("not-native");
        fs::write(&tool, "touch injected\n").unwrap();
        fs::set_permissions(&tool, fs::Permissions::from_mode(0o700)).unwrap();
        let error = run(&tool, &[], dir.path(), &dir.path().join("logs"), &limits()).unwrap_err();
        assert_eq!(error.code, "PROCESS_SPAWN_FAILED");
        assert!(!dir.path().join("injected").exists());
    }

    fn helper(name: &str, log_dir: &Path, limits: &ProcessLimits) -> ProcessReceipt {
        run(
            &std::env::current_exe().unwrap(),
            &helper_arguments(name),
            log_dir.parent().unwrap(),
            log_dir,
            limits,
        )
        .unwrap()
    }

    fn helper_arguments(name: &str) -> Vec<String> {
        vec![
            "--exact".to_owned(),
            format!("process::tests::{name}"),
            "--ignored".to_owned(),
            "--nocapture".to_owned(),
            "--test-threads=1".to_owned(),
        ]
    }

    #[test]
    fn sigterm_cancels_controller_and_restores_handlers_under_outer_limits() {
        let dir = tempfile::tempdir().unwrap();
        // This trusted controller fixture stays outside the tool's seccomp
        // guard so it can establish its own guarded child process group.
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args(helper_arguments("fixture_sigterm_controller"))
            .current_dir(dir.path())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0);
        unsafe {
            command.pre_exec(|| {
                let address = libc::rlimit {
                    rlim_cur: 1024 * 1024 * 1024,
                    rlim_max: 1024 * 1024 * 1024,
                };
                let files = libc::rlimit {
                    rlim_cur: 64 * 1024 * 1024,
                    rlim_max: 64 * 1024 * 1024,
                };
                let core = libc::rlimit {
                    rlim_cur: 0,
                    rlim_max: 0,
                };
                if libc::setrlimit(libc::RLIMIT_AS, &address) != 0
                    || libc::setrlimit(libc::RLIMIT_FSIZE, &files) != 0
                    || libc::setrlimit(libc::RLIMIT_CORE, &core) != 0
                    || libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0
                {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let started = Instant::now();
        let mut child = command.spawn().unwrap();
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break status,
                Ok(None) if started.elapsed() < Duration::from_secs(8) => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                other => {
                    // The leader remains unreaped here, so its PID still pins
                    // this fixture's group. Its tool also has PDEATHSIG.
                    unsafe {
                        libc::kill(-(child.id() as libc::pid_t), libc::SIGKILL);
                    }
                    let cleanup_started = Instant::now();
                    while child.try_wait().is_ok_and(|status| status.is_none())
                        && cleanup_started.elapsed() < Duration::from_secs(1)
                    {
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    panic!("SIGTERM controller fixture did not finish: {other:?}");
                }
            }
        };
        assert!(status.success(), "controller fixture failed: {status}");
        let receipt: ProcessReceipt =
            serde_json::from_slice(&fs::read(dir.path().join("cancel-receipt.json")).unwrap())
                .unwrap();
        assert!(!receipt.success());
        assert_eq!(receipt.exit_code, None);
        assert!(!receipt.timed_out);
        assert!(!receipt.log_limit_exceeded);
        assert!(receipt.elapsed_seconds < 3.0);
        let pid: u32 = fs::read_to_string(dir.path().join("cancel-target.pid"))
            .unwrap()
            .parse()
            .unwrap();
        assert_pid_stopped(pid);
    }

    #[test]
    fn seccomp_allows_native_rust_threads() {
        let dir = tempfile::tempdir().unwrap();
        let mut limits = limits();
        limits.memory_mib = 512;
        let receipt = helper("fixture_threaded", &dir.path().join("logs"), &limits);
        assert!(receipt.success(), "{receipt:?}");
        assert!(
            fs::read_to_string(receipt.stdout)
                .unwrap()
                .contains("thread-result=2000")
        );
    }

    #[test]
    fn timeout_kills_descendants_that_try_to_escape() {
        let dir = tempfile::tempdir().unwrap();
        let mut limits = limits();
        limits.timeout_secs = 1;
        limits.memory_mib = 512;
        let receipt = helper(
            "fixture_descendant_timeout",
            &dir.path().join("logs"),
            &limits,
        );
        assert!(receipt.timed_out);
        assert_descendant_stopped(&receipt);
        assert!(
            fs::read_to_string(receipt.stdout)
                .unwrap()
                .contains("escape-denied")
        );
    }

    #[test]
    fn successful_leader_cannot_leave_a_daemon() {
        let dir = tempfile::tempdir().unwrap();
        let mut limits = limits();
        limits.memory_mib = 512;
        let receipt = helper(
            "fixture_descendant_early_exit",
            &dir.path().join("logs"),
            &limits,
        );
        assert!(receipt.success(), "{receipt:?}");
        assert_descendant_stopped(&receipt);
    }

    fn assert_descendant_stopped(receipt: &ProcessReceipt) {
        let output = fs::read_to_string(&receipt.stdout).unwrap();
        let pid = output
            .lines()
            .find_map(|line| line.split_once("descendant=").map(|(_, pid)| pid))
            .unwrap();
        let pid: u32 = pid.parse().unwrap();
        assert_pid_stopped(pid);
    }

    fn assert_pid_stopped(pid: u32) {
        let started = Instant::now();
        loop {
            // A zombie is already terminated and awaits its new parent's reap.
            // This supervisor does not claim ownership of orphan reaping.
            let state = fs::read_to_string(format!("/proc/{pid}/stat"))
                .ok()
                .and_then(|text| {
                    text.rsplit_once(") ")
                        .map(|(_, fields)| fields.chars().next().unwrap())
                });
            if state.is_none() || matches!(state, Some('Z' | 'X')) {
                break;
            }
            assert!(
                started.elapsed() < Duration::from_secs(1),
                "descendant {pid} remains running: {state:?}"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    #[ignore = "fixture executed explicitly inside a guarded child"]
    fn fixture_threaded() {
        let threads: Vec<_> = (0..2)
            .map(|_| std::thread::spawn(|| (0..1000).map(|_| 1).sum::<u32>()))
            .collect();
        let total: u32 = threads.into_iter().map(|t| t.join().unwrap()).sum();
        assert_eq!(total, 2000);
        println!("thread-result={total}");
    }

    fn fork_descendant() {
        let pid = unsafe { libc::fork() };
        assert!(pid >= 0, "fork fixture failed");
        if pid == 0 {
            // The post-fork child uses only async-signal-safe system calls.
            let message: &[u8] = if unsafe { libc::setsid() } < 0 {
                b"escape-denied\n"
            } else {
                b"escape-allowed\n"
            };
            unsafe {
                libc::write(libc::STDOUT_FILENO, message.as_ptr().cast(), message.len());
            }
            loop {
                unsafe {
                    libc::pause();
                }
            }
        }
        println!("descendant={pid}");
        std::io::stdout().flush().unwrap();
    }

    #[test]
    #[ignore = "fixture executed explicitly inside a guarded child"]
    fn fixture_descendant_timeout() {
        fork_descendant();
        std::thread::sleep(Duration::from_secs(30));
    }

    #[test]
    #[ignore = "fixture executed explicitly inside a guarded child"]
    fn fixture_descendant_early_exit() {
        fork_descendant();
    }

    #[test]
    #[ignore = "fixture executed explicitly inside a guarded controller"]
    fn fixture_sigterm_target() {
        fs::write("cancel-target.pid", std::process::id().to_string()).unwrap();
        println!("signal-target-ready");
        std::io::stdout().flush().unwrap();
        std::thread::sleep(Duration::from_secs(30));
    }

    #[test]
    #[ignore = "fixture executed explicitly under outer OS resource guards"]
    fn fixture_sigterm_controller() {
        let directory = std::env::current_dir().unwrap();
        let log_dir = directory.join("controlled-logs");
        let stdout = log_dir.join("stdout.log");
        let handler = |signal| {
            let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
            assert_eq!(
                unsafe { libc::sigaction(signal, std::ptr::null(), &mut action) },
                0
            );
            action.sa_sigaction
        };
        let prior_interrupt = handler(libc::SIGINT);
        let prior_terminate = handler(libc::SIGTERM);
        let sender = std::thread::spawn(move || {
            let started = Instant::now();
            loop {
                if fs::read_to_string(&stdout)
                    .is_ok_and(|text| text.contains("signal-target-ready"))
                {
                    assert_eq!(unsafe { libc::kill(libc::getpid(), libc::SIGTERM) }, 0);
                    break;
                }
                assert!(
                    started.elapsed() < Duration::from_secs(5),
                    "signal target never started"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
        });
        let receipt = helper(
            "fixture_sigterm_target",
            &log_dir,
            &ProcessLimits {
                timeout_secs: 10,
                memory_mib: 512,
                max_log_bytes: 4096,
            },
        );
        sender.join().unwrap();
        assert_eq!(handler(libc::SIGINT), prior_interrupt);
        assert_eq!(handler(libc::SIGTERM), prior_terminate);
        fs::write(
            directory.join("cancel-receipt.json"),
            serde_json::to_vec(&receipt).unwrap(),
        )
        .unwrap();
    }
}

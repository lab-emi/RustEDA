// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Linux process primitives of the step-graph executor.
//!
//! Every step runs in its own session, with standard input closed, on a fixed CPU set
//! when its group assigns one, with the environment in the order the executor received
//! it, and with every inherited descriptor above standard error closed at `execve`.
//! This is worker-side launching: unlike the native supervisor of `process.rs` it
//! installs no system-call filter, because recipe steps start sessions of their own.
//! Containment of a whole run is the owning controller's control group.

use crate::error::{Error, Result};
use std::{
    ffi::{CString, OsString},
    fs::File,
    io,
    os::{
        fd::{AsRawFd, FromRawFd, OwnedFd, RawFd},
        unix::{ffi::OsStrExt, process::CommandExt},
    },
    path::Path,
    process::{Command, Stdio},
    sync::atomic::{AtomicI32, Ordering},
    time::Duration,
};

static CANCELLATION: AtomicI32 = AtomicI32::new(0);
const CLOSE_RANGE_CLOEXEC: libc::c_uint = 1 << 2;

fn os_error(action: &str) -> Error {
    Error::new(
        "IO_ERROR",
        format!("{action}: {}", io::Error::last_os_error()),
    )
}

extern "C" fn record_signal(signal: libc::c_int) {
    CANCELLATION.store(signal, Ordering::Relaxed);
}

/// Record SIGINT and SIGTERM instead of dying, so that the run can stop its steps and
/// write its receipts. Installed by the command-line entry only.
pub fn install_cancellation() -> Result<()> {
    for signal in [libc::SIGINT, libc::SIGTERM] {
        let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
        action.sa_sigaction = record_signal as extern "C" fn(libc::c_int) as usize;
        // SAFETY: the handler only stores to an atomic; the action is fully initialised.
        if unsafe {
            libc::sigemptyset(&mut action.sa_mask);
            libc::sigaction(signal, &action, std::ptr::null_mut())
        } != 0
        {
            return Err(os_error("install cancellation handler"));
        }
    }
    Ok(())
}

/// The signal that asked this process to stop, if any.
pub fn cancellation() -> Option<i32> {
    Some(CANCELLATION.load(Ordering::Relaxed)).filter(|signal| *signal != 0)
}

/// CPUs the calling thread may run on.
pub fn affinity() -> Result<Vec<usize>> {
    // SAFETY: the set is a plain bit array owned by this frame.
    let mut set: libc::cpu_set_t = unsafe { std::mem::zeroed() };
    if unsafe { libc::sched_getaffinity(0, size_of::<libc::cpu_set_t>(), &mut set) } != 0 {
        return Err(os_error("read CPU affinity"));
    }
    Ok((0..libc::CPU_SETSIZE as usize)
        .filter(|cpu| unsafe { libc::CPU_ISSET(*cpu, &set) })
        .collect())
}

fn cpu_set(cpus: &[usize]) -> Result<libc::cpu_set_t> {
    // SAFETY: as above; indices are checked against the set size first.
    let mut set: libc::cpu_set_t = unsafe { std::mem::zeroed() };
    for &cpu in cpus {
        if cpu >= libc::CPU_SETSIZE as usize {
            return Err(Error::new(
                "CPU_ALLOCATION_INVALID",
                "CPU index is outside the affinity mask",
            ));
        }
        unsafe { libc::CPU_SET(cpu, &mut set) };
    }
    Ok(set)
}

/// Restrict the calling thread (and every thread and child it starts later).
pub fn set_affinity(cpus: &[usize]) -> Result<()> {
    let set = cpu_set(cpus)?;
    // SAFETY: the set is initialised and its size is passed.
    if unsafe { libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set) } != 0 {
        return Err(os_error("set CPU affinity"));
    }
    Ok(())
}

/// Largest resident set of this process and of the children it has reaped, in KiB.
pub fn peak_rss_kib() -> u64 {
    let peak = |who| {
        // SAFETY: getrusage fills the zeroed structure.
        let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
        if unsafe { libc::getrusage(who, &mut usage) } == 0 {
            usage.ru_maxrss.max(0) as u64
        } else {
            0
        }
    };
    peak(libc::RUSAGE_SELF).max(peak(libc::RUSAGE_CHILDREN))
}

/// User and system CPU seconds of this process itself (not of its children).
pub fn self_cpu_seconds() -> (f64, f64) {
    // SAFETY: getrusage fills the zeroed structure.
    let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut usage) };
    (seconds(usage.ru_utime), seconds(usage.ru_stime))
}

fn seconds(time: libc::timeval) -> f64 {
    time.tv_sec as f64 + time.tv_usec as f64 / 1e6
}

/// Where a step's standard stream goes.
#[derive(Debug)]
pub enum Sink {
    File(File),
    Pipe,
}

#[derive(Debug)]
pub struct Launch<'a> {
    /// Program path followed by its arguments.
    pub argv: &'a [String],
    pub cwd: &'a Path,
    pub environment: &'a [(OsString, OsString)],
    pub stdout: Sink,
    pub stderr: Sink,
    /// CPUs for the step and everything it starts; `None` inherits the executor's.
    pub affinity: Option<&'a [usize]>,
}

/// What a reaped step reported.
#[derive(Debug, Clone, Copy)]
pub struct Exit {
    /// Exit status, or the negated signal number that ended the step.
    pub code: i32,
    /// Largest resident set among the step's reaped processes, in KiB.
    pub max_rss_kib: u64,
    pub user_seconds: f64,
    pub system_seconds: f64,
}

/// A running step: leader of its own session and process group.
#[derive(Debug)]
pub struct Child {
    pub pid: i32,
    /// Readable once the leader has exited; absent on kernels without pidfds.
    waiter: Option<OwnedFd>,
    pub stdout: Option<File>,
    pub stderr: Option<File>,
    reaped: bool,
}

pub fn spawn(launch: Launch<'_>) -> Result<Child> {
    let invalid =
        |what: &str| Error::new("STEP_SPAWN_FAILED", format!("{what} contains a NUL byte"));
    let program = launch
        .argv
        .first()
        .ok_or_else(|| Error::new("STEP_SPAWN_FAILED", "empty command"))?;
    let argv: Vec<CString> = launch
        .argv
        .iter()
        .map(|arg| CString::new(arg.as_bytes()).map_err(|_| invalid("an argument")))
        .collect::<Result<_>>()?;
    let environment: Vec<CString> = launch
        .environment
        .iter()
        .map(|(name, value)| {
            let mut entry = name.as_bytes().to_vec();
            entry.push(b'=');
            entry.extend_from_slice(value.as_bytes());
            CString::new(entry).map_err(|_| invalid("the environment"))
        })
        .collect::<Result<_>>()?;
    let mut argv_pointers: Vec<*const libc::c_char> = argv.iter().map(|arg| arg.as_ptr()).collect();
    argv_pointers.push(std::ptr::null());
    let mut env_pointers: Vec<*const libc::c_char> =
        environment.iter().map(|entry| entry.as_ptr()).collect();
    env_pointers.push(std::ptr::null());
    let set = launch.affinity.map(cpu_set).transpose()?;
    let mut limit: libc::rlimit = unsafe { std::mem::zeroed() };
    // SAFETY: getrlimit fills the structure; a failure leaves the conservative default below.
    let descriptors = if unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) } == 0 {
        limit.rlim_cur.min(65536) as libc::c_int
    } else {
        1024
    };
    let stdio = |sink: Sink| match sink {
        Sink::File(file) => Stdio::from(file),
        Sink::Pipe => Stdio::piped(),
    };
    let mut command = Command::new(program);
    command
        .current_dir(launch.cwd)
        .stdin(Stdio::null())
        .stdout(stdio(launch.stdout))
        .stderr(stdio(launch.stderr));
    // Raw pointers are not Send; the vectors that own the strings live until spawn returns.
    let argv_address = argv_pointers.as_ptr() as usize;
    let env_address = env_pointers.as_ptr() as usize;
    // SAFETY: between fork and exec only async-signal-safe system calls run; every
    // allocation happened above. `execve` replaces the image, so the closure returns
    // only with its error.
    unsafe {
        command.pre_exec(move || {
            if libc::setsid() < 0 {
                return Err(io::Error::last_os_error());
            }
            if let Some(set) = &set
                && libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), set) != 0
            {
                return Err(io::Error::last_os_error());
            }
            // Descriptors the executor inherited (a caller's lock or log) must not reach
            // the tools. Marking instead of closing keeps the spawn error channel alive.
            if libc::syscall(
                libc::SYS_close_range,
                3 as libc::c_uint,
                libc::c_uint::MAX,
                CLOSE_RANGE_CLOEXEC,
            ) != 0
            {
                for descriptor in 3..descriptors {
                    let flags = libc::fcntl(descriptor, libc::F_GETFD);
                    if flags >= 0 {
                        libc::fcntl(descriptor, libc::F_SETFD, flags | libc::FD_CLOEXEC);
                    }
                }
            }
            let argv = argv_address as *const *const libc::c_char;
            libc::execve(*argv, argv, env_address as *const *const libc::c_char);
            Err(io::Error::last_os_error())
        });
    }
    let mut child = command.spawn().map_err(|error| {
        Error::new(
            "STEP_SPAWN_FAILED",
            format!("could not start {program}: {error}"),
        )
    })?;
    drop((argv_pointers, env_pointers));
    let pid = child.id() as i32;
    // SAFETY: plain system call; a valid descriptor is owned from here on.
    let descriptor = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) };
    let waiter = (descriptor >= 0).then(|| unsafe { OwnedFd::from_raw_fd(descriptor as RawFd) });
    let pipe = |stream: Option<OwnedFd>| -> Result<Option<File>> {
        let Some(stream) = stream else {
            return Ok(None);
        };
        // SAFETY: fcntl on a descriptor this process owns.
        let flags = unsafe { libc::fcntl(stream.as_raw_fd(), libc::F_GETFL) };
        if flags < 0
            || unsafe { libc::fcntl(stream.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) }
                < 0
        {
            return Err(os_error("set step pipe nonblocking"));
        }
        Ok(Some(File::from(stream)))
    };
    let stdout = pipe(child.stdout.take().map(OwnedFd::from));
    let stderr = pipe(child.stderr.take().map(OwnedFd::from));
    // The standard handle is not used for waiting: `wait4` below also returns resource use.
    drop(child);
    let mut child = Child {
        pid,
        waiter,
        stdout: None,
        stderr: None,
        reaped: false,
    };
    match (stdout, stderr) {
        (Ok(stdout), Ok(stderr)) => {
            child.stdout = stdout;
            child.stderr = stderr;
            Ok(child)
        }
        (Err(error), _) | (_, Err(error)) => {
            child.kill_group();
            let _ = child.reap(Duration::from_secs(10));
            Err(error)
        }
    }
}

impl Child {
    pub fn waiter(&self) -> Option<RawFd> {
        self.waiter.as_ref().map(AsRawFd::as_raw_fd)
    }

    /// True once the leader has exited. It stays unreaped, so its process-group identifier
    /// cannot be reused before [`Child::kill_group`] and [`Child::reap`].
    pub fn exited(&self) -> bool {
        if self.reaped {
            return true;
        }
        // SAFETY: waitid fills the zeroed structure and, with WNOWAIT, changes no state.
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        let result = unsafe {
            libc::waitid(
                libc::P_PID,
                self.pid as libc::id_t,
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        // An error means the child is no longer ours to wait for; `reap` reports it.
        result != 0 || unsafe { info.si_pid() } != 0
    }

    /// SIGKILL every process still in the step's process group.
    pub fn kill_group(&self) {
        if !self.reaped {
            // SAFETY: the unreaped leader pins the group identifier to this step.
            unsafe { libc::killpg(self.pid, libc::SIGKILL) };
        }
    }

    /// Reap the leader, waiting at most `limit` for it to exit.
    pub fn reap(&mut self, limit: Duration) -> Result<Exit> {
        let deadline = std::time::Instant::now() + limit;
        while !self.exited() {
            let left = deadline.saturating_duration_since(std::time::Instant::now());
            if left.is_zero() {
                return Err(Error::new(
                    "PROCESS_CLEANUP_TIMEOUT",
                    format!("step process {} did not exit after SIGKILL", self.pid),
                ));
            }
            wait(
                &self.waiter().into_iter().collect::<Vec<_>>(),
                left.min(Duration::from_millis(50)),
            );
        }
        let mut status = 0;
        // SAFETY: wait4 fills both out-parameters for our own unreaped child.
        let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
        let pid = loop {
            let pid = unsafe { libc::wait4(self.pid, &mut status, 0, &mut usage) };
            if pid >= 0 || io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
                break pid;
            }
        };
        self.reaped = true;
        if pid != self.pid {
            return Err(Error::new(
                "PROCESS_OWNERSHIP_LOST",
                format!("step process {} was reaped elsewhere", self.pid),
            ));
        }
        Ok(Exit {
            code: if libc::WIFEXITED(status) {
                libc::WEXITSTATUS(status)
            } else {
                -libc::WTERMSIG(status)
            },
            max_rss_kib: usage.ru_maxrss.max(0) as u64,
            user_seconds: seconds(usage.ru_utime),
            system_seconds: seconds(usage.ru_stime),
        })
    }
}

impl Drop for Child {
    fn drop(&mut self) {
        // A step must never outlive the run that owns it, whatever path dropped it.
        if !self.reaped {
            self.kill_group();
            let _ = self.reap(Duration::from_secs(10));
        }
    }
}

/// Sleep until one descriptor is readable, a signal arrives or the time is over.
/// Returns the readable descriptors.
pub fn wait(descriptors: &[RawFd], limit: Duration) -> Vec<RawFd> {
    let mut entries: Vec<libc::pollfd> = descriptors
        .iter()
        .map(|&fd| libc::pollfd {
            fd,
            events: libc::POLLIN,
            revents: 0,
        })
        .collect();
    let milliseconds = limit.as_millis().clamp(1, 60_000) as libc::c_int;
    // SAFETY: the slice is valid for its length for the duration of the call.
    let ready = unsafe {
        libc::poll(
            entries.as_mut_ptr(),
            entries.len() as libc::nfds_t,
            milliseconds,
        )
    };
    if ready <= 0 {
        return Vec::new();
    }
    entries
        .iter()
        .filter(|entry| entry.revents != 0)
        .map(|entry| entry.fd)
        .collect()
}

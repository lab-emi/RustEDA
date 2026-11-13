// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Execution of a validated step graph: serial steps, parallel groups, receipts, summary.
//!
//! One foreground coordinator thread launches and reaps every step; at most 16 steps
//! of a group are alive at once. Receipts are kept in manifest order and published
//! after every step. Logs are hashed with a 1 MiB buffer; stdout artifacts of serial
//! steps are streamed through two 64 KiB buffers. Coordinator memory is O(steps).

use super::{
    Options, Outcome,
    events::Events,
    identity::{self, Identity, utf8},
    manifest::{self, ArtifactSpec, Kind, Manifest, Step},
    pyjson::Json,
    query::{load_manifest, pinned_tool, verify_pins},
    sys::{self, Exit, Launch, Sink},
    template::{Context, resolve_lenient},
};
use crate::error::{Error, Result};
use fs2::FileExt;
use serde_json::json;
use std::{
    collections::BTreeSet,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

const CAPTURE_CHUNK: usize = 64 << 10;
/// Bytes a failed capture may still drain from its pipes after the group was killed.
const DRAIN_BYTES: u64 = 1 << 20;
pub(super) const REAP_LIMIT: Duration = Duration::from_secs(10);
/// Budget tick of a serial step and of a parallel group; exits wake the wait at once.
const SERIAL_TICK: Duration = Duration::from_millis(200);
pub(super) const GROUP_TICK: Duration = Duration::from_millis(50);
const UNVERIFIED_PREFIX: [&str; 10] = [
    "complete_flow",
    "routing",
    "extraction",
    "gds",
    "drc",
    "antenna",
    "lvs",
    "extracted_timing",
    "timing_accuracy",
    "benchmark",
];

/// Why a run stopped after it had started; the message is the summary's `error` text.
#[derive(Debug)]
pub(super) struct Failure {
    pub(super) code: &'static str,
    pub(super) message: String,
    pub(super) step: Option<String>,
}

impl Failure {
    pub(super) fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            step: None,
        }
    }

    pub(super) fn at(mut self, step: &str) -> Self {
        self.step.get_or_insert_with(|| step.to_owned());
        self
    }
}

impl From<Error> for Failure {
    fn from(error: Error) -> Self {
        Self::new(error.code, error.message)
    }
}

impl From<std::io::Error> for Failure {
    fn from(error: std::io::Error) -> Self {
        Self::new("IO_ERROR", error.to_string())
    }
}

pub(super) type Attempt<T> = std::result::Result<T, Failure>;

/// Write a JSON document in the reference text form through `<path>.tmp`, which must not
/// exist, and rename it into place. Only `durable` documents are synced: a host crash ends
/// the run, and an interrupted run is never resumed from its intermediate files.
pub(super) fn write_json(path: &Path, value: &Json, durable: bool) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut name = path.as_os_str().to_owned();
    name.push(".tmp");
    let temporary = PathBuf::from(name);
    let text = value.to_pretty()?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    file.write_all(text.as_bytes())?;
    if durable {
        file.sync_all()?;
    }
    drop(file);
    fs::rename(&temporary, path)?;
    Ok(())
}

pub(super) fn new_log(path: &Path) -> Attempt<File> {
    Ok(OpenOptions::new().write(true).create_new(true).open(path)?)
}

pub(super) fn size(path: &Path) -> u64 {
    fs::metadata(path).map_or(0, |meta| meta.len())
}

/// Set or append an environment variable, keeping the position of an existing one.
pub(super) fn set_variable(
    environment: &mut Vec<(OsString, OsString)>,
    name: &str,
    value: impl Into<OsString>,
) {
    match environment.iter_mut().find(|(key, _)| key == name) {
        Some(entry) => entry.1 = value.into(),
        None => environment.push((name.into(), value.into())),
    }
}

/// Peak memory of the control group this process runs in: cgroup v2 `memory.peak` and
/// `memory.swap.peak`, or the cgroup v1 memory controller's maxima.
fn cgroup_resources() -> Json {
    let read =
        |path: PathBuf| -> Option<u64> { fs::read_to_string(path).ok()?.trim().parse().ok() };
    let mut memory = None;
    let mut swap = None;
    if let Ok(table) = fs::read_to_string("/proc/self/cgroup") {
        if let Some(entry) = table.lines().find_map(|line| line.strip_prefix("0::")) {
            let location = Path::new("/sys/fs/cgroup").join(entry.trim_start_matches('/'));
            memory = read(location.join("memory.peak"));
            swap = read(location.join("memory.swap.peak"));
        }
        if memory.is_none() && swap.is_none() {
            let entry = table.lines().find_map(|line| {
                let mut fields = line.splitn(3, ':');
                let (_, controllers, path) = (fields.next()?, fields.next()?, fields.next()?);
                controllers
                    .split(',')
                    .any(|name| name == "memory")
                    .then_some(path)
            });
            if let Some(entry) = entry {
                let location =
                    Path::new("/sys/fs/cgroup/memory").join(entry.trim_start_matches('/'));
                memory = read(location.join("memory.max_usage_in_bytes"));
                let total = read(location.join("memory.memsw.max_usage_in_bytes"));
                swap = memory
                    .zip(total)
                    .map(|(memory, total)| total.saturating_sub(memory));
            }
        }
    }
    let number = |value: Option<u64>| value.map_or(Json::Null, Json::int);
    Json::object([
        ("memory_peak_bytes", number(memory)),
        ("swap_peak_bytes", number(swap)),
    ])
}

pub(super) struct Logs {
    pub(super) command: PathBuf,
    pub(super) time: PathBuf,
    pub(super) stdout: PathBuf,
    pub(super) stderr: PathBuf,
}

impl Logs {
    pub(super) fn new(output: &Path, tag: &str) -> Self {
        let file = |suffix: &str| output.join("logs").join(format!("{tag}.{suffix}"));
        Self {
            command: file("command.json"),
            time: file("time"),
            stdout: file("stdout"),
            stderr: file("stderr"),
        }
    }
}

pub(super) struct Run<'a> {
    pub(super) manifest: &'a Manifest,
    pub(super) context: Context,
    pub(super) started: Instant,
    pub(super) gnu_time: String,
    pub(super) environment: Vec<(OsString, OsString)>,
    pub(super) receipts: Vec<Json>,
    pub(super) log_bytes: u64,
    pub(super) artifact_bytes: u64,
    pub(super) events: Events,
}

impl Run<'_> {
    fn receipts_path(&self) -> PathBuf {
        self.context.output.join("step-receipts.json")
    }

    pub(super) fn publish(&self, group: &[Json], durable: bool) -> Result<()> {
        let all = self.receipts.iter().chain(group).cloned().collect();
        write_json(&self.receipts_path(), &Json::Array(all), durable)
    }

    pub(super) fn artifact(&self, command: &manifest::Command) -> Option<ArtifactSpec> {
        let handle = command.stdout_artifact.as_ref()?;
        self.manifest
            .stdout_artifacts
            .as_ref()?
            .get(handle)
            .cloned()
    }

    /// The pinned tool path followed by the expanded arguments, under the resource recorder.
    pub(super) fn argv(&self, command: &manifest::Command, logs: &Logs) -> Attempt<Vec<String>> {
        let tool = self
            .context
            .record(&format!("tools:{}", command.tool))
            .ok_or_else(|| {
                Failure::new("STEP_INVALID", format!("unpinned tool: {}", command.tool))
            })?;
        let mut argv = vec![
            self.gnu_time.clone(),
            "-v".into(),
            "-o".into(),
            utf8(&logs.time)?,
            tool.path.clone(),
        ];
        for argument in &command.arguments {
            argv.push(self.context.expand(argument)?);
        }
        Ok(argv)
    }

    /// Instantiate a template into the output directory and return the step's receipt.
    pub(super) fn render(
        &mut self,
        step: &Step,
        destination: &str,
        content: &Json,
    ) -> Attempt<Json> {
        let tick = Instant::now();
        let written = self.context.output_path(destination).and_then(|path| {
            write_json(&path, &self.context.render(content)?, false)?;
            Ok(path)
        });
        let wall = tick.elapsed().as_secs_f64();
        match written {
            Ok(path) => {
                self.events.emit(
                    "step_finished",
                    json!({"id": step.id, "kind": "render", "wall_seconds": wall, "destination": path}),
                );
                Ok(Json::object([
                    ("id", Json::str(step.id.clone())),
                    ("kind", Json::str("render")),
                    ("wall_seconds", Json::Float(wall)),
                ]))
            }
            Err(error) => {
                self.events.emit(
                    "step_failed",
                    json!({"id": step.id, "kind": "render", "wall_seconds": wall,
                        "error": {"code": error.code, "message": error.message}}),
                );
                Err(error.into())
            }
        }
    }

    pub(super) fn started_event(&mut self, step: &Step, pid: i32, job: Option<(&[usize], u64)>) {
        let mut fields = json!({"id": step.id, "kind": "command", "pid": pid});
        if let Some((cpus, memory)) = job {
            fields["cpu_affinity"] = json!(cpus);
            fields["memory_reservation_mib"] = json!(memory);
        }
        self.events.emit("step_started", fields);
    }

    pub(super) fn finished_event(
        &mut self,
        step: &Step,
        exit: Option<Exit>,
        wall: f64,
        streams: (&Identity, &Identity),
        failure: Option<(&'static str, &str)>,
    ) {
        let mut fields = json!({"id": step.id, "kind": "command", "wall_seconds": wall,
            "exit_code": exit.map(|exit| exit.code),
            "max_rss_kib": exit.map(|exit| exit.max_rss_kib),
            "user_seconds": exit.map(|exit| exit.user_seconds),
            "system_seconds": exit.map(|exit| exit.system_seconds),
            "stdout_bytes": streams.0.bytes, "stderr_bytes": streams.1.bytes});
        match failure {
            Some((code, message)) => {
                fields["error"] = json!({"code": code, "message": message});
                self.events.emit("step_failed", fields);
            }
            None => self.events.emit("step_finished", fields),
        }
    }

    /// Run one step outside a parallel group.
    fn serial(&mut self, step: &Step) -> Attempt<()> {
        let command = match &step.kind {
            Kind::Render {
                destination,
                content,
            } => {
                let receipt = self.render(step, destination, content)?;
                self.receipts.push(receipt);
                self.publish(&[], false)?;
                return Ok(());
            }
            Kind::Command(command) => command,
        };
        let tick = Instant::now();
        let tag = step.id.as_str();
        let logs = Logs::new(&self.context.output, tag);
        let argv = self.argv(command, &logs)?;
        let record = Json::object([
            (
                "argv",
                Json::Array(argv.iter().cloned().map(Json::Str).collect()),
            ),
            ("cwd", Json::str(self.context.output_text.clone())),
        ]);
        write_json(&logs.command, &record, false)?;
        let artifact = self.artifact(command);
        let outcome = match &artifact {
            Some(spec) => self.capture(step, &argv, &logs, tick, command.timeout_s, spec.max_bytes),
            None => self.plain(step, &argv, &logs, tick, command.timeout_s),
        };
        let (stdout, stderr) = match (
            identity::identity(&logs.stdout),
            identity::identity(&logs.stderr),
        ) {
            (Ok(stdout), Ok(stderr)) => (stdout, stderr),
            // Without both streams there is nothing to record; the step's own failure,
            // when there is one, is the reason to report.
            (stdout, stderr) => {
                let unreadable = stdout.and(stderr).err().map(Failure::from);
                return Err(outcome
                    .err()
                    .or(unreadable)
                    .unwrap_or_else(|| Failure::new("IO_ERROR", "step streams are unreadable"))
                    .at(tag));
            }
        };
        self.log_bytes += stderr.bytes + if artifact.is_none() { stdout.bytes } else { 0 };
        self.artifact_bytes += if artifact.is_some() { stdout.bytes } else { 0 };
        let wall = tick.elapsed().as_secs_f64();
        let exit = outcome.as_ref().ok().copied();
        let mut receipt = Json::object([
            ("id", Json::str(tag)),
            ("kind", Json::str("command")),
            ("wall_seconds", Json::Float(wall)),
            (
                "exit_code",
                exit.map_or(Json::Null, |exit| Json::int(exit.code)),
            ),
            ("stdout", stdout.json()),
            ("stderr", stderr.json()),
        ]);
        if let (Some(spec), Some(handle)) = (&artifact, &command.stdout_artifact) {
            receipt.set("stdout_artifact", declared(handle, spec));
        }
        if let Err(failure) = &outcome {
            receipt.set("capture_error", Json::str(failure.message.clone()));
        }
        self.receipts.push(receipt);
        self.publish(&[], false)?;
        let verdict = match &outcome {
            Err(failure) => Some(Failure::new(failure.code, failure.message.clone())),
            Ok(_) if self.log_bytes > self.manifest.scope.max_log_bytes => Some(Failure::new(
                "LOG_BUDGET_EXCEEDED",
                "aggregate log byte budget exceeded",
            )),
            Ok(exit) if exit.code != 0 => Some(Failure::new(
                "STEP_FAILED",
                format!("step failed: {tag}, exit={}", exit.code),
            )),
            Ok(_) => None,
        };
        self.finished_event(
            step,
            exit,
            wall,
            (&stdout, &stderr),
            verdict.as_ref().map(|f| (f.code, f.message.as_str())),
        );
        verdict.map_or(Ok(()), |failure| Err(failure.at(tag)))
    }

    /// A serial step whose streams are diagnostics: both go straight to their log files,
    /// and every byte counts against the aggregate log budget.
    fn plain(
        &mut self,
        step: &Step,
        argv: &[String],
        logs: &Logs,
        tick: Instant,
        seconds: u64,
    ) -> Attempt<Exit> {
        let stdout = new_log(&logs.stdout)?;
        let stderr = new_log(&logs.stderr)?;
        let mut child = sys::spawn(Launch {
            argv,
            cwd: &self.context.output,
            environment: &self.environment,
            stdout: Sink::File(stdout),
            stderr: Sink::File(stderr),
            affinity: None,
        })?;
        self.started_event(step, child.pid, None);
        let deadline = Duration::from_secs(seconds);
        let failure = loop {
            if child.exited() {
                break None;
            }
            if let Some(signal) = sys::cancellation() {
                break Some(Failure::new(
                    "CANCELLED",
                    format!("cancelled by signal {signal}"),
                ));
            }
            let elapsed = tick.elapsed();
            if elapsed > deadline {
                break Some(Failure::new(
                    "STEP_TIMEOUT",
                    format!("step deadline exceeded: {}", step.id),
                ));
            }
            if self.log_bytes + size(&logs.stdout) + size(&logs.stderr)
                > self.manifest.scope.max_log_bytes
            {
                break Some(Failure::new(
                    "LOG_BUDGET_EXCEEDED",
                    "aggregate log byte budget exceeded",
                ));
            }
            let left = deadline.saturating_sub(elapsed) + Duration::from_millis(1);
            sys::wait(
                &child.waiter().into_iter().collect::<Vec<_>>(),
                left.min(SERIAL_TICK),
            );
        };
        if let Some(failure) = failure {
            child.kill_group();
            child.reap(REAP_LIMIT)?;
            return Err(failure);
        }
        Ok(child.reap(REAP_LIMIT)?)
    }

    /// A serial step whose standard output is a declared data stream. Both pipes are
    /// streamed to their files; the first chunk over a budget fails the step, the whole
    /// process group is killed, and at most one chunk plus a bounded drain is kept as
    /// evidence. An over-budget capture is never reported as a success.
    fn capture(
        &mut self,
        step: &Step,
        argv: &[String],
        logs: &Logs,
        tick: Instant,
        seconds: u64,
        file_budget: u64,
    ) -> Attempt<Exit> {
        let mut targets = [new_log(&logs.stdout)?, new_log(&logs.stderr)?];
        let mut child = sys::spawn(Launch {
            argv,
            cwd: &self.context.output,
            environment: &self.environment,
            stdout: Sink::Pipe,
            stderr: Sink::Pipe,
            affinity: None,
        })?;
        self.started_event(step, child.pid, None);
        let mut pipes = [child.stdout.take(), child.stderr.take()];
        let mut sizes = [0_u64; 2];
        let (mut failure, mut drain_end, mut drained) = (None::<Failure>, None::<Instant>, 0_u64);
        let deadline = Duration::from_secs(seconds);
        let log_budget = self.manifest.scope.max_log_bytes;
        let artifact_budget = self.manifest.capture.aggregate_limit_bytes;
        let mut buffer = vec![0_u8; CAPTURE_CHUNK];
        while pipes.iter().any(Option::is_some) || !child.exited() {
            if failure.is_none() {
                if let Some(signal) = sys::cancellation() {
                    failure = Some(Failure::new(
                        "CANCELLED",
                        format!("cancelled by signal {signal}"),
                    ));
                } else if tick.elapsed() > deadline {
                    failure = Some(Failure::new(
                        "STEP_TIMEOUT",
                        "step deadline exceeded during stdout artifact capture",
                    ));
                }
            }
            if failure.is_some() && drain_end.is_none() {
                child.kill_group();
                drain_end = Some(Instant::now() + Duration::from_secs(2));
            }
            if drain_end.is_some_and(|end| Instant::now() > end || drained > DRAIN_BYTES) {
                failure = Some(Failure::new(
                    "CAPTURE_CLEANUP_FAILED",
                    "failed capture cleanup exceeded bounded pipe drain",
                ));
                break;
            }
            let mut watched: Vec<_> = pipes
                .iter()
                .flatten()
                .map(std::os::fd::AsRawFd::as_raw_fd)
                .collect();
            if !child.exited() {
                watched.extend(child.waiter());
            }
            sys::wait(&watched, Duration::from_millis(100));
            for stream in 0..2 {
                // A bounded number of chunks per stream and turn keeps both streams and the
                // deadline served while a tool writes continuously.
                for _ in 0..16 {
                    let Some(pipe) = pipes[stream].as_mut() else {
                        break;
                    };
                    match pipe.read(&mut buffer) {
                        Ok(0) => pipes[stream] = None,
                        Ok(count) => {
                            targets[stream].write_all(&buffer[..count])?;
                            sizes[stream] += count as u64;
                            if drain_end.is_some() {
                                drained += count as u64;
                            }
                            if failure.is_none() {
                                failure = if self.log_bytes + sizes[1] > log_budget {
                                    Some(Failure::new(
                                        "LOG_BUDGET_EXCEEDED",
                                        "aggregate log byte budget exceeded (all stderr is diagnostic)",
                                    ))
                                } else if sizes[0] > file_budget {
                                    Some(Failure::new(
                                        "STDOUT_ARTIFACT_BUDGET_EXCEEDED",
                                        "per-file stdout artifact byte budget exceeded",
                                    ))
                                } else if self.artifact_bytes + sizes[0] > artifact_budget {
                                    Some(Failure::new(
                                        "STDOUT_ARTIFACT_BUDGET_EXCEEDED",
                                        "aggregate stdout artifact byte budget exceeded",
                                    ))
                                } else {
                                    None
                                };
                                if failure.is_some() {
                                    break;
                                }
                            }
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
                        Err(error) if error.kind() == std::io::ErrorKind::Interrupted => (),
                        Err(error) => return Err(error.into()),
                    }
                }
            }
        }
        drop(pipes);
        if failure.is_some() || !child.exited() {
            child.kill_group();
        }
        let exit = child.reap(REAP_LIMIT)?;
        match failure {
            Some(failure) => Err(failure),
            None => Ok(exit),
        }
    }

    /// Run the steps in manifest order up to the stop target.
    fn steps(&mut self, stop_after: Option<usize>) -> Attempt<()> {
        let manifest = self.manifest;
        let mut index = 0;
        while index < manifest.steps.len() {
            if let Some(signal) = sys::cancellation() {
                return Err(Failure::new(
                    "CANCELLED",
                    format!("cancelled by signal {signal}"),
                ));
            }
            // A stop target inside a group makes that group a serial prefix.
            if let Some(group) = manifest.groups.get(&index)
                && !stop_after.is_some_and(|stop| group.steps.contains(&stop))
            {
                self.group(group)?;
                index = group.steps[group.steps.len() - 1] + 1;
                continue;
            }
            let step = &manifest.steps[index];
            self.serial(step).map_err(|failure| failure.at(&step.id))?;
            if stop_after == Some(index) {
                break;
            }
            index += 1;
        }
        // Pinned sources are checked again: no file changed while the tools ran.
        let paths: Vec<PathBuf> = self
            .context
            .records
            .iter()
            .map(|(_, record)| PathBuf::from(&record.path))
            .collect();
        for (found, (_, record)) in identity::identities(&paths)
            .into_iter()
            .zip(&self.context.records)
        {
            if found? != *record {
                return Err(Failure::new(
                    "INPUT_CHANGED_DURING_RUN",
                    "producer input changed during execution",
                ));
            }
        }
        Ok(())
    }

    /// The summary of a run whose steps all succeeded.
    fn completed(&self, stop_after: Option<usize>, manifest_sha256: &str) -> Attempt<Json> {
        let manifest = self.manifest;
        let produced = |path: &str| -> Attempt<Identity> {
            Ok(identity::identity(&self.context.output_path(path)?)?)
        };
        let mut summary = Json::Object(Vec::new());
        match stop_after {
            None => {
                let names: BTreeSet<&str> = manifest
                    .gates
                    .iter()
                    .map(|(name, _)| name.as_str())
                    .collect();
                if names != crate::ihp::GATES.iter().copied().collect() {
                    return Err(Failure::new(
                        "GATE_SET_MISMATCH",
                        "full-flow gate set differs",
                    ));
                }
                let mut gates = Json::Object(Vec::new());
                for (name, path) in &manifest.gates {
                    gates.set(name.clone(), produced(path)?.json());
                }
                let mut artifacts = Json::Object(Vec::new());
                for (name, path) in &manifest.artifacts {
                    artifacts.set(name.clone(), produced(path)?.json());
                }
                summary.set("schema", Json::str("rusteda.ihp-rtl2gds.v1"));
                summary.set("status", Json::str("completed"));
                summary.set("implementation", Json::str("rusteda"));
                summary.set("manifest_sha256", Json::str(manifest_sha256));
                summary.set("scope", manifest.scope.raw.clone());
                summary.set("gates", gates);
                summary.set("artifacts", artifacts);
                summary.set("failed_items", Json::Array(Vec::new()));
                summary.set("unverified_items", Json::Array(Vec::new()));
            }
            Some(stop) => {
                let mut artifacts = Json::Object(Vec::new());
                let mut missing = Vec::new();
                for (name, path) in &manifest.artifacts {
                    let source = self.context.output_path(path)?;
                    if source.is_file() {
                        artifacts.set(name.clone(), identity::identity(&source)?.json());
                    } else {
                        missing.push(Json::str(name.clone()));
                    }
                }
                summary.set("schema", Json::str("rusteda.ihp-frontend.v1"));
                summary.set("status", Json::str("frontend_completed"));
                summary.set("implementation", Json::str("rusteda"));
                summary.set("stop_after", Json::str(manifest.steps[stop].id.clone()));
                summary.set("manifest_sha256", Json::str(manifest_sha256));
                summary.set("scope", manifest.scope.raw.clone());
                summary.set("artifacts", artifacts);
                summary.set("unavailable_artifacts", Json::Array(missing));
                summary.set("failed_items", Json::Array(Vec::new()));
                summary.set(
                    "unverified_items",
                    Json::Array(
                        UNVERIFIED_PREFIX
                            .iter()
                            .map(|item| Json::str(*item))
                            .collect(),
                    ),
                );
            }
        }
        Ok(summary)
    }
}

/// `{"handle": ..., <registry entry members>}` as the receipts record a stdout artifact.
pub(super) fn declared(handle: &str, spec: &ArtifactSpec) -> Json {
    let mut value = Json::object([("handle", Json::str(handle))]);
    for (name, item) in spec.raw.members().unwrap_or_default() {
        value.set(name.clone(), item.clone());
    }
    value
}

fn refused(code: &'static str, message: impl Into<String>) -> Error {
    Error::new(code, message)
}

/// The worker identity a run is bound to: the option, else `RUSTEDA_WORKER_ID`.
fn worker_identity(options: &Options) -> Result<String> {
    let identity = options
        .worker_id
        .clone()
        .or_else(|| std::env::var("RUSTEDA_WORKER_ID").ok())
        .unwrap_or_default();
    if identity.len() != 64
        || !identity
            .bytes()
            .all(|b| matches!(b, b'a'..=b'f' | b'0'..=b'9'))
    {
        return Err(refused(
            "WORKER_IDENTITY_REQUIRED",
            "missing revision-bound worker ownership identity",
        ));
    }
    Ok(identity)
}

/// The CPUs of the allocation must be available to this process and must be distinct
/// physical cores.
fn check_cpus(manifest: &Manifest) -> Result<()> {
    let available = sys::affinity()?;
    let cpus = &manifest.scope.cpu_affinity;
    if cpus.iter().any(|cpu| !available.contains(cpu)) {
        return Err(refused(
            "CPU_ALLOCATION_INVALID",
            "invalid physical-core CPU allocation",
        ));
    }
    let mut cores = BTreeSet::new();
    for cpu in cpus {
        let topology = PathBuf::from(format!("/sys/devices/system/cpu/cpu{cpu}/topology"));
        let read = |name: &str| {
            fs::read_to_string(topology.join(name)).map_err(|error| {
                refused(
                    "CPU_ALLOCATION_INVALID",
                    format!("cannot read the topology of CPU {cpu}: {error}"),
                )
            })
        };
        cores.insert((
            read("physical_package_id")?.trim().to_owned(),
            read("core_id")?.trim().to_owned(),
        ));
    }
    if cores.len() != cpus.len() {
        return Err(refused(
            "CPU_ALLOCATION_INVALID",
            "CPU allocation contains SMT siblings",
        ));
    }
    Ok(())
}

pub(super) fn execute(options: &Options) -> Result<Outcome> {
    let started = Instant::now();
    let worker = worker_identity(options)?;
    let ownership = options
        .ownership
        .clone()
        .or_else(|| std::env::var_os("RUSTEDA_OWNERSHIP").map(PathBuf::from))
        .ok_or_else(|| {
            refused(
                "WORKER_IDENTITY_REQUIRED",
                "missing worker ownership directory (RUSTEDA_OWNERSHIP)",
            )
        })?;
    fs::create_dir_all(&options.output)?;
    let output = resolve_lenient(&options.output);
    let lock = OpenOptions::new()
        .append(true)
        .create(true)
        .open(output.join("workspace.lock"))?;
    FileExt::try_lock_exclusive(&lock).map_err(|_| {
        refused(
            "WORKSPACE_BUSY",
            format!(
                "another executor holds {}",
                output.join("workspace.lock").display()
            ),
        )
    })?;
    let occupied = fs::read_dir(&output)?
        .filter_map(|entry| entry.ok())
        .any(|entry| entry.file_name() != "workspace.lock");
    if options.base_rev != "empty" || occupied {
        return Err(refused(
            "REVISION_CONFLICT",
            "the executor requires --base-rev empty and an empty output directory",
        ));
    }
    let (manifest, stop_after) = load_manifest(&options.manifest, options.stop_after.as_deref())?;
    let records = verify_pins(&manifest, &options.project)?;
    pinned_tool(&records, "rustroad", options.rustroad.as_deref())?;
    let gnu_time = pinned_tool(&records, "gnu_time", options.gnu_time.as_deref())?;
    check_cpus(&manifest)?;
    let output_text = utf8(&output)?;
    let context = Context {
        output: output.clone(),
        output_text: output_text.clone(),
        threads: manifest.scope.threads,
        records,
    };
    // A placeholder that cannot be resolved would fail its step only when the run reaches
    // it; every command that is going to run is expanded once before anything starts.
    let last = stop_after.unwrap_or(manifest.steps.len() - 1);
    for step in &manifest.steps[..=last] {
        if let Kind::Command(command) = &step.kind {
            for argument in &command.arguments {
                context.expand(argument).map_err(|error| {
                    refused(error.code, format!("step {}: {}", step.id, error.message))
                })?;
            }
        }
    }
    let mut events = match &options.events {
        Some(target) => Events::open(target, started).map_err(|error| {
            refused(
                "EVENTS_UNAVAILABLE",
                format!("cannot open the event stream {}: {error}", target.display()),
            )
        })?,
        None => Events::none(started),
    };
    sys::set_affinity(&manifest.scope.cpu_affinity)?;
    if let Some(parent) = ownership.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::create_dir(&ownership).map_err(|error| {
        refused(
            "OWNERSHIP_EXISTS",
            format!(
                "cannot create the ownership directory {}: {error}",
                ownership.display()
            ),
        )
    })?;
    let manifest_identity = identity::identity(&options.manifest)?;
    write_json(
        &ownership.join("native.json"),
        &Json::object([
            ("request_id", Json::str(worker.clone())),
            ("implementation", Json::str("native")),
            ("manifest", manifest_identity.json()),
        ]),
        true,
    )?;
    fs::write(output.join("HEAD"), "rev0\n")?;
    fs::create_dir(output.join("logs"))?;
    fs::create_dir(output.join("tmp"))?;
    let threads = manifest.scope.threads;
    let mut environment: Vec<(OsString, OsString)> = std::env::vars_os().collect();
    for (name, value) in [
        ("CARGO_BUILD_JOBS", "2".to_owned()),
        ("RUST_TEST_THREADS", "2".to_owned()),
        ("OMP_NUM_THREADS", threads.to_string()),
        ("RAYON_NUM_THREADS", threads.to_string()),
        ("OPENBLAS_NUM_THREADS", "1".to_owned()),
    ] {
        set_variable(&mut environment, name, value);
    }
    set_variable(
        &mut environment,
        "TMPDIR",
        output.join("tmp").into_os_string(),
    );
    // A path-valued debug writer of the router stays disabled inside a run.
    environment.retain(|(name, _)| name != "RUSTROAD_RULE_DUMP");
    set_variable(&mut environment, "RUSTEDA_WORKER_ID", worker.clone());
    set_variable(
        &mut environment,
        "RUSTEDA_OWNERSHIP",
        ownership.clone().into_os_string(),
    );
    events.emit(
        "run_started",
        json!({"manifest": manifest_identity.path, "manifest_sha256": manifest_identity.sha256,
            "output": output, "steps": manifest.steps.len(), "stop_after": options.stop_after,
            "worker_id": worker, "pid": std::process::id()}),
    );
    let mut run = Run {
        manifest: &manifest,
        context,
        started,
        gnu_time,
        environment,
        receipts: Vec::new(),
        log_bytes: 0,
        artifact_bytes: 0,
        events,
    };
    let result = run
        .steps(stop_after)
        .and_then(|()| run.completed(stop_after, &manifest_identity.sha256))
        .and_then(|summary| {
            run.publish(&[], true)?;
            Ok(summary)
        });
    let (mut summary, failure) = match result {
        Ok(summary) => (summary, None),
        Err(failure) => (
            Json::object([
                ("schema", Json::str("rusteda.ihp-rtl2gds.v1")),
                ("status", Json::str("failed")),
                ("implementation", Json::str("rusteda")),
                ("error", Json::str(failure.message.clone())),
                (
                    "failed_items",
                    Json::Array(vec![Json::str(failure.message.clone())]),
                ),
                (
                    "unverified_items",
                    Json::Array(vec![Json::str("complete_flow")]),
                ),
            ]),
            Some(failure),
        ),
    };
    let number = |value: u64| Json::int(value);
    let mut accounting = Json::object([
        ("mode", Json::str(manifest.capture.mode)),
        ("declared_bytes", number(manifest.capture.declared_bytes)),
        (
            "aggregate_limit_bytes",
            number(manifest.capture.aggregate_limit_bytes),
        ),
        ("files", number(manifest.capture.files as u64)),
        ("diagnostic_bytes", number(run.log_bytes)),
        (
            "diagnostic_limit_bytes",
            number(manifest.scope.max_log_bytes),
        ),
        ("stdout_artifact_bytes", number(run.artifact_bytes)),
    ]);
    accounting.set(
        "stderr_policy",
        Json::str("every stderr byte is diagnostic, without exemptions"),
    );
    accounting.set(
        "raw_paths",
        Json::str("logs/<step>.stdout and logs/<step>.stderr; preserved and hashed"),
    );
    accounting.set(
        "overflow_policy",
        Json::str("failed capture; bounded extra chunks retained, never a truncated success"),
    );
    summary.set("capture_accounting", accounting);
    let wall = started.elapsed().as_secs_f64();
    let peak = sys::peak_rss_kib();
    let cgroup = cgroup_resources();
    summary.set(
        "resources",
        Json::object([
            ("wall_seconds", Json::Float(wall)),
            ("peak_reaped_process_rss_kib", number(peak)),
            ("cgroup", cgroup.clone()),
            (
                "cpu_affinity",
                manifest
                    .scope
                    .raw
                    .get("cpu_affinity")
                    .cloned()
                    .unwrap_or(Json::Null),
            ),
            ("threads", number(threads as u64)),
            (
                "scope",
                Json::str(
                    "whole worker including parsing, plans, tool I/O, extraction and signoff",
                ),
            ),
        ]),
    );
    write_json(&output.join("summary.json"), &summary, true)?;
    fs::write(output.join("HEAD"), "rev1\n")?;
    let metrics = ownership
        .parent()
        .unwrap_or(Path::new("."))
        .join("worker-metrics.json");
    write_json(
        &metrics,
        &Json::object([
            ("elapsed_seconds", Json::Float(wall)),
            ("peak_rss_kib", number(peak)),
            (
                "rss_scope",
                Json::str(
                    "maximum reaped worker/tool RSS; cgroup peak separately recorded in summary",
                ),
            ),
            ("request_id", Json::str(worker)),
            ("cgroup", cgroup),
        ]),
        true,
    )?;
    let status = summary
        .get("status")
        .and_then(Json::as_str)
        .unwrap_or("failed");
    let (user, system) = sys::self_cpu_seconds();
    let mut envelope = json!({"schema": "rustlane.v1", "ok": failure.is_none(), "status": status,
        "output": output_text, "qualification": "requires_checked_flow_ingestion",
        "steps_completed": run.receipts.len(), "steps_total": manifest.steps.len(),
        "wall_seconds": wall,
        "executor": {"user_seconds": user, "system_seconds": system}});
    if let Some(failure) = &failure {
        envelope["error"] =
            json!({"code": failure.code, "message": failure.message, "step": failure.step});
    }
    run.events.emit(
        "run_finished",
        json!({"status": status, "wall_seconds": wall, "steps_completed": run.receipts.len(),
            "error": envelope.get("error"),
            "executor_user_seconds": user, "executor_system_seconds": system}),
    );
    let exit_code = match &failure {
        None => 0,
        Some(failure) if failure.code == "CANCELLED" => 130,
        Some(_) => 1,
    };
    drop(lock);
    Ok(Outcome {
        exit_code,
        value: envelope,
        events_on_stdout: run.events.to_stdout(),
    })
}

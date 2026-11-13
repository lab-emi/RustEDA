// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Parallel groups of a step graph: admission by dependencies, CPU slots, memory
//! reservations and width; cancellation of the group on its first failure.
//!
//! One coordinator, at most 16 live process groups, no helper thread. State is
//! O(steps of the group); every wait ends at a step's exit or after 50 ms.

use super::{
    identity,
    manifest::{self, ArtifactSpec, Group, Kind, Step},
    pyjson::Json,
    run::{
        Attempt, Failure, GROUP_TICK, Logs, REAP_LIMIT, Run, declared, new_log, set_variable, size,
        write_json,
    },
    sys::{self, Child, Launch, Sink},
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    time::{Duration, Instant},
};

/// A live step of a parallel group.
struct Job {
    index: usize,
    child: Child,
    logs: Logs,
    tick: Instant,
    seconds: u64,
    cpus: Vec<usize>,
    artifact: Option<ArtifactSpec>,
}

impl Run<'_> {
    /// Run a parallel group to completion. Ready steps launch in manifest order on fixed,
    /// non-overlapping CPU sets; admission sums the memory reservations of live steps;
    /// all live logs share the aggregate byte budget; a declared stdout artifact is
    /// charged to its own caps. The first failure cancels every live step of the group.
    pub(super) fn group(&mut self, group: &Group) -> Attempt<()> {
        let mut observed: BTreeMap<usize, Json> = BTreeMap::new();
        let mut running: Vec<Job> = Vec::new();
        let outcome = self.group_loop(group, &mut observed, &mut running);
        if let Err(failure) = &outcome {
            let note = format!("parallel group cancelled: {}", failure.message);
            while !running.is_empty() {
                let job = running.remove(0);
                // The cancelled steps' own results cannot replace the first failure.
                let _ = self.finish(group, job, Some(note.clone()), &mut observed);
            }
        }
        self.receipts.extend(observed.into_values());
        let published = self.publish(&[], false);
        // The group's own failure is the one to report.
        outcome?;
        Ok(published?)
    }

    fn group_loop(
        &mut self,
        group: &Group,
        observed: &mut BTreeMap<usize, Json>,
        running: &mut Vec<Job>,
    ) -> Attempt<()> {
        let manifest = self.manifest;
        let log_budget = manifest.scope.max_log_bytes;
        let artifact_budget = manifest.capture.aggregate_limit_bytes;
        let mut pending: Vec<usize> = group.steps.clone();
        let mut completed: BTreeSet<&str> = BTreeSet::new();
        while !pending.is_empty() || !running.is_empty() {
            if let Some(signal) = sys::cancellation() {
                return Err(Failure::new(
                    "CANCELLED",
                    format!("cancelled by signal {signal}"),
                ));
            }
            let (mut live_bytes, mut live_artifacts) = (0, 0);
            for job in running.iter() {
                live_bytes += size(&job.logs.stderr);
                let stdout = size(&job.logs.stdout);
                match &job.artifact {
                    None => live_bytes += stdout,
                    Some(spec) => {
                        live_artifacts += stdout;
                        if stdout > spec.max_bytes {
                            return Err(Failure::new(
                                "STDOUT_ARTIFACT_BUDGET_EXCEEDED",
                                "per-file stdout artifact byte budget exceeded during parallel execution",
                            )
                            .at(&manifest.steps[job.index].id));
                        }
                    }
                }
            }
            if self.log_bytes + live_bytes > log_budget {
                return Err(Failure::new(
                    "LOG_BUDGET_EXCEEDED",
                    "aggregate log byte budget exceeded during parallel execution",
                ));
            }
            if self.artifact_bytes + live_artifacts > artifact_budget {
                return Err(Failure::new(
                    "STDOUT_ARTIFACT_BUDGET_EXCEEDED",
                    "aggregate stdout artifact byte budget exceeded during parallel execution",
                ));
            }
            let mut position = 0;
            while position < running.len() {
                let tag = manifest.steps[running[position].index].id.as_str();
                if running[position].child.exited() {
                    let job = running.remove(position);
                    let code = self.finish(group, job, None, observed)?;
                    if code != 0 {
                        return Err(Failure::new(
                            "STEP_FAILED",
                            format!("step failed: {tag}, exit={code}"),
                        )
                        .at(tag));
                    }
                    completed.insert(tag);
                } else if running[position].tick.elapsed()
                    > Duration::from_secs(running[position].seconds)
                {
                    return Err(Failure::new(
                        "STEP_TIMEOUT",
                        format!("step deadline exceeded: {tag}"),
                    )
                    .at(tag));
                } else {
                    position += 1;
                }
            }
            let mut slot = 0;
            while slot < pending.len() {
                let index = pending[slot];
                let step = &manifest.steps[index];
                let tag = step.id.as_str();
                let node = &group.nodes[tag];
                if !node
                    .depends_on
                    .iter()
                    .all(|dep| completed.contains(dep.as_str()))
                {
                    slot += 1;
                    continue;
                }
                let command = match &step.kind {
                    Kind::Render {
                        destination,
                        content,
                    } => {
                        let receipt = self
                            .render(step, destination, content)
                            .map_err(|f| f.at(tag))?;
                        observed.insert(index, receipt);
                        completed.insert(tag);
                        pending.remove(slot);
                        self.publish(&observed.values().cloned().collect::<Vec<_>>(), false)?;
                        continue;
                    }
                    Kind::Command(command) => command,
                };
                let occupied = running
                    .iter()
                    .flat_map(|job| group.nodes[&manifest.steps[job.index].id].cpu_slots.iter())
                    .any(|slot| node.cpu_slots.contains(slot));
                let memory: u64 = running
                    .iter()
                    .map(|job| group.nodes[&manifest.steps[job.index].id].memory_mib)
                    .sum();
                if running.len() >= group.max_parallel
                    || occupied
                    || memory + node.memory_mib > group.max_memory_mib
                {
                    slot += 1;
                    continue;
                }
                let job = self
                    .launch(index, step, command, node)
                    .map_err(|f| f.at(tag))?;
                running.push(job);
                pending.remove(slot);
            }
            if !pending.is_empty() && running.is_empty() {
                return Err(Failure::new(
                    "SCHEDULER_STALLED",
                    "parallel dependency/resource admission made no progress",
                ));
            }
            if !running.is_empty() {
                let waiters: Vec<_> = running
                    .iter()
                    .filter_map(|job| job.child.waiter())
                    .collect();
                if waiters.len() == running.len() {
                    sys::wait(&waiters, GROUP_TICK);
                } else {
                    std::thread::sleep(GROUP_TICK);
                }
            }
        }
        if self.log_bytes > log_budget {
            return Err(Failure::new(
                "LOG_BUDGET_EXCEEDED",
                "aggregate log byte budget exceeded during parallel execution",
            ));
        }
        Ok(())
    }

    fn launch(
        &mut self,
        index: usize,
        step: &Step,
        command: &manifest::Command,
        node: &manifest::Node,
    ) -> Attempt<Job> {
        let tick = Instant::now();
        let tag = step.id.as_str();
        let cpus: Vec<usize> = node
            .cpu_slots
            .iter()
            .map(|slot| self.manifest.scope.cpu_affinity[*slot])
            .collect();
        let logs = Logs::new(&self.context.output, tag);
        let argv = self.argv(command, &logs)?;
        let numbers =
            |values: &[usize]| Json::Array(values.iter().map(|v| Json::int(*v as u64)).collect());
        let record = Json::object([
            (
                "argv",
                Json::Array(argv.iter().cloned().map(Json::Str).collect()),
            ),
            ("cwd", Json::str(self.context.output_text.clone())),
            ("cpu_affinity", numbers(&cpus)),
            ("memory_reservation_mib", Json::int(node.memory_mib)),
        ]);
        write_json(&logs.command, &record, false)?;
        let temporary = self.context.output.join("tmp").join(tag);
        fs::create_dir(&temporary)?;
        let mut environment = self.environment.clone();
        set_variable(&mut environment, "OMP_NUM_THREADS", cpus.len().to_string());
        set_variable(
            &mut environment,
            "RAYON_NUM_THREADS",
            cpus.len().to_string(),
        );
        set_variable(&mut environment, "TMPDIR", temporary.into_os_string());
        set_variable(&mut environment, "RUSTEDA_PARALLEL_STEP", tag);
        let stdout = new_log(&logs.stdout)?;
        let stderr = new_log(&logs.stderr)?;
        // Affinity is set before exec so that every descendant starts in the same lane.
        let child = sys::spawn(Launch {
            argv: &argv,
            cwd: &self.context.output,
            environment: &environment,
            stdout: Sink::File(stdout),
            stderr: Sink::File(stderr),
            affinity: Some(&cpus),
        })?;
        self.started_event(step, child.pid, Some((&cpus, node.memory_mib)));
        Ok(Job {
            index,
            child,
            logs,
            tick,
            seconds: command.timeout_s,
            artifact: self.artifact(command),
            cpus,
        })
    }

    /// Reap a group step, kill what it left behind, hash its logs and record its receipt.
    /// Returns the step's exit code as the group sees it (non-zero for a failed capture).
    fn finish(
        &mut self,
        group: &Group,
        mut job: Job,
        mut error: Option<String>,
        observed: &mut BTreeMap<usize, Json>,
    ) -> Attempt<i32> {
        let manifest = self.manifest;
        let step = &manifest.steps[job.index];
        let tag = step.id.as_str();
        let node = &group.nodes[tag];
        // Leftover descendants die even when the leader exited: a step owns its group.
        job.child.kill_group();
        let exit = job.child.reap(REAP_LIMIT)?;
        let stdout = identity::identity(&job.logs.stdout)?;
        let stderr = identity::identity(&job.logs.stderr)?;
        self.log_bytes += stderr.bytes
            + if job.artifact.is_none() {
                stdout.bytes
            } else {
                0
            };
        self.artifact_bytes += if job.artifact.is_some() {
            stdout.bytes
        } else {
            0
        };
        let wall = job.tick.elapsed().as_secs_f64();
        let mut code = exit.code;
        let numbers =
            |values: &[usize]| Json::Array(values.iter().map(|v| Json::int(*v as u64)).collect());
        let mut receipt = Json::object([
            ("id", Json::str(tag)),
            ("kind", Json::str("command")),
            ("exit_code", Json::int(code)),
            ("wall_seconds", Json::Float(wall)),
            ("stdout", stdout.json()),
            ("stderr", stderr.json()),
            (
                "started_seconds",
                Json::Float(job.tick.duration_since(self.started).as_secs_f64()),
            ),
            ("cpu_affinity", numbers(&job.cpus)),
            ("memory_reservation_mib", Json::int(node.memory_mib)),
            (
                "depends_on",
                Json::Array(node.depends_on.iter().cloned().map(Json::Str).collect()),
            ),
        ]);
        let handle = match &step.kind {
            Kind::Command(command) => command.stdout_artifact.as_deref(),
            Kind::Render { .. } => None,
        };
        if let (Some(spec), Some(handle)) = (&job.artifact, handle) {
            receipt.set("stdout_artifact", declared(handle, spec));
            if error.is_none() && stdout.bytes > spec.max_bytes {
                error = Some("per-file stdout artifact byte budget exceeded".into());
            }
            if error.is_none() && self.artifact_bytes > manifest.capture.aggregate_limit_bytes {
                error = Some("aggregate stdout artifact byte budget exceeded".into());
            }
            if error.is_some() {
                if code == 0 {
                    code = 1;
                }
                receipt.set("exit_code", Json::int(code));
            }
        }
        if let Some(error) = &error {
            receipt.set("capture_error", Json::str(error.clone()));
        }
        observed.insert(job.index, receipt);
        self.publish(&observed.values().cloned().collect::<Vec<_>>(), false)?;
        let failure = match &error {
            Some(error) => Some(("STEP_CANCELLED", error.clone())),
            None if code != 0 => Some(("STEP_FAILED", format!("step failed: {tag}, exit={code}"))),
            None => None,
        };
        self.finished_event(
            step,
            Some(exit),
            wall,
            (&stdout, &stderr),
            failure
                .as_ref()
                .map(|(code, message)| (*code, message.as_str())),
        );
        Ok(code)
    }
}

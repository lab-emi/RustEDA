// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Read-only queries: the resolved step graph of a manifest (dry run) and the state of
//! an output directory. Neither creates, changes or locks anything for longer than the
//! query; both are O(steps) and read at most the manifest, the receipts, the summary and
//! the names in `logs/`.

use super::{
    Options,
    identity::{self, Identity, utf8},
    manifest::{self, Kind, Manifest},
    pyjson::Json,
    template::{Context, resolve_lenient},
};
use crate::error::{Error, Result};
use fs2::FileExt;
use serde_json::{Map, Value, json};
use std::{
    collections::BTreeSet,
    fs::{self, File},
    path::{Path, PathBuf},
    time::UNIX_EPOCH,
};

const MAX_REPORT_BYTES: u64 = 16 * 1024 * 1024;

fn value(json: &Json) -> Value {
    json.to_line()
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or(Value::Null)
}

/// Read, parse and validate the manifest of a run, and resolve its stop target.
pub(super) fn load_manifest(
    path: &Path,
    stop_after: Option<&str>,
) -> Result<(Manifest, Option<usize>)> {
    let refused = || {
        Error::new(
            "MANIFEST_BUDGET",
            format!("missing or oversized JSON: {}", path.display()),
        )
    };
    let meta = fs::metadata(path).map_err(|_| refused())?;
    if !meta.is_file() || meta.len() > manifest::MAX_MANIFEST_BYTES {
        return Err(refused());
    }
    let manifest = manifest::parse(&fs::read(path)?)?;
    let stop = match stop_after {
        None => None,
        Some(target) => Some(manifest.step_index(target).ok_or_else(|| {
            Error::new(
                "UNKNOWN_STEP",
                "prefix stop target must identify exactly one manifest step",
            )
        })?),
    };
    Ok((manifest, stop))
}

/// Hash every pinned input and require the identity the manifest states.
pub(super) fn verify_pins(manifest: &Manifest, project: &Path) -> Result<Vec<(String, Identity)>> {
    let mut paths = Vec::with_capacity(manifest.pins.len());
    for pin in &manifest.pins {
        let path = Path::new(&pin.path);
        paths.push(if path.is_absolute() {
            path.to_path_buf()
        } else {
            project.join(super::template::relative(&pin.path)?)
        });
    }
    let mut records = Vec::with_capacity(paths.len());
    for (found, pin) in identity::identities(&paths).into_iter().zip(&manifest.pins) {
        let name = pin
            .key
            .split_once(':')
            .map_or(pin.key.as_str(), |(_, name)| name);
        let found = found.map_err(|error| {
            Error::new(
                "INPUT_UNAVAILABLE",
                format!("pinned input {name}: {}", error.message),
            )
        })?;
        if found.sha256 != pin.sha256 || found.bytes != pin.bytes {
            return Err(Error::new(
                "INPUT_IDENTITY_CHANGED",
                format!("input identity changed: {name}"),
            ));
        }
        records.push((pin.key.clone(), found));
    }
    Ok(records)
}

/// A tool path given on the command line must be the pinned file; the given spelling is
/// the one commands are launched and recorded with.
pub(super) fn pinned_tool(
    records: &[(String, Identity)],
    name: &str,
    given: Option<&Path>,
) -> Result<String> {
    let pinned = records
        .iter()
        .find(|(key, _)| key.strip_prefix("tools:") == Some(name))
        .map(|(_, record)| record)
        .ok_or_else(|| {
            Error::new(
                "MANIFEST_FIELDS",
                format!("the manifest must pin the {name} tool"),
            )
        })?;
    let Some(given) = given else {
        return Ok(pinned.path.clone());
    };
    let same = fs::canonicalize(given).is_ok_and(|resolved| resolved == Path::new(&pinned.path));
    if !same {
        return Err(Error::new(
            "TOOL_ARGUMENT_MISMATCH",
            format!("worker argument differs from pinned {name}"),
        ));
    }
    // Components are joined again so that `a//b` and `a/./b` are recorded as `a/b`.
    utf8(&given.components().collect::<PathBuf>())
}

/// The resolved step graph of a run, without running it: every step with its expanded
/// command line, working directory, deadline, CPU set, memory reservation, declared
/// stdout artifact and the steps that must finish before it may start. Pinned inputs
/// are hashed and must match. Nothing is created.
pub fn plan(options: &Options) -> Result<Value> {
    let (manifest, stop_after) = load_manifest(&options.manifest, options.stop_after.as_deref())?;
    let records = verify_pins(&manifest, &options.project)?;
    pinned_tool(&records, "rustroad", options.rustroad.as_deref())?;
    let gnu_time = pinned_tool(&records, "gnu_time", options.gnu_time.as_deref())?;
    let output = resolve_lenient(&options.output);
    let output_text = utf8(&output)?;
    let output_ready = match fs::read_dir(&output) {
        Ok(entries) => entries
            .filter_map(|entry| entry.ok())
            .all(|entry| entry.file_name() == "workspace.lock"),
        Err(_) => !output.exists(),
    };
    let context = Context {
        output: output.clone(),
        output_text: output_text.clone(),
        threads: manifest.scope.threads,
        records,
    };
    // The group a step runs in: a group that holds the stop target runs as a serial prefix.
    let parallel = |index: usize| {
        manifest
            .group_of(index)
            .filter(|group| !stop_after.is_some_and(|stop| group.steps.contains(&stop)))
    };
    let mut steps = Vec::with_capacity(manifest.steps.len());
    // Steps whose completion the next serial step (or the sources of the next group) waits for.
    let mut frontier: Vec<String> = Vec::new();
    let mut index = 0;
    while index < manifest.steps.len() {
        let will_run = stop_after.is_none_or(|stop| index <= stop);
        let members: Vec<usize> = match parallel(index) {
            Some(group) => group.steps.clone(),
            None => vec![index],
        };
        let group = parallel(index);
        for &member in &members {
            let step = &manifest.steps[member];
            let node = group.map(|group| &group.nodes[&step.id]);
            let depends_on: Vec<String> = match node {
                Some(node) if !node.depends_on.is_empty() => node.depends_on.clone(),
                _ => frontier.clone(),
            };
            let mut row = Map::new();
            row.insert("index".into(), json!(member));
            row.insert("id".into(), json!(step.id));
            row.insert("kind".into(), json!(step.kind_name()));
            row.insert("will_run".into(), json!(will_run));
            row.insert(
                "mode".into(),
                json!(if group.is_some() {
                    "parallel"
                } else {
                    "serial"
                }),
            );
            row.insert(
                "group".into(),
                json!(group.map(|group| &manifest.steps[group.steps[0]].id)),
            );
            row.insert("depends_on".into(), json!(depends_on));
            match &step.kind {
                Kind::Render { destination, .. } => {
                    row.insert(
                        "destination".into(),
                        json!(context.output_path(destination).ok()),
                    );
                }
                Kind::Command(command) => {
                    let tool = context.record(&format!("tools:{}", command.tool));
                    let logs = output.join("logs");
                    let mut argv = vec![
                        gnu_time.clone(),
                        "-v".into(),
                        "-o".into(),
                        utf8(&logs.join(format!("{}.time", step.id)))?,
                        tool.map(|tool| tool.path.clone()).unwrap_or_default(),
                    ];
                    for argument in &command.arguments {
                        argv.push(context.expand(argument).map_err(|error| {
                            Error::new(error.code, format!("step {}: {}", step.id, error.message))
                        })?);
                    }
                    row.insert("tool".into(), json!(command.tool));
                    row.insert("argv".into(), json!(argv));
                    row.insert("cwd".into(), json!(output_text));
                    row.insert("timeout_s".into(), json!(command.timeout_s));
                    row.insert(
                        "stdout".into(),
                        json!(logs.join(format!("{}.stdout", step.id))),
                    );
                    row.insert(
                        "stderr".into(),
                        json!(logs.join(format!("{}.stderr", step.id))),
                    );
                    let cpus: Option<Vec<usize>> = node.map(|node| {
                        node.cpu_slots
                            .iter()
                            .map(|slot| manifest.scope.cpu_affinity[*slot])
                            .collect()
                    });
                    let threads = cpus.as_ref().map_or(manifest.scope.threads, Vec::len);
                    row.insert(
                        "cpu_affinity".into(),
                        json!(cpus.unwrap_or_else(|| manifest.scope.cpu_affinity.clone())),
                    );
                    row.insert(
                        "memory_reservation_mib".into(),
                        json!(node.map(|node| node.memory_mib)),
                    );
                    row.insert("tool_threads".into(), json!(threads));
                    let artifact = command
                        .stdout_artifact
                        .as_ref()
                        .and_then(|handle| manifest.stdout_artifacts.as_ref()?.get(handle));
                    row.insert(
                        "stdout_artifact".into(),
                        artifact.map_or(Value::Null, |spec| value(&spec.raw)),
                    );
                }
            }
            steps.push(Value::Object(row));
        }
        // Everything a later step waits for: this serial step, or the group's steps that
        // no other step of the group depends on.
        frontier = match group {
            None => vec![manifest.steps[index].id.clone()],
            Some(group) => {
                let needed: BTreeSet<&str> = group
                    .nodes
                    .values()
                    .flat_map(|node| node.depends_on.iter().map(String::as_str))
                    .collect();
                members
                    .iter()
                    .map(|member| manifest.steps[*member].id.clone())
                    .filter(|id| !needed.contains(id.as_str()))
                    .collect()
            }
        };
        index = members[members.len() - 1] + 1;
    }
    let groups: Vec<Value> = manifest
        .groups
        .values()
        .map(|group| {
            json!({"first": manifest.steps[group.steps[0]].id,
                "last": manifest.steps[group.steps[group.steps.len() - 1]].id,
                "steps": group.steps.len(), "max_parallel": group.max_parallel,
                "max_memory_mib": group.max_memory_mib,
                "mode": if parallel(group.steps[0]).is_some() { "parallel" } else { "serial_prefix" }})
        })
        .collect();
    let manifest_identity = identity::identity(&options.manifest)?;
    Ok(
        json!({"schema": "rustlane.v1", "ok": true, "status": "planned",
        "manifest": value(&manifest_identity.json()), "name": manifest.name,
        "output": output_text, "output_ready": output_ready,
        "stop_after": options.stop_after, "scope": value(&manifest.scope.raw),
        "inputs_verified": context.records.len(),
        "capture": {"mode": manifest.capture.mode, "declared_bytes": manifest.capture.declared_bytes,
            "aggregate_limit_bytes": manifest.capture.aggregate_limit_bytes,
            "files": manifest.capture.files},
        "environment": {"OMP_NUM_THREADS": "tool_threads", "RAYON_NUM_THREADS": "tool_threads",
            "OPENBLAS_NUM_THREADS": "1", "CARGO_BUILD_JOBS": "2", "RUST_TEST_THREADS": "2",
            "TMPDIR": output.join("tmp"),
            "RUSTEDA_PARALLEL_STEP": "step id, parallel steps only"},
        "steps_total": manifest.steps.len(),
        "steps_to_run": stop_after.map_or(manifest.steps.len(), |stop| stop + 1),
        "groups": groups, "steps": steps}),
    )
}

fn read_json(path: &Path) -> Option<Json> {
    let meta = fs::metadata(path).ok()?;
    if !meta.is_file() || meta.len() > MAX_REPORT_BYTES {
        return None;
    }
    Json::parse(&fs::read(path).ok()?).ok()
}

/// The state of an output directory, running or finished. `state` is one of `absent`,
/// `empty`, `running`, `completed`, `frontend_completed`, `failed` and `interrupted`
/// (a run that ended without a summary). With a manifest (given, or named by the
/// ownership record beside the directory) the pending steps are listed as well.
pub fn status(output: &Path, manifest: Option<&Path>) -> Result<Value> {
    let output = resolve_lenient(output);
    let output_text = utf8(&output)?;
    if !output.is_dir() {
        return Ok(
            json!({"schema": "rustlane.v1", "ok": true, "state": "absent", "output": output_text}),
        );
    }
    // An executor holds the lock exclusively for its whole run.
    let locked = File::open(output.join("workspace.lock")).is_ok_and(|lock| {
        let busy = FileExt::try_lock_shared(&lock).is_err();
        if !busy {
            let _ = FileExt::unlock(&lock);
        }
        busy
    });
    let head = fs::read_to_string(output.join("HEAD"))
        .ok()
        .map(|text| text.trim().to_owned())
        .filter(|text| text.len() <= 128);
    let receipts = read_json(&output.join("step-receipts.json"))
        .and_then(|value| value.items().map(<[Json]>::to_vec))
        .unwrap_or_default();
    let summary = read_json(&output.join("summary.json"));
    let finished: BTreeSet<&str> = receipts
        .iter()
        .filter_map(|row| row.get("id").and_then(Json::as_str))
        .collect();
    let failed: Vec<&str> = receipts
        .iter()
        .filter(|row| {
            row.get("capture_error").is_some()
                || row
                    .get("exit_code")
                    .is_some_and(|code| code.as_i64() != Some(0))
        })
        .filter_map(|row| row.get("id").and_then(Json::as_str))
        .collect();
    // A command record without a receipt is a step that was launched and has not finished.
    let mut live = Vec::new();
    if let Ok(entries) = fs::read_dir(output.join("logs")) {
        for entry in entries.filter_map(|entry| entry.ok()).take(4096) {
            let name = entry.file_name();
            let Some(id) = name
                .to_str()
                .and_then(|name| name.strip_suffix(".command.json"))
            else {
                continue;
            };
            if finished.contains(id) {
                continue;
            }
            let since = entry
                .metadata()
                .ok()
                .and_then(|meta| meta.modified().ok())
                .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                .map(|time| time.as_secs_f64());
            let bytes = |suffix: &str| {
                fs::metadata(output.join("logs").join(format!("{id}.{suffix}")))
                    .map_or(0, |m| m.len())
            };
            live.push(json!({"id": id, "started_unix_time": since,
                "stdout_bytes": bytes("stdout"), "stderr_bytes": bytes("stderr")}));
        }
    }
    live.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
    let summary_status = summary
        .as_ref()
        .and_then(|summary| summary.get("status"))
        .and_then(Json::as_str);
    let state = match (locked, summary_status, head.as_deref()) {
        (true, _, _) => "running",
        (false, Some(status @ ("completed" | "frontend_completed" | "failed")), _) => status,
        (false, _, None) if receipts.is_empty() && live.is_empty() => "empty",
        _ => "interrupted",
    };
    let manifest_path = manifest.map(Path::to_path_buf).or_else(|| {
        let record = read_json(&output.parent()?.join("ownership/native.json"))?;
        Some(PathBuf::from(
            record.get("manifest")?.get("path")?.as_str()?,
        ))
    });
    let known = manifest_path
        .as_deref()
        .and_then(|path| load_manifest(path, None).ok())
        .map(|(manifest, _)| manifest);
    let stop_after = summary
        .as_ref()
        .and_then(|summary| summary.get("stop_after"))
        .and_then(Json::as_str);
    let pending: Option<Vec<&str>> = known.as_ref().map(|manifest| {
        let last = stop_after
            .and_then(|target| manifest.step_index(target))
            .unwrap_or(manifest.steps.len() - 1);
        manifest.steps[..=last]
            .iter()
            .map(|step| step.id.as_str())
            .filter(|id| !finished.contains(id) && !live.iter().any(|row| row["id"] == *id))
            .collect()
    });
    let pick = |key: &str| {
        summary
            .as_ref()
            .and_then(|summary| summary.get(key))
            .map_or(Value::Null, value)
    };
    Ok(
        json!({"schema": "rustlane.v1", "ok": true, "state": state, "output": output_text,
        "head": head, "locked": locked,
        "steps": {"finished": receipts.len(), "failed": failed,
            "last_finished": receipts.last().and_then(|row| row.get("id")).and_then(Json::as_str),
            "running": if locked { json!(live) } else { json!([]) },
            "unfinished": if locked { json!([]) } else { json!(live) },
            "total": known.as_ref().map(|manifest| manifest.steps.len()),
            "pending": pending},
        "manifest": manifest_path,
        "summary": if summary.is_some() {
            json!({"schema": pick("schema"), "status": pick("status"), "error": pick("error"),
                "stop_after": pick("stop_after"), "failed_items": pick("failed_items"),
                "unverified_items": pick("unverified_items"), "resources": pick("resources")})
        } else {
            Value::Null
        }}),
    )
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Revision-bound external work. Long-running container work never holds workspace.lock.
use crate::{
    config::FlowKind,
    engine,
    error::{Error, Result},
    flow, integration,
    process::ProcessReceipt,
    workspace::{self, Artifact, StageResult, StageStatus, Workspace},
};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    schema: String,
    request_sha256: String,
    exit_code: Option<i32>,
    elapsed_seconds: f64,
    peak_rss_kib: Option<u64>,
    timed_out: bool,
    cancelled: bool,
    log_limit_exceeded: bool,
    cleanup_verified: bool,
    stdout: PathBuf,
    stderr: PathBuf,
    stdout_sha256: String,
    stderr_sha256: String,
    cleanup_report: PathBuf,
    cleanup_sha256: String,
}

fn text(path: &Path) -> Result<String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| Error::new("PATH_ENCODING", "worker paths must be UTF-8"))
}

pub fn prepare(root: &Path, base: &str, retry_failed: bool) -> Result<Value> {
    let ws = Workspace::open(root, true, false)?;
    ws.require_base(base)?;
    let mut state = flow::load_checked(&ws, "HEAD")?;
    ws.validate_inputs(&state)?;
    if state.config.flow != FlowKind::Rusteda {
        return Err(Error::new(
            "WORKER_NOT_REQUIRED",
            "use run for native flows",
        ));
    }
    let stage = &state.stages[0];
    if stage.status == StageStatus::Passed {
        integration::validate_cached(&state)?;
        return Ok(
            json!({"schema":"rustlane.v1","ok":true,"status":"cached", "new_rev":state.manifest.revision,
                         "executed_steps":0,"cached_steps":1,"summary":flow::summary(&state)}),
        );
    }
    if stage.status == StageStatus::Running {
        return Err(Error::new(
            "INTERRUPTED_RUN",
            "a worker request is active; inspect or recover its exact ownership before retrying",
        ));
    }
    if stage.status == StageStatus::Failed && !retry_failed {
        return Err(Error::new(
            "FAILED_STEP",
            "inspect evidence and pass --retry-failed",
        ));
    }
    let parent = ws.root.join("steps/rtl2gds");
    fs::create_dir_all(&parent)?;
    let mut attempt = stage.attempts;
    let mut selected = None;
    for _ in 0..128 {
        attempt += 1;
        if attempt > 1000 {
            break;
        }
        let dir = parent.join(format!("attempt{attempt}"));
        match fs::create_dir(&dir) {
            Ok(()) => {
                selected = Some(dir);
                break;
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(e) => return Err(e.into()),
        }
    }
    let dir =
        selected.ok_or_else(|| Error::new("ATTEMPT_BUDGET", "no unused worker attempt slot"))?;
    let config = &state.config;
    let road = config.road.as_ref().unwrap();
    let program = engine::resolve_tool(&config.tools.python)?;
    let rustroad = engine::resolve_tool(&config.tools.rustroad)?;
    let timer = engine::resolve_tool(&config.tools.gnu_time)?;
    let podman = engine::resolve_tool(&config.tools.podman)?;
    let args = vec![
        text(&road.driver)?,
        "--project".into(),
        text(&road.project)?,
        "--manifest".into(),
        text(&road.manifest)?,
        "--pdk".into(),
        integration::tag(&config.pdk)?.into(),
        "--output".into(),
        text(&dir.join("output"))?,
        "--rustroad".into(),
        text(&rustroad)?,
        "--gnu-time".into(),
        text(&timer)?,
    ];
    let mut request = json!({"schema":"rustlane.worker.v1","workspace":ws.root,"base_rev":base,
        "stage":"rtl2gds","attempt":attempt,"program":program,"arguments":args,"cwd":dir,
        "podman":podman,"timeout_seconds":config.limits.timeout_secs,
        "memory_mib":config.limits.memory_mib,"max_log_bytes":config.limits.max_log_bytes,
        "environment":{"PYTHONDONTWRITEBYTECODE":"1","OMP_NUM_THREADS":"2","OPENBLAS_NUM_THREADS":"2"}});
    let id = format!("{:x}", Sha256::digest(serde_json::to_vec(&request)?));
    request["request_id"] = json!(id);
    request["unit"] = json!(format!("rusteda-worker-{}", &id[..24]));
    request["environment"]["RUSTEDA_WORKER_ID"] = json!(id);
    request["environment"]["RUSTEDA_OWNERSHIP"] = json!(dir.join("ownership"));
    let request_path = dir.join("request.json");
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&request_path)?;
    file.write_all(&serde_json::to_vec_pretty(&request)?)?;
    file.sync_all()?;
    let identity = workspace::identify(&request_path, 1024 * 1024)?;
    state.stages[0].status = StageStatus::Running;
    state.stages[0].attempts = attempt;
    state.stages[0].error = None;
    state.stages[0].result = Some(StageResult {
        artifacts: vec![Artifact {
            kind: "worker_request".into(),
            file: identity.clone(),
        }],
        evidence: json!({"request":request,"request_sha256":identity.sha256,"ownership":"external worker; completion requires explicit cleanup receipt"}),
        ..StageResult::default()
    });
    let revision = ws.commit(&mut state)?;
    Ok(
        json!({"schema":"rustlane.v1","ok":true,"status":"worker_required","new_rev":revision,
              "request":request_path,"request_sha256":identity.sha256}),
    )
}

pub fn request(root: &Path, rev: &str) -> Result<Value> {
    let ws = Workspace::open(root, false, false)?;
    let state = flow::load_checked(&ws, rev)?;
    // Recovery must remain possible after a source/tool changes. Only the
    // persisted request identity is needed to inspect and clean its ownership.
    if state.config.flow != FlowKind::Rusteda || state.stages[0].status != StageStatus::Running {
        return Err(Error::new("NO_ACTIVE_WORKER", "no active worker request"));
    }
    let r = state.stages[0]
        .result
        .as_ref()
        .ok_or_else(|| Error::new("CORRUPT_WORKSPACE", "worker result missing"))?;
    let a = r
        .artifacts
        .iter()
        .find(|a| a.kind == "worker_request")
        .ok_or_else(|| Error::new("CORRUPT_WORKSPACE", "worker request missing"))?;
    workspace::verify(&a.file, 1024 * 1024)?;
    Ok(
        json!({"schema":"rustlane.v1","ok":true,"rev":state.manifest.revision,
        "request":a.file.path,"request_sha256":a.file.sha256,"worker":r.evidence["request"]}),
    )
}

fn inside(path: &Path, parent: &Path) -> Result<PathBuf> {
    let path = fs::canonicalize(path)?;
    if !path.starts_with(parent) || !path.is_file() {
        return Err(Error::new(
            "WORKER_PROTOCOL",
            "receipt/log path escapes its attempt",
        ));
    }
    Ok(path)
}

pub fn finish(root: &Path, base: &str, receipt_path: &Path) -> Result<Value> {
    let ws = Workspace::open(root, true, false)?;
    ws.require_base(base)?;
    let mut state = flow::load_checked(&ws, "HEAD")?;
    let input_failure = ws.validate_inputs(&state).err();
    if state.config.flow != FlowKind::Rusteda || state.stages[0].status != StageStatus::Running {
        return Err(Error::new("NO_ACTIVE_WORKER", "no active worker to finish"));
    }
    let old = state.stages[0]
        .result
        .as_ref()
        .ok_or_else(|| Error::new("CORRUPT_WORKSPACE", "worker result missing"))?;
    let request_file = old
        .artifacts
        .iter()
        .find(|a| a.kind == "worker_request")
        .ok_or_else(|| Error::new("CORRUPT_WORKSPACE", "worker request missing"))?
        .file
        .clone();
    workspace::verify(&request_file, 1024 * 1024)?;
    let request = integration::read_json(&request_file.path, 1024 * 1024)?;
    let attempt = fs::canonicalize(request_file.path.parent().unwrap())?;
    let receipt_path = inside(receipt_path, &attempt)?;
    let receipt: Receipt =
        serde_json::from_value(integration::read_json(&receipt_path, 1024 * 1024)?)?;
    if receipt.schema != "rustlane.worker-receipt.v1"
        || receipt.request_sha256 != request_file.sha256
        || !receipt.elapsed_seconds.is_finite()
        || receipt.elapsed_seconds < 0.0
        || receipt.elapsed_seconds > state.config.limits.timeout_secs as f64 + 300.0
    {
        return Err(Error::new(
            "WORKER_PROTOCOL",
            "receipt does not match its request or time budget",
        ));
    }
    let mut result = old.clone();
    let mut bytes = 0;
    for (kind, path, wanted) in [
        ("worker_stdout", &receipt.stdout, &receipt.stdout_sha256),
        ("worker_stderr", &receipt.stderr, &receipt.stderr_sha256),
    ] {
        let path = inside(path, &attempt)?;
        let id = workspace::identify(&path, state.config.limits.max_log_bytes)?;
        bytes += id.bytes;
        if &id.sha256 != wanted {
            return Err(Error::new("WORKER_PROTOCOL", "worker log hash differs"));
        }
        result.artifacts.push(Artifact {
            kind: kind.into(),
            file: id,
        });
    }
    if bytes > state.config.limits.max_log_bytes {
        return Err(Error::new(
            "WORKER_PROTOCOL",
            "combined log budget exceeded",
        ));
    }
    let cleanup = inside(&receipt.cleanup_report, &attempt)?;
    let cleanup_id = workspace::identify(&cleanup, 1024 * 1024)?;
    if cleanup_id.sha256 != receipt.cleanup_sha256 {
        return Err(Error::new("WORKER_PROTOCOL", "cleanup report hash differs"));
    }
    let cleanup_report = integration::read_json(&cleanup, 1024 * 1024)?;
    if cleanup_report["schema"] != "rusteda.worker-cleanup.v1"
        || cleanup_report["request_id"] != request["request_id"]
        || cleanup_report["verified"] != receipt.cleanup_verified
        || !cleanup_report["errors"].is_array()
        || (receipt.cleanup_verified
            && (cleanup_report["scope"] != "inactive"
                || cleanup_report["containers"] != "absent"
                || !cleanup_report["errors"].as_array().unwrap().is_empty()))
    {
        return Err(Error::new(
            "WORKER_PROTOCOL",
            "cleanup report ownership or result differs from receipt",
        ));
    }
    if receipt
        .peak_rss_kib
        .is_some_and(|rss| rss == 0 || rss > state.config.limits.memory_mib * 1024)
    {
        return Err(Error::new(
            "WORKER_PROTOCOL",
            "invalid worker RSS measurement",
        ));
    }
    result.artifacts.push(Artifact {
        kind: "worker_cleanup".into(),
        file: cleanup_id,
    });
    result.artifacts.push(Artifact {
        kind: "worker_receipt".into(),
        file: workspace::identify(&receipt_path, 1024 * 1024)?,
    });
    result.processes.push(ProcessReceipt {
        program: request["program"].as_str().unwrap().into(),
        arguments: serde_json::from_value(request["arguments"].clone())?,
        elapsed_seconds: receipt.elapsed_seconds,
        peak_rss_kib: receipt.peak_rss_kib,
        exit_code: receipt.exit_code,
        timed_out: receipt.timed_out,
        log_limit_exceeded: receipt.log_limit_exceeded,
        stdout: receipt.stdout.clone(),
        stderr: receipt.stderr.clone(),
    });
    let mut failure = if !receipt.cleanup_verified {
        Some(Error::new(
            "CLEANUP_UNVERIFIED",
            "external worker ownership cleanup is not verified",
        ))
    } else if input_failure.is_some() {
        input_failure
    } else if receipt.exit_code != Some(0)
        || receipt.timed_out
        || receipt.cancelled
        || receipt.log_limit_exceeded
    {
        Some(Error::new(
            "WORKER_FAILED",
            "external worker failed, timed out or was cancelled",
        ))
    } else {
        None
    };
    if failure.is_none() {
        match integration::ingest(&state, &attempt.join("output"), &mut result) {
            Ok(gate) => result.evidence["gate"] = gate,
            Err(e) => failure = Some(e),
        }
    }
    result.evidence["cleanup_verified"] = json!(receipt.cleanup_verified);
    result.evidence["cancelled"] = json!(receipt.cancelled);
    let status = if failure.is_none() {
        StageStatus::Passed
    } else if receipt.cleanup_verified {
        StageStatus::Failed
    } else {
        StageStatus::Running
    };
    state.stages[0].status = status;
    state.stages[0].error = failure.as_ref().map(ToString::to_string);
    state.stages[0].result = Some(result);
    state.manifest.total_wall_seconds += receipt.elapsed_seconds;
    let revision = ws.commit(&mut state)?;
    Ok(
        json!({"schema":"rustlane.v1","ok":failure.is_none(),"new_rev":revision,
        "status":if failure.is_none(){"complete"}else if receipt.cleanup_verified{"failed"}else{"cleanup_unverified"},
        "error":failure.map(|e|json!({"code":e.code,"message":e.message})),"summary":flow::summary(&state)}),
    )
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

use crate::{
    config::{Config, FlowKind},
    engine,
    error::{Error, Result},
    workspace::{self, Manifest, Stage, StageResult, StageStatus, State, Workspace},
};
use serde_json::{Value, json};
use std::{fs, path::Path, time::Instant};

pub fn stages(config: &Config) -> Vec<Stage> {
    if config.flow == FlowKind::Rusteda {
        return vec![Stage {
            id: "rtl2gds".into(),
            operation: "RustROAD.RtlToGds".into(),
            status: StageStatus::Pending,
            attempts: 0,
            result: None,
            error: None,
        }];
    }
    let mut definitions = vec![
        ("synthesis".to_owned(), "RustSys.Synthesis".to_owned()),
        ("equivalence".into(), "RustSys.Equivalence".into()),
        ("sta_pre".into(), "RustSTA.PrePnR".into()),
    ];
    if config.flow == FlowKind::Classic {
        definitions.extend(
            config
                .physical_steps
                .iter()
                .map(|s| (s.id.clone(), format!("{}:{}", s.tool.name(), s.id))),
        );
        definitions.extend([
            (
                "equivalence_post".into(),
                "RustSys.EquivalencePostPhysical".into(),
            ),
            ("sta_post".into(), "RustSTA.PostPnR".into()),
        ]);
    }
    definitions
        .into_iter()
        .map(|(id, operation)| Stage {
            id,
            operation,
            status: StageStatus::Pending,
            attempts: 0,
            result: None,
            error: None,
        })
        .collect()
}

pub fn init(root: &Path, config_path: &Path, base: &str) -> Result<Value> {
    if base != "empty" {
        return Err(Error::new(
            "REVISION_CONFLICT",
            "init requires --base-rev empty",
        ));
    }
    let original_config = workspace::identify(config_path, crate::config::MAX_CONFIG_BYTES)?;
    let mut config = Config::load(config_path)?;
    workspace::verify(&original_config, crate::config::MAX_CONFIG_BYTES)?;
    let ws = Workspace::open(root, true, true)?;
    ws.require_base(base)?;
    let inputs = ws.capture_inputs(&mut config)?;
    let plan = stages(&config);
    let mut state = State {
        config,
        manifest: Manifest {
            schema: "rustlane.workspace.v1".into(),
            rustlane_version: env!("CARGO_PKG_VERSION").into(),
            revision: String::new(),
            original_config,
            inputs,
            total_wall_seconds: 0.0,
        },
        stages: plan,
    };
    let revision = ws.commit(&mut state)?;
    Ok(
        json!({"schema":"rustlane.v1","ok":true,"new_rev":revision,"workspace":ws.root,
        "status":"initialized","plan":state.stages,"limits":state.config.limits}),
    )
}

pub(crate) fn load_checked(ws: &Workspace, rev: &str) -> Result<State> {
    let state = ws.load(rev)?;
    let expected = stages(&state.config);
    if state.stages.len() != expected.len()
        || state
            .stages
            .iter()
            .zip(expected.iter())
            .any(|(a, b)| a.id != b.id || a.operation != b.operation)
    {
        return Err(Error::new(
            "CORRUPT_WORKSPACE",
            "stage sequence differs from resolved configuration",
        ));
    }
    Ok(state)
}

pub fn plan(root: &Path, rev: &str) -> Result<Value> {
    let ws = Workspace::open(root, false, false)?;
    let state = load_checked(&ws, rev)?;
    let plan: Vec<_> = state.stages.iter().map(|s| json!({"id":s.id,"operation":s.operation,"status":s.status,
        "capability":if s.operation=="RustROAD.RtlToGds" {"external_worker"} else if s.operation=="RustSTA.PostPnR" {"legacy_adapter_unverified"} else if s.operation.contains(':') {"caller_recipe_unverified"} else {"implemented"}})).collect();
    Ok(
        json!({"schema":"rustlane.v1","ok":true,"rev":state.manifest.revision,"flow":state.config.flow,"pdk":state.config.pdk,"steps":plan,
        "limitations":["physical recipes are caller-supplied and not automatically equivalent to LibreLane Classic", "flow rusteda delegates qualified extracted timing to RustROAD; generic classic recipes remain unqualified"]}),
    )
}

pub fn doctor(root: &Path, rev: &str) -> Result<Value> {
    let ws = Workspace::open(root, false, false)?;
    let state = load_checked(&ws, rev)?;
    let mut requested = if state.config.flow == FlowKind::Rusteda {
        vec![
            ("python", &state.config.tools.python),
            ("rustroad", &state.config.tools.rustroad),
            ("gnu_time", &state.config.tools.gnu_time),
            ("podman", &state.config.tools.podman),
        ]
    } else {
        vec![
            ("rustsys", &state.config.tools.rustsys),
            ("rusteda", &state.config.tools.rusteda),
            ("ruststa", &state.config.tools.ruststa),
        ]
    };
    for step in &state.config.physical_steps {
        let name = step.tool.name();
        if requested.iter().any(|(n, _)| *n == name) {
            continue;
        }
        let p = match step.tool {
            crate::config::PhysicalTool::Openroad => &state.config.tools.openroad,
            crate::config::PhysicalTool::Klayout => &state.config.tools.klayout,
            crate::config::PhysicalTool::Magic => &state.config.tools.magic,
            crate::config::PhysicalTool::Netgen => &state.config.tools.netgen,
        };
        requested.push((name, p));
    }
    let mut healthy = true;
    let checks: Vec<_> = requested.into_iter().map(|(name,p)| match engine::resolve_tool(p) {
        Ok(path) => json!({"tool":name,"availability":"available","path":path,"execution_verified":false}),
        Err(e) => { healthy=false; json!({"tool":name,"availability":"unavailable","code":e.code,"message":e.message}) }
    }).collect();
    let integrity = ws
        .validate_inputs(&state)
        .and_then(|_| crate::integration::validate_cached(&state));
    if integrity.is_err() {
        healthy = false;
    }
    let native_only = state.config.flow == FlowKind::SynthesisSta;
    healthy &= native_only || state.config.flow == FlowKind::Rusteda;
    Ok(
        json!({"schema":"rustlane.v1","ok":healthy,"rev":state.manifest.revision,"checks":checks,
        "input_integrity":integrity.as_ref().map(|_| "verified").unwrap_or("failed"),
        "input_diagnostic":integrity.err().map(|e| e.to_string()),
        "extracted_parasitic_sta":if state.config.flow==FlowKind::Rusteda {"qualified_producer_contract"} else {"legacy_adapter_unavailable"},"physical_recipes":if native_only {"not_requested"} else if state.config.flow==FlowKind::Rusteda {"qualified_rustroad_contract"} else {"unverified"},
        "scope":"filesystem/prerequisite inspection; actual tool compatibility requires execution"}),
    )
}

pub fn run(root: &Path, base: &str, until: Option<&str>, retry_failed: bool) -> Result<Value> {
    let begin = Instant::now();
    let ws = Workspace::open(root, true, false)?;
    ws.require_base(base)?;
    let mut state = load_checked(&ws, "HEAD")?;
    ws.validate_inputs(&state)?;
    if state.config.flow == FlowKind::Rusteda {
        return Err(Error::new(
            "EXTERNAL_WORKER_REQUIRED",
            "use prepare and finish, or the RustEDA dev flow runner",
        ));
    }
    let last = if let Some(target) = until {
        state
            .stages
            .iter()
            .position(|s| s.id == target)
            .ok_or_else(|| Error::new("UNKNOWN_STEP", target))?
    } else {
        state.stages.len() - 1
    };
    if state.stages[..=last]
        .iter()
        .any(|s| s.operation == "RustSTA.PostPnR")
    {
        return Err(Error::new(
            "CAPABILITY_UNAVAILABLE",
            "The generic Classic adapter is not qualified for post-route signoff. Use flow rusteda for the qualified producer, or select an explicit partial stage.",
        ));
    }
    let mut executed = 0_usize;
    let mut cached = 0_usize;
    for index in 0..=last {
        let stage = &state.stages[index];
        match stage.status {
            StageStatus::Passed => {
                cached += 1;
                continue;
            }
            StageStatus::Running => {
                return Err(Error::new(
                    "INTERRUPTED_RUN",
                    format!(
                        "{} was interrupted. Cleanup is not attested; inspect owned processes and attempt logs before starting a fresh workspace.",
                        stage.id
                    ),
                ));
            }
            StageStatus::Failed if !retry_failed => {
                return Err(Error::new(
                    "FAILED_STEP",
                    format!(
                        "{} failed; inspect its receipt and explicitly pass --retry-failed",
                        stage.id
                    ),
                ));
            }
            _ => (),
        }
        if stage.attempts >= 1000 {
            return Err(Error::new(
                "ATTEMPT_BUDGET",
                "maximum stage attempts reached",
            ));
        }
        state.stages[index].attempts += 1;
        state.stages[index].status = StageStatus::Running;
        state.stages[index].error = None;
        state.stages[index].result = None;
        let stage_id = state.stages[index].id.clone();
        let operation = state.stages[index].operation.clone();
        let attempts = state.stages[index].attempts;
        ws.commit(&mut state)?;
        let parent = ws.root.join("steps").join(&stage_id);
        fs::create_dir_all(&parent)?;
        let attempt_dir = parent.join(format!("attempt{attempts}"));
        fs::create_dir(&attempt_dir)?;
        let mut result = StageResult::default();
        let outcome = engine::execute(&operation, &state, &attempt_dir, &mut result);
        state.stages[index].result = Some(result);
        executed += 1;
        if let Err(e) = outcome {
            let cleanup_unknown = matches!(
                e.code,
                "PROCESS_CLEANUP_TIMEOUT"
                    | "PROCESS_OWNERSHIP_LOST"
                    | "PROCESS_IO_ERROR"
                    | "PROCESS_PIPE_FAILED"
            );
            state.stages[index].status = if cleanup_unknown {
                StageStatus::Running
            } else {
                StageStatus::Failed
            };
            state.stages[index].error = Some(e.to_string());
            state.manifest.total_wall_seconds += begin.elapsed().as_secs_f64();
            let revision = ws.commit(&mut state)?;
            return Ok(
                json!({"schema":"rustlane.v1","ok":false,"new_rev":revision,"status":if cleanup_unknown {"cleanup_unverified"} else {"failed"},"step":stage_id,
                "error":{"code":e.code,"message":e.message},"result":state.stages[index].result,
                "elapsed_seconds":begin.elapsed().as_secs_f64(),"drc_status":"unchecked","lvs_status":"unchecked"}),
            );
        }
        state.stages[index].status = StageStatus::Passed;
        // Completion is journaled before proceeding; no existing side-table is edited.
        ws.commit(&mut state)?;
    }
    if executed > 0 {
        state.manifest.total_wall_seconds += begin.elapsed().as_secs_f64();
        ws.commit(&mut state)?;
    }
    let complete = state.stages.iter().all(|s| s.status == StageStatus::Passed);
    Ok(
        json!({"schema":"rustlane.v1","ok":true,"new_rev":state.manifest.revision,"status":if complete {"complete"} else {"partial"},
        "executed_steps":executed,"cached_steps":cached,"elapsed_seconds":begin.elapsed().as_secs_f64(),"summary":summary(&state)}),
    )
}

pub fn report(root: &Path, rev: &str) -> Result<Value> {
    let ws = Workspace::open(root, false, false)?;
    let state = load_checked(&ws, rev)?;
    let integrity = ws
        .validate_inputs(&state)
        .and_then(|_| crate::integration::validate_cached(&state));
    let mut value = json!({"schema":"rustlane.v1","ok":integrity.is_ok(),"rev":state.manifest.revision,
        "workspace":ws.root,"summary":summary(&state),"stages":state.stages,"inputs":state.manifest.inputs});
    if let Err(e) = integrity {
        value["integrity_error"] = json!({"code":e.code,"message":e.message});
    }
    Ok(value)
}

pub(crate) fn summary(state: &State) -> Value {
    if state.config.flow == FlowKind::Rusteda {
        let result = state.stages.first().and_then(|s| s.result.as_ref());
        let gate = result.map(|r| &r.evidence["gate"]);
        return json!({"design_name":state.config.design_name,"pdk":state.config.pdk,"flow":state.config.flow,
            "complete":state.stages.iter().all(|s|s.status==StageStatus::Passed),
            "completed_steps":state.stages.iter().filter(|s|s.status==StageStatus::Passed).count(),"total_steps":state.stages.len(),
            "qualification_status":gate.and_then(|g|g.get("qualification_status")).unwrap_or(&Value::Null),
            "qualification_scope":gate.and_then(|g|g.get("scope")).unwrap_or(&Value::Null),
            "declared_deviations":gate.and_then(|g|g.get("declared_deviations")).unwrap_or(&Value::Null),
            "qualification":gate,"drc_status":gate.and_then(|g|g.pointer("/statuses/drc_magic/status")).unwrap_or(&Value::Null),"lvs_status":gate.and_then(|g|g.pointer("/statuses/lvs_netgen/status")).unwrap_or(&Value::Null),"timing_metrics":gate.and_then(|g|g.pointer("/timing_sources/native/metrics")),"analysis_scope":gate.and_then(|g|g.pointer("/timing_sources/native/scope")),"unconstrained_endpoints":gate.and_then(|g|g.pointer("/timing_sources/native/completeness/unconstrained_endpoints")),"recorded_run_wall_seconds":state.manifest.total_wall_seconds,
            "measurement_scope":"external worker receipt; maximum observed individual worker/tool/container RSS, not aggregate family RSS"});
    }
    let timing = state
        .stages
        .iter()
        .rev()
        .find(|s| s.operation.starts_with("RustSTA.") && s.status == StageStatus::Passed)
        .and_then(|s| s.result.as_ref())
        .map(|r| &r.evidence);
    let equivalence = state
        .stages
        .iter()
        .rev()
        .find(|s| s.operation.starts_with("RustSys.Equivalence") && s.status == StageStatus::Passed)
        .and_then(|s| s.result.as_ref())
        .map(|r| &r.evidence);
    let completed = state
        .stages
        .iter()
        .filter(|s| s.status == StageStatus::Passed)
        .count();
    let peak = state
        .stages
        .iter()
        .filter_map(|s| s.result.as_ref())
        .flat_map(|r| &r.processes)
        .filter_map(|p| p.peak_rss_kib)
        .max();
    json!({"design_name":state.config.design_name,"pdk":state.config.pdk,"flow":state.config.flow,
        "completed_steps":completed,"total_steps":state.stages.len(),"complete":completed==state.stages.len(),
        "synthesis_provider":"RustSys","timing_provider":"RustSTA","equivalence_verdict":equivalence.and_then(|v|v.get("verdict")),
        "timing_metrics":timing.and_then(|v|v.get("metrics")),"analysis_scope":timing.and_then(|v|v.get("scope")),
        "unconstrained_endpoints":timing.and_then(|v|v.pointer("/completeness/unconstrained_endpoints")),
        "timing_completeness":timing.and_then(|v|v.get("completeness")),"timing_verification":timing.and_then(|v|v.get("verification")),
        "drc_status":"unchecked","lvs_status":"unchecked","extracted_parasitic_sta":"unsupported",
        "peak_direct_child_rss_kib":peak,"recorded_run_wall_seconds":state.manifest.total_wall_seconds,
        "measurement_scope":"child RSS is the largest direct tool peak, not aggregate process-family memory; recorded wall time excludes final checkpoint"})
}

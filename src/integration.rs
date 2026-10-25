//! Bounded file protocol for the qualified RustROAD producer. No EDA algorithm lives here.
use crate::{
    config::{Config, Pdk},
    engine,
    error::{Error, Result},
    workspace::{self, Artifact, FileIdentity, StageResult, State},
};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    io::Read,
    path::{Path, PathBuf},
};

pub const MAX_FILES: usize = 1024;
pub const MAX_SUMMARY: u64 = 16 * 1024 * 1024;
pub const GATES: &[&str] = &[
    "inputs_frozen",
    "implementation",
    "cell_policy",
    "cec_prelayout",
    "hold_closure",
    "antenna_native",
    "extraction",
    "timing_setup",
    "timing_hold",
    "mpw_min_period",
    "drv",
    "timing_accuracy",
    "gds_xor",
    "drc_magic",
    "drc_klayout",
    "antenna_deck",
    "lvs_netgen",
    "lvs_klayout",
    "cec_final",
    "cec_powered",
    "simulation",
    "cell_proofs",
    "artifact_binding",
    "determinism",
    "controls",
];
const GLOBAL: &[&str] = &[
    "tools_identity",
    "openroad_image",
    "design_manifest",
    "budget_floors",
    "manifest_provenance",
    "sdc_rendering",
    "clock_period",
];

fn bad(message: impl Into<String>) -> Error {
    Error::new("RUSTROAD_PROTOCOL", message)
}

pub fn tag(pdk: &Pdk) -> Result<&'static str> {
    match pdk {
        Pdk::Sky130 => Ok("sky130"),
        Pdk::Gf180Seven => Ok("gf180"),
        Pdk::Gf180Nine => Err(bad("the qualified RustROAD flow covers GF180 7T, not 9T")),
    }
}

pub fn validate_config(config: &Config) -> Result<()> {
    tag(&config.pdk)?;
    if config.road.is_none()
        || !config.verilog_files.is_empty()
        || !config.libraries.is_empty()
        || !config.sdc_file.as_os_str().is_empty()
        || !config.physical_steps.is_empty()
        || !config.collateral.is_empty()
        || !config.defines.is_empty()
        || !config.parameters.is_empty()
    {
        return Err(bad(
            "flow rusteda uses one authoritative road manifest; native input/recipe overrides are not accepted",
        ));
    }
    let l = &config.limits;
    if !(1..=86400).contains(&l.timeout_secs)
        || !(8192..=16384).contains(&l.memory_mib)
        || !(1024..=64 * 1024 * 1024).contains(&l.max_log_bytes)
        || !(1..=16 * 1024 * 1024 * 1024).contains(&l.max_file_bytes)
        || l.max_input_bytes < l.max_file_bytes
        || l.max_input_bytes > 64 * 1024 * 1024 * 1024
        || l.threads != 2
    {
        return Err(bad(
            "invalid external-worker resource bounds (8 GiB worker memory minimum; qualified producer requires two tool threads)",
        ));
    }
    Ok(())
}

pub(crate) fn read_json(path: &Path, limit: u64) -> Result<Value> {
    let file = fs::File::open(path)?;
    if !file.metadata()?.is_file() || file.metadata()?.len() > limit {
        return Err(bad("JSON input exceeds budget"));
    }
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(bad("JSON input grew beyond budget"));
    }
    Ok(serde_json::from_slice(&bytes)?)
}

fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| bad(format!("missing string {key}")))
}

fn add(files: &mut BTreeSet<PathBuf>, path: &Path) -> Result<()> {
    let path = fs::canonicalize(path)?;
    if !path.is_file() {
        return Err(bad("dependency is not a regular file"));
    }
    files.insert(path);
    if files.len() > MAX_FILES {
        return Err(bad("dependency file budget exceeded"));
    }
    Ok(())
}

fn tree(files: &mut BTreeSet<PathBuf>, root: &Path, python_only: bool) -> Result<()> {
    let mut pending = vec![(root.to_owned(), 0)];
    let mut visited = 0;
    while let Some((dir, depth)) = pending.pop() {
        if depth > 32 {
            return Err(bad("dependency depth budget exceeded"));
        }
        for entry in fs::read_dir(&dir)? {
            visited += 1;
            if visited > 4096 {
                return Err(bad("dependency directory budget exceeded"));
            }
            let entry = entry?;
            let p = entry.path();
            let ty = entry.file_type()?;
            if entry.file_name() == "__pycache__" {
                continue;
            }
            if ty.is_dir() {
                pending.push((p, depth + 1));
            } else if ty.is_file() || ty.is_symlink() {
                if p.is_dir() {
                    return Err(bad("symlink directories are not followed"));
                }
                if !python_only || p.extension().is_some_and(|e| e == "py") {
                    add(files, &p)?;
                }
            }
        }
    }
    Ok(())
}

fn project_file(project: &Path, value: &Value) -> Result<PathBuf> {
    let relative = Path::new(text(value, "path")?);
    if !crate::config::relative_output(relative) {
        return Err(bad("producer path must be project-relative"));
    }
    let path = fs::canonicalize(project.join(relative))?;
    if !path.starts_with(project) || !path.is_file() {
        return Err(bad("producer input escapes project"));
    }
    Ok(path)
}

/// Capture scripts, fixture definitions, pinned host tools and selected PDK data.
/// The producer reads its original tree, whose bytes and captured copies are both checked.
pub fn input_paths(config: &Config) -> Result<Vec<PathBuf>> {
    let road = config
        .road
        .as_ref()
        .ok_or_else(|| bad("road configuration missing"))?;
    let design = read_json(&road.manifest, 2 * 1024 * 1024)?;
    let pdk_tag = tag(&config.pdk)?;
    if text(&design, "schema")? != "rustroad.design.v3"
        || text(&design, "name")? != config.design_name
        || design.pointer(&format!("/pdks/{pdk_tag}")).is_none()
    {
        return Err(bad(
            "design identity or PDK differs from its producer manifest",
        ));
    }
    let mut files = BTreeSet::new();
    add(&mut files, &road.driver)?;
    add(&mut files, &road.manifest)?;
    tree(&mut files, &road.project.join("scripts"), true)?;
    tree(&mut files, &road.project.join("fixtures"), false)?;
    let tools_path = project_file(&road.project, &design["tools"])?;
    let tools = read_json(&tools_path, 2 * 1024 * 1024)?;
    for spec in tools["tools"]
        .as_object()
        .ok_or_else(|| bad("tool manifest missing"))?
        .values()
    {
        if let Some(p) = spec.get("path").and_then(Value::as_str) {
            let p = Path::new(p);
            add(
                &mut files,
                &if p.is_absolute() {
                    p.to_owned()
                } else {
                    road.project.join(p)
                },
            )?;
        }
    }
    let pdk_path = project_file(&road.project, &design["signoff_manifest"])?;
    let manifest = read_json(&pdk_path, 2 * 1024 * 1024)?;
    let pdk = &manifest["pdks"][pdk_tag];
    let pdk_root = Path::new(text(pdk, "host_root")?);
    for spec in pdk["files"]
        .as_object()
        .ok_or_else(|| bad("PDK files missing"))?
        .values()
    {
        let rel = Path::new(text(spec, "path")?);
        if !crate::config::relative_output(rel) {
            return Err(bad("unsafe PDK file path"));
        }
        add(&mut files, &pdk_root.join(rel))?;
    }
    for spec in pdk["trees"]
        .as_object()
        .ok_or_else(|| bad("PDK trees missing"))?
        .values()
    {
        let rel = Path::new(text(spec, "path")?);
        if !crate::config::relative_output(rel) {
            return Err(bad("unsafe PDK tree path"));
        }
        tree(&mut files, &pdk_root.join(rel), false)?;
    }
    for tool in [
        &config.tools.python,
        &config.tools.rustroad,
        &config.tools.gnu_time,
        &config.tools.podman,
    ] {
        add(&mut files, &engine::resolve_tool(tool)?)?;
    }
    Ok(files.into_iter().collect())
}

fn empty_array(value: &Value) -> bool {
    value.as_array().is_some_and(Vec::is_empty)
}

/// Only the qualified producer's complete gate, never an exit code or a single DRC count.
pub fn ingest(state: &State, output: &Path, result: &mut StageResult) -> Result<Value> {
    let path = output.join("summary.json");
    let summary = read_json(&path, MAX_SUMMARY)?;
    let pdk_tag = tag(&state.config.pdk)?;
    if text(&summary, "schema")? != "rustroad.rtl2gds.v1"
        || !matches!(text(&summary, "status")?, "passed" | "deviation_declared")
        || summary["exit_code"].as_i64() != Some(0)
        || !empty_array(&summary["failed_items"])
        || !empty_array(&summary["unverified_items"])
        || summary.pointer("/design/name").and_then(Value::as_str)
            != Some(&state.config.design_name)
        || summary["requested_pdks"] != json!([pdk_tag])
    {
        return Err(bad(
            "producer did not complete the requested design/PDK gate",
        ));
    }
    let advertised: BTreeSet<_> = summary["gating"]
        .as_array()
        .ok_or_else(|| bad("gating list missing"))?
        .iter()
        .filter_map(Value::as_str)
        .collect();
    if advertised != GATES.iter().copied().collect()
        || summary["gating"].as_array().unwrap().len() != GATES.len()
    {
        return Err(bad("producer gate set differs"));
    }
    for name in GLOBAL {
        if summary
            .pointer(&format!("/global_statuses/{name}/status"))
            .and_then(Value::as_str)
            != Some("passed")
        {
            return Err(bad(format!("global gate {name} did not pass")));
        }
    }
    let pdks = summary["pdks"]
        .as_array()
        .ok_or_else(|| bad("PDK result missing"))?;
    if pdks.len() != 1 || text(&pdks[0], "pdk")? != pdk_tag {
        return Err(bad("unexpected PDK result set"));
    }
    let statuses = &pdks[0]["statuses"];
    for name in GATES {
        let status = statuses
            .pointer(&format!("/{name}/status"))
            .and_then(Value::as_str);
        if status != Some("passed")
            && !(*name == "mpw_min_period" && status == Some("not_applicable"))
            && !(*name == "timing_accuracy" && status == Some("deviation_declared"))
        {
            return Err(bad(format!("gate {name} did not pass")));
        }
    }
    let declared = statuses
        .pointer("/timing_accuracy/status")
        .and_then(Value::as_str)
        == Some("deviation_declared");
    if (summary["status"] == "deviation_declared") != declared
        || summary["declared_deviations"]
            .as_array()
            .is_none_or(|a| a.is_empty() == declared)
    {
        return Err(bad("declaration status and evidence disagree"));
    }
    let design_manifest = read_json(
        &state.config.road.as_ref().unwrap().manifest,
        2 * 1024 * 1024,
    )?;
    let clocked = !design_manifest
        .pointer("/clock/port")
        .unwrap_or(&Value::Null)
        .is_null();
    let mut sources = serde_json::Map::new();
    for source in ["native", "openrcx"] {
        let file = output
            .join("receipts")
            .join(format!("{pdk_tag}_final_{source}_run_gating"))
            .join("stdout.txt");
        let report = read_json(&file, MAX_SUMMARY)?;
        let scope = &report["scope"];
        let corners: BTreeSet<_> = scope["corners"]
            .as_array()
            .ok_or_else(|| bad("timing corners missing"))?
            .iter()
            .filter_map(Value::as_str)
            .collect();
        if report["ok"] != true
            || scope["wire_model"] != "spef_rc"
            || scope["parasitics"] != "spef"
            || scope["precision"] != "f64"
            || scope["backend"] != "cpu"
            || corners != ["tt", "ss", "ff"].into_iter().collect()
            || scope["corners"].as_array().unwrap().len() != 3
            || scope["clock_model"] != if clocked { "propagated" } else { "ideal" }
            || !scope["physical_clock_domains"]
                .as_u64()
                .is_some_and(|n| if clocked { n > 0 } else { n == 0 })
            || report
                .pointer("/completeness/unconstrained_endpoints")
                .and_then(Value::as_u64)
                != Some(0)
            || report
                .pointer("/completeness/unconstrained_inputs")
                .and_then(Value::as_u64)
                != Some(0)
        {
            return Err(bad(
                "timing scope is incomplete or differs from the qualified CPU/f64/SPEF contract",
            ));
        }
        let matrix = statuses
            .pointer(&format!("/timing_accuracy/by_spef/{source}/corners"))
            .and_then(Value::as_object)
            .ok_or_else(|| bad("timing parity matrix missing"))?;
        if matrix.keys().map(String::as_str).collect::<BTreeSet<_>>() != corners {
            return Err(bad("timing parity corner coverage differs"));
        }
        for row in matrix.values() {
            if row.pointer("/endpoints/status").and_then(Value::as_str) != Some("passed")
                || !row
                    .pointer("/endpoints/max_difference_ns")
                    .and_then(Value::as_f64)
                    .is_some_and(|d| d.is_finite() && (0.0..=0.001).contains(&d))
                || row.pointer("/drv/status").and_then(Value::as_str) != Some("passed")
            {
                return Err(bad("ordinary timing parity did not meet the 1 ps contract"));
            }
            match row.pointer("/pulse/status").and_then(Value::as_str) {
                Some("passed")
                    if row
                        .pointer("/pulse/max_difference_ns")
                        .and_then(Value::as_f64)
                        .is_some_and(|d| d.is_finite() && (0.0..=0.001).contains(&d)) => {}
                Some("deviation_declared")
                    if row
                        .pointer("/pulse/declared_deviations")
                        .and_then(Value::as_array)
                        .is_some_and(|a| !a.is_empty()) => {}
                _ => {
                    return Err(bad(
                        "pulse parity is missing, failed or lacks its declaration",
                    ));
                }
            }
        }
        let id = workspace::identify(&file, MAX_SUMMARY)?;
        capture_bound(state, output, &format!("{source}_timing"), id, result)?;
        sources.insert(source.into(),json!({"scope":scope,"completeness":report["completeness"],"metrics":report["metrics"]}));
    }
    let expected_manifest = state.config.road.as_ref().unwrap().manifest.clone();
    let manifest_identity =
        workspace::identify(&expected_manifest, state.config.limits.max_file_bytes)?;
    if summary
        .pointer("/design/manifest_sha256")
        .and_then(Value::as_str)
        != Some(&manifest_identity.sha256)
    {
        return Err(bad("result is for a different design manifest"));
    }
    let binding_path = output.join(format!("{pdk_tag}_binding.json"));
    let binding = read_json(&binding_path, MAX_SUMMARY)?;
    if binding["digest"] != pdks[0]["binding"]["digest"]
        || binding.pointer("/revision/dirty") != Some(&Value::Bool(false))
    {
        return Err(bad("producer binding identity differs or source was dirty"));
    }
    let entries = binding["artifacts"]
        .as_object()
        .ok_or_else(|| bad("binding artifacts missing"))?;
    if entries.len() > 512 {
        return Err(bad("artifact count exceeds budget"));
    }
    for required in [
        "gds",
        "def",
        "netlist.final",
        "spef.native.tt",
        "spef.native.ss",
        "spef.native.ff",
        "spef.openrcx.tt",
        "spef.openrcx.ss",
        "spef.openrcx.ff",
    ] {
        if !entries.contains_key(required) {
            return Err(bad(format!("missing artifact {required}")));
        }
    }
    for (name, entry) in entries {
        let id = FileIdentity {
            path: PathBuf::from(text(entry, "path")?),
            sha256: text(entry, "sha256")?.into(),
            bytes: entry["bytes"]
                .as_u64()
                .ok_or_else(|| bad("missing artifact size"))?,
        };
        capture_bound(state, output, name, id, result)?;
    }
    // Declarations depend on additional original observations/circuit records.
    let mut declaration_count = 0;
    for source in ["native", "openrcx"] {
        for corner in ["tt", "ss", "ff"] {
            let key = format!(
                "/timing_accuracy/by_spef/{source}/corners/{corner}/pulse/declared_deviations"
            );
            if let Some(rows) = statuses.pointer(&key).and_then(Value::as_array) {
                if rows.len() > 64 {
                    return Err(bad("declaration count budget exceeded"));
                }
                for row in rows {
                    declaration_count += 1;
                    if row.pointer("/evidence/valid") != Some(&Value::Bool(true)) {
                        return Err(bad("unproven declaration"));
                    }
                    let artifacts = row
                        .pointer("/evidence/proof/artifacts")
                        .and_then(Value::as_array)
                        .ok_or_else(|| bad("declaration artifacts missing"))?;
                    if !(5..=64).contains(&artifacts.len()) {
                        return Err(bad("declaration artifact budget exceeded"));
                    }
                    for a in artifacts {
                        let p = PathBuf::from(text(a, "path")?);
                        let id = workspace::identify(&p, 64 * 1024 * 1024)?;
                        if id.sha256 != text(a, "sha256")? {
                            return Err(bad("declaration artifact changed"));
                        }
                        capture_bound(state, output, "mpw_evidence", id, result)?;
                    }
                }
            }
        }
    }
    if declared && declaration_count == 0 {
        return Err(bad("declared deviation has no per-row evidence"));
    }
    for (kind, p) in [("road_summary", path), ("road_binding", binding_path)] {
        let id = workspace::identify(&p, MAX_SUMMARY)?;
        capture_bound(state, output, kind, id, result)?;
    }
    Ok(
        json!({"qualification_status":summary["status"],"scope":summary["scope"],
        "design":summary["design"],"pdk":pdk_tag,"statuses":statuses,
        "timing_matrix":pdks[0]["details"]["timing_matrix"],"resources":summary["resources"],
        "declared_deviations":summary["declared_deviations"],"source_revision":summary["revision"],
        "binding_digest":binding["digest"],"timing_sources":sources}),
    )
}

/// Reapply the current acceptance contract to a saved result on cache reuse.
pub fn validate_cached(state: &State) -> Result<()> {
    if state.config.flow != crate::config::FlowKind::Rusteda {
        return Ok(());
    }
    let Some(stage) = state
        .stages
        .first()
        .filter(|s| s.status == crate::workspace::StageStatus::Passed)
    else {
        return Ok(());
    };
    let path = stage
        .result
        .as_ref()
        .and_then(|r| r.artifacts.iter().find(|a| a.kind == "road_summary"))
        .ok_or_else(|| bad("completed worker lacks its producer summary"))?
        .file
        .path
        .parent()
        .ok_or_else(|| bad("producer summary has no directory"))?;
    ingest(state, path, &mut StageResult::default()).map(|_| ())
}

fn capture_bound(
    state: &State,
    output: &Path,
    kind: &str,
    id: FileIdentity,
    result: &mut StageResult,
) -> Result<()> {
    let path = fs::canonicalize(&id.path)?;
    let output = fs::canonicalize(output)?;
    let external = state
        .manifest
        .inputs
        .iter()
        .find(|i| i.original == path || i.captured.path == path);
    if !path.starts_with(&output) && external.is_none() {
        return Err(bad(format!(
            "unbound external dependency {}",
            path.display()
        )));
    }
    if external.is_some_and(|i| i.captured.sha256 != id.sha256 || i.captured.bytes != id.bytes) {
        return Err(bad("producer consumed a changed dependency"));
    }
    workspace::verify(&id, state.config.limits.max_file_bytes)?;
    if path.starts_with(output) && !result.artifacts.iter().any(|a| a.file.path == path) {
        if result.artifacts.len() >= 1024 {
            return Err(bad("captured artifact budget exceeded"));
        }
        result.artifacts.push(Artifact {
            kind: kind.into(),
            file: FileIdentity { path, ..id },
        });
    }
    Ok(())
}

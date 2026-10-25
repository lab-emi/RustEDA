//! Orchestrator contracts only. These fixtures do not validate EDA algorithms;
//! actual dual-PDK/external-oracle checks live in scripts/verify_engines.py.
use rustlane::{
    config::Config,
    flow,
    workspace::{StageStatus, Workspace},
};
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
};

fn fixture() -> (tempfile::TempDir, PathBuf) {
    let parent = Path::new(env!("CARGO_MANIFEST_DIR")).join(".work/test-tmp");
    fs::create_dir_all(&parent).unwrap();
    let root = tempfile::tempdir_in(parent).unwrap();
    fs::write(
        root.path().join("top.v"),
        "module top(input a, output y); assign y=a; endmodule\n",
    )
    .unwrap();
    fs::write(root.path().join("cells.lib"), "library(dummy){}\n").unwrap();
    fs::write(
        root.path().join("top.sdc"),
        "set_max_delay 1 -from [all_inputs] -to [all_outputs]\n",
    )
    .unwrap();
    let config = root.path().join("config.json");
    fs::write(&config, serde_json::to_vec(&json!({"design_name":"top","pdk":"sky130_fd_sc_hd",
        "verilog_files":["top.v"],"libraries":[{"path":"cells.lib","corner":"tt"}],"sdc_file":"top.sdc",
        "tools":{"rustsys":"a_tool_that_does_not_exist_rustlane"}})).unwrap()).unwrap();
    (root, config)
}

#[test]
fn immutable_tables_roundtrip_and_conflicts_preserve_head() {
    let (root, config) = fixture();
    let ws = root.path().join("ws with spaces");
    assert_eq!(
        flow::init(&ws, &config, "empty").unwrap()["new_rev"],
        "rev0"
    );
    let current = Workspace::open(&ws, false, false).unwrap();
    let state = current.load("HEAD").unwrap();
    assert_eq!(state.manifest.inputs.len(), 3);
    assert_eq!(state.stages.len(), 3);
    assert!(state.config.verilog_files[0].starts_with(ws.canonicalize().unwrap().join("inputs")));
    assert!(ws.join("revisions/rev0/config.rbin").is_file());
    assert!(ws.join("revisions/rev0/manifest.rbin").is_file());
    assert!(ws.join("revisions/rev0/results.rbin").is_file());
    assert_eq!(
        current.require_base("rev1").unwrap_err().code,
        "REVISION_CONFLICT"
    );
    assert_eq!(current.head().unwrap(), "rev0");
    drop(current);
    assert_eq!(
        flow::init(&ws, &config, "empty").unwrap_err().code,
        "REVISION_CONFLICT"
    );
}

#[test]
fn shared_queries_exclude_mutating_lock() {
    let (root, config) = fixture();
    let ws = root.path().join("ws");
    flow::init(&ws, &config, "empty").unwrap();
    let reader = Workspace::open(&ws, false, false).unwrap();
    let reader2 = Workspace::open(&ws, false, false).unwrap();
    assert_eq!(
        Workspace::open(&ws, true, false).unwrap_err().code,
        "WORKSPACE_BUSY"
    );
    drop(reader);
    drop(reader2);
    assert!(Workspace::open(&ws, true, false).is_ok());
}

#[test]
fn unsupported_config_and_escaping_outputs_are_diagnosed() {
    let (root, config) = fixture();
    let mut value: serde_json::Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
    value["RUN_UNSUPPORTED_CONSTRAINT"] = json!(true);
    fs::write(&config, serde_json::to_vec(&value).unwrap()).unwrap();
    assert_eq!(Config::load(&config).unwrap_err().code, "CONFIG_ERROR");
    value
        .as_object_mut()
        .unwrap()
        .remove("RUN_UNSUPPORTED_CONSTRAINT");
    value["flow"] = json!("classic");
    value["physical_steps"] = json!([{"id":"place","tool":"openroad","script":"top.sdc","arguments":["{driver}"],
        "outputs":[{"kind":"netlist","path":"../escaped.v"},{"kind":"spef","path":"a.spef"},{"kind":"gds","path":"a.gds"}]}]);
    fs::write(&config, serde_json::to_vec(&value).unwrap()).unwrap();
    assert_eq!(Config::load(&config).unwrap_err().code, "CONFIG_ERROR");
    assert!(!root.path().join("escaped.v").exists());
}

#[test]
fn changed_source_refuses_resume_without_advancing_head() {
    let (root, config) = fixture();
    let ws = root.path().join("ws");
    flow::init(&ws, &config, "empty").unwrap();
    fs::write(root.path().join("top.v"), "changed input").unwrap();
    assert_eq!(
        flow::run(&ws, "rev0", None, false).unwrap_err().code,
        "INPUT_CHANGED"
    );
    let reader = Workspace::open(&ws, false, false).unwrap();
    assert_eq!(reader.head().unwrap(), "rev0");
}

#[test]
fn unavailable_engine_commits_failure_with_unknown_metrics() {
    let (root, config) = fixture();
    let ws = root.path().join("ws");
    flow::init(&ws, &config, "empty").unwrap();
    let result = flow::run(&ws, "rev0", None, false).unwrap();
    assert_eq!(result["ok"], false);
    assert_eq!(result["error"]["code"], "TOOL_UNAVAILABLE");
    assert_eq!(result["new_rev"], "rev2");
    let report = flow::report(&ws, "HEAD").unwrap();
    assert!(report["summary"]["timing_metrics"].is_null());
    assert!(report["summary"]["unconstrained_endpoints"].is_null());
    assert_eq!(report["summary"]["drc_status"], "unchecked");
    assert_eq!(
        flow::run(&ws, "rev2", None, false).unwrap_err().code,
        "FAILED_STEP"
    );
}

#[test]
fn checksum_corruption_is_diagnosed_before_use() {
    let (root, config) = fixture();
    let ws = root.path().join("ws");
    flow::init(&ws, &config, "empty").unwrap();
    let table = ws.join("revisions/rev0/results.rbin");
    let mut bytes = fs::read(&table).unwrap();
    bytes[48] ^= 1;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&table, fs::Permissions::from_mode(0o600)).unwrap();
    }
    fs::write(&table, bytes).unwrap();
    assert_eq!(
        flow::report(&ws, "HEAD").unwrap_err().code,
        "CORRUPT_WORKSPACE"
    );
}

#[cfg(unix)]
#[test]
fn zero_exit_undecided_cec_cannot_become_a_success() {
    use std::os::unix::fs::PermissionsExt;
    let (root, config) = fixture();
    let ws = root.path().join("ws");
    let program = root.path().join("fake engine with spaces");
    fs::write(&program,r##"#!/bin/sh
mode="$3"
out=''
report=''
while [ "$#" -gt 0 ]; do
 case "$1" in
  --out) out="$2"; shift 2;;
  --report) report="$2"; shift 2;;
  *) shift;;
 esac
done
if [ "$mode" = synth ]; then
 printf '%s\n' 'module top(input a, output y); assign y=a; endmodule' > "$out"
 value='{"format":"rustsys-synth-report","format_version":1,"warnings":[]}'
else
 value='{"format":"rustsys-check-synth-report","format_version":2,"verdict":"undecided","internal_error":false,"errors":[],"mismatches":[],"undecided":["test budget reached"]}'
fi
printf '%s\n' "$value" > "$report"
printf '%s\n' "$value"
"##).unwrap();
    fs::set_permissions(&program, fs::Permissions::from_mode(0o755)).unwrap();
    let mut value: serde_json::Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
    value["tools"]["rustsys"] = json!(program);
    fs::write(&config, serde_json::to_vec(&value).unwrap()).unwrap();
    flow::init(&ws, &config, "empty").unwrap();
    let result = flow::run(&ws, "rev0", None, false).unwrap();
    assert_eq!(result["error"]["code"], "EQUIVALENCE_UNVERIFIED");
    let reader = Workspace::open(&ws, false, false).unwrap();
    let state = reader.load("HEAD").unwrap();
    assert_eq!(state.stages[0].status, StageStatus::Passed);
    assert_eq!(state.stages[1].status, StageStatus::Failed);
    assert_eq!(state.stages[2].status, StageStatus::Pending);
}

#[test]
fn extracted_parasitics_are_rejected_without_mutation() {
    let (root, config) = fixture();
    let ws = root.path().join("ws");
    let mut value: serde_json::Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
    value["flow"] = json!("classic");
    value["physical_steps"] = json!([{"id":"route","tool":"openroad","script":"top.sdc","arguments":["{driver}"],
        "outputs":[{"kind":"netlist","path":"top.v"},{"kind":"spef","path":"top.spef"},{"kind":"gds","path":"top.gds"}]}]);
    fs::write(&config, serde_json::to_vec(&value).unwrap()).unwrap();
    flow::init(&ws, &config, "empty").unwrap();
    let plan = flow::plan(&ws, "HEAD").unwrap();
    assert_eq!(
        plan["steps"].as_array().unwrap().last().unwrap()["capability"],
        "legacy_adapter_unverified"
    );
    assert_eq!(
        flow::run(&ws, "rev0", None, false).unwrap_err().code,
        "CAPABILITY_UNAVAILABLE"
    );
    assert_eq!(
        Workspace::open(&ws, false, false).unwrap().head().unwrap(),
        "rev0"
    );
}

#[test]
fn orphan_snapshot_is_preserved_and_does_not_brick_future_commits() {
    let (root, config) = fixture();
    let ws_path = root.path().join("ws");
    flow::init(&ws_path, &config, "empty").unwrap();
    let ws = Workspace::open(&ws_path, true, false).unwrap();
    let mut state = ws.load("HEAD").unwrap();
    let orphan = ws_path.join("revisions/rev1");
    fs::create_dir(&orphan).unwrap();
    fs::write(orphan.join("retained-evidence"), "do not overwrite").unwrap();
    assert_eq!(ws.commit(&mut state).unwrap(), "rev2");
    assert_eq!(
        fs::read_to_string(orphan.join("retained-evidence")).unwrap(),
        "do not overwrite"
    );
    assert_eq!(ws.load("HEAD").unwrap().manifest.revision, "rev2");
    assert_eq!(ws.load("rev0").unwrap().manifest.revision, "rev0");
}

#[test]
fn query_of_uninitialized_directory_does_not_create_a_lock() {
    let parent = Path::new(env!("CARGO_MANIFEST_DIR")).join(".work/test-tmp");
    fs::create_dir_all(&parent).unwrap();
    let root = tempfile::tempdir_in(parent).unwrap();
    assert!(flow::report(root.path(), "HEAD").is_err());
    assert!(!root.path().join("workspace.lock").exists());
}

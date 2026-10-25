//! Protocol/state tests with synthetic reports; these are not EDA correctness oracles.
//! <20 files, one step, no EDA graph; <1 MiB parsed state and bounded temporary files.
use rustlane::{
    flow,
    integration::GATES,
    worker,
    workspace::{self, Workspace},
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

fn write(path: &Path, value: &Value) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, serde_json::to_vec_pretty(value).unwrap()).unwrap();
}

#[test]
fn fixed_producer_thread_count_cannot_be_misrepresented_as_an_override() {
    let mut config: rustlane::config::Config = serde_json::from_value(json!({
        "schema_version":1,"design_name":"top","pdk":"sky130_fd_sc_hd","flow":"rusteda",
        "road":{"project":"producer","manifest":"design.json","driver":"worker.py"},
        "limits":{"memory_mib":8192,"threads":2}
    }))
    .unwrap();
    assert!(config.validate().is_ok());
    for threads in [0, 1, 3, 4] {
        config.limits.threads = threads;
        assert_eq!(config.validate().unwrap_err().code, "RUSTROAD_PROTOCOL");
    }
}

fn fixture() -> (tempfile::TempDir, PathBuf) {
    let parent = Path::new(env!("CARGO_MANIFEST_DIR")).join(".work/worker-tests");
    fs::create_dir_all(&parent).unwrap();
    let temp = tempfile::tempdir_in(parent).unwrap();
    let root = temp.path();
    let project = root.join("producer");
    fs::create_dir_all(project.join("scripts")).unwrap();
    fs::write(
        project.join("scripts/main.py"),
        "# synthetic producer dependency\n",
    )
    .unwrap();
    fs::write(root.join("driver.py"), "# synthetic external worker\n").unwrap();
    fs::write(root.join("cell.lib"), "library(synthetic){}\n").unwrap();
    let program = std::env::current_exe().unwrap();
    write(
        &project.join("fixtures/tools.json"),
        &json!({"tools":{"stub":{"path":program}}}),
    );
    write(
        &project.join("fixtures/pdk.json"),
        &json!({"pdks":{"sky130":{"host_root":root,"files":{"lib":{"path":"cell.lib"}},"trees":{}}}}),
    );
    write(
        &project.join("fixtures/design.json"),
        &json!({"schema":"rustroad.design.v3","name":"top","pdks":{"sky130":{}},
            "tools":{"path":"fixtures/tools.json"},"signoff_manifest":{"path":"fixtures/pdk.json"},"clock":{"port":"clk"}}),
    );
    let config = root.join("config.json");
    write(
        &config,
        &json!({"schema_version":1,"design_name":"top","pdk":"sky130_fd_sc_hd","flow":"rusteda",
        "road":{"project":project,"manifest":"fixtures/design.json","driver":root.join("driver.py")},
        "tools":{"python":program,"rustroad":program,"gnu_time":program,"podman":program},
        "limits":{"memory_mib":8192}}),
    );
    let ws = root.join("workspace");
    flow::init(&ws, &config, "empty").unwrap();
    (temp, ws)
}

fn prepare(ws: &Path) -> (String, PathBuf, String) {
    let value = worker::prepare(ws, "rev0", false).unwrap();
    (
        value["new_rev"].as_str().unwrap().into(),
        value["request"].as_str().unwrap().into(),
        value["request_sha256"].as_str().unwrap().into(),
    )
}

fn receipt(request: &Path, hash: &str, code: i32, cleanup: bool) -> PathBuf {
    let dir = request.parent().unwrap();
    let file = dir.join(format!("receipt-{code}-{cleanup}.json"));
    let stdout = dir.join("test.stdout");
    let stderr = dir.join("test.stderr");
    let report = dir.join("cleanup.json");
    fs::write(&stdout, "synthetic output\n").unwrap();
    fs::write(&stderr, "").unwrap();
    let request_value: Value = serde_json::from_slice(&fs::read(request).unwrap()).unwrap();
    write(
        &report,
        &json!({"schema":"rusteda.worker-cleanup.v1", "request_id":request_value["request_id"],
        "verified":cleanup,"scope":"inactive","containers":if cleanup {"absent"} else {"unknown"},
        "errors":if cleanup {json!([])} else {json!(["synthetic cleanup unavailable"])}}),
    );
    write(
        &file,
        &json!({"schema":"rustlane.worker-receipt.v1","request_sha256":hash,"exit_code":code,
        "elapsed_seconds":0.2,"peak_rss_kib":null,"timed_out":false,"cancelled":code!=0,"log_limit_exceeded":false,
        "cleanup_verified":cleanup,"stdout":stdout,"stderr":stderr,
        "stdout_sha256":workspace::identify(&stdout,1024).unwrap().sha256,
        "stderr_sha256":workspace::identify(&stderr,1024).unwrap().sha256,
        "cleanup_report":report,"cleanup_sha256":workspace::identify(&report,1024).unwrap().sha256}),
    );
    file
}

fn producer_report(ws: &Path, request: &Path) -> PathBuf {
    let dir = request.parent().unwrap().join("output");
    fs::create_dir(&dir).unwrap();
    let mut entries = BTreeMap::new();
    for name in [
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
        let path = dir.join(name);
        fs::write(&path, b"synthetic artifact\n").unwrap();
        entries.insert(
            name,
            serde_json::to_value(workspace::identify(&path, 1024).unwrap()).unwrap(),
        );
    }
    let mut statuses = serde_json::Map::new();
    for name in GATES {
        statuses.insert((*name).into(), json!({"status":"passed"}));
    }
    let mut parity = serde_json::Map::new();
    for source in ["native", "openrcx"] {
        let mut corners = serde_json::Map::new();
        for corner in ["tt", "ss", "ff"] {
            corners.insert(corner.into(),json!({
            "endpoints":{"status":"passed","max_difference_ns":0.0001},
            "drv":{"status":"passed"},"pulse":{"status":"passed","max_difference_ns":0.0001,"declared_deviations":[]}}));
        }
        parity.insert(source.into(), json!({"corners":corners}));
        write(
            &dir.join(format!(
                "receipts/sky130_final_{source}_run_gating/stdout.txt"
            )),
            &json!({"ok":true,
                "scope":{"wire_model":"spef_rc","parasitics":"spef","precision":"f64","backend":"cpu","corners":["tt","ss","ff"],"clock_model":"propagated","physical_clock_domains":1},
            "completeness":{"unconstrained_endpoints":0,"unconstrained_inputs":0},"metrics":{}}),
        );
    }
    statuses["timing_accuracy"]["by_spef"] = Value::Object(parity);
    let mut global = serde_json::Map::new();
    for name in [
        "tools_identity",
        "openroad_image",
        "design_manifest",
        "budget_floors",
        "manifest_provenance",
        "sdc_rendering",
        "clock_period",
    ] {
        global.insert(name.into(), json!({"status":"passed"}));
    }
    let state = Workspace::open(ws, false, false)
        .unwrap()
        .load("HEAD")
        .unwrap();
    let manifest =
        workspace::identify(&state.config.road.as_ref().unwrap().manifest, 1024 * 1024).unwrap();
    write(
        &dir.join("sky130_binding.json"),
        &json!({"digest":"synthetic-binding","revision":{"dirty":false},"artifacts":entries}),
    );
    write(
        &dir.join("summary.json"),
        &json!({"schema":"rustroad.rtl2gds.v1","status":"passed","exit_code":0,
        "failed_items":[],"unverified_items":[],"declared_deviations":[],"requested_pdks":["sky130"],
        "gating":GATES,"global_statuses":global,"design":{"name":"top","manifest_sha256":manifest.sha256},
        "pdks":[{"pdk":"sky130","statuses":statuses,"binding":{"digest":"synthetic-binding"}}]}),
    );
    dir
}

#[test]
fn claim_releases_lock_and_rejects_stale_or_duplicate_claims() {
    let (_temp, ws) = fixture();
    let (rev, request, _) = prepare(&ws);
    assert_eq!(rev, "rev1");
    assert!(request.is_file());
    assert!(Workspace::open(&ws, true, false).is_ok());
    assert_eq!(
        worker::prepare(&ws, "rev0", false).unwrap_err().code,
        "REVISION_CONFLICT"
    );
    assert_eq!(
        worker::prepare(&ws, &rev, false).unwrap_err().code,
        "INTERRUPTED_RUN"
    );
    assert!(
        worker::request(&ws, &rev).unwrap()["worker"]["unit"]
            .as_str()
            .unwrap()
            .starts_with("rusteda-worker-")
    );
    assert_eq!(
        flow::report(&ws, "HEAD").unwrap()["summary"]["complete"],
        false
    );
}

#[test]
fn unknown_cleanup_blocks_retry_and_explicit_recovery_preserves_attempts() {
    let (_temp, ws) = fixture();
    let (rev, request, hash) = prepare(&ws);
    let path = receipt(&request, &hash, 1, false);
    let failed = worker::finish(&ws, &rev, &path).unwrap();
    assert_eq!(failed["status"], "cleanup_unverified");
    let rev = failed["new_rev"].as_str().unwrap();
    assert_eq!(
        worker::prepare(&ws, rev, true).unwrap_err().code,
        "INTERRUPTED_RUN"
    );
    let path = receipt(&request, &hash, 2, true);
    let recovered = worker::finish(&ws, rev, &path).unwrap();
    let rev = recovered["new_rev"].as_str().unwrap();
    assert_eq!(
        worker::prepare(&ws, rev, false).unwrap_err().code,
        "FAILED_STEP"
    );
    let next = worker::prepare(&ws, rev, true).unwrap();
    assert!(next["request"].as_str().unwrap().contains("attempt2"));
    assert!(request.is_file());
}

#[test]
fn exit_zero_without_a_complete_producer_report_is_a_failed_stage() {
    let (_temp, ws) = fixture();
    let (rev, request, hash) = prepare(&ws);
    let path = receipt(&request, &hash, 0, true);
    let result = worker::finish(&ws, &rev, &path).unwrap();
    assert_eq!(result["ok"], false);
    assert_eq!(result["status"], "failed");
}

#[test]
fn changed_inputs_do_not_block_cleanup_or_allow_a_successful_result() {
    let (temp, ws) = fixture();
    let (rev, request, hash) = prepare(&ws);
    fs::write(temp.path().join("cell.lib"), "changed during worker run").unwrap();
    assert!(worker::request(&ws, &rev).is_ok());
    let path = receipt(&request, &hash, 0, true);
    let result = worker::finish(&ws, &rev, &path).unwrap();
    assert_eq!(result["status"], "failed");
    assert_eq!(result["error"]["code"], "INPUT_CHANGED");
    assert_eq!(result["new_rev"], "rev2");
}

#[test]
fn cleanup_report_must_match_its_receipt_and_request() {
    let (_temp, ws) = fixture();
    let (rev, request, hash) = prepare(&ws);
    let path = receipt(&request, &hash, 1, true);
    let report = request.parent().unwrap().join("cleanup.json");
    let mut cleanup: Value = serde_json::from_slice(&fs::read(&report).unwrap()).unwrap();
    cleanup["request_id"] = json!("another-worker");
    write(&report, &cleanup);
    let mut value: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    value["cleanup_sha256"] = json!(workspace::identify(&report, 1024).unwrap().sha256);
    write(&path, &value);
    assert_eq!(
        worker::finish(&ws, &rev, &path).unwrap_err().code,
        "WORKER_PROTOCOL"
    );
    assert_eq!(
        Workspace::open(&ws, false, false).unwrap().head().unwrap(),
        rev
    );
}

#[test]
fn wrong_request_or_logs_cannot_advance_revision() {
    let (_temp, ws) = fixture();
    let (rev, request, hash) = prepare(&ws);
    let path = receipt(&request, "wrong", 1, true);
    assert_eq!(
        worker::finish(&ws, &rev, &path).unwrap_err().code,
        "WORKER_PROTOCOL"
    );
    let path = receipt(&request, &hash, 1, true);
    fs::write(request.parent().unwrap().join("test.stdout"), "changed").unwrap();
    assert_eq!(
        worker::finish(&ws, &rev, &path).unwrap_err().code,
        "WORKER_PROTOCOL"
    );
    assert_eq!(
        Workspace::open(&ws, false, false).unwrap().head().unwrap(),
        rev
    );
}

#[test]
fn complete_bound_result_is_cached_but_tampered_artifacts_are_not() {
    let (_temp, ws) = fixture();
    let (rev, request, hash) = prepare(&ws);
    let output = producer_report(&ws, &request);
    let path = receipt(&request, &hash, 0, true);
    let result = worker::finish(&ws, &rev, &path).unwrap();
    assert_eq!(result["ok"], true, "{result}");
    assert_eq!(result["summary"]["qualification_status"], "passed");
    let rev = result["new_rev"].as_str().unwrap();
    let reused = worker::prepare(&ws, rev, false).unwrap();
    assert_eq!(reused["status"], "cached");
    assert_eq!(reused["new_rev"], rev);
    let added = ws
        .parent()
        .unwrap()
        .join("producer/scripts/new_dependency.py");
    fs::write(&added, "# dependency added after the initial snapshot\n").unwrap();
    assert_eq!(
        worker::prepare(&ws, rev, false).unwrap_err().code,
        "INPUT_CHANGED"
    );
    fs::remove_file(added).unwrap();
    fs::write(output.join("gds"), "changed").unwrap();
    assert!(worker::prepare(&ws, rev, false).is_err());
    assert_eq!(flow::report(&ws, "HEAD").unwrap()["ok"], false);
}

#[test]
fn missing_scope_and_fake_declarations_fail_closed() {
    for defect in ["scope", "declaration", "gate"] {
        let (_temp, ws) = fixture();
        let (rev, request, hash) = prepare(&ws);
        let output = producer_report(&ws, &request);
        let mut summary: Value =
            serde_json::from_slice(&fs::read(output.join("summary.json")).unwrap()).unwrap();
        match defect {
            "scope" => {
                fs::remove_file(output.join("receipts/sky130_final_native_run_gating/stdout.txt"))
                    .unwrap();
            }
            "declaration" => {
                summary["status"] = json!("deviation_declared");
                summary["declared_deviations"] = json!(["sky130:timing_accuracy"]);
                summary["pdks"][0]["statuses"]["timing_accuracy"]["status"] =
                    json!("deviation_declared");
            }
            _ => {
                summary["pdks"][0]["statuses"]["lvs_netgen"]["status"] = json!("failed");
            }
        }
        write(&output.join("summary.json"), &summary);
        let path = receipt(&request, &hash, 0, true);
        let result = worker::finish(&ws, &rev, &path).unwrap();
        assert_eq!(result["ok"], false, "{defect}: {result}");
    }
}

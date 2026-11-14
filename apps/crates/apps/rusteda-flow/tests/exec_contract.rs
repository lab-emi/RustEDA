// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Step-graph executor controls (FLOWEXEC-REQ-0001 to 0031) with shell commands as steps.
//! These establish scheduling, accounting and file-format behaviour, never EDA correctness.
//! At most four children of a few MiB and a dozen small files per case.
#![cfg(target_os = "linux")]

use rusteda_flow::exec::{self, Options, identity::identity, manifest, pyjson::Json};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
    process::Command,
    time::{Duration, Instant},
};

/// Write an executable script without ever holding it open for writing in this
/// multi-threaded process: a concurrently forked child would otherwise make its
/// execution fail with "text file busy".
fn script(path: &Path, body: &str) {
    let status = Command::new("/bin/sh")
        .args([
            "-c",
            "printf '%s' \"$1\" > \"$2\" && chmod 755 \"$2\"",
            "sh",
            body,
        ])
        .arg(path)
        .status()
        .unwrap();
    assert!(status.success());
}

/// CPUs this process may use that are distinct physical cores.
fn cores() -> Vec<usize> {
    let status = fs::read_to_string("/proc/self/status").unwrap();
    let list = status
        .lines()
        .find_map(|line| line.strip_prefix("Cpus_allowed_list:"))
        .unwrap()
        .trim();
    let mut seen = BTreeSet::new();
    let mut cpus = Vec::new();
    for range in list.split(',') {
        let (first, last) = range.split_once('-').unwrap_or((range, range));
        for cpu in first.parse::<usize>().unwrap()..=last.parse::<usize>().unwrap() {
            let topology = format!("/sys/devices/system/cpu/cpu{cpu}/topology");
            let key = ["physical_package_id", "core_id"]
                .map(|name| fs::read_to_string(format!("{topology}/{name}")).unwrap_or_default());
            if seen.insert(key) {
                cpus.push(cpu);
            }
        }
    }
    cpus
}

fn id(path: &Path) -> Value {
    let found = identity(path).unwrap();
    json!({"path": found.path, "sha256": found.sha256, "bytes": found.bytes})
}

fn sh(tag: &str, code: &str) -> Value {
    json!({"id": tag, "kind": "command", "tool": "sh", "arguments": ["-c", code], "timeout_s": 10})
}

struct Fixture {
    _temp: tempfile::TempDir,
    root: PathBuf,
    cpus: Vec<usize>,
    data: Value,
    content: String,
}

impl Fixture {
    /// A manifest with `threads` CPUs, one input and no steps; `None` on a smaller host.
    fn new(threads: usize) -> Option<Self> {
        let cpus: Vec<usize> = cores().into_iter().take(threads).collect();
        if cpus.len() < threads {
            eprintln!("skipped: {threads} physical cores required");
            return None;
        }
        let temp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(temp.path()).unwrap();
        fs::write(root.join("input.txt"), "immutable source\n").unwrap();
        // Stand-in for the resource recorder: `time -v -o FILE command...`.
        script(
            &root.join("time"),
            "#!/bin/sh\nout=$3\nshift 3\n: > \"$out\"\nexec \"$@\"\n",
        );
        // Stand-in for the pin-capacitance exporter: prints its input.
        script(&root.join("pin_caps"), "#!/bin/sh\nexec cat \"$1\"\n");
        let gates: serde_json::Map<String, Value> = rusteda_flow::ihp::GATES
            .iter()
            .map(|name| ((*name).to_owned(), json!("control.json")))
            .collect();
        let data = json!({
            "schema": "rusteda.ihp-flow.v1", "name": "protocol_control", "pdk": "ihp-sg13g2",
            "library": "ez130_8t", "corners": ["tt", "ss"],
            "files": {"source": id(&root.join("input.txt")), "std_tt": id(&root.join("input.txt"))},
            "tools": {"sh": id(Path::new("/bin/sh")), "rustroad": id(Path::new("/bin/sh")),
                "gnu_time": id(&root.join("time")), "pin_caps": id(&root.join("pin_caps"))},
            "scope": {"threads": threads, "cpu_affinity": cpus, "memory_mib": 512,
                "max_log_bytes": 8192},
            "steps": [], "gates": gates, "artifacts": {}});
        Some(Self {
            _temp: temp,
            root,
            cpus,
            data,
            content: String::new(),
        })
    }

    fn output(&self) -> PathBuf {
        self.root.join("run/output")
    }

    fn options(&self) -> Options {
        Options {
            project: self.root.clone(),
            manifest: self.root.join("manifest.json"),
            output: self.output(),
            base_rev: "empty".into(),
            worker_id: Some("a".repeat(64)),
            ownership: Some(self.root.join("run/ownership")),
            ..Options::default()
        }
    }

    /// Write the manifest. The JSON string `"@CONTENT@"` is replaced by `self.content`, a
    /// document whose member order is kept (the test builder itself sorts members).
    fn write(&self) {
        let text = self
            .data
            .to_string()
            .replace("\"@CONTENT@\"", &self.content);
        fs::write(self.root.join("manifest.json"), text).unwrap();
    }

    fn run_with(&self, options: &Options) -> rusteda_flow::error::Result<exec::Outcome> {
        self.write();
        exec::execute(options)
    }

    fn run(&self) -> exec::Outcome {
        self.run_with(&self.options()).unwrap()
    }

    fn read(&self, name: &str) -> Value {
        serde_json::from_slice(&fs::read(self.output().join(name)).unwrap()).unwrap()
    }

    fn receipt(&self, tag: &str) -> Value {
        self.read("step-receipts.json")
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["id"] == tag)
            .unwrap_or_else(|| panic!("no receipt for {tag}"))
            .clone()
    }

    /// Two steps `first` and `second` in one group, then a render after it.
    fn pair(first: &str, second: &str) -> Option<Self> {
        let mut fixture = Self::new(2)?;
        fixture.data["steps"] = json!([
            sh("first", first),
            sh("second", second),
            {"id": "after", "kind": "render", "destination": "control.json",
                "content": {"protocol_only": true}}]);
        fixture.data["parallel_groups"] = json!([{"max_parallel": 2, "max_memory_mib": 128,
            "steps": {"first": {"depends_on": [], "cpu_slots": [0], "memory_mib": 32},
                "second": {"depends_on": [], "cpu_slots": [1], "memory_mib": 32}}}]);
        Some(fixture)
    }
}

const NAP: &str = "grep Cpus_allowed_list /proc/self/status > $RUSTEDA_PARALLEL_STEP.cpus; \
                   echo $OMP_NUM_THREADS $RAYON_NUM_THREADS $TMPDIR > $RUSTEDA_PARALLEL_STEP.env; \
                   sleep 0.4";

fn started(row: &Value) -> f64 {
    row["started_seconds"].as_f64().unwrap()
}

/// Member names of a JSON object in document order.
fn members(text: &str, tag: &str) -> Vec<String> {
    let receipts = Json::parse(text.as_bytes()).unwrap();
    let row = receipts
        .items()
        .unwrap()
        .iter()
        .find(|row| row.get("id").and_then(Json::as_str) == Some(tag))
        .unwrap();
    row.keys().into_iter().map(str::to_owned).collect()
}

/// FLOWEXEC-REQ-0001, 0004, 0010, 0011, 0020, 0021: a serial command and a render run in manifest
/// order, receipts and summary have the reference members and text form, and a second run
/// into the same directory is refused.
#[test]
fn serial_steps_leave_reference_receipts_and_refuse_a_second_run() {
    let Some(mut f) = Fixture::new(1) else { return };
    f.data["steps"] = json!([
        sh("external_command", "printf 'observed child output' > payload; echo out; echo err >&2"),
        {"id": "render_identity", "kind": "render", "destination": "control.json",
            "content": "@CONTENT@"}]);
    f.content = r#"{"status": "synthetic_protocol_only", "payload": {"$identity": "payload"},
        "source_sha256": {"$sha256": "payload"}, "input": {"$input": "source"},
        "where": "{output}/x", "ratio": 7.5e-2}"#
        .into();
    f.data["artifacts"] = json!({"payload": "payload"});
    let outcome = f.run();
    assert_eq!(outcome.exit_code, 0, "{}", outcome.value);
    assert_eq!(outcome.value["ok"], true);
    assert_eq!(
        outcome.value["qualification"],
        "requires_checked_flow_ingestion"
    );
    let output = f.output();
    let out = output.to_str().unwrap();
    let summary = f.read("summary.json");
    assert_eq!(summary["schema"], "rusteda.ihp-rtl2gds.v1");
    assert_eq!(summary["status"], "completed");
    assert_eq!(
        summary["manifest_sha256"],
        id(&f.root.join("manifest.json"))["sha256"]
    );
    assert_eq!(summary["artifacts"]["payload"], id(&output.join("payload")));
    assert_eq!(summary["gates"]["drc"], id(&output.join("control.json")));
    assert_eq!(summary["scope"], f.data["scope"]);
    assert_eq!(
        summary["capture_accounting"]["mode"],
        "legacy_all_streams_are_logs"
    );
    assert_eq!(summary["capture_accounting"]["diagnostic_bytes"], 8);
    assert_eq!(summary["resources"]["threads"], 1);
    assert!(
        summary["resources"]["peak_reaped_process_rss_kib"]
            .as_u64()
            .unwrap()
            > 0
    );
    let text = fs::read_to_string(output.join("summary.json")).unwrap();
    let order: Vec<&str> = Json::parse(text.as_bytes())
        .unwrap()
        .keys()
        .into_iter()
        .map(|_| "")
        .collect();
    assert_eq!(order.len(), 11);
    assert!(text.starts_with("{\n  \"schema\": \"rusteda.ihp-rtl2gds.v1\",\n  \"status\": \"completed\",\n  \"implementation\": \"rusteda\",\n  \"manifest_sha256\": "));
    // The rendered document is the reference writer's text: two-space indent, member order kept.
    let rendered = fs::read_to_string(output.join("control.json")).unwrap();
    let payload = id(&output.join("payload"));
    let source = id(&f.root.join("input.txt"));
    assert_eq!(
        rendered,
        format!(
            "{{\n  \"status\": \"synthetic_protocol_only\",\n  \"payload\": {{\n    \"path\": \"{out}/payload\",\n    \"sha256\": \"{hash}\",\n    \"bytes\": 21\n  }},\n  \"source_sha256\": \"{hash}\",\n  \"input\": {{\n    \"path\": \"{input}\",\n    \"sha256\": \"{input_hash}\",\n    \"bytes\": 17\n  }},\n  \"where\": \"{out}/x\",\n  \"ratio\": 0.075\n}}\n",
            hash = payload["sha256"].as_str().unwrap(),
            input = source["path"].as_str().unwrap(),
            input_hash = source["sha256"].as_str().unwrap(),
        )
    );
    let command = fs::read_to_string(output.join("logs/external_command.command.json")).unwrap();
    assert_eq!(
        command,
        format!(
            "{{\n  \"argv\": [\n    \"{time}\",\n    \"-v\",\n    \"-o\",\n    \"{out}/logs/external_command.time\",\n    \"/bin/sh\",\n    \"-c\",\n    \"printf 'observed child output' > payload; echo out; echo err >&2\"\n  ],\n  \"cwd\": \"{out}\"\n}}\n",
            time = f.root.join("time").display(),
        )
        .replace("\"/bin/sh\"", &format!("\"{}\"", id(Path::new("/bin/sh"))["path"].as_str().unwrap()))
    );
    let receipts = fs::read_to_string(output.join("step-receipts.json")).unwrap();
    assert!(receipts.starts_with("[\n  {\n    \"id\": \"external_command\",\n    \"kind\": \"command\",\n    \"wall_seconds\": "));
    assert_eq!(
        members(&receipts, "external_command"),
        [
            "id",
            "kind",
            "wall_seconds",
            "exit_code",
            "stdout",
            "stderr"
        ]
    );
    assert_eq!(
        members(&receipts, "render_identity"),
        ["id", "kind", "wall_seconds"]
    );
    let row = f.receipt("external_command");
    assert_eq!(row["exit_code"], 0);
    assert_eq!(
        row["stdout"],
        id(&output.join("logs/external_command.stdout"))
    );
    assert_eq!(row["stderr"]["bytes"], 4);
    assert_eq!(fs::read_to_string(output.join("HEAD")).unwrap(), "rev1\n");
    let owner: Value =
        serde_json::from_slice(&fs::read(f.root.join("run/ownership/native.json")).unwrap())
            .unwrap();
    assert_eq!(owner["request_id"], "a".repeat(64));
    assert_eq!(owner["implementation"], "native");
    assert_eq!(owner["manifest"], id(&f.root.join("manifest.json")));
    let metrics: Value =
        serde_json::from_slice(&fs::read(f.root.join("run/worker-metrics.json")).unwrap()).unwrap();
    assert_eq!(metrics["request_id"], "a".repeat(64));
    assert!(metrics["peak_rss_kib"].as_u64().unwrap() > 0);
    let stale = f.run_with(&f.options()).unwrap_err();
    assert_eq!(stale.code, "REVISION_CONFLICT");
    let mut wrong = f.options();
    wrong.output = f.root.join("other");
    wrong.base_rev = "rev0".into();
    assert_eq!(f.run_with(&wrong).unwrap_err().code, "REVISION_CONFLICT");
}

/// FLOWEXEC-REQ-0002, 0003: a changed input, a foreign tool argument, an unknown stop target and a
/// missing worker identity are refused before the ownership record or any run file exists.
#[test]
fn refusals_happen_before_anything_is_created() {
    let Some(mut f) = Fixture::new(1) else { return };
    f.data["steps"] = json!([sh("only", "touch ran")]);
    let untouched = |f: &Fixture| {
        assert!(!f.root.join("run/ownership").exists());
        let left: Vec<_> = fs::read_dir(f.output())
            .map(|entries| entries.map(|e| e.unwrap().file_name()).collect())
            .unwrap_or_default();
        assert!(left.iter().all(|name| name == "workspace.lock"), "{left:?}");
    };
    let mut unknown = f.options();
    unknown.stop_after = Some("unknown_step".into());
    assert_eq!(f.run_with(&unknown).unwrap_err().code, "UNKNOWN_STEP");
    untouched(&f);
    let mut foreign = f.options();
    foreign.gnu_time = Some("/bin/sh".into());
    assert_eq!(
        f.run_with(&foreign).unwrap_err().code,
        "TOOL_ARGUMENT_MISMATCH"
    );
    untouched(&f);
    if std::env::var_os("RUSTEDA_WORKER_ID").is_none() {
        let mut anonymous = f.options();
        anonymous.worker_id = None;
        assert_eq!(
            f.run_with(&anonymous).unwrap_err().code,
            "WORKER_IDENTITY_REQUIRED"
        );
    }
    let mut short = f.options();
    short.worker_id = Some("A".repeat(64));
    assert_eq!(
        f.run_with(&short).unwrap_err().code,
        "WORKER_IDENTITY_REQUIRED"
    );
    untouched(&f);
    // A CPU this process may not use is no allocation.
    let cpus = f.data["scope"]["cpu_affinity"].clone();
    f.data["scope"]["cpu_affinity"] = json!([4095]);
    assert_eq!(
        f.run_with(&f.options()).unwrap_err().code,
        "CPU_ALLOCATION_INVALID"
    );
    untouched(&f);
    f.data["scope"]["cpu_affinity"] = cpus;
    fs::write(f.root.join("input.txt"), "changed source").unwrap();
    let error = f.run_with(&f.options()).unwrap_err();
    assert_eq!(error.code, "INPUT_IDENTITY_CHANGED");
    assert!(
        error.message.contains("input identity changed: source"),
        "{}",
        error.message
    );
    untouched(&f);
    assert_eq!(
        exec::plan(&f.options()).unwrap_err().code,
        "INPUT_IDENTITY_CHANGED"
    );
    fs::remove_file(f.root.join("input.txt")).unwrap();
    assert_eq!(
        f.run_with(&f.options()).unwrap_err().code,
        "INPUT_UNAVAILABLE"
    );
    untouched(&f);
}

/// FLOWEXEC-REQ-0012: a stop target ends the run after that step with a prefix summary that
/// never claims full-flow qualification.
#[test]
fn a_prefix_run_stops_after_its_target_and_claims_no_qualification() {
    let Some(mut f) = Fixture::new(1) else { return };
    f.data["steps"] = json!([
        sh("one", "touch one"),
        sh("two", "touch two"),
        sh("three", "touch three")
    ]);
    f.data["gates"] = json!({});
    f.data["artifacts"] = json!({"made": "one", "future_gds": "not_generated.gds"});
    let mut options = f.options();
    options.stop_after = Some("two".into());
    let outcome = f.run_with(&options).unwrap();
    assert_eq!(outcome.exit_code, 0, "{}", outcome.value);
    assert_eq!(outcome.value["status"], "frontend_completed");
    let summary = f.read("summary.json");
    assert_eq!(summary["schema"], "rusteda.ihp-frontend.v1");
    assert_eq!(summary["stop_after"], "two");
    assert_eq!(summary["unavailable_artifacts"], json!(["future_gds"]));
    assert_eq!(summary["artifacts"]["made"], id(&f.output().join("one")));
    assert!(
        summary["unverified_items"]
            .as_array()
            .unwrap()
            .contains(&json!("complete_flow"))
    );
    assert!(summary.get("gates").is_none());
    assert!(f.output().join("two").exists() && !f.output().join("three").exists());
    let ids: Vec<_> = f
        .read("step-receipts.json")
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["id"].clone())
        .collect();
    assert_eq!(ids, ["one", "two"]);
}

/// FLOWEXEC-REQ-0013: a failing command, an output escape, a log overflow, a deadline and an input
/// changed by a step each end the run with a failed summary; later steps do not run.
#[test]
fn serial_failures_end_the_run_with_a_failed_summary() {
    let cases: [(&str, Value, &str, &str); 5] = [
        (
            "exit",
            sh("bad", "exit 7"),
            "STEP_FAILED",
            "step failed: bad, exit=7",
        ),
        (
            "escape",
            json!({"id": "bad", "kind": "render", "destination": "../escaped.json", "content": {}}),
            "TEMPLATE_INVALID",
            "expected a normal relative output path",
        ),
        (
            "log",
            sh("bad", "head -c 9000 /dev/zero | tr '\\0' x"),
            "LOG_BUDGET_EXCEEDED",
            "aggregate log byte budget exceeded",
        ),
        (
            "deadline",
            json!({"id": "bad", "kind": "command", "tool": "sh", "timeout_s": 1,
                "arguments": ["-c", "(sleep 2; touch escaped) & sleep 5"]}),
            "STEP_TIMEOUT",
            "step deadline exceeded: bad",
        ),
        (
            "mutation",
            json!({"id": "bad", "kind": "command", "tool": "sh", "timeout_s": 10,
                "arguments": ["-c", "echo modified > \"$1\"", "sh", "{files:source}"]}),
            "INPUT_CHANGED_DURING_RUN",
            "producer input changed during execution",
        ),
    ];
    for (name, step, code, message) in cases {
        let Some(mut f) = Fixture::new(1) else { return };
        f.data["steps"] = json!([step, sh("later", "touch later")]);
        if name == "mutation" {
            f.data["steps"].as_array_mut().unwrap().pop();
        }
        let outcome = f.run();
        assert_eq!(outcome.exit_code, 1, "{name}: {}", outcome.value);
        assert_eq!(outcome.value["ok"], false);
        assert_eq!(outcome.value["error"]["code"], code, "{name}");
        let summary = f.read("summary.json");
        assert_eq!(summary["status"], "failed", "{name}");
        assert_eq!(summary["schema"], "rusteda.ihp-rtl2gds.v1");
        assert_eq!(summary["error"], message, "{name}");
        assert_eq!(summary["failed_items"], json!([message]));
        assert_eq!(summary["unverified_items"], json!(["complete_flow"]));
        assert!(!f.output().join("later").exists(), "{name}");
        assert!(!f.root.join("run/escaped.json").exists());
        assert_eq!(
            fs::read_to_string(f.output().join("HEAD")).unwrap(),
            "rev1\n"
        );
        match name {
            "exit" => assert_eq!(f.receipt("bad")["exit_code"], 7),
            "deadline" => {
                let row = f.receipt("bad");
                assert_eq!(row["exit_code"], Value::Null);
                assert_eq!(row["capture_error"], message);
                // The whole process group of the step is gone, not only its leader.
                std::thread::sleep(Duration::from_millis(1500));
                assert!(!f.output().join("escaped").exists());
            }
            _ => (),
        }
    }
}

/// FLOWEXEC-REQ-0014, 0015, 0022: ready steps of a group overlap on disjoint CPU sets, each with its
/// own thread count, temporary directory and step name, and their receipts keep manifest order.
#[test]
fn ready_group_steps_overlap_on_disjoint_cpu_lanes() {
    let Some(f) = Fixture::pair(NAP, NAP) else {
        return;
    };
    let outcome = f.run();
    assert_eq!(outcome.exit_code, 0, "{}", outcome.value);
    let (first, second) = (f.receipt("first"), f.receipt("second"));
    assert!(
        (started(&first) - started(&second)).abs() < 0.3,
        "{first} {second}"
    );
    for (tag, cpu) in [("first", f.cpus[0]), ("second", f.cpus[1])] {
        let allowed = fs::read_to_string(f.output().join(format!("{tag}.cpus"))).unwrap();
        assert_eq!(allowed.split_whitespace().last().unwrap(), cpu.to_string());
        let environment = fs::read_to_string(f.output().join(format!("{tag}.env"))).unwrap();
        assert_eq!(
            environment.trim(),
            format!("1 1 {}", f.output().join("tmp").join(tag).display())
        );
        assert_eq!(f.receipt(tag)["cpu_affinity"], json!([cpu]));
        let record: Value = serde_json::from_slice(
            &fs::read(f.output().join(format!("logs/{tag}.command.json"))).unwrap(),
        )
        .unwrap();
        assert_eq!(record["cpu_affinity"], json!([cpu]));
        assert_eq!(record["memory_reservation_mib"], 32);
    }
    let text = fs::read_to_string(f.output().join("step-receipts.json")).unwrap();
    assert_eq!(
        members(&text, "second"),
        [
            "id",
            "kind",
            "exit_code",
            "wall_seconds",
            "stdout",
            "stderr",
            "started_seconds",
            "cpu_affinity",
            "memory_reservation_mib",
            "depends_on"
        ]
    );
    let ids: Vec<_> = f
        .read("step-receipts.json")
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["id"].clone())
        .collect();
    assert_eq!(ids, ["first", "second", "after"]);
    assert_eq!(first["depends_on"], json!([]));
    assert_eq!(first["memory_reservation_mib"], 32);
}

/// FLOWEXEC-REQ-0016: a dependency, a shared CPU slot, the memory cap and the width of a group each
/// make the second step wait for the first one.
#[test]
fn dependencies_cpu_slots_memory_and_width_serialize_group_steps() {
    type Change = fn(&mut Value);
    let changes: [(&str, Change); 4] = [
        ("dependency", |g| {
            g["steps"]["second"]["depends_on"] = json!(["first"])
        }),
        ("cpu slot", |g| {
            g["steps"]["second"]["cpu_slots"] = json!([0])
        }),
        ("memory", |g| {
            g["steps"]["first"]["memory_mib"] = json!(80);
            g["steps"]["second"]["memory_mib"] = json!(80);
        }),
        ("width", |g| g["max_parallel"] = json!(1)),
    ];
    for (name, change) in changes {
        let Some(mut f) = Fixture::pair("sleep 0.4", "sleep 0.1") else {
            return;
        };
        change(&mut f.data["parallel_groups"][0]);
        let outcome = f.run();
        assert_eq!(outcome.exit_code, 0, "{name}: {}", outcome.value);
        let (first, second) = (f.receipt("first"), f.receipt("second"));
        assert!(
            started(&second) >= started(&first) + 0.39,
            "{name}: {first} {second}"
        );
        if name == "dependency" {
            assert_eq!(second["depends_on"], json!(["first"]));
        }
    }
}

/// FLOWEXEC-REQ-0017: the first failure of a group cancels its live steps with everything they
/// started, records why in their receipts, and no later step runs.
#[test]
fn a_group_failure_cancels_live_steps_and_their_descendants() {
    let Some(f) = Fixture::pair("sleep 0.2; exit 7", "(sleep 1; touch escaped) & sleep 5") else {
        return;
    };
    let begun = Instant::now();
    let outcome = f.run();
    assert!(begun.elapsed() < Duration::from_secs(4));
    assert_eq!(outcome.exit_code, 1, "{}", outcome.value);
    assert_eq!(outcome.value["error"]["code"], "STEP_FAILED");
    assert_eq!(outcome.value["error"]["step"], "first");
    let summary = f.read("summary.json");
    assert_eq!(summary["status"], "failed");
    assert_eq!(summary["error"], "step failed: first, exit=7");
    assert!(!f.output().join("control.json").exists());
    assert_eq!(f.receipt("first")["exit_code"], 7);
    let cancelled = f.receipt("second");
    assert_eq!(cancelled["exit_code"], -9);
    assert_eq!(
        cancelled["capture_error"],
        "parallel group cancelled: step failed: first, exit=7"
    );
    std::thread::sleep(Duration::from_millis(1300));
    assert!(!f.output().join("escaped").exists());
}

/// FLOWEXEC-REQ-0017, 0018: a failed dependency never launches its dependant, and a deadline inside a
/// group cancels the group and blocks the steps after it.
#[test]
fn failed_dependencies_and_group_deadlines_block_what_follows() {
    let Some(mut f) = Fixture::pair("exit 7", "touch second.started") else {
        return;
    };
    f.data["parallel_groups"][0]["steps"]["second"]["depends_on"] = json!(["first"]);
    assert_eq!(f.run().exit_code, 1);
    assert!(!f.output().join("second.started").exists());
    assert!(!f.output().join("control.json").exists());
    let Some(mut f) = Fixture::pair("sleep 5", "sleep 0.1") else {
        return;
    };
    f.data["steps"][0]["timeout_s"] = json!(1);
    let outcome = f.run();
    assert_eq!(outcome.exit_code, 1, "{}", outcome.value);
    assert_eq!(outcome.value["error"]["code"], "STEP_TIMEOUT");
    assert_eq!(
        f.read("summary.json")["error"],
        "step deadline exceeded: first"
    );
    assert!(!f.output().join("control.json").exists());
    assert_eq!(f.receipt("second")["exit_code"], 0);
    assert!(
        f.receipt("first")["capture_error"]
            .as_str()
            .unwrap()
            .starts_with("parallel group cancelled")
    );
}

/// FLOWEXEC-REQ-0019: the aggregate log budget counts the streams of simultaneous group steps.
#[test]
fn the_log_budget_counts_simultaneous_group_streams() {
    let noise = "head -c 600 /dev/zero | tr '\\0' x";
    let Some(mut f) = Fixture::pair(noise, noise) else {
        return;
    };
    f.data["scope"]["max_log_bytes"] = json!(1024);
    let outcome = f.run();
    assert_eq!(outcome.exit_code, 1, "{}", outcome.value);
    assert_eq!(outcome.value["error"]["code"], "LOG_BUDGET_EXCEEDED");
    assert!(
        f.read("summary.json")["error"]
            .as_str()
            .unwrap()
            .contains("aggregate log byte budget")
    );
}

/// FLOWEXEC-REQ-0012: a stop target inside a group runs the group as a serial prefix.
#[test]
fn a_stop_target_inside_a_group_is_a_serial_prefix() {
    let Some(f) = Fixture::pair("touch first.done", "touch second.done") else {
        return;
    };
    let mut options = f.options();
    options.stop_after = Some("first".into());
    let outcome = f.run_with(&options).unwrap();
    assert_eq!(outcome.exit_code, 0, "{}", outcome.value);
    assert!(f.output().join("first.done").exists() && !f.output().join("second.done").exists());
    assert_eq!(f.read("summary.json")["status"], "frontend_completed");
    let text = fs::read_to_string(f.output().join("step-receipts.json")).unwrap();
    assert_eq!(
        members(&text, "first"),
        [
            "id",
            "kind",
            "wall_seconds",
            "exit_code",
            "stdout",
            "stderr"
        ]
    );
}

/// FLOWEXEC-REQ-0005, 0006: malformed manifests, graphs and resource requests are refused when parsed.
#[test]
fn invalid_manifests_and_group_requests_are_refused() {
    let Some(f) = Fixture::pair("true", "true") else {
        return;
    };
    type Change = fn(&mut Value);
    let changes: [(Change, &str); 17] = [
        (
            |m| m["parallel_groups"][0]["steps"]["first"]["depends_on"] = json!(["second"]),
            "PARALLEL_GROUP_INVALID",
        ),
        (
            |m| m["parallel_groups"][0]["steps"]["first"]["cpu_slots"] = json!([2]),
            "PARALLEL_GROUP_INVALID",
        ),
        (
            |m| m["parallel_groups"][0]["steps"]["first"]["memory_mib"] = json!(129),
            "PARALLEL_GROUP_INVALID",
        ),
        (
            |m| m["parallel_groups"][0]["max_memory_mib"] = json!(512),
            "PARALLEL_GROUP_INVALID",
        ),
        (
            |m| m["parallel_groups"][0]["steps"]["missing"] = json!({"depends_on": []}),
            "PARALLEL_GROUP_INVALID",
        ),
        (
            |m| {
                m["parallel_groups"][0]["steps"]["after"] =
                    json!({"depends_on": [], "cpu_slots": [0], "memory_mib": 1})
            },
            "PARALLEL_GROUP_INVALID",
        ),
        (
            |m| m["parallel_groups"][0]["extra"] = json!(1),
            "PARALLEL_GROUP_INVALID",
        ),
        (
            |m| m["schema"] = json!("rusteda.other.v1"),
            "MANIFEST_IDENTITY",
        ),
        (|m| m["unexpected"] = json!(1), "MANIFEST_FIELDS"),
        (
            |m| {
                m.as_object_mut().unwrap().remove("gates");
            },
            "MANIFEST_FIELDS",
        ),
        (|m| m["steps"][0]["id"] = json!("second"), "STEP_INVALID"),
        (|m| m["steps"][0]["shell"] = json!(true), "STEP_INVALID"),
        (|m| m["steps"][0]["timeout_s"] = json!(0), "STEP_INVALID"),
        (
            |m| m["steps"][0]["tool"] = json!("unpinned"),
            "STEP_INVALID",
        ),
        (|m| m["steps"][0]["kind"] = json!("shell"), "STEP_INVALID"),
        (
            |m| m["scope"]["max_log_bytes"] = json!(1023),
            "SCOPE_INVALID",
        ),
        (
            |m| m["scope"]["cpu_affinity"] = json!([0, 0]),
            "CPU_ALLOCATION_INVALID",
        ),
    ];
    assert!(manifest::parse(f.data.to_string().as_bytes()).is_ok());
    for (index, (change, code)) in changes.into_iter().enumerate() {
        let mut data = f.data.clone();
        change(&mut data);
        let error = manifest::parse(data.to_string().as_bytes()).unwrap_err();
        assert_eq!(error.code, code, "case {index}: {}", error.message);
    }
}

/// FLOWEXEC-REQ-0023, 0024: a declared stdout artifact is charged to its own caps, serial (streamed)
/// and in a group; an oversized one fails the step and is never a success.
#[test]
fn stdout_artifacts_have_their_own_budgets() {
    for parallel in [false, true] {
        for limit in [64_u64, 8] {
            let Some(mut f) = Fixture::new(1) else { return };
            f.data["steps"] = json!([
                {"id": "caps", "kind": "command", "tool": "pin_caps", "arguments": ["{files:std_tt}"],
                    "timeout_s": 10, "stdout_artifact": "caps"},
                sh("later", "touch later")]);
            f.data["stdout_artifacts"] = json!({"caps": {"path": "logs/caps.stdout", "format": "pin_capacitance", "max_bytes": limit}});
            f.data["scope"]["max_stdout_artifact_bytes"] = json!(1024);
            // A tiny diagnostic budget shows that the artifact is not charged to it.
            f.data["scope"]["max_log_bytes"] = json!(1024);
            f.data["gates"] = json!({});
            if parallel {
                f.data["parallel_groups"] = json!([{"max_parallel": 1, "max_memory_mib": 64,
                    "steps": {"caps": {"depends_on": [], "cpu_slots": [0], "memory_mib": 8}}}]);
            }
            let mut options = f.options();
            options.stop_after = Some("later".into());
            let outcome = f.run_with(&options).unwrap();
            let row = f.receipt("caps");
            assert_eq!(
                row["stdout_artifact"],
                json!({"handle": "caps", "path": "logs/caps.stdout", "format": "pin_capacitance", "max_bytes": limit})
            );
            let summary = f.read("summary.json");
            assert_eq!(
                summary["capture_accounting"]["mode"],
                "explicit_stdout_artifacts"
            );
            assert_eq!(summary["capture_accounting"]["aggregate_limit_bytes"], 1024);
            if limit == 64 {
                assert_eq!(outcome.exit_code, 0, "{}", outcome.value);
                assert_eq!(
                    fs::read_to_string(f.output().join("logs/caps.stdout")).unwrap(),
                    "immutable source\n"
                );
                assert_eq!(summary["capture_accounting"]["stdout_artifact_bytes"], 17);
                assert_eq!(summary["capture_accounting"]["diagnostic_bytes"], 0);
                assert!(row.get("capture_error").is_none());
            } else {
                assert_eq!(outcome.exit_code, 1, "{}", outcome.value);
                assert!(!f.output().join("later").exists());
                // A group sees the overflow when the step ends or while its file grows.
                let error = row["capture_error"].as_str().unwrap();
                assert!(
                    error.contains("per-file stdout artifact byte budget exceeded"),
                    "{error}"
                );
                assert_ne!(row["exit_code"], 0);
            }
        }
    }
    // Only the pinned export commands may declare one, and every entry must be referenced.
    let Some(mut f) = Fixture::new(1) else { return };
    f.data["steps"] = json!([{"id": "caps", "kind": "command", "tool": "sh", "arguments": ["{files:std_tt}"],
        "timeout_s": 10, "stdout_artifact": "caps"}]);
    f.data["stdout_artifacts"] =
        json!({"caps": {"path": "logs/caps.stdout", "format": "pin_capacitance", "max_bytes": 64}});
    f.data["scope"]["max_stdout_artifact_bytes"] = json!(1024);
    assert_eq!(
        f.run_with(&f.options()).unwrap_err().code,
        "STDOUT_ARTIFACT_POLICY"
    );
    f.data["steps"][0]["tool"] = json!("pin_caps");
    f.data["stdout_artifacts"]["unused"] = f.data["stdout_artifacts"]["caps"].clone();
    assert_eq!(
        f.run_with(&f.options()).unwrap_err().code,
        "STDOUT_ARTIFACT_POLICY"
    );
}

/// FLOWEXEC-REQ-0025, 0026: progress events are JSON lines with times and resource use, and the
/// status query follows a run from running to finished.
#[test]
fn progress_events_and_status_follow_a_run() {
    let Some(mut f) = Fixture::pair("sleep 1.2", "sleep 0.1; exit 3") else {
        return;
    };
    f.data["parallel_groups"][0]["steps"]["second"]["depends_on"] = json!(["first"]);
    let mut options = f.options();
    options.events = Some(f.root.join("events.jsonl"));
    assert_eq!(exec::status(&f.output(), None).unwrap()["state"], "absent");
    f.write();
    let output = f.output();
    let manifest_path = f.root.join("manifest.json");
    let outcome = std::thread::scope(|scope| {
        let run = scope.spawn(|| exec::execute(&options).unwrap());
        let mut seen = Value::Null;
        for _ in 0..100 {
            std::thread::sleep(Duration::from_millis(20));
            let status = exec::status(&output, Some(&manifest_path)).unwrap();
            if status["state"] == "running" && status["steps"]["running"][0]["id"] == "first" {
                seen = status;
                break;
            }
        }
        assert_eq!(seen["locked"], true, "{seen}");
        // A second executor on the directory is refused at once.
        assert_eq!(exec::execute(&options).unwrap_err().code, "WORKSPACE_BUSY");
        assert_eq!(seen["head"], "rev0");
        assert_eq!(seen["steps"]["total"], 3);
        assert_eq!(seen["steps"]["pending"], json!(["second", "after"]));
        assert_eq!(seen["summary"], Value::Null);
        run.join().unwrap()
    });
    assert_eq!(outcome.exit_code, 1);
    let status = exec::status(&output, None).unwrap();
    assert_eq!(status["state"], "failed");
    assert_eq!(status["locked"], false);
    assert_eq!(status["steps"]["finished"], 2);
    assert_eq!(status["steps"]["failed"], json!(["second"]));
    assert_eq!(status["steps"]["last_finished"], "second");
    assert_eq!(status["steps"]["pending"], json!(["after"]));
    assert_eq!(status["summary"]["error"], "step failed: second, exit=3");
    assert_eq!(status["manifest"], json!(manifest_path));
    let events: Vec<Value> = fs::read_to_string(f.root.join("events.jsonl"))
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    let names: Vec<&str> = events
        .iter()
        .map(|e| e["event"].as_str().unwrap())
        .collect();
    assert_eq!(
        names,
        [
            "run_started",
            "step_started",
            "step_finished",
            "step_started",
            "step_failed",
            "run_finished"
        ]
    );
    assert!(
        events
            .iter()
            .all(|e| e["schema"] == "rusteda.flow-exec.event.v1" && e["t"].is_number())
    );
    assert_eq!(events[0]["steps"], 3);
    assert_eq!(events[1]["id"], "first");
    assert_eq!(events[1]["cpu_affinity"], json!([f.cpus[0]]));
    assert!(events[1]["pid"].as_u64().unwrap() > 1);
    assert_eq!(events[2]["exit_code"], 0);
    assert!(events[2]["wall_seconds"].as_f64().unwrap() >= 1.2);
    assert!(events[2]["max_rss_kib"].as_u64().unwrap() > 0);
    assert!(events[2]["user_seconds"].is_number() && events[2]["system_seconds"].is_number());
    assert_eq!(events[4]["exit_code"], 3);
    assert_eq!(events[4]["error"]["code"], "STEP_FAILED");
    assert_eq!(events[5]["status"], "failed");
    assert_eq!(events[5]["error"]["step"], "second");
    // An existing event file is never appended to or replaced.
    let mut again = f.options();
    again.output = f.root.join("second/output");
    again.ownership = Some(f.root.join("second/ownership"));
    again.events = Some(f.root.join("events.jsonl"));
    assert_eq!(
        exec::execute(&again).unwrap_err().code,
        "EVENTS_UNAVAILABLE"
    );
    assert!(!f.root.join("second/ownership").exists());
}

/// FLOWEXEC-REQ-0027: the dry run resolves the whole graph and creates nothing.
#[test]
fn the_dry_run_prints_the_resolved_graph_and_creates_nothing() {
    let Some(mut f) = Fixture::pair("true", "echo {threads} {files:source} > {output}/x") else {
        return;
    };
    f.data["steps"]
        .as_array_mut()
        .unwrap()
        .push(sh("last", "true"));
    f.write();
    let plan = exec::plan(&f.options()).unwrap();
    assert!(!f.root.join("run").exists());
    assert_eq!(plan["ok"], true);
    assert_eq!(plan["status"], "planned");
    assert_eq!(plan["output_ready"], true);
    assert_eq!(plan["steps_total"], 4);
    assert_eq!(plan["inputs_verified"], 6);
    assert_eq!(plan["manifest"], id(&f.root.join("manifest.json")));
    let out = f.output();
    let steps = plan["steps"].as_array().unwrap();
    assert_eq!(steps[1]["id"], "second");
    assert_eq!(steps[1]["mode"], "parallel");
    assert_eq!(steps[1]["group"], "first");
    assert_eq!(steps[1]["cpu_affinity"], json!([f.cpus[1]]));
    assert_eq!(steps[1]["tool_threads"], 1);
    assert_eq!(steps[1]["memory_reservation_mib"], 32);
    assert_eq!(steps[1]["timeout_s"], 10);
    assert_eq!(
        steps[1]["argv"],
        json!([
            f.root.join("time"),
            "-v",
            "-o",
            out.join("logs/second.time"),
            id(Path::new("/bin/sh"))["path"],
            "-c",
            format!(
                "echo 2 {} > {}/x",
                f.root.join("input.txt").display(),
                out.display()
            )
        ])
    );
    assert_eq!(steps[0]["depends_on"], json!([]));
    assert_eq!(steps[2]["kind"], "render");
    assert_eq!(steps[2]["mode"], "serial");
    assert_eq!(steps[2]["depends_on"], json!(["first", "second"]));
    assert_eq!(steps[2]["destination"], json!(out.join("control.json")));
    assert_eq!(steps[3]["depends_on"], json!(["after"]));
    assert_eq!(steps[3]["cpu_affinity"], json!(f.cpus));
    assert_eq!(plan["groups"][0]["mode"], "parallel");
    let mut prefix = f.options();
    prefix.stop_after = Some("first".into());
    let plan = exec::plan(&prefix).unwrap();
    assert_eq!(plan["steps_to_run"], 1);
    assert_eq!(plan["groups"][0]["mode"], "serial_prefix");
    assert_eq!(plan["steps"][1]["mode"], "serial");
    assert_eq!(plan["steps"][1]["will_run"], false);
    assert_eq!(plan["steps"][1]["depends_on"], json!(["first"]));
    fs::create_dir_all(out.join("logs")).unwrap();
    assert_eq!(exec::plan(&f.options()).unwrap()["output_ready"], false);
}

/// FLOWEXEC-REQ-0007: a placeholder left in a command that is going to run is refused before any
/// step runs; the dry run reports the same.
#[test]
fn unresolved_placeholders_are_refused_before_any_step_runs() {
    let Some(mut f) = Fixture::new(1) else { return };
    f.data["steps"] = json!([sh("first", "touch ran"), sh("bad", "cat {files:absent}")]);
    f.write();
    for error in [
        exec::plan(&f.options()).unwrap_err(),
        f.run_with(&f.options()).unwrap_err(),
    ] {
        assert_eq!(error.code, "TEMPLATE_INVALID");
        assert!(error.message.contains("step bad"), "{}", error.message);
    }
    assert!(!f.output().join("ran").exists());
    assert!(!f.root.join("run/ownership").exists());
    // A prefix that ends before the step is not affected by it.
    let mut prefix = f.options();
    prefix.stop_after = Some("first".into());
    assert_eq!(f.run_with(&prefix).unwrap().exit_code, 0);
    assert!(f.output().join("ran").exists());
}

/// A profile-bound manifest of another PDK: the fixture's manifest with the
/// `rusteda.native-flow.v1` schema, the given identity and a pinned profile document.
fn native(f: &mut Fixture, pdk: &str, library: &str, corners: &[&str], profile: &Value) {
    fs::write(f.root.join("profile.json"), profile.to_string()).unwrap();
    f.data["schema"] = json!("rusteda.native-flow.v1");
    f.data["pdk"] = json!(pdk);
    f.data["library"] = json!(library);
    f.data["corners"] = json!(corners);
    f.data["files"]["profile"] = id(&f.root.join("profile.json"));
}

fn sky130_profile() -> Value {
    json!({"schema": "rusteda.native-pdk-profile.v1", "name": "sky130hd", "pdk": "sky130A",
        "library": "sky130_fd_sc_hd", "corners": [{"name": "tt"}, {"name": "ss"}]})
}

/// FLOWEXEC-REQ-0033: a `rusteda.native-flow.v1` manifest runs when its identity is that of
/// its pinned PDK profile; its summary names the PDK; `--pdk` must name the manifest's PDK.
#[test]
fn a_profile_bound_manifest_of_another_pdk_runs() {
    let Some(mut f) = Fixture::new(1) else { return };
    native(
        &mut f,
        "sky130A",
        "sky130_fd_sc_hd",
        &["tt", "ss"],
        &sky130_profile(),
    );
    f.data["steps"] = json!([sh("one", "touch one"), sh("two", "touch two")]);
    f.data["gates"] = json!({});
    f.data["artifacts"] = json!({"made": "one"});
    f.write();
    let plan = exec::plan(&f.options()).unwrap();
    assert_eq!(plan["pdk"], "sky130A");
    assert_eq!(plan["library"], "sky130_fd_sc_hd");
    assert_eq!(plan["corners"], json!(["tt", "ss"]));
    // `--pdk`: the family or the exact name is accepted, another PDK is refused.
    for (pdk, accepted) in [
        ("sky130", true),
        ("sky130A", true),
        ("ihp", false),
        ("gf180", false),
        ("sky130B", false),
    ] {
        let mut options = f.options();
        options.pdk = Some(pdk.into());
        match exec::plan(&options) {
            Ok(_) => assert!(accepted, "--pdk {pdk} accepted"),
            Err(error) => {
                assert!(!accepted, "--pdk {pdk}: {}", error.message);
                assert_eq!(error.code, "PDK_MISMATCH");
            }
        }
    }
    let mut refused = f.options();
    refused.pdk = Some("ihp".into());
    assert_eq!(f.run_with(&refused).unwrap_err().code, "PDK_MISMATCH");
    assert!(!f.root.join("run/ownership").exists());
    let mut options = f.options();
    options.pdk = Some("sky130".into());
    options.stop_after = Some("two".into());
    let outcome = f.run_with(&options).unwrap();
    assert_eq!(outcome.exit_code, 0, "{}", outcome.value);
    let summary = f.read("summary.json");
    assert_eq!(summary["schema"], "rusteda.native-frontend.v1");
    assert_eq!(summary["status"], "frontend_completed");
    assert_eq!(summary["pdk"], "sky130A");
    assert_eq!(summary["library"], "sky130_fd_sc_hd");
    assert_eq!(summary["corners"], json!(["tt", "ss"]));
    assert_eq!(summary["artifacts"]["made"], id(&f.output().join("one")));
}

/// FLOWEXEC-REQ-0033: the IHP manifest keeps its fixed identity and accepts `--pdk ihp` only.
#[test]
fn the_ihp_manifest_keeps_its_fixed_identity() {
    let Some(mut f) = Fixture::new(1) else { return };
    f.data["steps"] = json!([sh("one", "true")]);
    f.write();
    let parsed = manifest::parse(f.data.to_string().as_bytes()).unwrap();
    assert_eq!(parsed.identity, manifest::FlowIdentity::ihp());
    let mut options = f.options();
    options.pdk = Some("ihp".into());
    assert_eq!(exec::plan(&options).unwrap()["pdk"], "ihp-sg13g2");
    options.pdk = Some("sky130".into());
    assert_eq!(exec::plan(&options).unwrap_err().code, "PDK_MISMATCH");
    // The IHP schema with another identity, and another PDK without a profile, stay refused.
    type Change = fn(&mut Value);
    let changes: [Change; 5] = [
        |m| m["pdk"] = json!("sky130A"),
        |m| m["library"] = json!("sky130_fd_sc_hd"),
        |m| m["corners"] = json!(["tt"]),
        |m| m["corners"] = json!(["ss", "tt"]),
        |m| m["schema"] = json!("rusteda.native-flow.v1"),
    ];
    for (index, change) in changes.into_iter().enumerate() {
        let mut data = f.data.clone();
        change(&mut data);
        let error = manifest::parse(data.to_string().as_bytes()).unwrap_err();
        assert_eq!(error.code, "MANIFEST_IDENTITY", "case {index}");
    }
}

/// FLOWEXEC-REQ-0033: a profile-bound manifest whose identity is malformed, or differs from
/// the pinned profile, is refused before anything is created.
#[test]
fn a_manifest_that_differs_from_its_profile_is_refused() {
    let Some(mut f) = Fixture::new(1) else { return };
    native(
        &mut f,
        "sky130A",
        "sky130_fd_sc_hd",
        &["tt", "ss"],
        &sky130_profile(),
    );
    f.data["steps"] = json!([sh("one", "touch one")]);
    f.data["gates"] = json!({});
    assert!(manifest::parse(f.data.to_string().as_bytes()).is_ok());
    type Change = fn(&mut Value);
    let malformed: [Change; 6] = [
        |m| m["pdk"] = json!("sky 130"),
        |m| m["library"] = json!(""),
        |m| m["corners"] = json!([]),
        |m| m["corners"] = json!(["tt", "tt"]),
        |m| m["corners"] = json!(["TT", "ss"]),
        |m| {
            m["files"].as_object_mut().unwrap().remove("profile");
        },
    ];
    for (index, change) in malformed.into_iter().enumerate() {
        let mut data = f.data.clone();
        change(&mut data);
        let error = manifest::parse(data.to_string().as_bytes()).unwrap_err();
        assert_eq!(error.code, "MANIFEST_IDENTITY", "case {index}");
    }
    // Well-formed, but not the pinned profile's identity.
    let differing: [Change; 4] = [
        |m| m["pdk"] = json!("gf180mcuD"),
        |m| m["library"] = json!("sky130_fd_sc_hs"),
        |m| m["corners"] = json!(["tt"]),
        |m| m["corners"] = json!(["ss", "tt"]),
    ];
    let good = f.data.clone();
    for (index, change) in differing.into_iter().enumerate() {
        f.data = good.clone();
        change(&mut f.data);
        f.write();
        for error in [
            exec::plan(&f.options()).unwrap_err(),
            f.run_with(&f.options()).unwrap_err(),
        ] {
            assert_eq!(error.code, "MANIFEST_IDENTITY", "case {index}");
            assert!(
                error.message.contains("pinned PDK profile"),
                "case {index}: {}",
                error.message
            );
        }
        assert!(!f.root.join("run/ownership").exists());
        assert!(!f.output().join("one").exists());
    }
    // A pinned file that is not a profile document.
    f.data = good.clone();
    fs::write(f.root.join("profile.json"), "{\"schema\": \"other\"}").unwrap();
    f.data["files"]["profile"] = id(&f.root.join("profile.json"));
    f.write();
    assert_eq!(
        exec::plan(&f.options()).unwrap_err().code,
        "MANIFEST_IDENTITY"
    );
}

/// FLOWEXEC-REQ-0023, 0033: the pin-capacitance and corner-report exports of a profile-bound
/// manifest are recognised for its own corners and Liberty groups.
#[test]
fn stdout_artifacts_follow_the_manifest_corners() {
    let Some(mut f) = Fixture::new(1) else { return };
    let profile = json!({"schema": "rusteda.native-pdk-profile.v1", "pdk": "gf180mcuD",
        "library": "gf180mcu_fd_sc_mcu7t5v0", "corners": [{"name": "typ"}, {"name": "slow"}]});
    native(
        &mut f,
        "gf180mcuD",
        "gf180mcu_fd_sc_mcu7t5v0",
        &["typ", "slow"],
        &profile,
    );
    f.data["files"]["mem_slow"] = id(&f.root.join("input.txt"));
    f.data["gates"] = json!({});
    f.data["scope"]["max_stdout_artifact_bytes"] = json!(1024);
    let caps = |file: &str| {
        json!([{"id": "caps", "kind": "command", "tool": "pin_caps",
            "arguments": [format!("{{files:{file}}}")], "timeout_s": 10, "stdout_artifact": "caps"}])
    };
    f.data["stdout_artifacts"] =
        json!({"caps": {"path": "logs/caps.stdout", "format": "pin_capacitance", "max_bytes": 64}});
    f.data["steps"] = caps("mem_slow");
    assert!(manifest::parse(f.data.to_string().as_bytes()).is_ok());
    // A file of no corner of this manifest is not a Liberty export.
    for file in ["std_tt", "source", "mem_fast", "MEM_slow"] {
        f.data["steps"] = caps(file);
        let error = manifest::parse(f.data.to_string().as_bytes()).unwrap_err();
        assert_eq!(error.code, "STDOUT_ARTIFACT_POLICY", "{file}");
    }
    // The IHP recipe keeps its four Liberty keys.
    let Some(mut ihp) = Fixture::new(1) else {
        return;
    };
    ihp.data["scope"]["max_stdout_artifact_bytes"] = json!(1024);
    ihp.data["stdout_artifacts"] = f.data["stdout_artifacts"].clone();
    ihp.data["files"]["mem_tt"] = id(&ihp.root.join("input.txt"));
    ihp.data["steps"] = caps("std_tt");
    assert!(manifest::parse(ihp.data.to_string().as_bytes()).is_ok());
    ihp.data["steps"] = caps("mem_tt");
    assert_eq!(
        manifest::parse(ihp.data.to_string().as_bytes())
            .unwrap_err()
            .code,
        "STDOUT_ARTIFACT_POLICY"
    );
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! `rusteda flow exec` and `rusteda flow exec-status` through the binary: documents, exit
//! codes, the event stream on standard output and cancellation by a signal
//! (FLOWEXEC-REQ-0028 to 0031). Steps are shell commands; nothing here is an EDA check.
#![cfg(target_os = "linux")]

use rusteda_flow::exec::identity::identity;
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Output, Stdio},
    time::{Duration, Instant},
};

fn script(path: &Path, body: &str) {
    // Written by a child so that this process never holds the script open for writing.
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

fn id(path: &Path) -> Value {
    let found = identity(path).unwrap();
    json!({"path": found.path, "sha256": found.sha256, "bytes": found.bytes})
}

struct Fixture {
    _temp: tempfile::TempDir,
    root: PathBuf,
}

impl Fixture {
    fn new(steps: Value) -> Self {
        let temp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(temp.path()).unwrap();
        fs::write(root.join("input.txt"), "immutable source\n").unwrap();
        script(
            &root.join("time"),
            "#!/bin/sh\nout=$3\nshift 3\n: > \"$out\"\nexec \"$@\"\n",
        );
        let status = fs::read_to_string("/proc/self/status").unwrap();
        let allowed = status
            .lines()
            .find_map(|line| line.strip_prefix("Cpus_allowed_list:"))
            .unwrap()
            .trim();
        let cpu: usize = allowed.split([',', '-']).next().unwrap().parse().unwrap();
        let manifest = json!({
            "schema": "rusteda.ihp-flow.v1", "name": "cli_control", "pdk": "ihp-sg13g2",
            "library": "ez130_8t", "corners": ["tt", "ss"],
            "files": {"source": id(&root.join("input.txt"))},
            "tools": {"sh": id(Path::new("/bin/sh")), "rustroad": id(Path::new("/bin/sh")),
                "gnu_time": id(&root.join("time"))},
            "scope": {"threads": 1, "cpu_affinity": [cpu], "memory_mib": 512, "max_log_bytes": 8192},
            "steps": steps, "gates": {}, "artifacts": {}});
        fs::write(root.join("manifest.json"), manifest.to_string()).unwrap();
        Self { _temp: temp, root }
    }

    fn command(&self, run: &str, arguments: &[&str]) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_rusteda"));
        command
            .args(["flow", "exec", "--manifest"])
            .arg(self.root.join("manifest.json"))
            .arg("--output")
            .arg(self.root.join(run).join("output"))
            .args(arguments)
            .env("RUSTEDA_WORKER_ID", "b".repeat(64))
            .env("RUSTEDA_OWNERSHIP", self.root.join(run).join("ownership"))
            .stdin(Stdio::null());
        command
    }

    fn exec(&self, run: &str, arguments: &[&str]) -> (Output, Vec<Value>) {
        let output = self.command(run, arguments).output().unwrap();
        let lines = String::from_utf8(output.stdout.clone())
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap_or_else(|e| panic!("{e}: {line}")))
            .collect();
        (output, lines)
    }

    fn status(&self, run: &str) -> Value {
        let output = Command::new(env!("CARGO_BIN_EXE_rusteda"))
            .args(["flow", "exec-status", "--output"])
            .arg(self.root.join(run).join("output"))
            .output()
            .unwrap();
        assert!(output.status.success());
        serde_json::from_slice(&output.stdout).unwrap()
    }
}

fn sh(tag: &str, code: &str) -> Value {
    json!({"id": tag, "kind": "command", "tool": "sh", "arguments": ["-c", code], "timeout_s": 30})
}

/// FLOWEXEC-REQ-0028, 0029: one document per invocation with coded errors; exit 0 for a completed
/// run, 1 for a failed one, 2 for a refusal, 4 for an occupied output; a repeated option takes
/// its last value; the dry run creates nothing.
#[test]
fn exec_prints_one_document_and_distinguishes_its_exit_codes() {
    let f = Fixture::new(json!([
        sh("one", "touch one"),
        sh("two", "exit 5"),
        sh("three", "touch three")
    ]));
    let (output, lines) = f.exec("plan", &["--dry-run", "--stop-after", "one"]);
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(lines.len(), 1);
    assert_eq!(lines[0]["schema"], "rustlane.v1");
    assert_eq!(lines[0]["status"], "planned");
    assert_eq!(lines[0]["steps_to_run"], 1);
    assert_eq!(lines[0]["steps"][1]["will_run"], false);
    assert!(!f.root.join("plan").exists());

    // The wrapper's fixed stop target is overridden by the one appended after it.
    let (output, lines) = f.exec(
        "prefix",
        &[
            "--base-rev",
            "empty",
            "--stop-after",
            "three",
            "--stop-after",
            "one",
        ],
    );
    assert_eq!(output.status.code(), Some(0), "{lines:?}");
    assert_eq!(lines.len(), 1);
    assert_eq!(lines[0]["ok"], true);
    assert_eq!(lines[0]["status"], "frontend_completed");
    assert_eq!(lines[0]["steps_completed"], 1);
    assert_eq!(lines[0]["qualification"], "requires_checked_flow_ingestion");
    assert!(f.root.join("prefix/output/one").exists());
    let status = f.status("prefix");
    assert_eq!(status["state"], "frontend_completed");
    assert_eq!(status["steps"]["finished"], 1);
    assert_eq!(status["steps"]["total"], 3);
    assert_eq!(status["summary"]["stop_after"], "one");

    let (output, lines) = f.exec("prefix", &["--base-rev", "empty"]);
    assert_eq!(output.status.code(), Some(4));
    assert_eq!(lines[0]["error"]["code"], "REVISION_CONFLICT");
    assert_eq!(lines[0]["ok"], false);

    let (output, lines) = f.exec("failed", &["--base-rev", "empty"]);
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(lines[0]["status"], "failed");
    assert_eq!(
        lines[0]["error"],
        json!({"code": "STEP_FAILED", "message": "step failed: two, exit=5", "step": "two"})
    );
    assert_eq!(f.status("failed")["state"], "failed");
    assert_eq!(f.status("failed")["steps"]["failed"], json!(["two"]));
    assert!(!f.root.join("failed/output/three").exists());

    let (output, lines) = f.exec("unknown", &["--base-rev", "empty", "--stop-after", "nine"]);
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(lines[0]["error"]["code"], "UNKNOWN_STEP");
    fs::write(f.root.join("input.txt"), "changed").unwrap();
    let (output, lines) = f.exec("changed", &["--base-rev", "empty"]);
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(lines[0]["error"]["code"], "INPUT_IDENTITY_CHANGED");
    assert!(!f.root.join("changed/ownership").exists());
    assert_eq!(f.status("changed")["state"], "empty");
    assert_eq!(f.status("never")["state"], "absent");
    // A run needs its expected base revision; the plan does not.
    let missing = f.command("nobase", &[]).output().unwrap();
    assert_eq!(missing.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&missing.stderr).contains("--base-rev"));
}

/// FLOWEXEC-REQ-0030: with `--events -` standard output is a stream of JSON lines that ends with
/// the result document.
#[test]
fn the_event_stream_on_standard_output_ends_with_the_result() {
    let f = Fixture::new(json!([
        sh("one", "echo hello"),
        {"id": "two", "kind": "render", "destination": "two.json", "content": {"run": "{output}"}}]));
    let (output, lines) = f.exec(
        "stream",
        &[
            "--base-rev",
            "empty",
            "--events",
            "-",
            "--stop-after",
            "two",
        ],
    );
    assert_eq!(output.status.code(), Some(0), "{lines:?}");
    let names: Vec<&str> = lines
        .iter()
        .map(|line| line["event"].as_str().unwrap())
        .collect();
    assert_eq!(
        names,
        [
            "run_started",
            "step_started",
            "step_finished",
            "step_finished",
            "run_finished",
            "result"
        ]
    );
    let result = lines.last().unwrap();
    assert_eq!(result["schema"], "rustlane.v1");
    assert_eq!(result["ok"], true);
    assert_eq!(result["status"], "frontend_completed");
    assert!(
        lines[..5]
            .iter()
            .all(|line| line["schema"] == "rusteda.flow-exec.event.v1")
    );
    assert_eq!(lines[2]["stdout_bytes"], 6);
    assert_eq!(lines[3]["kind"], "render");
}

/// FLOWEXEC-REQ-0031: SIGTERM stops the running step with everything it started, the receipts and a
/// failed summary are written, and the exit code is 130.
#[test]
fn a_termination_signal_stops_the_steps_and_leaves_a_failed_summary() {
    let f = Fixture::new(json!([
        sh("long", "(sleep 3; touch escaped) & touch started; sleep 30"),
        sh("later", "touch later")
    ]));
    let events = f.root.join("events.jsonl");
    let child = f
        .command("cancel", &["--base-rev", "empty", "--events"])
        .arg(&events)
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let output = f.root.join("cancel/output");
    let begun = Instant::now();
    while !output.join("started").exists() {
        assert!(
            begun.elapsed() < Duration::from_secs(20),
            "the step did not start"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    let running = f.status("cancel");
    assert_eq!(running["state"], "running");
    assert_eq!(running["steps"]["running"][0]["id"], "long");
    let signalled = Command::new("/bin/sh")
        .args(["-c", "kill -TERM \"$1\"", "sh", &child.id().to_string()])
        .status()
        .unwrap();
    assert!(signalled.success());
    let finished = child.wait_with_output().unwrap();
    assert_eq!(finished.status.code(), Some(130));
    assert!(begun.elapsed() < Duration::from_secs(25));
    let document: Value = serde_json::from_slice(&finished.stdout).unwrap();
    assert_eq!(document["error"]["code"], "CANCELLED");
    assert_eq!(document["status"], "failed");
    let summary: Value =
        serde_json::from_slice(&fs::read(output.join("summary.json")).unwrap()).unwrap();
    assert_eq!(summary["status"], "failed");
    assert_eq!(summary["error"], "cancelled by signal 15");
    let receipts: Value =
        serde_json::from_slice(&fs::read(output.join("step-receipts.json")).unwrap()).unwrap();
    assert_eq!(receipts[0]["id"], "long");
    assert_eq!(receipts[0]["capture_error"], "cancelled by signal 15");
    assert!(!output.join("later").exists());
    let status = f.status("cancel");
    assert_eq!(status["state"], "failed");
    assert_eq!(status["locked"], false);
    let last: Value =
        serde_json::from_str(fs::read_to_string(&events).unwrap().lines().last().unwrap()).unwrap();
    assert_eq!(last["event"], "run_finished");
    assert_eq!(last["error"]["code"], "CANCELLED");
    std::thread::sleep(Duration::from_millis(3300).saturating_sub(begun.elapsed()));
    assert!(!output.join("escaped").exists());
}

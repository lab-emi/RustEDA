// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Integration tests for `rusteda-cli` covering `RCLI-REQ-0001` through `RCLI-REQ-0018`.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::{Arc, Barrier};
use std::thread;

use rusteda_workspace::ErrorCode;
use serde_json::Value;

const PREFIX_KEYS: &[&str] = &[
    "schema",
    "ok",
    "command",
    "workspace",
    "base_rev",
    "rev",
    "new_rev",
    "scope",
    "completeness",
    "metrics",
];
const SUFFIX_KEYS: &[&str] = &["diagnostics", "error", "next_actions"];

fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_rusteda"))
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .expect("workspace root")
        .to_path_buf()
}

fn synthetic_lib(name: &str) -> PathBuf {
    for sub in ["synth/lib", "sta/lib"] {
        let p = repo_root()
            .join("verification/fixtures/synthetic")
            .join(sub)
            .join(name);
        if p.exists() {
            return p;
        }
    }
    repo_root()
        .join("verification/fixtures/synthetic/synth/lib")
        .join(name)
}

fn synthetic_rtl_dir() -> PathBuf {
    repo_root().join("verification/fixtures/synthetic/rtl")
}

fn temp_dir(tag: &str) -> PathBuf {
    let base = std::env::temp_dir().join(format!(
        "rusteda-cli-test-{}-{}-{}",
        std::process::id(),
        tag,
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    let _ = fs::remove_dir_all(&base);
    fs::create_dir_all(&base).expect("create temp dir");
    base
}

fn run_cmd(args: &[&str]) -> Output {
    Command::new(bin())
        .args(args)
        .stdin(Stdio::null())
        .output()
        .expect("spawn rusteda")
}

fn run_json(args: &[&str]) -> (i32, Value, String) {
    let mut full_args = vec!["--format", "json"];
    full_args.extend_from_slice(args);
    let out = run_cmd(&full_args);
    let code = out.status.code().unwrap_or(-1);
    let stdout = String::from_utf8(out.stdout).expect("utf8 stdout");
    let stderr = String::from_utf8(out.stderr).expect("utf8 stderr");
    assert_envelope_key_order(&stdout);
    let val: Value = serde_json::from_str(stdout.trim()).expect("valid json envelope on stdout");
    (code, val, stderr)
}

/// Checks that the raw JSON string has the standard prefix and suffix keys in exact contract order.
fn assert_envelope_key_order(raw_json: &str) {
    let keys: Vec<&str> = raw_json
        .lines()
        .filter_map(|line| {
            let rest = line.strip_prefix("  \"")?;
            let (key, _) = rest.split_once("\": ")?;
            Some(key)
        })
        .collect();
    assert!(
        keys.len() >= PREFIX_KEYS.len() + SUFFIX_KEYS.len(),
        "envelope has fewer keys than standard keys: {keys:?}"
    );
    assert_eq!(
        &keys[..PREFIX_KEYS.len()],
        PREFIX_KEYS,
        "envelope prefix standard keys out of order"
    );
    assert_eq!(
        &keys[keys.len() - SUFFIX_KEYS.len()..],
        SUFFIX_KEYS,
        "envelope suffix standard keys out of order"
    );
}

fn history_lines(ws: &Path) -> Vec<Value> {
    let path = ws.join("history.jsonl");
    let text = fs::read_to_string(&path).expect("read history.jsonl");
    text.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("valid history json line"))
        .collect()
}

/// RCLI-REQ-0001, RCLI-REQ-0002: Global options before and after subcommand, `--version` output,
/// and no tool-specific subcommands.
#[test]
fn global_flags_and_version() {
    let out = run_cmd(&["--version"]);
    assert_eq!(out.status.code(), Some(0));
    let s = String::from_utf8(out.stdout).unwrap();
    assert!(
        s.starts_with("rusteda "),
        "expected 'rusteda <version>', got {s:?}"
    );

    let root = temp_dir("global-flags");
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap();

    // Global options before subcommand.
    let (code, env, _) = run_json(&[
        "--threads",
        "2",
        "-v",
        "--color",
        "never",
        "--progress",
        "never",
        "-w",
        ws_s,
        "init",
    ]);
    assert_eq!(code, 0);
    assert_eq!(env["ok"], true);
    assert_eq!(env["command"], "rusteda init");

    // Global options after subcommand.
    let out2 = run_cmd(&[
        "show",
        "-w",
        ws_s,
        "--format",
        "json",
        "-q",
        "--color=always",
        "--progress=auto",
    ]);
    assert_eq!(out2.status.code(), Some(0));
    let stdout2 = String::from_utf8(out2.stdout).unwrap();
    assert_envelope_key_order(&stdout2);

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0003: Standard input is never read; commands succeed with null stdin and closed pipe.
#[test]
fn stdin_closed_or_null_never_blocks() {
    let root = temp_dir("stdin");
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap();

    // 1. Spawn with Stdio::null().
    let out_null = Command::new(bin())
        .args(["--format", "json", "init", "-w", ws_s])
        .stdin(Stdio::null())
        .output()
        .expect("spawn with null stdin");
    assert_eq!(out_null.status.code(), Some(0));

    // 2. Spawn with a piped stdin that is closed immediately before waiting.
    let mut child = Command::new(bin())
        .args(["--format", "json", "show", "-w", ws_s])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn with piped stdin");
    drop(child.stdin.take());
    let out_pipe = child.wait_with_output().expect("wait with closed pipe");
    assert_eq!(out_pipe.status.code(), Some(0));
    assert_envelope_key_order(&String::from_utf8(out_pipe.stdout).unwrap());

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0004, RCLI-REQ-0007: JSON mode prints a valid envelope with keys in
/// contract order for every command and every error class (including usage errors, `--help`, and
/// `--version`).
#[test]
fn json_envelope_key_order_every_command_and_error_class() {
    // --version in JSON mode
    let (code, env, _) = run_json(&["--version"]);
    assert_eq!(code, 0);
    assert_eq!(env["ok"], true);

    // --help in JSON mode
    let (code, env, _) = run_json(&["--help"]);
    assert_eq!(code, 0);
    assert_eq!(env["ok"], true);

    // USAGE error in JSON mode (unknown flag)
    let (code, env, _) = run_json(&["--definitely-not-a-flag"]);
    assert_eq!(code, 1);
    assert_eq!(env["ok"], false);
    assert_eq!(env["error"]["code"], "USAGE");

    // completions in JSON mode
    let (code, env, _) = run_json(&["completions", "bash"]);
    assert_eq!(code, 0);
    assert_eq!(env["command"], "rusteda completions");

    let root = temp_dir("envelopes");
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap();

    // NOT_A_WORKSPACE
    let (code, env, _) = run_json(&["show", "-w", ws_s]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "NOT_A_WORKSPACE");

    // init -> ok
    let (code, env, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);
    assert_eq!(env["new_rev"], "rev0");

    // WORKSPACE_EXISTS
    let (code, env, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "WORKSPACE_EXISTS");

    // UNKNOWN_REVISION
    let (code, env, _) = run_json(&["show", "-w", ws_s, "--rev", "rev99"]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "UNKNOWN_REVISION");

    // import --lib -> ok (rev1)
    let lib = synthetic_lib("wt_minimal.lib");
    let lib_s = lib.to_str().unwrap();
    let (code, env, _) = run_json(&["import", "-w", ws_s, "--base-rev", "rev0", "--lib", lib_s]);
    assert_eq!(code, 0);
    assert_eq!(env["new_rev"], "rev1");

    // REVISION_CONFLICT (exit 4)
    let (code, env, _) = run_json(&["import", "-w", ws_s, "--base-rev", "rev0", "--lib", lib_s]);
    assert_eq!(code, 4);
    assert_eq!(env["error"]["code"], "REVISION_CONFLICT");

    // log -> ok
    let (code, env, _) = run_json(&["log", "-w", ws_s]);
    assert_eq!(code, 0);
    assert_eq!(env["revisions"].as_array().unwrap().len(), 2);

    // show -> ok
    let (code, env, _) = run_json(&["show", "-w", ws_s]);
    assert_eq!(code, 0);
    assert_eq!(env["rev"], "rev1");

    // diff -> ok
    let (code, env, _) = run_json(&["diff", "-w", ws_s, "--from", "rev0", "--to", "rev1"]);
    assert_eq!(code, 0);
    assert_eq!(env["from_rev"], "rev0");
    assert_eq!(env["to_rev"], "rev1");

    // revert -> ok
    let (code, env, _) = run_json(&["revert", "-w", ws_s, "--base-rev", "rev1", "--to", "rev0"]);
    assert_eq!(code, 0);
    assert_eq!(env["rev"], "rev0");

    // export on rev0 without netlist -> MISSING_TABLE (exit 1)
    let out_v = root.join("out.v");
    let (code, env, _) = run_json(&["export", "-w", ws_s, "--netlist", out_v.to_str().unwrap()]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "MISSING_TABLE");

    // CORRUPT_TABLE -> exit 2 (corrupt manifest.json)
    fs::write(ws.join("manifest.json"), b"{not valid json").unwrap();
    let (code, env, _) = run_json(&["show", "-w", ws_s]);
    assert_eq!(code, 2);
    assert_eq!(env["error"]["code"], "CORRUPT_TABLE");

    // Verify ErrorCode::exit_code contract mappings for all classes including 2 and 130.
    assert_eq!(ErrorCode::CorruptTable.exit_code(), 2);
    assert_eq!(ErrorCode::Internal.exit_code(), 2);
    assert_eq!(ErrorCode::RevisionConflict.exit_code(), 4);
    assert_eq!(ErrorCode::Cancelled.exit_code(), 130);

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0005, RCLI-REQ-0006: Text-mode output format (`rev<N>  <command>  (base rev<M>)`,
/// `next: <command>` lines with POSIX quoting, diagnostics on stderr).
#[test]
fn text_mode_format_and_posix_quoting() {
    let root = temp_dir("text mode");
    let ws = root.join("ws with space");
    let ws_s = ws.to_str().unwrap();

    let out = run_cmd(&["init", "-w", ws_s]);
    assert_eq!(out.status.code(), Some(0));
    let stdout = String::from_utf8(out.stdout).unwrap();
    let lines: Vec<&str> = stdout.lines().collect();
    assert_eq!(lines[0], "rev0  rusteda init  (base rev0)");
    assert!(
        lines
            .iter()
            .any(|l| l.starts_with("next: rusteda show -w '")),
        "expected POSIX-quoted workspace path in next line: {stdout}"
    );

    // Trigger an INPUT_REJECTED error in text mode and verify stderr formatting.
    let bad_lib = root.join("bad.lib");
    fs::write(&bad_lib, b"not a liberty file {{{").unwrap();
    let err_out = run_cmd(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        bad_lib.to_str().unwrap(),
    ]);
    assert_eq!(err_out.status.code(), Some(1));
    assert!(
        err_out.stdout.is_empty(),
        "text mode error must not write to stdout"
    );
    let stderr = String::from_utf8(err_out.stderr).unwrap();
    assert!(
        stderr.contains("error[L"),
        "expected error[L...]: on stderr, got: {stderr}"
    );

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0008, RCLI-REQ-0010, RCLI-REQ-0011: History lines are written for every
/// workspace-changing command (`init`, `import`, `revert`) including rejected/conflicted ones,
/// and never written for queries (`log`, `show`, `diff`, `export`, `completions`).
/// Concurrent imports on the same base revision result in one commit and one exit 4 conflict,
/// with both recorded in `history.jsonl`.
#[test]
fn history_logging_and_concurrent_imports() {
    let root = temp_dir("history-concurrency");
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap();

    // Initialize workspace (history entry #1).
    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);
    assert_eq!(history_lines(&ws).len(), 1);

    // Re-initializing an existing workspace fails with WORKSPACE_EXISTS and logs to history (#2).
    let (code, env, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "WORKSPACE_EXISTS");
    assert_eq!(history_lines(&ws).len(), 2);

    // Run queries: history count must remain 2.
    let _ = run_json(&["log", "-w", ws_s]);
    let _ = run_json(&["show", "-w", ws_s]);
    let _ = run_json(&["diff", "-w", ws_s, "--from", "rev0", "--to", "rev0"]);
    let _ = run_json(&["export", "-w", ws_s, "--netlist", "out.v"]);
    let _ = run_json(&["completions", "zsh"]);
    assert_eq!(
        history_lines(&ws).len(),
        2,
        "queries must not append to history.jsonl"
    );

    // Rejected import (no tables specified -> USAGE) on existing workspace logs to history (#3).
    let (code, env, _) = run_json(&["import", "-w", ws_s, "--base-rev", "rev0"]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "USAGE");
    assert_eq!(history_lines(&ws).len(), 3);

    // Two concurrent imports on base rev0 -> one exits 0 (rev1), one exits 4 (REVISION_CONFLICT).
    let lib = synthetic_lib("wt_minimal.lib");
    let barrier = Arc::new(Barrier::new(2));
    let mut handles = Vec::new();
    for _ in 0..2 {
        let b = Arc::clone(&barrier);
        let ws_p = ws.clone();
        let lib_p = lib.clone();
        handles.push(thread::spawn(move || {
            b.wait();
            Command::new(bin())
                .args([
                    "--format",
                    "json",
                    "import",
                    "-w",
                    ws_p.to_str().unwrap(),
                    "--base-rev",
                    "rev0",
                    "--lib",
                    lib_p.to_str().unwrap(),
                ])
                .stdin(Stdio::null())
                .output()
                .expect("spawn concurrent import")
        }));
    }
    let mut codes: Vec<i32> = handles
        .into_iter()
        .map(|h| h.join().unwrap().status.code().unwrap_or(-1))
        .collect();
    codes.sort_unstable();
    assert_eq!(
        codes,
        vec![0, 4],
        "expected one 0 and one 4 from concurrent imports on rev0"
    );

    let hist = history_lines(&ws);
    assert_eq!(
        hist.len(),
        5,
        "both concurrent imports must be logged in history.jsonl"
    );
    assert!(
        hist.iter()
            .any(|h| h["ok"] == false && h["error"]["code"] == "REVISION_CONFLICT")
    );

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0011: Deterministic `manifest.json` and `meta.json` bytes (except `created_unix_ms`)
/// across runs.
#[test]
fn deterministic_manifest_and_meta_bytes() {
    let root = temp_dir("determinism");
    let lib = synthetic_lib("wt_minimal.lib");
    let rtl = synthetic_rtl_dir().join("counter.v");
    let inc = synthetic_rtl_dir().join("inc");

    let run_once = |ws: &Path| -> (Value, Value) {
        let ws_s = ws.to_str().unwrap();
        let (c0, _, _) = run_json(&["init", "-w", ws_s]);
        assert_eq!(c0, 0);
        let (c1, _, _) = run_json(&[
            "import",
            "-w",
            ws_s,
            "--base-rev",
            "rev0",
            "-m",
            "initial import",
            "--lib",
            lib.to_str().unwrap(),
            "--corner",
            "tt",
            "--rtl",
            rtl.to_str().unwrap(),
            "--top",
            "counter",
            "-I",
            inc.to_str().unwrap(),
            "-D",
            "SYNTH=1",
            "-P",
            "WIDTH=8",
        ]);
        assert_eq!(c1, 0);
        let mut manifest: Value =
            serde_json::from_slice(&fs::read(ws.join("manifest.json")).unwrap()).unwrap();
        let mut meta: Value =
            serde_json::from_slice(&fs::read(ws.join("revisions/rev0001/meta.json")).unwrap())
                .unwrap();
        manifest.as_object_mut().unwrap().remove("created_unix_ms");
        meta.as_object_mut().unwrap().remove("created_unix_ms");
        // Strip the workspace argument in argv since ws1 vs ws2 path differs.
        meta.as_object_mut().unwrap().remove("argv");
        (manifest, meta)
    };

    let (man1, meta1) = run_once(&root.join("ws1"));
    let (man2, meta2) = run_once(&root.join("ws2"));
    assert_eq!(
        serde_json::to_string(&man1).unwrap(),
        serde_json::to_string(&man2).unwrap()
    );
    assert_eq!(
        serde_json::to_string(&meta1).unwrap(),
        serde_json::to_string(&meta2).unwrap()
    );
    assert_eq!(
        fs::read(root.join("ws1/revisions/rev0001/rtl/rtl.json")).unwrap(),
        fs::read(root.join("ws2/revisions/rev0001/rtl/rtl.json")).unwrap()
    );

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0012, RCLI-REQ-0013: `--lib` with `--corner` pairing, warning replay, duplicate
/// `(name, corner)` rejected, and duplicate cell within one corner rejected with `L`-series error.
#[test]
fn import_lib_corners_warnings_and_duplicate_checks() {
    let root = temp_dir("import-lib");
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap();
    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    // "--corner" before "--lib" is a USAGE error.
    let lib_min = synthetic_lib("wt_minimal.lib");
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--corner",
        "tt",
        "--lib",
        lib_min.to_str().unwrap(),
    ]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "USAGE");

    // Duplicate (name, corner) in one invocation is rejected.
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_min.to_str().unwrap(),
        "--corner",
        "tt",
        "--lib",
        lib_min.to_str().unwrap(),
        "--corner",
        "tt",
    ]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "INPUT_REJECTED");

    // Two different libraries in the same corner defining the same cell name -> L-series error.
    let lib_a = root.join("lib_a.lib");
    let lib_b = root.join("lib_b.lib");
    let lib_template = |lib_name: &str| {
        format!(
            "library ({lib_name}) {{\n  delay_model : table_lookup;\n  time_unit : \"1ns\";\n  voltage_unit : \"1V\";\n  current_unit : \"1mA\";\n  capacitive_load_unit (1, pf);\n  pulling_resistance_unit : \"1kohm\";\n  leakage_power_unit : \"1nW\";\n  slew_lower_threshold_pct_rise : 20;\n  slew_upper_threshold_pct_rise : 80;\n  slew_lower_threshold_pct_fall : 20;\n  slew_upper_threshold_pct_fall : 80;\n  input_threshold_pct_rise : 50;\n  input_threshold_pct_fall : 50;\n  output_threshold_pct_rise : 50;\n  output_threshold_pct_fall : 50;\n  cell (DUP_CELL) {{\n    area : 1.0;\n    pin (A) {{ direction : input; capacitance : 0.01; }}\n  }}\n}}\n"
        )
    };
    fs::write(&lib_a, lib_template("lib_a")).unwrap();
    fs::write(&lib_b, lib_template("lib_b")).unwrap();

    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_a.to_str().unwrap(),
        "--corner",
        "tt",
        "--lib",
        lib_b.to_str().unwrap(),
        "--corner",
        "tt",
    ]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "INPUT_REJECTED");
    let diags = env["diagnostics"].as_array().unwrap();
    assert!(
        diags
            .iter()
            .any(|d| d["code"].as_str().unwrap().starts_with('L')
                && d["message"].as_str().unwrap().contains("DUP_CELL")),
        "expected L-series diagnostic mentioning DUP_CELL: {diags:?}"
    );

    // Same cell in DIFFERENT corners (tt and ff) is allowed!
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_a.to_str().unwrap(),
        "--corner",
        "tt",
        "--lib",
        lib_b.to_str().unwrap(),
        "--corner",
        "ff",
    ]);
    assert_eq!(code, 0);
    assert_eq!(env["new_rev"], "rev1");
    assert_eq!(env["metrics"]["libraries"], 2);

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0014: `--rtl` include closure and hermetic resolution (rejects CWD fallback,
/// rejects `..` parent escape, rejects external absolute include path).
#[test]
fn import_rtl_include_closure_and_hermetic_resolution() {
    let root = temp_dir("import-rtl");
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap();
    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    // 1. Valid import with synthetic fixture (`counter.v` + `-I inc`).
    let rtl_file = synthetic_rtl_dir().join("counter.v");
    let inc_dir = synthetic_rtl_dir().join("inc");
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--rtl",
        rtl_file.to_str().unwrap(),
        "--top",
        "counter",
        "-I",
        inc_dir.to_str().unwrap(),
        "-D",
        "DEFAULT_WIDTH=8",
        "-P",
        "WIDTH=16",
        "--no-predefined-macros",
    ]);
    assert_eq!(code, 0);
    assert_eq!(env["new_rev"], "rev1");
    assert_eq!(env["metrics"]["rtl_files"], 2);

    let rtl_json: Value = serde_json::from_slice(
        &fs::read(ws.join("revisions/rev0001/rtl/rtl.json")).expect("rtl.json"),
    )
    .unwrap();
    assert_eq!(rtl_json["top"], "counter");
    assert_eq!(rtl_json["predefined_macros"], false);
    assert_eq!(rtl_json["defines"]["DEFAULT_WIDTH"], "8");
    assert_eq!(rtl_json["params"]["WIDTH"], "16");
    assert_eq!(rtl_json["files"].as_array().unwrap().len(), 2);

    // 2. Missing `-I` (even if CWD has the header) must fail hermetically (no CWD fallback).
    let cwd_dir = root.join("cwd_test");
    let sub_rtl = cwd_dir.join("src");
    fs::create_dir_all(&sub_rtl).unwrap();
    fs::write(cwd_dir.join("only_in_cwd.vh"), b"`define X 1\n").unwrap();
    let top_v = sub_rtl.join("top.v");
    fs::write(
        &top_v,
        b"`include \"only_in_cwd.vh\"\nmodule top; endmodule\n",
    )
    .unwrap();

    let out_cwd = Command::new(bin())
        .current_dir(&cwd_dir)
        .args([
            "--format",
            "json",
            "import",
            "-w",
            ws_s,
            "--base-rev",
            "rev1",
            "--rtl",
            top_v.to_str().unwrap(),
            "--top",
            "top",
        ])
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert_eq!(out_cwd.status.code(), Some(1));
    let env_cwd: Value = serde_json::from_slice(&out_cwd.stdout).unwrap();
    assert_eq!(env_cwd["error"]["code"], "INPUT_REJECTED");

    // 3. Parent-escaping include (`../escape.vh`) is rejected.
    fs::write(cwd_dir.join("escape.vh"), b"`define Y 1\n").unwrap();
    let esc_v = sub_rtl.join("esc.v");
    fs::write(
        &esc_v,
        b"`include \"../escape.vh\"\nmodule esc; endmodule\n",
    )
    .unwrap();
    let (code, env_esc, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--rtl",
        esc_v.to_str().unwrap(),
        "--top",
        "esc",
    ]);
    assert_eq!(code, 1);
    assert_eq!(env_esc["error"]["code"], "INPUT_REJECTED");

    // 4. Absolute include path is rejected.
    let abs_hdr = cwd_dir.join("escape.vh");
    let abs_v = sub_rtl.join("abs.v");
    fs::write(
        &abs_v,
        format!(
            "`include \"{}\"\nmodule abs; endmodule\n",
            abs_hdr.display()
        ),
    )
    .unwrap();
    let (code, env_abs, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--rtl",
        abs_v.to_str().unwrap(),
        "--top",
        "abs",
    ]);
    assert_eq!(code, 1);
    assert_eq!(env_abs["error"]["code"], "INPUT_REJECTED");

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0015, RCLI-REQ-0016, RCLI-REQ-0017, RCLI-REQ-0018: `log`, `show` (including
/// `not_analyzed` and `unchecked` default statuses), `diff`, `revert`, and `completions`.
#[test]
fn query_show_diff_revert_and_completions() {
    let root = temp_dir("queries");
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap();

    run_json(&["init", "-w", ws_s]);
    let lib = synthetic_lib("wt_minimal.lib");
    let rtl = synthetic_rtl_dir().join("counter.v");
    let inc = synthetic_rtl_dir().join("inc");

    let (code, _, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "-m",
        "add lib and rtl",
        "--lib",
        lib.to_str().unwrap(),
        "--rtl",
        rtl.to_str().unwrap(),
        "--top",
        "counter",
        "-I",
        inc.to_str().unwrap(),
    ]);
    assert_eq!(code, 0);

    // `show` renders missing `sta` as `timing: "not_analyzed"` and missing `check` as `equivalence: "unchecked"`.
    let (code, show_env, _) = run_json(&["show", "-w", ws_s, "--rev", "rev1"]);
    assert_eq!(code, 0);
    assert_eq!(show_env["status"]["timing"], "not_analyzed");
    assert_eq!(show_env["status"]["equivalence"], "unchecked");
    assert!(show_env["tables"]["rtl"].is_object());
    assert_eq!(show_env["libraries"].as_array().unwrap().len(), 1);

    // Also verify text-mode `show` contains `timing=not_analyzed` and `equivalence=unchecked`.
    let show_txt = String::from_utf8(run_cmd(&["show", "-w", ws_s]).stdout).unwrap();
    assert!(
        show_txt.contains("timing=not_analyzed"),
        "expected 'timing=not_analyzed' in show text output: {show_txt}"
    );
    assert!(
        show_txt.contains("equivalence=unchecked"),
        "expected 'equivalence=unchecked' in show text output: {show_txt}"
    );

    // `diff` from rev0 to rev1 reports `rtl` in `added_tables` and library in `libraries.added`.
    let (code, diff_env, _) = run_json(&["diff", "-w", ws_s, "--from", "rev0", "--to", "rev1"]);
    assert_eq!(code, 0);
    assert_eq!(
        diff_env["added_tables"].as_array().unwrap(),
        &vec![Value::String("rtl".to_string())]
    );
    assert_eq!(diff_env["libraries"]["added"].as_array().unwrap().len(), 1);

    // `log --limit 1` returns only rev1.
    let (code, log_env, _) = run_json(&["log", "-w", ws_s, "--limit", "1"]);
    assert_eq!(code, 0);
    let revs = log_env["revisions"].as_array().unwrap();
    assert_eq!(revs.len(), 1);
    assert_eq!(revs[0]["rev"], "rev1");
    assert_eq!(revs[0]["message"], "add lib and rtl");

    // `revert` moves HEAD back to rev0 without creating rev2.
    let (code, rev_env, _) =
        run_json(&["revert", "-w", ws_s, "--base-rev", "rev1", "--to", "rev0"]);
    assert_eq!(code, 0);
    assert_eq!(rev_env["rev"], "rev0");
    assert!(!ws.join("revisions/rev0002").exists());

    // `completions` outputs non-empty scripts in text mode.
    for shell in ["bash", "zsh", "fish", "elvish", "powershell"] {
        let out = run_cmd(&["completions", shell]);
        assert_eq!(out.status.code(), Some(0));
        assert!(!out.stdout.is_empty(), "empty completion for {shell}");
    }

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0012, WS-REQ-0113, WS-REQ-0115, WS-REQ-0116: Every synthetic fixture `.lib`
/// round-trips exactly through `rlib::encode`/`rlib::decode`, and `rusteda import --lib`
/// writes a compiled `.rlib` that `rlib::load` decodes back identically.
#[test]
fn synthetic_fixtures_rlib_round_trip_and_cli_load() {
    let root = temp_dir("rlib-fixtures");
    let ws_dir = root.join("ws");
    let ws_s = ws_dir.to_str().unwrap();
    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    let fixture_paths = [
        synthetic_lib("wt_minimal.lib"),
        synthetic_lib("wt_full.lib"),
        synthetic_lib("wt_rich.lib"),
        synthetic_lib("wt_reset_only.lib"),
        repo_root().join("verification/fixtures/synthetic/liberty/extensions_ad.lib"),
    ];

    for (idx, path) in fixture_paths.iter().enumerate() {
        let bytes = fs::read(path).expect("read synthetic lib");
        let text = std::str::from_utf8(&bytes).unwrap();
        let (parsed, _) =
            rusteda_formats::liberty::read_text_diags(text, &path.display().to_string())
                .expect("parse synthetic lib");

        let encoded1 = rusteda_workspace::rlib::encode(&parsed).expect("encode rlib");
        let decoded = rusteda_workspace::rlib::decode(&encoded1).expect("decode rlib");
        assert_eq!(
            decoded,
            parsed,
            "exact round-trip failed for {}",
            path.display()
        );
        let encoded2 = rusteda_workspace::rlib::encode(&decoded).expect("re-encode rlib");
        assert_eq!(
            encoded1,
            encoded2,
            "re-encode not byte-identical for {}",
            path.display()
        );

        let base_rev = format!("rev{idx}");
        let corner = format!("c{idx}");
        let (c, env, _) = run_json(&[
            "import",
            "-w",
            ws_s,
            "--base-rev",
            &base_rev,
            "--lib",
            path.to_str().unwrap(),
            "--corner",
            &corner,
        ]);
        assert_eq!(c, 0);
        let new_rev_str = env["new_rev"].as_str().unwrap();
        let ws = rusteda_workspace::Workspace::open(&ws_dir).unwrap();
        let rev = ws
            .revision(rusteda_workspace::Rev::parse(new_rev_str).unwrap())
            .unwrap();
        let loaded =
            rusteda_workspace::rlib::load(&ws, &rev, &parsed.name, &corner).expect("load rlib");
        assert_eq!(loaded, parsed);
    }

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0012, WS-REQ-0113, WS-REQ-0116: PDK smoke test importing sky130 tt/ss/ff and
/// gf180 7t/9t tt from `RUSTEDA_PDK_DIR` (or `target/pdk` fallback), verifying exact `.rlib`
/// round-trips and >= 5x decode speedup on sky130 tt.
#[test]
#[ignore = "requires local PDK checkout in RUSTEDA_PDK_DIR or target/pdk"]
fn pdk_smoke_import_sky130_and_gf180() {
    let pdk_root = std::env::var_os("RUSTEDA_PDK_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| repo_root().join("target/pdk"));
    if !pdk_root.is_dir() {
        let _ = writeln!(
            std::io::stderr(),
            "skipping PDK smoke test: {} not present",
            pdk_root.display()
        );
        return;
    }

    let mut found_libs: Vec<(PathBuf, &'static str)> = Vec::new();
    for (candidates, corner) in [
        (
            &[
                "sky130_fd_sc_hd__tt_025C_1v80.lib",
                "sky130/sky130_fd_sc_hd__tt_025C_1v80.lib",
            ][..],
            "tt",
        ),
        (
            &[
                "sky130_fd_sc_hd__ss_100C_1v60.lib",
                "sky130/sky130_fd_sc_hd__ss_100C_1v60.lib",
            ][..],
            "ss",
        ),
        (
            &[
                "sky130_fd_sc_hd__ff_n40C_1v95.lib",
                "sky130/sky130_fd_sc_hd__ff_n40C_1v95.lib",
            ][..],
            "ff",
        ),
        (
            &[
                "gf180mcu_fd_sc_mcu7t5v0__tt_025C_3v30.lib",
                "gf180mcu/gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib",
            ][..],
            "gf7t_tt",
        ),
        (
            &[
                "gf180mcu_fd_sc_mcu9t5v0__tt_025C_3v30.lib",
                "gf180mcu/gf180mcu_fd_sc_mcu9t5v0__tt_025C_5v00.lib",
            ][..],
            "gf9t_tt",
        ),
    ] {
        if let Some(p) = candidates
            .iter()
            .map(|rel| pdk_root.join(rel))
            .find(|p| p.is_file())
        {
            found_libs.push((p, corner));
        }
    }
    if found_libs.is_empty() {
        let _ = writeln!(
            std::io::stderr(),
            "skipping PDK smoke test: no PDK .lib files found under {}",
            pdk_root.display()
        );
        return;
    }

    let root = temp_dir("pdk-smoke");
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap();
    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    let mut args: Vec<String> = vec![
        "import".into(),
        "-w".into(),
        ws_s.into(),
        "--base-rev".into(),
        "rev0".into(),
    ];
    for (p, corner) in &found_libs {
        args.push("--lib".into());
        args.push(p.to_str().unwrap().into());
        args.push("--corner".into());
        args.push((*corner).into());
    }
    let arg_refs: Vec<&str> = args.iter().map(String::as_str).collect();
    let (code, env, _) = run_json(&arg_refs);
    assert_eq!(code, 0);
    assert_eq!(
        env["metrics"]["libraries"],
        serde_json::json!(found_libs.len())
    );

    let (show_code, show_env, _) = run_json(&["show", "-w", ws_s]);
    assert_eq!(show_code, 0);
    assert_eq!(
        show_env["libraries"].as_array().unwrap().len(),
        found_libs.len()
    );

    // Verify exact rlib round-trip for every imported PDK library and measure decode speedup.
    let opened_ws = rusteda_workspace::Workspace::open(&ws).unwrap();
    let rev1 = opened_ws.revision(rusteda_workspace::Rev(1)).unwrap();
    for (p, corner) in &found_libs {
        let raw = fs::read_to_string(p).unwrap();
        let t_parse0 = std::time::Instant::now();
        let (parsed, _) = rusteda_formats::liberty::read_text_diags(&raw, &p.display().to_string())
            .expect("parse PDK lib");
        let parse_dur = t_parse0.elapsed();

        let t_load0 = std::time::Instant::now();
        let loaded = rusteda_workspace::rlib::load(&opened_ws, &rev1, &parsed.name, corner)
            .expect("load PDK rlib");
        let load_dur = t_load0.elapsed();

        assert_eq!(
            loaded,
            parsed,
            "PDK rlib round-trip mismatch for {}",
            p.display()
        );
        let speedup = parse_dur.as_secs_f64() / load_dur.as_secs_f64().max(1e-9);
        let _ = writeln!(
            std::io::stderr(),
            "PDK {} ({corner}): .lib parse={:?}, .rlib load={:?}, speedup={speedup:.1}x",
            parsed.name,
            parse_dur,
            load_dur
        );
        if *corner == "tt" {
            assert!(
                speedup >= 5.0,
                "expected sky130 tt .rlib decode >= 5x faster than .lib parse, got {speedup:.2}x"
            );
        }
    }

    let _ = fs::remove_dir_all(&root);
}

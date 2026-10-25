// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Integration tests for BLIF and AIGER files of `rusteda import --rtl` (`RCLI-REQ-0021`,
//! `RCLI-REQ-0022`, `WS-REQ-0190`).

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::Value;

fn temp_dir(label: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(1);
    let id = NEXT.fetch_add(1, Ordering::Relaxed);
    let dir = std::env::temp_dir().join(format!(
        "rusteda-import-logic-{}-{label}-{id}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

/// Runs `rusteda --format json ARGS` and returns the exit status and the envelope.
fn run_json(args: &[&str]) -> (i32, Value) {
    let out = Command::new(env!("CARGO_BIN_EXE_rusteda"))
        .arg("--format")
        .arg("json")
        .args(args)
        .stdin(Stdio::null())
        .output()
        .expect("spawn rusteda");
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    let v: Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("invalid JSON: {e}\n{stdout}"));
    (out.status.code().unwrap_or(-1), v)
}

fn init(root: &Path) -> String {
    let ws = root.join("ws");
    let ws_s = ws.to_str().unwrap().to_string();
    assert_eq!(run_json(&["init", "-w", &ws_s]).0, 0);
    ws_s
}

fn rtl_json(ws: &str, rev: &str) -> Value {
    serde_json::from_slice(
        &fs::read(Path::new(ws).join(format!("revisions/{rev}/rtl/rtl.json"))).expect("rtl.json"),
    )
    .expect("json")
}

const BLIF: &str = ".model top\n.inputs a b\n.outputs y\n.wire_load_slope 0\n\
     .names a b y\n11 1\n.end\n";

// RCLI-REQ-0021, RCLI-REQ-0022, WS-REQ-0190: a BLIF file is validated, stored as given with its
// format in rtl.json, and its reader warnings keep their B codes in the envelope.
#[test]
fn blif_snapshot() {
    let root = temp_dir("blif");
    let ws = init(&root);
    let f = root.join("design.BLIF");
    fs::write(&f, BLIF).unwrap();
    let (code, v) = run_json(&[
        "import",
        "-w",
        &ws,
        "--base-rev",
        "rev0",
        "--rtl",
        f.to_str().unwrap(),
        "--top",
        "top",
    ]);
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["new_rev"], "rev1");
    let diags = v["diagnostics"].as_array().unwrap();
    assert!(diags.iter().any(|d| d["code"] == "B0020"), "{v}");
    let m = rtl_json(&ws, "rev0001");
    assert_eq!(m["format"], "blif");
    assert_eq!(m["top"], "top");
    let files = m["files"].as_array().unwrap();
    assert_eq!(files.len(), 1);
    let stored = files[0]["path"].as_str().unwrap();
    assert!(stored.ends_with("design.BLIF"), "{stored}");
    let bytes = fs::read(Path::new(&ws).join("revisions/rev0001/rtl").join(stored)).unwrap();
    assert_eq!(bytes, BLIF.as_bytes());
}

// RCLI-REQ-0021, RCLI-REQ-0022: an AIGER file; a SystemVerilog snapshot has no `format`.
#[test]
fn aiger_and_systemverilog_snapshots() {
    let root = temp_dir("aiger");
    let ws = init(&root);
    let f = root.join("and.aag");
    fs::write(&f, "aag 3 2 0 1 1\n2\n4\n6\n6 2 4\n").unwrap();
    let (code, v) = run_json(&[
        "import",
        "-w",
        &ws,
        "--base-rev",
        "rev0",
        "--rtl",
        f.to_str().unwrap(),
        "--top",
        "and",
    ]);
    assert_eq!(code, 0, "{v}");
    assert_eq!(rtl_json(&ws, "rev0001")["format"], "aiger");
    let sv = root.join("t.sv");
    fs::write(
        &sv,
        "module t(input a, output y); assign y = a; endmodule\n",
    )
    .unwrap();
    let (code, v) = run_json(&[
        "import",
        "-w",
        &ws,
        "--base-rev",
        "rev1",
        "--rtl",
        sv.to_str().unwrap(),
        "--top",
        "t",
    ]);
    assert_eq!(code, 0, "{v}");
    assert!(rtl_json(&ws, "rev0002").get("format").is_none());
}

// RCLI-REQ-0021: mixed formats, several AIGER files and the SystemVerilog options are usage
// errors; reader errors reject the import with their G and B codes and locations.
#[test]
fn rejected_imports() {
    let root = temp_dir("rejected");
    let ws = init(&root);
    let b = root.join("a.blif");
    fs::write(&b, BLIF).unwrap();
    let a = root.join("b.aag");
    fs::write(&a, "aag 1 1 0 1 0\n2\n2\n").unwrap();
    let a2 = root.join("c.aig");
    fs::write(&a2, "aag 1 1 0 1 0\n2\n2\n").unwrap();
    let (bs, as_, a2s) = (
        b.to_str().unwrap(),
        a.to_str().unwrap(),
        a2.to_str().unwrap(),
    );
    for args in [
        vec!["--rtl", bs, as_, "--top", "top"],
        vec!["--rtl", as_, a2s, "--top", "top"],
        vec!["--rtl", bs, "--top", "top", "-D", "X"],
        vec!["--rtl", bs, "--top", "top", "-P", "W=1"],
        vec!["--rtl", bs, "--top", "top", "--no-predefined-macros"],
    ] {
        let mut full = vec!["import", "-w", &ws, "--base-rev", "rev0"];
        full.extend(args.iter().copied());
        let (code, v) = run_json(&full);
        assert_eq!(code, 1, "{args:?}: {v}");
        assert_eq!(v["error"]["code"], "USAGE", "{args:?}: {v}");
    }
    let bad = root.join("bad.blif");
    fs::write(
        &bad,
        ".model top\n.inputs a\n.outputs y\n.names a y\n1x 1\n.end\n",
    )
    .unwrap();
    let (code, v) = run_json(&[
        "import",
        "-w",
        &ws,
        "--base-rev",
        "rev0",
        "--rtl",
        bad.to_str().unwrap(),
        "--top",
        "top",
    ]);
    assert_eq!(code, 1, "{v}");
    assert_eq!(v["error"]["code"], "INPUT_REJECTED");
    let d = &v["diagnostics"][0];
    assert_eq!(d["code"], "B0003", "{v}");
    assert_eq!(d["location"]["line"], 5);
    let prop = root.join("p.aag");
    fs::write(&prop, "aag 1 1 0 0 0 1\n2\n2\n").unwrap();
    let (code, v) = run_json(&[
        "import",
        "-w",
        &ws,
        "--base-rev",
        "rev0",
        "--rtl",
        prop.to_str().unwrap(),
        "--top",
        "p",
    ]);
    assert_eq!(code, 1, "{v}");
    assert_eq!(v["diagnostics"][0]["code"], "G0010", "{v}");
    // A model that the file does not have.
    let (code, v) = run_json(&[
        "import",
        "-w",
        &ws,
        "--base-rev",
        "rev0",
        "--rtl",
        bs,
        "--top",
        "nope",
    ]);
    assert_eq!(code, 1, "{v}");
    assert_eq!(v["diagnostics"][0]["code"], "B0013", "{v}");
}

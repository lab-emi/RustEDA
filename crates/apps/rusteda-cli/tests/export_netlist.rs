// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Integration tests for `rusteda export --netlist` (RCLI-REQ-0016, RCLI-REQ-0020).

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

use rusteda_workspace::Workspace;
use serde_json::Value;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_rusteda")
}

fn temp_dir(label: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(1);
    let id = NEXT.fetch_add(1, Ordering::Relaxed);
    let mut dir = env::temp_dir();
    let sanitized: String = label
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    dir.push(format!(
        "rusteda-export-netlist-{}-{sanitized}-{id}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

fn run_cmd(args: &[&str]) -> Output {
    Command::new(bin())
        .args(args)
        .stdin(Stdio::null())
        .output()
        .expect("spawn rusteda")
}

fn run_json(args: &[&str]) -> (i32, Value, String) {
    let mut full = vec!["--format", "json"];
    full.extend_from_slice(args);
    let out = run_cmd(&full);
    let code = out.status.code().unwrap_or(-1);
    let stdout = String::from_utf8(out.stdout).expect("stdout utf8");
    let stderr = String::from_utf8(out.stderr).expect("stderr utf8");
    let v: Value = serde_json::from_str(stdout.trim()).unwrap_or_else(|e| {
        panic!("invalid JSON on stdout (exit {code}): {e}\nstdout: {stdout}\nstderr: {stderr}")
    });
    assert!(
        stderr.is_empty(),
        "JSON mode must not write to stderr: {stderr}"
    );
    (code, v, stdout)
}

const SYNTH_LIB: &str = r#"
library (synth) {
  delay_model : table_lookup;
  time_unit : "1ns";
  voltage_unit : "1V";
  current_unit : "1mA";
  pulling_resistance_unit : "1kohm";
  capacitive_load_unit (1, pf);
  nom_process : 1.0;
  nom_temperature : 25.0;
  nom_voltage : 1.8;

  cell (INVX1) {
    pin (A) { direction : input; capacitance : 0.01; }
    pin (Y) { direction : output; function : "(!A)"; }
  }

  cell (NAND2X1) {
    pin (A) { direction : input; capacitance : 0.01; }
    pin (B) { direction : input; capacitance : 0.01; }
    pin (Y) { direction : output; function : "(!(A & B))"; }
  }
}
"#;

/// RCLI-REQ-0016, RCLI-REQ-0020: `rusteda export --netlist` writes a structural Verilog file that
/// round-trips through `rusteda import --netlist` with exact `FrozenNetlist` equality, and is a
/// pure query (does not advance `current` or log to `history.jsonl`).
#[test]
fn export_and_reimport_round_trip_is_exact_and_pure_query() {
    let root = temp_dir("export-roundtrip");
    let ws_path = root.join("ws");
    let ws_s = ws_path.to_str().unwrap();

    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    let lib_file = root.join("synth.lib");
    fs::write(&lib_file, SYNTH_LIB).unwrap();

    let v_in = root.join("flat_in.v");
    fs::write(
        &v_in,
        r#"
module flat_top (
  input wire [2:0] a,
  input wire [0:1] b,
  inout wire bidir,
  output wire [2:0] y
);
  wire \hier/net$0 ;
  wire \esc\slash ;

  INVX1 \u_inv/0 (
    .A(a[2]),
    .Y(\hier/net$0 )
  );

  NAND2X1 u_nand (
    .A(\hier/net$0 ),
    .B(b[1]),
    .Y(\esc\slash )
  );

  assign y[2] = \esc\slash ;
  assign y[1] = 1'b1;
  assign y[0] = 1'b0;
  assign bidir = a[0];
endmodule
"#,
    )
    .unwrap();

    let (code, import_env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_file.to_str().unwrap(),
        "--netlist",
        v_in.to_str().unwrap(),
    ]);
    assert_eq!(code, 0);
    assert_eq!(import_env["new_rev"], "rev1");
    let orig_hash = import_env["tables"]["netlist"]["hash"]
        .as_str()
        .unwrap()
        .to_string();

    let hist_before = fs::read_to_string(ws_path.join("history.jsonl")).unwrap();
    let manifest_before = fs::read_to_string(ws_path.join("manifest.json")).unwrap();

    // 1. Export in JSON mode (defaulting to current rev1).
    let v_out = root.join("exported.v");
    let (code, export_env, _) =
        run_json(&["export", "-w", ws_s, "--netlist", v_out.to_str().unwrap()]);
    assert_eq!(code, 0, "export failed: {export_env}");
    assert_eq!(export_env["command"], "rusteda export");
    assert_eq!(export_env["rev"], "rev1");
    assert_eq!(export_env["table"], "netlist");
    assert_eq!(export_env["output"], v_out.to_str().unwrap());

    // Pure query: neither history.jsonl nor manifest.json changed.
    let hist_after = fs::read_to_string(ws_path.join("history.jsonl")).unwrap();
    let manifest_after = fs::read_to_string(ws_path.join("manifest.json")).unwrap();
    assert_eq!(
        hist_before, hist_after,
        "export is a query and must not log to history.jsonl"
    );
    assert_eq!(
        manifest_before, manifest_after,
        "export must not modify manifest.json"
    );

    // 2. Export in text mode with explicit --rev rev1.
    let v_out_text = root.join("exported_text.v");
    let text_out = run_cmd(&[
        "export",
        "-w",
        ws_s,
        "--rev",
        "rev1",
        "--netlist",
        v_out_text.to_str().unwrap(),
    ]);
    assert_eq!(text_out.status.code(), Some(0));
    let stdout_str = String::from_utf8(text_out.stdout).unwrap();
    assert!(
        stdout_str.starts_with("rev1  rusteda export  netlist -> "),
        "unexpected text-mode export output: {stdout_str}"
    );
    assert_eq!(
        fs::read(&v_out).unwrap(),
        fs::read(&v_out_text).unwrap(),
        "JSON and text mode exports must write identical Verilog files"
    );

    // 3. Re-import the exported Verilog file and check exact FrozenNetlist and rbin equality.
    let (code, reimport_env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--netlist",
        v_out.to_str().unwrap(),
    ]);
    assert_eq!(
        code, 0,
        "re-import of exported netlist failed: {reimport_env}"
    );
    assert_eq!(reimport_env["new_rev"], "rev2");
    assert_eq!(
        reimport_env["tables"]["netlist"]["hash"], orig_hash,
        "exported + re-imported netlist must have identical table hash"
    );

    let ws = Workspace::open(&ws_path).unwrap();
    let rev1 = ws.revision(ws.resolve_query("rev1").unwrap()).unwrap();
    let rev2 = ws.revision(ws.resolve_query("rev2").unwrap()).unwrap();
    let rbin1 = rusteda_workspace::netlist::open_rev(&rev1).unwrap();
    let rbin2 = rusteda_workspace::netlist::open_rev(&rev2).unwrap();
    let nl1 = rusteda_workspace::netlist::decode(&rbin1).unwrap();
    let nl2 = rusteda_workspace::netlist::decode(&rbin2).unwrap();
    assert_eq!(nl1, nl2, "FrozenNetlist must round-trip exactly");

    // 4. Error cases: MISSING_TABLE on rev0, UNKNOWN_REVISION on rev99.
    let (code_missing, env_missing, _) = run_json(&[
        "export",
        "-w",
        ws_s,
        "--rev",
        "rev0",
        "--netlist",
        v_out.to_str().unwrap(),
    ]);
    assert_eq!(code_missing, 1);
    assert_eq!(env_missing["error"]["code"], "MISSING_TABLE");

    let (code_unk, env_unk, _) = run_json(&[
        "export",
        "-w",
        ws_s,
        "--rev",
        "rev99",
        "--netlist",
        v_out.to_str().unwrap(),
    ]);
    assert_eq!(code_unk, 1);
    assert_eq!(env_unk["error"]["code"], "UNKNOWN_REVISION");

    let _ = fs::remove_dir_all(&root);
}

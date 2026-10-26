// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Integration tests for `rusteda import --sdc` (`RCLI-REQ-0011`, `SDC-REQ-0051` to `SDC-REQ-0055`).

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

use rusteda_formats::sdc::{ConstrSection, Sdc, section_id};
use rusteda_workspace::{BaseRev, Invocation, RbinWriter, Rev, Upstream, Workspace};
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
        "rusteda-import-sdc-{}-{sanitized}-{id}",
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

// RCLI-REQ-0011, SDC-REQ-0051, SDC-REQ-0052, SDC-REQ-0055
#[test]
fn import_sdc_commits_constr_v1_and_reimport_is_byte_identical() {
    let root = temp_dir("commit-and-reimport");
    let ws_path = root.join("ws");
    let ws_s = ws_path.to_str().unwrap();

    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    let sub_sdc = root.join("clocks.sdc");
    fs::write(
        &sub_sdc,
        "create_clock -name $::env(CLK_NAME) -period $::env(CLK_PERIOD) [get_ports clk_i]\n",
    )
    .unwrap();

    let main_sdc = root.join("top.sdc");
    fs::write(
        &main_sdc,
        r#"
current_design chip_top
source clocks.sdc
set io_delay [expr {$::env(CLK_PERIOD) * 0.2}]
set_input_delay -clock $::env(CLK_NAME) -max $io_delay [all_inputs -no_clocks]
set_output_delay -clock $::env(CLK_NAME) -max $io_delay [all_outputs]
set_max_fanout 10 [current_design]
set_dont_use [get_lib_cells slow/*]
"#,
    )
    .unwrap();

    let main_s = main_sdc.to_str().unwrap();
    let (code, v1, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--sdc",
        main_s,
        "--sdc-env",
        "CLK_NAME=core_clk",
        "--sdc-env",
        "CLK_PERIOD=10.0",
        "-m",
        "initial sdc",
    ]);
    assert_eq!(code, 0, "import failed: {v1}");
    assert_eq!(v1["ok"], true);
    assert_eq!(v1["new_rev"], "rev1");
    assert!(v1["tables"]["constraints"].is_object());

    // Check D0021 warning and D0020 note in diagnostics
    let diags = v1["diagnostics"].as_array().unwrap();
    assert!(diags.iter().any(|d| d["code"] == "D0021"));
    assert!(diags.iter().any(|d| d["code"] == "D0020"));

    // Verify sources in meta.json include top.sdc, clocks.sdc, and env:CLK_NAME / env:CLK_PERIOD
    let sources = v1["sources"].as_array().unwrap();
    assert_eq!(sources.len(), 4);
    assert!(
        sources
            .iter()
            .any(|s| s["table"] == "constraints" && s["path"] == main_s)
    );
    assert!(sources.iter().any(|s| {
        s["table"] == "constraints"
            && s["path"]
                .as_str()
                .is_some_and(|p| p.ends_with("clocks.sdc"))
    }));
    assert!(
        sources
            .iter()
            .any(|s| s["table"] == "constraints" && s["path"] == "env:CLK_NAME=core_clk")
    );
    assert!(
        sources
            .iter()
            .any(|s| s["table"] == "constraints" && s["path"] == "env:CLK_PERIOD=10.0")
    );

    // Open rev1 constraints.rbin and decode CONSTR v1 sections
    let ws = Workspace::open(&ws_path).unwrap();
    let rev1 = ws.revision(Rev(1)).unwrap();
    let rbin1 = rev1.open_rbin("constraints", "CONSTR", 1..=1).unwrap();
    rbin1.verify_hash().unwrap();

    let mut sections = Vec::new();
    for id in section_id::STR_BYTES..=section_id::DIAGS {
        let info = rbin1
            .sections()
            .iter()
            .find(|s| s.id == u32::from(id))
            .expect("section present");
        let bytes = rbin1.section_bytes(u32::from(id)).unwrap().to_vec();
        sections.push(ConstrSection {
            id,
            elem_size: info.elem_size,
            count: info.count(),
            bytes,
        });
    }
    let decoded = Sdc::from_sections(&sections).unwrap();
    assert_eq!(decoded.current_design.as_deref(), Some("chip_top"));
    assert_eq!(decoded.cmds.len(), 6);
    assert_eq!(decoded.sources.len(), 2);
    assert_eq!(decoded.env.len(), 2);

    // Re-import the exact same SDC onto rev1 -> rev2 and verify byte-identical constraints.rbin
    let (code, v2, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--sdc",
        main_s,
        "--sdc-env",
        "CLK_NAME=core_clk",
        "--sdc-env",
        "CLK_PERIOD=10.0",
    ]);
    assert_eq!(code, 0, "re-import failed: {v2}");
    assert_eq!(v2["new_rev"], "rev2");
    assert_eq!(
        v1["tables"]["constraints"]["hash"],
        v2["tables"]["constraints"]["hash"]
    );

    let rev2 = ws.revision(Rev(2)).unwrap();
    let rbin2 = rev2.open_rbin("constraints", "CONSTR", 1..=1).unwrap();
    assert_eq!(rbin1.as_bytes(), rbin2.as_bytes());
}

// RCLI-REQ-0011, SDC-REQ-0053
#[test]
fn reimporting_modified_sdc_drops_carried_sta_table() {
    let root = temp_dir("drop-carried-sta");
    let ws_path = root.join("ws");
    let ws_s = ws_path.to_str().unwrap();

    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    let sdc1 = root.join("c1.sdc");
    fs::write(
        &sdc1,
        "create_clock -name clk -period 10.0 [get_ports clk]\n",
    )
    .unwrap();

    let (code, v1, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--sdc",
        sdc1.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "{v1}");

    // Commit rev2 with a synthetic `sta` table that depends on `constraints`
    let ws = Workspace::open(&ws_path).unwrap();
    let rev1 = ws.revision(Rev(1)).unwrap();
    let constr_hash = rev1.meta().tables["constraints"].hash;
    let inv = Invocation::new("ruststa", "0.1.0", "ruststa sta", ["ruststa", "sta"]);
    let mut txn = ws.begin(BaseRev::parse("rev1").unwrap(), &inv).unwrap();
    let mut upstream = Upstream::new();
    upstream.insert("constraints".to_string(), constr_hash);
    let mut sta_writer = RbinWriter::new("STA", 1).unwrap();
    sta_writer.section_bytes(1, 1, &b"dummy"[..]).unwrap();
    txn.add_rbin("sta", "sta.rbin", &sta_writer, upstream)
        .unwrap();
    let committed = txn.commit(Default::default()).unwrap();
    assert_eq!(committed.rev, Rev(2));
    assert!(committed.meta.tables.contains_key("sta"));
    assert!(committed.meta.tables.contains_key("constraints"));

    // Now import a modified SDC onto rev2 -> rev3 and verify `sta` is dropped
    let sdc2 = root.join("c2.sdc");
    fs::write(
        &sdc2,
        "create_clock -name clk -period 5.0 [get_ports clk]\n",
    )
    .unwrap();
    let (code, v3, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev2",
        "--sdc",
        sdc2.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "{v3}");
    assert_eq!(v3["new_rev"], "rev3");
    assert!(v3["tables"].get("sta").is_none());
    let dropped = v3["dropped"].as_array().unwrap();
    assert!(
        dropped.iter().any(|d| d["table"] == "sta"),
        "expected sta in dropped: {dropped:?}"
    );
}

// RCLI-REQ-0007, RCLI-REQ-0008, RCLI-REQ-0011, SDC-REQ-0054
#[test]
fn d_code_error_exits_1_with_location_and_stale_base_exits_4() {
    let root = temp_dir("errors-and-conflict");
    let ws_path = root.join("ws");
    let ws_s = ws_path.to_str().unwrap();

    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    let bad_sdc = root.join("bad.sdc");
    fs::write(
        &bad_sdc,
        "create_clock -name clk -period 10.0 [get_ports clk]\nset_timing_derate -early 0.95\n",
    )
    .unwrap();
    let bad_s = bad_sdc.to_str().unwrap();

    // 1. D0010 error in JSON mode: exits 1, INPUT_REJECTED, file:line:col in diagnostic
    let (code, v_err, _) = run_json(&["import", "-w", ws_s, "--base-rev", "rev0", "--sdc", bad_s]);
    assert_eq!(code, 1, "expected exit 1 on D0010: {v_err}");
    assert_eq!(v_err["ok"], false);
    assert_eq!(v_err["error"]["code"], "INPUT_REJECTED");
    let err_msg = v_err["error"]["message"].as_str().unwrap();
    assert!(
        err_msg.contains(":2:1:"),
        "expected line:col 2:1 in error message: {err_msg}"
    );
    let diags = v_err["diagnostics"].as_array().unwrap();
    assert_eq!(diags.len(), 1);
    assert_eq!(diags[0]["code"], "D0010");
    assert_eq!(diags[0]["location"]["file"], bad_s);
    assert_eq!(diags[0]["location"]["line"], 2);
    assert_eq!(diags[0]["location"]["col"], 1);

    // Also check text mode stderr contains file:line:col
    let text_out = run_cmd(&["import", "-w", ws_s, "--base-rev", "rev0", "--sdc", bad_s]);
    assert_eq!(text_out.status.code(), Some(1));
    let stderr = String::from_utf8(text_out.stderr).unwrap();
    assert!(
        stderr.contains("D0010") && stderr.contains(":2:1:"),
        "expected D0010 and :2:1: on stderr, got: {stderr}"
    );

    // HEAD is still rev0
    let ws = Workspace::open(&ws_path).unwrap();
    assert_eq!(ws.head().unwrap(), Rev(0));

    // Commit a valid rev1, then try importing with stale --base-rev rev0 -> exit 4
    let good_sdc = root.join("good.sdc");
    fs::write(
        &good_sdc,
        "create_clock -name clk -period 10.0 [get_ports clk]\n",
    )
    .unwrap();
    let good_s = good_sdc.to_str().unwrap();
    let (code, _, _) = run_json(&["import", "-w", ws_s, "--base-rev", "rev0", "--sdc", good_s]);
    assert_eq!(code, 0);

    let (code, v_conflict, _) =
        run_json(&["import", "-w", ws_s, "--base-rev", "rev0", "--sdc", good_s]);
    assert_eq!(code, 4, "expected exit 4 on stale base: {v_conflict}");
    assert_eq!(v_conflict["error"]["code"], "REVISION_CONFLICT");
}

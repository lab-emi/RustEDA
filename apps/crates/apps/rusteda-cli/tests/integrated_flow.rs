// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

use std::fs;
use std::process::{Command, Stdio};

use serde_json::{Value, json};

fn call(args: &[&str]) -> (std::process::ExitStatus, Value) {
    let output = Command::new(env!("CARGO_BIN_EXE_rusteda"))
        .args(args)
        .stdin(Stdio::null())
        .output()
        .expect("run integrated CLI");
    let value = serde_json::from_slice(&output.stdout).unwrap_or_else(|e| {
        panic!(
            "JSON output: {e}: {}",
            String::from_utf8_lossy(&output.stderr)
        )
    });
    (output.status, value)
}

#[test]
fn flow_uses_the_unified_binary_and_preserves_revision_guards() {
    let root = tempfile::tempdir().expect("temporary directory");
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
    fs::write(
        &config,
        serde_json::to_vec(&json!({
            "design_name":"top", "pdk":"sky130_fd_sc_hd", "verilog_files":["top.v"],
            "libraries":[{"path":"cells.lib","corner":"tt"}], "sdc_file":"top.sdc",
            "tools":{"rustsys":"missing_test_engine"}
        }))
        .unwrap(),
    )
    .unwrap();
    let workspace = root.path().join("workspace with spaces");
    let ws = workspace.to_str().unwrap();
    let cfg = config.to_str().unwrap();
    let (status, value) = call(&[
        "flow",
        "--workspace",
        ws,
        "init",
        "--config",
        cfg,
        "--base-rev",
        "empty",
    ]);
    assert!(status.success(), "{value}");
    assert_eq!(value["new_rev"], "rev0");
    let before = fs::read(workspace.join("HEAD")).unwrap();
    let (status, value) = call(&["flow", "--workspace", ws, "run", "--base-rev", "rev99"]);
    assert_eq!(status.code(), Some(4));
    assert_eq!(value["error"]["code"], "REVISION_CONFLICT");
    assert_eq!(fs::read(workspace.join("HEAD")).unwrap(), before);
    let (status, value) = call(&["flow", "--workspace", ws, "report"]);
    assert!(status.success(), "{value}");
    assert_eq!(value["rev"], "rev0");
}

#[test]
fn help_exposes_flow_and_workbench_in_one_namespace() {
    for args in [&["--help"][..], &["studio", "--help"], &["flow", "--help"]] {
        let output = Command::new(env!("CARGO_BIN_EXE_rusteda"))
            .args(args)
            .stdin(Stdio::null())
            .output()
            .unwrap();
        assert!(output.status.success(), "{:?}", output);
        let text = String::from_utf8_lossy(&output.stdout);
        assert!(text.contains("rusteda"), "{text}");
        if args.len() == 1 {
            assert!(text.contains("flow") && text.contains("studio"), "{text}");
        }
    }
}

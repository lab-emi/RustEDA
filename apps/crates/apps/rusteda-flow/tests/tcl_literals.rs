// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

use rusteda_flow::{
    engine,
    process::{self, ProcessLimits},
};
use std::{fs, path::Path};

#[test]
#[ignore = "requires installed Tcl; run explicitly for physical adapter validation"]
fn actual_tcl_preserves_empty_and_special_values() {
    let parent = Path::new(env!("CARGO_MANIFEST_DIR")).join(".work/test-tmp");
    fs::create_dir_all(&parent).unwrap();
    let root = tempfile::tempdir_in(parent).unwrap();
    let values = [
        "",
        "plain",
        "a {brace} [puts injected] $env(HOME) \\ trailing",
        "line one\nline two",
        "汉字路径",
    ];
    let mut script = String::new();
    let mut expected = String::new();
    for v in values {
        script.push_str(&format!(
            "puts [binary encode hex [encoding convertto utf-8 {}]]\n",
            engine::tcl_literal(v)
        ));
        expected.push_str(
            &v.as_bytes()
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect::<String>(),
        );
        expected.push('\n');
    }
    let script_path = root.path().join("values.tcl");
    fs::write(&script_path, script).unwrap();
    let tool = engine::resolve_tool(Path::new("tclsh"))
        .expect("tclsh is required for this explicitly selected check");
    let receipt = process::run(
        &tool,
        &[script_path.to_string_lossy().into_owned()],
        root.path(),
        &root.path().join("logs"),
        &ProcessLimits {
            timeout_secs: 5,
            memory_mib: 256,
            max_log_bytes: 65536,
        },
    )
    .unwrap();
    assert!(receipt.success(), "{receipt:?}");
    assert_eq!(fs::read_to_string(receipt.stdout).unwrap(), expected);
}

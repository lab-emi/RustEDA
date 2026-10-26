// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! `rusteda show` reports the design-rule counts of a timing table that does not state its
//! design-rule status as unchecked (RCLI-REQ-0023).
use rusteda_workspace::{Invocation, Upstream, Workspace};
use serde_json::{Value, json};
use std::process::{Command, Stdio};

fn show(ws: &str, rev: &str) -> Value {
    let out = Command::new(env!("CARGO_BIN_EXE_rusteda"))
        .args(["--format", "json", "show", "-w", ws, "--rev", rev])
        .stdin(Stdio::null())
        .output()
        .unwrap();
    serde_json::from_slice(&out.stdout).unwrap_or_else(|_| panic!("{out:?}"))
}

// RCLI-REQ-0023: zeros of a table without `drv_status` are not measurements; a stated status
// is reported as recorded.
#[test]
fn design_rule_counts_without_status_are_unchecked() {
    let dir = std::env::temp_dir().join(format!("show-drv-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let inv = |c: &str| Invocation::new("test", "0.1.0", c, vec!["test".to_string()]);
    let ws = Workspace::init(dir.join("ws"), &inv("init"))
        .unwrap()
        .workspace;
    for (rev, status) in [("rev0", None), ("rev1", Some("met"))] {
        let mut txn = ws
            .begin(ws.resolve_base(rev).unwrap(), &inv("seed"))
            .unwrap();
        txn.add_bytes("sta", "sta.bin", b"timing", Upstream::new())
            .unwrap();
        txn.set_summary("sta", "max_trans_violating", json!(0))
            .unwrap();
        txn.set_summary("sta", "max_cap_violating", json!(0))
            .unwrap();
        if let Some(s) = status {
            txn.set_summary("sta", "drv_status", json!(s)).unwrap();
        }
        txn.commit(Default::default()).unwrap();
    }
    let w = dir.join("ws").to_string_lossy().into_owned();
    let old = show(&w, "rev1");
    assert_eq!(old["metrics"]["drv_status"], "unchecked", "{old}");
    assert_eq!(old["metrics"]["max_trans_violating"], Value::Null);
    assert_eq!(old["metrics"]["max_cap_violating"], Value::Null);
    let new = show(&w, "rev2");
    assert_eq!(new["metrics"]["drv_status"], "met", "{new}");
    assert_eq!(new["metrics"]["max_trans_violating"], 0);
    std::fs::remove_dir_all(&dir).unwrap();
}

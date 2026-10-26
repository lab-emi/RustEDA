// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The `rusteda-studio` command line, run as a process with standard input closed.

#[path = "../../rusteda-studio/tests/common/mod.rs"]
mod common;

use std::path::Path;
use std::process::{Command, Output, Stdio};

use common::{design_workspace, scratch};
use rusteda_workspace::{Rev, Workspace};

fn run(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_rusteda"))
        .arg("studio")
        .args(args)
        .stdin(Stdio::null())
        .output()
        .expect("the binary runs")
}

fn text(b: &[u8]) -> String {
    String::from_utf8_lossy(b).into_owned()
}

fn ws_arg(p: &Path) -> String {
    p.display().to_string()
}

// STUDIO-REQ-0220, STUDIO-REQ-0222, STUDIO-REQ-0224: `--print` prints the panels of HEAD and
// exits 0 without a window, reading nothing from standard input.
#[test]
fn print_mode() {
    let root = scratch("cli-print");
    design_workspace(&root, false);
    let w = ws_arg(&root);
    let o = run(&["-w", &w, "--print", "--max-rows", "3"]);
    assert!(o.status.success(), "{}", text(&o.stderr));
    let out = text(&o.stdout);
    assert!(
        out.starts_with("revision rev3 (HEAD rev3, following)"),
        "{out}"
    );
    assert!(out.contains("path1"));
    let o = run(&["-w", &w, "--rev", "rev0002", "--print", "--split", "core"]);
    assert!(o.status.success());
    assert!(text(&o.stdout).starts_with("revision rev2 (HEAD rev3, pinned)"));
    let o = run(&["-w", &w, "--print", "--fanin", "1", "--fanout", "0"]);
    assert!(o.status.success());
}

// STUDIO-REQ-0223: `--select` selects and shares like the user would.
#[test]
fn select_flag_shares() {
    let root = scratch("cli-select");
    design_workspace(&root, false);
    let o = run(&[
        "-w",
        &ws_arg(&root),
        "--select",
        "path2",
        "inst3",
        "--print",
    ]);
    assert!(o.status.success(), "{}", text(&o.stderr));
    let u = Workspace::open(&root).unwrap().ui_state().unwrap().unwrap();
    assert_eq!(u.selection_handle, "sel1");
    assert_eq!(u.revision, Rev(3));
    assert_eq!(u.selected_paths, ["path2"]);
    assert_eq!(u.selected_instances, ["inst3"]);
    assert!(
        text(&o.stdout).contains("shared selection: sel1 of rev3: paths [path2] instances [inst3]")
    );
}

// STUDIO-REQ-0221: a bad command line exits 2, an unreadable workspace or revision 1, each
// with a coded line on standard error; `--help` and `--version` succeed.
#[test]
fn exit_codes() {
    let root = scratch("cli-errors");
    design_workspace(&root, false);
    let w = ws_arg(&root);
    for (args, status, code) in [
        (vec!["--print"], 2, "U0091"),
        (vec!["-w", &w, "--rev", "nope", "--print"], 2, "U0091"),
        (vec!["-w", &w, "--select", "sel3", "--print"], 2, "U0091"),
        (vec!["-w", &w, "--select", "$HOME", "--print"], 2, "U0091"),
        (vec!["-w", &w, "--split", "dots", "--print"], 2, "U0091"),
        (vec!["-w", "no/such/workspace", "--print"], 1, "U0001"),
        (vec!["-w", &w, "--rev", "rev99", "--print"], 1, "U0002"),
        (vec!["-w", &w, "--select", "path99", "--print"], 1, "U0043"),
        (vec!["-w", &w, "--select", "path9", "--print"], 1, "U0043"),
    ] {
        let o = run(&args);
        assert_eq!(
            o.status.code(),
            Some(status),
            "{args:?}: {}",
            text(&o.stderr)
        );
        let err = text(&o.stderr);
        assert!(
            err.contains(&format!("error[{code}]")) || err.contains(&format!("[{code}]")),
            "{args:?}: {err}"
        );
    }
    assert!(run(&["--help"]).status.success());
    let v = run(&["--version"]);
    assert!(v.status.success());
    assert!(text(&v.stdout).starts_with("rusteda studio "));
}

// STUDIO-REQ-0221, STUDIO-REQ-0224: without a display the window cannot open: `U0090`, exit 1,
// at once instead of a process without a window.
#[cfg(target_os = "linux")]
#[test]
fn no_display_is_an_error() {
    let root = scratch("cli-nodisplay");
    design_workspace(&root, false);
    let o = Command::new(env!("CARGO_BIN_EXE_rusteda"))
        .arg("studio")
        .args(["-w", &ws_arg(&root)])
        .env_remove("DISPLAY")
        .env_remove("WAYLAND_DISPLAY")
        .stdin(Stdio::null())
        .output()
        .expect("the binary runs");
    assert_eq!(o.status.code(), Some(1), "{}", text(&o.stderr));
    assert!(text(&o.stderr).contains("error[U0090]"));
}

// STUDIO-REQ-0225: a build without the `gui` feature prints the panels but opens no window.
#[cfg(not(feature = "gui"))]
#[test]
fn no_window_without_gui() {
    let root = scratch("cli-nogui");
    design_workspace(&root, false);
    let w = ws_arg(&root);
    assert!(run(&["-w", &w, "--print"]).status.success());
    let o = run(&["-w", &w]);
    assert_eq!(o.status.code(), Some(1));
    assert!(text(&o.stderr).contains("error[U0090]"));
}

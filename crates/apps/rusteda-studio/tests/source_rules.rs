// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Rules on the sources of the crate (`docs/specs/studio/README.md`).

use std::path::Path;

fn read(rel: &str) -> String {
    std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join(rel)).unwrap()
}

// STUDIO-REQ-0240: the headless model never names the user-interface framework, the framework
// is an optional dependency of the `gui` feature only, and the front end is compiled only
// with that feature.
#[test]
fn model_has_no_gui_dependency() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/model");
    let mut n = 0;
    for e in std::fs::read_dir(&dir).unwrap().flatten() {
        let text = std::fs::read_to_string(e.path()).unwrap();
        assert!(
            !text.contains("gpui"),
            "{} names the GUI framework",
            e.path().display()
        );
        n += 1;
    }
    assert!(n >= 10);
    let manifest = read("Cargo.toml");
    assert!(manifest.contains("gpui = { workspace = true, optional = true }"));
    assert!(manifest.contains("gui = [\"dep:gpui\""));
    let lib = read("src/lib.rs");
    assert!(lib.contains("#[cfg(feature = \"gui\")]\npub mod gui;"));
}

// STUDIO-REQ-0241: of the RustEDA crates, the workbench uses the core and the workspace only.
#[test]
fn uses_core_and_workspace_only() {
    let manifest = read("Cargo.toml");
    let deps: Vec<&str> = manifest
        .lines()
        .filter(|l| l.starts_with("rusteda-"))
        .map(|l| l.split(['.', ' ', '=']).next().unwrap_or(""))
        .collect();
    assert_eq!(deps, ["rusteda-core", "rusteda-workspace"]);
}

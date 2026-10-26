// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! `cargo xtask layout`: the repository layout rules (CONTRIBUTING, "Layout and files";
//! STUDIO-REQ-0251), checked on a list of repository files so that the tests can run them on
//! synthetic trees.
//!
//! - [`check_paths`]: top-level allowlist, crate locations, Rust source locations, SPDX headers
//!   with the copyright holder of the file ([`holder`]), line and size limits;
//! - [`packages`]: the crates of the repository and their places;
//! - [`data`]: where design data files may be tracked;
//! - [`deps`]: dependency edges between the crates (the layout rules).

mod data;
mod deps;
mod manifest;
mod packages;

use std::path::Path;

/// Findings of a layout check. Errors fail the check.
#[derive(Debug, Default)]
pub(crate) struct Report {
    pub(crate) errors: Vec<String>,
}

/// Runs every layout rule on `files` (paths relative to `root`, `/`-separated).
pub(crate) fn check(root: &Path, files: &[String]) -> Report {
    let mut r = Report::default();
    check_paths(root, files, &mut r);
    packages::check(root, files, &mut r);
    data::check(files, &mut r);
    deps::check(root, files, &mut r);
    r
}

/// The `layout` command.
pub(crate) fn run(root: &Path, files: &[String]) -> Result<(), String> {
    let r = check(root, files);
    if r.errors.is_empty() {
        println!("layout ok");
        Ok(())
    } else {
        Err(r.errors.join("\n"))
    }
}

/// The copyright holder of the shared crates and their specifications ([`crate::shared`]).
const SHARED_HOLDER: &str = "2026 RustEDA contributors";
/// The copyright holder of every other file.
const OWN_HOLDER: &str = "2026 RustEDA Studio contributors";
/// The SPDX copyright tag (assembled so that license scanners do not read it as a tag of this
/// file).
const COPYRIGHT_TAG: &str = concat!("SPDX-", "FileCopyrightText:");

/// The copyright holder that the SPDX header of the repository file `f` names.
fn holder(f: &str) -> &'static str {
    if f == "crates/apps/rusteda-cli/tests/workbench_cli.rs" {
        OWN_HOLDER
    } else if crate::shared::is_shared(f) || f.starts_with("crates/apps/rusteda-flow/") {
        SHARED_HOLDER
    } else {
        OWN_HOLDER
    }
}

/// Checks that no SPDX copyright tag in the text of file `f` names the holder of the other kind
/// of file.
fn check_copyright(f: &str, text: &str, r: &mut Report) {
    let want = holder(f);
    let other = if want == SHARED_HOLDER {
        OWN_HOLDER
    } else {
        SHARED_HOLDER
    };
    for (n, line) in text.lines().enumerate() {
        let named = line
            .split_once(COPYRIGHT_TAG)
            .map(|(_, rest)| rest.trim().trim_end_matches("-->").trim());
        if named == Some(other) {
            r.errors.push(format!(
                "{f}:{}: copyright of {other}; this file belongs to {want}",
                n + 1
            ));
        }
    }
}

/// Top-level entries of the layout rules.
fn top_ok(p: &str) -> bool {
    const DIRS: [&str; 8] = [
        "crates",
        "docs",
        "verification",
        "assets",
        "xtask",
        ".github",
        ".cargo",
        ".config",
    ];
    const TOPS: [&str; 18] = [
        "Cargo.toml",
        "Cargo.lock",
        "README.md",
        "LICENSE",
        "NOTICE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "REUSE.toml",
        "deny.toml",
        "rust-toolchain.toml",
        "rustfmt.toml",
        "clippy.toml",
        "typos.toml",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
    ];
    let first = p.split('/').next().unwrap_or("");
    (p.contains('/') && DIRS.contains(&first)) || TOPS.contains(&p)
}

/// Path, source, SPDX header and size rules of the layout rules, 6.2, 6.6 and 10.7.
fn check_paths(root: &Path, files: &[String], r: &mut Report) {
    const GROUPS: [&str; 6] = ["core", "formats", "engines", "verify", "gui", "apps"];
    for f in files {
        if !top_ok(f) {
            r.errors
                .push(format!("{f}: not an allowed top-level location"));
            continue;
        }
        // Binary files carry no SPDX tags.
        let text = std::fs::read_to_string(root.join(f)).ok();
        if let Some(t) = &text {
            check_copyright(f, t, r);
        }
        let parts: Vec<&str> = f.split('/').collect();
        let part = |i: usize| parts.get(i).copied().unwrap_or("");
        if part(0) == "crates"
            && parts.len() > 1
            && part(1) != "README.md"
            && (parts.len() < 4 || !GROUPS.contains(&part(1)))
        {
            r.errors
                .push(format!("{f}: crates live in crates/<group>/<name>/"));
        }
        if f.ends_with(".rs") {
            let ok = (part(0) == "crates"
                && parts.len() >= 5
                && ["src", "tests", "benches", "examples", "fuzz"].contains(&part(3)))
                || (part(0) == "crates" && parts.len() == 4 && part(3) == "build.rs")
                || (part(0) == "xtask" && part(1) == "src");
            if !ok {
                r.errors.push(format!(
                    "{f}: Rust sources belong in a crate's src/, tests/, benches/, examples/ or fuzz/"
                ));
            }
            let text = text.as_deref().unwrap_or_default();
            if !text.starts_with(crate::SPDX_HEADER) {
                r.errors.push(format!("{f}: missing SPDX header"));
            } else if text.lines().nth(1)
                != Some(format!("// {COPYRIGHT_TAG} {}", holder(f)).as_str())
            {
                r.errors.push(format!(
                    "{f}: the second line must be the SPDX copyright line of {}",
                    holder(f)
                ));
            }
            let lines = text.lines().count();
            if lines > 1200 {
                r.errors.push(format!("{f}: {lines} lines (limit 1200)"));
            }
        }
        let size = std::fs::metadata(root.join(f)).map_or(0, |m| m.len());
        let limit = if f.starts_with("assets/") {
            25 << 20
        } else {
            1 << 20
        };
        if size > limit {
            r.errors
                .push(format!("{f}: {size} bytes exceeds the size limit"));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{Tree, rs, rs_shared};

    // the layout rules and 10.7 rules keep working on a synthetic tree.
    #[test]
    fn path_rules() {
        let t = Tree::new("layout-paths");
        t.file("stray.txt", "x")
            .file("crates/lonely.rs", &rs(""))
            .file("crates/engines/demo-engine/src/lib.rs", "fn main() {}\n")
            .file("crates/engines/demo-engine/src/ok.rs", &rs(""))
            .file("xtask/src/main.rs", &rs(""))
            .file("docs/README.md", "text");
        let r = check(t.root(), &t.files());
        let has = |s: &str| r.errors.iter().any(|e| e.contains(s));
        assert!(has("stray.txt: not an allowed top-level location"));
        assert!(has("crates/lonely.rs: crates live in"));
        assert!(has(
            "crates/engines/demo-engine/src/lib.rs: missing SPDX header"
        ));
        assert!(!has("ok.rs"));
        assert!(!has("xtask/src/main.rs"));
        assert!(!has("docs/README.md"));
    }

    // The shared crates and their specifications belong to the RustEDA contributors, every other
    // file to the RustEDA Studio contributors: Rust sources name their holder on the second line, and a
    // copyright tag of the other holder is an error in any file.
    #[test]
    fn copyright_holders() {
        let t = Tree::new("layout-copyright");
        let tag = |holder: &str| format!("<!-- {COPYRIGHT_TAG} {holder} -->\n# Title\n");
        t.file("crates/core/rusteda-core/src/ok.rs", &rs_shared(""))
            .file("crates/formats/rusteda-formats/tests/own.rs", &rs(""))
            .file("crates/engines/demo-engine/src/ok.rs", &rs(""))
            .file("crates/engines/demo-engine/src/shared.rs", &rs_shared(""))
            .file("xtask/src/main.rs", &rs(""))
            .file("xtask/src/shared.rs", &rs_shared(""))
            .file(
                "crates/core/rusteda-compute/src/short.rs",
                crate::SPDX_HEADER,
            )
            .file("docs/specs/liberty/ok.md", &tag(SHARED_HOLDER))
            .file("docs/specs/sdc/own.md", &tag(OWN_HOLDER))
            .file("docs/specs/sta/ok.md", &tag(OWN_HOLDER))
            .file("docs/specs/cli/shared.md", &tag(SHARED_HOLDER))
            .file("docs/specs/cli/third.md", &tag("2026 Somebody Else"));
        let mut r = Report::default();
        check_paths(t.root(), &t.files(), &mut r);
        let mut e = r.errors;
        e.sort();
        assert_eq!(
            e,
            [
                "crates/core/rusteda-compute/src/short.rs: the second line must be the SPDX copyright line of 2026 RustEDA contributors",
                "crates/engines/demo-engine/src/shared.rs: the second line must be the SPDX copyright line of 2026 RustEDA Studio contributors",
                "crates/engines/demo-engine/src/shared.rs:2: copyright of 2026 RustEDA contributors; this file belongs to 2026 RustEDA Studio contributors",
                "crates/formats/rusteda-formats/tests/own.rs: the second line must be the SPDX copyright line of 2026 RustEDA contributors",
                "crates/formats/rusteda-formats/tests/own.rs:2: copyright of 2026 RustEDA Studio contributors; this file belongs to 2026 RustEDA contributors",
                "docs/specs/cli/shared.md:1: copyright of 2026 RustEDA contributors; this file belongs to 2026 RustEDA Studio contributors",
                "docs/specs/sdc/own.md:1: copyright of 2026 RustEDA Studio contributors; this file belongs to 2026 RustEDA contributors",
            ]
        );
    }
}

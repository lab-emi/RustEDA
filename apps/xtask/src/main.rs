// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Repository automation. Run with `cargo xtask <command>`.
//!
//! - `layout`: checks the repository layout rules on tracked (and untracked, not ignored) files:
//!   locations, crates, SPDX headers, sizes and dependency edges ([`layout`]; STUDIO-REQ-0251).
//! - `coverage`: writes the requirement-to-test matrix to `docs/verification/requirements.md`
//!   and fails on a requirement that nothing cites ([`coverage`]; STUDIO-REQ-0250).
//! - `shared <check|update|diff OTHER>`: checks or rewrites the manifest of the files shared with
//!   the RustEDA tool repositories, or compares them with another repository ([`shared`];
//!   STUDIO-REQ-0252).
//! - `pdk fetch [--dir DIR] [--only NAME...] [--offline VENDOR_DIR]`: downloads the pinned
//!   standard-cell libraries of `verification/pdks.toml` and verifies their hashes ([`pdk`]).

#![allow(clippy::print_stdout)]

mod coverage;
mod foundation;
mod layout;
mod pdk;
mod shared;
#[cfg(test)]
mod testutil;

use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

/// First line every Rust source must have (assembled so that license scanners do not read it as
/// a tag of this file).
pub(crate) const SPDX_HEADER: &str = concat!("// SPDX-", "License-Identifier: Apache-2.0");

/// The workspace root (the parent of this crate's directory).
pub(crate) fn root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .map_or(manifest.clone(), Path::to_path_buf)
}

pub(crate) fn write(path: &Path, text: &str) -> Result<(), String> {
    if let Some(d) = path.parent() {
        std::fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?;
    }
    std::fs::write(path, text).map_err(|e| format!("{}: {e}", path.display()))
}

/// Files to check: tracked files and untracked files that git does not ignore, without the
/// local-only files that are never part of the product tree (agent notes, the scratch, tool and
/// build directories). Other files of a local setup belong in the clone's `.git/info/exclude`,
/// which git applies like `.gitignore`.
fn files() -> Vec<String> {
    let out = Command::new("git")
        .args([
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--full-name",
            ".",
        ])
        .current_dir(root())
        .output();
    let mut v: Vec<String> = match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout)
            .lines()
            .filter_map(|path| {
                let prefix = root().file_name()?.to_str()?.to_owned() + "/";
                Some(path.strip_prefix(&prefix).unwrap_or(path).to_owned())
            })
            .collect(),
        _ => Vec::new(),
    };
    let local = |p: &str| {
        let lower = p.to_ascii_lowercase();
        lower == "agents.md"
            || lower == "claude.md"
            || lower.starts_with(".work/")
            || lower.starts_with("target/")
            || lower.starts_with(".claude/")
    };
    v.retain(|p| !local(p));
    // Files deleted from the working tree but still in the index are not checked.
    let r = root();
    v.retain(|p| r.join(p).exists());
    v.sort();
    v.dedup();
    v
}

/// The commands, printed for a missing or unknown command.
const USAGE: &str = "usage: cargo xtask <command>
  layout
  coverage
  shared <check|update|diff OTHER>
  pdk fetch [--dir DIR] [--only NAME...] [--offline VENDOR_DIR]";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let r = match args.first().map(String::as_str) {
        Some("layout") => layout::run(&root(), &files()),
        Some("coverage") => coverage::run(&root()),
        Some("shared") => shared::run(&root(), args.get(1..).unwrap_or_default()),
        Some("pdk") => {
            pdk::fetch_args(args.get(1..).unwrap_or_default()).and_then(|o| pdk::fetch(&root(), &o))
        }
        _ => Err(USAGE.into()),
    };
    match r {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // STUDIO-REQ-0252: the shared files of the working tree are those of the manifest.
    #[test]
    fn own_shared_files_match_the_manifest() {
        if files().is_empty() {
            return;
        }
        let here = root();
        shared::check(&here).unwrap_or_else(|e| panic!("{e}"));
        for file in shared::FILES {
            assert!(here.join(file).is_file(), "required shared input: {file}");
        }
        let canonical = foundation::root(&here)
            .unwrap_or_else(|e| panic!("{e}"))
            .expect("canonical foundation dependency");
        for (group, name) in shared::CRATES {
            let relative = format!("crates/{group}/{name}/src/lib.rs");
            assert!(canonical.join(&relative).is_file(), "{relative}");
            if group != "apps" {
                assert!(!here.join(&relative).exists(), "duplicated {relative}");
            }
        }
        assert!(here.join("crates/apps/rusteda-cli/src/lib.rs").is_file());
    }

    // STUDIO-REQ-0251: the repository's own tree passes its layout rules (sources, headers,
    // places of crates and data, dependency edges).
    #[test]
    fn own_tree_passes_layout() {
        let files = files();
        if files.is_empty() {
            // Not a git work tree (a source archive): nothing to list.
            return;
        }
        let r = layout::check(&root(), &files);
        assert!(r.errors.is_empty(), "{}", r.errors.join("\n"));
    }
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! `cargo xtask shared <check|update|diff OTHER>`: the files that every RustEDA tool repository
//! shares verbatim, and their manifest [`MANIFEST`].
//!
//! The shared files are the files below the shared crates ([`CRATES`]) and their specifications
//! ([`DOCS`]), and the shared automation files ([`FILES`]); a shared path that a repository does
//! not have is skipped. In a git work tree they are the files that git tracks and the untracked
//! files that it does not ignore, so that editor backups and other ignored local files never
//! count. The manifest has one line per shared file, `<sha256>  <path>` (lower-case hexadecimal,
//! two spaces, the path relative to the repository root with `/` separators), sorted by path,
//! with LF line endings: the format of `sha256sum`, so that `sha256sum -c verification/shared.sha256`
//! verifies it too.
//!
//! - `check` fails unless the manifest is the one `update` writes: every shared file of the
//!   working tree listed with its SHA-256, and nothing else;
//! - `update` writes the manifest of the working tree;
//! - `diff OTHER` lists the shared files that differ between this repository and the one at
//!   `OTHER`, or that only one of them has, and fails when there is any.

mod sha256;
#[cfg(test)]
mod tests;

use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;

/// The shared crates: group and package name. Each lives at `crates/<group>/<name>/`.
pub(crate) const CRATES: [(&str, &str); 5] = [
    ("core", "rusteda-core"),
    ("core", "rusteda-workspace"),
    ("core", "rusteda-compute"),
    ("formats", "rusteda-formats"),
    ("apps", "rusteda-cli"),
];

/// The specifications of the shared crates, which their sources cite.
pub(crate) const DOCS: [&str; 9] = [
    "docs/specs/core",
    "docs/specs/workspace",
    "docs/specs/compute",
    "docs/specs/verilog",
    "docs/specs/liberty",
    "docs/specs/sdc",
    "docs/specs/rusteda-cli",
    "docs/specs/blif",
    "docs/specs/aiger",
];

/// Individual files outside [`CRATES`] and [`DOCS`] that are shared verbatim across the tool
/// repositories.
pub(crate) const FILES: [&str; 8] = [
    "verification/pdks.toml",
    "xtask/src/foundation.rs",
    "xtask/src/pdk/fetch.rs",
    "xtask/src/pdk/json.rs",
    "xtask/src/pdk/manifest.rs",
    "xtask/src/shared.rs",
    "xtask/src/shared/sha256.rs",
    "xtask/src/shared/tests.rs",
];

/// The manifest of the shared files, relative to the repository root.
pub(crate) const MANIFEST: &str = "verification/shared.sha256";

/// The shared directories, relative to the repository root, without a trailing `/`.
fn dirs() -> Vec<String> {
    CRATES
        .iter()
        .map(|(group, name)| format!("crates/{group}/{name}"))
        .chain(DOCS.iter().map(|d| d.to_string()))
        .collect()
}

/// The shared directories and individual shared files, relative to the repository root.
fn paths() -> Vec<String> {
    dirs()
        .into_iter()
        .chain(FILES.iter().map(|f| f.to_string()))
        .collect()
}

/// True when the repository-relative path `p` is a shared file or lies below a shared directory.
pub(crate) fn is_shared(p: &str) -> bool {
    FILES.contains(&p)
        || dirs().iter().any(|d| {
            p.strip_prefix(d.as_str())
                .is_some_and(|rest| rest.starts_with('/'))
        })
}

/// Every file below `dir`, as `/`-separated paths relative to `root`; links to directories are not
/// followed.
fn walk(root: &Path, dir: &Path, out: &mut Vec<String>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for e in rd.flatten() {
        let p = e.path();
        if e.file_type().is_ok_and(|t| t.is_dir()) {
            walk(root, &p, out);
        } else if p.is_file()
            && let Ok(rel) = p.strip_prefix(root)
        {
            let parts: Vec<String> = rel
                .components()
                .map(|c| c.as_os_str().to_string_lossy().into_owned())
                .collect();
            out.push(parts.join("/"));
        }
    }
}

/// The files below the shared paths that git lists for the work tree at `root`: tracked files and
/// untracked files that git does not ignore, as the layout check takes them. `None` when `root` is
/// not the top of a git work tree or git cannot run.
fn git_files(root: &Path) -> Option<Vec<String>> {
    if !root.join(".git").exists() {
        return None;
    }
    let o = Command::new("git")
        .args([
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
        ])
        .args(paths())
        .current_dir(root)
        .output()
        .ok()
        .filter(|o| o.status.success())?;
    Some(
        o.stdout
            .split(|b| *b == 0)
            .filter(|p| !p.is_empty())
            .map(|p| String::from_utf8_lossy(p).into_owned())
            // Not files of the work tree: files deleted from it but still in the index, and links
            // to directories.
            .filter(|p| root.join(p).is_file())
            .collect(),
    )
}

/// The shared files of the repository at `root`: those that git lists, or without git every file
/// below the shared directories and in [`FILES`].
fn files(root: &Path) -> Vec<String> {
    git_files(root).unwrap_or_else(|| {
        let mut out = Vec::new();
        for d in dirs() {
            walk(root, &root.join(&d), &mut out);
        }
        for f in FILES {
            if root.join(f).is_file() {
                out.push(f.to_string());
            }
        }
        out
    })
}

/// The shared files of the repository at `root` with their SHA-256, by path.
fn hashes(root: &Path) -> Result<BTreeMap<String, String>, String> {
    let mut out = BTreeMap::new();
    for f in files(root) {
        if f.contains(['\n', '\r', '\\']) {
            return Err(format!(
                "{f}: a shared file name must not contain a line break or a backslash"
            ));
        }
        let data = std::fs::read(root.join(&f)).map_err(|e| format!("{f}: {e}"))?;
        out.insert(f, sha256::hex(&data));
    }
    Ok(out)
}

/// The manifest text of `hashes`.
fn text(hashes: &BTreeMap<String, String>) -> String {
    hashes.iter().map(|(p, h)| format!("{h}  {p}\n")).collect()
}

/// The entries of a manifest text, by path, and its lines that are not `<sha256>  <path>`.
fn entries(text: &str) -> (BTreeMap<String, String>, Vec<String>) {
    let mut map = BTreeMap::new();
    let mut bad = Vec::new();
    for (n, line) in text.lines().enumerate() {
        match line.split_once("  ") {
            Some((h, p))
                if h.len() == 64
                    && h.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
                    && !p.is_empty() =>
            {
                map.insert(p.to_string(), h.to_string());
            }
            _ => bad.push(format!(
                "{MANIFEST}:{}: not a `<sha256>  <path>` line",
                n + 1
            )),
        }
    }
    (map, bad)
}

/// Checks the manifest of the repository at `root` against its working tree; returns the number
/// of shared files.
pub(crate) fn check(root: &Path) -> Result<usize, String> {
    let want = hashes(root)?;
    let have_text = std::fs::read_to_string(root.join(MANIFEST))
        .map_err(|e| format!("{MANIFEST}: {e}\n`cargo xtask shared update` writes the manifest"))?;
    if have_text == text(&want) {
        return Ok(want.len());
    }
    let (have, mut problems) = entries(&have_text);
    for (p, h) in &want {
        match have.get(p) {
            None => problems.push(format!("{p}: shared file not listed in {MANIFEST}")),
            Some(x) if x != h => {
                problems.push(format!("{p}: SHA-256 {h}, but {MANIFEST} lists {x}"))
            }
            Some(_) => {}
        }
    }
    for p in have.keys().filter(|p| !want.contains_key(*p)) {
        problems.push(format!(
            "{p}: listed in {MANIFEST} but not a shared file of the working tree"
        ));
    }
    if problems.is_empty() {
        problems.push(format!(
            "{MANIFEST}: not in its canonical form (one `<sha256>  <path>` line per shared file, \
             sorted by path, LF line endings)"
        ));
    }
    problems.push(
        "after a deliberate change of shared files, `cargo xtask shared update` rewrites the \
         manifest"
            .into(),
    );
    Err(problems.join("\n"))
}

/// Writes the manifest of the repository at `root`; returns the number of shared files.
pub(crate) fn update(root: &Path) -> Result<usize, String> {
    let h = hashes(root)?;
    crate::write(&root.join(MANIFEST), &text(&h))?;
    Ok(h.len())
}

/// The differences between the shared files of the repositories at `here` and `there`, one line
/// per file in path order: `differs: <path>`, `only here: <path>` or `only there: <path>`; and the
/// number of shared files of both together.
pub(crate) fn diff(here: &Path, there: &Path) -> Result<(Vec<String>, usize), String> {
    let a = hashes(here)?;
    let b = hashes(there)?;
    let mut all: Vec<&String> = a.keys().chain(b.keys()).collect();
    all.sort();
    all.dedup();
    let lines = all
        .iter()
        .filter_map(|p| match (a.get(*p), b.get(*p)) {
            (Some(x), Some(y)) if x != y => Some(format!("differs: {p}")),
            (Some(_), None) => Some(format!("only here: {p}")),
            (None, Some(_)) => Some(format!("only there: {p}")),
            _ => None,
        })
        .collect();
    Ok((lines, all.len()))
}

/// The `shared` command with its arguments.
pub(crate) fn run(root: &Path, args: &[String]) -> Result<(), String> {
    let usage = "usage: cargo xtask shared <check|update|diff OTHER-REPOSITORY>";
    match args {
        [c] if c == "check" => {
            let n = check(root)?;
            println!("shared files ok: {n} files match {MANIFEST}");
            Ok(())
        }
        [c] if c == "update" => {
            let n = update(root)?;
            println!("wrote {MANIFEST}: {n} files");
            Ok(())
        }
        [c, other] if c == "diff" => {
            let other = Path::new(other);
            if !other.is_dir() {
                return Err(format!("{}: not a directory", other.display()));
            }
            let (lines, n) = diff(root, other)?;
            for l in &lines {
                println!("{l}");
            }
            if lines.is_empty() {
                println!("shared files identical: {n} files");
                Ok(())
            } else {
                Err(format!(
                    "{} of {n} shared files differ or exist in one repository only",
                    lines.len()
                ))
            }
        }
        _ => Err(usage.into()),
    }
}

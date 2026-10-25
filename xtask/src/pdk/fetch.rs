// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! `cargo xtask pdk fetch`: downloads the libraries of the manifest `verification/pdks.toml`
//! ([`super::manifest`]) into the library directory with `curl`, verifies every file with
//! `sha256sum` (or `shasum -a 256`) and assembles the libraries that upstream ships as per-cell
//! fragments ([`super::manifest::assemble`]).
//!
//! The library directory is `--dir`, else `$RUSTEDA_PDK_DIR`, else `target/pdk` of the repository
//! ([`library_dir`]). It never lies in the tracked tree or in a directory that the repository's
//! own commands delete ([`guard`]).
//!
//! A library file that is present with the recorded SHA-256 is kept without network access. The
//! parts of an assembled library are kept below `sources/<name>/` in the library directory (the
//! upstream paths), so a later assembly needs no network either; when the kept parts do not
//! assemble, or their assembly does not match its SHA-256, they are downloaded once more.
//! Downloads go to a `.part` file that is renamed only when complete, and a library file is
//! renamed into place only after its hash matched. A failed download is attempted again unless
//! another attempt cannot change the result ([`FINAL`]), and no download of parts starts after
//! one has failed, so an unreachable server is reported within one attempt per library.
//!
//! This file is shared by the RustEDA tool repositories: it, `manifest.rs`, `json.rs` and
//! `verification/pdks.toml` are identical in each of them, use only the standard library and
//! each other, and change in all of them at once.

use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::Duration;

use super::manifest::{self, Library, Source};

/// The environment variable that names the library directory.
pub(crate) const DIR_VAR: &str = "RUSTEDA_PDK_DIR";
/// Attempts per download. `curl` itself retries timeouts and the HTTP statuses 408, 429, 500,
/// 502, 503, 504, 522 and 524 (`--retry`); the attempts here cover its other errors, except the
/// [`FINAL`] ones.
const ATTEMPTS: u32 = 3;
/// `curl` exit statuses that end a download at once: 7 (no connection to the host or the proxy),
/// 22 (an HTTP status of 400 or more), 28 (a timeout) and 37 (a `file://` file that cannot be
/// read). The transient cases of 22 and 28 have been retried inside `curl` already.
const FINAL: [i32; 4] = [7, 22, 28, 37];
/// Parallel downloads of fragments.
const WORKERS: usize = 8;

/// The library directory: `dir`, else the environment value `env` (when not empty), else
/// `target/pdk` below `root`; a relative path is taken from the current directory.
pub(crate) fn library_dir(root: &Path, dir: Option<&Path>, env: Option<OsString>) -> PathBuf {
    let chosen = match (dir, env) {
        (Some(d), _) => d.to_path_buf(),
        (None, Some(e)) if !e.is_empty() => PathBuf::from(e),
        _ => root.join("target").join("pdk"),
    };
    std::path::absolute(&chosen).unwrap_or(chosen)
}

/// The programs the library directory needs: a SHA-256 program, and `curl` to download.
#[derive(Clone, Debug)]
pub(crate) struct Tools {
    hasher: (&'static str, &'static [&'static str]),
}

fn runs(program: &str) -> bool {
    Command::new(program)
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok()
}

impl Tools {
    /// Finds `sha256sum` (else `shasum -a 256`) and, with `download`, `curl`.
    pub(crate) fn find(download: bool) -> Result<Tools, String> {
        if download && !runs("curl") {
            return Err(
                "`curl` was not found on PATH; install curl to download the libraries".into(),
            );
        }
        let hasher: (&'static str, &'static [&'static str]) = if runs("sha256sum") {
            ("sha256sum", &[])
        } else if runs("shasum") {
            ("shasum", &["-a", "256"])
        } else {
            return Err(
                "neither `sha256sum` nor `shasum` was found on PATH; install coreutils \
                        (sha256sum) or Perl's shasum to verify the libraries"
                    .into(),
            );
        };
        Ok(Tools { hasher })
    }

    /// The SHA-256 of a file, in lower-case hexadecimal.
    fn sha256(&self, path: &Path) -> Result<String, String> {
        let (program, args) = self.hasher;
        let out = Command::new(program)
            .args(args)
            .arg(path)
            .output()
            .map_err(|e| format!("{program}: {e}"))?;
        let text = String::from_utf8_lossy(&out.stdout);
        // A name with a backslash or newline makes `sha256sum` start the line with a backslash.
        let digest = text
            .split_whitespace()
            .next()
            .map(|d| d.trim_start_matches('\\').to_ascii_lowercase())
            .unwrap_or_default();
        if out.status.success()
            && digest.len() == 64
            && digest.bytes().all(|c| c.is_ascii_hexdigit())
        {
            Ok(digest)
        } else {
            Err(format!(
                "{program} {}: {}",
                path.display(),
                String::from_utf8_lossy(&out.stderr).trim()
            ))
        }
    }

    /// `Ok(None)` when `path` has the library's size and SHA-256, else the difference found.
    pub(crate) fn mismatch(&self, path: &Path, lib: &Library) -> Result<Option<String>, String> {
        let size = std::fs::metadata(path)
            .map_err(|e| format!("{}: {e}", path.display()))?
            .len();
        if size != lib.size {
            return Ok(Some(format!("size {size} bytes, expected {}", lib.size)));
        }
        let got = self.sha256(path)?;
        Ok((got != lib.sha256).then(|| format!("SHA-256 {got}, expected {}", lib.sha256)))
    }
}

/// `path` made absolute: its longest existing part with symbolic links resolved, then the rest
/// (which holds no links) applied component by component.
fn resolve(path: &Path) -> Result<PathBuf, String> {
    let abs = std::path::absolute(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let comps: Vec<Component> = abs.components().collect();
    let mut k = comps.len();
    let mut out = loop {
        let prefix: PathBuf = comps.iter().take(k).collect();
        if prefix.exists() {
            break prefix
                .canonicalize()
                .map_err(|e| format!("{}: {e}", prefix.display()))?;
        }
        if k == 0 {
            return Err(format!("{}: cannot resolve the directory", path.display()));
        }
        k -= 1;
    };
    for c in comps.iter().skip(k) {
        match c {
            Component::ParentDir => {
                out.pop();
            }
            Component::Normal(name) => out.push(name),
            _ => {}
        }
    }
    Ok(out)
}

/// Whether `dir` is `base` or lies below it, with symbolic links resolved in both.
pub(crate) fn inside(dir: &Path, base: &Path) -> Result<bool, String> {
    Ok(resolve(dir)?.starts_with(resolve(base)?))
}

/// Refuses a library directory that would lose its files or enter the tracked tree:
///
/// - one at or below a directory of `outputs`, which the repository's own commands delete and
///   write again (the outputs of its suite);
/// - one inside the repository unless git ignores it and tracks nothing below it: downloaded
///   libraries never enter the tracked tree, where the only design data allowed are the
///   repository's own fixture files. Without git, only `target/` of the repository is accepted.
fn guard(root: &Path, dir: &Path, outputs: &[PathBuf]) -> Result<(), String> {
    let refuse = |why: &str| {
        Err(format!(
            "refusing to write libraries to {}: {why}; use another directory below target/ (the \
             default is target/pdk) or one outside the repository",
            dir.display()
        ))
    };
    for out in outputs {
        if inside(dir, out)? {
            return refuse(&format!(
                "it is at or below {}, which this repository's commands delete and write again",
                out.display()
            ));
        }
    }
    let target = resolve(dir)?;
    let root = resolve(root)?;
    let Ok(rel) = target.strip_prefix(&root) else {
        return Ok(());
    };
    if rel.as_os_str().is_empty() {
        return refuse("it is the repository root");
    }
    let rel = rel.to_string_lossy().replace('\\', "/");
    let git = |args: &[&str]| {
        Command::new("git")
            .arg("-C")
            .arg(&root)
            .args(args)
            .stderr(Stdio::null())
            .output()
            .ok()
    };
    let ignored = git(&["check-ignore", "-q", "--", &format!("{rel}/")]).map(|o| o.status.code());
    match ignored {
        Some(Some(0)) => {}
        Some(Some(1)) => return refuse("it is inside the repository and git does not ignore it"),
        _ => {
            if rel.split('/').next() != Some("target") {
                return refuse("git cannot tell whether it is ignored");
            }
            return Ok(());
        }
    }
    match git(&["ls-files", "--", &rel]) {
        Some(o) if o.status.success() && o.stdout.is_empty() => Ok(()),
        Some(o) if o.status.success() => refuse("git tracks files below it"),
        _ => refuse("git cannot list its tracked files"),
    }
}

/// `dest` with `.part` appended to its file name.
fn part_path(dest: &Path) -> PathBuf {
    let mut name = dest.file_name().unwrap_or_default().to_os_string();
    name.push(".part");
    dest.with_file_name(name)
}

/// Whether another attempt may change the outcome of a `curl` run that ended with `status`.
fn retryable(status: Option<i32>) -> bool {
    !status.is_some_and(|c| FINAL.contains(&c))
}

/// Downloads `url` to `dest` (through `dest.part`, renamed when complete), with retries.
fn download(url: &str, dest: &Path) -> Result<(), String> {
    if let Some(d) = dest.parent() {
        std::fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?;
    }
    let part = part_path(dest);
    let mut last = String::new();
    for attempt in 1..=ATTEMPTS {
        let out = Command::new("curl")
            .args(["--fail", "--silent", "--show-error", "--location"])
            .args(["--connect-timeout", "30", "--max-time", "900"])
            .args(["--retry", "3", "--retry-delay", "2", "--output"])
            .arg(&part)
            .arg(url)
            .stdout(Stdio::null())
            .output()
            .map_err(|e| format!("curl: {e}"))?;
        if out.status.success() {
            return std::fs::rename(&part, dest).map_err(|e| format!("{}: {e}", dest.display()));
        }
        let _ = std::fs::remove_file(&part);
        last = String::from_utf8_lossy(&out.stderr).trim().to_string();
        if !retryable(out.status.code()) {
            return Err(format!("{url}: download failed: {last}"));
        }
        if attempt < ATTEMPTS {
            std::thread::sleep(Duration::from_secs(2 * u64::from(attempt)));
        }
    }
    Err(format!(
        "{url}: download failed after {ATTEMPTS} attempts: {last}"
    ))
}

/// Downloads the parts (`base_url` + path, to `dir`/path) that `dir` lacks, several at a time,
/// and returns how many it downloaded. No download starts after one has failed; the parts
/// downloaded until then are kept.
fn download_parts(base_url: &str, paths: &[&str], dir: &Path) -> Result<usize, String> {
    let todo: Vec<&str> = paths
        .iter()
        .copied()
        .filter(|p| !dir.join(p).is_file())
        .collect();
    let next = AtomicUsize::new(0);
    let done = AtomicUsize::new(0);
    let failed = AtomicBool::new(false);
    let errors = Mutex::new(Vec::new());
    std::thread::scope(|s| {
        for _ in 0..WORKERS.min(todo.len()) {
            s.spawn(|| {
                while !failed.load(Ordering::Relaxed) {
                    let Some(p) = todo.get(next.fetch_add(1, Ordering::Relaxed)) else {
                        break;
                    };
                    match download(&format!("{base_url}{p}"), &dir.join(p)) {
                        Ok(()) => {
                            done.fetch_add(1, Ordering::Relaxed);
                        }
                        Err(e) => {
                            failed.store(true, Ordering::Relaxed);
                            errors.lock().unwrap_or_else(|e| e.into_inner()).push(e);
                        }
                    }
                }
            });
        }
    });
    let errors = errors.into_inner().unwrap_or_else(|e| e.into_inner());
    match errors.first() {
        None => Ok(todo.len()),
        Some(first) => Err(format!(
            "{first} ({} of {} parts downloaded, {} failed; no download starts after a failure)",
            done.into_inner(),
            todo.len(),
            errors.len()
        )),
    }
}

/// Renames the staged file `part` to `dest` when its size and SHA-256 are the library's; otherwise
/// removes it and returns the difference.
fn commit(
    part: &Path,
    dest: &Path,
    lib: &Library,
    tools: &Tools,
) -> Result<Option<String>, String> {
    let found = tools.mismatch(part, lib);
    if !matches!(found, Ok(None)) {
        let _ = std::fs::remove_file(part);
        return found;
    }
    std::fs::rename(part, dest).map_err(|e| format!("{}: {e}", dest.display()))?;
    Ok(None)
}

/// The library assembled from its parts below `src` (upstream paths).
fn assemble_from(src: &Path, header: &str, fragments: &[String]) -> Result<Vec<u8>, String> {
    let read = |p: &str| {
        let f = src.join(p);
        std::fs::read(&f).map_err(|e| format!("{}: {e}", f.display()))
    };
    let parts = fragments
        .iter()
        .map(|p| read(p))
        .collect::<Result<Vec<_>, String>>()?;
    manifest::assemble(&read(header)?, &parts).map_err(|e| format!("{header}: {e}"))
}

/// Assembles the library from its parts below `src` into `part` and commits it to `dest`
/// ([`commit`]). `Ok(Some(why))` when the parts do not give the library: a part cannot be read,
/// they do not assemble (a page served in place of a file, for example) or the result differs
/// from the manifest.
fn assemble_into(
    src: &Path,
    header: &str,
    fragments: &[String],
    part: &Path,
    dest: &Path,
    lib: &Library,
    tools: &Tools,
) -> Result<Option<String>, String> {
    let data = match assemble_from(src, header, fragments) {
        Ok(data) => data,
        Err(e) => return Ok(Some(format!("the parts do not assemble ({e})"))),
    };
    std::fs::write(part, data).map_err(|e| format!("{}: {e}", part.display()))?;
    Ok(commit(part, dest, lib, tools)?
        .map(|why| format!("the assembled library differs from the manifest ({why})")))
}

/// How a library came to be in place.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Outcome {
    /// Present with the recorded hash already.
    Present,
    /// Downloaded (or assembled) now.
    Fetched,
}

/// Puts one library into `dir` (see the module documentation).
fn library(lib: &Library, dir: &Path, tools: &Tools) -> Result<Outcome, String> {
    let dest = dir.join(lib.file_name());
    if dest.is_file() {
        match tools.mismatch(&dest, lib)? {
            None => return Ok(Outcome::Present),
            Some(why) => println!("{}: present file differs ({why}); fetching again", lib.name),
        }
    }
    let part = part_path(&dest);
    match &lib.source {
        Source::File { url } => {
            println!("{}: downloading {url}", lib.name);
            download(url, &part)?;
            match commit(&part, &dest, lib, tools)? {
                None => Ok(Outcome::Fetched),
                Some(why) => Err(format!(
                    "the download differs from the manifest ({why}); it was not kept"
                )),
            }
        }
        Source::Assembled {
            base_url,
            header,
            fragments,
        } => {
            let src = dir.join("sources").join(&lib.name);
            let paths: Vec<&str> = std::iter::once(header.as_str())
                .chain(fragments.iter().map(String::as_str))
                .collect();
            let mut why = String::new();
            for round in 0..2 {
                if round == 1 {
                    println!("{}: {why}; downloading the parts again", lib.name);
                    for p in &paths {
                        let _ = std::fs::remove_file(src.join(p));
                    }
                }
                let n = download_parts(base_url, &paths, &src)?;
                println!(
                    "{}: {} parts below {base_url} ({n} downloaded, {} kept in {})",
                    lib.name,
                    paths.len(),
                    paths.len() - n,
                    src.display()
                );
                match assemble_into(&src, header, fragments, &part, &dest, lib, tools)? {
                    None => return Ok(Outcome::Fetched),
                    Some(w) => why = w,
                }
                // Parts that were all downloaded just now are not downloaded once more.
                if n == paths.len() {
                    break;
                }
            }
            Err(format!(
                "{why}, with every part downloaded just now: the parts below {base_url} or their \
                 order differ from the manifest; the library was not kept (the parts are in {})",
                src.display()
            ))
        }
    }
}

/// Fetches every library into `dir`, which [`guard`] accepts with the repository's `outputs`;
/// every library is attempted, failures are reported together.
pub(crate) fn run(
    root: &Path,
    libs: &[Library],
    dir: &Path,
    outputs: &[PathBuf],
) -> Result<(), String> {
    guard(root, dir, outputs)?;
    let tools = Tools::find(true)?;
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let mut failed = Vec::new();
    for lib in libs {
        match library(lib, dir, &tools) {
            Ok(Outcome::Present) => {
                println!("{}: {} present, SHA-256 ok", lib.name, lib.file_name())
            }
            Ok(Outcome::Fetched) => {
                println!("{}: {} fetched, SHA-256 ok", lib.name, lib.file_name())
            }
            Err(e) => {
                eprintln!("{}: {e}", lib.name);
                failed.push(lib.name.as_str());
            }
        }
    }
    if failed.is_empty() {
        println!("{} libraries in {}", libs.len(), dir.display());
        Ok(())
    } else {
        Err(format!("pdk fetch: failed for {}", failed.join(", ")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A directory below the operating system's temporary directory, removed when dropped.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new(tag: &str) -> Scratch {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            loop {
                let n = NEXT.fetch_add(1, Ordering::Relaxed);
                let root = std::env::temp_dir()
                    .join(format!("pdk-fetch-test-{}-{tag}-{n}", std::process::id()));
                match std::fs::create_dir(&root) {
                    Ok(()) => return Scratch(root),
                    Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists && n < 1 << 20 => {}
                    Err(e) => panic!("{}: {e}", root.display()),
                }
            }
        }

        fn root(&self) -> &Path {
            &self.0
        }

        /// Writes `text` to the relative path `rel`, creating directories.
        fn file(&self, rel: &str, text: &str) -> &Scratch {
            let p = self.0.join(rel);
            if let Some(d) = p.parent() {
                std::fs::create_dir_all(d).unwrap_or_else(|e| panic!("{}: {e}", d.display()));
            }
            std::fs::write(&p, text).unwrap_or_else(|e| panic!("{}: {e}", p.display()));
            self
        }

        /// Every file below the root, as sorted `/`-separated relative paths.
        fn files(&self) -> Vec<String> {
            let mut out = Vec::new();
            let mut stack = vec![self.0.clone()];
            while let Some(d) = stack.pop() {
                for e in std::fs::read_dir(&d).into_iter().flatten().flatten() {
                    let p = e.path();
                    if p.is_dir() {
                        stack.push(p);
                    } else if let Ok(rel) = p.strip_prefix(&self.0) {
                        out.push(rel.to_string_lossy().replace('\\', "/"));
                    }
                }
            }
            out.sort();
            out
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn strings(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    /// The repository root: the parent of the `xtask` crate.
    fn repository() -> Option<&'static Path> {
        Path::new(env!("CARGO_MANIFEST_DIR")).parent()
    }

    // The library directory: `--dir`, else a non-empty environment value, else target/pdk;
    // relative paths become absolute.
    #[test]
    fn library_directory() {
        let root = Path::new("/repo");
        assert_eq!(
            library_dir(root, Some(Path::new("/libs")), Some("/env".into())),
            PathBuf::from("/libs")
        );
        assert_eq!(
            library_dir(root, None, Some("/env".into())),
            PathBuf::from("/env")
        );
        assert_eq!(
            library_dir(root, None, Some("".into())),
            PathBuf::from("/repo/target/pdk")
        );
        assert_eq!(
            library_dir(root, None, None),
            PathBuf::from("/repo/target/pdk")
        );
        let rel = library_dir(root, Some(Path::new("libs")), None);
        assert!(
            rel.is_absolute() && rel.ends_with("libs"),
            "{}",
            rel.display()
        );
        assert_eq!(DIR_VAR, "RUSTEDA_PDK_DIR");
    }

    // Downloaded libraries never land in tracked places: inside the repository only ignored
    // directories (target/) are accepted, outside it any directory.
    #[test]
    fn library_directory_guard() {
        let Some(root) = repository() else {
            return;
        };
        assert!(guard(root, &root.join("target/pdk"), &[]).is_ok());
        assert!(guard(root, &root.join("target/pdk/../pdk-other"), &[]).is_ok());
        for bad in ["verification/pdk", "docs", "xtask/src/pdk", "."] {
            let e = guard(root, &root.join(bad), &[]).err().unwrap_or_default();
            assert!(e.contains("refusing"), "{bad}: {e}");
        }
        let t = Scratch::new("guard");
        assert!(guard(root, &t.root().join("libs"), &[]).is_ok());
    }

    // A library directory at or below an output directory of the repository is refused, inside
    // the repository or outside it, also when reached through a symbolic link; a sibling of the
    // output directory is accepted.
    #[test]
    fn library_directory_inside_outputs() {
        let Some(root) = repository() else {
            return;
        };
        let out = [root.join("target/suite-out")];
        for bad in [
            "target/suite-out",
            "target/suite-out/libs",
            "target/pdk/../suite-out/libs",
        ] {
            let e = guard(root, &root.join(bad), &out).err().unwrap_or_default();
            assert!(e.contains("refusing") && e.contains("delete"), "{bad}: {e}");
        }
        assert!(guard(root, &root.join("target/suite-out-libs"), &out).is_ok());
        assert!(guard(root, &root.join("target/pdk"), &out).is_ok());
        let t = Scratch::new("outputs");
        t.file("out/keep.txt", "");
        let outside = [t.root().join("out")];
        assert!(guard(root, &t.root().join("out/libs"), &outside).is_err());
        assert!(guard(root, &t.root().join("libs"), &outside).is_ok());
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(t.root().join("out"), t.root().join("link"))
                .unwrap_or_else(|e| panic!("{e}"));
            let e = guard(root, &t.root().join("link/libs"), &outside)
                .err()
                .unwrap_or_default();
            assert!(e.contains("refusing"), "{e}");
            assert_eq!(inside(&t.root().join("link"), &outside[0]), Ok(true));
        }
    }

    const COMMIT: &str = "0123456789abcdef0123456789abcdef01234567";

    fn demo_library(base_url: String, sha256: String, size: u64) -> Library {
        Library {
            name: "demo_tt".into(),
            library: "demo".into(),
            corner: "tt".into(),
            repo: "https://example.org/demo".into(),
            commit: COMMIT.into(),
            license: "Apache-2.0".into(),
            source: Source::Assembled {
                base_url,
                header: "liberty/demo__tt.lib".into(),
                fragments: strings(&["cells/b/demo__b_1__tt.lib", "cells/a/demo__a_2__tt.lib"]),
            },
            sha256,
            size,
        }
    }

    // An offline fetch through `file://` URLs: an assembled library is compared with its hash
    // and placed; once present it is kept without downloading; a damaged copy is assembled again
    // from the kept parts; kept parts that do not assemble (a page saved in place of the header)
    // or that give another library are downloaded again; parts that were all downloaded just now
    // are not; a library that does not match its hash is never placed. Skipped without curl or a
    // SHA-256 program.
    #[test]
    fn fetch_offline() {
        let Ok(tools) = Tools::find(true) else {
            return;
        };
        let t = Scratch::new("fetch");
        let expected =
            "library (demo) {\n  time_unit : \"1ns\";\n\n  cell (b_1) {}\n\n  cell (a_2) {}\n}\n";
        let header = "library (demo) {\n  time_unit : \"1ns\";\n}\n\n";
        let page = "<html><body>Sign in to continue</body></html>\n";
        t.file("remote/liberty/demo__tt.lib", header)
            .file("remote/cells/b/demo__b_1__tt.lib", "  cell (b_1) {}\n")
            .file("remote/cells/a/demo__a_2__tt.lib", "  cell (a_2) {}")
            .file("remote/whole.lib", expected)
            .file("portal/liberty/demo__tt.lib", page)
            .file("portal/cells/b/demo__b_1__tt.lib", page)
            .file("portal/cells/a/demo__a_2__tt.lib", page)
            .file("expected.lib", expected);
        let sha = tools
            .sha256(&t.root().join("expected.lib"))
            .unwrap_or_else(|e| panic!("{e}"));
        let base = format!("file://{}/remote/", t.root().display());
        let dir = t.root().join("cache");
        let placed = dir.join("demo__tt.lib");
        let kept = |p: &str| dir.join("sources/demo_tt").join(p);
        let read = |p: &Path| std::fs::read_to_string(p).unwrap_or_default();
        let write =
            |p: &Path, text: &str| std::fs::write(p, text).unwrap_or_else(|e| panic!("{e}"));
        let remove = |p: &Path| std::fs::remove_file(p).unwrap_or_else(|e| panic!("{e}"));

        let wrong = demo_library(base.clone(), "0".repeat(64), expected.len() as u64);
        let e = library(&wrong, &dir, &tools).err().unwrap_or_default();
        assert!(
            e.contains("differs from the manifest") && e.contains("every part downloaded"),
            "{e}"
        );
        assert!(!placed.exists());
        let mut whole = demo_library(base.clone(), "0".repeat(64), expected.len() as u64);
        whole.source = Source::File {
            url: format!("{base}whole.lib"),
        };
        assert!(library(&whole, &dir, &tools).is_err_and(|e| e.contains("SHA-256")));
        assert!(!placed.exists());
        whole.sha256 = sha.clone();
        assert_eq!(library(&whole, &dir, &tools), Ok(Outcome::Fetched));
        remove(&placed);

        let lib = demo_library(base, sha.clone(), expected.len() as u64);
        assert_eq!(library(&lib, &dir, &tools), Ok(Outcome::Fetched));
        assert_eq!(read(&placed), expected);
        // A kept header that does not assemble, and a kept fragment that assembles into another
        // library, are both replaced by a new download.
        write(&kept("liberty/demo__tt.lib"), page);
        remove(&placed);
        assert_eq!(library(&lib, &dir, &tools), Ok(Outcome::Fetched));
        assert_eq!(
            (read(&placed), read(&kept("liberty/demo__tt.lib"))),
            (expected.to_string(), header.to_string())
        );
        write(&kept("cells/b/demo__b_1__tt.lib"), "  cell (c_1) {}\n");
        remove(&placed);
        assert_eq!(library(&lib, &dir, &tools), Ok(Outcome::Fetched));
        assert_eq!(read(&placed), expected);
        // Downloaded parts that do not assemble fail at once.
        let portal = demo_library(
            format!("file://{}/portal/", t.root().display()),
            sha,
            expected.len() as u64,
        );
        let other = t.root().join("other");
        let e = library(&portal, &other, &tools).err().unwrap_or_default();
        assert!(
            e.contains("the parts do not assemble (liberty/demo__tt.lib: the header does not end")
                && e.contains("every part downloaded"),
            "{e}"
        );
        assert!(!other.join("demo__tt.lib").exists());

        std::fs::remove_dir_all(t.root().join("remote")).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(library(&lib, &dir, &tools), Ok(Outcome::Present));
        write(&placed, "damaged");
        assert_eq!(library(&lib, &dir, &tools), Ok(Outcome::Fetched));
        assert_eq!(read(&placed), expected);
        // Without a source, kept parts that do not assemble fail the fetch at the first download.
        write(&kept("liberty/demo__tt.lib"), page);
        remove(&placed);
        let e = library(&lib, &dir, &tools).err().unwrap_or_default();
        assert!(
            e.contains("download failed: ") && e.contains("(0 of 3 parts downloaded, "),
            "{e}"
        );
        assert!(!placed.exists());
        let names = t.files();
        assert!(!names.iter().any(|n| n.ends_with(".part")), "{names:?}");
    }

    // Another attempt follows only the errors it may change: not a refused connection, an HTTP
    // status of 400 or more, a timeout (retried inside curl already) or a missing local file. No
    // download starts after one has failed, so a missing source costs at most one attempt per
    // worker, not one per part. The download part is skipped without curl or a SHA-256 program.
    #[test]
    fn download_failures() {
        for (status, again) in [
            (Some(6), true),
            (Some(18), true),
            (Some(56), true),
            (None, true),
            (Some(7), false),
            (Some(22), false),
            (Some(28), false),
            (Some(37), false),
        ] {
            assert_eq!(retryable(status), again, "{status:?}");
        }
        if Tools::find(true).is_err() {
            return;
        }
        let t = Scratch::new("download");
        let names: Vec<String> = (0..40).map(|k| format!("cells/c{k}.lib")).collect();
        let paths: Vec<&str> = names.iter().map(String::as_str).collect();
        let base = format!("file://{}/missing/", t.root().display());
        let e = download_parts(&base, &paths, &t.root().join("parts"))
            .err()
            .unwrap_or_default();
        assert!(
            e.starts_with(&base)
                && e.contains("download failed: ")
                && e.contains("(0 of 40 parts downloaded, "),
            "{e}"
        );
        let failed = e
            .split("parts downloaded, ")
            .nth(1)
            .and_then(|r| r.split(' ').next())
            .and_then(|n| n.parse::<usize>().ok());
        assert!(failed.is_some_and(|n| (1..=WORKERS).contains(&n)), "{e}");
        assert_eq!(t.files(), Vec::<String>::new());
    }
}

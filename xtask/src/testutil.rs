// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Synthetic repository trees in temporary directories for the tests of this crate. Every tree
//! lives below the operating system's temporary directory and is removed when dropped.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

static NEXT: AtomicUsize = AtomicUsize::new(0);

/// A temporary directory tree that is removed on drop.
#[derive(Debug)]
pub(crate) struct Tree {
    root: PathBuf,
}

impl Tree {
    /// Creates an empty tree with a fresh name (a directory that exists already is never reused).
    pub(crate) fn new(tag: &str) -> Tree {
        loop {
            let n = NEXT.fetch_add(1, Ordering::Relaxed);
            let root = std::env::temp_dir().join(format!(
                "studio-xtask-test-{}-{tag}-{n}",
                std::process::id()
            ));
            match std::fs::create_dir(&root) {
                Ok(()) => return Tree { root },
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists && n < 1 << 20 => {}
                Err(e) => panic!("{}: {e}", root.display()),
            }
        }
    }

    /// The tree's root directory.
    pub(crate) fn root(&self) -> &Path {
        &self.root
    }

    /// Writes `text` to the relative path `rel`, creating directories.
    pub(crate) fn file(&self, rel: &str, text: &str) -> &Tree {
        self.bytes(rel, text.as_bytes())
    }

    /// Writes raw bytes to the relative path `rel`, creating directories.
    pub(crate) fn bytes(&self, rel: &str, data: &[u8]) -> &Tree {
        let p = self.root.join(rel);
        if let Some(d) = p.parent() {
            std::fs::create_dir_all(d).unwrap_or_else(|e| panic!("{}: {e}", d.display()));
        }
        std::fs::write(&p, data).unwrap_or_else(|e| panic!("{}: {e}", p.display()));
        self
    }

    /// Every file of the tree as a sorted list of `/`-separated relative paths.
    pub(crate) fn files(&self) -> Vec<String> {
        let mut out = Vec::new();
        let mut stack = vec![self.root.clone()];
        while let Some(d) = stack.pop() {
            let Ok(rd) = std::fs::read_dir(&d) else {
                continue;
            };
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() {
                    stack.push(p);
                } else if let Ok(rel) = p.strip_prefix(&self.root) {
                    let parts: Vec<String> = rel
                        .components()
                        .map(|c| c.as_os_str().to_string_lossy().into_owned())
                        .collect();
                    out.push(parts.join("/"));
                }
            }
        }
        out.sort();
        out
    }
}

impl Drop for Tree {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

/// The SPDX header lines every Rust source of this tool in a synthetic tree starts with
/// (assembled so that license scanners do not read it as a tag of this file).
pub(crate) fn rs(body: &str) -> String {
    format!(
        "{}\n{}\n{body}",
        crate::SPDX_HEADER,
        concat!(
            "// SPDX-",
            "FileCopyrightText: 2026 RustEDA Studio contributors"
        )
    )
}

/// The SPDX header lines of a Rust source of a shared crate in a synthetic tree.
pub(crate) fn rs_shared(body: &str) -> String {
    format!(
        "{}\n{}\n{body}",
        crate::SPDX_HEADER,
        concat!("// SPDX-", "FileCopyrightText: 2026 RustEDA contributors")
    )
}

/// A minimal crate manifest with the given package name and extra sections.
pub(crate) fn manifest(name: &str, rest: &str) -> String {
    format!("[package]\nname = \"{name}\"\nversion = \"0.1.0\"\n\n{rest}")
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The workspace watcher (`docs/specs/studio/workspace.md`).
//!
//! The workbench follows a workspace by polling two files: `manifest.json`, whose atomic
//! replacement is the commit point of every revision (WS-REQ-0076), and `ui_state.json`, the
//! shared selection. A poll compares a fingerprint of each file (length, modification time and
//! file identity) with the one last reported; a change is reported once the file has kept its
//! new fingerprint for the debounce interval, so a burst of commits gives one reload
//! (STUDIO-REQ-0001, STUDIO-REQ-0002). Polling needs no daemon and no extra dependency; a poll
//! costs two `stat` calls. The watcher takes the time as an argument, so tests drive it without
//! sleeping.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, UNIX_EPOCH};

use rusteda_workspace::workspace::layout;

/// The identity of a file's state: length, modification time and file id (STUDIO-REQ-0001).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Fingerprint {
    /// Length in bytes.
    pub len: u64,
    /// Modification time in nanoseconds since the Unix epoch.
    pub modified_ns: u128,
    /// The inode number where the platform has one, else 0; an atomic replacement changes it.
    pub id: u64,
}

/// The fingerprint of `path`, `None` when it does not exist (or is not a regular file).
pub fn fingerprint(path: &Path) -> Option<Fingerprint> {
    let meta = std::fs::symlink_metadata(path).ok()?;
    if !meta.is_file() {
        return None;
    }
    let modified_ns = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_nanos());
    #[cfg(unix)]
    let id = std::os::unix::fs::MetadataExt::ino(&meta);
    #[cfg(not(unix))]
    let id = 0;
    Some(Fingerprint {
        len: meta.len(),
        modified_ns,
        id,
    })
}

/// A change the watcher reports (STUDIO-REQ-0001).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WatchEvent {
    /// `manifest.json` changed: a commit or a revert happened.
    Manifest,
    /// `manifest.json` disappeared.
    ManifestLost,
    /// `ui_state.json` changed, appeared or disappeared.
    UiState,
}

/// One watched file.
#[derive(Debug, Clone)]
struct Watched {
    path: PathBuf,
    seen: Option<Fingerprint>,
    pending: Option<(Option<Fingerprint>, Instant)>,
}

impl Watched {
    fn new(path: PathBuf) -> Watched {
        let seen = fingerprint(&path);
        Watched {
            path,
            seen,
            pending: None,
        }
    }

    /// Whether the file has a new state that kept still for `debounce`.
    fn poll(&mut self, now: Instant, debounce: Duration) -> Option<Option<Fingerprint>> {
        let fp = fingerprint(&self.path);
        if fp == self.seen {
            self.pending = None;
            return None;
        }
        match self.pending {
            Some((p, since)) if p == fp => {
                if now.saturating_duration_since(since) >= debounce {
                    self.seen = fp;
                    self.pending = None;
                    return Some(fp);
                }
                None
            }
            _ => {
                self.pending = Some((fp, now));
                if debounce.is_zero() {
                    self.seen = fp;
                    self.pending = None;
                    return Some(fp);
                }
                None
            }
        }
    }
}

/// The watcher of one workspace (STUDIO-REQ-0001, STUDIO-REQ-0002).
#[derive(Debug, Clone)]
pub struct Watcher {
    manifest: Watched,
    ui_state: Watched,
    debounce: Duration,
}

impl Watcher {
    /// A watcher of the workspace at `root` that takes the files' current states as seen.
    pub fn new(root: &Path, debounce: Duration) -> Watcher {
        Watcher {
            manifest: Watched::new(root.join(layout::MANIFEST)),
            ui_state: Watched::new(root.join(layout::UI_STATE)),
            debounce,
        }
    }

    /// The changes that kept still for the debounce interval at time `now`.
    pub fn poll(&mut self, now: Instant) -> Vec<WatchEvent> {
        let mut out = Vec::new();
        if let Some(fp) = self.manifest.poll(now, self.debounce) {
            out.push(if fp.is_some() {
                WatchEvent::Manifest
            } else {
                WatchEvent::ManifestLost
            });
        }
        if self.ui_state.poll(now, self.debounce).is_some() {
            out.push(WatchEvent::UiState);
        }
        out
    }

    /// Takes the current state of `ui_state.json` as seen, after the workbench wrote it
    /// itself (STUDIO-REQ-0146).
    pub fn mark_ui_state_seen(&mut self) {
        self.ui_state.seen = fingerprint(&self.ui_state.path);
        self.ui_state.pending = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(tag: &str) -> PathBuf {
        let d =
            std::env::temp_dir().join(format!("rusteda-studio-watch-{}-{tag}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// Replaces `path` atomically, as the workspace does (temporary file and rename).
    fn replace(path: &Path, text: &str) {
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, text).unwrap();
        std::fs::rename(&tmp, path).unwrap();
    }

    // STUDIO-REQ-0001, STUDIO-REQ-0002: a replaced manifest is reported once, after it kept
    // still for the debounce interval; a burst of replacements gives one event.
    #[test]
    fn debounced_manifest_changes() {
        let d = dir("debounce");
        let m = d.join("manifest.json");
        replace(&m, "{\"head\":\"rev0\"}");
        let t0 = Instant::now();
        let ms = Duration::from_millis;
        let mut w = Watcher::new(&d, ms(100));
        assert!(w.poll(t0).is_empty());
        replace(&m, "{\"head\":\"rev1\"}");
        assert!(w.poll(t0 + ms(10)).is_empty(), "pending, not yet still");
        replace(&m, "{\"head\":\"rev22\"}");
        assert!(w.poll(t0 + ms(20)).is_empty(), "changed again: wait anew");
        assert!(w.poll(t0 + ms(60)).is_empty());
        assert_eq!(w.poll(t0 + ms(130)), [WatchEvent::Manifest]);
        assert!(w.poll(t0 + ms(400)).is_empty(), "reported once");
        std::fs::remove_file(&m).unwrap();
        assert!(w.poll(t0 + ms(500)).is_empty());
        assert_eq!(w.poll(t0 + ms(700)), [WatchEvent::ManifestLost]);
        std::fs::remove_dir_all(&d).ok();
    }

    // STUDIO-REQ-0001, STUDIO-REQ-0146: ui_state changes are reported, except those the
    // workbench marked as its own; without debounce a change is reported at once.
    #[test]
    fn ui_state_changes() {
        let d = dir("ui");
        replace(&d.join("manifest.json"), "{}");
        let u = d.join("ui_state.json");
        let t0 = Instant::now();
        let mut w = Watcher::new(&d, Duration::ZERO);
        replace(&u, "{\"a\":1}");
        assert_eq!(w.poll(t0), [WatchEvent::UiState]);
        replace(&u, "{\"a\":22}");
        w.mark_ui_state_seen();
        assert!(w.poll(t0).is_empty());
        assert!(fingerprint(&d.join("nothing")).is_none());
        assert!(fingerprint(&d).is_none(), "a directory has no fingerprint");
        std::fs::remove_dir_all(&d).ok();
    }
}

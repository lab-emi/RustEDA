// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! File identities (resolved path, SHA-256, size) of pinned inputs and produced files.
//!
//! One file is hashed with a 1 MiB streaming buffer. Several files are hashed
//! on at most [`MAX_HASH_WORKERS`] scoped threads (never more than the CPUs
//! the process may use), so peak memory is one buffer per worker.

use super::pyjson::Json;
use crate::error::{Error, Result};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
    sync::atomic::{AtomicUsize, Ordering},
};

/// Largest file an identity may describe (4 GiB), as in the reference producer.
pub const MAX_FILE: u64 = 4 * 1024 * 1024 * 1024;
pub const MAX_HASH_WORKERS: usize = 8;
const BUFFER: usize = 1024 * 1024;
/// Below this many bytes in total, extra threads cost more than they save.
const PARALLEL_FLOOR: u64 = 8 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Identity {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
}

impl Identity {
    /// `{"path": ..., "sha256": ..., "bytes": ...}` in the reference member order.
    pub fn json(&self) -> Json {
        Json::object([
            ("path", Json::str(self.path.clone())),
            ("sha256", Json::str(self.sha256.clone())),
            ("bytes", Json::int(self.bytes)),
        ])
    }
}

pub fn utf8(path: &Path) -> Result<String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| Error::new("PATH_ENCODING", "executor paths must be UTF-8"))
}

fn unavailable(path: &Path, error: impl std::fmt::Display) -> Error {
    Error::new("FILE_UNAVAILABLE", format!("{}: {error}", path.display()))
}

/// Resolve symbolic links, require a regular file within the size budget, hash it and
/// require that its size did not change meanwhile.
pub fn identity(path: &Path) -> Result<Identity> {
    let resolved = fs::canonicalize(path).map_err(|e| unavailable(path, e))?;
    let before = fs::metadata(&resolved).map_err(|e| unavailable(&resolved, e))?;
    if !before.is_file() || before.len() > MAX_FILE {
        return Err(Error::new(
            "FILE_BUDGET",
            format!("file budget exceeded: {}", resolved.display()),
        ));
    }
    let mut file = File::open(&resolved).map_err(|e| unavailable(&resolved, e))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; BUFFER.min(before.len() as usize + 1)];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|e| unavailable(&resolved, e))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    let after = fs::metadata(&resolved).map_err(|e| unavailable(&resolved, e))?;
    if after.len() != before.len() {
        return Err(Error::new(
            "FILE_CHANGED",
            "input size changed during hashing",
        ));
    }
    Ok(Identity {
        path: utf8(&resolved)?,
        sha256: format!("{:x}", hasher.finalize()),
        bytes: before.len(),
    })
}

/// CPUs the calling thread may run on (at least one).
pub fn usable_cpus() -> usize {
    std::thread::available_parallelism().map_or(1, usize::from)
}

/// Identities of several files, in the order given. Results are independent, so one
/// missing file does not hide the identity of another.
pub fn identities(paths: &[PathBuf]) -> Vec<Result<Identity>> {
    let total: u64 = paths
        .iter()
        .filter_map(|path| fs::metadata(path).ok())
        .map(|meta| meta.len())
        .sum();
    let workers = usable_cpus().min(MAX_HASH_WORKERS).min(paths.len());
    if workers < 2 || total < PARALLEL_FLOOR {
        return paths.iter().map(|path| identity(path)).collect();
    }
    // Largest first, so that the long files do not all land on one worker.
    let mut order: Vec<usize> = (0..paths.len()).collect();
    order.sort_by_key(|&index| {
        std::cmp::Reverse(fs::metadata(&paths[index]).map_or(0, |meta| meta.len()))
    });
    let next = AtomicUsize::new(0);
    let mut results: Vec<Option<Result<Identity>>> = (0..paths.len()).map(|_| None).collect();
    std::thread::scope(|scope| {
        let handles: Vec<_> = (0..workers)
            .map(|_| {
                scope.spawn(|| {
                    let mut done = Vec::new();
                    loop {
                        let slot = next.fetch_add(1, Ordering::Relaxed);
                        let Some(&index) = order.get(slot) else {
                            return done;
                        };
                        done.push((index, identity(&paths[index])));
                    }
                })
            })
            .collect();
        for handle in handles {
            match handle.join() {
                Ok(done) => {
                    for (index, result) in done {
                        results[index] = Some(result);
                    }
                }
                Err(_) => continue,
            }
        }
    });
    results
        .into_iter()
        .map(|slot| {
            slot.unwrap_or_else(|| Err(Error::new("INTERNAL", "a hashing worker stopped early")))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// FLOWEXEC-REQ-0009: resolved path, SHA-256 and size of a regular file.
    #[test]
    fn identity_resolves_links_and_many_files_keep_their_order() {
        let root = tempfile::tempdir().unwrap();
        let mut paths = Vec::new();
        for index in 0..12_usize {
            let path = root.path().join(format!("file{index}"));
            // Sizes straddle the parallel floor so that both paths are exercised by the suite.
            fs::write(&path, vec![index as u8; index * 1024 * 1024 / 3 + index]).unwrap();
            paths.push(path);
        }
        std::os::unix::fs::symlink(&paths[1], root.path().join("link")).unwrap();
        let linked = identity(&root.path().join("link")).unwrap();
        let direct = identity(&paths[1]).unwrap();
        assert_eq!(linked, direct);
        assert_eq!(
            direct.path,
            fs::canonicalize(&paths[1]).unwrap().to_str().unwrap()
        );
        assert_eq!(
            identity(&paths[0]).unwrap().sha256,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        paths.push(root.path().join("missing"));
        let all = identities(&paths);
        for (path, found) in paths.iter().zip(&all).take(12) {
            assert_eq!(found.as_ref().unwrap(), &identity(path).unwrap());
        }
        assert_eq!(all[12].as_ref().unwrap_err().code, "FILE_UNAVAILABLE");
        assert_eq!(identity(root.path()).unwrap_err().code, "FILE_BUDGET");
    }
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

use crate::{
    config::{Config, FlowKind, MAX_STEPS, handle},
    error::{Error, Result},
    process::ProcessReceipt,
};
use fs2::FileExt;
use memmap2::MmapOptions;
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

const TABLE_LIMIT: u64 = 16 * 1024 * 1024;
const MAGIC: &[u8; 8] = b"RLANE001";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileIdentity {
    pub path: PathBuf,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Input {
    pub original: PathBuf,
    pub captured: FileIdentity,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StageStatus {
    Pending,
    Running,
    Passed,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Artifact {
    pub kind: String,
    pub file: FileIdentity,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct StageResult {
    pub processes: Vec<ProcessReceipt>,
    pub tools: Vec<FileIdentity>,
    pub artifacts: Vec<Artifact>,
    pub evidence: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Stage {
    pub id: String,
    pub operation: String,
    pub status: StageStatus,
    pub attempts: u32,
    pub result: Option<StageResult>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub schema: String,
    pub rustlane_version: String,
    pub revision: String,
    pub original_config: FileIdentity,
    pub inputs: Vec<Input>,
    pub total_wall_seconds: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct State {
    pub config: Config,
    pub manifest: Manifest,
    pub stages: Vec<Stage>,
}

#[derive(Debug)]
pub struct Workspace {
    pub root: PathBuf,
    lock: File,
    exclusive: bool,
}

impl Workspace {
    pub fn open(path: &Path, exclusive: bool, create: bool) -> Result<Self> {
        if create {
            fs::create_dir_all(path)?;
        }
        let root = fs::canonicalize(path)?;
        if !root.is_dir() {
            return Err(Error::new(
                "INVALID_WORKSPACE",
                "workspace must be a directory",
            ));
        }
        let lock_path = root.join("workspace.lock");
        if fs::symlink_metadata(&lock_path).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err(Error::new(
                "INVALID_WORKSPACE",
                "workspace.lock is a symlink",
            ));
        }
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(create)
            .truncate(false)
            .open(lock_path)?;
        let acquired = if exclusive {
            FileExt::try_lock_exclusive(&lock)
        } else {
            FileExt::try_lock_shared(&lock)
        };
        acquired.map_err(|e| Error::new("WORKSPACE_BUSY", e.to_string()))?;
        Ok(Self {
            root,
            lock,
            exclusive,
        })
    }

    pub fn head(&self) -> Result<String> {
        let path = self.root.join("HEAD");
        if !path.exists() {
            return Ok("empty".into());
        }
        let mut content = String::new();
        File::open(&path)?.take(129).read_to_string(&mut content)?;
        if content.len() > 128 {
            return Err(Error::new("CORRUPT_WORKSPACE", "HEAD exceeds handle bound"));
        }
        let head = content.trim().to_owned();
        parse_revision(&head)?;
        Ok(head)
    }

    pub fn require_base(&self, base: &str) -> Result<()> {
        let actual = self.head()?;
        if base != actual {
            return Err(Error::new(
                "REVISION_CONFLICT",
                format!("expected {base}, HEAD is {actual}"),
            ));
        }
        Ok(())
    }

    pub fn load(&self, revision: &str) -> Result<State> {
        let rev = if revision == "HEAD" {
            self.head()?
        } else {
            revision.into()
        };
        parse_revision(&rev)?;
        let dir = self.root.join("revisions").join(&rev);
        let config: Config = read_table(&dir.join("config.rbin"))?;
        let manifest: Manifest = read_table(&dir.join("manifest.rbin"))?;
        let stages: Vec<Stage> = read_table(&dir.join("results.rbin"))?;
        config.validate()?;
        if manifest.schema != "rustlane.workspace.v1"
            || manifest.revision != rev
            || stages.len() > MAX_STEPS
            || stages.iter().any(|s| !handle(&s.id) || s.attempts > 1000)
        {
            return Err(Error::new(
                "CORRUPT_WORKSPACE",
                "invalid workspace schema, revision or stage budget",
            ));
        }
        if manifest.inputs.len() > 1024 {
            return Err(Error::new("CORRUPT_WORKSPACE", "input count exceeds bound"));
        }
        Ok(State {
            config,
            manifest,
            stages,
        })
    }

    pub fn commit(&self, state: &mut State) -> Result<String> {
        if !self.exclusive {
            return Err(Error::new(
                "LOCK_REQUIRED",
                "mutation requires exclusive workspace.lock",
            ));
        }
        let head = self.head()?;
        let mut next = if head == "empty" {
            0
        } else {
            parse_revision(&head)?
                .checked_add(1)
                .ok_or_else(|| Error::new("REVISION_BUDGET", "revision overflow"))?
        };
        if state.stages.len() > MAX_STEPS {
            return Err(Error::new("STATE_BUDGET", "too many stages"));
        }
        let parent = self.root.join("revisions");
        fs::create_dir_all(&parent)?;
        // A crash may publish a complete directory before HEAD is advanced.
        // Preserve such orphan snapshots and reserve a fresh number. Never
        // overwrite/delete an existing revision, and bound recovery work.
        let mut found = false;
        for _ in 0..128 {
            if fs::symlink_metadata(parent.join(format!("rev{next}"))).is_err() {
                found = true;
                break;
            }
            next = next
                .checked_add(1)
                .ok_or_else(|| Error::new("REVISION_BUDGET", "revision overflow"))?;
        }
        if !found {
            return Err(Error::new(
                "ORPHAN_BUDGET",
                "128 occupied revision slots after HEAD; preserve evidence and use a fresh workspace",
            ));
        }
        let revision = format!("rev{next}");
        state.manifest.revision = revision.clone();
        // A private staging directory and create_new keep interrupted writes visible and harmless.
        let staging = parent.join(format!("staging-{}-{next}", std::process::id()));
        fs::create_dir(&staging)?;
        write_table(&staging.join("config.rbin"), &state.config)?;
        write_table(&staging.join("manifest.rbin"), &state.manifest)?;
        write_table(&staging.join("results.rbin"), &state.stages)?;
        File::open(&staging)?.sync_all()?;
        fs::rename(&staging, parent.join(&revision))?;
        File::open(&parent)?.sync_all()?;
        let head_temp = self
            .root
            .join(format!("HEAD-{}-{next}.tmp", std::process::id()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&head_temp)?;
        writeln!(file, "{revision}")?;
        file.sync_all()?;
        fs::rename(head_temp, self.root.join("HEAD"))?;
        File::open(&self.root)?.sync_all()?;
        Ok(revision)
    }

    pub fn capture_inputs(&self, config: &mut Config) -> Result<Vec<Input>> {
        let directory = self.root.join("inputs");
        fs::create_dir_all(&directory)?;
        let mut inputs = Vec::new();
        let mut replacements = BTreeMap::new();
        let mut total = 0_u64;
        for original in config.input_paths()? {
            let identity = identify(&original, config.limits.max_file_bytes)?;
            total = total
                .checked_add(identity.bytes)
                .filter(|n| *n <= config.limits.max_input_bytes)
                .ok_or_else(|| {
                    Error::new(
                        "INPUT_BUDGET",
                        "aggregate input bytes exceed max_input_bytes",
                    )
                })?;
            let extension = original
                .extension()
                .and_then(|s| s.to_str())
                .filter(|s| s.len() < 16 && s.bytes().all(|b| b.is_ascii_alphanumeric()))
                .unwrap_or("dat");
            let captured = directory.join(format!("{}.{}", identity.sha256, extension));
            if !captured.exists() {
                let mut source = File::open(&original)?;
                let mut dest = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&captured)?;
                let mut taken = (&mut source).take(config.limits.max_file_bytes + 1);
                let copied = std::io::copy(&mut taken, &mut dest)?;
                if copied > config.limits.max_file_bytes {
                    return Err(Error::new("INPUT_BUDGET", "input grew while captured"));
                }
                dest.sync_all()?;
            }
            let actual = identify(&captured, config.limits.max_file_bytes)?;
            if actual.sha256 != identity.sha256 || actual.bytes != identity.bytes {
                return Err(Error::new(
                    "INPUT_CHANGED",
                    format!("input changed while capturing {}", original.display()),
                ));
            }
            readonly(&captured)?;
            replacements.insert(original.clone(), captured);
            inputs.push(Input {
                original,
                captured: actual,
            });
        }
        config.replace_paths(&replacements);
        Ok(inputs)
    }

    pub fn validate_inputs(&self, state: &State) -> Result<()> {
        if state.config.flow == FlowKind::Rusteda {
            // A new script or PDK tree entry can affect a producer without
            // modifying any previously captured file. Recheck the bounded list.
            let current: BTreeSet<_> = state
                .config
                .input_paths()?
                .into_iter()
                .map(|path| {
                    state
                        .manifest
                        .inputs
                        .iter()
                        .find(|i| i.captured.path == path)
                        .map_or(path.clone(), |i| i.original.clone())
                })
                .collect();
            let captured = state
                .manifest
                .inputs
                .iter()
                .map(|i| i.original.clone())
                .collect();
            if current != captured {
                return Err(Error::new(
                    "INPUT_CHANGED",
                    "producer dependency inventory changed; use a fresh workspace",
                ));
            }
        }
        for input in &state.manifest.inputs {
            verify(&input.captured, state.config.limits.max_file_bytes)?;
            let original = identify(&input.original, state.config.limits.max_file_bytes)?;
            if original.sha256 != input.captured.sha256 || original.bytes != input.captured.bytes {
                return Err(Error::new(
                    "INPUT_CHANGED",
                    format!(
                        "{} changed since init; use a fresh workspace",
                        input.original.display()
                    ),
                ));
            }
        }
        for stage in &state.stages {
            if stage.status == StageStatus::Passed {
                let result = stage.result.as_ref().ok_or_else(|| {
                    Error::new("CORRUPT_WORKSPACE", "passed stage missing result")
                })?;
                for file in result
                    .tools
                    .iter()
                    .chain(result.artifacts.iter().map(|a| &a.file))
                {
                    verify(file, state.config.limits.max_file_bytes)?;
                }
            }
        }
        Ok(())
    }
}

impl Drop for Workspace {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.lock);
    }
}

pub fn identify(path: &Path, max_bytes: u64) -> Result<FileIdentity> {
    let mut file = File::open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() > max_bytes {
        return Err(Error::new(
            "FILE_BUDGET",
            format!("not a regular file within byte budget: {}", path.display()),
        ));
    }
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 65536];
    let mut bytes = 0_u64;
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        bytes = bytes
            .checked_add(n as u64)
            .filter(|s| *s <= max_bytes)
            .ok_or_else(|| Error::new("FILE_BUDGET", "file grew beyond byte budget"))?;
        hasher.update(&buffer[..n]);
    }
    Ok(FileIdentity {
        path: fs::canonicalize(path)?,
        sha256: format!("{:x}", hasher.finalize()),
        bytes,
    })
}

pub fn verify(expected: &FileIdentity, max_bytes: u64) -> Result<()> {
    let actual = identify(&expected.path, max_bytes)?;
    if actual.sha256 != expected.sha256 || actual.bytes != expected.bytes {
        return Err(Error::new(
            "ARTIFACT_CHANGED",
            format!("identity changed: {}", expected.path.display()),
        ));
    }
    Ok(())
}

pub fn parse_revision(rev: &str) -> Result<u64> {
    let n = rev
        .strip_prefix("rev")
        .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|s| s.parse::<u64>().ok())
        .ok_or_else(|| {
            Error::new(
                "INVALID_REVISION",
                "revision must be rev followed by decimal digits",
            )
        })?;
    if format!("rev{n}") != rev {
        return Err(Error::new("INVALID_REVISION", "noncanonical revision"));
    }
    Ok(n)
}

fn readonly(path: &Path) -> Result<()> {
    let mut permissions = fs::metadata(path)?.permissions();
    permissions.set_readonly(true);
    fs::set_permissions(path, permissions)?;
    Ok(())
}

fn write_table<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    // Bound the serializer before it can build a large intermediate Vec.
    let mut encoded = BoundedBytes {
        bytes: Vec::new(),
        max: TABLE_LIMIT as usize,
    };
    serde_json::to_writer(&mut encoded, value)
        .map_err(|e| Error::new("STATE_BUDGET", e.to_string()))?;
    let digest = Sha256::digest(&encoded.bytes);
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(MAGIC)?;
    file.write_all(&(encoded.bytes.len() as u64).to_le_bytes())?;
    file.write_all(&digest)?;
    file.write_all(&encoded.bytes)?;
    file.sync_all()?;
    readonly(path)
}

fn read_table<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let file = File::open(path)?;
    let len = file.metadata()?.len();
    if !(48..=TABLE_LIMIT + 48).contains(&len) {
        return Err(Error::new("STATE_BUDGET", "invalid side-table size"));
    }
    // SAFETY: cooperating writers hold workspace.lock and publish new files, never
    // overwrite/truncate a published table. The mapping is read-only and short-lived.
    let map = unsafe { MmapOptions::new().map(&file)? };
    let stored_len = u64::from_le_bytes(map[8..16].try_into().unwrap());
    if &map[..8] != MAGIC || stored_len != len - 48 || Sha256::digest(&map[48..])[..] != map[16..48]
    {
        return Err(Error::new(
            "CORRUPT_WORKSPACE",
            "side-table header or SHA-256 mismatch",
        ));
    }
    Ok(serde_json::from_slice(&map[48..])?)
}

struct BoundedBytes {
    bytes: Vec<u8>,
    max: usize,
}
impl Write for BoundedBytes {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.max.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other("side-table byte budget reached"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

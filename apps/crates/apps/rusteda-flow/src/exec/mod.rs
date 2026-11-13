// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Native executor of a `rusteda.ihp-flow.v1` step graph (`rusteda flow exec`).
//!
//! The executor is a worker program: it validates a hash-pinned manifest, runs its
//! render and command steps (serial steps and parallel groups with CPU lanes and
//! memory reservations), and leaves `step-receipts.json`, `summary.json` and per-step
//! logs in the layout and JSON text form of the reference producer script. It contains
//! no EDA algorithm. Commands never run through a shell.
//!
//! Bounds: a 2 MiB manifest with at most 129 steps, 288 pinned inputs and 128
//! artifacts; at most 16 live steps; one coordinator thread plus at most eight hashing
//! threads with a 1 MiB buffer each. Expected executor memory is below 64 MiB.
//! The memory limit of a run, its deadline and the cleanup of everything it started are
//! the owning controller's control group (see `docs/flow/native-executor.md`).

mod events;
#[cfg(target_os = "linux")]
mod group;
pub mod identity;
pub mod manifest;
pub mod pyjson;
mod query;
#[cfg(target_os = "linux")]
mod run;
#[cfg(target_os = "linux")]
mod sys;
pub mod template;

use crate::error::Result;
use serde_json::Value;
use std::path::PathBuf;

/// What `rusteda flow exec` was asked to do.
#[derive(Debug, Clone, Default)]
pub struct Options {
    /// Base directory of relative pinned input paths.
    pub project: PathBuf,
    pub manifest: PathBuf,
    /// New or empty directory that receives the run.
    pub output: PathBuf,
    /// Must be `empty`: a run never continues another one.
    pub base_rev: String,
    /// When given, must be the pinned `rustroad` tool.
    pub rustroad: Option<PathBuf>,
    /// When given, must be the pinned `gnu_time` tool; commands are recorded with this spelling.
    pub gnu_time: Option<PathBuf>,
    /// Run the serial prefix that ends with this step.
    pub stop_after: Option<String>,
    /// 64 lower-case hexadecimal digits; defaults to `RUSTEDA_WORKER_ID`.
    pub worker_id: Option<String>,
    /// Directory the run creates for its ownership record; defaults to `RUSTEDA_OWNERSHIP`.
    pub ownership: Option<PathBuf>,
    /// Progress events: a new file, or `-` for standard output.
    pub events: Option<PathBuf>,
}

/// The JSON document a command prints and its process exit code.
#[derive(Debug)]
pub struct Outcome {
    pub exit_code: u8,
    pub value: Value,
    /// Progress events went to standard output, so the document is the last line of a stream.
    pub events_on_stdout: bool,
}

/// Run a step graph. `Err` means the run was refused before any step ran and nothing
/// but the output directory's lock file was created; a run that started and failed is
/// an `Ok` outcome with exit code 1 and a `failed` summary.
pub fn execute(options: &Options) -> Result<Outcome> {
    #[cfg(target_os = "linux")]
    {
        run::execute(options)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = options;
        Err(crate::error::Error::new(
            "UNSUPPORTED_PLATFORM",
            "the step-graph executor requires Linux",
        ))
    }
}

/// Record SIGINT and SIGTERM so that a run stops its steps and writes its receipts.
pub fn handle_termination_signals() -> Result<()> {
    #[cfg(target_os = "linux")]
    {
        sys::install_cancellation()
    }
    #[cfg(not(target_os = "linux"))]
    {
        Ok(())
    }
}

pub use query::{plan, status};

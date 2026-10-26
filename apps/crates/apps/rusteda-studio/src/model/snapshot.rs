// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Loading a revision read-only (`docs/specs/studio/workspace.md`).
//!
//! A [`Snapshot`] is everything the panels show of one revision: its metadata, its netlist with
//! the hierarchy, its timing table and its RTL snapshot. It is read through
//! `rusteda-workspace` only: the manifest under the shared lock of `workspace.lock`
//! (WS-REQ-0050), tables of committed revisions through the validated, memory-mapped readers
//! (WS-REQ-0037, WS-REQ-0122, WS-REQ-0126), the RTL directory table through its checked
//! accessors (WS-REQ-0026). Nothing is ever written (STUDIO-REQ-0004). A table that is absent
//! is a note, one that cannot be read an error, and the rest of the revision is still shown
//! (STUDIO-REQ-0005).

use std::time::Instant;

use rusteda_core::job::{Ctx, Event, JobError};
use rusteda_workspace::{Rev, Revision, Workspace, netlist, sta};
use serde_json::Value;

use super::budget::Budgets;
use super::diag::{Code, StudioDiag};
use super::hierarchy::{Hierarchy, SplitMode};
use super::netlist::NetlistView;
use super::rtl::{RtlFile, RtlIndex};
use super::timing::TimingView;

/// Which revision to load.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Which {
    /// The workspace's HEAD.
    Head,
    /// A given revision.
    Rev(Rev),
}

/// The metadata of a revision for the panels (STUDIO-REQ-0006).
#[derive(Debug, Clone, PartialEq)]
pub struct RevisionInfo {
    /// The revision.
    pub rev: Rev,
    /// Its base revision.
    pub parent: Option<Rev>,
    /// The tool that made it.
    pub tool: String,
    /// The command.
    pub command: String,
    /// Commit time in milliseconds since the Unix epoch.
    pub created_unix_ms: u64,
    /// The commit message.
    pub message: Option<String>,
    /// Its tables, by name.
    pub tables: Vec<String>,
    /// The `timing` status of the `sta` table; `not_analyzed` without one (Rule 4.2).
    pub timing: String,
}

impl RevisionInfo {
    /// The metadata of `revision`.
    pub fn of(revision: &Revision) -> RevisionInfo {
        let meta = revision.meta();
        let timing = meta
            .status
            .get(sta::TABLE_NAME)
            .and_then(|s| s.get("timing"))
            .and_then(Value::as_str)
            .unwrap_or("not_analyzed")
            .to_string();
        RevisionInfo {
            rev: meta.rev,
            parent: meta.parent,
            tool: meta.tool.clone(),
            command: meta.command.clone(),
            created_unix_ms: meta.created_unix_ms,
            message: meta.message.clone(),
            tables: meta.tables.keys().cloned().collect(),
            timing,
        }
    }
}

/// One revision as the panels show it (STUDIO-REQ-0005).
#[derive(Debug)]
pub struct Snapshot {
    /// The displayed revision.
    pub rev: Rev,
    /// HEAD when the revision was loaded.
    pub head: Rev,
    /// Its metadata.
    pub info: RevisionInfo,
    /// Its netlist, when it has one that reads.
    pub netlist: Option<NetlistView>,
    /// The hierarchy of the netlist.
    pub hierarchy: Option<Hierarchy>,
    /// Its timing table, when it has one that reads.
    pub timing: Option<TimingView>,
    /// Its RTL snapshot, when it has one.
    pub rtl: Option<RtlIndex>,
    /// What could not be shown, and why.
    pub diagnostics: Vec<StudioDiag>,
    /// Wall-clock time of the load in milliseconds.
    pub load_ms: u128,
}

/// A failed load of a whole revision (the workspace or the revision cannot be read).
fn fail(code: Code, e: impl std::fmt::Display) -> StudioDiag {
    StudioDiag::new(code, e.to_string())
}

/// Loads revision `which` of `ws` read-only (STUDIO-REQ-0004, STUDIO-REQ-0005).
pub fn load(
    ws: &Workspace,
    which: Which,
    budgets: &Budgets,
    split: SplitMode,
) -> Result<Snapshot, StudioDiag> {
    load_with(ws, which, budgets, split, &Ctx::NONE).map_err(|e| match e {
        JobError::Failed(d) => d,
        // Without a cancel token no checkpoint fails.
        JobError::Cancelled => StudioDiag::new(Code::ReloadFailed, "the load was cancelled"),
    })
}

/// The phases of a load, in order (STUDIO-REQ-0008).
pub const PHASES: [&str; 5] = ["revision", "netlist", "hierarchy", "timing", "rtl"];

/// Runs `f` as phase `phase` of a load: checkpoints before and after it, and its start and end
/// events (JOB-REQ-0003, JOB-REQ-0005, JOB-REQ-0006).
fn phase<T>(
    ctx: &Ctx<'_>,
    phase: &'static str,
    f: impl FnOnce() -> Result<T, JobError<StudioDiag>>,
) -> Result<T, JobError<StudioDiag>> {
    ctx.checkpoint()?;
    ctx.emit(|| Event::PhaseStarted { phase });
    let t = Instant::now();
    let out = f()?;
    let seconds = t.elapsed().as_secs_f64();
    ctx.emit(|| Event::PhaseFinished { phase, seconds });
    // A token set during the phase stops the run here: a cancelled run returns no result.
    ctx.checkpoint()?;
    Ok(out)
}

/// [`load`] as an observable, cancellable run (STUDIO-REQ-0008): the phases of [`PHASES`] run
/// one after the other on the calling thread, with a checkpoint before each and inside the
/// hierarchy build, so that a load superseded by a newer commit stops early with
/// `JobError::Cancelled` and no partial snapshot (JOB-REQ-0005, JOB-REQ-0006).
pub fn load_with(
    ws: &Workspace,
    which: Which,
    budgets: &Budgets,
    split: SplitMode,
    ctx: &Ctx<'_>,
) -> Result<Snapshot, JobError<StudioDiag>> {
    let started = Instant::now();
    let (manifest, revision) = phase(ctx, PHASES[0], || {
        let manifest = ws
            .manifest()
            .map_err(|e| JobError::Failed(fail(Code::WorkspaceUnreadable, e)))?;
        let rev = match which {
            Which::Head => manifest.head,
            Which::Rev(r) => r,
        };
        let revision = ws.revision(rev).map_err(|e| {
            JobError::Failed(match e.code() {
                rusteda_workspace::ErrorCode::UnknownRevision => fail(Code::UnknownRevision, e),
                _ => fail(Code::RevisionUnreadable, e),
            })
        })?;
        Ok((manifest, revision))
    })?;
    let rev = revision.rev();
    let info = RevisionInfo::of(&revision);
    let mut diagnostics = Vec::new();
    let netlist = phase(ctx, PHASES[1], || {
        Ok(load_netlist(&revision, &mut diagnostics))
    })?;
    let hierarchy = phase(ctx, PHASES[2], || match &netlist {
        Some(n) => {
            let h = Hierarchy::build_with(n, split, budgets.max_hierarchy_depth, ctx)?;
            diagnostics.extend(h.diagnostics(budgets.max_hierarchy_depth));
            Ok(Some(h))
        }
        None => Ok(None),
    })?;
    let timing = phase(ctx, PHASES[3], || {
        Ok(load_timing(&revision, budgets, &mut diagnostics))
    })?;
    let rtl = phase(ctx, PHASES[4], || {
        Ok(load_rtl(&revision, budgets, &mut diagnostics))
    })?;
    if let Some(n) = &netlist {
        let count = n.instance_count() as f64;
        ctx.emit(|| Event::Metric {
            name: "instances",
            value: count,
        });
    }
    Ok(Snapshot {
        rev,
        head: manifest.head,
        info,
        netlist,
        hierarchy,
        timing,
        rtl,
        diagnostics,
        load_ms: started.elapsed().as_millis(),
    })
}

fn load_netlist(revision: &Revision, diags: &mut Vec<StudioDiag>) -> Option<NetlistView> {
    if !revision.has_table(netlist::TABLE_NAME) {
        diags.push(StudioDiag::new(
            Code::NoNetlist,
            format!("{} has no netlist table", revision.rev()),
        ));
        return None;
    }
    let read = netlist::open_rev(revision)
        .and_then(|rbin| netlist::decode(&rbin).map(|f| NetlistView::new(f.into_owned())));
    match read {
        Ok(v) => Some(v),
        Err(e) => {
            diags.push(StudioDiag::new(
                Code::NetlistUnreadable,
                format!("the netlist of {}: {e}", revision.rev()),
            ));
            None
        }
    }
}

fn load_timing(
    revision: &Revision,
    budgets: &Budgets,
    diags: &mut Vec<StudioDiag>,
) -> Option<TimingView> {
    if !revision.has_table(sta::TABLE_NAME) {
        diags.push(StudioDiag::new(
            Code::NoTiming,
            format!(
                "{} has no timing table: timing not_analyzed",
                revision.rev()
            ),
        ));
        return None;
    }
    let read =
        sta::StaTable::open_rev(revision).and_then(|t| TimingView::new(t, budgets.max_path_rows));
    match read {
        Ok((v, d)) => {
            diags.extend(d);
            Some(v)
        }
        Err(e) => {
            diags.push(StudioDiag::new(
                Code::TimingUnreadable,
                format!("the timing table of {}: {e}", revision.rev()),
            ));
            None
        }
    }
}

/// The name of the RTL snapshot's directory table and of its description file.
const RTL_TABLE: &str = "rtl";
const RTL_JSON: &str = "rtl.json";

fn load_rtl(
    revision: &Revision,
    budgets: &Budgets,
    diags: &mut Vec<StudioDiag>,
) -> Option<RtlIndex> {
    if !revision.has_table(RTL_TABLE) {
        diags.push(StudioDiag::new(
            Code::NoRtl,
            format!("{} has no RTL snapshot", revision.rev()),
        ));
        return None;
    }
    let files = match revision.table_files(RTL_TABLE) {
        Ok(f) => f,
        Err(e) => {
            diags.push(StudioDiag::new(
                Code::RtlFileSkipped,
                format!("the RTL snapshot of {}: {e}", revision.rev()),
            ));
            return None;
        }
    };
    let top = revision
        .read_table_file(RTL_TABLE, RTL_JSON)
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v.get("top").and_then(Value::as_str).map(str::to_string));
    let mut total = 0u64;
    let mut out = Vec::new();
    for (path, _) in files {
        if path == RTL_JSON {
            continue;
        }
        let size = std::fs::symlink_metadata(revision.dir().join(RTL_TABLE).join(&path))
            .map_or(0, |m| m.len());
        if size > budgets.rtl_max_file_bytes || total + size > budgets.rtl_max_total_bytes {
            diags.push(StudioDiag::new(
                Code::RtlFileSkipped,
                format!("{path}: {size} bytes exceed the RTL budget; not indexed"),
            ));
            out.push(RtlFile::new(path, None));
            continue;
        }
        total += size;
        let text = revision
            .read_table_file(RTL_TABLE, &path)
            .ok()
            .and_then(|b| String::from_utf8(b).ok());
        if text.is_none() {
            diags.push(StudioDiag::new(
                Code::RtlFileSkipped,
                format!("{path}: unreadable or not UTF-8; not indexed"),
            ));
        }
        out.push(RtlFile::new(path, text));
    }
    Some(RtlIndex::new(top, out))
}

/// The newest revisions first, at most `max`, skipping those that cannot be read
/// (STUDIO-REQ-0006).
pub fn revisions(ws: &Workspace, max: usize) -> Vec<RevisionInfo> {
    let Ok(all) = ws.revisions() else {
        return Vec::new();
    };
    all.into_iter()
        .rev()
        .take(max)
        .filter_map(|r| ws.revision(r).ok())
        .map(|r| RevisionInfo::of(&r))
        .collect()
}

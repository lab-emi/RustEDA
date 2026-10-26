// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! `rusteda-studio`: the workbench on a workspace (`docs/specs/studio/cli.md`).
//!
//! ```text
//! rusteda-studio -w <DIR> [--rev <REV>] [--select <HANDLE>...] [--print] [options]
//! ```
//!
//! Without `--print` it opens the window and follows the workspace until the window closes.
//! With `--print` it prints the panels as text and exits: the same model, no window. Errors
//! are printed as `error[U<nnnn>]: <message>` on standard error; the exit status is 0 on
//! success, 1 when the workspace or the revision cannot be read or the window cannot open, and
//! 2 for a bad command line (STUDIO-REQ-0220 to STUDIO-REQ-0225). Standard input is never
//! read.

use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use crate::model::hierarchy::SplitMode;
use crate::model::print;
use crate::model::selection::Selection;
use crate::{Code, Studio, StudioDiag, StudioOptions};
use clap::Parser;
use rusteda_workspace::{Handle, RevSpec};

/// The workbench of the RustEDA suite: a live view of a workspace.
#[derive(Debug, Parser)]
#[command(name = "rusteda studio", version, about)]
struct Args {
    /// Workspace directory.
    #[arg(short = 'w', long = "workspace", value_name = "DIR")]
    workspace: PathBuf,
    /// Revision to display (`rev<N>`); `HEAD` (the default) follows the workspace live.
    #[arg(long, value_name = "REV", default_value = "HEAD")]
    rev: String,
    /// Select these `path<N>` and `inst<N>` handles of the displayed revision at start, and
    /// share the selection in `ui_state.json` as a user selection would.
    #[arg(long, value_name = "HANDLE", num_args = 1..)]
    select: Vec<String>,
    /// Print the panels as text and exit instead of opening the window.
    #[arg(long)]
    print: bool,
    /// Rows per list in `--print`.
    #[arg(long, value_name = "N", default_value_t = 20)]
    max_rows: usize,
    /// Poll interval of the workspace watcher in milliseconds.
    #[arg(long, value_name = "MS", default_value_t = 250)]
    poll_ms: u64,
    /// Debounce interval of the workspace watcher in milliseconds.
    #[arg(long, value_name = "MS", default_value_t = 150)]
    debounce_ms: u64,
    /// Fan-in levels of logic cones.
    #[arg(long, value_name = "N")]
    fanin: Option<u32>,
    /// Fan-out levels of logic cones.
    #[arg(long, value_name = "N")]
    fanout: Option<u32>,
    /// Log watcher events, reloads and shared selections on standard error.
    #[arg(short = 'v', long)]
    verbose: bool,
    /// How instance names are split into hierarchy levels.
    #[arg(long, value_name = "MODE", default_value = "flattened", value_parser = ["flattened", "core"])]
    split: String,
}

/// Prints a diagnostic line on standard error.
fn report(d: &StudioDiag) {
    let _ = writeln!(std::io::stderr(), "{d}");
}

/// The exit status of a failed start (STUDIO-REQ-0221).
fn status(code: Code) -> ExitCode {
    match code {
        Code::BadArgument => ExitCode::from(2),
        _ => ExitCode::from(1),
    }
}

/// Builds the options from the command line (STUDIO-REQ-0220).
fn options(args: &Args) -> Result<StudioOptions, StudioDiag> {
    let mut o = StudioOptions::new(&args.workspace);
    o.revision = match RevSpec::parse(&args.rev) {
        Ok(RevSpec::Head) => None,
        Ok(RevSpec::Rev(r)) => Some(r),
        Err(e) => return Err(StudioDiag::new(Code::BadArgument, format!("--rev {e}"))),
    };
    o.verbose = args.verbose;
    o.poll = Duration::from_millis(args.poll_ms.max(10));
    o.debounce = Duration::from_millis(args.debounce_ms);
    if let Some(n) = args.fanin {
        o.budgets.cone_fanin_depth = n.min(16);
    }
    if let Some(n) = args.fanout {
        o.budgets.cone_fanout_depth = n.min(16);
    }
    o.split = if args.split == "core" {
        SplitMode::Core
    } else {
        SplitMode::Flattened
    };
    for h in &args.select {
        match Handle::parse(h) {
            Ok(x) if x.prefix() == "path" || x.prefix() == "inst" => {}
            Ok(_) => {
                return Err(StudioDiag::new(
                    Code::BadArgument,
                    format!("--select {h}: only path<N> and inst<N> handles are selected"),
                ));
            }
            Err(e) => return Err(StudioDiag::new(Code::BadArgument, format!("--select {e}"))),
        }
    }
    Ok(o)
}

/// Applies `--select` as user selections (STUDIO-REQ-0223).
fn select(studio: &mut Studio, handles: &[String]) -> Result<(), StudioDiag> {
    let instances = studio
        .snapshot()
        .netlist
        .as_ref()
        .map_or(0, |n| n.instance_count());
    let paths = studio
        .snapshot()
        .timing
        .as_ref()
        .map_or(0, |t| t.total_paths());
    if handles.is_empty() {
        return Ok(());
    }
    let mut sel = Selection::default();
    for h in handles {
        let x = Handle::parse(h).map_err(|e| StudioDiag::new(Code::BadArgument, e.to_string()))?;
        let row = x.row();
        let known = match x.prefix() {
            "path" => (row as usize) < paths,
            _ => (row as usize) < instances,
        };
        if !known {
            return Err(StudioDiag::new(
                Code::SelectionHandleUnknown,
                format!("--select {h}: no such object in {}", studio.snapshot().rev),
            ));
        }
        if x.prefix() == "path" {
            if !sel.paths.contains(&(row as usize)) {
                sel.paths.push(row as usize);
            }
        } else if !sel.instances.contains(&row) {
            sel.instances.push(row);
        }
    }
    studio.select(sel);
    Ok(())
}

/// Run the workbench with explicitly supplied command-line arguments.
pub fn run(args: Vec<std::ffi::OsString>) -> ExitCode {
    let args = match Args::try_parse_from(args) {
        Ok(a) => a,
        Err(e) => {
            // `--help` and `--version` are successes of their own.
            if !e.use_stderr() {
                let _ = e.print();
                return ExitCode::SUCCESS;
            }
            report(&StudioDiag::new(Code::BadArgument, e.to_string().trim()));
            return ExitCode::from(2);
        }
    };
    let opts = match options(&args) {
        Ok(o) => o,
        Err(d) => {
            report(&d);
            return status(d.code);
        }
    };
    let mut studio = match Studio::open(opts) {
        Ok(s) => s,
        Err(d) => {
            report(&d);
            return status(d.code);
        }
    };
    if let Err(d) = select(&mut studio, &args.select) {
        report(&d);
        return ExitCode::from(1);
    }
    if args.print {
        let text = print::summary(&studio, args.max_rows);
        let mut out = std::io::stdout().lock();
        let _ = out.write_all(text.as_bytes());
        let _ = out.flush();
        return ExitCode::SUCCESS;
    }
    run_window(studio)
}

#[cfg(feature = "gui")]
fn run_window(studio: Studio) -> ExitCode {
    match crate::gui::run(studio) {
        Ok(()) => ExitCode::SUCCESS,
        Err(d) => {
            report(&d);
            ExitCode::from(1)
        }
    }
}

#[cfg(not(feature = "gui"))]
fn run_window(_studio: Studio) -> ExitCode {
    report(&StudioDiag::new(
        Code::GuiUnavailable,
        "this build has no window (feature `gui` off); use --print",
    ));
    ExitCode::from(1)
}

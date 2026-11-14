// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

use crate::{
    error::{Error, Result},
    exec, flow,
};
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use std::{io::Write, path::PathBuf, process::ExitCode};

#[derive(Debug, Parser)]
#[command(
    name = "rusteda flow",
    version,
    about = "Daemonless ASIC flows with RustSys synthesis and RustSTA timing"
)]
struct Cli {
    #[arg(long, short = 'w', global = true)]
    workspace: Option<PathBuf>,
    #[command(subcommand)]
    command: Action,
}

#[derive(Debug, clap::Args)]
struct ExecArguments {
    /// The step-graph manifest to execute (`rusteda.ihp-flow.v1` or `rusteda.native-flow.v1`).
    #[arg(long)]
    manifest: PathBuf,
    /// Base directory of relative pinned inputs [default: the manifest's directory].
    #[arg(long)]
    project: Option<PathBuf>,
    /// New or empty directory for the run (`--workspace` is accepted as well).
    #[arg(long)]
    output: Option<PathBuf>,
    /// Must be `empty`: a run never continues another one.
    #[arg(long, required_unless_present = "dry_run")]
    base_rev: Option<String>,
    /// The PDK the manifest must be for: a family (`ihp`, `sky130`, `gf180`) or the manifest's
    /// own `pdk` name [default: the manifest's PDK].
    #[arg(long, value_parser = pdk_argument)]
    pdk: Option<String>,
    /// When given, must be the pinned `rustroad` tool.
    #[arg(long)]
    rustroad: Option<PathBuf>,
    /// When given, must be the pinned `gnu_time` tool.
    #[arg(long)]
    gnu_time: Option<PathBuf>,
    /// Run the serial prefix that ends with this step.
    #[arg(long)]
    stop_after: Option<String>,
    /// Worker identity (64 hex digits) [default: RUSTEDA_WORKER_ID].
    #[arg(long)]
    worker_id: Option<String>,
    /// Ownership directory the run creates [default: RUSTEDA_OWNERSHIP].
    #[arg(long)]
    ownership: Option<PathBuf>,
    /// Write progress events as JSON lines to a new file, or to standard output with `-`.
    #[arg(long)]
    events: Option<PathBuf>,
    /// Validate and print the resolved step graph; create and run nothing.
    #[arg(long)]
    dry_run: bool,
}

/// `--pdk`: a PDK name of `[A-Za-z0-9_.-]{1,64}`.
fn pdk_argument(text: &str) -> std::result::Result<String, String> {
    if exec::manifest::pdk_name(text) {
        Ok(text.to_owned())
    } else {
        Err("a PDK name of 1 to 64 letters, digits, '_', '.' or '-'".into())
    }
}

#[derive(Debug, Subcommand)]
enum Action {
    /// Capture configuration/inputs and create rev0; existing HEAD is never reset.
    Init {
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        base_rev: String,
    },
    /// Show the bounded sequence and unsupported capabilities.
    Plan {
        #[arg(long, default_value = "HEAD")]
        rev: String,
    },
    /// Inspect configured executable paths and input identity, without executing tools.
    Doctor {
        #[arg(long, default_value = "HEAD")]
        rev: String,
    },
    /// Run pending steps with immutable checkpoints; completed steps are validated and reused.
    Run {
        #[arg(long)]
        base_rev: String,
        #[arg(long)]
        until: Option<String>,
        #[arg(long)]
        retry_failed: bool,
    },
    /// Claim an external task and release the workspace lock while it executes.
    Prepare {
        #[arg(long)]
        base_rev: String,
        #[arg(long)]
        retry_failed: bool,
    },
    /// Inspect the active revision-bound worker request.
    Worker {
        #[arg(long, default_value = "HEAD")]
        rev: String,
    },
    /// Verify a worker receipt and commit its completed or failed result.
    Finish {
        #[arg(long)]
        base_rev: String,
        #[arg(long)]
        receipt: PathBuf,
    },
    /// Query results, scope, diagnostics and evidence locations.
    Report {
        #[arg(long, default_value = "HEAD")]
        rev: String,
    },
    /// Alias for report.
    Status {
        #[arg(long, default_value = "HEAD")]
        rev: String,
    },
    /// Execute a hash-pinned step-graph manifest as a foreground worker (or plan it).
    /// A repeated option takes its last value, so a wrapper can append overrides.
    #[command(args_override_self = true)]
    Exec(Box<ExecArguments>),
    /// State of an executor output directory, running or finished.
    ExecStatus {
        /// The run's output directory (`--workspace` is accepted as well).
        #[arg(long)]
        output: Option<PathBuf>,
        /// Manifest of the run, to list its pending steps.
        #[arg(long)]
        manifest: Option<PathBuf>,
    },
}

/// The document a command prints and, when it is not implied by `ok`, its exit code.
struct Reply {
    value: Value,
    code: Option<u8>,
    /// Progress events were written to standard output before the document.
    stream: bool,
}

impl From<Value> for Reply {
    fn from(value: Value) -> Self {
        Self {
            value,
            code: None,
            stream: false,
        }
    }
}

fn failure(error: &Error) -> Value {
    json!({"schema":"rustlane.v1","ok":false,"error":{"code":error.code,"message":error.message}})
}

/// `exec`: 0 completed, 1 a started run failed, 2 refused before any step ran,
/// 4 the output directory is not empty, 130 cancelled by a signal.
fn exec(options: &exec::Options, dry_run: bool) -> Reply {
    let refused = |error: Error| Reply {
        value: failure(&error),
        code: Some(if error.code == "REVISION_CONFLICT" {
            4
        } else {
            2
        }),
        stream: false,
    };
    if dry_run {
        return exec::plan(options).map_or_else(refused, Reply::from);
    }
    if let Err(error) = exec::handle_termination_signals() {
        return refused(error);
    }
    match exec::execute(options) {
        Ok(outcome) => Reply {
            value: outcome.value,
            code: Some(outcome.exit_code),
            stream: outcome.events_on_stdout,
        },
        Err(error) => refused(error),
    }
}

fn execute(cli: Cli) -> Result<Reply> {
    let directory = |output: Option<PathBuf>, workspace: Option<PathBuf>| {
        output
            .or(workspace)
            .ok_or_else(|| Error::new("OUTPUT_REQUIRED", "pass --output <dir>"))
    };
    let (root, command) = match cli.command {
        Action::Exec(arguments) => {
            let ExecArguments {
                manifest,
                project,
                output,
                base_rev,
                pdk,
                rustroad,
                gnu_time,
                stop_after,
                worker_id,
                ownership,
                events,
                dry_run,
            } = *arguments;
            let options = exec::Options {
                project: project.unwrap_or_else(|| {
                    manifest
                        .parent()
                        .filter(|parent| !parent.as_os_str().is_empty())
                        .unwrap_or(std::path::Path::new("."))
                        .to_path_buf()
                }),
                manifest,
                output: directory(output, cli.workspace)?,
                base_rev: base_rev.unwrap_or_else(|| "empty".into()),
                pdk,
                rustroad,
                gnu_time,
                stop_after,
                worker_id,
                ownership,
                events,
            };
            return Ok(exec(&options, dry_run));
        }
        Action::ExecStatus { output, manifest } => {
            return exec::status(&directory(output, cli.workspace)?, manifest.as_deref())
                .map(Reply::from);
        }
        command => (
            cli.workspace
                .ok_or_else(|| Error::new("WORKSPACE_REQUIRED", "pass --workspace <dir>"))?,
            command,
        ),
    };
    match command {
        Action::Init { config, base_rev } => flow::init(&root, &config, &base_rev),
        Action::Plan { rev } => flow::plan(&root, &rev),
        Action::Doctor { rev } => flow::doctor(&root, &rev),
        Action::Run {
            base_rev,
            until,
            retry_failed,
        } => flow::run(&root, &base_rev, until.as_deref(), retry_failed),
        Action::Prepare {
            base_rev,
            retry_failed,
        } => crate::worker::prepare(&root, &base_rev, retry_failed),
        Action::Worker { rev } => crate::worker::request(&root, &rev),
        Action::Finish { base_rev, receipt } => crate::worker::finish(&root, &base_rev, &receipt),
        Action::Report { rev } | Action::Status { rev } => flow::report(&root, &rev),
        Action::Exec(_) | Action::ExecStatus { .. } => Err(Error::new(
            "INTERNAL",
            "executor commands are handled above",
        )),
    }
    .map(Reply::from)
}

/// Execute the internal flow command under the unified RustEDA entrypoint.
pub fn run(args: Vec<std::ffi::OsString>) -> ExitCode {
    let cli = Cli::parse_from(args);
    let result = execute(cli);
    let (mut value, code) = match result {
        Ok(reply) => {
            let code = reply.code.unwrap_or(
                if reply.value.get("ok").and_then(Value::as_bool) == Some(true) {
                    0
                } else {
                    1
                },
            );
            let mut value = reply.value;
            if reply.stream {
                // The last line of an event stream is the result, marked as such.
                value["event"] = json!("result");
            }
            (value, code)
        }
        Err(error) => (
            failure(&error),
            if error.code == "REVISION_CONFLICT" {
                4
            } else {
                1
            },
        ),
    };
    if let Some(object) = value.as_object_mut() {
        object
            .entry("schema")
            .or_insert_with(|| json!("rustlane.v1"));
    }
    let mut stdout = std::io::stdout().lock();
    if serde_json::to_writer(&mut stdout, &value).is_err() || writeln!(stdout).is_err() {
        return ExitCode::FAILURE;
    }
    ExitCode::from(code)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// FLOWEXEC-REQ-0032: the argument list `prepare` publishes for the native executor is a valid
    /// `exec` command line, and a wrapper's appended override wins.
    #[test]
    fn the_published_native_request_parses_as_an_exec_command() {
        let line = [
            "rusteda flow",
            "exec",
            "--project",
            "/p",
            "--manifest",
            "/p/m.json",
            "--pdk",
            "ihp",
            "--output",
            "/w/steps/rtl2gds/attempt1/output",
            "--rustroad",
            "/t/rustroad",
            "--gnu-time",
            "/t/time",
            "--base-rev",
            "empty",
            "--stop-after",
            "a",
            "--stop-after",
            "b",
        ];
        let cli = Cli::try_parse_from(line).unwrap();
        let Action::Exec(arguments) = cli.command else {
            panic!("not an exec command");
        };
        assert_eq!(arguments.manifest, PathBuf::from("/p/m.json"));
        assert_eq!(arguments.base_rev.as_deref(), Some("empty"));
        assert_eq!(arguments.stop_after.as_deref(), Some("b"));
        assert!(!arguments.dry_run);
        assert_eq!(arguments.pdk.as_deref(), Some("ihp"));
        // FLOWEXEC-REQ-0033: `--pdk` admits the other PDK families and full PDK names.
        for pdk in ["sky130", "gf180", "sky130A", "gf180mcuD", "ihp-sg13g2"] {
            let line = [
                "rusteda flow",
                "exec",
                "--manifest",
                "m",
                "--dry-run",
                "--pdk",
                pdk,
            ];
            assert!(Cli::try_parse_from(line).is_ok(), "{pdk}");
        }
        assert!(
            Cli::try_parse_from(["rusteda flow", "exec", "--manifest", "m", "--pdk", "a/b"])
                .is_err()
        );
        assert!(
            Cli::try_parse_from(["rusteda flow", "exec", "--manifest", "m", "--output", "o"])
                .is_err()
        );
        assert!(
            Cli::try_parse_from(["rusteda flow", "exec", "--manifest", "m", "--dry-run"]).is_ok()
        );
    }
}

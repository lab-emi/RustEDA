use clap::{Parser, Subcommand};
use rustlane::{
    error::{Error, Result},
    flow,
};
use serde_json::{Value, json};
use std::{io::Write, path::PathBuf, process::ExitCode};

#[derive(Debug, Parser)]
#[command(
    version,
    about = "Daemonless ASIC flows with RustSys synthesis and RustSTA timing"
)]
struct Cli {
    #[arg(long, short = 'w', global = true)]
    workspace: Option<PathBuf>,
    #[command(subcommand)]
    command: Action,
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
}

fn execute(cli: Cli) -> Result<Value> {
    let root = cli
        .workspace
        .ok_or_else(|| Error::new("WORKSPACE_REQUIRED", "pass --workspace <dir>"))?;
    match cli.command {
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
        } => rustlane::worker::prepare(&root, &base_rev, retry_failed),
        Action::Worker { rev } => rustlane::worker::request(&root, &rev),
        Action::Finish { base_rev, receipt } => {
            rustlane::worker::finish(&root, &base_rev, &receipt)
        }
        Action::Report { rev } | Action::Status { rev } => flow::report(&root, &rev),
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let result = execute(cli);
    let (value, code) = match result {
        Ok(value) => {
            let code = if value.get("ok").and_then(Value::as_bool) == Some(true) {
                0
            } else {
                1
            };
            (value, code)
        }
        Err(Error { code, message }) => (
            json!({"schema":"rustlane.v1","ok":false,"error":{"code":code,"message":message}}),
            if code == "REVISION_CONFLICT" { 4 } else { 1 },
        ),
    };
    let mut stdout = std::io::stdout().lock();
    if serde_json::to_writer(&mut stdout, &value).is_err() || writeln!(stdout).is_err() {
        return ExitCode::FAILURE;
    }
    ExitCode::from(code)
}

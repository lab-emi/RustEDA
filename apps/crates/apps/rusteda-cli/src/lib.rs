// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Product adapter for the canonical shared workspace command.
pub use rusteda_cli_shared::*;

/// Retain this product's command version while sharing the implementation.
pub fn command() -> clap::Command {
    command_with_version(env!("CARGO_PKG_VERSION"))
        .subcommand(clap::Command::new("flow").about("Run and inspect revisioned design flows"))
        .subcommand(clap::Command::new("studio").about("Open the RustEDA workbench"))
}

/// Run the shared CLI using this product's version in output and history.
pub fn run(args: Vec<std::ffi::OsString>) -> std::process::ExitCode {
    if let Some(command) = args.get(1).and_then(|s| s.to_str()) {
        if command == "flow" || command == "studio" {
            let command = command.to_owned();
            let forwarded = std::iter::once(std::ffi::OsString::from(format!("rusteda {command}")))
                .chain(args.into_iter().skip(2))
                .collect();
            return if command == "flow" {
                rusteda_flow::cli::run(forwarded)
            } else {
                rusteda_studio::cli::run(forwarded)
            };
        }
        if command == "--help" || command == "-h" {
            let _ = self::command().print_help();
            return std::process::ExitCode::SUCCESS;
        }
    }
    run_with_version(args, env!("CARGO_PKG_VERSION"))
}

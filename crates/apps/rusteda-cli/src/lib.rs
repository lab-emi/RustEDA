// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Product adapter for the canonical shared workspace command.
pub use rusteda_cli_shared::*;

/// Retain this product's command version while sharing the implementation.
pub fn command() -> clap::Command {
    command_with_version(env!("CARGO_PKG_VERSION"))
}

/// Run the shared CLI using this product's version in output and history.
pub fn run(args: Vec<std::ffi::OsString>) -> std::process::ExitCode {
    run_with_version(args, env!("CARGO_PKG_VERSION"))
}

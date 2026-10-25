// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

use std::process::ExitCode;

fn main() -> ExitCode {
    rusteda_cli::run(std::env::args_os().collect())
}

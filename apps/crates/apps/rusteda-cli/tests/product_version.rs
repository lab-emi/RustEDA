// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

use clap::error::ErrorKind;

#[test]
fn caller_version_is_used_by_top_level_and_subcommands() {
    for args in [
        vec!["rusteda", "--version"],
        vec!["rusteda", "import", "--version"],
    ] {
        let error = rusteda_cli::command_with_version("7.8.9")
            .try_get_matches_from(args)
            .unwrap_err();
        assert_eq!(error.kind(), ErrorKind::DisplayVersion);
        assert!(error.to_string().contains("7.8.9"));
    }
}

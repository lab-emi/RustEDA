// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! `cargo xtask pdk fetch [--dir DIR] [--only NAME...] [--offline VENDOR_DIR]`: downloads and
//! verifies the pinned standard-cell libraries of `verification/pdks.toml` ([`mod@fetch`]) into
//! the library directory: `--dir`, else `$RUSTEDA_PDK_DIR`, else `target/pdk` of the repository.
//! The workbench runs no PDK suite of its own; the libraries serve local smoke tests that build
//! workspaces with the suite's tools.
//!
//! `fetch.rs`, `manifest.rs`, `json.rs` and `verification/pdks.toml` are shared verbatim with the
//! other RustEDA tool repositories; this file belongs to this repository. `json.rs` serves the
//! suites of the tool repositories and is compiled here for its own tests only.

mod fetch;
#[allow(dead_code)]
pub(crate) mod json;
#[allow(dead_code)]
mod manifest;

use std::path::{Path, PathBuf};

/// Options of `pdk fetch`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct FetchOptions {
    pub(crate) dir: Option<PathBuf>,
    pub(crate) only: Vec<String>,
    pub(crate) offline: Option<PathBuf>,
}

/// The value of option `name` at `args[*i]` (`--name VALUE` or `--name=VALUE`), advancing `*i`
/// past a separate value; `None` when `args[*i]` is not that option.
fn option_value(args: &[String], i: &mut usize, name: &str) -> Option<Result<String, String>> {
    let a = args.get(*i)?;
    if let Some(v) = a.strip_prefix(name).and_then(|r| r.strip_prefix('=')) {
        return Some(if v.is_empty() {
            Err(format!("{name} needs a value"))
        } else {
            Ok(v.to_string())
        });
    }
    if a != name {
        return None;
    }
    *i += 1;
    Some(match args.get(*i) {
        Some(v) if !v.is_empty() && !v.starts_with("--") => Ok(v.clone()),
        _ => Err(format!("{name} needs a value")),
    })
}

/// `pdk fetch [--dir DIR] [--only NAME...] [--offline VENDOR_DIR]`: the arguments after `pdk`.
pub(crate) fn fetch_args(args: &[String]) -> Result<FetchOptions, String> {
    match args.first().map(String::as_str) {
        Some("fetch") => {}
        Some(other) => return Err(format!("unknown pdk command '{other}' (expected 'fetch')")),
        None => {
            return Err(
                "usage: cargo xtask pdk fetch [--dir DIR] [--only NAME...] [--offline VENDOR_DIR]"
                    .into(),
            );
        }
    }
    let mut o = FetchOptions::default();
    let mut i = 1;
    while let Some(a) = args.get(i) {
        if let Some(v) = option_value(args, &mut i, "--dir") {
            o.dir = Some(PathBuf::from(v?));
        } else if let Some(v) = option_value(args, &mut i, "--only") {
            for item in v?.split(',').filter(|s| !s.is_empty()) {
                o.only.push(item.to_string());
            }
        } else if let Some(v) = option_value(args, &mut i, "--offline") {
            o.offline = Some(PathBuf::from(v?));
        } else if a.starts_with('-') {
            return Err(format!("unknown option '{a}'"));
        } else {
            return Err(format!("unexpected argument '{a}'"));
        }
        i += 1;
    }
    Ok(o)
}

/// The `pdk fetch` command.
pub(crate) fn fetch(root: &Path, o: &FetchOptions) -> Result<(), String> {
    let mut libs = manifest::load(root)?;
    if !o.only.is_empty() {
        for wanted in &o.only {
            if !libs
                .iter()
                .any(|l| l.name == *wanted || l.library == *wanted)
            {
                return Err(format!("pdk fetch: unknown library '{wanted}' in --only"));
            }
        }
        libs.retain(|l| o.only.iter().any(|w| *w == l.name || *w == l.library));
    }
    let dir = fetch::library_dir(root, o.dir.as_deref(), std::env::var_os(fetch::DIR_VAR));
    if let Some(vendor) = &o.offline {
        let abs_vendor =
            std::path::absolute(vendor).map_err(|e| format!("{}: {e}", vendor.display()))?;
        std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        for lib in &mut libs {
            let fname = lib.file_name();
            let prebuilt = abs_vendor.join(&fname);
            if prebuilt.is_file() {
                let dest = dir.join(&fname);
                if prebuilt != dest {
                    std::fs::copy(&prebuilt, &dest)
                        .map_err(|e| format!("{}: {e}", dest.display()))?;
                }
            } else {
                let base = format!("file://{}/", abs_vendor.display());
                match &mut lib.source {
                    manifest::Source::File { url } => {
                        *url = format!("{base}{fname}");
                    }
                    manifest::Source::Assembled { base_url, .. } => {
                        *base_url = base;
                    }
                }
            }
        }
    }
    fetch::run(root, &libs, &dir, &[])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    // `pdk fetch [--dir DIR] [--only NAME...] [--offline VENDOR_DIR]`: the subcommand is
    // required, options take `--opt val` or `--opt=val`, and unknown arguments fail.
    #[test]
    fn fetch_arguments() {
        let a = |s: &[&str]| fetch_args(&strings(s));
        assert_eq!(a(&["fetch"]), Ok(FetchOptions::default()));
        let dir = Ok(FetchOptions {
            dir: Some("libs".into()),
            only: Vec::new(),
            offline: None,
        });
        assert_eq!(a(&["fetch", "--dir", "libs"]), dir);
        assert_eq!(a(&["fetch", "--dir=libs"]), dir);
        assert_eq!(
            a(&["fetch", "--only", "a", "--only=b,c", "--offline", "v"]),
            Ok(FetchOptions {
                dir: None,
                only: strings(&["a", "b", "c"]),
                offline: Some("v".into()),
            })
        );
        assert!(a(&[]).is_err());
        assert!(a(&["get"]).is_err());
        assert!(a(&["fetch", "--dir"]).is_err());
        assert!(a(&["fetch", "--fast"]).is_err());
        assert!(a(&["fetch", "extra"]).is_err());
    }

    // The shared manifest of pinned libraries reads.
    #[test]
    fn manifest_reads() {
        let libs = manifest::load(&crate::root()).unwrap();
        assert!(!libs.is_empty());
    }
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Locate canonical first-party source through Cargo, including explicit local patches.
use crate::pdk::json::Json;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

fn from_metadata(text: &str) -> Result<PathBuf, String> {
    let value = Json::parse(text)?;
    let Some(Json::List(packages)) = value.get("packages") else {
        return Err("Cargo metadata has no packages".into());
    };
    if packages.len() > 4096 {
        return Err("Cargo package budget exceeded".into());
    }
    let mut found = None;
    for package in packages {
        if package.get("name").and_then(Json::as_str) != Some("rusteda-core") {
            continue;
        }
        let manifest = package
            .get("manifest_path")
            .and_then(Json::as_str)
            .ok_or("foundation has no manifest path")?;
        let root = Path::new(manifest)
            .ancestors()
            .nth(4)
            .ok_or("invalid foundation manifest path")?
            .to_path_buf();
        if found.replace(root).is_some() {
            return Err("multiple foundation packages".into());
        }
    }
    found.ok_or_else(|| "canonical rusteda-core is missing from Cargo metadata".into())
}

pub(crate) fn root(workspace: &Path) -> Result<Option<PathBuf>, String> {
    let manifest = std::fs::read_to_string(workspace.join("Cargo.toml")).unwrap_or_default();
    if !manifest.contains("RustEDA-Common.git") {
        return Ok(None);
    }
    let cargo = std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
    let mut child = Command::new(cargo)
        .args(["metadata", "--format-version", "1", "--locked", "--offline"])
        .current_dir(workspace)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| e.to_string())?;
    let output = child.stdout.take().ok_or("missing Cargo output")?;
    let (send, receive) = mpsc::sync_channel(1);
    let reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let read = output.take(16 * 1024 * 1024 + 1).read_to_end(&mut bytes);
        let _ = send.send(read.map(|_| bytes));
    });
    let bytes = match receive.recv_timeout(Duration::from_secs(30)) {
        Ok(Ok(bytes)) if bytes.len() <= 16 * 1024 * 1024 => bytes,
        _ => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = reader.join();
            return Err("Cargo metadata exceeded its time/byte budget".into());
        }
    };
    let _ = reader.join();
    if !child.wait().map_err(|e| e.to_string())?.success() {
        return Err("Cargo metadata failed; fetch the pinned foundation first".into());
    }
    let text = std::str::from_utf8(&bytes).map_err(|e| e.to_string())?;
    from_metadata(text).map(Some)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn metadata_resolves_the_exact_library_tree() {
        let input = r#"{"packages":[{"name":"rusteda-core","manifest_path":"/cache/common/crates/core/rusteda-core/Cargo.toml"}]}"#;
        assert_eq!(from_metadata(input).unwrap(), Path::new("/cache/common"));
    }
    #[test]
    fn missing_or_duplicate_foundations_are_rejected() {
        assert!(from_metadata(r#"{"packages":[]}"#).is_err());
        let input = r#"{"packages":[{"name":"rusteda-core","manifest_path":"/a/crates/core/rusteda-core/Cargo.toml"},{"name":"rusteda-core","manifest_path":"/b/crates/core/rusteda-core/Cargo.toml"}]}"#;
        assert!(from_metadata(input).is_err());
    }
}

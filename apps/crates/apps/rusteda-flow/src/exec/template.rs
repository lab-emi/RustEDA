// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Argument placeholders and JSON templates of a step graph.
//!
//! `{output}`, `{threads}`, `{files:<name>}` and `{tools:<name>}` expand inside strings.
//! A template object with the single member `$identity`, `$sha256`, `$input` or `$tool`
//! is replaced by a file identity (or its hash). One render visits at most 65,536 nodes
//! to depth 32; an expanded string is at most 65,536 characters.

use super::{
    identity::{self, Identity},
    pyjson::Json,
};
use crate::error::{Error, Result};
use std::path::{Component, Path, PathBuf};

const MAX_NODES: usize = 65536;
const MAX_DEPTH: usize = 32;
const MAX_STRING: usize = 65536;

fn bad(message: impl Into<String>) -> Error {
    Error::new("TEMPLATE_INVALID", message)
}

/// A normal relative path: not empty, not absolute, no `..` component.
pub fn relative(value: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    let mut clean = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Normal(part) => clean.push(part),
            Component::CurDir => (),
            _ => return Err(bad("expected a normal relative output path")),
        }
    }
    if value.is_empty() || clean.as_os_str().is_empty() {
        return Err(bad("expected a normal relative output path"));
    }
    Ok(clean)
}

#[derive(Debug)]
pub struct Context {
    /// Resolved output directory.
    pub output: PathBuf,
    pub output_text: String,
    pub threads: usize,
    /// `files:<name>` then `tools:<name>` in manifest order, with verified identities.
    pub records: Vec<(String, Identity)>,
}

impl Context {
    pub fn record(&self, key: &str) -> Option<&Identity> {
        self.records
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, identity)| identity)
    }

    /// A path below the output directory; resolving it must not leave the directory.
    pub fn output_path(&self, value: &str) -> Result<PathBuf> {
        let path = self.output.join(relative(value)?);
        if !resolve_lenient(&path).starts_with(&self.output) {
            return Err(bad("output path escapes workspace"));
        }
        Ok(path)
    }

    /// Replace the placeholders of one argument or template string, in the reference order:
    /// `{output}`, `{threads}`, then every pinned input by name.
    pub fn expand(&self, value: &str) -> Result<String> {
        let mut text = if value.contains('{') {
            let mut text = value
                .replace("{output}", &self.output_text)
                .replace("{threads}", &self.threads.to_string());
            for (name, record) in &self.records {
                if text.contains('{') {
                    text = text.replace(&format!("{{{name}}}"), &record.path);
                }
            }
            text
        } else {
            value.to_owned()
        };
        if unresolved(&text) {
            return Err(bad("unresolved input/tool argument placeholder"));
        }
        if text.chars().count() > MAX_STRING || text.contains('\0') {
            return Err(bad("argument string budget exceeded"));
        }
        text.shrink_to_fit();
        Ok(text)
    }

    /// Instantiate a template. `$identity` and `$sha256` hash files of this run.
    pub fn render(&self, value: &Json) -> Result<Json> {
        let mut nodes = 0;
        self.render_node(value, 0, &mut nodes)
    }

    fn render_node(&self, value: &Json, depth: usize, nodes: &mut usize) -> Result<Json> {
        *nodes += 1;
        if depth > MAX_DEPTH || *nodes > MAX_NODES {
            return Err(bad("template depth/node budget exceeded"));
        }
        Ok(match value {
            Json::Object(members) => {
                if let [(key, argument)] = members.as_slice() {
                    let name = || {
                        argument
                            .as_str()
                            .ok_or_else(|| bad(format!("{key} takes one string")))
                    };
                    match key.as_str() {
                        "$identity" => return Ok(self.produced(name()?)?.json()),
                        "$sha256" => return Ok(Json::str(self.produced(name()?)?.sha256)),
                        "$input" | "$tool" => {
                            let group = if key == "$input" { "files" } else { "tools" };
                            let key = format!("{group}:{}", name()?);
                            return self
                                .record(&key)
                                .map(Identity::json)
                                .ok_or_else(|| bad(format!("unknown pinned input: {key}")));
                        }
                        _ => (),
                    }
                }
                let mut object = Json::Object(Vec::with_capacity(members.len()));
                for (key, item) in members {
                    object.set(key.clone(), self.render_node(item, depth + 1, nodes)?);
                }
                object
            }
            Json::Array(items) => Json::Array(
                items
                    .iter()
                    .map(|item| self.render_node(item, depth + 1, nodes))
                    .collect::<Result<_>>()?,
            ),
            Json::Str(text) => Json::Str(self.expand(text)?),
            other => other.clone(),
        })
    }

    fn produced(&self, value: &str) -> Result<Identity> {
        identity::identity(&self.output_path(value)?)
    }
}

/// True when a `{files:...}` or `{tools:...}` placeholder is left in the text.
fn unresolved(text: &str) -> bool {
    ["{files:", "{tools:"].iter().any(|open| {
        text.match_indices(open)
            .any(|(at, _)| text[at + open.len()..].contains('}'))
    })
}

/// Resolve symbolic links of the existing part of a path and append the rest, removing
/// `.` and applying `..` to the path resolved so far (the behaviour of a non-strict
/// `realpath`). A relative path starts at the current directory.
pub fn resolve_lenient(path: &Path) -> PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_default().join(path)
    };
    let mut resolved = PathBuf::new();
    let mut exists = true;
    for component in absolute.components() {
        match component {
            Component::ParentDir => {
                resolved.pop();
            }
            Component::CurDir => (),
            other => {
                resolved.push(other);
                if exists {
                    match std::fs::canonicalize(&resolved) {
                        Ok(real) => resolved = real,
                        Err(_) => exists = false,
                    }
                }
            }
        }
    }
    resolved
}

#[cfg(test)]
mod tests {
    use super::*;

    fn context(root: &Path) -> Context {
        let output = std::fs::canonicalize(root).unwrap();
        Context {
            output_text: output.to_str().unwrap().to_owned(),
            output,
            threads: 4,
            records: vec![
                (
                    "files:rtl".into(),
                    Identity {
                        path: "/pinned/design.v".into(),
                        sha256: "a".repeat(64),
                        bytes: 7,
                    },
                ),
                (
                    "tools:python".into(),
                    Identity {
                        path: "/pinned/python".into(),
                        sha256: "b".repeat(64),
                        bytes: 9,
                    },
                ),
            ],
        }
    }

    /// FLOWEXEC-REQ-0007: the four placeholders, and what is left unresolved.
    #[test]
    fn placeholders_expand_and_unknown_inputs_are_refused() {
        let root = tempfile::tempdir().unwrap();
        let c = context(root.path());
        assert_eq!(
            c.expand("{output}/a --threads={threads} {files:rtl} {tools:python} {other}")
                .unwrap(),
            format!(
                "{}/a --threads=4 /pinned/design.v /pinned/python {{other}}",
                c.output_text
            )
        );
        assert_eq!(c.expand("{files:rtl").unwrap(), "{files:rtl");
        for invalid in ["{files:missing}", "x{tools:}y", "nul\0"] {
            assert_eq!(c.expand(invalid).unwrap_err().code, "TEMPLATE_INVALID");
        }
        assert!(c.expand(&"x".repeat(MAX_STRING + 1)).is_err());
    }

    /// FLOWEXEC-REQ-0011: `$identity`, `$sha256`, `$input`, `$tool` and the template budget.
    #[test]
    fn templates_bind_inputs_and_files_of_the_run() {
        let root = tempfile::tempdir().unwrap();
        let c = context(root.path());
        std::fs::write(c.output.join("payload"), "observed child output").unwrap();
        let template = Json::parse(
            br#"{"run": "{output}", "n": 1.50, "rtl": {"$input": "rtl"}, "py": {"$tool": "python"},
                 "payload": {"$identity": "payload"}, "hash": [{"$sha256": "payload"}],
                 "$identity": "kept", "two": {"$input": "rtl", "x": 1}}"#,
        )
        .unwrap();
        let rendered = c.render(&template).unwrap();
        let text = rendered.to_line().unwrap();
        let payload = identity::identity(&c.output.join("payload")).unwrap();
        assert_eq!(
            text,
            format!(
                "{{\"run\": \"{out}\", \"n\": 1.5, \"rtl\": {{\"path\": \"/pinned/design.v\", \
                 \"sha256\": \"{a}\", \"bytes\": 7}}, \"py\": {{\"path\": \"/pinned/python\", \
                 \"sha256\": \"{b}\", \"bytes\": 9}}, \"payload\": {{\"path\": \"{out}/payload\", \
                 \"sha256\": \"{h}\", \"bytes\": 21}}, \"hash\": [\"{h}\"], \"$identity\": \
                 \"kept\", \"two\": {{\"$input\": \"rtl\", \"x\": 1}}}}",
                out = c.output_text,
                a = "a".repeat(64),
                b = "b".repeat(64),
                h = payload.sha256
            )
        );
        for invalid in [
            r#"{"$identity": "../escape"}"#,
            r#"{"$identity": "/etc/hostname"}"#,
            r#"{"$identity": ""}"#,
            r#"{"$identity": "."}"#,
            r#"{"$input": "missing"}"#,
            r#"{"$sha256": 3}"#,
        ] {
            let error = c
                .render(&Json::parse(invalid.as_bytes()).unwrap())
                .unwrap_err();
            assert_eq!(error.code, "TEMPLATE_INVALID", "{invalid}");
        }
        assert_eq!(
            c.render(&Json::parse(br#"{"$identity": "absent"}"#).unwrap())
                .unwrap_err()
                .code,
            "FILE_UNAVAILABLE"
        );
        let mut deep = Json::Null;
        for _ in 0..40 {
            deep = Json::Array(vec![deep]);
        }
        assert!(c.render(&deep).is_err());
    }

    #[test]
    fn a_link_out_of_the_output_directory_is_an_escape() {
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let c = context(root.path());
        std::os::unix::fs::symlink(outside.path(), c.output.join("link")).unwrap();
        assert!(c.output_path("link/file.json").is_err());
        assert!(c.output_path("new/dir/file.json").is_ok());
        assert!(c.output_path("a/../../b").is_err());
        assert_eq!(relative("./a//b/").unwrap(), PathBuf::from("a/b"));
    }
}

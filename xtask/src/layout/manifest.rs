// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! A line reader for the parts of crate manifests that the dependency rules need: the package name
//! and every dependency with its kind, whether written as `name = …`, `name.workspace = true`,
//! `[dependencies.name]` or under a `[target.….dependencies]` table, and its `package` rename. A
//! value spread over several lines (an inline table or array) is read as a whole. Renames made in
//! the root manifest's `[workspace.dependencies]` apply to members that inherit the dependency.

use std::collections::BTreeMap;
use std::path::Path;

/// The section a dependency is declared in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Kind {
    Normal,
    Dev,
    Build,
}

impl Kind {
    pub(super) fn section(self) -> &'static str {
        match self {
            Kind::Normal => "dependencies",
            Kind::Dev => "dev-dependencies",
            Kind::Build => "build-dependencies",
        }
    }
}

/// One declared dependency.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Dep {
    /// The name the manifest uses (the key).
    pub(super) key: String,
    /// The package depended on (`package = …`, else the key).
    pub(super) package: String,
    pub(super) kind: Kind,
    /// 1-based line of the declaration.
    pub(super) line: usize,
    /// Inherited from the workspace (`workspace = true`).
    pub(super) workspace: bool,
    /// Optional (`optional = true`): only a feature turns it on.
    pub(super) optional: bool,
}

/// What the rules need from one manifest.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(super) struct Manifest {
    pub(super) name: Option<String>,
    pub(super) deps: Vec<Dep>,
    /// `[workspace.dependencies]`: key → package.
    pub(super) workspace_deps: BTreeMap<String, String>,
}

#[derive(Clone, Debug)]
enum Section {
    Package,
    Deps(Kind),
    /// `[dependencies.<key>]`: index of the dependency in `deps`.
    DepTable(usize),
    WorkspaceDeps,
    /// `[workspace.dependencies.<key>]`.
    WorkspaceDepTable(String),
    Other,
}

/// Removes a trailing comment (a `#` outside quotes).
fn strip_comment(line: &str) -> &str {
    let mut quote: Option<char> = None;
    let mut escaped = false;
    for (i, c) in line.char_indices() {
        match quote {
            Some(q) => {
                if escaped {
                    escaped = false;
                } else if c == '\\' && q == '"' {
                    escaped = true;
                } else if c == q {
                    quote = None;
                }
            }
            None if c == '"' || c == '\'' => quote = Some(c),
            None if c == '#' => return line.get(..i).unwrap_or(line),
            None => {}
        }
    }
    line
}

/// Splits a dotted key or header at dots outside quotes, trimming and unquoting each part.
fn dotted(s: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut cur = String::new();
    let mut quote: Option<char> = None;
    for c in s.chars() {
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => cur.push(c),
            None if c == '"' || c == '\'' => quote = Some(c),
            None if c == '.' => parts.push(std::mem::take(&mut cur).trim().to_string()),
            None => cur.push(c),
        }
    }
    parts.push(cur.trim().to_string());
    parts
}

/// Splits `key = value` at the first `=` outside quotes.
fn key_value(line: &str) -> Option<(&str, &str)> {
    let mut quote: Option<char> = None;
    for (i, c) in line.char_indices() {
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if c == '"' || c == '\'' => quote = Some(c),
            None if c == '=' => return Some((line.get(..i)?, line.get(i + 1..)?)),
            None => {}
        }
    }
    None
}

/// Net count of opening minus closing brackets and braces outside strings.
fn depth_change(s: &str) -> i64 {
    let mut d = 0;
    let mut quote: Option<char> = None;
    for c in s.chars() {
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if c == '"' || c == '\'' => quote = Some(c),
            None if c == '[' || c == '{' => d += 1,
            None if c == ']' || c == '}' => d -= 1,
            None => {}
        }
    }
    d
}

/// The position just after `key` followed by `=` at a key boundary in `value`, if any.
fn after_key<'a>(value: &'a str, key: &str) -> Option<&'a str> {
    let mut rest = value;
    while let Some(pos) = rest.find(key) {
        let before = rest.get(..pos)?.chars().next_back();
        let after = rest.get(pos + key.len()..)?;
        let boundary = before.is_none_or(|c| !(c.is_ascii_alphanumeric() || c == '_' || c == '-'));
        if boundary && let Some(v) = after.trim_start().strip_prefix('=') {
            return Some(v.trim_start());
        }
        rest = after;
    }
    None
}

/// The string value of `key = "…"` inside an inline table text, if any.
fn inline_string(value: &str, key: &str) -> Option<String> {
    let v = after_key(value, key)?;
    let q = v.chars().next()?;
    if q == '"' || q == '\'' {
        let body = v.get(1..)?;
        let end = body.find(q)?;
        return Some(body.get(..end)?.to_string());
    }
    None
}

/// True when an inline table text sets `key = true`.
fn inline_true(value: &str, key: &str) -> bool {
    after_key(value, key).is_some_and(|v| v.starts_with("true"))
}

/// The string of a plain value (`"…"` or `'…'`).
fn plain_string(value: &str) -> Option<String> {
    inline_string(&format!("x = {value}"), "x")
}

fn dep_kind(word: &str) -> Option<Kind> {
    match word {
        "dependencies" => Some(Kind::Normal),
        "dev-dependencies" | "dev_dependencies" => Some(Kind::Dev),
        "build-dependencies" | "build_dependencies" => Some(Kind::Build),
        _ => None,
    }
}

/// The section a header line opens (after `[` and before `]`); pushes the dependency of a
/// `[dependencies.<key>]` header.
fn section(inner: &str, line_no: usize, m: &mut Manifest) -> Section {
    let parts = dotted(inner);
    let words: Vec<&str> = parts.iter().map(String::as_str).collect();
    match words.as_slice() {
        ["package"] => Section::Package,
        ["workspace", "dependencies"] => Section::WorkspaceDeps,
        ["workspace", "dependencies", name] => {
            m.workspace_deps
                .entry(name.to_string())
                .or_insert_with(|| name.to_string());
            Section::WorkspaceDepTable(name.to_string())
        }
        [k] | ["target", _, k] => dep_kind(k).map_or(Section::Other, Section::Deps),
        [k, name] | ["target", _, k, name] => match dep_kind(k) {
            Some(kind) => {
                m.deps.push(Dep {
                    key: name.to_string(),
                    package: name.to_string(),
                    kind,
                    line: line_no,
                    workspace: false,
                    optional: false,
                });
                Section::DepTable(m.deps.len() - 1)
            }
            None => Section::Other,
        },
        _ => Section::Other,
    }
}

/// Records one complete `keys = value` of `section`.
fn apply(m: &mut Manifest, section: &Section, keys: &[String], value: &str, line_no: usize) {
    let first = keys.first().map(String::as_str).unwrap_or("");
    let second = keys.get(1).map(String::as_str);
    // `x = { package = "…", workspace = true }`, `x.package = "…"` or `x.workspace = true`.
    let (rename, inherits) = match (keys.len(), second) {
        (1, _) => (
            inline_string(value, "package"),
            inline_true(value, "workspace"),
        ),
        (2, Some("package")) => (plain_string(value), false),
        (2, Some("workspace")) => (None, value.trim_start().starts_with("true")),
        _ => (None, false),
    };
    let optional = match (keys.len(), second) {
        (1, _) => inline_true(value, "optional"),
        (2, Some("optional")) => value.trim_start().starts_with("true"),
        _ => false,
    };
    match section {
        Section::Package => {
            if keys.len() == 1 && first == "name" {
                m.name = plain_string(value);
            }
        }
        Section::Deps(kind) => {
            match m
                .deps
                .iter_mut()
                .find(|d| d.kind == *kind && d.key == first)
            {
                Some(d) => {
                    if let Some(p) = rename {
                        d.package = p;
                    }
                    d.workspace |= inherits;
                    d.optional |= optional;
                }
                None => m.deps.push(Dep {
                    key: first.to_string(),
                    package: rename.unwrap_or_else(|| first.to_string()),
                    kind: *kind,
                    line: line_no,
                    workspace: inherits,
                    optional,
                }),
            }
        }
        Section::DepTable(i) => {
            if let Some(d) = m.deps.get_mut(*i) {
                if keys.len() == 1 && first == "package" {
                    if let Some(p) = plain_string(value) {
                        d.package = p;
                    }
                } else if keys.len() == 1 && first == "workspace" {
                    d.workspace |= value.trim_start().starts_with("true");
                } else if keys.len() == 1 && first == "optional" {
                    d.optional |= value.trim_start().starts_with("true");
                }
            }
        }
        Section::WorkspaceDeps => {
            let e = m
                .workspace_deps
                .entry(first.to_string())
                .or_insert_with(|| first.to_string());
            if let Some(p) = rename {
                *e = p;
            }
        }
        Section::WorkspaceDepTable(name) => {
            if keys.len() == 1
                && first == "package"
                && let Some(p) = plain_string(value)
            {
                m.workspace_deps.insert(name.clone(), p);
            }
        }
        Section::Other => {}
    }
}

/// Reads the package name, the dependencies and the workspace dependencies of a manifest. Unknown
/// constructs are skipped; nothing panics on malformed text.
pub(super) fn parse(text: &str) -> Manifest {
    let mut m = Manifest::default();
    let mut sec = Section::Other;
    let mut in_multiline_string = false;
    // A value that continues on the next lines: its keys, text so far, first line and depth.
    let mut pending: Option<(Vec<String>, String, usize, i64)> = None;
    for (n, raw) in text.lines().enumerate() {
        let line_no = n + 1;
        if in_multiline_string {
            if raw.matches("\"\"\"").count() % 2 == 1 || raw.matches("'''").count() % 2 == 1 {
                in_multiline_string = false;
            }
            continue;
        }
        if raw.matches("\"\"\"").count() % 2 == 1 || raw.matches("'''").count() % 2 == 1 {
            in_multiline_string = true;
            continue;
        }
        let line = strip_comment(raw).trim();
        if let Some((keys, mut value, first_line, depth)) = pending.take() {
            value.push(' ');
            value.push_str(line);
            let depth = depth + depth_change(line);
            if depth > 0 {
                pending = Some((keys, value, first_line, depth));
            } else {
                apply(&mut m, &sec, &keys, &value, first_line);
            }
            continue;
        }
        if line.is_empty() {
            continue;
        }
        if line.starts_with("[[") {
            sec = Section::Other;
            continue;
        }
        if let Some(inner) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
            sec = section(inner, line_no, &mut m);
            continue;
        }
        let Some((k, v)) = key_value(line) else {
            continue;
        };
        let keys = dotted(k);
        let depth = depth_change(v);
        if depth > 0 {
            pending = Some((keys, v.to_string(), line_no, depth));
        } else {
            apply(&mut m, &sec, &keys, v, line_no);
        }
    }
    if let Some((keys, value, first_line, _)) = pending {
        apply(&mut m, &sec, &keys, &value, first_line);
    }
    m
}

/// Applies the workspace renames `ws` (key → package) to the inherited dependencies of `m`.
pub(super) fn resolve(m: &mut Manifest, ws: &BTreeMap<String, String>) {
    for d in m
        .deps
        .iter_mut()
        .filter(|d| d.workspace && d.package == d.key)
    {
        if let Some(p) = ws.get(&d.key) {
            d.package = p.clone();
        }
    }
}

/// The `[workspace.dependencies]` of the root manifest below `root` (empty when it is missing).
pub(super) fn workspace_deps(root: &Path) -> BTreeMap<String, String> {
    let text = std::fs::read_to_string(root.join("Cargo.toml")).unwrap_or_default();
    parse(&text).workspace_deps
}

/// Reads the member manifest `rel` below `root` with the workspace renames applied.
pub(super) fn load_member(root: &Path, rel: &str, ws: &BTreeMap<String, String>) -> Manifest {
    let mut m = parse(&std::fs::read_to_string(root.join(rel)).unwrap_or_default());
    resolve(&mut m, ws);
    m
}

#[cfg(test)]
mod tests {
    use super::*;

    fn deps(m: &Manifest) -> Vec<(String, String, Kind)> {
        m.deps
            .iter()
            .map(|d| (d.key.clone(), d.package.clone(), d.kind))
            .collect()
    }

    // the layout rules: every way of declaring a dependency is seen, with its kind and rename.
    #[test]
    fn dependency_forms() {
        let text = r#"
[package]
name = "demo-crate" # the name
description = """
[dependencies]
not = "a dependency"
"""

[features]
x = ["dep:alpha", "beta/y"]

[dependencies]
alpha.workspace = true
alpha.features = ["f"]
beta = { workspace = true, optional = true }
gamma = { package = "real-gamma", version = "1" }
delta.package = "real-delta"
multi = { version = "1", features = [
    "a",
] }
"quoted" = "1"

[dev-dependencies]
epsilon = "1" # comment with = sign

[build-dependencies]
zeta = "1"

[target.'cfg(unix)'.dependencies]
eta = "1"

[dependencies.theta]
package = "real-theta"
version = "1"

[[bench]]
name = "not-a-dependency"
"#;
        let m = parse(text);
        assert_eq!(m.name.as_deref(), Some("demo-crate"));
        let s = |a: &str, b: &str, k| (a.to_string(), b.to_string(), k);
        assert_eq!(
            deps(&m),
            [
                s("alpha", "alpha", Kind::Normal),
                s("beta", "beta", Kind::Normal),
                s("gamma", "real-gamma", Kind::Normal),
                s("delta", "real-delta", Kind::Normal),
                s("multi", "multi", Kind::Normal),
                s("quoted", "quoted", Kind::Normal),
                s("epsilon", "epsilon", Kind::Dev),
                s("zeta", "zeta", Kind::Build),
                s("eta", "eta", Kind::Normal),
                s("theta", "real-theta", Kind::Normal),
            ]
        );
        assert_eq!(m.deps.first().map(|d| d.line), Some(13));
    }

    // the layout rules: a rename inside a value spread over several lines, and renames of the root
    // manifest's `[workspace.dependencies]`, reach the dependencies that use them.
    #[test]
    fn optional_dependencies() {
        let m = parse(
            "[dependencies]\na = { workspace = true, optional = true }\nb.workspace = true\nc.optional = true\n\n[dependencies.d]\nversion = \"1\"\noptional = true\n",
        );
        let opt: Vec<(&str, bool)> = m
            .deps
            .iter()
            .map(|d| (d.key.as_str(), d.optional))
            .collect();
        assert_eq!(opt, [("a", true), ("b", false), ("c", true), ("d", true)]);
    }

    // the layout rules: values over several lines and renames in the root manifest.
    #[test]
    fn multi_line_values_and_workspace_renames() {
        let root = r#"
[workspace]
members = ["crates/*/*"]

[workspace.dependencies]
plain = { path = "p", version = "0.1.0" }
engine = { path = "e", features = [
    "a",
], package = "real-engine" }

[workspace.dependencies.tabled]
package = "real-tabled"
path = "t"
"#;
        let ws = parse(root).workspace_deps;
        assert_eq!(ws.get("plain").map(String::as_str), Some("plain"));
        assert_eq!(ws.get("engine").map(String::as_str), Some("real-engine"));
        assert_eq!(ws.get("tabled").map(String::as_str), Some("real-tabled"));
        let member = r#"
[package]
name = "demo"

[dependencies]
foo = { version = "1", features = [
    "a",
], package = "real-foo" }
engine.workspace = true
tabled = { workspace = true, optional = true }
plain.workspace = true
own = { workspace = true }

[dev-dependencies.dev-engine]
workspace = true
"#;
        let mut m = parse(member);
        assert_eq!(m.deps.first().map(|d| d.line), Some(6));
        let mut ws = ws;
        ws.insert("dev-engine".into(), "real-engine".into());
        resolve(&mut m, &ws);
        let s = |a: &str, b: &str, k| (a.to_string(), b.to_string(), k);
        assert_eq!(
            deps(&m),
            [
                s("foo", "real-foo", Kind::Normal),
                s("engine", "real-engine", Kind::Normal),
                s("tabled", "real-tabled", Kind::Normal),
                s("plain", "plain", Kind::Normal),
                s("own", "own", Kind::Normal),
                s("dev-engine", "real-engine", Kind::Dev),
            ]
        );
    }

    // Malformed manifests never panic; unknown lines are skipped.
    #[test]
    fn tolerates_malformed_lines() {
        for text in [
            "[dependencies\nx = 1",
            "[dependencies]\n= \"x\"\nname",
            "[package]\nname = \"unterminated\n",
            "[dependencies]\nx = { package = ",
            "[dependencies]\nx = { features = [\n\"a\",\n",
            "[workspace.dependencies]\nx = { package = \"y\" }\n[workspace.dependencies.z]\npackage =",
        ] {
            let _ = parse(text);
        }
    }
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Dependency edges between the workspace crates (the layout rules, Rule 1.3) and of the shared
//! crates of the RustEDA tools.
//!
//! By group: `core` ← `formats` ← `engines` ← `gui`, `apps`; `verify` uses only `core` and
//! `formats` (other verification crates only as development dependencies); engines never use one
//! another; nothing depends on `apps`. Development dependencies of `core`, `formats` and `engines`
//! may also name verification crates (fixtures). Crates below `gui` and `apps` use no
//! user-interface crate (by name prefix, so that a toolkit's companion crates count too).
//!
//! The shared crates form their own layer below the tools ([`SHARED_DEPS`]): of the workspace
//! crates, `rusteda-core` uses none, `rusteda-workspace`, `rusteda-formats` and `rusteda-compute`
//! use only `rusteda-core`, and `rusteda-cli` uses only shared crates, in every kind of
//! dependency. GPU bindings ([`GPU_PREFIXES`]) are dependencies of `rusteda-compute` only.
//!
//! The workbench ([`STUDIO`]) uses only `rusteda-core` and `rusteda-workspace` of the workspace
//! crates (plan §2), and its user-interface crates are optional normal dependencies, turned on by
//! its `gui` feature, so that its headless model builds and is tested without them.

use super::Report;
use super::manifest::{self, Kind};
use std::collections::BTreeMap;
use std::path::Path;

/// Name prefixes of user-interface crates (a prefix matches the name itself and names continuing
/// with `-` or `_`): only `gui` and `apps` crates may use them.
const UI_PREFIXES: [&str; 9] = [
    "gpui",
    "egui",
    "eframe",
    "epaint",
    "ecolor",
    "emath",
    "winit",
    "ratatui",
    "crossterm",
];

/// Name prefixes of GPU binding crates: only [`COMPUTE`] may use them.
const GPU_PREFIXES: [&str; 5] = ["cudarc", "objc2", "cubecl", "wgpu", "metal"];

/// The shared crate that holds every GPU binding.
const COMPUTE: &str = "rusteda-compute";

/// The workspace crates each shared crate may depend on.
const SHARED_DEPS: [(&str, &[&str]); 5] = [
    ("rusteda-core", &[]),
    ("rusteda-workspace", &["rusteda-core"]),
    ("rusteda-compute", &["rusteda-core"]),
    ("rusteda-formats", &["rusteda-core"]),
    (
        "rusteda-cli",
        &[
            "rusteda-core",
            "rusteda-workspace",
            "rusteda-compute",
            "rusteda-formats",
        ],
    ),
];

/// The workbench and the workspace crates it may use (plan §2).
const STUDIO: (&str, &[&str]) = ("rusteda-studio", &["rusteda-core", "rusteda-workspace"]);

/// True when `name` is `prefix` or continues it with `-` or `_`.
fn has_prefix(name: &str, prefix: &str) -> bool {
    name.strip_prefix(prefix)
        .is_some_and(|rest| rest.is_empty() || rest.starts_with(['-', '_']))
}

fn is_ui(name: &str) -> bool {
    UI_PREFIXES.iter().any(|p| has_prefix(name, p))
}

fn is_gpu_binding(name: &str) -> bool {
    GPU_PREFIXES.iter().any(|p| has_prefix(name, p))
}

/// A workspace crate: its manifest path and group.
struct Member {
    manifest: String,
    group: String,
}

/// Groups a crate of `group` may depend on through a dependency of `kind`.
fn allowed_groups(group: &str, kind: Kind) -> &'static [&'static str] {
    let dev = kind == Kind::Dev;
    match group {
        "core" if dev => &["core", "formats", "verify"],
        "core" => &["core"],
        "formats" if dev => &["core", "formats", "verify"],
        "formats" => &["core", "formats"],
        "engines" if dev => &["core", "formats", "verify"],
        "engines" => &["core", "formats"],
        "verify" if dev => &["core", "formats", "verify"],
        "verify" => &["core", "formats"],
        "gui" | "apps" => &["core", "formats", "engines", "verify", "gui"],
        _ => &[],
    }
}

/// Checks every crate manifest in `files`; `[workspace.dependencies]` renames of the root manifest
/// apply to members that inherit a dependency. GPU bindings are looked for in every manifest.
pub(super) fn check(root: &Path, files: &[String], r: &mut Report) {
    let ws = manifest::workspace_deps(root);
    let mut members: BTreeMap<String, Member> = BTreeMap::new();
    let mut parsed = Vec::new();
    for f in files {
        let parts: Vec<&str> = f.split('/').collect();
        if f != "Cargo.toml" && !f.ends_with("/Cargo.toml") {
            continue;
        }
        let m = manifest::load_member(root, f, &ws);
        let group = match parts.as_slice() {
            ["crates", group, _, "Cargo.toml"] => group.to_string(),
            // Other manifests (the workspace root, `xtask`) are checked for GPU bindings only.
            _ => String::new(),
        };
        if !group.is_empty()
            && let Some(name) = &m.name
        {
            members.insert(
                name.clone(),
                Member {
                    manifest: f.clone(),
                    group: group.clone(),
                },
            );
        }
        parsed.push((f.clone(), group, m));
    }
    for (f, group, m) in &parsed {
        let me = m.name.as_deref().unwrap_or(f);
        let shared = SHARED_DEPS.iter().find(|(n, _)| *n == me);
        for d in &m.deps {
            let q = d.package.as_str();
            let at = format!("{f}:{} [{}]", d.line, d.kind.section());
            let mut deny = |why: &str| {
                r.errors
                    .push(format!("{at}: {me} must not depend on {q} ({why})"));
            };
            if me != COMPUTE && is_gpu_binding(q) {
                deny("GPU bindings are used only by rusteda-compute, RustEDA contract 1");
                continue;
            }
            if group.is_empty() {
                continue;
            }
            if group != "gui" && group != "apps" && is_ui(q) {
                deny("user-interface crates are used only by gui and apps crates, Rule 1.3");
                continue;
            }
            if me == STUDIO.0 && d.kind == Kind::Normal && is_ui(q) && !d.optional {
                deny(
                    "the workbench's user-interface crates are optional, so that its model builds \
                     without them",
                );
                continue;
            }
            let Some(t) = members.get(q) else {
                continue;
            };
            if t.group == "apps" {
                deny("nothing depends on an apps crate, the layout rules");
            } else if me == STUDIO.0 && !STUDIO.1.contains(&q) {
                deny(&format!(
                    "of the workspace crates, {me} uses only {}, plan §2",
                    STUDIO.1.join(", ")
                ));
            } else if let Some((_, ok)) = shared
                && !ok.contains(&q)
            {
                deny(&if ok.is_empty() {
                    format!("{me} uses no other workspace crate, RustEDA contract 1")
                } else {
                    format!(
                        "of the workspace crates, {me} uses only {}, RustEDA contract 1",
                        ok.join(", ")
                    )
                });
            } else if group == "engines" && t.group == "engines" {
                deny("engines never depend on another engine, the layout rules");
            } else if group == "verify" && t.group == "engines" {
                deny("verification crates never depend on an engine, Rule 8.2");
            } else if !allowed_groups(group, d.kind).contains(&t.group.as_str()) {
                deny(&format!(
                    "a {group} crate does not depend on the {} group ({}), the layout rules",
                    t.group, t.manifest
                ));
            }
        }
    }
    for f in files {
        if f.starts_with("crates/verify/")
            && f.ends_with("-check/src/lib.rs")
            && let Ok(text) = std::fs::read_to_string(root.join(f))
            && !text.contains("#![forbid(unsafe_code)]")
        {
            r.errors.push(format!(
                "{f}: checker crate must carry #![forbid(unsafe_code)], Rule 8.2"
            ));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{Tree, manifest};

    /// A tree with the crates of the workspace, the shared crates that come later, a second engine,
    /// a user-interface crate and the given extra dependency lines.
    fn tree(tag: &str, extra: &[(&str, &str, &str)]) -> Tree {
        let t = Tree::new(tag);
        let crates = [
            ("core/rusteda-core", "rusteda-core"),
            ("core/rusteda-workspace", "rusteda-workspace"),
            ("core/rusteda-compute", "rusteda-compute"),
            ("formats/rusteda-formats", "rusteda-formats"),
            ("engines/demo-engine", "demo-engine"),
            ("engines/demo", "demo-other"),
            ("verify/demo-check", "demo-check"),
            ("verify/demo-fixtures", "demo-fixtures"),
            ("gui/view", "demo-view"),
            ("apps/demo-cli", "demo-cli"),
            ("apps/rusteda-cli", "rusteda-cli"),
            ("apps/rusteda-studio", "rusteda-studio"),
        ];
        for (dir, name) in crates {
            let mut rest = String::new();
            for (who, section, line) in extra {
                if *who == name {
                    rest.push_str(&format!("[{section}]\n{line}\n\n"));
                }
            }
            t.file(&format!("crates/{dir}/Cargo.toml"), &manifest(name, &rest));
        }
        let mut rest = String::new();
        for (who, section, line) in extra {
            if *who == "xtask" {
                rest.push_str(&format!("[{section}]\n{line}\n\n"));
            }
        }
        t.file("xtask/Cargo.toml", &manifest("xtask", &rest));
        t
    }

    fn errors(t: &Tree) -> Vec<String> {
        let mut r = Report::default();
        check(t.root(), &t.files(), &mut r);
        r.errors
    }

    // the layout rules, 7.1, 9.2 and the RustEDA contract: the edges of the workspace pass, and those of
    // the shared crates that come later.
    #[test]
    fn allowed_edges() {
        let t = tree(
            "deps-ok",
            &[
                (
                    "rusteda-compute",
                    "dependencies",
                    "cudarc = { workspace = true, optional = true }\nobjc2-metal = { version = \"0.3\", optional = true }\nrusteda-core.workspace = true",
                ),
                (
                    "rusteda-formats",
                    "dependencies",
                    "rusteda-core.workspace = true\nthiserror.workspace = true",
                ),
                (
                    "rusteda-workspace",
                    "dependencies",
                    "rusteda-core.workspace = true\nmemmap2.workspace = true",
                ),
                (
                    "rusteda-cli",
                    "dependencies",
                    "rusteda-core.workspace = true\nrusteda-workspace.workspace = true\nrusteda-formats.workspace = true\nclap.workspace = true\ncrossterm.workspace = true",
                ),
                (
                    "demo-fixtures",
                    "dev-dependencies",
                    "rusteda-formats.workspace = true",
                ),
                (
                    "demo-engine",
                    "dependencies",
                    "rusteda-core.workspace = true\nrusteda-formats.workspace = true\nrusteda-compute = { workspace = true, optional = true }\nrayon.workspace = true",
                ),
                (
                    "demo-engine",
                    "dev-dependencies",
                    "demo-fixtures.workspace = true\nserde_json.workspace = true",
                ),
                (
                    "demo-check",
                    "dependencies",
                    "rusteda-core.workspace = true\nrusteda-formats.workspace = true\nserde_json = \"1\"",
                ),
                (
                    "demo-check",
                    "dev-dependencies",
                    "demo-fixtures.workspace = true",
                ),
                (
                    "demo-view",
                    "dependencies",
                    "rusteda-core.workspace = true\ndemo-engine.workspace = true\ndemo-check.workspace = true\negui = \"1\"",
                ),
                (
                    "demo-cli",
                    "dependencies",
                    "rusteda-core.workspace = true\nrusteda-formats.workspace = true\ndemo-engine.workspace = true\ndemo-check.workspace = true\ncrossterm.workspace = true",
                ),
                (
                    "demo-cli",
                    "dev-dependencies",
                    "demo-fixtures.workspace = true",
                ),
                ("xtask", "dependencies", "demo-fixtures.workspace = true"),
                (
                    "rusteda-studio",
                    "dependencies",
                    "rusteda-core.workspace = true\nrusteda-workspace.workspace = true\ngpui = { workspace = true, optional = true }",
                ),
                (
                    "rusteda-studio",
                    "dev-dependencies",
                    "gpui = { workspace = true, features = [\"test-support\"] }",
                ),
            ],
        );
        t.file(
            "Cargo.toml",
            "[workspace.dependencies]\ncudarc = { version = \"=0.19.10\" }\n",
        );
        assert_eq!(errors(&t), Vec::<String>::new());
    }

    // the layout rules, 7.1, 9.2 and the RustEDA contract: forbidden edges are reported with their
    // manifest line and section.
    #[test]
    fn forbidden_edges() {
        let cases: [(&str, &str, &str, &str); 33] = [
            (
                "demo-engine",
                "dependencies",
                "demo-other.workspace = true",
                "another engine",
            ),
            (
                "demo-other",
                "dev-dependencies",
                "demo-engine.workspace = true",
                "another engine",
            ),
            (
                "demo-engine",
                "dependencies",
                "demo-view.workspace = true",
                "crate does not depend on the gui group",
            ),
            (
                "demo-engine",
                "dependencies",
                "crossterm = \"0.29\"",
                "user-interface",
            ),
            (
                "demo-engine",
                "dependencies",
                "egui = \"1\"",
                "user-interface",
            ),
            // A companion crate of a toolkit counts by its name prefix.
            (
                "rusteda-core",
                "dependencies",
                "crossterm_winapi = \"0.9\"",
                "user-interface",
            ),
            (
                "rusteda-formats",
                "dependencies",
                "epaint = \"1\"",
                "user-interface",
            ),
            (
                "demo-check",
                "dependencies",
                "demo-view.workspace = true",
                "a verify crate does not depend on the gui group",
            ),
            (
                "demo-check",
                "dependencies",
                "demo-engine.workspace = true",
                "never depend on an engine",
            ),
            (
                "demo-check",
                "dev-dependencies",
                "demo-engine.workspace = true",
                "never depend on an engine",
            ),
            (
                "demo-fixtures",
                "dev-dependencies",
                "demo-engine.workspace = true",
                "never depend on an engine",
            ),
            (
                "demo-fixtures",
                "dependencies",
                "demo-check.workspace = true",
                "verify group",
            ),
            (
                "demo-engine",
                "dependencies",
                "demo-cli.workspace = true",
                "apps crate",
            ),
            (
                "demo-view",
                "dev-dependencies",
                "rusteda-cli.workspace = true",
                "apps crate",
            ),
            (
                "demo-cli",
                "dependencies",
                "rusteda-cli.workspace = true",
                "apps crate",
            ),
            // The shared crates: none uses a crate of the tool, and of the shared crates only those
            // below it.
            (
                "rusteda-formats",
                "dependencies",
                "demo-engine.workspace = true",
                "rusteda-formats uses only rusteda-core",
            ),
            (
                "rusteda-formats",
                "dev-dependencies",
                "demo-fixtures.workspace = true",
                "rusteda-formats uses only rusteda-core",
            ),
            (
                "rusteda-formats",
                "dependencies",
                "rusteda-compute.workspace = true",
                "rusteda-formats uses only rusteda-core",
            ),
            (
                "rusteda-compute",
                "dependencies",
                "rusteda-workspace.workspace = true",
                "rusteda-compute uses only rusteda-core",
            ),
            (
                "rusteda-workspace",
                "build-dependencies",
                "rusteda-formats.workspace = true",
                "rusteda-workspace uses only rusteda-core",
            ),
            (
                "rusteda-core",
                "dependencies",
                "rusteda-compute.workspace = true",
                "rusteda-core uses no other workspace crate",
            ),
            (
                "rusteda-cli",
                "dependencies",
                "demo-check.workspace = true",
                "rusteda-cli uses only rusteda-core, rusteda-workspace",
            ),
            // GPU bindings belong to rusteda-compute, also as companion crates and in the
            // manifests outside the crate groups.
            (
                "demo-engine",
                "dependencies",
                "cudarc = { workspace = true, optional = true }",
                "GPU bindings",
            ),
            (
                "demo-cli",
                "dependencies",
                "wgpu-core = \"25\"",
                "GPU bindings",
            ),
            (
                "rusteda-core",
                "dev-dependencies",
                "objc2-metal = \"0.3\"",
                "GPU bindings",
            ),
            (
                "demo-engine",
                "dependencies",
                "cubecl = { version = \"0.6\" }",
                "GPU bindings",
            ),
            (
                "demo-check",
                "dependencies",
                "metal = \"0.32\"",
                "GPU bindings",
            ),
            (
                "rusteda-workspace",
                "dependencies",
                "cudarc_sys = \"1\"",
                "GPU bindings",
            ),
            ("xtask", "dependencies", "cudarc = \"0.19\"", "GPU bindings"),
            // The workbench: the core and the workspace only, and optional user-interface
            // crates; the framework is a user-interface crate for every other crate.
            (
                "rusteda-studio",
                "dependencies",
                "rusteda-formats.workspace = true",
                "rusteda-studio uses only rusteda-core, rusteda-workspace",
            ),
            (
                "rusteda-studio",
                "dependencies",
                "gpui.workspace = true",
                "user-interface crates are optional",
            ),
            (
                "demo-engine",
                "dependencies",
                "gpui-component = \"0.7\"",
                "user-interface",
            ),
            (
                "rusteda-core",
                "dependencies",
                "gpui = \"0.2\"",
                "user-interface",
            ),
        ];
        for (who, section, line, why) in cases {
            let t = tree("deps-bad", &[(who, section, line)]);
            let e = errors(&t);
            assert_eq!(e.len(), 1, "{who} [{section}] {line}: {e:?}");
            assert!(e[0].contains(why), "{who} {line}: {}", e[0]);
            assert!(e[0].contains(&format!("[{section}]")), "{}", e[0]);
            assert!(
                e[0].contains(&format!("{who} must not depend on")),
                "{}",
                e[0]
            );
        }
    }

    // Rule 8.2: a renamed dependency is judged by its package, also when the rename is made in
    // a value over several lines or in the root manifest's workspace dependencies.
    #[test]
    fn renames_do_not_hide_edges() {
        for (root, line) in [
            ("", "engine = { package = \"demo-engine\", path = \"x\" }"),
            (
                "",
                "engine = { path = \"x\", features = [\n    \"a\",\n], package = \"demo-engine\" }",
            ),
            (
                "[workspace.dependencies]\nengine = { path = \"x\", package = \"demo-engine\" }\n",
                "engine.workspace = true",
            ),
            (
                "[workspace.dependencies]\ngpu = { version = \"=0.19.10\", package = \"cudarc\" }\n",
                "gpu.workspace = true",
            ),
        ] {
            let t = tree("deps-rename", &[("demo-check", "dependencies", line)]);
            if !root.is_empty() {
                t.file("Cargo.toml", root);
            }
            let e = errors(&t);
            assert_eq!(e.len(), 1, "{line}: {e:?}");
            assert!(
                e[0].contains("must not depend on demo-engine")
                    || e[0].contains("must not depend on cudarc"),
                "{}",
                e[0]
            );
        }
    }

    // Rule 8.2: checker crates must carry `#![forbid(unsafe_code)]`.
    #[test]
    fn checker_forbids_unsafe_code() {
        let ok = tree("deps-check-ok", &[]);
        ok.file(
            "crates/verify/demo-check/src/lib.rs",
            "#![forbid(unsafe_code)]\n",
        );
        assert!(errors(&ok).is_empty());

        let bad = tree("deps-check-bad", &[]);
        bad.file("crates/verify/demo-check/src/lib.rs", "// missing forbid\n");
        let e = errors(&bad);
        assert_eq!(e.len(), 1, "{e:?}");
        assert!(e[0].contains("#![forbid(unsafe_code)]"), "{}", e[0]);
    }
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The crates of the repository (the layout rules): the shared crates of the RustEDA tools
//! ([`crate::shared::CRATES`]) and the crates of the workbench ([`TOOL`]), each in its group and in
//! a directory named after its package.

use super::Report;
use super::manifest;
use std::path::Path;

/// The crates of this repository: group and package name.
pub(super) const TOOL: [(&str, &str); 2] = [("apps", "rusteda-studio"), ("apps", "rusteda-flow")];

/// Checks the package of every crate manifest `crates/<group>/<dir>/Cargo.toml` in `files`.
pub(super) fn check(root: &Path, files: &[String], r: &mut Report) {
    let known = || crate::shared::CRATES.iter().chain(TOOL.iter());
    for f in files {
        let parts: Vec<&str> = f.split('/').collect();
        let ["crates", group, dir, "Cargo.toml"] = parts.as_slice() else {
            continue;
        };
        let text = std::fs::read_to_string(root.join(f)).unwrap_or_default();
        let Some(name) = manifest::parse(&text).name else {
            r.errors.push(format!("{f}: no package name"));
            continue;
        };
        match known().find(|(_, n)| *n == name) {
            None => {
                let names: Vec<&str> = known().map(|(_, n)| *n).collect();
                r.errors.push(format!(
                    "{f}: {name} is neither a shared RustEDA crate nor a crate of this tool \
                     (crates: {})",
                    names.join(", ")
                ));
            }
            Some((g, _)) if g != group => {
                r.errors
                    .push(format!("{f}: {name} belongs in crates/{g}/{name}/"));
            }
            Some(_) if name != *dir => {
                r.errors
                    .push(format!("{f}: {name} belongs in crates/{group}/{name}/"));
            }
            Some(_) => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{Tree, manifest};

    fn errors(t: &Tree) -> Vec<String> {
        let mut r = Report::default();
        check(t.root(), &t.files(), &mut r);
        r.errors
    }

    // the layout rules: the shared crates, also those that come later, and the crates of this
    // repository pass in their groups.
    #[test]
    fn known_crates_pass() {
        let t = Tree::new("packages-ok");
        for (group, name) in crate::shared::CRATES.iter().chain(TOOL.iter()) {
            t.file(
                &format!("crates/{group}/{name}/Cargo.toml"),
                &manifest(name, ""),
            );
        }
        t.file("crates/core/rusteda-core/src/Cargo.toml", "not a manifest");
        assert_eq!(errors(&t), Vec::<String>::new());
    }

    // the layout rules: an unknown package, a crate in another group, a directory that is not
    // named after its package and a manifest without a package name are errors.
    #[test]
    fn unknown_or_misplaced_crates() {
        for (path, name, want) in [
            (
                "crates/engines/demo",
                "demo-other",
                "demo-other is neither a shared RustEDA crate nor a crate of this tool",
            ),
            (
                "crates/core/rusteda-extra",
                "rusteda-extra",
                "rusteda-extra is neither",
            ),
            (
                "crates/formats/rusteda-core",
                "rusteda-core",
                "rusteda-core belongs in crates/core/rusteda-core/",
            ),
            (
                "crates/gui/rusteda-studio",
                "rusteda-studio",
                "rusteda-studio belongs in crates/apps/rusteda-studio/",
            ),
            (
                "crates/apps/studio",
                "rusteda-studio",
                "rusteda-studio belongs in crates/apps/rusteda-studio/",
            ),
        ] {
            let t = Tree::new("packages-bad");
            t.file(&format!("{path}/Cargo.toml"), &manifest(name, ""));
            let e = errors(&t);
            assert_eq!(e.len(), 1, "{path}: {e:?}");
            assert!(
                e[0].starts_with(&format!("{path}/Cargo.toml: ")) && e[0].contains(want),
                "{}",
                e[0]
            );
        }
        let t = Tree::new("packages-unnamed");
        t.file("crates/apps/rusteda-studio/Cargo.toml", "[lib]\n");
        assert_eq!(
            errors(&t),
            ["crates/apps/rusteda-studio/Cargo.toml: no package name"]
        );
    }
}

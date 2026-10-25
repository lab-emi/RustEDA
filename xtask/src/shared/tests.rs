// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Tests of the shared files and their manifest (`cargo xtask shared`).

use super::*;
use crate::testutil::Tree;

fn args(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| s.to_string()).collect()
}

// SHA-256 test vectors: the empty message, `abc`, the two-block messages of 448 and 896 bits,
// lengths around the padding boundaries and a million times `a`.
#[test]
fn sha256_vectors() {
    let a = |n: usize| vec![b'a'; n];
    for (data, want) in [
        (
            Vec::new(),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
        (
            b"abc".to_vec(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        ),
        (
            b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq".to_vec(),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
        ),
        (
            b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu".to_vec(),
            "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1",
        ),
        (
            a(55),
            "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318",
        ),
        (
            a(56),
            "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a",
        ),
        (
            a(64),
            "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb",
        ),
        (
            a(119),
            "31eba51c313a5c08226adf18d4a359cfdfd8d2e816b13f4af952f7ea6584dcfb",
        ),
        (
            a(1_000_000),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0",
        ),
    ] {
        assert_eq!(sha256::hex(&data), want, "{} bytes", data.len());
    }
}

// The shared paths are those of the RustEDA contract; a path counts only below a shared directory
// or when it is one of the individual shared files.
#[test]
fn shared_paths() {
    assert_eq!(
        dirs(),
        [
            "crates/core/rusteda-core",
            "crates/core/rusteda-workspace",
            "crates/core/rusteda-compute",
            "crates/formats/rusteda-formats",
            "crates/apps/rusteda-cli",
            "docs/specs/core",
            "docs/specs/workspace",
            "docs/specs/compute",
            "docs/specs/verilog",
            "docs/specs/liberty",
            "docs/specs/sdc",
            "docs/specs/rusteda-cli",
            "docs/specs/blif",
            "docs/specs/aiger",
        ]
    );
    assert!(is_shared("docs/specs/blif/README.md"));
    assert!(is_shared("docs/specs/aiger/README.md"));
    assert!(is_shared("crates/core/rusteda-core/src/lib.rs"));
    assert!(is_shared("docs/specs/liberty/liberty.md"));
    for f in FILES {
        assert!(is_shared(f), "{f}");
    }
    assert!(!is_shared("crates/core/rusteda-core"));
    assert!(!is_shared("crates/core/rusteda-core-extra/src/lib.rs"));
    assert!(!is_shared("crates/engines/some-tool/src/lib.rs"));
    assert!(!is_shared("docs/specs/some-tool/README.md"));
    assert!(!is_shared("docs/specs/cli/README.md"));
    assert!(!is_shared("xtask/src/main.rs"));
}

/// A tree with shared files of two crates, one specification, one shared automation file, and
/// files that are not shared.
fn tree(tag: &str) -> Tree {
    let t = Tree::new(tag);
    t.file("crates/core/rusteda-core/src/lib.rs", "core\n")
        .file("crates/core/rusteda-core/Cargo.toml", "[package]\n")
        .file("crates/formats/rusteda-formats/src/sdc.rs", "sdc\n")
        .file("docs/specs/core/README.md", "# Core\n")
        .file("verification/pdks.toml", "# PDKs\n")
        .file("crates/engines/some-tool/src/lib.rs", "engine\n")
        .file("docs/specs/some-tool/README.md", "# Tool\n")
        .file("README.md", "readme\n");
    t
}

fn read(t: &Tree, rel: &str) -> String {
    std::fs::read_to_string(t.root().join(rel)).unwrap_or_default()
}

// `update` lists every shared file, sorted by path, with its SHA-256 in the `sha256sum` format;
// absent shared paths are skipped and other files are not listed; `check` accepts the result.
#[test]
fn update_then_check() {
    let t = tree("shared-update");
    assert_eq!(update(t.root()), Ok(5));
    let h = |s: &str| sha256::hex(s.as_bytes());
    assert_eq!(
        read(&t, MANIFEST),
        format!(
            "{}  crates/core/rusteda-core/Cargo.toml\n{}  crates/core/rusteda-core/src/lib.rs\n\
             {}  crates/formats/rusteda-formats/src/sdc.rs\n{}  docs/specs/core/README.md\n\
             {}  verification/pdks.toml\n",
            h("[package]\n"),
            h("core\n"),
            h("sdc\n"),
            h("# Core\n"),
            h("# PDKs\n")
        )
    );
    assert_eq!(check(t.root()), Ok(5));
    assert!(run(t.root(), &args(&["check"])).is_ok());
    // A change outside the shared paths does not matter.
    t.file("crates/engines/some-tool/src/lib.rs", "engine 2\n")
        .file("docs/specs/coreish/README.md", "x\n");
    assert_eq!(check(t.root()), Ok(5));
}

// `check` names every shared file that changed, is not listed or is listed but gone, and points
// to `update`.
#[test]
fn check_reports_each_difference() {
    let t = tree("shared-check");
    assert_eq!(update(t.root()), Ok(5));
    t.file("crates/core/rusteda-core/src/lib.rs", "core 2\n")
        .file("docs/specs/liberty/liberty.md", "# Liberty\n");
    std::fs::remove_file(t.root().join("crates/formats/rusteda-formats/src/sdc.rs"))
        .unwrap_or_else(|e| panic!("{e}"));
    let e = check(t.root()).unwrap_err();
    let lines: Vec<&str> = e.lines().collect();
    assert_eq!(lines.len(), 4, "{e}");
    assert!(
        lines[0].starts_with("crates/core/rusteda-core/src/lib.rs: SHA-256 ")
            && lines[0].contains(&format!("but {MANIFEST} lists {}", sha256::hex(b"core\n"))),
        "{e}"
    );
    assert_eq!(
        lines[1],
        format!("docs/specs/liberty/liberty.md: shared file not listed in {MANIFEST}")
    );
    assert_eq!(
        lines[2],
        format!(
            "crates/formats/rusteda-formats/src/sdc.rs: listed in {MANIFEST} but not a shared \
             file of the working tree"
        )
    );
    assert!(lines[3].contains("cargo xtask shared update"), "{e}");
    assert_eq!(update(t.root()), Ok(5));
    assert_eq!(check(t.root()), Ok(5));
}

// `check` fails on a missing manifest, on a manifest in another order or with CR LF line endings,
// and on lines that are not `<sha256>  <path>`.
#[test]
fn check_wants_the_canonical_form() {
    let t = tree("shared-form");
    let e = check(t.root()).unwrap_err();
    assert!(
        e.starts_with(MANIFEST) && e.contains("shared update"),
        "{e}"
    );
    assert_eq!(update(t.root()), Ok(5));
    let canonical = read(&t, MANIFEST);
    let mut reversed: Vec<&str> = canonical.lines().collect();
    reversed.reverse();
    for text in [
        reversed.join("\n") + "\n",
        canonical.replace('\n', "\r\n"),
        canonical.trim_end().to_string(),
    ] {
        t.file(MANIFEST, &text);
        let e = check(t.root()).unwrap_err();
        assert!(e.contains("not in its canonical form"), "{e}");
    }
    t.file(MANIFEST, &format!("{canonical}junk\n{}\n", "0".repeat(64)));
    let e = check(t.root()).unwrap_err();
    assert!(
        e.contains(&format!("{MANIFEST}:6: not a `<sha256>  <path>` line"))
            && e.contains(&format!("{MANIFEST}:7: not a `<sha256>  <path>` line")),
        "{e}"
    );
    t.file(MANIFEST, &canonical.replacen("  ", " ", 1));
    assert!(check(t.root()).unwrap_err().contains(":1: not a"));
    t.file(MANIFEST, &canonical);
    assert_eq!(check(t.root()), Ok(5));
}

// In a git work tree, a file below a shared path that git ignores (an editor backup) is no shared
// file; without git, every file below the shared paths is one.
#[test]
fn ignored_files_do_not_count() {
    let t = tree("shared-git");
    let init = Command::new("git")
        .args(["init", "-q"])
        .current_dir(t.root())
        .output()
        .is_ok_and(|o| o.status.success());
    assert!(init, "git init in {}", t.root().display());
    t.file(".gitignore", "*.orig\n")
        .file("crates/core/rusteda-core/src/lib.rs.orig", "backup\n");
    assert_eq!(update(t.root()), Ok(5));
    assert!(!read(&t, MANIFEST).contains(".orig"));
    assert_eq!(check(t.root()), Ok(5));
    std::fs::remove_dir_all(t.root().join(".git")).unwrap_or_else(|e| panic!("{e}"));
    let e = check(t.root()).unwrap_err();
    assert!(
        e.starts_with("crates/core/rusteda-core/src/lib.rs.orig: shared file not listed"),
        "{e}"
    );
}

// A link to a directory below a shared path is not followed.
#[cfg(unix)]
#[test]
fn links_to_directories_are_not_followed() {
    let t = tree("shared-link");
    let elsewhere = Tree::new("shared-link-target");
    elsewhere.file("x.rs", "x\n");
    std::os::unix::fs::symlink(
        elsewhere.root(),
        t.root().join("crates/core/rusteda-core/linked"),
    )
    .unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(update(t.root()), Ok(5));
    assert!(!read(&t, MANIFEST).contains("linked"));
}

// `diff` lists, in path order, the shared files whose contents differ and those that only one
// repository has; files outside the shared paths are not compared.
#[test]
fn diff_lists_differences() {
    let a = tree("shared-diff-a");
    let b = tree("shared-diff-b");
    assert_eq!(diff(a.root(), b.root()), Ok((Vec::new(), 5)));
    assert!(run(a.root(), &args(&["diff", &b.root().to_string_lossy()])).is_ok());
    b.file("crates/core/rusteda-core/src/lib.rs", "core 2\n")
        .file("crates/core/rusteda-workspace/src/lib.rs", "workspace\n")
        .file("crates/engines/some-tool/src/lib.rs", "engine 2\n");
    a.file("docs/specs/compute/README.md", "# Compute\n");
    assert_eq!(
        diff(a.root(), b.root()),
        Ok((
            vec![
                "differs: crates/core/rusteda-core/src/lib.rs".to_string(),
                "only there: crates/core/rusteda-workspace/src/lib.rs".to_string(),
                "only here: docs/specs/compute/README.md".to_string(),
            ],
            7
        ))
    );
    let e = run(a.root(), &args(&["diff", &b.root().to_string_lossy()])).unwrap_err();
    assert_eq!(
        e,
        "3 of 7 shared files differ or exist in one repository only"
    );
}

// The command takes exactly one of `check`, `update` and `diff OTHER`; `diff` needs a directory.
#[test]
fn command_arguments() {
    let t = tree("shared-args");
    let wrong: [&[&str]; 5] = [
        &[],
        &["verify"],
        &["check", "extra"],
        &["diff"],
        &["update", "x"],
    ];
    for a in wrong {
        let e = run(t.root(), &args(a)).unwrap_err();
        assert!(e.starts_with("usage: cargo xtask shared"), "{a:?}: {e}");
    }
    let missing = t.root().join("no-such-repository");
    let e = run(t.root(), &args(&["diff", &missing.to_string_lossy()])).unwrap_err();
    assert!(e.ends_with("not a directory"), "{e}");
}

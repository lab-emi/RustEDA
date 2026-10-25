"""Black-box safety checks using real, disposable Git repositories."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]


def git(where, *args):
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args], cwd=where,
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()


def fixture(base, names=("One", "Two"), shared=False):
    root = base / "root"
    sources = base / "sources"
    root.mkdir(parents=True)
    sources.mkdir()
    shutil.copyfile(PROJECT / "dev", root / "dev")
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "RustEDA fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    for name in names:
        source = sources / name
        source.mkdir()
        git(source, "init", "--initial-branch=main")
        git(source, "config", "user.name", "RustEDA fixture")
        git(source, "config", "user.email", "fixture@example.invalid")
        (source / "README").write_text("original\n")
        if shared:
            (source / "verification").mkdir()
            sha = hashlib.sha256(b"original\n").hexdigest()
            (source / "verification/shared.sha256").write_text(f"{sha}  README\n")
        git(source, "add", ".")
        git(source, "commit", "-m", "fixture")
        git(root, "submodule", "add", str(source), f"modules/{name}")
        git(root / "modules" / name, "config", "user.name", "RustEDA fixture")
        git(root / "modules" / name, "config", "user.email", "fixture@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture root")
    return root, sources


class DevSafety(unittest.TestCase):
    def setUp(self):
        directory = PROJECT / ".work/test-fixtures"
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="git-", dir=directory)
        self.base = Path(self.temp.name)
        self.root, self.sources = fixture(self.base / "default")

    def tearDown(self):
        self.temp.cleanup()

    def dev(self, *args, root=None):
        root = root or self.root
        result = subprocess.run(
            [sys.executable, str(root / "dev"), *args, "--json"], cwd=root,
            capture_output=True, text=True, timeout=15,
        )
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_status_matches_real_gitlinks(self):
        code, value = self.dev("status")
        self.assertEqual(code, 0)
        self.assertTrue(value["ok"])
        self.assertEqual(len(value["modules"]), 2)
        self.assertTrue(all(item["head"] == item["expected"] for item in value["modules"]))

    def test_dirty_checkout_is_not_overwritten(self):
        path = self.root / "modules/One/README"
        path.write_text("uncommitted user work\n")
        code, value = self.dev("init")
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "LOCAL_CHANGES")
        self.assertEqual(path.read_text(), "uncommitted user work\n")
        self.assertEqual((self.sources / "One/README").read_text(), "original\n")

    def test_new_commit_is_not_reset(self):
        module = self.root / "modules/One"
        (module / "README").write_text("new commit\n")
        git(module, "add", "README")
        git(module, "commit", "-m", "user commit")
        before = git(module, "rev-parse", "HEAD")
        code, value = self.dev("init")
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "LOCAL_CHANGES")
        self.assertEqual(git(module, "rev-parse", "HEAD"), before)

    def test_all_modules_are_checked_before_initializing_any(self):
        git(self.root, "submodule", "deinit", "--force", "--", "modules/One")
        (self.root / "modules/Two/README").write_text("keep this\n")
        code, value = self.dev("init", "--from-local", str(self.sources))
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "LOCAL_CHANGES")
        self.assertFalse((self.root / "modules/One/.git").exists())

    def test_missing_local_revision_is_rejected_without_writes(self):
        git(self.root, "submodule", "deinit", "--force", "--", "modules/One")
        missing = self.base / "empty-sources"
        missing.mkdir()
        code, value = self.dev("init", "--from-local", str(missing))
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "MISSING_LOCAL_REVISION")
        self.assertFalse((self.root / "modules/One/.git").exists())

    def test_fresh_clone_initializes_from_recorded_local_sources(self):
        clone = self.base / "clone"
        git(self.base, "clone", "--no-hardlinks", str(self.root), str(clone))
        code, value = self.dev("init", "--from-local", str(self.sources), root=clone)
        self.assertEqual(code, 0, value)
        self.assertEqual(value["initialized"], ["One", "Two"])
        self.assertTrue(all(item["state"] == "pinned" for item in value["modules"]))

    def test_symlink_module_is_rejected(self):
        git(self.root, "submodule", "deinit", "--force", "--", "modules/One")
        destination = self.root / "modules/One"
        destination.rmdir()
        destination.symlink_to(self.sources / "One", target_is_directory=True)
        code, value = self.dev("status")
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "UNSAFE_PATH")
        self.assertEqual((self.sources / "One/README").read_text(), "original\n")

    def test_gitmodules_cannot_escape_module_directory(self):
        git(self.root, "config", "--file", ".gitmodules",
            "submodule.modules/One.path", "../outside")
        code, value = self.dev("init")
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "UNSAFE_PATH")

    def test_hash_mismatch_is_not_reported_as_pass(self):
        root, _ = fixture(self.base / "shared",
                          ("RustSys", "RustSTA", "RustEDA-Studio"), shared=True)
        code, value = self.dev("shared", root=root)
        self.assertEqual(code, 0, value)
        (root / "modules/RustSTA/README").write_text("changed\n")
        code, value = self.dev("shared", root=root)
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "SHARED_HASH_MISMATCH")

    def foundation_fixture(self):
        names = ("RustEDA-Common", "RustSys", "RustSTA", "RustEDA-Studio")
        root, _ = fixture(self.base / "foundation", names, shared=True)
        revision = git(root / "modules/RustEDA-Common", "rev-parse", "HEAD")
        url = "https://github.com/lab-emi/RustEDA-Common.git"
        for name in names[1:]:
            path = root / "modules" / name
            declarations = ["[workspace.dependencies]"]
            for package in ("rusteda-core", "rusteda-workspace", "rusteda-compute", "rusteda-formats"):
                declarations.append(f'{package} = {{ git = "{url}", rev = "{revision}" }}')
            declarations.append(
                f'rusteda-cli-shared = {{ package = "rusteda-cli-common", git = "{url}", rev = "{revision}" }}')
            (path / "Cargo.toml").write_text("\n".join(declarations) + "\n")
            (path / "Cargo.lock").write_text(
                f'[[package]]\nname = "rusteda-core"\nversion = "0.1.0"\n'
                f'source = "git+{url}?rev={revision}#{revision}"\n')
        return root

    def test_canonical_revision_is_checked_in_manifests(self):
        root = self.foundation_fixture()
        code, value = self.dev("shared", root=root)
        self.assertEqual(code, 0, value)
        manifest = root / "modules/RustSTA/Cargo.toml"
        text = manifest.read_text()
        revision = value["foundation"]["revision"]
        manifest.write_text(text.replace(revision, "a" * 40))
        code, value = self.dev("shared", root=root)
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "FOUNDATION_PIN_MISMATCH")

    def test_copied_foundation_cannot_silently_return(self):
        root = self.foundation_fixture()
        path = root / "modules/RustSys/crates/core/rusteda-core"
        path.mkdir(parents=True)
        (path / "Cargo.toml").write_text('[package]\nname = "rusteda-core"\n')
        code, value = self.dev("shared", root=root)
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "DUPLICATE_FOUNDATION")

    def test_lockfile_must_resolve_the_canonical_revision(self):
        root = self.foundation_fixture()
        path = root / "modules/RustEDA-Studio/Cargo.lock"
        path.write_text('[[package]]\nname = "rusteda-core"\nversion = "0.1.0"\n')
        code, value = self.dev("shared", root=root)
        self.assertEqual(code, 1)
        self.assertEqual(value["code"], "FOUNDATION_LOCK_MISMATCH")


if __name__ == "__main__":
    unittest.main()

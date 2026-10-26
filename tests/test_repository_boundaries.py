"""Exercise the repository boundary guard on disposable tracked Git trees."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import check_repository_boundaries as boundaries


class RepositoryBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True, timeout=10)

    def tearDown(self):
        self.temp.cleanup()

    def file(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        subprocess.run(["git", "-C", str(self.root), "add", name], check=True, timeout=10)

    def test_remote_dependencies_are_rejected_in_manifests_and_workflows(self):
        for repo in ("Rust" + "Lane", "RustEDA" + "-Studio"):
            for url in (f"https://github.com/lab-emi/{repo}.git",
                        f"git@github.com:lab-emi/{repo}.git"):
                with self.subTest(url=url):
                    self.file("Cargo.toml", f'dep = {{ git = "{url}" }}\n')
                    self.file(".github/workflows/check.yml", f"uses: lab-emi/{repo}@main\n")
                    result = boundaries.audit(self.root)
                    self.assertFalse(result["ok"])
                    self.assertEqual({r["path"] for r in result["active_dependencies"]},
                                     {"Cargo.toml", ".github/workflows/check.yml"})

    def test_obsolete_checkout_and_binary_paths_are_rejected(self):
        repo = "Rust" + "Lane"
        self.file("dev", f"cd modules/{repo}\n.work/target/{repo}/release/driver\n")
        result = boundaries.audit(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual({r["rule"] for r in result["active_dependencies"]},
                         {"retired_checkout", "retired_build_directory"})

    def test_local_crates_and_compatibility_schemas_are_not_remote_dependencies(self):
        self.file("Cargo.toml", 'studio = { path = "apps/crates/apps/rusteda-studio" }\n')
        self.file("worker.json", '{"schema":"rustlane.worker.v1"}\n')
        self.assertTrue(boundaries.audit(self.root)["ok"])

    def test_history_is_exempt_only_when_its_bytes_are_unchanged(self):
        name = "evidence.json"
        original = f'https://github.com/lab-emi/{"Rust" + "Lane"}/actions/runs/1\n'
        self.file(name, original)
        with mock.patch.dict(boundaries.HISTORICAL_SHA256,
                             {name: hashlib.sha256(original.encode()).hexdigest()}, clear=True):
            result = boundaries.audit(self.root)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["preserved_historical_references"]), 1)
            self.file(name, original + "changed\n")
            self.assertFalse(boundaries.audit(self.root)["ok"])

    def test_budget_exhaustion_is_an_error(self):
        self.file("one.md", "content\n")
        with mock.patch.object(boundaries, "MAX_FILES", 0):
            with self.assertRaisesRegex(ValueError, "FILE_BUDGET"):
                boundaries.audit(self.root)


if __name__ == "__main__":
    unittest.main()

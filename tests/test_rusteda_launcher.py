"""One-entrypoint selection controls; no EDA analysis is executed.

Three tiny executable/config files, no graphs; <32 MiB expected RSS.
"""
import importlib.machinery
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from integration.toolchain import digest


ROOT = Path(__file__).resolve().parents[1]


class Executed(Exception):
    pass


class RustedaLauncher(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".work/test-fixtures"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temp.name)
        loader = importlib.machinery.SourceFileLoader("rusteda_launcher", str(ROOT / "rusteda"))
        self.module = types.ModuleType(loader.name)
        self.module.__file__ = str(ROOT / "rusteda")
        loader.exec_module(self.module)
        self.module.ROOT = self.root
        self.built = self.root / ".work/target/RustEDA/debug/rusteda"
        self.built.parent.mkdir(parents=True)
        self.built.write_text("old local build\n")
        self.built.chmod(0o755)
        self.configured = self.root / "qualified-rusteda"
        self.configured.write_text("qualified application\n")
        self.configured.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def configure(self):
        path = self.root / ".work/integration/toolchain.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(schema="rusteda.toolchain.v1", status="ready", programs={
            "flow_driver": dict(path=str(self.configured), sha256=digest(self.configured))})))

    def test_workspace_commands_use_the_same_qualified_application_as_flows(self):
        self.configure()
        with mock.patch.object(self.module.sys, "argv", ["rusteda", "export", "--help"]), \
                mock.patch.object(self.module.os, "execv", side_effect=Executed) as execute:
            with self.assertRaises(Executed):
                self.module.main()
        execute.assert_called_once_with(str(self.configured), [str(self.configured), "export", "--help"])

    def test_changed_qualified_binary_does_not_silently_select_an_old_build(self):
        self.configure()
        self.configured.write_text("changed application")
        with mock.patch.object(self.module.sys, "argv", ["rusteda", "show"]), \
                mock.patch.object(self.module.os, "execv") as execute, \
                mock.patch.object(self.module.sys, "stderr", io.StringIO()):
            self.assertEqual(self.module.main(), 1)
        execute.assert_not_called()

    def test_an_unconfigured_checkout_can_use_its_local_build(self):
        with mock.patch.object(self.module.sys, "argv", ["rusteda", "show"]), \
                mock.patch.object(self.module.os, "execv", side_effect=Executed) as execute:
            with self.assertRaises(Executed):
                self.module.main()
        execute.assert_called_once_with(self.built, [str(self.built), "show"])


if __name__ == "__main__":
    unittest.main()

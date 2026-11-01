"""Native-worker protocol controls; these never establish EDA correctness.

At most six tiny files and one child command per case; <64 MiB expected RSS,
one worker. The caller runs this suite with an OS memory and time guard.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from integration.ibex_sg13g2_ez130 import producer, signoff_drc


ROOT = Path(__file__).resolve().parents[1]


class IhpWorkerProtocol(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".work/test-fixtures"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temp.name)
        lock = ROOT / ".work/integration/toolchain.json"
        if not lock.is_file():
            self.skipTest("pinned GNU time unavailable; worker execution unverified")
        self.timer = Path(json.loads(lock.read_text())["programs"]["gnu_time"]["path"])
        if not self.timer.is_file():
            self.skipTest("pinned GNU time unavailable; worker execution unverified")
        self.input = self.root / "input.txt"
        self.input.write_text("immutable source\n")
        self.script = Path(producer.__file__).resolve()
        self.manifest = self.root / "manifest.json"
        self.output = self.root / "output"
        self.owner = self.root / "ownership"
        self.data = dict(
            schema="rusteda.ihp-flow.v1", name="protocol_control", pdk="ihp-sg13g2",
            library="ez130_8t", corners=["tt", "ss"],
            files={"source": producer.identity(self.input)},
            tools={"python": producer.identity(Path(sys.executable)),
                   "rustroad": producer.identity(Path(sys.executable)),
                   "gnu_time": producer.identity(self.timer)},
            scope=dict(threads=1, cpu_affinity=[min(os.sched_getaffinity(0))], max_log_bytes=1024),
            steps=[dict(id="external_command", kind="command", tool="python", timeout_s=10,
                        arguments=["-c", "from pathlib import Path; Path('payload').write_text('observed child output')"]),
                   dict(id="render_identity", kind="render", destination="control.json",
                        content={"status": "synthetic_protocol_only", "payload": {"$identity": "payload"},
                                 "source_sha256": {"$sha256": "payload"}})],
            gates={name: "control.json" for name in producer.GATES}, artifacts={"payload": "payload"})

    def tearDown(self):
        self.temp.cleanup()

    def run_worker(self, *extra):
        self.manifest.write_text(json.dumps(self.data))
        command = [sys.executable, str(self.script), "--project", str(self.root), "--manifest", str(self.manifest),
                   "--workspace", str(self.output), "--base-rev", "empty", "--pdk", "ihp", "--rustroad",
                   sys.executable, "--gnu-time", str(self.timer)]
        command.extend(extra)
        return subprocess.run(command, env=dict(os.environ, RUSTEDA_WORKER_ID="a" * 64,
                                                RUSTEDA_OWNERSHIP=str(self.owner)),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)

    def test_command_and_render_are_observed_but_not_eda_qualified(self):
        result = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["status"], "completed")
        self.assertNotEqual(summary["status"], "passed")
        self.assertEqual((self.output / "payload").read_text(), "observed child output")
        rendered = json.loads((self.output / "control.json").read_text())
        self.assertEqual(rendered["payload"], producer.identity(self.output / "payload"))
        self.assertEqual(rendered["source_sha256"], rendered["payload"]["sha256"])
        stale = self.run_worker()
        self.assertEqual(stale.returncode, 2)
        self.assertIn("REVISION_CONFLICT", stale.stdout)

    def test_changed_pinned_input_is_rejected_before_execution(self):
        self.input.write_text("changed source")
        result = self.run_worker()
        self.assertEqual(result.returncode, 2)
        self.assertIn("identity changed", result.stdout)
        self.assertFalse(self.owner.exists())

    def test_prefix_completion_never_claims_full_flow_qualification(self):
        self.data["gates"] = {}
        self.data["artifacts"]["future_gds"] = "not_generated.gds"
        result = self.run_worker("--stop-after", "render_identity")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["status"], "frontend_completed")
        self.assertEqual(summary["schema"], "rusteda.ihp-frontend.v1")
        self.assertIn("complete_flow", summary["unverified_items"])
        self.assertEqual(summary["unavailable_artifacts"], ["future_gds"])
        self.assertNotIn("gates", summary)

    def test_invalid_prefix_is_rejected_before_ownership_creation(self):
        result = self.run_worker("--stop-after", "unknown_step")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.owner.exists())

    def test_output_escape_is_rejected(self):
        self.data["steps"][1]["destination"] = "../escaped.json"
        result = self.run_worker()
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / "escaped.json").exists())
        self.assertEqual(json.loads((self.output / "summary.json").read_text())["status"], "failed")

    def test_log_budget_is_enforced_after_fast_child_exit(self):
        self.data["steps"][0]["arguments"] = ["-c", "print('x' * 2048)"]
        result = self.run_worker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("log byte budget", (self.output / "summary.json").read_text())

    def test_command_failure_cannot_complete_the_producer(self):
        self.data["steps"][0]["arguments"] = ["-c", "raise SystemExit(7)"]
        result = self.run_worker()
        self.assertEqual(result.returncode, 1)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["status"], "failed")
        self.assertIn("exit=7", summary["error"])
        self.assertEqual(summary["unverified_items"], ["complete_flow"])

    def test_input_mutation_during_a_run_invalidates_completion(self):
        self.data["steps"][0]["arguments"] = ["-c", "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('modified')", "{files:source}"]
        self.data["steps"] = self.data["steps"][:1]
        result = self.run_worker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("input changed during execution", (self.output / "summary.json").read_text())

    def test_foundry_report_count_requires_a_real_report_root(self):
        path = self.root / "report.xml"
        path.write_text("<not-a-report/>")
        with self.assertRaisesRegex(ValueError, "report root"):
            signoff_drc.findings(path)
        path.write_text("<report-database><items><item><category>'M1.b'</category><cell>top</cell><values><value>edge</value></values></item></items></report-database>")
        count, categories, _ = signoff_drc.findings(path)
        self.assertEqual(count, 1)
        self.assertEqual(categories, {"'M1.b'": 1})


if __name__ == "__main__":
    unittest.main()

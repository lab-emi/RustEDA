"""Ownership and protocol tests. No EDA correctness is inferred from these fixtures."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from integration import rusteda_flow, toolchain, worker_control

ROOT = Path(__file__).resolve().parents[1]


class WorkerSafety(unittest.TestCase):
    def setUp(self):
        parent=ROOT/".work/test-fixtures";parent.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=parent);self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self):
        attempt=self.root/"workspace/steps/rtl2gds/attempt1";attempt.mkdir(parents=True)
        identity="a"*64
        value=dict(schema="rustlane.worker.v1",request_id=identity,unit="rusteda-worker-"+identity[:24],
                   workspace=str(self.root/"workspace"),cwd=str(attempt),timeout_seconds=30,memory_mib=8192,
                   max_log_bytes=4096,podman="podman")
        path=attempt/"request.json";toolchain.write_json(path,value)
        return path,value

    def test_request_hash_and_owned_unit_are_required(self):
        path,value=self.request();self.assertEqual(worker_control.request(path,toolchain.digest(path)),value)
        with self.assertRaises(ValueError):worker_control.request(path,"b"*64)
        value["unit"]="unrelated.service";toolchain.write_json(path,value)
        with self.assertRaises(ValueError):worker_control.request(path,toolchain.digest(path))

    def test_unknown_unit_ownership_never_stops_it(self):
        _,r=self.request();calls=[]
        def command(args,timeout=10):
            calls.append(args)
            if "show" in args:
                return subprocess.CompletedProcess(args,0,"LoadState=loaded\nActiveState=active\nDescription=unrelated\n","")
            return subprocess.CompletedProcess(args,0,"","")
        with mock.patch.object(worker_control,"command",command):result=worker_control.cleanup(r)
        self.assertFalse(result["verified"])
        self.assertFalse(any("stop" in c for c in calls))
        self.assertTrue(all(r["unit"]+".scope" in c for c in calls if c[0]=="systemctl"))

    def test_cleanup_requires_matching_container_label(self):
        _,r=self.request();calls=[]
        def command(args,timeout=10):
            calls.append(args)
            if "show" in args:return subprocess.CompletedProcess(args,0,"LoadState=not-found\nActiveState=inactive\n","")
            if "is-active" in args:return subprocess.CompletedProcess(args,3,"inactive\n","")
            if "ps" in args:return subprocess.CompletedProcess(args,0,"0123456789ab\n","")
            if "inspect" in args:return subprocess.CompletedProcess(args,0,"other-request\n","")
            raise AssertionError(args)
        with mock.patch.object(worker_control,"command",command):result=worker_control.cleanup(r)
        self.assertFalse(result["verified"])
        self.assertFalse(any("rm" in c for c in calls))

    def test_input_and_tool_mismatches_are_not_ready(self):
        tool=self.root/"tool";tool.write_text("original")
        manifest=self.root/"manifest.json";manifest.write_text("{}")
        record=dict(path=str(manifest),sha256=toolchain.digest(manifest))
        lock=dict(schema=toolchain.SCHEMA,status="ready",programs={"tool":dict(path=str(tool),sha256=toolchain.digest(tool))},
                  tool_manifest=record,signoff_manifest=record)
        tool.chmod(0o755);self.assertEqual(toolchain.verify(lock),lock)
        tool.write_text("changed")
        with self.assertRaises(ValueError):toolchain.verify(lock)
        lock["status"]="unverified"
        with self.assertRaises(ValueError):toolchain.verify(lock)

    def test_active_memory_lock_is_not_bypassed(self):
        with worker_control.scale_lock(self.root):
            with self.assertRaises(ValueError):
                with worker_control.scale_lock(self.root):pass

    def test_revision_conflicts_keep_the_structured_error_code(self):
        reply=json.dumps(dict(ok=False,error=dict(code="REVISION_CONFLICT",message="stale revision")))
        with mock.patch.object(rusteda_flow.subprocess,"run",return_value=subprocess.CompletedProcess([],1,reply,"")):
            with self.assertRaises(rusteda_flow.FlowError) as raised:
                rusteda_flow.flow_engine("rusteda",self.root,["prepare","--base-rev","rev0"])
        self.assertEqual(raised.exception.code,"REVISION_CONFLICT")

    def test_recovery_is_available_after_producer_dependencies_change(self):
        lane=self.root/"lane";lane.write_text("trusted lane");lane.chmod(0o755)
        producer=self.root/"producer";producer.write_text("old producer");producer.chmod(0o755)
        manifest=self.root/"manifest.json";manifest.write_text("{}")
        record=dict(path=str(manifest),sha256=toolchain.digest(manifest))
        lock=dict(schema=toolchain.SCHEMA,status="ready",programs={
            "flow_driver":dict(path=str(lane),sha256=toolchain.digest(lane)),
            "producer":dict(path=str(producer),sha256=toolchain.digest(producer))},
            tool_manifest=record,signoff_manifest=record)
        producer.write_text("changed producer");manifest.write_text('{"changed":true}')
        self.assertEqual(toolchain.verify(lock,recovery=True),lock)
        with self.assertRaises(ValueError):toolchain.verify(lock)
        lane.write_text("untrusted lane")
        with self.assertRaises(ValueError):toolchain.verify(lock,recovery=True)

    def test_crashed_caller_leaves_a_recoverable_global_reservation(self):
        path,r=self.request();expected=toolchain.digest(path)
        worker_control.register(self.root,r,path,expected)
        with self.assertRaises(ValueError):worker_control.require_idle(self.root)
        worker_control.require_idle(self.root,Path(r["workspace"]))
        receipt=self.root/"receipt.json"
        toolchain.write_json(receipt,dict(request_sha256=expected,cleanup_verified=False))
        worker_control.close_registration(self.root,receipt)
        with self.assertRaises(ValueError):worker_control.require_idle(self.root)
        toolchain.write_json(receipt,dict(request_sha256=expected,cleanup_verified=True))
        worker_control.close_registration(self.root,receipt)
        worker_control.require_idle(self.root)


if __name__ == "__main__":
    unittest.main()

"""RustEDA entrypoint for RustLane's persistent external-worker protocol."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from . import worker_control
from .toolchain import read_json, verify, write_json


class LaneError(ValueError):
    def __init__(self, error):
        self.code = error.get("code", "RUSTLANE_ERROR")
        super().__init__(error.get("message", json.dumps(error)))


def lane(program, workspace, arguments, require_ok=True):
    command = [program,"--workspace",str(workspace),*arguments]
    r = subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=180)
    if len(r.stdout)+len(r.stderr)>16*1024**2:
        raise ValueError("RustLane response exceeds budget")
    try:
        value=json.loads(r.stdout)
    except ValueError as error:
        raise ValueError("RustLane returned no valid JSON: "+r.stderr[-1000:]) from error
    if require_ok and (r.returncode or value.get("ok") is not True):
        raise LaneError(value.get("error") or value)
    return value


def main(argv=None, root=None):
    root=Path(root or Path(__file__).resolve().parents[1])
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace","-w",type=Path,required=True)
    p.add_argument("--tools",type=Path,default=root/".work/integration/toolchain.json")
    sub=p.add_subparsers(dest="action",required=True)
    init=sub.add_parser("init")
    init.add_argument("--base-rev",required=True)
    init.add_argument("--design",required=True)
    init.add_argument("--pdk",choices=("sky130","gf180"),required=True)
    run=sub.add_parser("run")
    run.add_argument("--base-rev",required=True)
    run.add_argument("--retry-failed",action="store_true")
    recover=sub.add_parser("recover")
    recover.add_argument("--base-rev",required=True)
    for name in ("report","plan","doctor"):
        q=sub.add_parser(name);q.add_argument("--rev",default="HEAD")
        if name=="report":q.add_argument("--summary-only",action="store_true")
    a=p.parse_args(argv)
    # Recovery must be possible when producer tools, PDK files or manifests changed.
    # The RustLane executable still must match the trusted local tool configuration.
    lock=verify(read_json(a.tools),recovery=a.action=="recover");program=lock["programs"]["rustlane"]["path"]
    workspace=a.workspace.resolve()
    if a.action=="init":
        if a.base_rev!="empty":
            return dict(ok=False,error=dict(code="REVISION_CONFLICT",message="init requires --base-rev empty"))
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}",a.design):
            raise ValueError("design must be a safe manifest name")
        project=Path(lock["project"]);manifest=project/"fixtures/designs"/(a.design+".json")
        design=read_json(manifest)
        if design.get("name")!=a.design or a.pdk not in design.get("pdks",{}):
            raise ValueError("unknown design/PDK pair")
        canonical_pdk={"sky130":"sky130_fd_sc_hd","gf180":"gf180mcu_fd_sc_mcu7t5v0"}[a.pdk]
        config=dict(schema_version=1,design_name=a.design,pdk=canonical_pdk,flow="rusteda",
                    road=dict(project=str(project),manifest=str(manifest),driver=str(root/"integration/road_worker.py")),
                    tools={k:lock["programs"][k]["path"] for k in ("python","rustroad","gnu_time","podman")},
                    limits=dict(timeout_secs=int(design["budgets"]["max_wall_s"]),memory_mib=8192,
                                max_log_bytes=16*1024**2,max_file_bytes=1024**3,max_input_bytes=4*1024**3,
                                max_paths=8,max_state_gib=1,threads=2,max_conflicts=10000))
        identity=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
        path=root/".work/integration/configs"/(identity+".json")
        if path.exists():
            if read_json(path)!=config:raise ValueError("existing configuration differs")
        else:write_json(path,config)
        return lane(program,workspace,["init","--base-rev",a.base_rev,"--config",str(path)])
    if a.action in ("report","plan","doctor"):
        value=lane(program,workspace,[a.action,"--rev",a.rev],require_ok=False)
        if a.action=="report" and a.summary_only:
            value.pop("inputs",None);value.pop("stages",None)
            if isinstance(value.get("summary"),dict):value["summary"].pop("qualification",None)
        return value
    if a.action=="run":
        worker_control.available()
        with worker_control.scale_lock(root):
            worker_control.require_idle(root)
            args=["prepare","--base-rev",a.base_rev]
            if a.retry_failed:args.append("--retry-failed")
            prepared=lane(program,workspace,args)
            if prepared["status"]=="cached":return prepared
            request_data=worker_control.request(prepared["request"],prepared["request_sha256"])
            worker_control.register(root,request_data,prepared["request"],prepared["request_sha256"])
            receipt=worker_control.execute(prepared["request"],prepared["request_sha256"])
            worker_control.close_registration(root,receipt)
            return lane(program,workspace,["finish","--base-rev",prepared["new_rev"],"--receipt",str(receipt)],require_ok=False)
    worker_control.available()
    active=lane(program,workspace,["worker","--rev",a.base_rev])
    with worker_control.scale_lock(root):
        worker_control.require_idle(root,workspace)
        receipt=worker_control.recover(active["request"],active["request_sha256"])
        worker_control.close_registration(root,receipt)
        return lane(program,workspace,["finish","--base-rev",a.base_rev,"--receipt",str(receipt)],require_ok=False)

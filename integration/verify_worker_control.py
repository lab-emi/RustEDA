#!/usr/bin/env python3
"""Real SIGTERM/SIGKILL recovery tests with owned containers; not an EDA oracle.

Run under a 2 GiB memory scope and 180-second deadline. Each synthetic container
uses 64 MiB and a fixed lifetime. The fixture never reads reference-tool source.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integration import worker_control as W
from integration.toolchain import digest,read_json,write_json


def cmd(args):
    r=subprocess.run(args,capture_output=True,text=True,timeout=15,stdin=subprocess.DEVNULL)
    if r.returncode:raise RuntimeError(r.stderr[-1000:])
    return r.stdout.strip()


def case(root,kind,podman,python,image):
    ws=root/kind;attempt=ws/"steps/rtl2gds/attempt1";attempt.mkdir(parents=True)
    project=ws/"producer";(project/"scripts").mkdir(parents=True)
    (project/"scripts/rtl2gds.py").write_text(
        "import subprocess\nsubprocess.run("+repr([podman,"run","--rm","--pull=never","--network=none",
        "--read-only","--memory=64m","--memory-swap=64m","--pids-limit=32","--timeout=90",
        "--entrypoint=/bin/sleep",image,"80"])+",check=True)\n")
    manifest=project/"unused.json";manifest.write_text("{}")
    identity=__import__('hashlib').sha256(str(attempt).encode()).hexdigest()
    r=dict(schema="rustlane.worker.v1",request_id=identity,unit="rusteda-worker-"+identity[:24],
           workspace=str(ws),cwd=str(attempt),program=python,podman=podman,timeout_seconds=100,
           memory_mib=8192,max_log_bytes=1024*1024,
           arguments=[str(ROOT/"integration/road_worker.py"),"--project",str(project),"--manifest",str(manifest),
                      "--output",str(attempt/"output"),"--pdk","sky130","--rustroad","/bin/true","--gnu-time","/bin/true"],
           environment=dict(RUSTEDA_WORKER_ID=identity,RUSTEDA_OWNERSHIP=str(attempt/"ownership")))
    path=attempt/"request.json";write_json(path,r);expected=digest(path)
    code="from integration.worker_control import execute;print(execute("+repr(str(path))+","+repr(expected)+"))"
    log=attempt/"controller.stdout";err=attempt/"controller.stderr"
    process=None
    try:
        with log.open("w") as out,err.open("w") as errors:
            process=subprocess.Popen([python,"-c",code],cwd=ROOT,stdout=out,stderr=errors,start_new_session=True)
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                owned=cmd([podman,"ps","--filter",f"label={W.LABEL}={identity}","--format","{{.ID}}"])
                if owned:break
                if process.poll() is not None:raise RuntimeError("controller exited before test container: "+err.read_text())
                time.sleep(.1)
            else:raise RuntimeError("owned test container did not become active")
            if cmd(["systemctl","--user","is-active",r["unit"]+".scope"])!="active":
                raise RuntimeError("owned process scope was not active before cancellation")
            process.send_signal(signal.SIGTERM if kind=="sigterm" else signal.SIGKILL)
            process.wait(timeout=45)
        if kind=="sigkill":
            receipt=W.recover(path,expected)
        else:
            if process.returncode:raise RuntimeError("graceful controller did not finish: "+err.read_text())
            receipt=Path(log.read_text().strip())
        value=read_json(receipt)
        if not value['cancelled'] or not value['cleanup_verified']:
            raise RuntimeError("cancel/recovery was not attested")
        if cmd([podman,"ps","-a","--filter",f"label={W.LABEL}={identity}","--format","{{.ID}}"]):
            raise RuntimeError("owned container survived cancellation")
        return dict(status="passed",request_sha256=expected,receipt=read_json(receipt),
                    cleanup=read_json(value['cleanup_report']))
    finally:
        W.cleanup(r)
        if process is not None and process.poll() is None:
            process.kill();process.wait(timeout=10)


def root_flow_case(root, podman, tools):
    """Exercise the real root CLI, registry, RustEDA flow revisions and producer."""
    W.require_idle(ROOT)
    workspace=root/"root-flow"
    prefix=[str(ROOT/"rusteda"),"flow","--workspace",str(workspace),"--tools",str(tools)]
    def invoke(label,args):
        output=root/(label+".json");errors=root/(label+".stderr")
        with output.open("x") as out, errors.open("x") as err:
            p=subprocess.run(prefix+args,stdin=subprocess.DEVNULL,stdout=out,stderr=err,timeout=180)
        return p.returncode,read_json(output,16*1024**2)
    code,initial=invoke("root-init",["init","--base-rev","empty","--design","counter","--pdk","gf180"])
    if code or not initial["ok"]:raise RuntimeError("root lifecycle initialization failed")
    rev=initial["new_rev"];observations=[]
    for kind in ["sigterm","sigkill"]:
        argv=["run","--base-rev",rev]+(["--retry-failed"] if observations else [])
        output=root/("root-"+kind+".json");errors=root/("root-"+kind+".stderr")
        active=None;process=None
        try:
            with output.open("x") as out, errors.open("x") as err:
                process=subprocess.Popen(prefix+argv,stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True)
                deadline=time.monotonic()+40
                while time.monotonic()<deadline:
                    registry=W.active_file(ROOT)
                    if registry.exists():
                        r=read_json(registry)
                        if r.get("status")=="active" and r.get("workspace")==str(workspace):
                            active=r
                            owned=cmd([podman,"ps","--filter",f"label={W.LABEL}={r['request_id']}","--format","{{.ID}}"])
                            if owned:break
                    if process.poll() is not None:raise RuntimeError("root controller exited before container start")
                    time.sleep(.1)
                else:raise RuntimeError("root-owned container did not become active")
                if cmd(["systemctl","--user","is-active",active["unit"]+".scope"])!="active":
                    raise RuntimeError("root process scope was not active before cancellation")
                process.send_signal(signal.SIGTERM if kind=="sigterm" else signal.SIGKILL)
                process.wait(timeout=45)
            if kind=="sigterm":
                value=read_json(output,16*1024**2)
                if value.get("status")!="failed":raise RuntimeError("root cancellation did not commit failure")
            else:
                rev=(workspace/"HEAD").read_text().strip()
                code,blocked=invoke("root-blocked",["run","--base-rev",rev,"--retry-failed"])
                if code==0 or (workspace/"HEAD").read_text().strip()!=rev:
                    raise RuntimeError("crashed caller did not block new work")
                code,value=invoke("root-recover",["recover","--base-rev",rev])
                if value.get("status")!="failed":raise RuntimeError("explicit recovery did not commit failure")
            rev=value["new_rev"]
            if W.active_file(ROOT).exists() and read_json(W.active_file(ROOT))["status"]!="closed":
                raise RuntimeError("successful cleanup did not close the registry")
            request=Path(active["request"])
            receipt=read_json(request.parent/"worker-receipt0.json")
            if not receipt["cleanup_verified"] or not receipt["cancelled"]:
                raise RuntimeError("root cancellation lacks verified cleanup")
            observations.append(dict(signal=kind,revision=rev,receipt=receipt))
            code,blocked=invoke("root-retry-required-"+kind,["run","--base-rev",rev])
            if code==0 or blocked.get("code")!="FAILED_STEP":
                raise RuntimeError("failed root flow did not require explicit retry")
        finally:
            if process is not None and process.poll() is None:
                process.terminate();process.wait(timeout=45)
            registry=W.active_file(ROOT)
            if active and registry.exists() and read_json(registry).get("status")=="active":
                current=(workspace/"HEAD").read_text().strip()
                invoke("root-emergency-recover-"+kind,["recover","--base-rev",current])
    code,report=invoke("root-report",["report"])
    if code or report["summary"]["complete"] or report["stages"][0]["attempts"]!=2:
        raise RuntimeError("root lifecycle final state differs")
    return dict(status="passed",cases=observations,attempts=2,final_revision=rev)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--tools',type=Path,default=ROOT/'.work/integration/toolchain.json')
    p.add_argument('--with-root-flow',action='store_true',help='also capture full producer inputs and test the root CLI lifecycle')
    a=p.parse_args()
    root=a.out.resolve();root.mkdir(parents=True,exist_ok=False)
    lock=read_json(a.tools);podman=lock['programs']['podman']['path'];python=lock['programs']['python']['path']
    image=read_json(lock['signoff_manifest']['path'])['images']['iic_osic_tools']['id']
    result=dict(schema='rusteda.worker-control-verification.v1',scope='process/container lifecycle only',status='unverified',cases={})
    sentinel=None
    try:
        W.available();cmd([podman,'image','exists',image])
        sentinel=cmd([podman,'run','--detach','--rm','--pull=never','--network=none','--read-only',
                      '--memory=64m','--memory-swap=64m','--pids-limit=32','--timeout=180',
                      '--entrypoint=/bin/sleep',image,'170'])
        for kind in ['sigterm','sigkill']:
            result['cases'][kind]=case(root,kind,podman,python,image)
            if cmd([podman,'inspect','--format','{{.State.Running}}',sentinel])!='true':
                raise RuntimeError('unrelated sentinel was affected')
        if a.with_root_flow:
            result['root_flow']=root_flow_case(root,podman,a.tools)
            if cmd([podman,'inspect','--format','{{.State.Running}}',sentinel])!='true':
                raise RuntimeError('root lifecycle test affected unrelated sentinel')
        result['unrelated_container_preserved']=True;result['status']='passed'
    except Exception as error:
        result['error']=str(error)
    finally:
        if sentinel is not None:
            try:cmd([podman,'rm','--force','--time','0',sentinel])
            except Exception as error:
                result.update(status='unverified',sentinel_cleanup_error=str(error))
        write_json(root/'result.json',result)
    print(json.dumps(dict(status=result['status'],result=str(root/'result.json'))))
    return 0 if result['status']=='passed' else 3


if __name__=='__main__':raise SystemExit(main())

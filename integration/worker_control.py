"""Bounded foreground worker scopes and cleanup by exact task ownership."""
from __future__ import annotations

import json
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

from .toolchain import digest, read_json, write_json

LABEL = "org.rusteda.request"


@contextmanager
def scale_lock(root):
    path=Path(root)/".work/scale.lock";path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a") as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise ValueError("another high-memory RustEDA run is active") from error
        yield


def active_file(root):
    return Path(root)/".work/integration/active-worker.json"


def require_idle(root, workspace=None):
    path=active_file(root)
    if path.exists():
        active=read_json(path)
        if not isinstance(active,dict) or active.get("schema")!="rusteda.active-worker.v1" or active.get("status") not in ("active","closed"):
            raise ValueError("active worker registry is malformed")
        if active.get("status")=="active" and (workspace is None or active.get("workspace")!=str(workspace)):
            raise ValueError("an external worker needs explicit recovery before another run: "+active.get("workspace","unknown"))


def register(root, r, request_path, expected):
    require_idle(root)
    write_json(active_file(root),dict(schema="rusteda.active-worker.v1",status="active",workspace=r["workspace"],request=str(request_path),
                                    request_sha256=expected,request_id=r["request_id"],unit=r["unit"]))


def close_registration(root, receipt):
    result=read_json(receipt)
    path=active_file(root)
    if path.exists() and result.get("cleanup_verified") is True:
        active=read_json(path)
        if active.get("request_sha256")!=result["request_sha256"]:
            raise ValueError("active worker registry differs from completed request")
        active.update(status="closed",receipt=str(receipt),receipt_sha256=digest(receipt))
        write_json(path,active)


def command(argv, timeout=10):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    if len(result.stdout)+len(result.stderr)>1024*1024:
        raise ValueError("cleanup command output exceeded budget")
    return result


def request(path, expected):
    path = Path(path).resolve()
    if digest(path) != expected:
        raise ValueError("worker request identity changed")
    r = read_json(path)
    identity = r.get("request_id", "")
    if (r.get("schema") != "rustlane.worker.v1" or not re.fullmatch(r"[a-f0-9]{64}", identity)
            or r.get("unit") != "rusteda-worker-"+identity[:24]
            or Path(r["cwd"]).resolve() != path.parent
            or not path.is_relative_to(Path(r["workspace"]).resolve()/"steps/rtl2gds")
            or not 1<=r["timeout_seconds"]<=86400 or not 8192<=r["memory_mib"]<=16384
            or not 1024<=r["max_log_bytes"]<=64*1024**2):
        raise ValueError("invalid bounded worker request")
    return r


def available():
    result = command(["systemctl","--user","is-active","default.target"])
    if result.returncode:
        raise ValueError("external workers require the configured user systemd manager")


def cleanup(r):
    # systemctl otherwise appends .service, but systemd-run --scope creates .scope.
    unit = r["unit"]+".scope"
    details = dict(schema="rusteda.worker-cleanup.v1", request_id=r["request_id"], unit=unit, errors=[], removed=[])
    description = "RustEDA worker "+r["request_id"]
    try:
        probe = command(["systemctl","--user","show",unit,"--property=LoadState,ActiveState,Description"])
        fields = dict(line.split("=",1) for line in probe.stdout.splitlines() if "=" in line)
        if fields.get("LoadState") is None:
            raise ValueError("cannot determine worker unit ownership/state")
        if fields.get("ActiveState") not in (None,"inactive","failed"):
            if fields.get("Description") != description:
                raise ValueError("worker unit ownership differs; refusing to stop it")
            stop = command(["systemctl","--user","stop",unit],timeout=20)
            if stop.returncode:
                raise ValueError("worker scope did not stop: "+stop.stderr[-500:])
        after = command(["systemctl","--user","is-active",unit])
        if after.stdout.strip() not in ("inactive","failed","unknown"):
            raise ValueError("worker unit is active or its state is unknown")
        details["scope"] = "inactive"
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        details["errors"].append(str(error))
    try:
        podman = r["podman"]
        args = [podman,"ps","-a","--filter",f"label={LABEL}={r['request_id']}","--format","{{.ID}}"]
        listing = command(args)
        if listing.returncode:
            raise ValueError("cannot inspect owned containers: "+listing.stderr[-500:])
        ids = listing.stdout.split()
        if len(ids)>64 or any(not re.fullmatch(r"[a-f0-9]{12,64}",i) for i in ids):
            raise ValueError("owned-container inventory is invalid or over budget")
        deadline = time.monotonic()+30
        for identity in ids:
            if time.monotonic()>deadline:
                raise ValueError("container cleanup deadline exceeded")
            inspected = command([podman,"inspect","--format",'{{index .Config.Labels "'+LABEL+'"}}',identity])
            if inspected.returncode or inspected.stdout.strip()!=r["request_id"]:
                raise ValueError("container ownership changed; refusing removal")
            removed = command([podman,"rm","--force","--time","0",identity])
            if removed.returncode:
                raise ValueError("owned container could not be removed")
            details["removed"].append(identity)
        # The owning process scope is already inactive; no worker can start another container.
        for _ in range(2):
            listing = command(args)
            if listing.returncode or listing.stdout.strip():
                raise ValueError("owned containers remain after cleanup")
            time.sleep(.1)
        details["containers"] = "absent"
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        details["errors"].append(str(error))
    details["verified"] = not details["errors"]
    return details


def receipt_path(directory):
    for i in range(128):
        p = directory/f"worker-receipt{i}.json"
        if not p.exists():
            return p
    raise ValueError("worker receipt slot budget exceeded")


def save_receipt(r, expected, started, code, cancelled, timed_out, log_limit, stdout, stderr, clean):
    directory = Path(r["cwd"])
    destination = receipt_path(directory)
    cleanup_file = destination.with_name(destination.stem+"-cleanup.json")
    write_json(cleanup_file,clean)
    metrics_file = directory/"worker-metrics.json"
    peak = None
    if metrics_file.exists():
        metrics = read_json(metrics_file,64*1024)
        if metrics.get("request_id") == r["request_id"]:
            value = metrics.get("peak_rss_kib")
            if isinstance(value,int) and 0<value<=r["memory_mib"]*1024:
                peak=value
    result = dict(schema="rustlane.worker-receipt.v1",request_sha256=expected,exit_code=code,
                  elapsed_seconds=time.monotonic()-started,peak_rss_kib=peak,timed_out=timed_out,
                  cancelled=cancelled,log_limit_exceeded=log_limit,cleanup_verified=clean["verified"],
                  stdout=str(stdout),stderr=str(stderr),stdout_sha256=digest(stdout),stderr_sha256=digest(stderr),
                  cleanup_report=str(cleanup_file),cleanup_sha256=digest(cleanup_file))
    write_json(destination,result)
    return destination


def execute(path, expected):
    available()
    r = request(path,expected)
    directory = Path(r["cwd"])
    probe = command(["systemctl","--user","is-active",r["unit"]+".scope"])
    if probe.stdout.strip() in ("active","activating","deactivating"):
        raise ValueError("worker unit already exists")
    stdout,stderr = directory/"worker.stdout",directory/"worker.stderr"
    if stdout.exists() or stderr.exists():
        raise ValueError("worker logs already exist; recover the request instead of executing it again")
    environment = dict(os.environ, **r["environment"])
    environment["PATH"] = str(Path(r["podman"]).parent)+os.pathsep+environment.get("PATH","")
    argv = ["systemd-run","--user","--scope","--quiet","--unit",r["unit"],
            "--description","RustEDA worker "+r["request_id"],
            "-p",f"MemoryMax={r['memory_mib']}M","-p","MemorySwapMax=2048M",
            r["program"],*r["arguments"]]
    cancelled=False
    def cancel(_signum,_frame):
        nonlocal cancelled
        cancelled=True
    old = {sig:signal.signal(sig,cancel) for sig in (signal.SIGTERM,signal.SIGINT)}
    started=time.monotonic(); timed_out=log_limit=False; code=None
    proc=None;used=0
    try:
        with stdout.open("xb") as out, stderr.open("xb") as err:
            proc = subprocess.Popen(argv, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            selector=selectors.DefaultSelector()
            for stream,target in [(proc.stdout,out),(proc.stderr,err)]:
                os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,target)
            while selector.get_map():
                if time.monotonic()-started>r["timeout_seconds"]:timed_out=True
                if cancelled or timed_out or log_limit:
                    break
                for key,_ in selector.select(.1):
                    data=os.read(key.fileobj.fileno(),16384)
                    if not data:
                        selector.unregister(key.fileobj);continue
                    left=max(0,r["max_log_bytes"]-used)
                    key.data.write(data[:left]);used+=min(left,len(data))
                    if len(data)>left:log_limit=True;break
            selector.close()
            if not (cancelled or timed_out or log_limit):
                code=proc.wait(timeout=10)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        with stderr.open("ab") as err:
            err.write(("worker launch/stream error: "+str(error)).encode()[:min(4096,max(0,r["max_log_bytes"]-used))])
        code=125
    finally:
        clean=cleanup(r)
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait(timeout=5)
        for sig,handler in old.items():signal.signal(sig,handler)
    return save_receipt(r,expected,started,code,cancelled,timed_out,log_limit,stdout,stderr,clean)


def recover(path, expected):
    r=request(path,expected);directory=Path(r["cwd"]);started=time.monotonic()
    clean=cleanup(r);dest=receipt_path(directory)
    stdout=dest.with_suffix(".stdout");stderr=dest.with_suffix(".stderr")
    stdout.write_text("");stderr.write_text("explicit recovery of interrupted external worker\n")
    return save_receipt(r,expected,started,125,True,False,False,stdout,stderr,clean)

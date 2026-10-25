#!/usr/bin/env python3
"""Foreground RustROAD worker with per-request container ownership metadata.

The controller owns the cgroup. This script only adds labels/CID files to
existing Podman invocations; numerical inputs and acceptance rules are unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import resource
import runpy
import subprocess
import sys
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("project","manifest","output","rustroad","gnu-time"):
        p.add_argument("--"+name, required=True, type=Path)
    p.add_argument("--pdk", required=True, choices=("sky130","gf180"))
    a = p.parse_args()
    identity = os.environ.get("RUSTEDA_WORKER_ID", "")
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("worker ownership ID is missing")
    owner = Path(os.environ["RUSTEDA_OWNERSHIP"]).resolve()
    owner.mkdir(parents=True, exist_ok=False)
    original = subprocess.Popen
    count = 0

    class OwnedPopen(original):
        def __init__(self, argv, *args, **kwargs):
            nonlocal count
            actual = argv
            if isinstance(argv, (list,tuple)):
                actual = list(map(str,argv))
                for index in range(len(actual)-1):
                    if Path(actual[index]).name == "podman" and actual[index+1] == "run":
                        if count >= 512:
                            raise RuntimeError("container invocation budget exceeded")
                        cid = owner/f"container{count}.cid"
                        actual[index+2:index+2] = [f"--label=org.rusteda.request={identity}", f"--cidfile={cid}"]
                        with (owner/f"container{count}.json").open("x") as stream:
                            json.dump(dict(request_id=identity, original_argv=list(map(str,argv)),
                                           actual_argv=actual, cidfile=str(cid)),stream,indent=2)
                            stream.flush();os.fsync(stream.fileno())
                        count += 1
                        break
            super().__init__(actual, *args, **kwargs)

    subprocess.Popen = OwnedPopen
    os.environ.update(RUSTEDA_GNU_TIME=str(a.gnu_time.resolve()), PYTHONDONTWRITEBYTECODE="1",
                      OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2")
    sys.path.insert(0, str(a.project.resolve()/"scripts"))
    sys.argv = [str(a.project/"scripts/rtl2gds.py"), "--design", str(a.manifest), "--pdk", a.pdk,
                "--run", str(a.output), "--base-rev", "empty", "--rustroad", str(a.rustroad)]
    started = time.monotonic()
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    finally:
        subprocess.Popen = original
        usage = resource.getrusage(resource.RUSAGE_SELF)
        child = resource.getrusage(resource.RUSAGE_CHILDREN)
        peak = max(usage.ru_maxrss, child.ru_maxrss)
        summary = a.output/"summary.json"
        if summary.is_file() and summary.stat().st_size < 16*1024**2:
            data = json.loads(summary.read_text())
            peak = max(peak, data.get("resources",{}).get("peak_rss_kib") or 0)
        metrics = dict(elapsed_seconds=time.monotonic()-started, peak_rss_kib=peak,
                       rss_scope="maximum observed individual worker/tool/container RSS, not aggregate",
                       container_invocations=count, request_id=identity)
        (owner.parent/"worker-metrics.json").write_text(json.dumps(metrics,indent=2)+"\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Reproduce bounded full-flow acceptance through the public RustEDA entrypoint.

No EDA oracle is implemented here: RustEDA flow verifies the qualified RustROAD
producer's independent tool receipts. Run under an OS memory/time guard. Each
worker also owns its bounded scope and containers. Interrupted runs require
`rusteda flow recover`; evidence and active reservations are never deleted here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integration.toolchain import digest, read_json, write_json

CASES = ("counter:sky130", "counter:gf180", "fifo:sky130")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=CASES)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    result = dict(schema="rusteda.phase3-flow.v1", status="unverified", cases=[],
                  scope="root CLI, full producer gates, cache integrity and revision conflicts")

    def call(label, arguments, *, expected_ok=True):
        stdout, stderr = out/(label+".json"), out/(label+".stderr")
        started = time.monotonic()
        with stdout.open("xb") as output, stderr.open("xb") as errors:
            process = subprocess.run([str(ROOT/"rusteda"), *arguments], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                     timeout=4200)
        wall = time.monotonic()-started
        value = read_json(stdout, 16*1024**2)
        if (process.returncode == 0 and value.get("ok") is True) != expected_ok:
            raise RuntimeError(f"{label}: unexpected result; inspect {stdout}")
        return value, wall

    try:
        call("tools", ["tools", "verify"])
        for case in args.case or CASES:
            design, pdk = case.split(":")
            name = design+"-"+pdk
            workspace = out/name
            prefix = ["flow", "--workspace", str(workspace)]
            initialized, init_wall = call(name+"-init", prefix+[
                "init", "--base-rev", "empty", "--design", design, "--pdk", pdk])
            completed, run_wall = call(name+"-run", prefix+[
                "run", "--base-rev", initialized["new_rev"]])
            if completed["status"] != "complete" or not completed["summary"]["complete"]:
                raise RuntimeError(name+": incomplete flow")
            revision = completed["new_rev"]
            reported, report_wall = call(name+"-report", prefix+["report", "--summary-only"])
            cached, cache_wall = call(name+"-cache", prefix+["run", "--base-rev", revision])
            if cached["status"] != "cached" or cached["executed_steps"] != 0 or cached["new_rev"] != revision:
                raise RuntimeError(name+": invalid cache reuse")
            stale, _ = call(name+"-stale", prefix+["run", "--base-rev", initialized["new_rev"]], expected_ok=False)
            if stale.get("code") != "REVISION_CONFLICT" or (workspace/"HEAD").read_text().strip() != revision:
                raise RuntimeError(name+": stale mutation was not rejected")
            receipt = read_json(workspace/"steps/rtl2gds/attempt1/worker-receipt0.json")
            if receipt["cleanup_verified"] is not True or receipt["exit_code"] != 0:
                raise RuntimeError(name+": worker cleanup/exit not verified")
            result["cases"].append(dict(case=case, status="passed", revision=revision,
                qualification_status=reported["summary"]["qualification_status"],
                summary=reported["summary"], wall_seconds=dict(init=init_wall, run=run_wall,
                report=report_wall, cache=cache_wall), worker_receipt=receipt,
                producer_summary=str(workspace/"steps/rtl2gds/attempt1/output/summary.json")))
            write_json(out/"result.json", result)
        result["status"] = "passed"
    except Exception as error:
        result["status"] = "failed"
        result["error"] = str(error)
    result["toolchain_sha256"] = digest(ROOT/".work/integration/toolchain.json")
    write_json(out/"result.json", result)
    print(json.dumps(dict(status=result["status"], result=str(out/"result.json"))))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

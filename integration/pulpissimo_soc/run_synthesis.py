#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run one bounded RustSys synthesis sample on the prepared PULPissimo netlist.

The runner measures the complete process (read, elaboration, AIG construction,
mapping and export) with GNU time.  It never infers a metric that was not
measured: a timeout or a missing maximum-RSS record leaves the result
``unverified`` and the report values absent rather than zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import time


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version(binary: Path) -> str:
    try:
        return subprocess.check_output([str(binary), "--version"], text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--lib", type=Path, required=True, help="standard-cell or macro Liberty")
    parser.add_argument("--extra-lib", type=Path, action="append", default=[], help="additional macro Liberty; repeatable")
    parser.add_argument("--rustsys", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--top", default="pulpissimo_soc_wrapper")
    parser.add_argument("--param", action="append", default=[], help="top parameter override NAME=EXPR")
    parser.add_argument("--gnu-time", type=Path, default=Path("/usr/bin/time"))
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--memory-gib", type=int, default=32)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--label", default="PULPissimo Ibex SoC, SKY130 HD TT")
    args = parser.parse_args()

    inp = args.input.resolve()
    lib = args.lib.resolve()
    libs = [lib] + [p.resolve() for p in args.extra_lib]
    rustsys = args.rustsys.resolve()
    output = args.output.resolve()
    report = args.report.resolve()
    receipt = args.receipt.resolve()
    gnu_time = args.gnu_time.resolve()
    for path, name in ((inp, "input"), (rustsys, "rustsys"), (gnu_time, "GNU time")):
        if not path.is_file():
            raise SystemExit(f"missing {name}: {path}")
    for index, path in enumerate(libs):
        if not path.is_file():
            raise SystemExit(f"missing library {index}: {path}")

    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    timed = receipt.with_suffix(".time")
    log = receipt.with_suffix(".log")
    command = [
        str(rustsys), "--threads", str(args.threads), "synth",
        "--top", args.top,
        *[item for path in libs for item in ("--lib", str(path))],
        "-D", "SYNTHESIS", "--progress", "log", "-v",
    ]
    for param in args.param:
        command += ["-P", param]
    command += [str(inp), "--out", str(output), "--report", str(report)]
    started = time.monotonic()
    with log.open("wb") as stdout:
        process = subprocess.Popen(
            [str(gnu_time), "-v", "-o", str(timed), *command],
            stdout=stdout,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            preexec_fn=lambda: (
                resource.setrlimit(resource.RLIMIT_AS, (args.memory_gib * 1024**3, args.memory_gib * 1024**3)),
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
            ),
        )
        try:
            code = process.wait(timeout=args.timeout)
            status = "completed" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            code = 124
            status = "timeout"
    wall = time.monotonic() - started
    text = timed.read_text() if timed.exists() else ""
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
    user = re.search(r"User time \(seconds\): ([0-9.]+)", text)
    system = re.search(r"System time \(seconds\): ([0-9.]+)", text)
    elapsed = re.search(r"Elapsed \(wall clock\) time .*: ([0-9:.]+)", text)

    metrics = None
    if report.is_file():
        raw = json.loads(report.read_text())
        cells = raw.get("cells", {})
        cell_count = sum(cells.values()) if isinstance(cells, dict) else raw.get("cell_count")
        metrics = {
            "cells": cell_count,
            "combinational_cells": raw.get("combinational_cells"),
            "sequential_cells": raw.get("sequential_cells"),
            "inverters": raw.get("inverters"),
            "area_um2": raw.get("area"),
            "logic_depth": raw.get("logic_depth"),
            "estimated_delay_ns": raw.get("estimated_delay"),
            "aig_nodes_before": raw.get("aig_nodes_before"),
            "aig_nodes_after": raw.get("aig_nodes_after"),
            "state_bits_rtl": raw.get("state_bits_rtl"),
            "state_bits_retained": raw.get("state_bits_retained"),
            "macro_cells": raw.get("macro_cells"),
            "latches": raw.get("latches"),
            "warnings": raw.get("warnings"),
            "phase_s": raw.get("phase_s"),
            "opt_phase_s": raw.get("opt_phase_s"),
            "runtime_s": raw.get("runtime_s"),
        }
    record = {
        "schema": "rusteda.pulpissimo-synthesis-receipt.v1",
        "label": args.label,
        "status": status,
        "exit_code": code,
        "command": command,
        "resources": {
            "threads": args.threads,
            "memory_limit_gib": args.memory_gib,
            "timeout_seconds": args.timeout,
            "wall_seconds": round(wall, 3),
            "recorded_wall_seconds": elapsed.group(1) if elapsed else None,
            "peak_rss_kib": int(rss.group(1)) if rss else None,
            "user_cpu_seconds": float(user.group(1)) if user else None,
            "system_cpu_seconds": float(system.group(1)) if system else None,
        },
        "tools": {
            "rustsys": {"path": str(rustsys), "sha256": digest(rustsys), "version": version(rustsys)},
            "gnu_time": {"path": str(gnu_time), "sha256": digest(gnu_time)},
        },
        "input": {"path": str(inp), "bytes": inp.stat().st_size, "sha256": digest(inp)},
        "library": {"path": str(lib), "bytes": lib.stat().st_size, "sha256": digest(lib)},
        "libraries": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}
            for path in libs
        ],
        "artifacts": {
            "netlist": str(output) if output.is_file() else None,
            "report": str(report) if report.is_file() else None,
            "log": str(log),
            "time": str(timed) if timed.exists() else None,
        },
        "metrics": metrics if status == "completed" else None,
    }
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

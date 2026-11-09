#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run the bounded Yosys reference on the PULPissimo Ibex core.

The generated sv2v netlist contains every PULPissimo module.  Yosys does not
need the unreachable SoC modules for a core-only comparison, so this script
first keeps the modules reachable from ``--top`` and then runs the bounded ABC
reference in ``yosys_ibex_core.ys``.  The reference is a labelled mapping
profile, not Yosys's default quality flow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
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
        return subprocess.check_output([str(binary), "-V"], text=True, stderr=subprocess.STDOUT).strip().splitlines()[0]
    except (OSError, subprocess.CalledProcessError, IndexError):
        return "unknown"


def prune(input_path: Path, top: str, output_path: Path) -> list[str]:
    lines = input_path.read_text(encoding="utf-8", errors="surrogateescape").splitlines(keepends=True)
    modules = {}
    order = []
    index = 0
    while index < len(lines):
        match = re.match(r"^module\s+([A-Za-z0-9_$]+)", lines[index])
        if match:
            name = match.group(1)
            end = index
            while end < len(lines) and not re.match(r"^endmodule\b", lines[end]):
                end += 1
            modules[name] = "".join(lines[index:end + 1])
            order.append(name)
            index = end + 1
            continue
        index += 1
    reachable = set()
    stack = [top]
    names = list(modules)
    while stack:
        name = stack.pop()
        if name in reachable or name not in modules:
            continue
        reachable.add(name)
        body = modules[name]
        # Substring reachability is conservative: it can retain a module whose
        # name appears only in a comment, but it never drops a real dependency.
        for candidate in names:
            if candidate != name and candidate in body:
                stack.append(candidate)
    output_path.write_text("".join(modules[name] for name in order if name in reachable), encoding="utf-8")
    return sorted(reachable)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--lib", type=Path, required=True)
    parser.add_argument("--yosys", type=Path, required=True)
    parser.add_argument("--top", default="ibex_core")
    parser.add_argument("--param", action="append", default=[], help="top parameter override NAME=EXPR")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stat", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--gnu-time", type=Path, default=Path("/usr/bin/time"))
    parser.add_argument("--memory-gib", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    inp = args.input.resolve()
    lib = args.lib.resolve()
    yosys = args.yosys.resolve()
    output = args.output.resolve()
    stat = args.stat.resolve()
    receipt = args.receipt.resolve()
    gnu_time = args.gnu_time.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    stat.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    pruned = receipt.with_suffix(".pruned.v")
    modules = prune(inp, args.top, pruned)
    script = receipt.with_suffix(".ys")
    script.write_text(
        "read_liberty -lib {lib}\n"
        "read_verilog -sv {pruned}\n"
        "{chparam}"
        "hierarchy -check -top {top}\n"
        "proc\nflatten\nopt\ntechmap\nopt\n"
        "dfflibmap -liberty {lib}\n"
        "abc -liberty {lib} -script \"+strash;&get,-n;&nf,-C,4,-F,1;&put\"\n"
        "opt -fast\n"
        "tee -o {stat} stat -json -liberty {lib}\n"
        "write_verilog -noattr {netlist}\n".format(
            lib=lib, pruned=pruned, top=args.top, stat=stat, netlist=output,
            chparam="".join(f"chparam -set {p.split('=',1)[0]} {p.split('=',1)[1]} {args.top}\n" for p in args.param),
        )
    )
    timed = receipt.with_suffix(".time")
    log = receipt.with_suffix(".log")
    started = time.monotonic()
    with log.open("wb") as stream:
        process = subprocess.Popen(
            [str(gnu_time), "-v", "-o", str(timed), str(yosys), "-Q", "-T", "-s", str(script)],
            stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
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
            code, status = 124, "timeout"
    wall = time.monotonic() - started
    text = timed.read_text() if timed.exists() else ""
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
    user = re.search(r"User time \(seconds\): ([0-9.]+)", text)
    system = re.search(r"System time \(seconds\): ([0-9.]+)", text)
    metrics = None
    if stat.is_file():
        raw = json.loads(stat.read_text())
        stat_modules = raw.get("modules", {})
        top = stat_modules.get("\\" + args.top, stat_modules.get(args.top, raw))
        cells = top.get("num_cells_by_type", top.get("cells", {}))
        standard = {name: count for name, count in cells.items() if not name.startswith("$")}
        metrics = {
            "cells": sum(standard.values()) if standard else top.get("num_cells"),
            "internal_cells": sum(count for name, count in cells.items() if name.startswith("$")),
            "area_um2": top.get("area"),
            "num_wires": top.get("num_wires"),
            "num_wire_bits": top.get("num_wire_bits"),
        }
    record = {
        "schema": "rusteda.pulpissimo-yosys-reference.v1",
        "status": status,
        "exit_code": code,
        "top": args.top,
        "command": [str(yosys), "-Q", "-T", "-s", str(script)],
        "script": script.read_text(),
        "resources": {
            "memory_limit_gib": args.memory_gib,
            "timeout_seconds": args.timeout,
            "wall_seconds": round(wall, 3),
            "peak_rss_kib": int(rss.group(1)) if rss else None,
            "user_cpu_seconds": float(user.group(1)) if user else None,
            "system_cpu_seconds": float(system.group(1)) if system else None,
        },
        "tools": {"yosys": {"path": str(yosys), "sha256": digest(yosys), "version": version(yosys)},
                  "gnu_time": {"path": str(gnu_time), "sha256": digest(gnu_time)}},
        "input": {"path": str(inp), "sha256": digest(inp), "pruned": str(pruned),
                  "pruned_sha256": digest(pruned), "modules": modules},
        "library": {"path": str(lib), "sha256": digest(lib)},
        "artifacts": {"netlist": str(output) if output.is_file() else None,
                      "stat": str(stat) if stat.is_file() else None,
                      "log": str(log), "script": str(script)},
        "metrics": metrics if status == "completed" else None,
    }
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("status", "resources", "metrics")}, indent=2))
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

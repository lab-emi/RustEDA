#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run Magic's antenna check on a final GDS with the pinned PDK technology file.

Input schema `rusteda.pdk-gate.magic-antenna.v1`: as `magic_drc.py` without `style`.

Magic extracts the layout (local, no parasitics) and applies the technology file's antenna
rules. Macro masters are abstract views read from their LEF, so gates inside a macro are not
part of the check; every gate of a standard cell is. The verdict is `passed` when the check ran
to its end and printed no violation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCHEMA = "rusteda.pdk-gate.magic-antenna.v1"
HERE = Path(__file__).resolve().parent
VIOLATION = re.compile(r"(?:Cell: (\S+)\s*\n)?\s*Antenna violation detected at plane (\S+)\s*\n"
                       r"\s*Effective antenna ratio ([0-9.eE+-]+) > limit ([0-9.eE+-]+)\s*\n"
                       r"\s*Gate rect \((-?\d+) (-?\d+)\) to \((-?\d+) (-?\d+)\)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    config = common.read_json(args.inputs, 8 * common.MIB)
    output = common.fresh_workspace(args.workspace, args.base_rev)
    common.require(config.get("schema") == SCHEMA, "INPUT_SCHEMA")
    top = config["top"]
    common.require(top.isascii() and top.replace("_", "").isalnum(), "TOP_HANDLE")
    gds = common.verify(config["gds"], "gds")
    tools = {name: common.verify(record, name) for name, record in config["tools"].items()}
    magicrc, env = common.magic_setup(config, tools)
    scratch = output / "ext"
    scratch.mkdir()
    magic_gds, pruned = common.pruned_gds(config, tools, output, gds)
    env.update(GATE_GDS=str(magic_gds), GATE_TOP=top, GATE_DIR=str(scratch))
    command = [tools["magic"], "-dnull", "-noconsole", "-rcfile", magicrc, HERE / "magic_antenna.tcl"]
    common.save_json(output / "command.json", [str(c) for c in command])
    run = common.run_tool(command, output / "magic.log", output / "magic.time", tools["gnu_time"],
                          int(config["timeout_seconds"]), env=env, cwd=output)
    summary = dict(schema="rusteda.pdk-gate.magic-antenna-result.v1", status="failed", complete=False, top=top,
                   gds=config["gds"], magicrc=config["magicrc"], tech_files=config["tech_files"],
                   abstract_cells=config["abstract_cells"], abstract_lefs=config["abstract_lefs"], tools=config["tools"],
                   script=common.identity(HERE / "magic_antenna.tcl"), magic_input=pruned,
                   scope="every gate of a standard cell; gates inside macro masters (LEF abstract views) are not checked",
                   engine=dict(exit_code=run["exit_code"], killed=run["killed"], wall_seconds=run["wall_seconds"],
                               peak_rss_kib=run["peak_rss_kib"]))
    try:
        common.require(run["exit_code"] == 0 and not run["killed"], f"MAGIC_RUN_FAILED: exit {run['exit_code']} {run['killed'] or ''}")
        log = (output / "magic.log").read_text(errors="replace")
        facts = common.magic_facts(log, config["abstract_cells"])
        common.require("Running antenna checks." in log and "antennacheck finished." in log
                       and re.search(r"^GATE_EXTRACTED\s*$", log, re.M) is not None, "ANTENNA_CHECK_INCOMPLETE")
        unit_um = round(float(facts["unit"]), 9)
        rows = [dict(cell=m[1], plane=m[2], ratio=float(m[3]), limit=float(m[4]),
                     gate_um=[round(int(m[i]) * unit_um, 4) for i in (5, 6, 7, 8)]) for m in VIOLATION.finditer(log)]
        loose = len(re.findall(r"Antenna violation detected", log))
        common.require(loose == len(rows), "ANTENNA_VIOLATION_FORMAT")
        planes = {}
        for row in rows:
            planes[row["plane"]] = planes.get(row["plane"], 0) + 1
        summary.update(technology=facts["tech"], unit_um=unit_um, violations=len(rows), categories=dict(sorted(planes.items())),
                       samples=rows[:20], status="passed" if not rows else "failed", complete=True)
        for record in (config["gds"], config["magicrc"], *config["tech_files"], *config["abstract_lefs"]):
            common.verify(record, "input after run")
    except (common.GateError, OSError, KeyError, ValueError) as error:
        summary.update(status="failed", complete=False, error=str(error))
    common.save_json(output / "summary.json", summary)
    print(json.dumps({k: summary.get(k) for k in ("status", "complete", "violations", "categories", "error")}))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

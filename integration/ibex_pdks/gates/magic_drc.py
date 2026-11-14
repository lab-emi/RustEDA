#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run Magic's DRC on a final GDS with the pinned PDK technology file.

Input schema `rusteda.pdk-gate.magic-drc.v1`:
  top, gds        top cell and pinned GDS record
  magicrc         pinned PDK start-up file; tech_files: the pinned files it loads
  pdk_root        exported as PDK_ROOT, which the start-up file reads
  style           DRC style, echoed back by Magic and compared
  abstract_lefs   pinned macro LEFs; abstract_cells: the macro masters they define
  tools           {magic, gnu_time}; timeout_seconds

Magic decides every rule. Macro masters are abstract views read from their LEF (pins and
obstructions): Magic checks all top-level and standard-cell geometry, including its spacing to
the macro's pins and obstructions, and nothing inside the macro. The verdict is the error count
of the hierarchical check of the top cell (`drc list count total`), which holds every error of
the cells below it, together with the boxes of `drc listall why`; both must be zero. The listing
names the rules; it can be shorter than the count, so the count decides.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCHEMA = "rusteda.pdk-gate.magic-drc.v1"
HERE = Path(__file__).resolve().parent


def rule_name(message) -> str:
    match = re.search(r"\(([^()]+)\)\s*$", message)
    return match[1] if match else message


def parse_listing(path, unit_um):
    """Magic's `R message` / `B x0 y0 x1 y1` listing -> counts and sample boxes (um) per rule."""
    counts, samples, current, total = {}, {}, None, 0
    with Path(path).open(errors="replace") as stream:
        for line in stream:
            if line.startswith("R "):
                current = rule_name(line[2:].strip())
                common.require(bool(current), "MAGIC_RULE_MESSAGE")
                counts.setdefault(current, 0)
            elif line.startswith("B "):
                numbers = line.split()[1:]
                common.require(current is not None and len(numbers) == 4, "MAGIC_BOX_FORMAT")
                counts[current] += 1
                total += 1
                common.require(total <= 20_000_000, "MAGIC_BOX_BUDGET")
                if len(samples.setdefault(current, [])) < 5:
                    samples[current].append([round(int(v) * unit_um, 4) for v in numbers])
    return total, dict(sorted(counts.items())), samples


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
    listing = output / "errors.txt"
    env.update(GATE_GDS=str(gds), GATE_TOP=top, GATE_STYLE=config["style"], GATE_OUT=str(listing))
    command = [tools["magic"], "-dnull", "-noconsole", "-rcfile", magicrc, HERE / "magic_drc.tcl"]
    common.save_json(output / "command.json", [str(c) for c in command])
    run = common.run_tool(command, output / "magic.log", output / "magic.time", tools["gnu_time"],
                          int(config["timeout_seconds"]), env=env, cwd=output)
    summary = dict(schema="rusteda.pdk-gate.magic-drc-result.v1", status="failed", complete=False, top=top,
                   gds=config["gds"], magicrc=config["magicrc"], tech_files=config["tech_files"], style=config["style"],
                   abstract_cells=config["abstract_cells"], abstract_lefs=config["abstract_lefs"], tools=config["tools"],
                   script=common.identity(HERE / "magic_drc.tcl"),
                   scope="all top-level and standard-cell geometry; macro masters are LEF abstract views",
                   engine=dict(exit_code=run["exit_code"], killed=run["killed"], wall_seconds=run["wall_seconds"],
                               peak_rss_kib=run["peak_rss_kib"]))
    try:
        common.require(run["exit_code"] == 0 and not run["killed"], f"MAGIC_RUN_FAILED: exit {run['exit_code']} {run['killed'] or ''}")
        facts = common.magic_facts((output / "magic.log").read_text(errors="replace"), config["abstract_cells"])
        common.require(all(name in facts for name in ("unit", "style", "count", "boxes")), "MAGIC_RUN_INCOMPLETE")
        common.require(facts["style"] == config["style"], "MAGIC_DRC_STYLE_NOT_ACTIVE")
        unit_um = round(float(facts["unit"]), 9)
        common.require(0 < unit_um < 1, "MAGIC_UNIT")
        total, categories, samples = parse_listing(listing, unit_um)
        common.require(total == int(facts["boxes"]), "MAGIC_BOX_COVERAGE")
        count = int(facts["count"])
        if count and not total:
            categories = {"errors Magic counted but did not list": count}
        summary.update(technology=facts["tech"], unit_um=unit_um, error_count=count, listed_boxes=total,
                       violations=max(count, total), categories=categories, samples=samples,
                       listing=common.identity(listing), status="passed" if count == 0 and total == 0 else "failed",
                       complete=True)
        for record in (config["gds"], config["magicrc"], *config["tech_files"], *config["abstract_lefs"]):
            common.verify(record, "input after run")
    except (common.GateError, OSError, KeyError, ValueError) as error:
        summary.update(status="failed", complete=False, error=str(error))
    common.save_json(output / "summary.json", summary)
    print(json.dumps({k: summary.get(k) for k in ("status", "complete", "error_count", "listed_boxes", "error")}))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

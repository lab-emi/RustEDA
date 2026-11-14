#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run one pinned foundry KLayout deck on a GDS and separate macro-interior markers.

Input schema `rusteda.pdk-gate.klayout-drc.v1`:
  mode       "layout" (a final layout) or "baseline" (one macro GDS alone)
  top, gds   top cell and pinned GDS record
  deck       pinned deck record; deck_files: every pinned file the deck includes
  switches   ordered [name, value] pairs passed as `-rd name=value`, exactly the profile's
  top_switch, threads_switch, threads
  macros     layout mode: {master: {gds, instances, baseline: {report, summary}}}
  tools      {klayout, gnu_time}; timeout_seconds

The deck decides every rule. This wrapper counts report items and, in layout mode, calls
`attribute_markers.py` to attribute to a macro exactly the markers the same deck with the
same switches reports on that macro's GDS alone. The verdict is `passed` when no other marker
exists. A baseline is accepted only if it was produced with the same deck bytes, switches,
tool and macro GDS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCHEMA = "rusteda.pdk-gate.klayout-drc.v1"
ATTRIBUTION = Path(__file__).resolve().parent / "attribute_markers.py"


def deck_key(config) -> str:
    """Identity of everything that determines the deck's verdict apart from the layout."""
    material = dict(deck=config["deck"]["sha256"],
                    deck_files=sorted(record["sha256"] for record in config["deck_files"]),
                    switches=config["switches"], top_switch=config["top_switch"],
                    threads_switch=config["threads_switch"], klayout=config["tools"]["klayout"]["sha256"])
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def count_items(path):
    common.require(path.is_file() and path.stat().st_size <= 1024 * common.MIB, "DRC_REPORT_MISSING_OR_OVERSIZED")
    categories, cells, total, root, declared = {}, {}, 0, None, 0
    for event, node in ET.iterparse(path, events=("start", "end")):
        if root is None:
            root = node.tag
            common.require(root == "report-database", "DRC_REPORT_ROOT")
        if event == "end" and node.tag == "item":
            category, cell = node.findtext("category"), node.findtext("cell")
            common.require(bool(category) and bool(cell), "DRC_REPORT_ITEM")
            category = category.strip("'")
            total += 1
            common.require(total <= 20_000_000, "DRC_ITEM_BUDGET")
            categories[category] = categories.get(category, 0) + 1
            cells[cell] = cells.get(cell, 0) + 1
            node.clear()
        elif event == "end" and node.tag == "category" and node.find("name") is not None:
            declared += 1
    return total, dict(sorted(categories.items())), dict(sorted(cells.items())), declared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    config = common.read_json(args.inputs, 8 * common.MIB)
    output = common.fresh_workspace(args.workspace, args.base_rev)
    common.require(config.get("schema") == SCHEMA and config["mode"] in ("layout", "baseline"), "INPUT_SCHEMA")
    top = config["top"]
    common.require(top.isascii() and top.replace("_", "").isalnum(), "TOP_HANDLE")
    gds = common.verify(config["gds"], "gds")
    deck = common.verify(config["deck"], "deck")
    for record in config["deck_files"]:
        common.verify(record, "deck file")
    tools = {name: common.verify(record, name) for name, record in config["tools"].items()}
    threads = int(config["threads"])
    common.require(1 <= threads <= 16, "THREADS")
    key = deck_key(config)
    report = output / "drc.lyrdb"
    command = [tools["klayout"], "-b", "-r", deck, "-rd", f"input={gds}", "-rd", f"report={report}",
               "-rd", f"{config['top_switch']}={top}", "-rd", f"{config['threads_switch']}={threads}"]
    for name, value in config["switches"]:
        common.require(name not in ("input", "report", config["top_switch"], config["threads_switch"]), "SWITCH_NAME")
        command += ["-rd", f"{name}={value}"]
    common.save_json(output / "command.json", [str(c) for c in command])
    # A report database above 1 GiB means millions of markers: stop the deck and report it.
    run = common.run_tool(command, output / "deck.log", output / "deck.time", tools["gnu_time"],
                          int(config["timeout_seconds"]), watch=[(report, int(config.get("max_report_bytes", 1 << 30)))])
    summary = dict(schema="rusteda.pdk-gate.klayout-drc-result.v1", status="failed", complete=False, mode=config["mode"],
                   top=top, gds=config["gds"], deck=config["deck"], deck_files=len(config["deck_files"]),
                   deck_key=key, switches=config["switches"], threads=threads, tools=config["tools"],
                   engine=dict(exit_code=run["exit_code"], killed=run["killed"], wall_seconds=run["wall_seconds"],
                               peak_rss_kib=run["peak_rss_kib"]))
    try:
        common.require(run["exit_code"] == 0 and not run["killed"], f"DECK_RUN_FAILED: exit {run['exit_code']} {run['killed'] or ''}")
        total, categories, cells, declared = count_items(report)
        summary.update(items=total, categories=categories, cells=cells, declared_rule_categories=declared,
                       raw_report=common.identity(report))
        if config["mode"] == "baseline":
            summary.update(status="recorded", complete=True)
        else:
            macros = {}
            for master, spec in config["macros"].items():
                base = common.read_json(common.verify(spec["baseline"]["summary"], "baseline summary"))
                common.require(base.get("status") == "recorded" and base.get("mode") == "baseline"
                               and base.get("deck_key") == key and base.get("top") == master
                               and base["gds"]["sha256"] == spec["gds"]["sha256"]
                               and base["raw_report"] == spec["baseline"]["report"],
                               f"MACRO_BASELINE_DIFFERS: {master}")
                macros[master] = dict(gds=str(common.verify(spec["gds"], "macro gds")), instances=spec["instances"],
                                      baseline=dict(kind="lyrdb", path=str(common.verify(spec["baseline"]["report"],
                                                                                         "baseline report"))))
            attribution = output / "attribution.json"
            common.save_json(output / "attribution-config.json", dict(
                layout=str(gds), top=top, source=dict(kind="lyrdb", path=str(report)), macros=macros,
                output=str(attribution), limits=dict(occurrences=int(config.get("max_occurrences", 20_000_000)))))
            second = common.run_tool([tools["klayout"], "-b", "-r", ATTRIBUTION, "-rd",
                                      f"config_path={output / 'attribution-config.json'}"],
                                     output / "attribution.log", output / "attribution.time", tools["gnu_time"], 3600)
            result = common.read_json(attribution) if attribution.is_file() else {}
            common.require(second["exit_code"] == 0 and result.get("status") == "completed",
                           "ATTRIBUTION_FAILED: " + str(result.get("error")))
            totals = result["totals"]
            common.require(totals["items"] == total, "ATTRIBUTION_ITEM_COVERAGE")
            summary.update(violations=totals["gating"], macro_interior=totals["macro_interior"],
                           macro_baseline_expected=totals["baseline_occurrences"],
                           macro_baseline_reproduced=totals["baseline_reproduced"],
                           occurrences=totals["occurrences"], gating_rules=result["gating_rules"],
                           macro_interior_rules=result["macro_interior_rules"], attribution_rule=result["rule"],
                           attribution=common.identity(attribution),
                           attribution_resources=dict(wall_seconds=second["wall_seconds"],
                                                      peak_rss_kib=second["peak_rss_kib"]),
                           samples={rule: result["rules"][rule]["samples"] for rule in result["gating_rules"]},
                           status="passed" if totals["gating"] == 0 else "failed", complete=True)
        for record in (config["gds"], config["deck"], *config["deck_files"]):
            common.verify(record, "input after run")
    except (common.GateError, OSError, KeyError, ET.ParseError) as error:
        summary.update(status="failed", complete=False, error=str(error))
    common.save_json(output / "summary.json", summary)
    print(json.dumps({k: summary.get(k) for k in ("status", "complete", "items", "violations", "macro_interior", "error")}))
    return 0 if summary["status"] in ("passed", "recorded") else 1


if __name__ == "__main__":
    raise SystemExit(main())

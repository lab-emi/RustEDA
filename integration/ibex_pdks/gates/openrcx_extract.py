#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Extract nominal parasitics of a final DEF with OpenRCX and the PDK's pinned rules.

Input schema `rusteda.pdk-gate.openrcx.v1`:
  top        design name the DEF must carry
  files      def, tech_lef, rules_nom (pinned records); lefs: ordered pinned cell and macro LEFs
  tools      {openroad, gnu_time}
  budgets    threads, timeout_seconds, max_spef_bytes, max_nets, max_log_bytes

The same commands as the IHP recipe's `reference_extract.py` (algorithm version 1, one nominal
corner, no option overrides), with the top name and the LEF list taken from the input. No net is
padded; the SPEF must name the top and close every `*D_NET`. The result status is
`extracted_not_qualified`: whether every active net is annotated is checked by the timing gate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCHEMA = "rusteda.pdk-gate.openrcx.v1"


def quote(value) -> str:
    value = str(value)
    common.require("\x00" not in value and len(value) <= 4096, "TCL_LITERAL")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace(
        "[", "\\[").replace("]", "\\]").replace("\n", "\\n") + '"'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    config = common.read_json(args.inputs, 2 * common.MIB)
    output = common.fresh_workspace(args.workspace, args.base_rev)
    result = dict(status="failed", extraction_finished=False)
    try:
        common.require(set(config) == {"schema", "top", "files", "tools", "budgets"} and config["schema"] == SCHEMA,
                       "INPUT_SCHEMA")
        top = config["top"]
        common.require(top.isascii() and top.replace("_", "").isalnum(), "TOP_HANDLE")
        files, budgets = config["files"], config["budgets"]
        common.require(set(files) == {"def", "tech_lef", "lefs", "rules_nom"} and 1 <= len(files["lefs"]) <= 16, "FILE_KEYS")
        common.require(set(budgets) == {"threads", "timeout_seconds", "max_spef_bytes", "max_nets", "max_log_bytes"},
                       "BUDGET_KEYS")
        records = [files["def"], files["tech_lef"], files["rules_nom"], *files["lefs"]]
        paths = [common.verify(record, "file") for record in records]
        tools = {name: common.verify(record, name) for name, record in config["tools"].items()}
        common.require(set(tools) == {"openroad", "gnu_time"}, "TOOL_KEYS")
        spef = output / "nom.spef"
        lines = [f"set_thread_count {int(budgets['threads'])}", "read_lef " + quote(files["tech_lef"]["path"])]
        lines += ["read_lef " + quote(record["path"]) for record in files["lefs"]]
        lines += ["read_def " + quote(files["def"]["path"]),
                  "if {[[ord::get_db_block] getName] ne " + quote(top) + '} {error "DEF top differs"}',
                  "define_process_corner -ext_model_index 0 nom",
                  "extract_parasitics -version 1 -ext_model_file " + quote(files["rules_nom"]["path"]) + " -corner_cnt 1",
                  "write_spef " + quote(spef)]
        script = output / "extract.tcl"
        script.write_text("if {[catch {\n" + "\n".join(lines) + "\n} message options]} {\n puts stderr $message\n"
                          " puts stderr [dict get $options -errorinfo]\n exit 2\n}\nexit 0\n")
        run = common.run_tool([tools["openroad"], "-exit", script], output / "extract.log", output / "extract.time",
                              tools["gnu_time"], int(budgets["timeout_seconds"]), log_cap=int(budgets["max_log_bytes"]))
        common.require(run["exit_code"] == 0 and not run["killed"], f"OPENRCX_FAILED: exit {run['exit_code']} {run['killed'] or ''}")
        record = common.identity(spef, int(budgets["max_spef_bytes"]))
        nets, ends, design = 0, 0, None
        with spef.open() as stream:
            for line in stream:
                if line.startswith("*DESIGN "):
                    common.require(design is None, "SPEF_DUPLICATE_DESIGN")
                    design = line.split(None, 1)[1].strip().strip('"')
                elif line.startswith("*D_NET "):
                    nets += 1
                    common.require(nets <= int(budgets["max_nets"]), "SPEF_NET_BUDGET")
                elif line.strip() == "*END":
                    ends += 1
        common.require(design == top and nets > 0 and ends == nets, "SPEF_INCOMPLETE_OR_FOREIGN")
        for item in records:
            common.verify(item, "file after run")
        result = dict(status="extracted_not_qualified", extraction_finished=True,
                      scope="geometry present in supplied DEF; active full-net coverage is not yet verified",
                      top=design, rc_corner="nom", source="openrcx", spef=record, annotated_net_records=nets,
                      extraction_options=dict(algorithm_version=1, via_merge="not_overridden",
                                              coupling_threshold="not_overridden"),
                      files=files, tools=config["tools"], wall_seconds=run["wall_seconds"],
                      peak_rss_kib=run["peak_rss_kib"],
                      unverified=["active_net_annotation", "LVS", "setup", "hold", "DRV", "timing_accuracy"])
    except (common.GateError, OSError, KeyError, ValueError) as error:
        result.update(error=str(error))
    common.save_json(output / "result.json", result)
    print(json.dumps({k: result.get(k) for k in ("status", "annotated_net_records", "error")}))
    return 0 if result["status"] == "extracted_not_qualified" else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Gate-level simulation of a final netlist with the vendor cell models and the macro model.

Input schema `rusteda.pdk-gate.simulation.v1`:
  top            design name
  files          netlist, tb, rtl, physical_lef, std_tt, std_ss, ram_tt, ram_ss, physical_policy
  vendor         ordered pinned Verilog models (cell library, primitives, macro model)
  model_edits    disclosed, mechanical edits of vendor models that Icarus needs, each
                 {file: index into vendor, hoist_line, before}: the one line `hoist_line` is moved
                 in front of the first line equal to `before` (a declaration used before it is
                 declared); the edited copy lives in the workspace, the vendor file is not touched
  defines        preprocessor defines for the vendor models; include_dirs
  macros         {master: [instance names]}
  pass_line      the line the test bench prints on success
  tools          {yosys, iverilog, vvp, gnu_time}

As in the IHP recipe's `reference_simulation.py`: Yosys reads the netlist to a structural JSON
(it must be flat and within the instance and pin budgets), cells that the LEF declares with
supply pins only and that have no Liberty model (the pinned policy of `signoff_timing.py`) get
empty declarations unless the vendor models define them, and Icarus compiles the test bench, the netlist and the original models.
The gate passes when the simulation ends with the pass line. It is a bounded functional test,
not an equivalence proof.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCHEMA = "rusteda.pdk-gate.simulation.v1"
FILES = {"netlist", "tb", "rtl", "physical_lef", "std_tt", "std_ss", "ram_tt", "ram_ss", "physical_policy"}


def edited_model(source, edit, target) -> dict:
    text = Path(source).read_text()
    line, anchor = edit["hoist_line"] + "\n", edit["before"] + "\n"
    common.require(text.count(line) == 1 and anchor in text and text.index(anchor) < text.index(line), "MODEL_EDIT_ANCHORS")
    body = text.replace(line, "")
    position = body.index(anchor)
    target.write_text(body[:position] + line + body[position:])
    return dict(source=common.identity(source), derived=common.identity(target), rule="one declaration line moved "
                "in front of its first use; no other byte changed", hoist_line=edit["hoist_line"], before=edit["before"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    config = common.read_json(args.inputs, 2 * common.MIB)
    output = common.fresh_workspace(args.workspace, args.base_rev)
    result = dict(schema="rusteda.pdk-gate.simulation-result.v1", status="failed", complete=False, stages=[])
    try:
        common.require(config.get("schema") == SCHEMA and set(config["files"]) == FILES, "INPUT_SCHEMA")
        files, tools, top = config["files"], config["tools"], config["top"]
        paths = {name: common.verify(record, name) for name, record in files.items()}
        vendor = [common.verify(record, "vendor model") for record in config["vendor"]]
        tool = {name: common.verify(record, name) for name, record in tools.items()}

        def stage(name, command, seconds):
            run = common.run_tool(command, output / (name + ".log"), output / (name + ".time"), tool["gnu_time"], seconds,
                                  log_cap=64 * common.MIB)
            result["stages"].append(dict(stage=name, exit_code=run["exit_code"], killed=run["killed"],
                                         wall_seconds=run["wall_seconds"], peak_rss_kib=run["peak_rss_kib"]))
            common.require(run["exit_code"] == 0 and not run["killed"], f"STAGE_FAILED: {name}")

        quoted = lambda value: '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'  # noqa: E731
        script = output / "structural.ys"
        script.write_text(f"read_verilog {quoted(paths['netlist'])}\nhierarchy -top {top}\nwrite_json {quoted(output / 'netlist.json')}\n")
        stage("structural", [tool["yosys"], "-s", script], 600)
        raw = common.read_json(output / "netlist.json", 256 * common.MIB)
        common.require(set(raw.get("modules", {})) == {top}, "NETLIST_NOT_FLAT")
        cells = raw["modules"][top].get("cells", {})
        common.require(1 <= len(cells) <= 500000, "INSTANCE_BUDGET")
        common.require(sum(sum(len(bits) for bits in cell["connections"].values()) for cell in cells.values()) <= 4_000_000,
                       "PIN_BIT_BUDGET")
        spec = importlib.util.spec_from_file_location("rusteda_pinned_physical_policy", paths["physical_policy"])
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        context = SimpleNamespace(files={key: paths[key] for key in
                                         ("physical_lef", "netlist", "std_tt", "std_ss", "ram_tt", "ram_ss")},
                                  root=output, result={})
        policy.Run.physical_models(context)
        physical = set(context.result["physical_only_models"]["masters"])
        for cell in cells.values():
            common.require(cell["type"] not in physical or not (cell.get("connections") or cell.get("parameters")),
                           "PHYSICAL_ONLY_DECLARATION_WOULD_REPLACE_A_CONNECTED_INSTANCE")
        macros = {}
        for master, instances in config["macros"].items():
            found = sorted(name for name, cell in cells.items() if cell["type"] == master)
            macros[master] = dict(expected=sorted(instances), found=found)
            common.require(found == sorted(instances), f"MACRO_INSTANCE_SET: {master}")
        edits = []
        for edit in config.get("model_edits", []):
            index = edit["file"]
            target = output / ("edited-" + vendor[index].name)
            edits.append(edited_model(vendor[index], edit, target))
            vendor[index] = target
        # Declarations only for supply-only cells the vendor models do not define themselves.
        declared = set()
        for path in vendor:
            declared.update(re.findall(r"^\s*(?:module|macromodule)\s+([A-Za-z_][A-Za-z0-9_$]*)", Path(path).read_text(errors="replace"), re.M))
        stubs = sorted(physical - declared)
        (output / "physical-only-sim.v").write_text("".join("module " + name + "(); endmodule\n" for name in stubs))
        command = [tool["iverilog"], "-g2012", "-s", "tb"]
        for define in config.get("defines", []):
            command.append("-D" + define)
        for directory in config.get("include_dirs", []):
            command += ["-I", directory]
        command += ["-o", output / "sim", paths["tb"], paths["netlist"], output / "physical-only-sim.v", *vendor]
        common.save_json(output / "compile-command.json", [str(c) for c in command])
        stage("compile", command, 600)
        stage("simulate", [tool["vvp"], output / "sim"], 1800)
        text = (output / "simulate.log").read_text(errors="replace")
        common.require(config["pass_line"] in text.splitlines(), "TEST_BENCH_DID_NOT_PASS")
        for record in (*files.values(), *config["vendor"], *tools.values()):
            common.verify(record, "input after run")
        result.update(status="passed", complete=True, top=top, netlist_sha256=files["netlist"]["sha256"],
                      original_rtl_sha256=files["rtl"]["sha256"], testbench=files["tb"], instances=len(cells),
                      macros=macros, physical_only_models=context.result["physical_only_models"],
                      empty_declarations=stubs, model_edits=edits,
                      defines=config.get("defines", []), vendor=config["vendor"], tools=tools,
                      log=common.identity(output / "simulate.log"),
                      scope="vendor cell and macro models, both memory banks at their boundary addresses, byte masks and "
                            "an Ibex program; bounded simulation, no equivalence claim")
    except (common.GateError, OSError, KeyError, ValueError, TypeError) as error:
        result.update(status="failed", complete=False, error=str(error))
        log = output / "simulate.log"
        if log.is_file():
            result["log_tail"] = log.read_text(errors="replace")[-800:]
    common.save_json(output / "summary.json", result)
    print(json.dumps({k: result.get(k) for k in ("status", "complete", "error")}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

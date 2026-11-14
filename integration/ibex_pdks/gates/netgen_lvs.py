#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Black-box-macro LVS: Magic extraction of the final GDS against the powered netlist in Netgen.

Input schema `rusteda.pdk-gate.netgen-lvs.v1`:
  top, gds, powered_netlist   pinned records
  magicrc, tech_files, pdk_root, abstract_lefs, abstract_cells   as for `magic_drc.py`
  netgen_setup                pinned PDK Netgen setup file
  cell_spice                  pinned device-level SPICE libraries of the standard cells
  macros                      {master: instance count}
  netgen_env                  environment switches the PDK setup file reads (profile data)
  tools                       {magic, netgen, gnu_time}; timeout_seconds

Scope. Standard cells are compared at device level: Magic extracts their transistors from the
final GDS and Netgen matches them against the PDK cell SPICE. Macro masters are black boxes on
both sides: in the layout they are the LEF abstract view (ports = LEF pins), in the reference a
subcircuit declaration with the same LEF pins. Netgen therefore checks, for every macro
instance, that each pin is on the net the powered netlist names; it does not look inside a
macro. Which device-less cells Netgen ignores is decided by the PDK setup file alone.

The verdict is `passed` when the layout holds the expected number of instances of each macro with
exactly the LEF pins, Netgen matched each macro as a black box pin by pin and counted the same
instances on both sides, Magic reports no shorted ports, the top circuits were compared, every
compared circuit is clean (devices, nets, pins, properties) and the final result is a unique
match without port or property errors.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCHEMA = "rusteda.pdk-gate.netgen-lvs.v1"
HERE = Path(__file__).resolve().parent


def subcircuits(path) -> dict:
    """{name: {ports, cards, uses}} of a SPICE netlist (continuation lines joined)."""
    result, current, card = {}, None, None

    def finish(text):
        nonlocal current
        if text is None:
            return
        tokens = text.split()
        if not tokens:
            return
        head = tokens[0].lower()
        if head == ".subckt":
            common.require(current is None and len(tokens) >= 2 and tokens[1] not in result, "SPICE_SUBCKT_NESTING")
            current = tokens[1]
            result[current] = dict(ports=[t for t in tokens[2:] if "=" not in t], cards=0, uses=Counter())
        elif head == ".ends":
            common.require(current is not None, "SPICE_ENDS_WITHOUT_SUBCKT")
            current = None
        elif current is not None and not head.startswith(("*", ".")):
            result[current]["cards"] += 1
            if head.startswith("x"):
                plain = [t for t in tokens[1:] if "=" not in t]
                common.require(bool(plain), "SPICE_INSTANCE_WITHOUT_MODEL")
                result[current]["uses"][plain[-1]] += 1

    with Path(path).open(errors="replace") as stream:
        for raw in stream:
            line = raw.rstrip("\n")
            if line.startswith("+"):
                common.require(card is not None, "SPICE_CONTINUATION")
                card += " " + line[1:]
            else:
                finish(card)
                card = line
        finish(card)
    common.require(current is None, "SPICE_UNFINISHED_SUBCKT")
    return result


def verilog_modules(path) -> list:
    text = Path(path).read_text(errors="replace")
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return [name.lstrip("\\") for name in re.findall(r"\b(?:module|macromodule)\s+(\\\S+|[A-Za-z_][\w$]*)", text)]


def netgen_facts(comp_json, comp_out, top) -> dict:
    data = json.loads(comp_json)
    common.require(isinstance(data, list) and data, "NETGEN_JSON_EMPTY")
    problems, top_seen, circuits = [], False, 0
    for entry in data:
        name = entry.get("name")
        label = name[0] if isinstance(name, list) and name else "<pins>"
        if isinstance(name, list) and len(name) == 2 and name[0] == top and name[1] == top:
            top_seen = True
        circuits += 1
        devices = entry.get("devices")
        if devices is not None:
            left, right = (Counter({d[0]: d[1] for d in side}) for side in devices)
            if left != right:
                problems.append(f"{label}: device counts differ")
        nets = entry.get("nets")
        if nets is not None and len(set(nets)) != 1:
            problems.append(f"{label}: net counts differ {nets}")
        for key in ("badnets", "badelements"):
            if entry.get(key):
                problems.append(f"{label}: {len(entry[key])} {key}")
        if entry.get("properties"):
            problems.append(f"{label}: {len(entry['properties'])} device property mismatches")
        pins = entry.get("pins")
        if pins is not None and (len(pins) != 2 or sorted(map(str, pins[0])) != sorted(map(str, pins[1]))):
            problems.append(f"{label}: pin lists differ")
    finals = re.findall(r"^Final result:\s*(.*)$", comp_out, re.M)
    final = finals[-1].strip() if finals else None
    if not top_seen:
        problems.append(f"top circuit {top} not compared")
    if final != "Circuits match uniquely.":
        problems.append(f"final result {final!r}")
    # A pin of the top circuit without any connection is an error (a labelled floating shape, an
    # unused reference port). Inside a library cell it is a property of that cell (the well pins
    # of a tie cell) and is only counted.
    for pattern, label in ((r"^.*\bproperty errors\b.*$", "property errors"), (r"^.*\bport errors?\b.*$", "port errors"),
                           (r"^.*\(no matching pin\).*$", "pins without a match"),
                           (r"^Cell\s+" + re.escape(top) + r"\s+\(\d+\)\s+disconnected node:.*$",
                            "disconnected nodes of the top circuit")):
        lines = re.findall(pattern, comp_out, re.M | re.I)
        if lines:
            problems.append(f"{len(lines)} lines report {label}, e.g. {lines[0].strip()[:160]!r}")
    library = Counter(re.findall(r"^Cell\s+(\S+)\s+\(\d+\)\s+disconnected node:", comp_out, re.M))
    library.pop(top, None)
    top_devices = [{}, {}]
    for entry in data:
        name = entry.get("name")
        if isinstance(name, list) and name == [top, top] and entry.get("devices"):
            top_devices = [{d[0]: d[1] for d in side} for side in entry["devices"]]
    return dict(final_result=final, circuits=circuits, problems=problems, top_compared=top_seen,
                library_cells_with_unconnected_pins=dict(sorted(library.items())), top_devices=top_devices)


def black_box_pins(comp_out, master):
    """Netgen's pin table of a black-box cell: (layout pins, reference pins, tables equivalent)."""
    block = re.search(r"^Subcircuit pins:\s*\nCircuit 1: " + re.escape(master) + r"\s*\|Circuit 2: " + re.escape(master)
                      + r"\s*\n-+\|-+\n(.*?)\n-{20,}\n([^\n]*)\n([^\n]*)", comp_out, re.M | re.S)
    if block is None:
        return None
    left, right = [], []
    for row in block[1].splitlines():
        a, _, b = row.partition("|")
        left.append(a.strip())
        right.append(b.strip())
    equivalent = (block[2].strip() == "Cell pin lists are equivalent."
                  and block[3].strip() == f"Device classes {master} and {master} are equivalent.")
    return left, right, equivalent


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
    common.require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top) is not None, "TOP_HANDLE")
    gds = common.verify(config["gds"], "gds")
    reference = common.verify(config["powered_netlist"], "powered netlist")
    setup = common.verify(config["netgen_setup"], "netgen setup")
    cells = [common.verify(record, "cell SPICE") for record in config["cell_spice"]]
    tools = {name: common.verify(record, name) for name, record in config["tools"].items()}
    magicrc, env = common.magic_setup(config, tools)
    macros = config["macros"]
    common.require(set(macros) == set(config["abstract_cells"]), "MACRO_SET")
    summary = dict(schema="rusteda.pdk-gate.netgen-lvs-result.v1", status="failed", complete=False, top=top,
                   gds=config["gds"], powered_netlist=config["powered_netlist"], netgen_setup=config["netgen_setup"],
                   cell_spice=config["cell_spice"], magicrc=config["magicrc"], tech_files=config["tech_files"],
                   abstract_lefs=config["abstract_lefs"], tools=config["tools"],
                   scripts=[common.identity(HERE / name) for name in ("magic_extract.tcl", "netgen_lvs.tcl")],
                   black_box_cells=sorted(macros), black_box_basis="LEF abstract view in the layout, LEF pin list in the reference",
                   scope="device-level LVS of standard cells; macros are black boxes with every instance pin checked",
                   stages=[])
    try:
        modules = verilog_modules(reference)
        common.require(modules == [top], f"REFERENCE_MODULES: expected exactly [{top}], found {modules[:4]}")
        lef = common.lef_macros([Path(record["path"]) for record in config["abstract_lefs"]]) if macros else {}
        common.require(set(macros) <= set(lef), "MACRO_WITHOUT_LEF")
        scratch = output / "ext"
        scratch.mkdir()
        layout = output / "layout.spice"
        env.update(GATE_GDS=str(gds), GATE_TOP=top, GATE_DIR=str(scratch), GATE_OUT=str(layout))
        seconds = int(config["timeout_seconds"])
        run = common.run_tool([tools["magic"], "-dnull", "-noconsole", "-rcfile", magicrc, HERE / "magic_extract.tcl"],
                              output / "magic.log", output / "magic.time", tools["gnu_time"], seconds, env=env, cwd=output)
        summary["stages"].append(dict(stage="magic_extract", exit_code=run["exit_code"], killed=run["killed"],
                                      wall_seconds=run["wall_seconds"], peak_rss_kib=run["peak_rss_kib"]))
        common.require(run["exit_code"] == 0 and not run["killed"], f"MAGIC_RUN_FAILED: exit {run['exit_code']} {run['killed'] or ''}")
        log = (output / "magic.log").read_text(errors="replace")
        facts = common.magic_facts(log, config["abstract_cells"])
        common.require(layout.is_file() and re.search(r"^GATE_EXTRACTED\s*$", log, re.M) is not None, "EXTRACTION_INCOMPLETE")
        shorted = re.findall(r'Ports\s+"([^"]*)"\s+and\s+"([^"]*)"\s+are electrically shorted', log)
        extracted = subcircuits(layout)
        common.require(top in extracted, "LAYOUT_TOP_SUBCKT_MISSING")
        interface = {}
        for master, count in macros.items():
            pins = list(lef[master]["pins"])
            found = extracted[top]["uses"].get(master, 0)
            entry = dict(lef_pins=len(pins), expected_instances=count, layout_instances=found)
            if master in extracted:
                entry.update(layout_ports=len(extracted[master]["ports"]), layout_cards=extracted[master]["cards"],
                             layout_only_ports=sorted(set(extracted[master]["ports"]) - set(pins))[:20],
                             lef_only_pins=sorted(set(pins) - set(extracted[master]["ports"]))[:20])
            interface[master] = entry
        summary.update(technology=facts["tech"], layout_netlist=common.identity(layout), macro_interface=interface,
                       shorted_ports=[list(pair) for pair in shorted[:50]], shorted_port_count=len(shorted),
                       layout_top_ports=len(extracted[top]["ports"]), layout_top_cards=extracted[top]["cards"])
        black = output / "macro-blackbox.spice"
        black.write_text("* Macro black-box declarations: the pins of each macro's LEF, in LEF order.\n" + "".join(
            ".subckt " + master + " " + " ".join(lef[master]["pins"]) + "\n.ends\n" for master in sorted(macros)))
        env_netgen = dict(env, GATE_LAYOUT=str(layout), GATE_REF=str(reference), GATE_SETUP=str(setup),
                          GATE_OUT=str(output / "comp.out"), GATE_CELLS=":".join(map(str, [*cells, black])))
        for name, value in config.get("netgen_env", {}).items():
            common.require(re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is not None and not name.startswith("GATE_"), "NETGEN_ENV_NAME")
            env_netgen[name] = str(value)
        run = common.run_tool([tools["netgen"], "-batch", "source", HERE / "netgen_lvs.tcl"],
                              output / "netgen.log", output / "netgen.time", tools["gnu_time"], seconds,
                              env=env_netgen, cwd=output)
        summary["stages"].append(dict(stage="netgen", exit_code=run["exit_code"], killed=run["killed"],
                                      wall_seconds=run["wall_seconds"], peak_rss_kib=run["peak_rss_kib"]))
        common.require(run["exit_code"] == 0 and not run["killed"], f"NETGEN_RUN_FAILED: exit {run['exit_code']} {run['killed'] or ''}")
        comp_out, comp_json = output / "comp.out", output / "comp.json"
        common.require(comp_out.is_file() and comp_json.is_file(), "NETGEN_RESULT_MISSING")
        result = netgen_facts(comp_json.read_text(errors="replace"), comp_out.read_text(errors="replace"), top)
        problems = list(result["problems"])
        for master, entry in interface.items():
            if entry["layout_instances"] != entry["expected_instances"]:
                problems.append(f"{master}: {entry['layout_instances']} layout instances, expected {entry['expected_instances']}")
            if master not in extracted:
                problems.append(f"{master}: no black-box subcircuit in the extracted layout")
            elif entry["layout_cards"] or entry["layout_only_ports"] or entry["lef_only_pins"]:
                problems.append(f"{master}: extracted interface differs from the LEF pins")
            pins = black_box_pins(comp_out.read_text(errors="replace"), master)
            expected = sorted(lef[master]["pins"])
            if pins is None or not pins[2] or sorted(pins[0]) != expected or sorted(pins[1]) != expected:
                problems.append(f"{master}: Netgen did not match the black box pin by pin with the LEF pins")
            if [side.get(master) for side in result["top_devices"]] != [entry["expected_instances"]] * 2:
                problems.append(f"{master}: Netgen counted {[side.get(master) for side in result['top_devices']]} instances")
            entry["netgen_pin_table"] = None if pins is None else dict(pins=len(pins[0]), equivalent=pins[2])
        if shorted:
            problems.append(f"{len(shorted)} port pairs shorted in the layout, e.g. {shorted[0]}")
        summary.update(netgen=result, problems=problems, status="passed" if not problems else "failed", complete=True,
                       reports={name: common.identity(output / name) for name in ("comp.out", "comp.json")})
        for record in (config["gds"], config["powered_netlist"], config["netgen_setup"], *config["cell_spice"],
                       config["magicrc"], *config["tech_files"], *config["abstract_lefs"]):
            common.verify(record, "input after run")
    except (common.GateError, OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        summary.update(status="failed", complete=False, error=str(error))
    common.save_json(output / "summary.json", summary)
    print(json.dumps({k: summary.get(k) for k in ("status", "complete", "problems", "error")}))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

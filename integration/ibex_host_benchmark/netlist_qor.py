#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Measure mapped-netlist QoR with tools neutral to both producers.

For each named netlist this helper reports, from one shared toolchain:

* standard-cell area and cell counts from `yosys stat -liberty` (EZ130 TT and
  the SRAM TT Liberty), split into sequential / buffer+inverter / other cells;
* the longest combinational path in cells (`yosys ltp -noff`), independent of
  loads and of where a flow chooses to insert fanout buffers;
* ideal-wire (zero-RC) static timing from OpenSTA at TT and SS
  with the benchmark SDC: worst setup and hold slack, setup TNS, and the
  worst data arrival. Pre-placement timing is diagnostic only: a flow that
  defers fanout buffering to placement (LibreLane/OpenROAD `repair_design`)
  shows unbuffered high-fanout nets here, so compare timing on final layouts.

The netlists are read as produced; nothing is re-optimized. Output: one JSON
document on stdout. Bounded to 16 netlists of at most 64 MiB each.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

MACRO = "RM_IHPSG13_1P_1024x32_c2_bm_bist"
MACRO_AREA_UM2 = 416.64 * 336.46  # LEF SIZE; equals the SRAM Liberty area attribute (140182.6944)
SEQ = re.compile(r"^(DFF|SDFF|LAT|DLAT|DLY_?FF)", re.I)
BUF = re.compile(r"^(BUF|INV|CLKBUF|CLKINV|DLY)", re.I)
PHYS = re.compile(r"^(FILLER|DECAP|WELLTAP|TAP|ANTENNA)", re.I)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw).stdout


def yosys_stat(yosys, netlist, libs, top):
    script = "".join(f"read_liberty -lib {l}\n" for l in libs)
    script += f"read_verilog {netlist}\nhierarchy -top {top}\n"
    script += "tee -o /dev/stdout stat -json " + " ".join(f"-liberty {l}" for l in libs) + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".ys", delete=False) as handle:
        handle.write(script)
    out = run([yosys, "-q", "-s", handle.name])
    start = out.index("{")
    data = json.loads(out[start:out.rindex("}") + 1])
    module = data["modules"][f"\\{top}"] if f"\\{top}" in data["modules"] else data["modules"][top]
    cells = {k.lstrip("\\"): v for k, v in module["num_cells_by_type"].items()}
    counts = dict(total=0, sequential=0, buffer_inverter=0, physical=0, macro=0, other=0)
    for name, count in cells.items():
        key = ("macro" if name == MACRO else "physical" if PHYS.match(name) else
               "sequential" if SEQ.match(name) else "buffer_inverter" if BUF.match(name) else "other")
        counts[key] += count
        counts["total"] += count
    depth = logic_depth(yosys, netlist, libs, top)
    macro_area = cells.get(MACRO, 0) * MACRO_AREA_UM2
    area = module.get("area")
    return dict(area_um2_including_macros=area, standard_cell_area_um2=round(area - macro_area, 3) if area else None,
                logic_depth=depth, cells=counts, by_type=cells)


def logic_depth(yosys, netlist, libs, top):
    """Longest combinational path between sequential cells, macros and ports.

    Computed on the Yosys JSON netlist with Liberty port directions. Reports
    the path length counting every cell, and counting only non-buffer,
    non-inverter cells (fanout trees inserted by synthesis weigh zero).
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        path = handle.name
    run([yosys, "-q", "-p", "".join(f"read_liberty -lib {l}; " for l in libs)
         + f"read_verilog {netlist}; hierarchy -top {top}; write_json {path}"])
    data = json.loads(Path(path).read_text())
    module = data["modules"][top]
    drivers, loads = {}, {}
    cut = lambda kind: kind == MACRO or SEQ.match(kind) is not None
    for name, cell in module["cells"].items():
        dirs = cell.get("port_directions", {})
        for port, bits in cell["connections"].items():
            for bit in bits:
                if not isinstance(bit, int):
                    continue
                if dirs.get(port) == "output":
                    drivers[bit] = name
                elif dirs.get(port) == "input":
                    loads.setdefault(bit, []).append(name)
    succ = {name: set() for name in module["cells"]}
    for bit, driver in drivers.items():
        for load in loads.get(bit, ()):
            succ[driver].add(load)
    kind = {name: cell["type"].lstrip("\\") for name, cell in module["cells"].items()}
    order, state = [], {}
    for start in succ:
        if state.get(start):
            continue
        stack = [(start, iter(succ[start]))]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                stack.pop(); state[node] = 2; order.append(node)
            elif not state.get(nxt) and not cut(kind[nxt]):
                state[nxt] = 1; stack.append((nxt, iter(succ[nxt])))
    best = {}
    for weighted in (False, True):
        dist = {}
        for node in order:  # reverse topological (post-order): successors first
            if cut(kind[node]):
                continue
            w = 0 if weighted and BUF.match(kind[node]) else 1
            tail = max((dist.get(s, 0) for s in succ[node] if not cut(kind[s])), default=0)
            dist[node] = w + tail
        best["logic_cells_excluding_buffers" if weighted else "cells"] = max(dist.values(), default=0)
    return best


def opensta(sta, netlist, libs_by_corner, sdc, top):
    out = {}
    for corner, libs in libs_by_corner.items():
        tcl = "".join(f"read_liberty {l}\n" for l in libs)
        tcl += (f"read_verilog {netlist}\nlink_design {top}\nread_sdc {sdc}\n"
                "puts \"WNS_SETUP [sta::worst_slack_cmd max]\"\n"
                "puts \"WNS_HOLD [sta::worst_slack_cmd min]\"\n"
                "puts \"TNS_SETUP [sta::total_negative_slack_cmd max]\"\n"
                "set paths [find_timing_paths -path_delay max -group_path_count 200 -sort_by_slack]\n"
                "set worst 0\n"
                "foreach p $paths { set a [$p data_arrival_time]; if {$a > $worst} {set worst $a} }\n"
                "puts \"MAX_ARRIVAL $worst\"\nexit\n")
        with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as handle:
            handle.write(tcl)
        text = run([sta, "-no_init", "-no_splash", "-exit", handle.name])
        # OpenSTA's Tcl API returns SI seconds; report nanoseconds.
        get = lambda k: round(float(re.search(rf"^{k} (\S+)", text, re.M)[1]) * 1e9, 4)
        out[corner] = dict(setup_wns_ns=get("WNS_SETUP"), hold_wns_ns=get("WNS_HOLD"),
                           setup_tns_ns=get("TNS_SETUP"), max_data_arrival_ns=get("MAX_ARRIVAL"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", action="append", required=True, help="NAME=PATH")
    parser.add_argument("--yosys", required=True)
    parser.add_argument("--sta", required=True)
    parser.add_argument("--lib-tt", action="append", required=True)
    parser.add_argument("--lib-ss", action="append", required=True)
    parser.add_argument("--sdc", required=True)
    parser.add_argument("--top", default="ibex_sram_bench")
    args = parser.parse_args()
    if len(args.netlist) > 16:
        raise SystemExit("netlist budget exceeded")
    result = {}
    for item in args.netlist:
        name, path = item.split("=", 1)
        if Path(path).stat().st_size > 64 << 20:
            raise SystemExit("netlist byte budget exceeded")
        result[name] = dict(path=str(Path(path).resolve()),
                            yosys_stat=yosys_stat(args.yosys, path, args.lib_tt, args.top),
                            ideal_timing=opensta(args.sta, path, {"tt": args.lib_tt, "ss": args.lib_ss},
                                                 args.sdc, args.top))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

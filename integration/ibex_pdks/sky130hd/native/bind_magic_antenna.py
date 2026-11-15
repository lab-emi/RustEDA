#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Bind completed SKY130 Magic antenna markers to pins in the routed DEF.

Use a producer receipt containing exact GDS and DEF identities. The output
reports any unbound markers and leaves every input untouched.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from functools import lru_cache
import json
from pathlib import Path
import shlex
import sys

INTEGRATION = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(INTEGRATION / "ibex_pdks/gates"))
sys.path.insert(0, str(INTEGRATION / "ibex_sg13g2_ez130"))
import common  # noqa: E402
from estimate_spef import read_def  # noqa: E402
from magic_antenna import VIOLATION, observations  # noqa: E402

DBU_PER_UM = 1000
MAGIC_UNIT_DBU = 5
STANDARD_HEIGHT_DBU = 2720
MAX_MARKERS = 4096


def read_transforms(ext):
    """Index simple orientations by Magic use name and by exact placement."""
    by_name = {}
    by_site = defaultdict(list)
    with ext.open() as stream:
        for line in stream:
            if not line.startswith("use "):
                continue
            fields = line.split()
            if len(fields) != 9:
                continue
            master, name = fields[1:3]
            a, b, x, d, e, y = map(int, fields[3:])
            if a != 1 or b != 0 or d != 0 or e not in (-1, 1):
                continue
            common.require(name not in by_name and len(by_name) < 250000, "MAGIC_USE_NAME_OR_COUNT")
            site = (master, x, y, e)
            by_name[name] = site
            by_site[site].append(name)
    return by_name, by_site


def placed_rows(components, masters):
    """Index nonoverlapping standard-cell row intervals in DEF units."""
    rows = defaultdict(list)
    for name, (master, x, y, orient) in components.items():
        facts = masters.get(master)
        if facts is None or orient not in ("N", "FS") or "CORE" not in facts["cls"]:
            continue
        width, height = (common.dbu(size, DBU_PER_UM) for size in facts["size"])
        if height == STANDARD_HEIGHT_DBU:
            rows[y].append((x, x + width, name, master, orient))
    x_starts = {}
    for y, row in rows.items():
        row.sort()
        common.require(all(a[1] <= b[0] for a, b in zip(row, row[1:])), "DEF_ROW_OVERLAP")
        x_starts[y] = [entry[0] for entry in row]
    return rows, sorted(rows), x_starts


def bind(gate_dir, producer, cell_lef):
    summary = common.read_json(gate_dir / "summary.json", 8 * common.MIB)
    common.require(summary.get("schema") == "rusteda.pdk-gate.magic-antenna-result.v1"
                   and summary.get("complete") and summary.get("top") == "ibex_sram_bench",
                   "MAGIC_ANTENNA_INCOMPLETE_OR_UNSUPPORTED")
    common.require(summary["engine"]["exit_code"] == 0 and not summary["engine"]["killed"], "MAGIC_ANTENNA_FAILED")
    gds, def_file = [common.verify(producer["artifacts"][kind], kind) for kind in ("gds", "def")]
    common.require(summary["gds"]["sha256"] == producer["artifacts"]["gds"]["sha256"], "MAGIC_GDS_MISMATCH")
    log_file = gate_dir / "magic.log"
    ext_dir = gate_dir / "ext"
    top_ext = ext_dir / "ibex_sram_bench.ext"
    common.require(log_file.stat().st_size <= 2 * common.MIB and top_ext.stat().st_size <= 64 * common.MIB
                   and def_file.stat().st_size <= 64 * common.MIB, "MAGIC_BINDING_INPUT_BUDGET")
    log = log_file.read_text(errors="replace")
    facts = observations(log, summary["abstract_cells"])
    common.require(facts["unit_um"] == MAGIC_UNIT_DBU / DBU_PER_UM and
                   facts["violations"] == summary["violations"] <= MAX_MARKERS, "MAGIC_UNITS_OR_COUNT")
    matches = list(VIOLATION.finditer(log))
    by_name, by_site = read_transforms(top_ext)
    masters = common.lef_macros([cell_lef])
    components, _, nets, units, name = read_def(def_file, 250000, 4096)
    common.require(name == "ibex_sram_bench" and units == DBU_PER_UM and len(components) <= 250000,
                   "SKY130_DEF_SCOPE")
    by_placement = {placement: name for name, placement in components.items()}
    common.require(len(by_placement) == len(components), "DEF_DUPLICATE_SITE")
    rows, y_starts, x_starts = placed_rows(components, masters)
    connections = {}
    for net, pins in nets:
        for terminal in pins:
            common.require(connections.setdefault(terminal, net) == net, "DEF_ALIAS_MISMATCH")
        common.require(len(connections) <= 1_000_000, "DEF_TERMINAL_BUDGET")

    @lru_cache(maxsize=512)
    def device_gates(master):
        ext = ext_dir / (master + ".ext")
        common.require(ext.resolve().parent == ext_dir.resolve() and ext.stat().st_size <= common.MIB,
                       "MAGIC_DEVICE_INPUT_BUDGET")
        devices = defaultdict(set)
        with ext.open() as stream:
            for line in stream:
                if not line.startswith("device msubckt "):
                    continue
                fields = shlex.split(line)
                if len(fields) > 10 and fields[7].startswith("l=") and fields[8].startswith("w="):
                    x, y = map(int, fields[3:5])
                    length, width = (int(field[2:]) for field in fields[7:9])
                    devices[x, y, x + length, y + width].add(fields[10])
        return devices

    def match_device(label, site, rect):
        master, x, y, orient_y = site
        if master not in masters:
            return None
        height = common.dbu(masters[master]["size"][1], DBU_PER_UM)
        offset_y = height if orient_y == -1 else 0
        placement = (master, x * MAGIC_UNIT_DBU, y * MAGIC_UNIT_DBU - offset_y,
                     "N" if orient_y == 1 else "FS")
        instance = by_placement.get(placement)
        lx, ly, ux, uy = rect
        endpoints_y = sorted(((ly - y) * orient_y, (uy - y) * orient_y))
        local_gate = (lx - x, endpoints_y[0], ux - x, endpoints_y[1])
        pins = device_gates(master).get(local_gate, set())
        if instance is None or len(pins) != 1:
            return None
        pin, = pins
        net = connections.get((instance, pin))
        return (label, instance, master, local_gate, pin, net) if net else None

    def locate_device(rect):
        lx, ly, ux, uy = (coordinate * MAGIC_UNIT_DBU for coordinate in rect)
        row_index = bisect_right(y_starts, ly) - 1
        if row_index < 0:
            return None
        y = y_starts[row_index]
        index = bisect_right(x_starts[y], lx) - 1
        if index < 0 or uy > y + STANDARD_HEIGHT_DBU:
            return None
        x, right, _, master, orient = rows[y][index]
        if ux > right or x % MAGIC_UNIT_DBU or y % MAGIC_UNIT_DBU:
            return None
        flipped = orient == "FS"
        site = (master, x // MAGIC_UNIT_DBU,
                (y + STANDARD_HEIGHT_DBU * flipped) // MAGIC_UNIT_DBU, -1 if flipped else 1)
        labels = by_site.get(site, [])
        return match_device(labels[0], site, rect) if len(labels) == 1 else None

    bound_rows, net_rows = [], defaultdict(list)
    for match in matches:
        reported, plane, ratio, limit, *coordinates = match.groups()
        rect = tuple(map(int, coordinates))
        direct = match_device(reported, by_name[reported], rect) if reported in by_name else None
        located = locate_device(rect)
        if direct and located:
            common.require(direct == located, "MAGIC_GATE_BINDING_DISAGREEMENT")
        binding = direct or located
        record = dict(plane=plane, effective_ratio=float(ratio), limit=float(limit),
                      gate_rect_magic_units=rect, magic_reported_instance=reported, status="unbound")
        if binding:
            magic_name, instance, master, local_gate, pin, net = binding
            record.update(magic_instance=magic_name, def_instance=instance, master=master,
                          local_gate_rect_magic_units=local_gate, candidate_pins=[pin], net=net,
                          status="bound", binding_method="tagged_instance" if direct else "exact_spatial_instance")
            net_rows[net].append(dict(plane=plane, instance=instance, pin=pin))
        bound_rows.append(record)
    return dict(scope="Completed Magic gate rectangles bound to exact extracted devices and routed DEF terminals",
                full_gate_complete=True, gds=common.identity(gds), final_def=common.identity(def_file),
                cell_lef=common.identity(cell_lef), magic_log=common.identity(log_file),
                magic_top_ext=common.identity(top_ext), observed_violations=len(bound_rows),
                counts=Counter(row["status"] for row in bound_rows), unique_nets=len(net_rows),
                binding_methods=Counter(row.get("binding_method", "unbound") for row in bound_rows),
                nets=dict(sorted(net_rows.items())), rows=bound_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-dir", type=Path, required=True)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--cell-lef", type=Path, required=True)
    args = parser.parse_args()
    report = bind(args.gate_dir, common.read_json(args.producer, 8 * common.MIB), args.cell_lef)
    print(json.dumps(report, indent=2))
    return 1 if report["counts"].get("unbound", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())

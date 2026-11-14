#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Final non-filler standard-cell utilization outside macro halos, from the DEF and the LEFs.

The quantity is the IHP recipe's (`final_density.utilization`): area of every placed cell that
is not a gap filler and not a macro, divided by the placement region minus the union of the macro halo boxes.
What differs is where the facts come from, so that one definition serves any flow:

- the database unit is read from the DEF `UNITS` statement;
- a master is a macro when its LEF class is `BLOCK`, a gap filler when its LEF class contains
  `SPACER` (fill and decap cells; compared without regard to case), and a counted standard cell
  otherwise (logic, buffers, taps, end caps, antenna diodes, tie cells);
- the placement region is the die of the reference configuration, which both flows share, and
  the DEF's `DIEAREA` must equal it;
- every macro instance of the reference configuration must be placed where that configuration
  puts it, with its orientation, and no other macro may exist.

`utilization()` returns the IHP result fields plus the per-class areas.
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def classify(tokens) -> str:
    if "BLOCK" in tokens:
        return "macro"
    if "SPACER" in tokens:
        return "filler"
    for name, label in (("WELLTAP", "tap"), ("ENDCAP", "endcap"), ("ANTENNACELL", "antenna"),
                        ("TIEHIGH", "tie"), ("TIELOW", "tie")):
        if name in tokens:
            return label
    return "logic"


def union_area(boxes) -> int:
    """Area covered by axis-parallel boxes [x0, y0, x1, y1]; neighbouring halos may overlap."""
    xs = sorted({v for box in boxes for v in (box[0], box[2])})
    ys = sorted({v for box in boxes for v in (box[1], box[3])})
    total = 0
    for left, right in zip(xs, xs[1:]):
        for bottom, top in zip(ys, ys[1:]):
            if any(box[0] <= left and right <= box[2] and box[1] <= bottom and top <= box[3] for box in boxes):
                total += (right - left) * (top - bottom)
    return total


def utilization(def_path, lef_paths, floorplan, maximum, top) -> dict:
    """floorplan: {die_um: [x0, y0, x1, y1], macro_halo_um, macros: {name: {cell, location_um, orientation}}}."""
    common.require(type(maximum) is int and 1 <= maximum <= 95, "DENSITY_LIMIT")
    design = common.def_design(def_path)
    lef = common.lef_macros(lef_paths)
    units = design["units"]
    common.require(design["top"] == top, "DEF_TOP")
    die = [common.dbu(v, units) for v in floorplan["die_um"]]
    common.require(design["die"] == die, f"DEF_DIE_DIFFERS_FROM_REFERENCE_CONFIGURATION: {design['die']} vs {die}")
    die_area = (die[2] - die[0]) * (die[3] - die[1])
    halo = common.dbu(floorplan["macro_halo_um"], units)
    common.require(die_area > 0 and halo >= 0, "FLOORPLAN_GEOMETRY")
    areas = dict(macro=0, filler=0, tap=0, endcap=0, antenna=0, tie=0, logic=0)
    counts, by_master, placed_macros = dict.fromkeys(areas, 0), {}, {}
    for name, component in design["components"].items():
        master = component["master"]
        common.require(master in lef, f"COMPONENT_WITHOUT_LEF: {master}")
        width, height = (common.dbu(v, units) for v in lef[master]["size"])
        common.require(width > 0 and height > 0, "LEF_FOOTPRINT")
        kind = classify(lef[master]["cls"])
        areas[kind] += width * height
        counts[kind] += 1
        by_master[master] = by_master.get(master, 0) + 1
        if kind == "macro":
            placed_macros[name] = component
    expected = floorplan["macros"]
    common.require(set(placed_macros) == set(expected), "MACRO_INSTANCE_SET_DIFFERS")
    boxes = []
    for name, component in placed_macros.items():
        want = expected[name]
        common.require(component["master"] == want["cell"] and component["orientation"] == want["orientation"]
                       and [component["x"], component["y"]] == [common.dbu(v, units) for v in want["location_um"]],
                       f"MACRO_PLACEMENT_DIFFERS: {name}")
        box = common.placed_box(component, lef[component["master"]]["size"], units)
        box = [max(die[0], box[0] - halo), max(die[1], box[1] - halo), min(die[2], box[2] + halo), min(die[3], box[3] + halo)]
        common.require(box[0] < box[2] and box[1] < box[3], "MACRO_OUTSIDE_DIE")
        boxes.append(box)
    reserved = union_area(boxes)
    occupied = sum(areas[k] for k in ("tap", "endcap", "antenna", "tie", "logic"))
    available = die_area - reserved
    common.require(available > 0, "AVAILABLE_AREA")
    passed = occupied * 100 <= maximum * available
    return dict(status="passed" if passed else "failed", max_percent=maximum,
                final_nonfiller_utilization_percent=100 * occupied / available,
                macro_inclusive_core_utilization_percent=100 * (occupied + areas["macro"]) / die_area,
                area_dbu2=dict(core=die_area, macros=areas["macro"], macro_halos_inclusive=reserved, available=available,
                               final_nonfiller_standard_cells=occupied, reported_separately_filler_decap=areas["filler"]),
                dbu_per_um=units, component_count=len(design["components"]), masters=dict(sorted(by_master.items())),
                placement_region="die of the reference configuration", region_um=floorplan["die_um"],
                macro_halo_um=floorplan["macro_halo_um"], class_area_dbu2=areas, class_count=counts,
                excluded_gap_fill_masters=sorted(m for m in by_master if classify(lef[m]["cls"]) == "filler"),
                definition="all final cell footprint except LEF class SPACER (fill, decap) and LEF class BLOCK (macros), "
                           "divided by the die minus the union of the macro halo boxes; classes read from the LEF, DBU from the DEF")

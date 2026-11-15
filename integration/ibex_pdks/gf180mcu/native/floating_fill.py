# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Emit the opt-in GF180MCU D Ibex Metal2 and top-metal floating fill.

Run with the benchmark's pinned KLayout from the repository checkout:

    klayout -b -r integration/ibex_pdks/gf180mcu/native/floating_fill.py \
        -rd input_gds=routed.gds -rd input_def=routed.def \
        -rd macro_lef=gf180mcu_fd_ip_sram__sram512x8m8wm1.lef \
        -rd output_gds=filled.gds

Use an unfilled routed GDS and its matching DEF. Output is published to a new
path, with a JSON geometry receipt on stdout. Run with a wall-time guard and
4 GiB of memory without swap. Independent foundry and parasitic checks are
separate. The selected geometry and die-density estimates have no timing or
power qualification.

The layout rules in the GF180 design manual, section 13.3, set the 2 µm square
size, 1.2 µm layout gap, 2 µm same-metal and 1 µm adjacent-metal clearances,
and 6 µm exclusion-marker clearance. Section 4.1 identifies drawing and
dummy purposes. Metal4 remains outside the scope of this benchmark emitter.
"""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import pya

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gates"))
import common


TOP = "ibex_sram_bench"
SRAM = "gf180mcu_fd_ip_sram__sram512x8m8wm1"
GDS_DBU_PER_UM = 1000
MAX_GDS_BYTES = 128 * common.MIB
MAX_DEF_BYTES = 64 * common.MIB
MAX_LEF_BYTES = common.MIB
MAX_INPUT_SHAPES = 1_700_000
MAX_CLEARANCE_VERTICES = 3_200_000
MAX_BOXES_PER_PHASE = 300_000
SIZE = 2000
PITCH = 3200
ROW_STAGGER = 500
DRAWING = 0
DUMMY = 4
EXCLUSION_MASKS = ((75, 0), (96, 1), (220, 0), (152, 5), (122, 5), (173, 5))
ROUTING_MASKS = (34, 36, 42, 46, 53, 81)


def gds_box(coordinates, def_units):
    converted = [divmod(value * GDS_DBU_PER_UM, def_units) for value in coordinates]
    common.require(all(remainder == 0 for _, remainder in converted), "DEF_OFF_GDS_GRID")
    return pya.Box(*(quotient for quotient, _ in converted))


def box_tuple(box):
    return (box.left, box.bottom, box.right, box.top)


def read_floorplan(input_def, macro_lef, top):
    design = common.def_design(input_def)
    units = design["units"]
    common.require(design["top"] == TOP and units > 0, "GF180_BENCHMARK_DEF")
    die = gds_box(design["die"], units)
    common.require(die.width() > 0 and die.height() > 0 and top.bbox() == die, "DEF_GDS_DIE_MISMATCH")
    lefs = common.lef_macros([macro_lef])
    bounds = common.macro_boxes(design, lefs, (SRAM,))
    names = {f"u_{bank}{index}" for bank in ("imem", "dmem") for index in range(4)}
    common.require(bounds.keys() == names, "GF180_BENCHMARK_MACRO_INSTANCES")
    boxes = [gds_box(entry["box"], units) for entry in bounds.values()]
    common.require(all(box.left >= die.left and box.right <= die.right
                       and box.bottom >= die.bottom and box.top <= die.top for box in boxes),
                   "GF180_MACRO_OUTSIDE_DIE")
    gds_boxes = [inst.bbox() for inst in top.each_inst() if inst.cell.name == SRAM]
    common.require(sorted(map(box_tuple, gds_boxes)) == sorted(map(box_tuple, boxes)),
                   "DEF_GDS_MACRO_BOXES_MISMATCH")
    keepout = pya.Region()
    for box in boxes:
        keepout.insert(box)
    return die, keepout.merged()


def read_masks(layout, top, purposes):
    remaining = MAX_INPUT_SHAPES
    masks = {}
    for number, purpose in purposes:
        index = layout.find_layer(number, purpose)
        region = pya.Region()
        if index is not None:
            source = top.begin_shapes_rec(index)
            while not source.at_end():
                remaining -= 1
                common.require(remaining >= 0, "GF180_FILL_INPUT_SHAPE_BUDGET")
                source.next()
            region = pya.Region(top.begin_shapes_rec(index)).merged()
        masks[number, purpose] = region
    return masks


def clearance(region, distance):
    # The square halo is conservative at diagonal corners for Euclidean rules.
    points = sum(shape.num_points() for shape in region.each_merged())
    common.require(points <= MAX_CLEARANCE_VERTICES, "GF180_FILL_CLEARANCE_VERTEX_BUDGET")
    return region.minkowski_sum(pya.Box(-distance, -distance, distance, distance)).merged()


def grid(die, offset_x, offset_y):
    result = pya.Region()
    count = 0
    for row, y in enumerate(range(die.bottom + offset_y, die.top - SIZE + 1, PITCH)):
        left = die.left + offset_x + (row % 2) * ROW_STAGGER
        for x in range(left, die.right - SIZE + 1, PITCH):
            count += 1
            common.require(count <= MAX_BOXES_PER_PHASE, "GF180_FILL_GRID_BOX_BUDGET")
            result.insert(pya.Box(x, y, x + SIZE, y + SIZE))
    return result


def choose_fill(die, layer, adjacent, masks, excluded, keepout):
    active = masks[layer, DRAWING]
    drawing_area = (active & pya.Region(die)).area()
    if 10 * drawing_area > 3 * die.area():
        return [], {"added_boxes": 0, "phase_dbu": None, "geometric_coverage_pct": 100 * drawing_area / die.area()}

    obstacles = keepout | clearance(active, 2000)
    for other in adjacent:
        obstacles |= clearance(masks[other, DRAWING], 1000)
    obstacles |= excluded
    best = []
    best_phase = None
    for offset_y in (0, 1000):
        for offset_x in (0, 500, 1000, 1500):
            remainder = grid(die, offset_x, offset_y) - obstacles
            boxes = []
            for polygon in remainder.each():
                bounds = polygon.bbox()
                if polygon.is_box() and bounds.width() == bounds.height() == SIZE:
                    boxes.append(bounds)
            if len(boxes) > len(best):
                best, best_phase = boxes, [offset_x, offset_y]

    area = drawing_area + len(best) * SIZE ** 2
    common.require(10 * area > 3 * die.area(), f"GF180_FILL_BELOW_GEOMETRIC_TARGET: layer {layer}")
    return best, {"added_boxes": len(best), "phase_dbu": best_phase, "geometric_coverage_pct": 100 * area / die.area()}


def emit(input_gds, input_def, macro_lef, output_gds):
    output = Path(output_gds).absolute()
    common.require(output.suffix.lower() == ".gds" and output.parent.is_dir(), "GF180_FILL_OUTPUT_PATH")
    common.require(not output.exists() and not output.is_symlink(), "OUTPUT_EXISTS")
    sources = dict(gds=common.identity(input_gds, MAX_GDS_BYTES), def_file=common.identity(input_def, MAX_DEF_BYTES),
                   macro_lef=common.identity(macro_lef, MAX_LEF_BYTES))
    layout = pya.Layout()
    layout.read(sources["gds"]["path"])
    top = layout.cell(TOP)
    common.require(layout.dbu == 1 / GDS_DBU_PER_UM and top is not None, "GF180_BENCHMARK_GDS")
    die, macro_keepout = read_floorplan(sources["def_file"]["path"], sources["macro_lef"]["path"], top)
    purposes = [(layer, DRAWING) for layer in (34, 36, 42, 46, 81)]
    purposes += [(layer, DUMMY) for layer in ROUTING_MASKS]
    purposes += EXCLUSION_MASKS
    masks = read_masks(layout, top, purposes)
    common.require(all(masks[layer, DUMMY].is_empty() for layer in ROUTING_MASKS), "GF180_GDS_ALREADY_FILLED")
    markers = pya.Region()
    for purpose in EXCLUSION_MASKS:
        markers |= masks[purpose]
    excluded = clearance(markers.merged(), 6000)
    reports = {}
    for layer, adjacent, keepout in ((36, (34, 42), macro_keepout), (81, (46,), pya.Region())):
        boxes, reports[f"{layer}/{DUMMY}"] = choose_fill(die, layer, adjacent, masks, excluded, keepout)
        if boxes:
            shapes = top.shapes(layout.layer(layer, DUMMY))
            for box in boxes:
                shapes.insert(box)
    for label, source in sources.items():
        common.verify(source, label)
    with TemporaryDirectory(prefix=".gf180-fill-", dir=output.parent) as directory:
        temporary = Path(directory) / output.name
        layout.write(str(temporary))
        common.require(temporary.stat().st_size <= MAX_GDS_BYTES, "GF180_FILL_OUTPUT_BYTE_BUDGET")
        os.link(temporary, output)
    return {"schema": "rusteda.gf180-ibex-floating-fill.v1", "scope": "GDS geometry only",
            "inputs": sources, "output": common.identity(output, MAX_GDS_BYTES), "layers": reports}


def main():
    parameters = globals()
    keys = ("input_gds", "input_def", "macro_lef", "output_gds")
    common.require(all(name in parameters for name in keys), "GF180_FILL_REQUIRED_ARGUMENTS: " + ", ".join(keys))
    print(json.dumps(emit(*(parameters[name] for name in keys)), sort_keys=True))


if __name__ == "__main__":
    main()

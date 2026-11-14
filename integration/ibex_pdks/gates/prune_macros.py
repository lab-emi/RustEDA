# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Check every macro master of a GDS against the macro's own GDS, then write a copy with it emptied.

KLayout batch script (KLayout is used as a GDS reader and editor through its public Python API):

  klayout -b -r prune_macros.py -rd gds_path=<in.gds> -rd top_name=<cell>
          -rd references=<master>=<macro.gds>[,<master>=<macro.gds>] -rd out_path=<out.gds> -rd record_path=<json>

Identity. A layout passes only if, for every listed master, the cell of that name exists, is
placed at least once below the top cell, and its flat geometry equals the flat geometry of the
master in the macro's own GDS on every layer either file uses (a per-layer XOR must be empty).
An empty macro cell, a missing one, a renamed one or any changed shape fails; the record names
the layers that differ and the XOR area on each. No copy is written then.

Copy. The input of the Magic-based checks, which read every macro as its LEF abstract view: the
macro masters keep their name and their placements, their shapes and their private subcells are
dropped, and nothing else changes. Without this Magic would still load, and queue for checking,
the thousands of cells below a macro that it then never uses.
"""
import json

import pya


def flat_difference(cell, reference_cell):
    """[(layer, xor area um2, reference area um2, layout area um2)] for every layer that differs."""
    layouts = (cell.layout(), reference_cell.layout())
    pairs = {}
    for index, source in enumerate(layouts):
        for layer in source.layer_indexes():
            info = source.get_info(layer)
            if info.layer >= 0:
                pairs.setdefault((info.layer, info.datatype), [None, None])[index] = layer
    area = layouts[0].dbu * layouts[0].dbu
    result = []
    for (number, datatype), (left, right) in sorted(pairs.items()):
        a = pya.Region(cell.begin_shapes_rec(left)) if left is not None else pya.Region()
        b = pya.Region(reference_cell.begin_shapes_rec(right)) if right is not None else pya.Region()
        xor = a ^ b
        if not xor.is_empty():
            result.append(["%d/%d" % (number, datatype), round(xor.area() * area, 6), round(b.area() * area, 6),
                           round(a.area() * area, 6)])
    return result


def count_cells(layout):
    return sum(1 for _ in layout.each_cell())


layout = pya.Layout()
layout.read(gds_path)  # noqa: F821
top = layout.cell(top_name)  # noqa: F821
record = dict(source=gds_path, top=top_name, status="failed", cells_before=count_cells(layout), masters={})  # noqa: F821
errors = []
if top is None:
    errors.append("TOP_CELL_MISSING")
references = [item.split("=", 1) for item in references.split(",") if item]  # noqa: F821
for master, macro_gds in references if top is not None else []:
    entry = record["masters"].setdefault(master, dict(reference=macro_gds))
    cell = layout.cell(master)
    if cell is None:
        errors.append("MACRO_CELL_MISSING: " + master)
        continue
    placements = 0
    iterator = top.begin_instances_rec()
    iterator.targets = [cell.cell_index()]
    while not iterator.at_end():
        placements += 1
        iterator.next()
    reference = pya.Layout()
    reference.read(macro_gds)
    reference_cell = reference.cell(master)
    if reference_cell is None:
        errors.append("MACRO_REFERENCE_WITHOUT_MASTER: " + master)
        continue
    if abs(reference.dbu - layout.dbu) > 1e-12:
        errors.append("MACRO_DBU_DIFFERS: " + master)
        continue
    differing = flat_difference(cell, reference_cell)
    box, reference_box = cell.dbbox(), reference_cell.dbbox()
    entry.update(placements=placements, empty=cell.bbox().empty(),
                 bbox_um=None if cell.bbox().empty() else [box.left, box.bottom, box.right, box.top],
                 reference_bbox_um=[reference_box.left, reference_box.bottom, reference_box.right, reference_box.top],
                 differing_layers=differing[:64], differing_layer_count=len(differing))
    if placements == 0:
        errors.append("MACRO_NOT_PLACED: " + master)
    if differing:
        errors.append("MACRO_DIFFERS_FROM_ITS_GDS: %s (%s%d layers differ)"
                      % (master, "the cell is empty; " if cell.bbox().empty() else "", len(differing)))
if not errors:
    for master, _ in references:
        cell = layout.cell(master)
        before = count_cells(layout)
        cell.prune_subcells()
        cell.clear()
        record["masters"][master]["private_cells_removed"] = before - count_cells(layout)
    record.update(status="identical_then_emptied", cells_after=count_cells(layout),
                  identity="flat geometry of every macro master equals the macro's own GDS on every layer")
    layout.write(out_path)  # noqa: F821
record["errors"] = errors
with open(record_path, "w") as stream:  # noqa: F821
    json.dump(record, stream, indent=2)
    stream.write("\n")

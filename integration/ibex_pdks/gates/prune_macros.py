# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Write a copy of a GDS in which the given macro masters are empty cells.

KLayout batch script (KLayout is used as a GDS editor through its public Python API):

  klayout -b -r prune_macros.py -rd gds_path=<in.gds> -rd top_name=<cell> -rd masters=<a>[,<b>]
          -rd out_path=<out.gds> -rd record_path=<json>

Input of the Magic-based checks, which read every macro as its LEF abstract view: the macro
masters keep their name and their placements, their shapes and their private subcells are
dropped, and nothing else changes. Without this Magic would still load, and queue for checking,
the thousands of cells below a macro that it then never uses. The record counts what was removed.
"""
import json

import pya

layout = pya.Layout()
layout.read(gds_path)  # noqa: F821
top = layout.cell(top_name)  # noqa: F821
count = lambda: sum(1 for _ in layout.each_cell())  # noqa: E731
record = dict(source=gds_path, top=top_name, cells_before=count(), masters={})  # noqa: F821
if top is None:
    raise RuntimeError("TOP_CELL_MISSING")
for master in [name for name in masters.split(",") if name]:  # noqa: F821
    cell = layout.cell(master)
    if cell is None:
        raise RuntimeError("MACRO_MASTER_MISSING: " + master)
    placements = 0
    iterator = top.begin_instances_rec()
    iterator.targets = [cell.cell_index()]
    while not iterator.at_end():
        placements += 1
        iterator.next()
    before = count()
    box = cell.dbbox()
    cell.prune_subcells()
    cell.clear()
    record["masters"][master] = dict(placements=placements, private_cells_removed=before - count(),
                                     bbox_um=[box.left, box.bottom, box.right, box.top])
record["cells_after"] = count()
layout.write(out_path)  # noqa: F821
with open(record_path, "w") as stream:  # noqa: F821
    json.dump(record, stream, indent=2)
    stream.write("\n")

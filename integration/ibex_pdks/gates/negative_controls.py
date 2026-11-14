# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Write a deliberately corrupted copy of a final layout: the controls a gate must fail.

KLayout batch script (KLayout is used as a GDS editor through its public Python API):

  klayout -b -r negative_controls.py -rd gds_path=<in.gds> -rd top_name=<cell> -rd out_path=<out.gds>
          -rd record_path=<json> -rd ops=stub,via,diodes
          -rd wire_layer=36/0 -rd cut_layer=35/0 -rd diode_cells=<master>[,<master>]
          [-rd avoid=<master>[,<master>]]

  stub    one long top-level wire on `wire_layer` is cut down to a square of its own width: an
          isolated shape below the layer's minimum area (DRC control; it also opens a net)
  via     one single top-level cut on `cut_layer` is removed: an open net (LVS control)
  diodes  every top-level instance of the antenna diode masters is removed (antenna control)

Shapes are taken from the top cell only and outside the bounding boxes of the `avoid` masters
(the macros), so that macro interiors stay untouched. The choice is deterministic: the candidate
with the smallest (x, y) of its lower-left corner. The record lists what was changed, in um.
"""
import json

import pya

layout = pya.Layout()
layout.read(gds_path)  # noqa: F821
top = layout.cell(top_name)  # noqa: F821
if top is None:
    raise RuntimeError("TOP_CELL_MISSING")
dbu = layout.dbu
record = dict(source=gds_path, top=top_name, operations=[])  # noqa: F821
keep_out = pya.Region()
avoided = [name for name in globals().get("avoid", "").split(",") if name]
for inst in top.each_inst():
    if inst.cell.name in avoided:
        keep_out.insert(inst.bbox().enlarged(5000, 5000))


def layer_of(text):
    number, datatype = (int(v) for v in text.split("/"))
    index = layout.find_layer(number, datatype)
    if index is None:
        raise RuntimeError("LAYER_MISSING: " + text)
    return index


def um(box):
    return [round(v * dbu, 4) for v in (box.left, box.bottom, box.right, box.top)]


def free(box):
    return (pya.Region(box) & keep_out).is_empty()


operations = ops.split(",")  # noqa: F821
if "stub" in operations:
    layer = layer_of(wire_layer)  # noqa: F821
    best = None
    for shape in top.shapes(layer).each():
        if not shape.is_box():
            continue
        box = shape.box
        width, length = min(box.width(), box.height()), max(box.width(), box.height())
        if length >= 20 * width and width > 0 and free(box):
            key = (box.left, box.bottom)
            if best is None or key < best[0]:
                best = (key, shape, box, width)
    if best is None:
        raise RuntimeError("NO_WIRE_TO_SHORTEN")
    _, shape, box, width = best
    middle = box.center()
    stub = pya.Box(middle.x - width // 2, middle.y - width // 2, middle.x - width // 2 + width, middle.y - width // 2 + width)
    shape.delete()
    top.shapes(layer).insert(stub)
    record["operations"].append(dict(op="stub", layer=wire_layer, wire_um=um(box), stub_um=um(stub),  # noqa: F821
                                     stub_area_um2=round(stub.area() * dbu * dbu, 6)))
if "via" in operations:
    layer = layer_of(cut_layer)  # noqa: F821
    region = pya.Region(top.shapes(layer))
    candidates = sorted((shape for shape in top.shapes(layer).each() if shape.is_box()),
                        key=lambda shape: (shape.box.left, shape.box.bottom))
    chosen = None
    for shape in candidates:
        box = shape.box
        # a single cut: no other cut of this layer within three cut sizes
        halo = box.enlarged(3 * box.width(), 3 * box.height())
        if free(box) and region.interacting(pya.Region(halo)).count() == 1:
            chosen = (shape, box)
            break
    if chosen is None:
        raise RuntimeError("NO_SINGLE_CUT_TO_REMOVE")
    shape, box = chosen
    shape.delete()
    record["operations"].append(dict(op="via", layer=cut_layer, removed_cut_um=um(box)))  # noqa: F821
if "diodes" in operations:
    masters = [name for name in diode_cells.split(",") if name]  # noqa: F821
    doomed = [inst for inst in top.each_inst() if inst.cell.name in masters]
    places = [um(inst.bbox()) for inst in doomed]
    for inst in doomed:
        inst.delete()
    record["operations"].append(dict(op="diodes", masters=masters, removed_instances=len(places), first_um=places[:5]))
    if not places:
        raise RuntimeError("NO_DIODE_INSTANCE")
layout.write(out_path)  # noqa: F821
with open(record_path, "w") as stream:  # noqa: F821
    json.dump(record, stream, indent=2)
    stream.write("\n")

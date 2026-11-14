# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Render a GDS to a PNG with a PDK's layer properties (KLayout batch script; documentation tooling).

usage: klayout -b -r render_gds.py -rd gds=IN.gds -rd lyp=LAYERS.lyp -rd out=OUT.png [-rd width=2400]
       [-rd hide=LAYER/DATATYPE,...] [-rd info=OUT.json]
The image shows every hierarchy level, without text, grid or rulers, on a black background, at the aspect ratio
of the top cell's bounding box. `info` receives the bounding box in micrometres and the image size.
"""
import json
import pya

width = int(globals().get("width", "2400"))
view = pya.LayoutView()
view.load_layout(gds, True)
view.load_layer_props(lyp)
view.max_hier()
for key, value in (("background-color", "#000000"), ("grid-visible", "false"), ("text-visible", "false"),
                   ("cell-box-visible", "false"), ("bitmap-oversampling", "2"), ("default-font-size", "0")):
    view.set_config(key, value)
hidden = {tuple(int(v) for v in item.split("/")) for item in globals().get("hide", "").split(",") if item}
it = view.begin_layers()
while not it.at_end():
    node = it.current()
    if (node.source_layer, node.source_datatype) in hidden:
        node.visible = False
    it.next()
layout = view.active_cellview().layout()
box = view.active_cellview().cell.dbbox()
view.zoom_box(box.enlarged(box.width() * 0.01, box.height() * 0.01))
height = int(round(width * box.height() / box.width()))
view.save_image(out, width, height)
if "info" in globals():
    with open(info, "w") as f:
        json.dump({"gds": gds, "top": view.active_cellview().cell.name, "dbu": layout.dbu,
                   "bbox_um": [box.left, box.bottom, box.right, box.top], "image": [width, height]}, f, indent=1)
print("rendered", out, width, height)

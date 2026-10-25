"""Original, optional KLayout DEF-to-GDS recipe; real PDK validation is UNVERIFIED.

Run only through RustLane's bounded external-tool runner. No core crate depends
on Python: this script runs in the independently supplied KLayout executable.
"""
import json
from collections import deque
from pathlib import Path

import pya


def fail(message):
    raise RuntimeError("RUSTLANE_STREAMOUT: " + message)


def read_json(path, byte_limit):
    with Path(path).open("rb") as source:
        raw = source.read(byte_limit + 1)
    if len(raw) > byte_limit:
        fail("JSON input exceeds its byte budget")
    value = json.loads(raw)
    if not isinstance(value, dict):
        fail("JSON object required")
    return value


if "context_path" not in globals():
    fail("pass -rd context_path={context} through the RustLane recipe")
context = read_json(context_path, 262144)
collateral = context.get("collateral", {})
if not isinstance(collateral, dict):
    fail("collateral dictionary required")


def input_path(key):
    value = collateral.get(key)
    if not isinstance(value, str) or not Path(value).is_file():
        fail("missing explicit collateral " + key)
    return value


limits = read_json(input_path("streamout_limits"), 8192)
required = {"max_cells", "max_edges", "max_input_bytes"}
if set(limits) != required:
    fail("streamout_limits must contain exactly max_cells, max_edges, max_input_bytes")
for name, maximum in (("max_cells", 100000), ("max_edges", 500000), ("max_input_bytes", 1073741824)):
    value = limits[name]
    if type(value) is not int or not 1 <= value <= maximum:
        fail(name + " is outside the supported finite range")
top_name = context.get("top")
if not isinstance(top_name, str) or not top_name:
    fail("top must be specified")
def_path = context.get("artifacts", {}).get("def")
if not isinstance(def_path, str) or not Path(def_path).is_file():
    fail("a preceding physical step must produce a DEF artifact")
tech_lef = input_path("tech_lef")
cell_lef = input_path("cell_lef")
cell_gds = input_path("cell_gds")
layer_map = input_path("lefdef_map")
macro_lefs = [input_path(key) for key in sorted(collateral) if key.startswith("macro_lef_")]
macro_gds = [input_path(key) for key in sorted(collateral) if key.startswith("macro_gds_")]
if len(macro_lefs) > 32 or len(macro_gds) > 32:
    fail("at most 32 additional macro LEFs and 32 macro GDS files are supported")
inputs = [def_path, tech_lef, cell_lef, cell_gds, layer_map] + macro_lefs + macro_gds
if sum(Path(path).stat().st_size for path in inputs) > limits["max_input_bytes"]:
    fail("aggregate input bytes exceed the declared budget")
if Path(layer_map).stat().st_size == 0:
    fail("the caller must supply a nonempty PDK-specific LEF/DEF layer map")

options = pya.LoadLayoutOptions()
reader = options.lefdef_config
reader.read_lef_with_def = False
reader.lef_files = [tech_lef, cell_lef] + macro_lefs
reader.map_file = layer_map
reader.create_other_layers = False
reader.macro_layout_files = [cell_gds] + macro_gds
# Documented mode 2 substitutes real layout cells, rather than LEF abstracts.
reader.macro_resolution_mode = 2
reader.produce_cell_outlines = False
reader.produce_obstructions = False
reader.produce_blockages = False
reader.produce_placement_blockages = False
reader.produce_regions = False
reader.produce_lef_pins = False
reader.produce_lef_labels = False
options.lefdef_config = reader
layout = pya.Layout()
layout.read(def_path, options)
if layout.cells() > limits["max_cells"]:
    fail("imported cell count exceeds the declared budget")
top = layout.cell(top_name)
if top is None:
    fail("the DEF does not contain the configured top cell")

# Validate the compressed cell graph once. Never expand instance arrays or
# enumerate hierarchy paths: shared subcells enter the queue at most once.
adjacency = {}
indegree = {}
for cell in layout.each_cell():
    index = cell.cell_index()
    if len(adjacency) >= limits["max_cells"]:
        fail("cell budget exceeded")
    if cell.is_empty():
        fail("empty or unresolved physical cell: " + cell.name)
    adjacency[index] = []
    indegree[index] = 0
edge_count = 0
for cell in layout.each_cell():
    children = adjacency[cell.cell_index()]
    for child in cell.each_child_cell():
        if edge_count >= limits["max_edges"]:
            fail("cell-edge budget exceeded")
        if child not in indegree:
            fail("unresolved child cell")
        children.append(child)
        indegree[child] += 1
        edge_count += 1
queue = deque(index for index, count in indegree.items() if count == 0)
visited = 0
while queue:
    index = queue.popleft()
    visited += 1
    for child in adjacency[index]:
        indegree[child] -= 1
        if indegree[child] == 0:
            queue.append(child)
if visited != len(adjacency):
    fail("cyclic cell hierarchy")

output_dir = Path(context["output_dir"])
if not output_dir.is_dir():
    fail("RustLane must provide the step output directory")
save = pya.SaveLayoutOptions()
save.format = "GDS2"
save.gds2_write_timestamps = False
save.select_cell(top.cell_index())
layout.write(str(output_dir / "layout.gds"), save)
(output_dir / "streamout.json").write_text(json.dumps({
    "top": top_name,
    "cells": len(adjacency),
    "unique_child_edges": edge_count,
    "drc_status": "unchecked",
    "lvs_status": "unchecked",
    "pdk_validation": "unverified",
}, indent=2) + "\n")

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Compose a matched Ibex GDS figure from renders, placed DEFs and shared-gate results.

The case JSON names the PDK, bank capacity, macro LEFs, an optional sample note and
the two flows. Each flow supplies ``render`` (render_gds.py's info JSON, next to
render.png) and ``signoff`` (the shared gate summary). Optional fields are a short
``caption`` and ``runtime`` (a measured producer resources.json). The DEF and GDS
must match the gate receipt.
"""

import argparse
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gates"))
import common


BACKGROUND = "#0b1320"
TEXT = "#eff6fd"
SUBTLE = "#a7bdd0"
ACCENTS = ("#63ded6", "#a7bfff")
ANNOTATION = "#f8dba1"


def checked_artifact(summary, key):
    record = summary["layout"][key]
    return common.verify(record, key)


def read_panel(flow, macro_lefs):
    render = Path(flow["render"])
    metadata = common.read_json(render, 64 * 1024)
    summary = common.read_json(flow["signoff"], 16 * common.MIB)
    gds = checked_artifact(summary, "gds")
    common.require(Path(metadata["gds"]).resolve() == gds, "RENDER_GDS_DIFFERS_FROM_GATE_GDS")
    design = common.def_design(checked_artifact(summary, "def"))
    common.require(metadata["top"] == design["top"], "RENDER_TOP_DIFFERS_FROM_DEF")
    lefs = common.lef_macros(macro_lefs)
    boxes = common.macro_boxes(design, lefs, lefs.keys())
    common.require(1 <= len(boxes) <= 16, "MACRO_COUNT")
    common.require(all(name.startswith(("u_imem", "u_dmem")) for name in boxes), "IBEX_BANK_NAMES")
    with Image.open(render.with_name("render.png")) as image:
        common.require(list(image.size) == metadata["image"] and max(image.size) <= 4096, "RENDER_SIZE")
        pixels = image.convert("RGB")
    gates = summary["gates"]
    density = gates.get("density", {}).get("report") or {}
    timing = gates.get("timing", {}).get("report") or {}
    drc = gates.get("drc", {}).get("report") or {}
    slow = timing.get("corners", {}).get("ss", {})
    area = density.get("area_dbu2", {}).get("final_nonfiller_standard_cells")
    area_um2 = area / density["dbu_per_um"] ** 2 if area is not None else None
    runtime = None
    if flow.get("runtime"):
        resources = common.read_json(flow["runtime"], common.MIB)
        common.require(resources["exit_code"] == 0, "PRODUCER_FAILED")
        runtime = resources["wall_seconds"]
    return dict(metadata=metadata, pixels=pixels, die=design["die"], boxes=boxes, units=design["units"],
                area=area_um2, runtime=runtime, setup=slow.get("setup_ws_ns"), hold=slow.get("hold_ws_ns"),
                timing=timing.get("status", "unchecked"), drc=drc.get("status", "unchecked"))


def annotate(ax, panel, bank_kib):
    x0, y0, x1, y1 = panel["metadata"]["bbox_um"]
    pad_x, pad_y = (x1 - x0) * 0.01, (y1 - y0) * 0.01
    ax.imshow(panel["pixels"], extent=(x0 - pad_x, x1 + pad_x, y0 - pad_y, y1 + pad_y))
    ax.set_xlim(x0 - pad_x, x1 + pad_x)
    ax.set_ylim(y0 - pad_y, y1 + pad_y)
    ax.set_axis_off()
    for prefix, label in (("u_imem", "Instruction SRAM"), ("u_dmem", "Data SRAM")):
        group = [[coordinate / panel["units"] for coordinate in row["box"]]
                 for name, row in panel["boxes"].items() if name.startswith(prefix)]
        common.require(group, f"MISSING_BANK: {label}")
        for left, low, right, high in group:
            ax.add_patch(Rectangle((left, low), right - left, high - low,
                                   fill=False, edgecolor="black", linewidth=2.8))
            ax.add_patch(Rectangle((left, low), right - left, high - low,
                                   fill=False, edgecolor=ANNOTATION, linewidth=1.0))
        left, right = min(b[0] for b in group), max(b[2] for b in group)
        low, high = min(b[1] for b in group), max(b[3] for b in group)
        ax.text((left + right) / 2, (low + high) / 2, f"{label}  ·  {bank_kib} KiB",
                color=ANNOTATION, ha="center", va="center", fontsize=9.5, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.48", facecolor=BACKGROUND, edgecolor=ANNOTATION, alpha=0.94))


def compose(case, output):
    flows = [case[key] for key in ("rusteda", "librelane")]
    panels = [read_panel(flow, case["macro_lefs"]) for flow in flows]
    die = [[value / p["units"] for value in p["die"]] for p in panels]
    common.require(die[0] == die[1], "UNMATCHED_DIE")
    macro_count = len(panels[0]["boxes"])
    common.require(panels[0]["boxes"].keys() == panels[1]["boxes"].keys(), "UNMATCHED_MACROS")
    for name, left in panels[0]["boxes"].items():
        right = panels[1]["boxes"][name]
        common.require(left["master"] == right["master"] and left["orientation"] == right["orientation"],
                       f"UNMATCHED_MACRO: {name}")
        common.require(all(a * panels[1]["units"] == b * panels[0]["units"]
                           for a, b in zip(left["box"], right["box"])), f"UNMATCHED_MACRO_LOCATION: {name}")
    colors = dict(color=TEXT, fontfamily="DejaVu Sans")
    width, height = panels[0]["metadata"]["image"]
    fig = plt.figure(figsize=(13, 6.1 * height / width + 2.9), facecolor=BACKGROUND)
    fig.text(0.035, 0.956, f"IBEX RTL-TO-GDS  /  {case['pdk']}", color=ACCENTS[0],
             weight="bold", size=12)
    fig.text(0.035, 0.900, "Two flows on the same floorplan", **colors, weight="bold", size=25)
    die_width, die_height = die[0][2] - die[0][0], die[0][3] - die[0][1]
    fig.text(0.035, 0.857, f"{die_width:g} × {die_height:g} µm die  ·  {macro_count} hard SRAM instances",
             color=SUBTLE, size=11)
    panel_height = 0.531 if any(flow.get("caption") for flow in flows) else 0.566
    for x, name, accent, panel, flow in zip((0.035, 0.52), ("RustEDA", "LibreLane"), ACCENTS, panels, flows):
        fig.text(x, 0.805, name, color=accent, weight="bold", size=16)
        if caption := flow.get("caption"):
            fig.text(x, 0.771, caption, color=SUBTLE, size=10)
        ax = fig.add_axes((x, 0.205, 0.445, panel_height), anchor="N")
        annotate(ax, panel, case["bank_kib"])
        area = "Area unchecked" if panel["area"] is None else f"{panel['area']:,.0f} µm² standard cells"
        runtime = "" if panel["runtime"] is None else f"   ·   {panel['runtime']:,.0f} s producer"
        fig.text(x, 0.153, area + runtime, **colors, weight="bold", size=10.5)
        slack = "" if panel["setup"] is None or panel["hold"] is None else (
            f"  ·  SS setup {panel['setup']:+.2f} ns / hold {panel['hold']:+.2f} ns")
        fig.text(x, 0.111, f"DRC {panel['drc']}  ·  timing {panel['timing']}{slack}", color=SUBTLE, size=9.5)
    note = case.get("note", "Standard-cell area excludes hard macros, gap fillers and decap cells.")
    fig.text(0.035, 0.042, note, color=SUBTLE, size=9)
    fig.savefig(output, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    common.require(not args.out.exists(), "OUTPUT_EXISTS")
    compose(common.read_json(args.case, 64 * 1024), args.out)


if __name__ == "__main__":
    main()

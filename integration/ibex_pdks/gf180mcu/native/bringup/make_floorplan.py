#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the native floorplan (die, core band, rows, fixed macros, macro pin buses) and the I/O plan.

Data preparation only: every number comes from the arguments (the die and the macro placements of the
shared reference configuration) and from the cell and macro LEF (site, macro size, bus pins). The rows
fill the horizontal band between the two macro rows; the I/O pins go to the west and east die edges,
one slot per standard-cell row, three routing tracks above the row's lower rail so that a pin never
sits on the rail via stacks of the supply spines.
"""
import argparse
import json
import re
import sys


def lef_macro(path, name):
    text = open(path).read()
    m = re.search(r"MACRO %s\b(.*?)END %s\b" % (re.escape(name), re.escape(name)), text, re.S)
    if not m:
        sys.exit("macro %s not in %s" % (name, path))
    body = m.group(1)
    size = re.search(r"SIZE ([\d.]+) BY ([\d.]+)", body)
    buses = {}
    for pin in re.findall(r"^\s*PIN (\S+)", body, re.M):
        b = re.fullmatch(r"(\w+)\[(\d+)\]", pin)
        if b:
            lo, hi = buses.get(b.group(1), (int(b.group(2)), int(b.group(2))))
            buses[b.group(1)] = (min(lo, int(b.group(2))), max(hi, int(b.group(2))))
    return float(size.group(1)), float(size.group(2)), {k: [hi, lo] for k, (lo, hi) in sorted(buses.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--config", required=True, help="reference configuration (DIE_AREA, MACROS)")
    ap.add_argument("--macro-lef", required=True)
    ap.add_argument("--netlist", required=True, help="mapped netlist (top-level ports)")
    ap.add_argument("--dbu", type=int, default=2000)
    ap.add_argument("--site", default="GF018hv5v_mcu_sc7")
    ap.add_argument("--site-width-um", type=float, default=0.56)
    ap.add_argument("--site-height-um", type=float, default=3.92)
    ap.add_argument("--halo-um", type=float, default=10.0, help="clearance between the macros and the rows")
    ap.add_argument("--macro-halo-um", type=float, help="halo recorded per macro (default --halo-um); "
                    "the engine refuses overlapping halos, so macros closer than twice the clearance get less")
    ap.add_argument("--margin-um", type=float, required=True, help="die edge to core, west and east")
    ap.add_argument("--pin-track-offset", type=int, default=3, help="tracks above the row's lower rail")
    ap.add_argument("--track-pitch-um", type=float, default=0.56)
    ap.add_argument("--out-floorplan", required=True)
    ap.add_argument("--out-io-plan", required=True)
    a = ap.parse_args()
    dbu = a.dbu
    u = lambda v: int(round(v * dbu))
    cfg = json.load(open(a.config))
    die = [u(v) for v in cfg["DIE_AREA"]]
    sw, sh = u(a.site_width_um), u(a.site_height_um)
    halo = u(a.halo_um)
    macro_halo = u(a.macro_halo_um) if a.macro_halo_um is not None else halo
    placements, buses, boxes = {}, {}, []
    for master, m in cfg["MACROS"].items():
        w, h, b = lef_macro(a.macro_lef, master)
        buses[master] = b
        for inst, p in sorted(m["instances"].items()):
            x, y = u(p["location"][0]), u(p["location"][1])
            placements[inst] = {"x": x, "y": y, "orientation": p["orientation"], "halo": macro_halo}
            boxes.append((x, y, x + u(w), y + u(h)))
    # The band: above every macro of the lower row, below every macro of the upper row.
    mid = (die[1] + die[3]) // 2
    lower = max((b[3] for b in boxes if b[3] <= mid), default=die[1])
    upper = min((b[1] for b in boxes if b[1] >= mid), default=die[3])
    up = lambda v, g: -(-v // g) * g
    y0 = up(lower + halo, sh) if lower > die[1] else up(die[1] + u(a.margin_um), sh)
    y1 = ((upper - halo) // sh) * sh if upper < die[3] else ((die[3] - u(a.margin_um)) // sh) * sh
    x0 = up(die[0] + u(a.margin_um), sw)
    x1 = ((die[2] - u(a.margin_um)) // sw) * sw
    rows = [{"y": y, "x_start": x0, "x_end": x1} for y in range(y0, y1, sh)]
    # The core holds every macro with its halo; rows exist in the band only.
    cy0 = min([y0] + [((b[1] - halo) // sh) * sh for b in boxes])
    cy1 = max([y1] + [up(b[3] + halo, sh) for b in boxes])
    if any(b[0] - halo < x0 or b[2] + halo > x1 for b in boxes) or cy0 < die[1] or cy1 > die[3]:
        sys.exit("a macro halo leaves the core")
    fp = {
        "name": a.name,
        "units_per_micron": dbu,
        "die_area": {"lx": die[0], "ly": die[1], "ux": die[2], "uy": die[3]},
        "core_area": {"lx": x0, "ly": cy0, "ux": x1, "uy": cy1},
        "site": {"name": a.site, "width": sw, "height": sh},
        "rows": rows,
        "macro_placements": placements,
        "pin_buses": buses,
    }
    json.dump(fp, open(a.out_floorplan, "w"), indent=1)
    open(a.out_floorplan, "a").write("\n")
    # Ports of the top module, in declaration order, expanded to bits.
    text = open(a.netlist).read()
    top = re.search(r"module\s+%s\s*\((.*?)\);(.*?)endmodule" % re.escape(a.name), text, re.S)
    decl = top.group(1) + ";" + top.group(2)
    ins, outs = [], []
    for kind, rng, names in re.findall(r"\b(input|output)\s+(?:wire\s+|reg\s+)?(\[[^\]]+\])?\s*([^;()]+?)\s*[;,)]", decl):
        for n in [s.strip() for s in names.split(",") if s.strip()]:
            if not re.fullmatch(r"[A-Za-z_]\w*", n):
                continue
            if rng:
                hi, lo = [int(v) for v in rng.strip("[]").split(":")]
                bits = ["%s[%d]" % (n, i) for i in range(min(hi, lo), max(hi, lo) + 1)]
            else:
                bits = [n]
            (ins if kind == "input" else outs).extend(bits)
    pitch, off = sh, y0 % sh + a.pin_track_offset * u(a.track_pitch_um)
    plan = {"west": ins, "east": outs, "south": [], "north": [], "y_track": [pitch, off % pitch], "targets": {}}
    for side in (ins, outs):
        n = len(side)
        nrows = len(rows)
        if n > nrows:
            sys.exit("more pins on a side (%d) than rows (%d)" % (n, nrows))
        for k, name in enumerate(side):
            r = (2 * k + 1) * nrows // (2 * n)
            plan["targets"][name] = rows[r]["y"] + a.pin_track_offset * u(a.track_pitch_um)
    json.dump(plan, open(a.out_io_plan, "w"), indent=1)
    open(a.out_io_plan, "a").write("\n")
    print(json.dumps({"rows": len(rows), "band": [y0, y1], "core": fp["core_area"], "inputs": len(ins), "outputs": len(outs)}))


if __name__ == "__main__":
    main()

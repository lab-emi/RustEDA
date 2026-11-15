#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Evidence for rc-estimate-policy.json from a routed layout: per-net resistance and capacitance per
routed micron (from the native SPEF) and routed length over the rectilinear minimum spanning tree of the
net's pin positions (from the DEF and the LEF pin geometry), over nets longer than a threshold.

usage: rc_estimate_evidence.py DEF SPEF TECH_LEF CELL_LEF [--min-um 200] [--max-pins 64]
Prints JSON with medians and quartiles; numbers only, no policy is written.
"""
import argparse
import json
import re
import statistics

ap = argparse.ArgumentParser()
ap.add_argument("def_path")
ap.add_argument("spef")
ap.add_argument("tech_lef")
ap.add_argument("cell_lef")
ap.add_argument("--min-um", type=float, default=200.0)
ap.add_argument("--max-pins", type=int, default=64)
a = ap.parse_args()

# LEF: pin centre (bounding box of the first port's rectangles) and size per master.
pins = {}
size = {}
for m in re.finditer(r"^MACRO (\S+)\n(.*?)^END \1", open(a.cell_lef).read(), re.S | re.M):
    body = m.group(2)
    s = re.search(r"SIZE ([\d.]+) BY ([\d.]+)", body)
    size[m.group(1)] = (float(s.group(1)), float(s.group(2)))
    for p in re.finditer(r"^\s*PIN (\S+)\n(.*?)^\s*END \1", body, re.S | re.M):
        rects = [tuple(map(float, r)) for r in re.findall(r"RECT ([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)", p.group(2))]
        if rects:
            r = rects[0]
            pins[(m.group(1), p.group(1))] = ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
via_layers = {}
routing = set()
for path in (a.tech_lef, a.cell_lef):
    t = open(path).read()
    for m in re.finditer(r"^\s*VIA (\S+)[^\n]*\n(.*?)^\s*END \1", t, re.S | re.M):
        via_layers[m.group(1)] = re.findall(r"LAYER (\S+) ;", m.group(2))
    for m in re.finditer(r"^LAYER (\S+)\s*\n\s*TYPE ROUTING", t, re.M):
        routing.add(m.group(1))

text = open(a.def_path).read()
dbu = int(re.search(r"UNITS DISTANCE MICRONS (\d+)", text).group(1))
comp = {}
for name, master, x, y, orient in re.findall(
        r"^\s*-\s+(\S+)\s+(\S+)\s+\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)", text, re.M):
    comp[name] = (master, int(x) / dbu, int(y) / dbu, orient)
ports = {}
for name, x, y in re.findall(r"^- (\S+) \+ NET \S+.*?\+ (?:PLACED|FIXED) \( (-?\d+) (-?\d+) \)", text, re.M):
    ports[name] = (int(x) / dbu, int(y) / dbu)


def point(inst, pin):
    if inst == "PIN":
        return ports.get(pin)
    master, x, y, orient = comp[inst]
    c = pins.get((master, pin))
    if c is None:
        return None
    w, h = size[master]
    px, py = c
    if orient in ("FS", "S"):
        py = h - py
    if orient in ("FN", "S"):
        px = w - px
    return (x + px, y + py)


def mst(points):
    n = len(points)
    best = [float("inf")] * n
    used = [False] * n
    best[0] = 0.0
    total = 0.0
    for _ in range(n):
        i = min((k for k in range(n) if not used[k]), key=lambda k: best[k])
        used[i] = True
        total += best[i]
        for k in range(n):
            if not used[k]:
                d = abs(points[i][0] - points[k][0]) + abs(points[i][1] - points[k][1])
                best[k] = min(best[k], d)
    return total


nets = re.search(r"^NETS \d+ ;(.*?)^END NETS", text, re.S | re.M).group(1)
token = re.compile(r"\(\s*(\S+)\s+(\S+)(?:\s+\S+)?\s*\)|(\S+)")
routed = {}
steiner = {}
for net in re.split(r"^\s*- ", nets, flags=re.M)[1:]:
    name = net.split(None, 1)[0]
    head = net.split("+", 1)[0]
    conns = re.findall(r"\(\s*(\S+)\s+(\S+)\s*\)", head)
    if "+ ROUTED" not in net:
        continue
    length = 0
    wiring = net.split("+ ROUTED", 1)[1].split(";")[0]
    wiring = re.sub(r"RECT\s*\([^)]*\)", " ", wiring)
    for seg in re.split(r"\bNEW\b", wiring):
        parts = seg.split(None, 1)
        if len(parts) < 2:
            continue
        layer, rest = parts
        last = None
        for m in token.finditer(rest):
            if m.group(3):
                continue
            x = last[0] if m.group(1) == "*" else int(m.group(1))
            y = last[1] if m.group(2) == "*" else int(m.group(2))
            if last is not None:
                length += abs(x - last[0]) + abs(y - last[1])
            last = (x, y)
    routed[name] = length / dbu
    if 2 <= len(conns) <= a.max_pins:
        pts = [point(i, p) for i, p in conns]
        if all(pts):
            steiner[name] = mst(pts)

spef = open(a.spef).read()
names = dict(re.findall(r"^\*(\d+) (\S+)$", spef.split("*PORTS")[0].split("*NAME_MAP", 1)[-1], re.M))
unit_r = re.search(r"\*R_UNIT (\S+) (\S+)", spef)
unit_c = re.search(r"\*C_UNIT (\S+) (\S+)", spef)
cscale = float(unit_c.group(1)) * {"PF": 1.0, "FF": 1e-3}[unit_c.group(2).upper()]
rscale = float(unit_r.group(1)) * {"OHM": 1.0, "KOHM": 1e3}[unit_r.group(2).upper()]
rc = {}
for block in re.finditer(r"^\*D_NET (\S+) ([\d.eE+-]+)(.*?)^\*END", spef, re.S | re.M):
    key = block.group(1)
    name = names.get(key.lstrip("*"), key).replace("\\", "")
    res = sum(float(v) for v in re.findall(r"^\d+ \S+ \S+ ([\d.eE+-]+)\s*$",
                                           block.group(3).split("*RES", 1)[-1], re.M)) if "*RES" in block.group(3) else 0.0
    rc[name] = (res * rscale, float(block.group(2)) * cscale)

rows = []
for name, length in routed.items():
    key = name.replace("\\", "")
    if length < a.min_um or key not in rc:
        continue
    r, c = rc[key]
    rows.append((r / length, c / length, length / steiner[name] if steiner.get(name) else None))


def q(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    return {"n": len(v), "median": statistics.median(v), "p25": v[len(v) // 4], "p75": v[(3 * len(v)) // 4]}


print(json.dumps({
    "def": a.def_path, "spef": a.spef, "min_routed_um": a.min_um,
    "nets": len(rows),
    "r_ohm_per_um": q(r for r, _, _ in rows),
    "c_pf_per_um": q(c for _, c, _ in rows),
    "routed_over_mst": q(d for _, _, d in rows),
}, indent=1))

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Count the markers of a KLayout report database per rule; optionally split by a set of excluded boxes.

usage: count_markers.py REPORT.lyrdb [--exclude lx,ly,ux,uy ...]   (microns; a marker whose every point lies
inside one excluded box is counted under "inside", the rest under "outside")
"""
import collections
import json
import re
import sys
import xml.etree.ElementTree as ET

path = sys.argv[1]
boxes = []
args = sys.argv[2:]
while args:
    if args[0] == "--exclude":
        boxes.append(tuple(float(v) for v in args[1].split(",")))
        args = args[2:]
    else:
        sys.exit(__doc__)
inside = collections.Counter()
outside = collections.Counter()
samples = {}
for _, item in ET.iterparse(path):
    if item.tag != "item":
        continue
    rule = (item.findtext("category") or "").strip("'")
    values = " ".join(v.text or "" for v in item.iter("value"))
    pts = [(float(x), float(y)) for x, y in re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", values)]
    hit = bool(pts) and any(all(b[0] <= x <= b[2] and b[1] <= y <= b[3] for x, y in pts) for b in boxes)
    (inside if hit else outside)[rule] += 1
    if not hit:
        samples.setdefault(rule, values[:160])
    item.clear()
print(json.dumps({
    "report": path,
    "outside": dict(sorted(outside.items())),
    "outside_total": sum(outside.values()),
    "inside_excluded_boxes": dict(sorted(inside.items())),
    "inside_total": sum(inside.values()),
    "samples": samples,
}, indent=1))

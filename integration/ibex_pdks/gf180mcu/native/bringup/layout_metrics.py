#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Layout metrics of a routed DEF: instances and area by LEF class, wire length and vias per layer.

usage: layout_metrics.py DEF TECH_LEF CELL_LEF [LEF ...]    (prints JSON; reads either flow's DEF)
Wire length is the centre-line length of the regular wiring in NETS; special nets are not counted.
"""
import collections
import json
import re
import sys

def_path, *lefs = sys.argv[1:]
masters = {}
for path in lefs:
    text = open(path).read()
    for m in re.finditer(r"^MACRO (\S+)\n(.*?)^END \1", text, re.S | re.M):
        body = m.group(2)
        cls = re.search(r"CLASS ([^;]+);", body)
        size = re.search(r"SIZE ([\d.]+) BY ([\d.]+)", body)
        masters[m.group(1)] = (" ".join(cls.group(1).split()).upper() if cls else "CORE",
                               float(size.group(1)) * float(size.group(2)) if size else 0.0)

text = open(def_path).read()
dbu = int(re.search(r"UNITS DISTANCE MICRONS (\d+)", text).group(1))
comp = re.search(r"^COMPONENTS \d+ ;(.*?)^END COMPONENTS", text, re.S | re.M).group(1)
count = collections.Counter()
area = collections.Counter()
for name, master in re.findall(r"^\s*-\s+(\S+)\s+(\S+)", comp, re.M):
    cls, a = masters.get(master, ("UNKNOWN", 0.0))
    if cls == "CORE":
        short = master.split("__")[-1]
        if re.match(r"(clkbuf|clkinv)", short):
            cls = "CORE clock cell"
        elif re.match(r"(dff|sdff|lat|icg)", short):
            cls = "CORE sequential"
        elif re.match(r"(buf|inv)_", short):
            cls = "CORE buffer/inverter"
        else:
            cls = "CORE combinational"
    count[cls] += 1
    area[cls] += a
via_layers = {}
for path in lefs:
    for m in re.finditer(r"^\s*VIA (\S+)[^\n]*\n(.*?)^\s*END \1", open(path).read(), re.S | re.M):
        via_layers[m.group(1)] = re.findall(r"LAYER (\S+) ;", m.group(2))
routing = set()
for path in lefs:
    for m in re.finditer(r"^LAYER (\S+)\s*\n\s*TYPE ROUTING", open(path).read(), re.M):
        routing.add(m.group(1))
nets = re.search(r"^NETS \d+ ;(.*?)^END NETS", text, re.S | re.M)
length = collections.Counter()
vias = collections.Counter()
cut = collections.Counter()
if nets:
    token = re.compile(r"\(\s*(\S+)\s+(\S+)(?:\s+\S+)?\s*\)|(\S+)")
    for net in re.split(r"^- ", nets.group(1), flags=re.M)[1:]:
        if "+ ROUTED" not in net:
            continue
        wiring = net.split("+ ROUTED", 1)[1].split(";")[0]
        wiring = re.split(r"\+ (?:FIXED|COVER|SOURCE|USE|WEIGHT|PROPERTY)", wiring)[0]
        for seg in re.split(r"\bNEW\b", wiring):
            parts = seg.split(None, 1)
            if len(parts) < 2:
                continue
            layer, rest = parts
            last = None
            for m in token.finditer(rest):
                if m.group(3):
                    word = m.group(3)
                    if word in ("TAPER", "TAPERRULE", "STYLE", "MASK", "RECT", "VIRTUAL") or word.lstrip("-").isdigit():
                        continue
                    vias[word] += 1
                    layers = via_layers.get(word, [])
                    for name in layers:
                        if name not in routing:
                            cut[name] += 1
                    other = [name for name in layers if name in routing and name != layer]
                    if len(other) == 1:
                        layer = other[0]
                    continue
                x = last[0] if m.group(1) == "*" else int(m.group(1))
                y = last[1] if m.group(2) == "*" else int(m.group(2))
                if last is not None:
                    length[layer] += abs(x - last[0]) + abs(y - last[1])
                last = (x, y)
std = {k: v for k, v in area.items() if k.startswith("CORE") or k.startswith("ENDCAP")}
logic = sum(v for k, v in area.items() if k.startswith("CORE ") and not any(
    s in k for s in ("SPACER", "WELLTAP", "ANTENNACELL")))
print(json.dumps({
    "def": def_path,
    "instances": dict(sorted(count.items())),
    "instances_total": sum(count.values()),
    "area_um2": {k: round(v, 3) for k, v in sorted(area.items())},
    "standard_cell_area_um2": round(sum(std.values()), 3),
    "standard_cell_area_without_fill_tap_um2": round(logic, 3),
    "wire_length_um": {k: round(v / dbu, 3) for k, v in sorted(length.items())},
    "wire_length_total_um": round(sum(length.values()) / dbu, 3),
    "vias_by_cut_layer": dict(sorted(cut.items())),
    "vias_total": sum(vias.values()),
}, indent=1))

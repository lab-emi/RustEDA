#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bind the clock tree of a physical configuration to the netlist's clock root (bring-up stand-in for
`rusteda frontend-physical`): when the clock port also drives pins that are not clock sinks (a clock
gate), the tree is built on the net that carries the most sink pins and the port's net is routed as it is.

usage: clock_root.py NETLIST PHYSICAL_IN PHYSICAL_OUT
"""
import collections
import json
import re
import sys

netlist, src, dst = sys.argv[1:]
cfg = json.load(open(src))
clock = cfg["clock"]
text = open(netlist).read()
loads = collections.Counter()
other = collections.Counter()
for body in re.findall(r"^\s*[A-Za-z_]\w*\s+\S+\s*\((\..*?)\);", text, re.M | re.S):
    for pin, sig in re.findall(r"\.(\w+)\(\s*([^(){},]+?)\s*\)", body):
        (loads if pin == clock["sink_pin"] else other)[sig] += 1
port = clock["port"]
aliases = {port}
for lhs, rhs in re.findall(r"assign\s+(\S+)\s*=\s*(\S+?)\s*;", text):
    if rhs == port:
        aliases.add(lhs)
clock.pop("root_net", None)
if any(other[a] for a in aliases):
    root, count = max(((n, c) for n, c in loads.items() if n not in aliases), key=lambda kv: (kv[1], kv[0]))
    clock["root_net"] = root
    print(json.dumps({"root_net": root, "sinks": count, "port_sinks": sum(loads[a] for a in aliases),
                      "port_other_loads": sum(other[a] for a in aliases)}))
else:
    print(json.dumps({"root_net": None, "port_sinks": sum(loads[a] for a in aliases)}))
json.dump(cfg, open(dst, "w"), indent=1)

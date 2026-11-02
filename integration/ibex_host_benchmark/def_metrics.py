#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Routed signal wirelength and via count from a final DEF.

Definition (identical to the published Ibex receipt's `wirelength_scope`):
regular NETS only; SPECIALNETS excluded; Manhattan segment lengths between
consecutive path points of ROUTED/FIXED/COVER/NEW paths; RECT patches and
VIRTUAL jumps excluded. Vias are counted per occurrence on regular nets.
Also reports per-layer wirelength, the routed-net count and the placed
standard-cell instance count by class. Streams the file; bounded to 2 GiB.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

TOKEN = re.compile(r"\(|\)|;|[^\s();]+")


def tokens(path: Path):
    with path.open() as stream:
        for line in stream:
            yield from TOKEN.findall(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("def_file", type=Path)
    parser.add_argument("--via-prefix", action="append", default=[],
                        help="extra via-name prefixes (LEF fixed vias are detected from VIAS too)")
    args = parser.parse_args()
    if args.def_file.stat().st_size > 2 << 30:
        raise SystemExit("DEF byte budget exceeded")
    units = 1000
    section = None
    vias_defined = set()
    layer_length = Counter()
    via_count = Counter()
    nets = routed_nets = 0
    components = Counter()
    it = tokens(args.def_file)
    statement = []
    for tok in it:
        if section is None:
            if tok == "UNITS":
                next(it); next(it); units = int(next(it))
            elif tok in ("NETS", "SPECIALNETS", "VIAS", "COMPONENTS"):
                section = tok
                statement = []
            continue
        if tok == "END":
            nxt = next(it)
            if nxt == section:
                section = None
                continue
            statement += [tok, nxt]
            continue
        statement.append(tok)
        if tok != ";":
            continue
        stmt, statement = statement, []
        if not stmt or stmt[0] != "-":
            continue
        if section == "VIAS":
            vias_defined.add(stmt[1])
        elif section == "COMPONENTS" and len(stmt) > 2:
            master = stmt[2]
            kind = ("filler" if re.match(r"FILLER|DECAP", master) else "tap" if master.startswith("WELLTAP")
                    else "macro" if master.startswith("RM_IHPSG13") else "antenna" if master.startswith("ANTENNA")
                    else "standard")
            components[kind] += 1
        elif section == "NETS":
            nets += 1
            layer, last, routed, i = None, None, False, 0
            while i < len(stmt):
                t = stmt[i]
                if t in ("ROUTED", "FIXED", "COVER", "NEW"):
                    layer, last = stmt[i + 1], None
                    routed = True
                    i += 2
                    continue
                if t == "(" and not (routed and re.fullmatch(r"-?\d+|\*", stmt[i + 1])):
                    i = stmt.index(")", i) + 1  # connection ( inst pin ) / ( PIN name )
                    continue
                if t == "(":
                    x, y = stmt[i + 1], stmt[i + 2]
                    j = i + 3
                    if stmt[j] != ")":  # extension value
                        j += 1
                    px = last[0] if x == "*" else int(x)
                    py = last[1] if y == "*" else int(y)
                    if last is not None and layer is not None:
                        layer_length[layer] += abs(px - last[0]) + abs(py - last[1])
                    last = (px, py)
                    i = j + 1
                    continue
                if t in ("RECT", "VIRTUAL"):
                    # RECT ( dx dy dx dy ) patch or VIRTUAL ( x y ) jump: no wire, restart the chain.
                    k = stmt.index(")", i)
                    if t == "VIRTUAL":
                        x, y = stmt[i + 2], stmt[i + 3]
                        last = (last[0] if x == "*" else int(x), last[1] if y == "*" else int(y))
                    i = k + 1
                    continue
                if routed and layer is not None and last is not None and (
                        t in vias_defined or re.match(r"(Via\d|TopVia\d|via\d|VIA)", t)
                        or any(t.startswith(p) for p in args.via_prefix)):
                    via_count[t] += 1
                i += 1
            routed_nets += routed
    total = sum(layer_length.values())
    result = dict(
        def_path=str(args.def_file.resolve()), dbu_per_micron=units,
        routed_signal_wirelength_um=round(total / units, 1),
        wirelength_by_layer_um={k: round(v / units, 1) for k, v in sorted(layer_length.items())},
        vias=sum(via_count.values()), vias_by_name_top=dict(via_count.most_common(12)),
        nets=nets, routed_nets=routed_nets, components=dict(components),
        scope="regular NETS only; SPECIALNETS excluded; Manhattan segments between consecutive path points; "
              "RECT patches and VIRTUAL jumps excluded")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

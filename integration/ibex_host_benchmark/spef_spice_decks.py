#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Build RustSpice/ngspice transient decks from an Ibex post-route SPEF.

IHP SG13G2 transistors use PSP103 through OSDI, which RustSpice does not
implement, so a transistor-level Ibex comparison is impossible. The extracted
interconnect is not: every routed net of the final Ibex layout is a linear RC
network. Each deck drives one or more real nets with a Thevenin ramp
(PULSE through `--driver-ohm`), keeps every extracted resistor and capacitor
(coupling capacitors are grounded, as in a single-corner Elmore view) and
loads each sink pin with `--sink-ff`. Decks carry `* bench.*` metadata so
`rustspice bench-suite --cases DIR` applies its own accuracy and timing rules
(tight-ngspice reference, `tran_lti` oracle up to 400 unknowns, interleaved
timing of ngspice SPARSE and KLU).

Families written:
  ibex_net_<k>    the K largest single nets by node count (accuracy + timing);
  ibex_bulk_<n>   n nets in one deck, largest first (10 ps output step keeps the
                  rawfiles of both simulators within the suite's 1 GiB budget).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re

SCALE = {"": 1.0, "K": 1e3, "M": 1e-3, "U": 1e-6, "N": 1e-9, "P": 1e-12, "F": 1e-15}


def unit(line: str) -> float:
    _, value, name = line.split()[:3]
    return float(value) * {"PF": 1e-12, "FF": 1e-15, "NF": 1e-9, "OHM": 1.0, "KOHM": 1e3}[name.upper()]


def parse(path: Path):
    cap_unit, res_unit, names = 1e-12, 1.0, {}
    nets, net, section = {}, None, None
    with path.open() as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("*C_UNIT"):
                cap_unit = unit(line)
            elif line.startswith("*R_UNIT"):
                res_unit = unit(line)
            elif line.startswith("*NAME_MAP"):
                section = "map"
            elif section == "map" and line.startswith("*") and line.split()[0][1:].isdigit():
                key, value = line.split(None, 1)
                names[key] = value
            elif line.startswith("*D_NET"):
                parts = line.split()
                net = dict(name=names.get(parts[1], parts[1]), conn=[], caps=[], res=[])
                nets[net["name"]] = net
                section = None
            elif net is not None and line in ("*CONN", "*CAP", "*RES"):
                section = line[1:].lower()
            elif line.startswith("*END"):
                net, section = None, None
            elif net is not None and section == "conn" and line.startswith(("*I", "*P")):
                parts = line.split()
                net["conn"].append((parts[0], parts[1], parts[2]))
            elif net is not None and section == "cap":
                parts = line.split()
                value = float(parts[-1]) * cap_unit
                net["caps"].append((parts[1], parts[2] if len(parts) == 4 else None, value))
            elif net is not None and section == "res":
                parts = line.split()
                net["res"].append((parts[1], parts[2], float(parts[3]) * res_unit))
    return nets, names


def node_name(token: str, names) -> str:
    # *12:3 or inst:pin, possibly mapped; SPICE node names must avoid ':' and '/'.
    if token.startswith("*") and ":" in token:
        head, tail = token.split(":", 1)
        token = names.get(head, head) + ":" + tail
    else:
        token = names.get(token, token)
    # SPICE is case-insensitive; the bench-suite probe grammar requires lower case.
    return ("n_" + re.sub(r"[^A-Za-z0-9_]", "_", token)).lower()


def emit_net(net, names, index, driver_ohm, sink_ff, rise_ps):
    lines, nodes = [], set()
    driver = None
    sinks = []
    for kind, token, direction in net["conn"]:
        node = node_name(token, names)
        if (kind == "*I" and direction == "O") or (kind == "*P" and direction == "I"):
            driver = driver or node
        else:
            sinks.append(node)
    if driver is None:
        return None
    for i, (a, b, value) in enumerate(net["res"]):
        na, nb = node_name(a, names), node_name(b, names)
        nodes.update((na, nb))
        lines.append(f"R{index}_{i} {na} {nb} {max(value, 1e-3):.6g}")
    for i, (a, b, value) in enumerate(net["caps"]):
        na = node_name(a, names)
        nodes.add(na)
        if value > 0:
            lines.append(f"C{index}_{i} {na} 0 {value:.6g}")
    if driver not in nodes or not sinks:
        return None
    for i, sink in enumerate(sinks):
        if sink in nodes:
            lines.append(f"CL{index}_{i} {sink} 0 {sink_ff * 1e-15:.6g}")
    rise = rise_ps * 1e-12
    lines.insert(0, f"V{index} src{index} 0 PULSE(0 1.2 100p {rise:.4g} {rise:.4g} 5n 10n)")
    lines.insert(1, f"RD{index} src{index} {driver} {driver_ohm:g}")
    probes = [driver] + [s for s in sinks if s in nodes][:3]
    return dict(lines=lines, unknowns=len(nodes) + 2, probes=probes, sinks=len(sinks))


def ref_tmax(tstep: str) -> str:
    """`bench.ref.tmax` of a deck: a tenth of its output step.

    The suite reads the converged ngspice reference by linear interpolation at both simulators' rows, so the
    reference's own interpolation error must stay well below theirs. A 2p limit (the earlier value) is coarser than
    the 1p output step of the single-net decks and than ngspice's own default step limit there: its error
    (2-4e-4 of the swing) dominated, and inverted, the published single-net accuracy ranking. The error falls with
    the square of the step: tstep/10 is 20x finer on those decks and 2x on the 10p bulk decks. The suite saves only
    `time` and the probes in such a reference run, so the finer reference stays small.
    """
    value, suffix = re.fullmatch(r"([0-9.]+)([a-z]*)", tstep.lower()).groups()
    return f"{float(value) / 10:g}{suffix}"


def write_deck(path, ident, title, nets, oracle, tags, tstep="1p"):
    unknowns = sum(n["unknowns"] for n in nets)
    probes = [f"v({p})" for n in nets[:4] for p in n["probes"][:2]]
    meta = [f"* bench.id: {ident}", "* bench.group: performance", f"* bench.title: {title}",
            f"* bench.probes: {', '.join(probes)}", "* bench.tol.norm: 2e-3", "* bench.expect: match",
            "* bench.ref: reltol=1e-6 abstol=1e-15 vntol=1e-9", f"* bench.ref.tmax: {ref_tmax(tstep)}",
            "* bench.ng.solver: both", "* bench.timing: yes", f"* bench.size: {unknowns}",
            f"* bench.est_rss_mib: {40 + unknowns // 100}", f"* bench.tags: linear, tran, ibex, spef, {tags}",
            "* bench.note: extracted post-route RC of the final Ibex SG13G2/EZ130 layout; Thevenin ramp driver; "
            "coupling capacitance grounded; sink pins loaded with a fixed Liberty-scale capacitance"]
    if oracle:
        meta.insert(5, "* bench.oracle: tran_lti")
    body = [line for n in nets for line in n["lines"]]
    path.write_text("\n".join([title] + meta + body + [f".tran {tstep} 6n", ".end", ""]))
    return unknowns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spef", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--top-nets", type=int, default=8)
    parser.add_argument("--bulk", type=int, action="append", default=[], help="nets per bulk deck")
    parser.add_argument("--max-net-unknowns", type=int, default=4000)
    parser.add_argument("--driver-ohm", type=float, default=1000.0)
    parser.add_argument("--sink-ff", type=float, default=2.0)
    parser.add_argument("--rise-ps", type=float, default=50.0)
    args = parser.parse_args()
    nets, names = parse(args.spef)
    built = []
    for i, net in enumerate(sorted(nets.values(), key=lambda n: -len(n["res"]))):
        if len(net["res"]) + 2 > args.max_net_unknowns:
            continue
        item = emit_net(net, names, i, args.driver_ohm, args.sink_ff, args.rise_ps)
        if item:
            item["name"] = net["name"]
            built.append(item)
    args.out.mkdir(parents=True, exist_ok=True)
    index = []
    for k, item in enumerate(built[: args.top_nets]):
        ident = f"ibex_net_{k:02d}"
        u = write_deck(args.out / f"{ident}.sp", ident, f"Ibex extracted net {item['name']} ({item['unknowns']} unknowns)",
                       [item], item["unknowns"] <= 400, "single_net")
        index.append(dict(id=ident, net=item["name"], unknowns=u, sinks=item["sinks"]))
    for count in args.bulk:
        ident = f"ibex_bulk_{count}"
        u = write_deck(args.out / f"{ident}.sp", ident, f"{count} extracted Ibex nets in one deck",
                       built[:count], False, "bulk", tstep="10p")
        index.append(dict(id=ident, nets=min(count, len(built)), unknowns=u))
    import json
    (args.out / "index.json").write_text(json.dumps(dict(spef=str(args.spef.resolve()), decks=index), indent=2) + "\n")
    print(json.dumps(index, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

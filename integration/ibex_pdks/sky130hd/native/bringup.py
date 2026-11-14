#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bring-up driver of the native SKY130 physical flow (phase 1).

It runs the engine commands of the composed IHP manifest by hand, in the same order, on the data
files of this directory, and records every command with its wall time and peak memory:

  prepare   combined cell LEF (the rule of the profile schema) and one cell GDS stream
  synth     rusteda init/import, rustsys synth, rusteda export            -> mapped.v
  place     rustroad import-pdk, place (pin-planning pass, then final), clock-tree
  route     rustroad route --scope all
  antenna   rustroad check-antenna, repair-antenna rounds (layer jumpers), final check
  extract   rustroad extract (nominal RC profile)                         -> <top>.nom.spef
  export    export-def, export-verilog (plain and powered), export-gds
  sta       rusteda import + ruststa run/report on the native SPEF, TT and SS
  checks    Magic drc(full), KLayout sky130A_mr.drc, Magic antenna, Netgen LVS (macros as black boxes)

Every path comes from the command line; nothing is read from the environment. The step-graph flow
(`rusteda flow exec` on a composed manifest) replaces this script. Two things here are scaffolding
that the flow's own steps do in Rust: the clock root net is taken as the net that drives the most
flop clock pins, and the I/O plan comes from a pin-planning placement (pins sorted by the mean
position of the cells they connect to), see `plan_pins`.

Heavy stages (route of the full design, deck runs) are meant to be started under the host's heavy
lock by the caller.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGES = ("prepare", "synth", "place", "route", "antenna", "extract", "export", "sta", "checks")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Run:
    def __init__(self, args):
        self.a = args
        self.out = Path(args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "logs").mkdir(exist_ok=True)
        self.metrics_path = self.out / "commands.json"
        self.commands = json.loads(self.metrics_path.read_text()) if self.metrics_path.exists() else []

    def run(self, label: str, argv: list, stdout: Path | None = None, env: dict | None = None,
            check: bool = True, cwd: Path | None = None) -> dict:
        argv = [str(x) for x in argv]
        log = self.out / "logs" / f"{label}.stderr"
        target = stdout or (self.out / "logs" / f"{label}.stdout")
        timing = self.out / "logs" / f"{label}.time"
        full = [self.a.gnu_time, "-f", "%e %M", "-o", str(timing)] + argv if self.a.gnu_time else argv
        start = time.time()
        with open(target, "wb") as so, open(log, "wb") as se:
            code = subprocess.run(full, stdout=so, stderr=se, cwd=cwd,
                                  env={**os.environ, **(env or {})}).returncode
        wall = time.time() - start
        rss = None
        if self.a.gnu_time and timing.exists():
            parts = timing.read_text().split()
            if len(parts) >= 2 and parts[-1].isdigit():
                wall, rss = float(parts[-2]), int(parts[-1])
        record = dict(label=label, argv=argv, exit=code, wall_s=round(wall, 3), peak_rss_kib=rss,
                      stdout=str(target), stderr=str(log))
        self.commands = [c for c in self.commands if c["label"] != label] + [record]
        self.metrics_path.write_text(json.dumps(self.commands, indent=1))
        print(f"[{label}] exit {code} wall {record['wall_s']} s rss {rss} KiB", flush=True)
        if check and code != 0:
            tail = (log.read_text(errors="replace")[-1500:] + "\n" +
                    Path(target).read_text(errors="replace")[-1500:])
            raise SystemExit(f"{label} failed:\n{tail}")
        return record

    def json(self, label: str, argv: list, **kw) -> dict:
        r = self.run(label, argv, **kw)
        return json.loads(Path(r["stdout"]).read_text())


def combined_lef(first: Path, others: list[Path], out: Path) -> None:
    """First cell LEF without its final END LIBRARY, the MACRO blocks of the others, END LIBRARY."""
    text = first.read_text()
    body = re.sub(r"END\s+LIBRARY\s*$", "", text.rstrip()).rstrip() + "\n"
    for path in others:
        t = path.read_text()
        blocks = re.findall(r"^MACRO\s+(\S+).*?^END\s+\1\s*$", t, flags=re.S | re.M)
        if not blocks:
            raise SystemExit(f"{path}: no MACRO block")
        for m in re.finditer(r"^MACRO\s+(\S+).*?^END\s+\1[ \t]*$", t, flags=re.S | re.M):
            body += m.group(0).rstrip() + "\n"
    out.write_text(body + "END LIBRARY\n")


def bundle_gds(sources: list[Path], out: Path) -> dict:
    """Structures of every library copied byte for byte, sorted by name, under the first header.

    The rule of `rustroad bundle-cell-gds`; that command caps a bundle at one million records and
    the OpenRAM macro stream alone has 1.3 million."""
    import struct
    blocks, units, header, records = {}, None, None, 0
    for path in sources:
        data = path.read_bytes()
        off, start, name, cur, header_end, done = 0, None, None, None, 0, False
        while off < len(data):
            records += 1
            size, kind, dtype = struct.unpack_from(">HBB", data, off)
            if size < 4 or size % 2 or off + size > len(data):
                raise SystemExit(f"{path}: malformed GDS record")
            payload = data[off + 4:off + size]
            if kind == 3:
                cur, header_end = payload, off + size
            elif kind == 5:
                start = off
            elif kind == 6:
                name = payload.rstrip(b"\0")
            elif kind == 7:
                raw = data[start:off + size]
                if blocks.setdefault(name, raw) != raw:
                    raise SystemExit(f"{path}: conflicting structure {name!r}")
                start = name = None
            elif kind == 4:
                done = True
                break
            off += size
        if not done or cur is None or units not in (None, cur):
            raise SystemExit(f"{path}: incomplete library or differing units")
        units = cur
        header = header or data[:header_end]
    with open(out, "wb") as f:
        f.write(header)
        for key in sorted(blocks):
            f.write(blocks[key])
        f.write(struct.pack(">HBB", 4, 4, 0))
    return dict(structures=len(blocks), records=records)


def ports_of(netlist: Path) -> list[str]:
    text = netlist.read_text()
    head = text[:text.index(");")]
    ports = []
    for m in re.finditer(r"(?:input|output|inout)\s+wire\s+(?:\[(\d+):(\d+)\]\s+)?(\\?[^\s,;()]+)", head):
        hi, lo, name = m.groups()
        if hi is None:
            ports.append(name)
        else:
            a, b = int(hi), int(lo)
            ports += [f"{name}[{i}]" for i in range(min(a, b), max(a, b) + 1)]
    return ports


def clock_root(netlist: Path, sink_pin: str, port: str) -> str | None:
    """The net on the most flop clock pins; None when that is the clock port itself."""
    counts: dict[str, int] = {}
    for m in re.finditer(r"\.%s\(\s*([^()\s]+)\s*\)" % re.escape(sink_pin), netlist.read_text()):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    if not counts:
        return None
    best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return None if best == port else best


def cts_projection(sdc: str, port: str) -> str:
    """The clock statements of the SDC in the literal form the CTS reader takes (as the flow's
    profile step writes them); the full SDC remains mandatory for STA."""
    def number(pattern: str) -> str | None:
        m = re.search(pattern, sdc, flags=re.M)
        return m.group(1) if m else None
    period = number(r"^\s*create_clock\b[^\n]*-period\s+([0-9.]+)")
    if period is None:
        raise SystemExit("SDC has no create_clock -period")
    lines = ["# CTS-only literal projection; full original SDC remains mandatory for STA.",
             f"create_clock -name {port} -period {float(period)} [get_ports {{{port}}}]"]
    transition = number(r"^\s*set_clock_transition\s+([0-9.]+)")
    if transition is not None:
        lines.append(f"set_clock_transition {float(transition)} [get_clocks {{{port}}}]")
    uncertainty = number(r"^\s*set_clock_uncertainty\s+([0-9.]+)")
    if uncertainty is not None:
        lines.append(f"set_clock_uncertainty {float(uncertainty)} [get_clocks {{{port}}}]")
    if re.search(r"^\s*set_propagated_clock\b", sdc, flags=re.M):
        lines.append(f"set_propagated_clock [get_clocks {{{port}}}]")
    fanout = number(r"^\s*set_max_fanout\s+([0-9.]+)")
    if fanout is not None:
        lines.append(f"set_max_fanout {float(fanout)} [current_design]")
    return "\n".join(lines) + "\n"


def plan_pins(def_path: Path, ports: list[str], side: str = "south") -> dict:
    """I/O plan from a placed DEF: pins on one side, ordered by the mean x of the cells on their net.

    Scaffolding for the flow's pin-planning step. The order is by (mean x, name): deterministic."""
    text = def_path.read_text()
    comp = {}
    section = text[text.index("COMPONENTS"):text.index("END COMPONENTS")]
    for m in re.finditer(r"-\s+(\S+)\s+\S+[^;]*?(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)", section):
        comp[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    nets = text[text.index("\nNETS"):text.index("END NETS")]
    position = {}
    for m in re.finditer(r"-\s+(\S+)((?:\s*\(\s*\S+\s+\S+\s*\))+)", nets):
        pins = re.findall(r"\(\s*(\S+)\s+(\S+)\s*\)", m.group(2))
        port = [p[1] for p in pins if p[0] == "PIN"]
        xs = [comp[i][0] for i, _ in pins if i in comp]
        for p in port:
            position[p] = sum(xs) / len(xs) if xs else None
    known = [p for p in ports if position.get(p) is not None]
    rest = [p for p in ports if position.get(p) is None]
    known.sort(key=lambda p: (position[p], p))
    # Unconnected or macro-only ports keep port order, spread through the list.
    order = known[:]
    for i, p in enumerate(rest):
        order.insert(min(len(order), (i + 1) * max(1, len(order) // (len(rest) + 1))), p)
    plan = {"west": [], "east": [], "south": [], "north": []}
    plan[side] = order
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for tool in ("rustroad", "rusteda", "rustsys", "ruststa"):
        ap.add_argument(f"--{tool}", type=Path, required=True)
    ap.add_argument("--pdk", type=Path, required=True, help="PDK tree (holds libs.ref and libs.tech)")
    ap.add_argument("--prep", type=Path, required=True, help="prepared design (rtl/design.v, constraints.sdc, macro/)")
    ap.add_argument("--profile", type=Path, default=HERE, help="native profile directory")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--gnu-time", default=None, help="GNU time binary for wall/RSS receipts")
    ap.add_argument("--rc-profile", type=Path, help="rustroad.rc-profile.v1 (default: <profile>/rc-nom.json)")
    ap.add_argument("--magic"); ap.add_argument("--klayout"); ap.add_argument("--netgen")
    # Ladder rungs: another top, RTL, floorplan or policy file than the benchmark's.
    ap.add_argument("--top"); ap.add_argument("--rtl", type=Path, action="append")
    ap.add_argument("--sdc", type=Path); ap.add_argument("--floorplan", type=Path)
    ap.add_argument("--physical", type=Path); ap.add_argument("--routing", type=Path)
    ap.add_argument("--no-macros", action="store_true", help="design without the SRAM macros")
    ap.add_argument("--antenna-rounds", type=int, default=3)
    ap.add_argument("--route-expansions", type=int, default=80_000_000)
    ap.add_argument("--whole-net", action="store_true",
                    help="also check whole-net antenna rows (as Magic's hierarchical antennacheck sees nets)")
    ap.add_argument("--diode-rounds", type=int, default=0,
                    help="rounds of antenna-eco diodes for whole-net violations, each followed by seeded re-place and re-route")
    ap.add_argument("--delay-cells", type=Path, help="rustroad.delay-cells.v1 profile naming the antenna diode")
    a = ap.parse_args(argv)
    r = Run(a)
    out = r.out
    stages = [s for s in a.stages.split(",") if s]
    for s in stages:
        if s not in STAGES:
            raise SystemExit(f"unknown stage {s}")
    profile = json.loads((a.profile / "profile.json").read_text())
    top = a.top or profile["top"]

    def ref(value) -> Path:
        value = value["path"] if isinstance(value, dict) else value
        root, rel = value.split(":", 1)
        base = {"pdk": a.pdk, "prep": a.prep, "profile": a.profile}[root]
        return (base / rel).resolve()

    corners = profile["corners"]
    macros = [] if a.no_macros else profile["macros"]
    tech_lef = ref(profile["lef"]["technology"])
    floorplan = a.floorplan or a.profile / "floorplan.json"
    physical_src = a.physical or a.profile / "physical.json"
    routing = a.routing or a.profile / "routing.json"
    sdc = a.sdc or a.prep / "constraints.sdc"
    rtl = a.rtl or [a.prep / "rtl" / "design.v"]
    lef = out / "combined.lef"
    cells_gds = out / "combined-cells.gds"
    buses = out / "cell-buses.json"
    mapped = out / "mapped.v"
    ws = out / "road"
    road = [a.rustroad, "--workspace", ws, "--threads", a.threads]
    spef = out / f"{top}.nom.spef"
    final_v, powered_v = out / f"{top}.v", out / f"{top}.powered.v"
    def_out, gds_out = out / f"{top}.def", out / f"{top}.gds"

    if "prepare" in stages:
        combined_lef(ref(profile["lef"]["cells"][0]),
                     [ref(x) for x in profile["lef"]["cells"][1:]] + [ref(m["lef"]) for m in macros], lef)
        info = bundle_gds([ref(x) for x in profile["gds"]["cells"]] + [ref(m["gds"]) for m in macros], cells_gds)
        buses.write_text(json.dumps(json.loads((a.profile / "interfaces.json").read_text())["cell_buses"], indent=1))
        (out / "prepare.json").write_text(json.dumps(dict(
            combined_lef=dict(path=str(lef), sha256=sha256(lef)),
            cells_gds=dict(path=str(cells_gds), sha256=sha256(cells_gds), **info)), indent=1))
        print("[prepare]", (out / "prepare.json").read_text())

    if "synth" in stages:
        sw = out / "synth"
        if sw.exists():
            shutil.rmtree(sw)
        first = corners[0]
        libs = ["--lib", ref(first["liberty"]["std"]), "--corner", first["name"]]
        for m in macros:
            libs += ["--lib", ref(first["liberty"][m["id"]]), "--corner", first["name"]]
        r.run("synth_init", [a.rusteda, "init", "-w", sw, "--format", "json", "--threads", 2])
        r.run("synth_import", [a.rusteda, "import", "-w", sw, "--base-rev", "rev0", "--threads", 2, "--format",
                               "json", "--lib-cache", out / "lib-cache", *libs, "--rtl", *rtl, "--top", top])
        dont = [x for g in profile.get("synthesis", {}).get("dont_use", []) for x in ("--dont-use", g)]
        r.run("synth", [a.rustsys, "--workspace", sw, "synth", "--base-rev", "rev1", "--threads", 2, *dont])
        r.run("synth_export", [a.rusteda, "export", "-w", sw, "--rev", "rev2", "--netlist", mapped,
                               "--cell-buses", buses, "--threads", 2])

    physical = out / "physical.json"
    current_file = out / "netlist.current"

    def current() -> Path:
        return Path(current_file.read_text().strip()) if current_file.exists() else mapped

    def do_place(netlist: Path, seed: Path | None, tag: str = "") -> None:
        cfg = json.loads(Path(physical_src).read_text())
        if physical.exists() and "io_plan" in json.loads(physical.read_text()):
            cfg["io_plan"] = json.loads(physical.read_text())["io_plan"]
        root = clock_root(netlist, cfg["clock"]["sink_pin"], cfg["clock"]["port"])
        if root:
            cfg["clock"]["root_net"] = root
        else:
            cfg["clock"].pop("root_net", None)
        ports = ports_of(netlist)
        common = ["--tech-lef", tech_lef, "--cell-lef", lef, "--netlist", netlist, "--floorplan", floorplan]
        if "io_plan" not in cfg:
            # Pin-planning pass: pins in port order on one side, placement only.
            cfg["io_plan"] = {"west": [], "east": [], "south": ports, "north": []}
            pins_cfg = out / "physical-pins.json"
            pins_cfg.write_text(json.dumps(cfg, indent=1))
            pw = out / "road-pins"
            if pw.exists():
                shutil.rmtree(pw)
            pr = [a.rustroad, "--workspace", pw, "--threads", a.threads, "--max-expansions", 4000000]
            r.run("pins_import", pr + ["import-pdk", "--base-rev", "rev0", *common, "--physical-config", pins_cfg])
            r.run("pins_place", pr + ["place", "--base-rev", "rev1"])
            r.run("pins_def", pr + ["export-def"], stdout=out / "pins.def")
            cfg["io_plan"] = plan_pins(out / "pins.def", ports)
            shutil.rmtree(pw)
            (out / "pins.def").unlink()
        physical.write_text(json.dumps(cfg, indent=1))
        if ws.exists():
            shutil.rmtree(ws)
        rr = road + ["--max-expansions", 4000000]
        r.run(f"import{tag}", rr + ["import-pdk", "--base-rev", "rev0", *common, "--physical-config", physical])
        r.run(f"place{tag}", rr + ["place", "--base-rev", "rev1"] + (["--seed", seed] if seed else []))
        cts = out / "cts.sdc"
        cts.write_text(cts_projection(Path(sdc).read_text(), cfg["clock"]["port"]))
        r.run(f"clock_tree{tag}", rr + ["clock-tree", "--base-rev", "rev2", "--sdc", cts])

    if "place" in stages:
        do_place(current(), None)

    def head() -> str:
        return (ws / "HEAD").read_text().strip()

    def do_route(tag: str = "") -> None:
        r.run(f"route{tag}", road + ["--max-expansions", a.route_expansions, "route", "--base-rev", head(),
                                     "--scope", "all", "--config", routing], env={"RUSTROAD_ROUTE_TRACE": "1"})

    def do_jumpers(tag: str, history: list) -> None:
        rr = road + ["--max-expansions", 8000000]
        for k in range(a.antenna_rounds + 1):
            report = out / f"antenna{tag}{k}.json"
            r.run(f"antenna_check{tag}{k}", rr + ["check-antenna"], stdout=report)
            rep = json.loads(report.read_text())
            history.append(dict(round=tag, revision=head(), kind="stage", violations=rep.get("violations"),
                                lef_violations=rep.get("lef_violations")))
            if not rep.get("violations") or k == a.antenna_rounds:
                break
            rec = r.run(f"antenna_repair{tag}{k}", rr + ["repair-antenna", "--base-rev", head(), "--antenna-report",
                                                         report], check=False)
            fixed = json.loads(Path(rec["stdout"]).read_text()) if rec["exit"] == 0 else {}
            history[-1]["jumpers"] = len(fixed.get("jumpers", []))
            if rec["exit"] != 0 or not fixed.get("jumpers"):
                break

    if "route" in stages:
        do_route()

    if "antenna" in stages:
        history = []
        do_jumpers("", history)
        for d in range(a.diode_rounds + 1):
            if not a.whole_net:
                break
            whole = out / f"antenna-whole{d}.json"
            r.run(f"antenna_whole{d}", road + ["check-antenna", "--whole-net"], stdout=whole)
            rep = json.loads(whole.read_text())
            rows = [c for c in rep["checks"] if c["violated"]]
            history.append(dict(round=f"d{d}", revision=head(), kind="whole_net", violations=len(rows),
                                nets=len({c["net"] for c in rows})))
            if not rows or d == a.diode_rounds or not a.delay_cells:
                break
            sel = json.loads((a.profile / "selection.json").read_text())
            eco_v, plan = out / f"netlist.diode{d + 1}.v", out / f"antenna-plan{d + 1}.json"
            rec = r.run(f"antenna_eco{d + 1}", [a.rustroad, "antenna-eco", "--netlist", current(), "--antenna-report",
                                               whole, "--delay-cells", a.delay_cells, "--cell",
                                               sel["port_diode"]["cell"], "--out", eco_v, "--plan-out", plan,
                                               "--prefix", f"rr_diode{d + 1}_", "--skip-unknown-nets"], check=False)
            if rec["exit"] != 0 or not eco_v.exists():
                history[-1]["eco"] = "not applied"
                break
            p = json.loads(plan.read_text())
            history[-1]["diodes"] = sum(n["diodes"] for n in p["nets"])
            history[-1]["skipped"] = p.get("skipped", [])
            seed = out / f"seed{d + 1}.json"
            r.run(f"export_seed{d + 1}", road + ["export-placement-seed"], stdout=seed)
            current_file.write_text(str(eco_v) + "\n")
            do_place(eco_v, seed, tag=f"_d{d + 1}")
            do_route(tag=f"_d{d + 1}")
            do_jumpers(f"_d{d + 1}_", history)
        (out / "antenna-history.json").write_text(json.dumps(history, indent=1))
        print("[antenna]", json.dumps(history))

    if "extract" in stages:
        rc = a.rc_profile or a.profile / "rc-nom.json"
        r.run("extract", road + ["--max-expansions", 8000000, "extract", "--rc-profile", rc, "--corner", "nom",
                                 "--out", spef, "--max-spef-mib", 256, "--extract-memory-mib", 3584,
                                 "--max-extract-shapes", 4000000, "--max-rc-nodes", 2000000, "--max-rc-elements",
                                 30000000, "--max-coupling-caps", 10000000, "--max-extract-work", 100000000000,
                                 "--max-extract-candidates", 65536, "--min-coupling-ff", 0.1])

    if "export" in stages:
        r.run("inspect", road + ["inspect"], stdout=out / "inspect.json")
        r.run("export_def", road + ["export-def"], stdout=def_out)
        r.run("export_verilog", road + ["export-verilog"], stdout=final_v)
        r.run("export_powered", road + ["export-verilog", "--include-power"], stdout=powered_v)
        r.run("export_routes", road + ["export-routes"], stdout=out / "routes.json")
        if gds_out.exists():
            gds_out.unlink()
        r.run("export_gds", road + ["export-gds", "--cell-gds", cells_gds, "--layer-map", a.profile / "gds-map.json",
                                    "--out", gds_out, "--merge-shapes", "rectangles", "--vias", "flat"])
        digests = {p.name: sha256(p) for p in (def_out, final_v, powered_v, gds_out, spef) if p.exists()}
        (out / "digests.json").write_text(json.dumps(digests, indent=1))
        print("[export]", json.dumps(digests))

    if "sta" in stages:
        sw = out / "sta"
        if sw.exists():
            shutil.rmtree(sw)
        libs = []
        for c in corners:
            libs += ["--lib", ref(c["liberty"]["std"]), "--corner", c["name"]]
            for m in macros:
                libs += ["--lib", ref(c["liberty"][m["id"]]), "--corner", c["name"]]
            libs += ["--spef", spef, "--spef-corner", c["name"]]
        r.run("sta_init", [a.rusteda, "init", "-w", sw, "--format", "json", "--threads", 2])
        r.run("sta_import", [a.rusteda, "import", "-w", sw, "--base-rev", "rev0", "--threads", 2, "--format", "json",
                             "--lib-cache", out / "lib-cache", "--max-spef-mib", 256, "--max-rc-name-mib", 64,
                             "--max-rc-nodes", 2000000, "--max-rc-elements", 4000000, "--max-rc-nets", 100000,
                             *libs, "--netlist", final_v, "--sdc", sdc])
        r.run("sta_run", [a.ruststa, "run", "-w", sw, "--base-rev", "rev1", "--threads", a.threads,
                          "--concurrent-corners", "--max-state-gib", 2, "--max-paths", 32, "--max-limit-rows", 100000,
                          "--require-wire-model", "spef_rc", "--format", "json"], stdout=out / "sta-run.json")
        for c in corners:
            r.run(f"sta_summary_{c['name']}", [a.ruststa, "report", "-w", sw, "--corner", c["name"], "--threads", 2,
                                              "--section", "summary", "--section", "limits", "--format", "json"],
                  stdout=out / f"sta-{c['name']}.json", check=False)

    if "checks" in stages:
        run_checks(r, a, top, gds_out, powered_v, [ref(m["lef"]) for m in macros], [m["master"] for m in macros])
    return 0


def msb_first(pins: list[str]) -> list[str]:
    """LEF pins with every bus most significant bit first at the position of its first bit: Netgen
    binds a Verilog bus to SPICE pins by position, and the OpenRAM LEF lists buses LSB first."""
    groups, order = {}, []
    for pin in pins:
        m = re.fullmatch(r"(.+)\[(\d+)\]", pin)
        key = m.group(1) if m else pin
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((int(m.group(2)) if m else -1, pin))
    return [pin for key in order for _, pin in sorted(groups[key], reverse=True)]


def run_checks(r: Run, a, top: str, gds: Path, powered: Path, macro_lefs: list[Path], masters: list[str]) -> None:
    out = r.out / "checks"
    out.mkdir(exist_ok=True)
    checks = HERE / "checks"
    rc = a.pdk / "libs.tech/magic/sky130A.magicrc"
    env = {"GDS": str(gds), "CELL": top, "PDK_ROOT": str(a.pdk.parent), "PDK": a.pdk.name,
           "MACRO_LEFS": " ".join(str(x) for x in macro_lefs)}
    if not macro_lefs:
        env.pop("MACRO_LEFS")
    magic = [a.magic, "-dnull", "-noconsole", "-rcfile", rc]
    summary = {}
    if a.magic:
        rec = r.run("magic_drc", magic + [checks / "magic_drc.tcl"], env=env, check=False, cwd=out)
        log = Path(rec["stdout"]).read_text(errors="replace")
        rules = {}
        for m in re.finditer(r"^RR_HIER_WHY (\d+) :: (.*)$", log, flags=re.M):
            rules[m.group(2)] = int(m.group(1))
        total = re.search(r"^RR_HIER_TOTAL (\d+)", log, flags=re.M)
        summary["magic_drc"] = dict(done="RR_DONE" in log, style=(re.search(r"^RR_STYLE (.*)$", log, flags=re.M) or [None, None])[1],
                                    total=int(total.group(1)) if total else None, by_rule=rules)
        rec = r.run("magic_antenna", magic + [checks / "magic_antenna.tcl"], env={**env, "OUTDIR": str(out / "antenna")},
                    check=False, cwd=out)
        log = Path(rec["stdout"]).read_text(errors="replace") + Path(rec["stderr"]).read_text(errors="replace")
        violations = re.findall(r"^Antenna violation detected at plane (\S+)", log, flags=re.M)
        by_plane = {}
        for p in violations:
            by_plane[p] = by_plane.get(p, 0) + 1
        summary["magic_antenna"] = dict(done="RR_ANT_DONE" in log, violations=len(violations), by_plane=by_plane)
        shutil.rmtree(out / "antenna", ignore_errors=True)
    if a.klayout:
        report = out / "klayout_mr.lyrdb"
        rec = r.run("klayout_drc", [a.klayout, "-b", "-r", a.pdk / "libs.tech/klayout/drc/sky130A_mr.drc",
                                    "-rd", f"input={gds}", "-rd", f"top_cell={top}", "-rd", f"report={report}",
                                    "-rd", "feol=true", "-rd", "beol=true", "-rd", "offgrid=true", "-rd", "thr=2"],
                    check=False, cwd=out)
        by_rule = {}
        if report.exists():
            text = report.read_text(errors="replace")
            for m in re.finditer(r"<category>'?([^<']+)'?</category>", text):
                by_rule[m.group(1)] = by_rule.get(m.group(1), 0) + 1
        summary["klayout_drc"] = dict(exit=rec["exit"], report=str(report), total=sum(by_rule.values()), by_rule=by_rule)
    if a.magic and a.netgen:
        ext = out / "ext"
        rec = r.run("magic_extract", magic + [checks / "magic_extract.tcl"], env={**env, "OUTDIR": str(ext)},
                    check=False, cwd=out)
        layout = ext / f"{top}.spice"
        macro_spice = out / "macro_blackbox.spice"
        text = ""
        for lef, master in zip(macro_lefs, masters):
            pins = re.findall(r"^\s*PIN\s+(\S+)", lef.read_text(), flags=re.M)
            text += f".subckt {master} " + " ".join(msb_first(pins)) + "\n.ends\n"
        macro_spice.write_text(text or "* no macros\n")
        comp = out / "netgen.comp.out"
        nenv = {"LAYOUT": str(layout), "TOP": top, "REFV": str(powered),
                "STDSPICE": str(a.pdk / "libs.ref/sky130_fd_sc_hd/spice/sky130_fd_sc_hd.spice"),
                "MACRO_SPICE": str(macro_spice), "SETUP": str(a.pdk / "libs.tech/netgen/sky130A_setup.tcl"),
                "OUT": str(comp)}
        rec = r.run("netgen_lvs", [a.netgen, "-batch", "source", checks / "netgen_lvs.tcl"], env=nenv, check=False, cwd=out)
        log = comp.read_text(errors="replace") if comp.exists() else ""
        summary["netgen_lvs"] = dict(
            extracted=layout.exists(), unique="Circuits match uniquely" in log or "Netlists match uniquely" in log,
            final=[l.strip() for l in log.splitlines() if l.startswith("Final result") or "match uniquely" in l
                   or "do not match" in l or "Property errors" in l][-6:])
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print("[checks]", json.dumps(summary, indent=1))


if __name__ == "__main__":
    sys.exit(main())

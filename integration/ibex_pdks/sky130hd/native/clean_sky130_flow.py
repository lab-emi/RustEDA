#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Clean single-binary, non-PGO SKY130A RTL-to-GDS producer.

Runs one standard release binary set (rusteda, rustsys, ruststa, rustroad) with:
  1. Standard composed native cold flow (`compose_native.py` + `rusteda flow exec`)
     using `profile-clean.json` (converged electrostatic `target_density_milli: 400`,
     zero design-specific vertical placement channels, generic capped router scouts,
     `met3: 0.40` macro OBS wide-spacing, and native `postroute-repair`).
  2. General native SKY130A contact-sidewall antenna closure using only the same
     `rustroad` binary and tracked `horizontal_repair/` helpers (zero OpenRCX/OpenSTA
     in the loop, zero hardcoded instance/net names, zero PGO):
       - Batched native antenna screen (`check-antenna-batch` + `sky130-contact-sidewall-axes.json`)
       - Native DRV headroom gating from `postroute-repair`'s RustSTA TT/SS reports
       - General receiver-pin diode ECO (`apply-eco` + gap-slot `place --seed` +
         incremental `route --prior-diode-plan --preserve-prior-escapes`)
       - Native ranked metal grouped jumpers (`ranked_metal.py` + `repair-antenna-groups`)
       - Native horizontal V3/V4 local via offsets (`generate.py` + `repair-via-offsets`)
       - Final native extraction and GDS/DEF/Verilog exports.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
HORIZ = HERE / "horizontal_repair"
MODEL = HORIZ / "sky130-contact-sidewall-axes.json"
DEFAULT_PROFILE = HERE / "profile-clean.json"

sys.path.insert(0, str(ROOT / "integration/ibex_sg13g2_ez130"))
from estimate_spef import read_def  # noqa: E402
from native_postroute import lef_sizes  # noqa: E402

sys.path.insert(0, str(ROOT / "integration/ibex_pdks/gates"))
from common import dbu, lef_macros  # noqa: E402

# PDK-calibrated native contact/sidewall antenna thresholds (SKY130A HD tech LEF).
# Conservative margins (0.90 metal/contact, 0.80 weighted partial via4) account for
# native-vs-Magic ratio variance so borderline metal3 and via4 nets receive diodes.
OTHER_NATIVE_THRESHOLD = 0.90
WEIGHTED_PARTIAL_VIA4_THRESHOLD = 0.80
HORIZ_V3_THRESHOLD = 0.9785932721712537
MAX_DIODE_OPS = 512
DIODE_CELL = "sky130_fd_sc_hd__diode_2"
# Physical headroom: ~2 fF fixed diode+escape cap plus 3.5% relative RC tolerance.
MIN_CAP_SLACK_PF = 0.002
MIN_CAP_SLACK_FRAC = 0.035
MIN_SLEW_SLACK_NS = 0.030
MIN_FANOUT_SLACK = 0.99
RANKED_METAL_PASSES = 2
HORIZ_V3_PASSES = 1
PROCESS_ORDER = {
    layer: idx
    for idx, layer in enumerate(
        ("li1", "mcon", "met1", "via", "met2", "via2", "met3", "via3", "met4", "via4", "met5")
    )
}
LAYER_RANK = {layer: idx for idx, layer in enumerate(("li1", "met1", "met2", "met3", "met4", "met5"))}


def sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record(path: Path) -> dict:
    p = Path(path).resolve(strict=True)
    return {"path": str(p), "sha256": sha256(p), "bytes": p.stat().st_size}


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def head_rev(ws: Path) -> str:
    return (ws / "HEAD").read_text().strip()


class Runner:
    def __init__(self, out: Path, gnu_time: str = "/usr/bin/time"):
        self.out = out
        self.logs = out / "stage-logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.gnu_time = gnu_time if Path(gnu_time).is_file() else None
        self.stages: list[dict] = []

    def run(
        self,
        name: str,
        argv: list,
        *,
        timeout: int = 1800,
        stdout_path: Path | None = None,
        env_extra: dict[str, str] | None = None,
        check: bool = True,
    ) -> dict:
        cmd = [str(a) for a in argv]
        so_path = stdout_path or (self.logs / f"{name}.stdout")
        se_path = self.logs / f"{name}.stderr"
        time_path = self.logs / f"{name}.time"
        save_json(self.logs / f"{name}.command.json", cmd)
        full = [self.gnu_time, "-v", "-o", str(time_path), *cmd] if self.gnu_time else cmd
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if env_extra:
            env.update(env_extra)
        t0 = time.monotonic()
        with so_path.open("wb") as so, se_path.open("wb") as se:
            proc = subprocess.run(
                full,
                stdin=subprocess.DEVNULL,
                stdout=so,
                stderr=se,
                cwd=ROOT,
                env=env,
                timeout=timeout,
                check=False,
            )
        wall = round(time.monotonic() - t0, 4)
        rss = None
        if time_path.exists():
            m = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", time_path.read_text(errors="replace"))
            if m:
                rss = int(m.group(1))
        entry = {
            "stage": name,
            "seconds": wall,
            "exit_code": proc.returncode,
            "peak_rss_kib": rss,
            "stdout": str(so_path),
            "stderr": str(se_path),
        }
        self.stages.append(entry)
        save_json(self.out / "stages.json", self.stages)
        print(json.dumps({"stage": name, "seconds": wall, "exit_code": proc.returncode, "peak_rss_kib": rss}), flush=True)
        if check and proc.returncode != 0:
            err_tail = se_path.read_text(errors="replace")[-1500:]
            out_tail = so_path.read_text(errors="replace")[-1000:] if so_path.stat().st_size < 10 << 20 else ""
            raise RuntimeError(f"Stage {name} failed (exit {proc.returncode}):\n{err_tail}\n{out_tail}")
        return entry


def parse_lef_pin_geometry(lef_path: Path) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], list]]:
    current = pin = layer = None
    gate_area: dict[tuple[str, str], float] = {}
    rects: dict[tuple[str, str], list] = defaultdict(list)
    with lef_path.open() as fh:
        for row in fh:
            fields = row.split()
            if len(fields) < 2:
                continue
            key = fields[0]
            if key == "MACRO":
                current = fields[1]
            elif key == "PIN" and current:
                pin, layer = fields[1], None
            elif key == "END" and fields[1] == pin:
                pin, layer = None, None
            elif key == "END" and fields[1] == current:
                current = None
            elif current and pin:
                if key == "ANTENNAGATEAREA":
                    gate_area[current, pin] = min(gate_area.get((current, pin), float("inf")), float(fields[1]))
                elif key == "LAYER":
                    layer = fields[1]
                elif key == "RECT" and layer and len(fields) == 6:
                    rects[current, pin].append((layer, *(dbu(x, 1000) for x in fields[1:5])))
    return gate_area, rects


def blocked_nets_from_native_drv(postroute_logs: Path) -> dict[str, list[str]]:
    """Read native RustSTA TT/SS DRV reports and return nets without safe diode headroom."""
    blocked: dict[str, list[str]] = defaultdict(list)
    for drv_file in sorted(postroute_logs.glob("check*-drv.stdout")):
        data = json.loads(drv_file.read_bytes())
        rows = data.get("sections", {}).get("drv", {}).get("rows", [])
        for row in rows:
            net = row.get("net")
            kind = row.get("kind")
            slack = row.get("slack")
            if not net or slack is None:
                continue
            if kind == "max_fanout" and slack < MIN_FANOUT_SLACK:
                blocked[net].append(f"{row.get('corner')}:fanout_slack={slack:.3f}")
            elif kind == "max_capacitance":
                limit = row.get("limit") or 0.0
                req = MIN_CAP_SLACK_PF + MIN_CAP_SLACK_FRAC * limit
                if slack < req:
                    blocked[net].append(f"{row.get('corner')}:cap_slack={slack:.5f}<{req:.5f}")
            elif kind == "max_transition" and slack < MIN_SLEW_SLACK_NS:
                blocked[net].append(f"{row.get('corner')}:slew_slack={slack:.4f}")
    return {k: sorted(set(v)) for k, v in blocked.items()}


def select_diode_targets(
    partition_complete: Path,
    def_path: Path,
    cell_lef: Path,
    blocked_drv: dict[str, list[str]],
    top: str,
) -> tuple[dict, dict, dict]:
    """Select native contact-sidewall antenna diode targets and rank receiver anchors."""
    inventory = json.loads(partition_complete.read_bytes())
    cells, ports, nets, units, def_top = read_def(str(def_path), 200000, 4096)
    assert units == 1000 and def_top == top
    terms = {net: set(map(tuple, ts)) for net, ts in nets}
    masters = lef_macros([cell_lef])
    gate_area, rects = parse_lef_pin_geometry(cell_lef)

    # Identify clock nets (driven by clock buffers or clock port) to exclude from diode ECO.
    clock_nets: set[str] = set()
    for net, ts in terms.items():
        if any(inst == "PIN" and pin == "clk_i" for inst, pin in ts):
            clock_nets.add(net)
        elif any(inst.startswith(("rr_clkbuf_", "native_primary_clock_")) for inst, _ in ts):
            clock_nets.add(net)

    def row_arm(row: dict) -> int | None:
        if row["layer"] != "via4":
            return 0
        if not row["kind"].startswith("whole_net"):
            return 1
        return None

    scores: dict[str, list[float]] = {}
    found: dict[str, list[dict]] = defaultdict(list)
    rejected: dict[str, list[str]] = {}
    admitted: set[str] = set()

    for item in inventory["reports"]:
        rep = json.loads(Path(item["report"]).read_bytes())
        names = {x["net"] for x in rep["selection"]["coverage"]}
        values = {n: [0.0, 0.0] for n in names}
        for row in rep["checks"]:
            if row["limit"] <= 0:
                continue
            a = row_arm(row)
            if a is not None:
                v = values[row["net"]]
                v[a] = max(v[a], row["ratio"] / row["limit"])
        selected = {
            n: v
            for n, v in values.items()
            if v[0] >= OTHER_NATIVE_THRESHOLD or v[1] >= WEIGHTED_PARTIAL_VIA4_THRESHOLD
        }
        scores.update(selected)
        for n in sorted(selected):
            reasons = list(blocked_drv.get(n, []))
            if n in clock_nets:
                reasons.append("clock_net")
            if n not in terms:
                reasons.append("missing_in_def")
            elif not any(
                inst in cells
                and "CORE" in masters[cells[inst][0]]["cls"]
                and masters[cells[inst][0]]["pins"].get(pin, {}).get("direction") == "INPUT"
                for inst, pin in terms[n]
            ):
                reasons.append("no_core_input_receiver")
            if reasons:
                rejected[n] = reasons
            else:
                admitted.add(n)
        for row in rep["checks"]:
            if row["net"] in admitted and row["limit"] > 0:
                found[row["net"]].append(row)

    def receiver_candidates(net: str, row: dict, severity: float):
        for label in row.get("gates", []):
            inst, _, pin = label.rpartition("/")
            if inst not in cells or (inst, pin) not in terms[net]:
                continue
            master, x, y, orient = cells[inst]
            if "CORE" not in masters[master]["cls"] or orient not in ("N", "FS"):
                continue
            pin_info = masters[master]["pins"].get(pin)
            if not pin_info or pin_info["direction"] != "INPUT":
                continue
            shapes = [s for s in rects.get((master, pin), []) if s[0] in LAYER_RANK]
            if not shapes:
                continue
            layer, lx, ly, ux, uy = min(shapes, key=lambda s: (LAYER_RANK[s[0]], s[1:]))
            dx, dy = (lx + ux) // 2, (ly + uy) // 2
            if orient == "FS":
                dy = dbu(masters[master]["size"][1], 1000) - dy
            key = (-severity, gate_area.get((master, pin), float("inf")), inst, pin, row["layer"], row["kind"])
            val = {
                "gate_instance": inst,
                "gate_pin": pin,
                "master": master,
                "net": net,
                "preferred_lower_left_dbu": [x + dx, y + dy],
                "pin_layer": layer,
            }
            yield key, val

    # Rank admitted nets by severity so if > MAX_DIODE_OPS, highest-severity nets come first.
    ordered_admitted = sorted(
        admitted,
        key=lambda n: (
            -max(scores[n][0] / OTHER_NATIVE_THRESHOLD, scores[n][1] / WEIGHTED_PARTIAL_VIA4_THRESHOLD),
            n,
        ),
    )[:MAX_DIODE_OPS]

    choices: dict[str, dict] = {}
    for n in sorted(ordered_admitted):
        stage_rows = []
        fallback_rows = []
        support: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
        for row in found[n]:
            a = row_arm(row)
            strength = (
                row["ratio"] / row["limit"] / (OTHER_NATIVE_THRESHOLD if a == 0 else WEIGHTED_PARTIAL_VIA4_THRESHOLD)
                if a is not None
                else 0.0
            )
            for key, val in receiver_candidates(n, row, strength):
                _, area, inst, pin, layer, kind = key
                if a is not None:
                    fallback_rows.append((key, val))
                if not (kind in ("area", "side_area") and layer in PROCESS_ORDER and 0 < area < float("inf")):
                    continue
                ordinal = PROCESS_ORDER[layer]
                q = row.get("effective_metal_um2", row["metal_um2"]) / (row["limit"] * area * len(row["gates"]))
                sym = (inst, pin)
                support[sym][ordinal] = max(support[sym].get(ordinal, 0.0), q)
                stage_rows.append((layer, q, sym, ordinal, key, val))
        if not fallback_rows:
            rejected[n] = ["no_receiver_pin_shape"]
            continue
        strongest = min(fallback_rows, key=lambda kv: kv[0])
        layer = strongest[0][-2]
        eligible = []
        for lyr, q, sym, ordinal, key, val in stage_rows:
            if lyr != layer:
                continue
            shared = sum(v / (1 + ordinal - j) for j, v in support[sym].items() if j < ordinal)
            weight = q * (1.0 + 0.25 * shared)
            eligible.append(((-weight, *key[1:]), val | {"implicated_layer": layer}))
        choices[n] = min(eligible, key=lambda kv: kv[0])[1] if eligible else strongest[1]

    ops = []
    anchors = {}
    for idx, net in enumerate(sorted(choices)):
        inst = f"rr_eco_ant_{idx:04d}"
        assert inst not in cells
        ops.append({"op": "insert_diode", "instance": inst, "cell": DIODE_CELL, "net": net})
        anchors[inst] = choices[net]
    plan = {"schema": "rustroad.eco-plan.v1", "module": top, "operations": ops}
    stats = {
        "selected_candidates": len(scores),
        "rejected_count": len(rejected),
        "admitted_diodes": len(ops),
        "rejected_sample": dict(list(sorted(rejected.items()))[:20]),
    }
    return plan, anchors, stats


def positions_from_gaps(
    design_json: dict,
    physical: dict,
    old_cells: dict,
    existing: set[str],
    master_sizes: dict[str, tuple[int, int]],
    operations: dict[str, dict],
    anchors: dict[str, dict],
) -> dict[str, list[int]]:
    """Place each new diode into the nearest unused row gap while freezing all existing cells."""
    old = design_json["design"]
    rows = {row["y"]: (row["x_start"], row["x_end"]) for row in old["rows"]}
    ys = sorted(rows)
    site, height = old["site"]["width"], old["site"]["height"]
    gap = site * physical["cell_spacing_sites"]
    occupied: dict[int, list[tuple[int, int]]] = defaultdict(list)

    def reserve(x0: int, y0: int, x1: int, y1: int) -> None:
        a, b = bisect_right(ys, y0 - height), bisect_left(ys, y1)
        for row_y in ys[a:b]:
            occupied[row_y].append((x0, x1))

    for name, (master, x, y, _orient) in old_cells.items():
        if name not in existing and not name.startswith("rr_tap_"):
            continue
        width, cell_height = master_sizes[master]
        reserve(x - gap, y, x + width + gap, y + cell_height)
    for box in physical.get("placement_blockages", []):
        reserve(box["lx"], box["ly"], box["ux"], box["uy"])

    slots: dict[str, list[int]] = {}
    for name, info in sorted(anchors.items()):
        width, cell_height = master_sizes[operations[name]["cell"]]
        assert cell_height == height and width % site == 0
        px, py = info["preferred_lower_left_dbu"]
        best = None
        for y, (rx0, rx1) in rows.items():
            if best and abs(y - py) > best[0]:
                continue
            intervals = sorted(occupied[y])
            cursor = rx0
            for lo, hi in [*intervals, (rx1, rx1)]:
                if lo > cursor:
                    lo_x = rx0 + ((cursor - rx0 + site - 1) // site) * site
                    hi_x = rx0 + ((min(lo, rx1) - width - rx0) // site) * site
                    if lo_x <= hi_x:
                        x = max(lo_x, min(hi_x, rx0 + ((px - rx0 + site // 2) // site) * site))
                        key = (abs(x - px) + abs(y - py), abs(y - py), y, x)
                        if best is None or key < best:
                            best = key
                cursor = max(cursor, hi)
                if cursor >= rx1:
                    break
        if best is None:
            raise RuntimeError(f"No legal gap slot found for diode {name}")
        _, _, y, x = best
        slots[name] = [x, y]
        reserve(x - gap, y, x + width + gap, y + height)
    return slots


def export_workspace(
    runner: Runner,
    prefix: str,
    rustroad: Path,
    ws: Path,
    threads: int,
    cells_gds: Path,
    layer_map: Path,
    rc_profile: Path,
    top: str,
    exports_dir: Path,
    synth_netlist: Path,
    *,
    do_extract: bool = True,
) -> dict:
    exports_dir.mkdir(parents=True, exist_ok=True)
    gds_out = exports_dir / f"{top}.gds"
    def_out = exports_dir / f"{top}.def"
    v_out = exports_dir / f"{top}.v"
    pv_out = exports_dir / f"{top}.powered.v"
    spef_out = exports_dir / "native.nom.spef"
    road_cmd = [rustroad, "--workspace", ws, "--threads", threads]
    runner.run(
        f"{prefix}_gds",
        [
            *road_cmd,
            "export-gds",
            "--cell-gds",
            cells_gds,
            "--layer-map",
            layer_map,
            "--out",
            gds_out,
            "--merge-shapes",
            "rectangles",
            "--vias",
            "flat",
        ],
        timeout=120,
    )
    runner.run(f"{prefix}_def", [*road_cmd, "export-def"], timeout=90, stdout_path=def_out)
    runner.run(f"{prefix}_verilog", [*road_cmd, "export-verilog"], timeout=90, stdout_path=v_out)
    runner.run(
        f"{prefix}_powered",
        [*road_cmd, "export-verilog", "--include-power"],
        timeout=90,
        stdout_path=pv_out,
    )
    arts = {
        "gds": record(gds_out),
        "def": record(def_out),
        "netlist": record(v_out),
        "powered_netlist": record(pv_out),
        "synthesis_netlist": record(synth_netlist),
    }
    if do_extract:
        runner.run(
            f"{prefix}_extract",
            [
                *road_cmd,
                "--max-expansions",
                8000000,
                "extract",
                "--rc-profile",
                rc_profile,
                "--corner",
                "nom",
                "--out",
                spef_out,
                "--max-spef-mib",
                256,
                "--extract-memory-mib",
                3584,
                "--max-extract-shapes",
                4000000,
                "--max-rc-nodes",
                2000000,
                "--max-rc-elements",
                30000000,
                "--max-coupling-caps",
                10000000,
                "--max-extract-work",
                100000000000,
                "--max-extract-candidates",
                65536,
                "--min-coupling-ff",
                "0.1",
            ],
            timeout=300,
        )
        arts["spef_native_nom"] = record(spef_out)
    return arts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", type=Path, help="Host JSON naming single standard release tools & PDK")
    ap.add_argument("--out", type=Path, required=True, help="New output directory")
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="Clean SKY130A native profile JSON")
    ap.add_argument("--threads", type=int, default=2, help="Worker thread count (default: 2)")
    ap.add_argument("--pdk-root", type=Path, help="SKY130A PDK root (used when --host is omitted or to override)")
    ap.add_argument("--prep-root", type=Path, help="Prepared RTL/SDC/macro root (used when --host is omitted or to override)")
    ap.add_argument("--rusteda", type=Path, help="Standard non-PGO rusteda binary override")
    ap.add_argument("--rustsys", type=Path, help="Standard non-PGO rustsys binary override")
    ap.add_argument("--ruststa", type=Path, help="Standard non-PGO ruststa binary override")
    ap.add_argument("--rustroad", type=Path, help="Standard non-PGO rustroad binary override")
    ap.add_argument("--pin-caps", type=Path, help="export_pin_caps helper binary override")
    ap.add_argument("--checker", type=Path, help="check_config helper binary override")
    ap.add_argument("--ref-tools", type=Path, help="Reference-tools directory for optional host fields")
    ap.add_argument("--skip-antenna-closure", action="store_true", help="Stop after cold flow + postroute-repair")
    args = ap.parse_args(argv)

    t_start = time.monotonic()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f"OUTPUT_EXISTS: {out}")
    out.mkdir(parents=True)

    profile_path = args.profile.resolve(strict=True)
    profile = json.loads(profile_path.read_text())
    top = profile["top"]

    if args.host is not None:
        host_src = json.loads(args.host.resolve(strict=True).read_text())
    else:
        if args.pdk_root is None or args.prep_root is None:
            raise SystemExit("Either --host or both --pdk-root and --prep-root are required")
        ref_dir = (args.ref_tools or (ROOT / ".work/three-pdk-20261114/reference-tools")).resolve()
        host_src = {
            "tools": {
                "rusteda": str((args.rusteda or (ROOT / ".work/target/RustEDA/release/rusteda")).resolve(strict=True)),
                "rustsys": str((args.rustsys or (ROOT / ".work/target/RustSys/release/rustsys")).resolve(strict=True)),
                "ruststa": str((args.ruststa or (ROOT / ".work/target/RustSTA/release/ruststa")).resolve(strict=True)),
                "rustroad": str((args.rustroad or (ROOT / ".work/target/RustROAD/release/rustroad")).resolve(strict=True)),
                "pin_caps": str((args.pin_caps or (ROOT / ".work/three-pdk-20261114/bin/baseline/export_pin_caps")).resolve(strict=True)),
                "checker": str((args.checker or (ROOT / ".work/three-pdk-20261114/bin/baseline/check_config")).resolve(strict=True)),
                "python": sys.executable,
                "gnu_time": "/usr/bin/time",
                "xxhsum": "/usr/bin/xxhsum",
                "appimage": str(ref_dir / "eda"),
                "opensta": str(ref_dir / "bin/sta"),
                "yosys": str(ref_dir / "bin/yosys"),
                "abc": str(ref_dir / "bin/yosys-abc"),
                "iverilog": str(ref_dir / "bin/iverilog"),
                "vvp": str(ref_dir / "bin/vvp"),
            },
            "backend_steps": "native",
            "profile": str(profile_path),
            "prepared": str(args.prep_root.resolve(strict=True)),
            "roots": {
                "pdk": str(args.pdk_root.resolve(strict=True)),
                "prep": str(args.prep_root.resolve(strict=True)),
            },
            "threads": args.threads,
            "parallel_signoff": False,
        }
    for key, val in (
        ("rusteda", args.rusteda),
        ("rustsys", args.rustsys),
        ("ruststa", args.ruststa),
        ("rustroad", args.rustroad),
        ("pin_caps", args.pin_caps),
        ("checker", args.checker),
    ):
        if val is not None:
            host_src["tools"][key] = str(val.resolve(strict=True))
    if args.pdk_root is not None:
        host_src.setdefault("roots", {})["pdk"] = str(args.pdk_root.resolve(strict=True))
    if args.prep_root is not None:
        prep_resolved = str(args.prep_root.resolve(strict=True))
        host_src["prepared"] = prep_resolved
        host_src.setdefault("roots", {})["prep"] = prep_resolved

    tools = host_src["tools"]
    rustroad = Path(tools["rustroad"]).resolve(strict=True)
    rusteda = Path(tools["rusteda"]).resolve(strict=True)
    rustsys = Path(tools["rustsys"]).resolve(strict=True)
    ruststa = Path(tools["ruststa"]).resolve(strict=True)
    gnu_time = tools.get("gnu_time", "/usr/bin/time")
    runner = Runner(out, gnu_time=gnu_time)

    # Bind host map with exact threads, cpu_affinity matching current process, and profile.
    host_local = json.loads(json.dumps(host_src))
    host_local["profile"] = str(profile_path)
    host_local["threads"] = args.threads
    aff = sorted(os.sched_getaffinity(0))
    if len(aff) >= args.threads:
        host_local["cpu_affinity"] = aff[: args.threads]
    host_local_path = out / "host.json"
    save_json(host_local_path, host_local)

    # Step 1: Compose standard single-binary manifest and execute cold RTL-to-GDS.
    compose_ws = out / "compose"
    runner.run(
        "compose_native",
        [
            sys.executable,
            ROOT / "integration/ibex_host_benchmark/compose_native.py",
            "--host",
            host_local_path,
            "--workspace",
            compose_ws,
            "--base-rev",
            "empty",
            "--profile",
            profile_path,
        ],
        timeout=180,
    )
    manifest = compose_ws / "be/prefix-manifest.json"
    cold_out = out / "cold"
    worker_id = hashlib.sha256(str(out).encode()).hexdigest()
    runner.run(
        "cold_rtl_to_gds",
        [
            rusteda,
            "flow",
            "exec",
            "--manifest",
            manifest,
            "--output",
            cold_out,
            "--base-rev",
            "empty",
            "--stop-after",
            "be_gds",
            "--worker-id",
            worker_id,
            "--ownership",
            out / "cold-ownership",
            "--events",
            out / "cold-events.jsonl",
        ],
        timeout=2400,
    )

    cold_road = cold_out / "backend/postroute/road"
    cold_rev = head_rev(cold_road)
    synth_netlist = cold_out / "frontend/mapped.v"
    cells_gds = cold_out / "backend/profiles/combined-cells.gds"
    layer_map = cold_out / "backend/profiles/gds-map.json"
    rc_profile = cold_out / "backend/profiles/rc-nom.json"
    cell_lef = compose_ws / "collateral/combined-cells.lef"
    tech_lef = Path(host_local["roots"]["pdk"]) / profile["lef"]["technology"]["path"].split(":", 1)[1]
    floorplan = profile_path.parent / "floorplan.json"
    cold_physical = cold_out / "frontend/physical-pins/physical.json"
    cold_routing = cold_out / "frontend/physical/routing.json"

    final_ws = cold_road
    final_artifacts = {
        "gds": record(cold_out / f"backend/{top}.gds"),
        "def": record(cold_out / "logs/be_def.stdout"),
        "netlist": record(cold_out / "logs/be_netlist.stdout"),
        "powered_netlist": record(cold_out / "logs/be_powered.stdout"),
        "synthesis_netlist": record(synth_netlist),
        "spef_native_nom": record(cold_out / "backend/native.nom.spef"),
    }
    closure_summary: dict = {"enabled": not args.skip_antenna_closure}

    if not args.skip_antenna_closure:
        closure = out / "antenna-closure"
        closure.mkdir()
        road_sha = sha256(rustroad)
        model_sha = sha256(MODEL)

        # Step 2a: Native batched contact-sidewall antenna screen.
        screen_dir = closure / "screen"
        runner.run(
            "antenna_batch_screen",
            [sys.executable, HORIZ / "batch.py", cold_road, rustroad, screen_dir, MODEL],
            timeout=180,
        )

        # Step 2b: General native diode selection with native RustSTA DRV headroom gating.
        blocked_drv = blocked_nets_from_native_drv(cold_out / "backend/postroute/logs")
        eco_plan, anchors, sel_stats = select_diode_targets(
            screen_dir / "complete.json",
            cold_out / "logs/be_def.stdout",
            cell_lef,
            blocked_drv,
            top,
        )
        closure_summary["diode_selection"] = sel_stats
        eco_plan_path = closure / "diode-eco.json"
        save_json(eco_plan_path, eco_plan)
        save_json(closure / "diode-anchors.json", anchors)

        if eco_plan["operations"]:
            diode_v = closure / "diode-netlist.v"
            runner.run(
                "apply_diode_eco",
                [
                    rustroad,
                    "apply-eco",
                    "--netlist",
                    cold_out / "logs/be_netlist.stdout",
                    "--plan",
                    eco_plan_path,
                    "--delay-cells",
                    cold_out / "frontend/profiles/delay.json",
                    "--cell-buses",
                    cold_out / "frontend/profiles/cell-buses.json",
                    "--cell-lef",
                    cell_lef,
                    "--out",
                    diode_v,
                ],
                timeout=90,
            )

            # Step 2c: Gap-slot seeded placement preserving all existing cells & clock tree.
            seed_path = closure / "prior-seed.json"
            runner.run(
                "export_prior_seed",
                [rustroad, "--workspace", cold_road, "--threads", args.threads, "export-placement-seed"],
                timeout=60,
                stdout_path=seed_path,
            )
            seed = json.loads(seed_path.read_bytes())
            prior_cells = dict(seed["cells"])
            existing = set(prior_cells)
            old_cells, _, old_nets, _, _ = read_def(str(cold_out / "logs/be_def.stdout"), 200000, 4096)
            phys_cfg = json.loads(cold_physical.read_bytes())
            phys_cfg["placer"]["detailed_passes"] = 0
            phys_noshift = closure / "physical-seeded.json"
            save_json(phys_noshift, phys_cfg)
            design_json = json.loads((cold_road / "revisions" / cold_rev / "design.json").read_bytes())
            ops_by_name = {op["instance"]: op for op in eco_plan["operations"]}
            slots = positions_from_gaps(
                design_json, phys_cfg, old_cells, existing, lef_sizes(cell_lef), ops_by_name, anchors
            )
            for inst_name, xy in slots.items():
                seed["cells"][inst_name] = xy
            anchored_seed = closure / "diode-seed.json"
            save_json(anchored_seed, seed)

            diode_ws = closure / "road"
            runner.run(
                "diode_import",
                [
                    rustroad,
                    "--workspace",
                    diode_ws,
                    "--threads",
                    args.threads,
                    "--max-expansions",
                    4000000,
                    "import-pdk",
                    "--base-rev",
                    "rev0",
                    "--tech-lef",
                    tech_lef,
                    "--cell-lef",
                    cell_lef,
                    "--netlist",
                    diode_v,
                    "--floorplan",
                    floorplan,
                    "--physical-config",
                    phys_noshift,
                ],
                timeout=120,
            )
            runner.run(
                "diode_place",
                [
                    rustroad,
                    "--workspace",
                    diode_ws,
                    "--threads",
                    args.threads,
                    "--max-expansions",
                    4000000,
                    "place",
                    "--base-rev",
                    "rev1",
                    "--seed",
                    anchored_seed,
                ],
                timeout=120,
            )
            post_seed_path = closure / "post-diode-seed.json"
            runner.run(
                "verify_diode_seed",
                [rustroad, "--workspace", diode_ws, "--threads", args.threads, "export-placement-seed"],
                timeout=60,
                stdout_path=post_seed_path,
            )
            post_cells = json.loads(post_seed_path.read_bytes()).get("cells", {})
            moved_set = {k for k, pos in prior_cells.items() if post_cells.get(k) != pos}
            diode_nets = {op["net"] for op in eco_plan["operations"]}
            diode_net_insts = {
                inst for net, ts in old_nets if net in diode_nets for inst, _ in ts if inst in prior_cells
            }
            moved_on_diode_nets = len(moved_set & diode_net_insts)
            closure_summary["diode_place_moved_existing"] = len(moved_set)
            closure_summary["diode_place_moved_on_diode_nets"] = moved_on_diode_nets

            # Step 2d: Incremental ECO route preserving unchanged routes & diode escapes.
            route_cmd = [
                rustroad,
                "--workspace",
                diode_ws,
                "--threads",
                args.threads,
                "--max-expansions",
                80000000,
                "route",
                "--base-rev",
                "rev2",
                "--scope",
                "all",
                "--config",
                cold_routing,
                "--prior-workspace",
                cold_road,
                "--prior-rev",
                cold_rev,
                "--prior-diode-plan",
                eco_plan_path,
            ]
            if moved_on_diode_nets == 0:
                route_cmd.append("--preserve-prior-escapes")
            runner.run("diode_eco_route", route_cmd, timeout=1800)
            final_ws = diode_ws
            # Intermediate export for ranked_metal / generate provenance checks.
            mid_exports = closure / "mid-exports"
            mid_arts = export_workspace(
                runner,
                "mid_export",
                rustroad,
                final_ws,
                args.threads,
                cells_gds,
                layer_map,
                rc_profile,
                top,
                mid_exports,
                synth_netlist,
                do_extract=False,
            )
            mid_producer = closure / "mid-producer.json"
            save_json(mid_producer, {"final_revision": head_rev(final_ws), "artifacts": mid_arts})

            # Step 2e: Native ranked metal grouped jumper repair (up to RANKED_METAL_PASSES passes).
            grouped_passes = []
            cur_producer = mid_producer
            for rm_pass in range(RANKED_METAL_PASSES):
                metal_dir = closure / f"ranked-metal-p{rm_pass}"
                rm_res = runner.run(
                    f"ranked_metal_select_p{rm_pass}",
                    [
                        sys.executable,
                        HORIZ / "ranked_metal.py",
                        "--workspace",
                        final_ws,
                        "--producer",
                        cur_producer,
                        "--out",
                        metal_dir,
                        "--native",
                        rustroad,
                        "--native-sha256",
                        road_sha,
                        "--model",
                        MODEL,
                        "--model-sha256",
                        model_sha,
                    ],
                    timeout=240,
                    check=False,
                )
                if rm_res["exit_code"] != 0 or not (metal_dir / "plan.json").is_file():
                    grouped_passes.append({"pass": rm_pass, "skipped": True, "exit_code": rm_res["exit_code"]})
                    break
                base_r = head_rev(final_ws)
                grp_res = runner.run(
                    f"repair_antenna_groups_p{rm_pass}",
                    [
                        rustroad,
                        "--workspace",
                        final_ws,
                        "--threads",
                        args.threads,
                        "repair-antenna-groups",
                        "--base-rev",
                        base_r,
                        "--plan",
                        metal_dir / "plan.json",
                    ],
                    timeout=480,
                )
                grp_data = json.loads(Path(grp_res["stdout"]).read_bytes())
                jumpers = int(grp_data.get("total_jumpers") or 0)
                grouped_passes.append(
                    {
                        "pass": rm_pass,
                        "committed_revision": grp_data.get("committed_revision"),
                        "total_jumpers": jumpers,
                    }
                )
                # Export updated revision for the next pass or horizontal V3 scout.
                pass_exports = closure / f"grouped-exports-p{rm_pass}"
                pass_arts = export_workspace(
                    runner,
                    f"grouped_export_p{rm_pass}",
                    rustroad,
                    final_ws,
                    args.threads,
                    cells_gds,
                    layer_map,
                    rc_profile,
                    top,
                    pass_exports,
                    synth_netlist,
                    do_extract=False,
                )
                cur_producer = closure / f"grouped-producer-p{rm_pass}.json"
                save_json(cur_producer, {"final_revision": head_rev(final_ws), "artifacts": pass_arts})
                if jumpers == 0:
                    break
            closure_summary["grouped_jumpers"] = {
                "passes": grouped_passes,
                "total_jumpers": sum(p.get("total_jumpers", 0) for p in grouped_passes),
                "committed_revision": head_rev(final_ws),
            }
            grp_producer = cur_producer

            # Step 2f: Native horizontal V3/V4 local via-offset repair (up to HORIZ_V3_PASSES passes;
            # a second pass catches spillover when pass 0 hits the 64-candidate per-call bound).
            vo_passes = []
            cur_vo_producer = grp_producer
            for vo_pass in range(HORIZ_V3_PASSES):
                horiz_dir = closure / f"horiz-v3-p{vo_pass}"
                hz_res = runner.run(
                    f"horiz_v3_select_p{vo_pass}",
                    [
                        sys.executable,
                        HORIZ / "generate.py",
                        "--workspace",
                        final_ws,
                        "--producer",
                        cur_vo_producer,
                        "--routing-config",
                        cold_routing,
                        "--out",
                        horiz_dir,
                        "--native",
                        rustroad,
                        "--native-sha256",
                        road_sha,
                        "--model",
                        MODEL,
                        "--model-sha256",
                        model_sha,
                        "--threshold",
                        str(HORIZ_V3_THRESHOLD),
                    ],
                    timeout=300,
                    check=False,
                )
                lower_plan_path = horiz_dir / "scout/lower-offset-plan.json"
                if hz_res["exit_code"] != 0 or not lower_plan_path.is_file():
                    vo_passes.append({"pass": vo_pass, "skipped": True, "exit_code": hz_res["exit_code"]})
                    break
                lower_plan = json.loads(lower_plan_path.read_bytes())
                cands = lower_plan.get("candidates") or []
                if not cands:
                    vo_passes.append({"pass": vo_pass, "candidates": 0, "accepted": 0})
                    break
                ready_plan = closure / f"via-offset-plan-p{vo_pass}.json"
                save_json(
                    ready_plan,
                    {
                        **lower_plan,
                        "fill_aligned_pad_notch": True,
                        "eager_lower_pad_gap_fill": True,
                    },
                )
                base_r = head_rev(final_ws)
                vo_res = runner.run(
                    f"repair_via_offsets_p{vo_pass}",
                    [
                        rustroad,
                        "--workspace",
                        final_ws,
                        "--threads",
                        args.threads,
                        "repair-via-offsets",
                        "--base-rev",
                        base_r,
                        "--plan",
                        ready_plan,
                    ],
                    timeout=360,
                )
                vo_data = json.loads(Path(vo_res["stdout"]).read_bytes())
                accepted = int(vo_data.get("accepted") or 0)
                vo_passes.append(
                    {
                        "pass": vo_pass,
                        "candidates": len(cands),
                        "accepted": accepted,
                        "revision": vo_data.get("revision"),
                    }
                )
                if accepted == 0 or len(cands) < 64 or vo_pass + 1 == HORIZ_V3_PASSES:
                    break
                vo_exports = closure / f"vo-exports-p{vo_pass}"
                vo_arts = export_workspace(
                    runner,
                    f"vo_export_p{vo_pass}",
                    rustroad,
                    final_ws,
                    args.threads,
                    cells_gds,
                    layer_map,
                    rc_profile,
                    top,
                    vo_exports,
                    synth_netlist,
                    do_extract=False,
                )
                cur_vo_producer = closure / f"vo-producer-p{vo_pass}.json"
                save_json(cur_vo_producer, {"final_revision": head_rev(final_ws), "artifacts": vo_arts})
            closure_summary["via_offsets"] = {
                "passes": vo_passes,
                "candidates": sum(p.get("candidates", 0) for p in vo_passes),
                "accepted": sum(p.get("accepted", 0) for p in vo_passes),
                "revision": head_rev(final_ws),
            }

            # Step 2g: Final exports & native extraction.
            final_artifacts = export_workspace(
                runner,
                "final_export",
                rustroad,
                final_ws,
                args.threads,
                cells_gds,
                layer_map,
                rc_profile,
                top,
                out / "final",
                synth_netlist,
                do_extract=True,
            )

    # Ensure clean canonical copies under out/final/ even if closure was skipped.
    final_dir = out / "final"
    if not final_dir.exists():
        final_dir.mkdir()
        for key, fname in (
            ("gds", f"{top}.gds"),
            ("def", f"{top}.def"),
            ("netlist", f"{top}.v"),
            ("powered_netlist", f"{top}.powered.v"),
            ("spef_native_nom", "native.nom.spef"),
        ):
            dst = final_dir / fname
            shutil.copyfile(final_artifacts[key]["path"], dst)
            final_artifacts[key] = record(dst)

    layout = {
        "gds": final_artifacts["gds"]["path"],
        "def": final_artifacts["def"]["path"],
        "netlist": final_artifacts["netlist"]["path"],
        "powered_netlist": final_artifacts["powered_netlist"]["path"],
        "synthesis_netlist": final_artifacts["synthesis_netlist"]["path"],
    }
    save_json(out / "layout.json", layout)
    producer_receipt = {
        "schema": "rusteda.clean-sky130-producer.v1",
        "top": top,
        "profile": record(profile_path),
        "tools": {
            "rustroad": record(rustroad),
            "rusteda": record(rusteda),
            "rustsys": record(rustsys),
            "ruststa": record(ruststa),
        },
        "threads": args.threads,
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "final_workspace": str(final_ws),
        "final_revision": head_rev(final_ws),
        "wall_seconds": round(time.monotonic() - t_start, 4),
        "antenna_closure": closure_summary,
        "stages": runner.stages,
        "artifacts": final_artifacts,
    }
    save_json(out / "producer.json", producer_receipt)
    print(
        json.dumps(
            {
                "status": "completed",
                "wall_seconds": producer_receipt["wall_seconds"],
                "gds_sha256": final_artifacts["gds"]["sha256"],
                "antenna_closure": closure_summary,
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

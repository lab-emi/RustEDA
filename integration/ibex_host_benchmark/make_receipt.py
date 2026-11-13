#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Assemble the host campaign receipt and its bounded evidence inventory.

Reads the completed campaign directory (`final_campaign.sh`), recomputes the
neutral QoR measurements (`def_metrics`, `final_density`, `netlist_qor`), and
writes `docs/benchmarks/ibex-sg13g2-ez130-host-<date>.json` plus small evidence
files under `docs/benchmarks/evidence/ibex-host-<date>/`. Nothing is invented:
every value is read from a run output or recomputed from a retained artifact,
and missing values stay null. Comparative ratios follow the publication rule.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
GATES = ("drc", "antenna", "latchup", "lvs", "timing", "simulation", "synthesis_equivalence",
         "final_equivalence", "density")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def maybe(path):
    path = Path(path)
    return read(path) if path.is_file() else None


def run_json(argv):
    return json.loads(subprocess.run(argv, check=True, capture_output=True, text=True).stdout)


def gnu_time(path):
    text = Path(path).read_text()
    wall = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", text)[1]
    parts = [float(p) for p in wall.split(":")]
    seconds = parts[-1] + 60 * parts[-2] + (3600 * parts[-3] if len(parts) == 3 else 0)
    rss = int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)[1])
    return dict(wall_seconds=round(seconds, 2), peak_rss_kib=rss)


def shared_gates(summary):
    """Gate verdicts from signoff_layout.py's summary (recipe scripts' own fields)."""
    g = summary["gates"]
    out = {}
    def status(name, report):
        return report.get("status") if isinstance(report, dict) else None
    for name in ("drc", "antenna"):
        r = (g.get(name) or {}).get("report") or {}
        out[name] = dict(status=r.get("status"), violations=r.get("violations"), scope=r.get("scope"),
                         disabled_rules=r.get("disabled_rules"))
    lu = g.get("latchup") or {}
    controller, coverage = ((lu.get("report") or {}).get(k) or {} for k in ("controller", "summary"))
    # The controller passes only on a zero exit with both rules passed and the report bound to the GDS.
    out["latchup"] = dict(status=(controller.get("status") if lu.get("exit_code") == 0 else "unverified") if lu
                          else None,
                          scope="tap_coverage.py: LU.a and LU.b, 20 um, conservative L1 box certificate, LU.a also in-well",
                          # Reported boxes of both certificates (each list is capped at 32 by the checker).
                          rules={k: dict(status=v.get("status"), target_polygons=v.get("target_polygons"),
                                         uncertified_boxes=len(v.get("uncertified_boxes") or [])
                                         + len(v.get("in_well_uncertified_boxes") or []))
                                 for k, v in (coverage.get("rules") or {}).items()})
    r = (g.get("lvs") or {}).get("report") or {}
    out["lvs"] = dict(status=r.get("status"), top_port_identity=r.get("top_port_identity"),
                      macro_instances=r.get("macro_instances"), macro_devices_verified=r.get("macro_devices_verified"))
    r = (g.get("timing") or {}).get("report") or {}
    corners = {}
    for corner, row in (r.get("corners") or {}).items():
        corners[corner] = {k: row.get(k) for k in ("setup_ws_ns", "hold_ws_ns", "setup_violations", "hold_violations",
                                                    "slew_violations", "cap_violations", "fanout_violations",
                                                    "active_unconstrained_endpoints", "unconstrained_endpoints")}
    out["timing"] = dict(status=r.get("status"), source="OpenRCX nominal SPEF; min(RustSTA, OpenSTA)",
                         corners=corners)
    r = (g.get("simulation") or {}).get("report") or {}
    out["simulation"] = dict(status=r.get("status"))
    logic = (g.get("logic") or {}).get("report") or {}
    for name in ("synthesis_equivalence", "final_equivalence"):
        row = logic.get(name) or {}
        out[name] = dict(status=row.get("status"), error=row.get("error"))
    r = (g.get("density") or {}).get("report") or {}
    a = r.get("area_dbu2") or {}
    out["density"] = dict(status=r.get("status"), max_percent=r.get("max_percent"),
                          final_nonfiller_utilization_percent=r.get("final_nonfiller_utilization_percent"),
                          macro_inclusive_core_utilization_percent=r.get("macro_inclusive_core_utilization_percent"),
                          standard_cell_area_um2=round(a["final_nonfiller_standard_cells"] / 1e6, 1) if a else None,
                          filler_decap_area_um2=round(a["reported_separately_filler_decap"] / 1e6, 1) if a else None)
    return out


# Shared gates the native producer can run itself, as the step that runs the same script.
PRODUCER_CHECKS = {"drc": ("be_main", "main"), "antenna": ("be_antenna", "antenna"), "lvs": ("be_lvs", "lvs")}
# Shared gates whose time the end-to-end total adds unless the producer ran the identical check.
# extraction and timing are covered by the producer's own be_openrcx/be_timing steps, as before.
COMPLETION_GATES = ("drc", "antenna", "latchup", "lvs", "simulation", "logic", "density")
DRC_FIELDS = ("status", "complete", "violations", "categories", "deck_sha256", "disabled_rules", "scope")
LVS_JOB_FIELDS = ("top", "macro", "macro_count", "qualified_frozen_hashes")
LVS_SOURCES = ("gds", "powered_netlist", "deck", "interfaces", "macro_qualification", "tool_appimage",
               "tool_yosys", "tool_time", "source_signoff_lvs.py", "source_build_lvs_reference.py",
               "source_check_lvs.py", "python")


def _normalized(command, replacements):
    """A command line with run-specific paths (workspace, input file) replaced by placeholders."""
    out = []
    for arg in command:
        for old, new in replacements:
            arg = arg.replace(old, new)
        out.append(arg)
    return out


def _same_drc(producer, shared):
    """Identical foundry DRC/antenna check: same deck, profile, tool, switches and GDS bytes, both complete."""
    a, b = (maybe(d / "summary.json") for d in (producer, shared))
    ca, cb = (maybe(d / "command.json") for d in (producer, shared))
    if not (a and b and ca and cb) or a.get("complete") is not True or b.get("complete") is not True:
        return "missing or incomplete report"
    for field in DRC_FIELDS:
        if a.get(field) != b.get(field):
            return f"{field} differs"
    for field in ("profile", "gds", "tool"):
        if (a.get(field) or {}).get("sha256") != (b.get(field) or {}).get("sha256"):
            return f"{field} sha256 differs"
    na = _normalized(ca, [(str(producer), "<workspace>"), (a["gds"]["path"], "<gds>")])
    nb = _normalized(cb, [(str(shared), "<workspace>"), (b["gds"]["path"], "<gds>")])
    return None if na == nb else "command differs"


def _same_lvs(producer, shared):
    """Identical device LVS: same deck, CDL, netlist, GDS, helpers and KLayout switches, both complete.

    The producer's job may carry a shorter deadline and expected-hash preconditions; those bound or
    guard the run but do not change what is compared, so they are not part of the identity.
    """
    a, b = (maybe(d / "summary.json") for d in (producer, shared))
    ja, jb = (maybe(d / "job.json") for d in (producer, shared))
    ca, cb = (maybe(d / "klayout-command.json") for d in (producer, shared))
    if not (a and b and ja and jb and ca and cb) or a.get("complete") is not True or b.get("complete") is not True:
        return "missing or incomplete report"
    if a.get("status") != b.get("status") or a.get("reference_cdl_sha256") != b.get("reference_cdl_sha256"):
        return "verdict or reference netlist differs"
    for field in LVS_JOB_FIELDS:
        if ja.get(field) != jb.get(field):
            return f"{field} differs"
    if (ja.get("limits") or {}).get("threads") != (jb.get("limits") or {}).get("threads"):
        return "threads differ"
    sa, sb = ja.get("sources") or {}, jb.get("sources") or {}
    cdl = sorted(k for k in set(sa) | set(sb) if k.startswith("cdl_"))
    for key in (*LVS_SOURCES, *cdl):
        if (sa.get(key) or {}).get("sha256") != (sb.get(key) or {}).get("sha256"):
            return f"{key} sha256 differs"
    na = _normalized(ca, [(ja["output"], "<workspace>"), (ja["gds"], "<gds>")])
    nb = _normalized(cb, [(jb["output"], "<workspace>"), (jb["gds"], "<gds>")])
    return None if na == nb else "command differs"


def producer_identical_checks(output, signoff, receipts):
    """Shared gates the producer ran itself as the identical check on the same final layout.

    `output` is the producer's output directory, `signoff` the shared-gate workspace and
    `receipts` the producer's step receipts. A gate counts as covered only when the producer
    step exited 0 and both runs used the same script, deck, rule selection, switches and input
    bytes (`_same_drc`, `_same_lvs`); every other shared gate keeps its completion time.
    Returns {gate: {"step", "identical", "reason"}}.
    """
    exits = {r.get("id"): r.get("exit_code") for r in receipts if r.get("kind") == "command"}
    out = {}
    for gate, (step, folder) in PRODUCER_CHECKS.items():
        if exits.get(step) != 0:
            reason = "producer step not run" if step not in exits else "producer step failed"
        else:
            same = _same_lvs if gate == "lvs" else _same_drc
            reason = same(Path(output) / "backend" / folder, Path(signoff) / gate)
        out[gate] = dict(step=step, identical=reason is None, reason=reason)
    return out


def completion_seconds(resources, identical):
    """Wall seconds of the shared gates the producer did not run identically, and their names."""
    gates = [g for g in COMPLETION_GATES if g in resources and not (identical.get(g) or {}).get("identical")]
    return round(sum(resources[g]["wall_seconds"] for g in gates), 2), gates


def _union_seconds(intervals):
    """Length of the union of [start, end) intervals."""
    total, end = 0.0, None
    for lo, hi in sorted(intervals):
        if end is None or lo > end:
            total, end = total + hi - lo, hi
        elif hi > end:
            total, end = total + hi - end, hi
    return total


def check_schedule(receipts, identical):
    """How the producer ran the foundry checks that cover shared gates (`producer_identical_checks`).

    Steps of a producer parallel group carry `started_seconds` and `cpu_affinity`: steps on the
    same CPU slots form one lane and run one after another, lanes run side by side. Steps without
    them ran serially. Returns the lanes and serial gates (shared-gate names), the serial sum of
    the steps' wall seconds, their elapsed span, and `mode` ("concurrent" when two or more lanes
    overlapped, "serial" otherwise, "none" when the producer covered no shared gate).
    """
    rows = {r.get("id"): r for r in receipts if r.get("kind") == "command"}
    lanes, serial, intervals, walls = {}, [], [], 0.0
    for gate, check in identical.items():
        if not check.get("identical"):
            continue
        row = rows[check["step"]]
        wall = row.get("wall_seconds") or 0.0
        walls += wall
        if row.get("started_seconds") is not None and row.get("cpu_affinity"):
            lanes.setdefault(tuple(row["cpu_affinity"]), []).append((row["started_seconds"], gate))
            intervals.append((row["started_seconds"], row["started_seconds"] + wall))
        else:
            serial.append(gate)
    span = _union_seconds(intervals) + sum(rows[identical[g]["step"]].get("wall_seconds") or 0.0 for g in serial)
    lanes = sorted([g for _, g in sorted(lane)] for lane in lanes.values())
    mode = "none" if not (lanes or serial) else ("concurrent" if len(lanes) > 1 and walls - span > 1e-6 else "serial")
    return dict(mode=mode, lanes=lanes, serial_gates=serial, serial_sum_seconds=round(walls, 2),
                span_seconds=round(span, 2))


def scheduled_seconds(seconds, schedule):
    """Wall seconds of the checks in `schedule` with durations `seconds` (by shared-gate name) under
    that schedule: lanes side by side (the longest lane), each lane's checks and the serial checks
    one after another. With every check serial this is their plain sum."""
    lanes = max((sum(seconds[g] for g in lane) for lane in schedule["lanes"]), default=0.0)
    return round(lanes + sum(seconds[g] for g in schedule["serial_gates"]), 2)


def shared_gate_seconds_on_schedule(resources, schedule):
    """All shared gates' wall seconds, with the gates in `schedule` charged under that schedule
    (`scheduled_seconds`) and every other gate one after another."""
    seconds = {g: v["wall_seconds"] for g, v in resources.items()}
    scheduled = {g for lane in schedule["lanes"] for g in lane} | set(schedule["serial_gates"])
    return round(sum(v for g, v in seconds.items() if g not in scheduled) + scheduled_seconds(seconds, schedule), 2)


def passed(gates):
    ok = {"passed", "proven"}
    return {k: (v.get("status") in ok) for k, v in gates.items()}


def checker_reason(path):
    r = maybe(path)
    return (r.get("undecided") or [None])[0] if r else None


def librelane_steps(run):
    rows = []
    for d in sorted(glob.glob(f"{run}/[0-9]*-*"), key=lambda p: int(Path(p).name.split("-", 1)[0])):
        rt = Path(d) / "runtime.txt"
        if rt.is_file():
            h, m, s = rt.read_text().strip().split(":")
            rows.append(dict(step=Path(d).name, seconds=round(int(h) * 3600 + int(m) * 60 + float(s), 3)))
    return rows


def native_stage_times(receipts):
    groups = {
        "synthesis (RustSys)": lambda i: i in ("fe_synth_init", "fe_synth_import", "fe_synth", "fe_synth_export"),
        "placement and CTS (RustROAD)": lambda i: i.startswith("fe_") and any(
            t in i for t in ("pdk_import", "_place", "_cts", "_export", "_seed", "_def", "inspect")),
        "frontend STA, ECO rounds and planning helpers (RustSTA, Python)": lambda i: i.startswith("fe_"),
        "routing (RustROAD)": lambda i: i == "be_route",
        "post-route repair": lambda i: i in ("be_postroute", "be_postroute_inputs"),
        # be_antenna is the foundry antenna table (a signoff check, like be_main), not repair.
        "antenna repair and verification": lambda i: i.startswith("be_antenna") and i not in (
            "be_antenna", "be_antenna_table"),
        "GDS/DEF/netlist export": lambda i: i in ("be_inspect", "be_def", "be_netlist", "be_powered", "be_gds"),
        "native extraction (RustROAD)": lambda i: i == "be_extract",
        "OpenRCX extraction": lambda i: i in ("be_openrcx", "be_rcx_inputs"),
        "signoff timing (RustSTA + OpenSTA)": lambda i: i in ("be_timing", "be_timing_inputs"),
        "foundry DRC, LVS, macro contract": lambda i: i in ("be_main", "be_antenna", "be_antenna_table", "be_lvs",
                                                            "be_lvs_inputs", "be_macro_contract"),
    }
    other_key = "other (renders, profiles, physical inputs)"
    out = {k: 0.0 for k in groups}
    out[other_key] = 0.0
    spans = {}
    for row in receipts:
        sid, wall = row["id"], row.get("wall_seconds") or 0.0
        key = next((k for k, test in groups.items() if test(sid)), other_key)
        if row.get("started_seconds") is None:
            out[key] += wall
        else:
            # Steps of a producer parallel group carry their start offset; overlapping steps of
            # one stage count their elapsed span once (the stage table stays a wall-clock table).
            spans.setdefault(key, []).append((row["started_seconds"], row["started_seconds"] + wall))
    for key, intervals in spans.items():
        end = None
        for lo, hi in sorted(intervals):
            if end is None or lo > end:
                out[key] += hi - lo
                end = hi
            elif hi > end:
                out[key] += hi - end
                end = hi
    return {k: round(v, 2) for k, v in out.items()}


def librelane_stage_times(steps):
    groups = {
        "synthesis (Yosys + ABC)": ("yosys-synthesis", "yosys-jsonheader"),
        "lint and checks": ("verilator", "checker-"),
        "floorplan, PDN, taps, IO": ("floorplan", "tapendcap", "generatepdn", "ioplacement", "cutrows",
                                     "macroplacement", "setpowerconnections", "pdnobstructions", "routingobstructions",
                                     "macroantenna", "dumprcvalues", "checkmacroinstances", "checksdcfiles",
                                     "applydeftemplate", "customioplacement", "writeverilogheader"),
        "placement (global + detailed)": ("globalplacement", "detailedplacement", "manualglobalplacement"),
        "resizer repair (design/timing)": ("repairdesign", "resizertiming"),
        "CTS": ("openroad-cts",),
        "global routing and antenna repair": ("globalrouting", "repairantennas", "checkantennas", "diodesonports"),
        "detailed routing": ("detailedrouting",),
        "STA (OpenSTA, pre/mid/post)": ("staprepnr", "stamidpnr", "stapostpnr"),
        "fill, RCX, IR drop, reports": ("fillinsertion", "openroad-rcx", "irdrop", "reportwirelength",
                                        "cellfrequency", "disconnectedpins", "removeroutingobstructions"),
        "streamout, render, LEF, XOR": ("streamout", "render", "writelef", "xor", "designantennaproperties"),
    }
    out = {k: 0.0 for k in groups}
    other = 0.0
    for row in steps:
        name = row["step"].split("-", 1)[1]
        for key, needles in groups.items():
            if any(n in name for n in needles):
                out[key] += row["seconds"]
                break
        else:
            other += row["seconds"]
    out["other"] = other
    return {k: round(v, 2) for k, v in out.items()}


# RustROAD steps whose thread count the receipt states: the recorded command's --threads value
# ("default": recorded without --threads, RustROAD's default 2-thread pool) or, for the Python repair
# helpers, the `limits.threads` of their input document. None means the step left no record.
THREAD_STEPS = ("fe_final_place", "fe_final_cts", "be_route", "be_extract", "be_gds")
THREAD_INPUTS = {"be_postroute": "backend/postroute-inputs.json", "be_antenna_repair": "backend/antenna-inputs.json"}


def recorded_threads(output):
    """Thread count each RustROAD step of a completed producer output actually ran with."""
    out = {}
    for step in THREAD_STEPS:
        record = maybe(Path(output) / "logs" / f"{step}.command.json")
        argv = record.get("argv") if isinstance(record, dict) else record
        if not argv:
            out[step] = None
        else:
            out[step] = int(argv[argv.index("--threads") + 1]) if "--threads" in argv else "default"
    for step, name in THREAD_INPUTS.items():
        out[step] = ((maybe(Path(output) / name) or {}).get("limits") or {}).get("threads")
    return out


def threads_text(threads):
    """Plain-language thread scope from `recorded_threads` ("default" = the default 2-thread pool,
    None = not recorded)."""
    names = dict(fe_final_place="frontend placement", fe_final_cts="CTS", be_route="route",
                 be_postroute="post-route repair", be_antenna_repair="antenna repair", be_extract="extract",
                 be_gds="export-gds")
    groups = {}
    for step, value in threads.items():
        groups.setdefault(value, []).append(names[step])
    parts = [f"{', '.join(v)} at --threads {k}" for k, v in sorted((k, v) for k, v in groups.items()
                                                                    if isinstance(k, int))]
    if "default" in groups:
        parts.append(f"{', '.join(groups['default'])} on the default 2-thread pool (no --threads)")
    if None in groups:
        parts.append(f"{', '.join(groups[None])}: thread count not recorded")
    return "; ".join(parts)


def thread_value_text(value):
    """One step's recorded thread count as text."""
    if value is None:
        return "not recorded"
    return "no --threads: default 2-thread pool" if value == "default" else f"--threads {value}"


def tool(path, version_cmd=None):
    p = Path(path).resolve()
    rec = dict(path=str(p), sha256=sha(p))
    if version_cmd:
        try:
            text = subprocess.run(version_cmd, capture_output=True, text=True, timeout=30).stdout
            rec["version"] = text.strip().splitlines()[0][:120] if text.strip() else None
        except (OSError, subprocess.TimeoutExpired):
            rec["version"] = None
    return rec


def synth_bench(path):
    rows = {}
    for line in Path(path).read_text().splitlines():
        name, rep, wall, rss = line.split("\t")
        if rep == "0":
            continue  # warm-up
        parts = [float(x) for x in wall.split(":")]
        seconds = parts[-1] + 60 * parts[-2] + (3600 * parts[-3] if len(parts) == 3 else 0)
        rows.setdefault(name, []).append((seconds, int(rss)))
    return {k: dict(runs=len(v), median_wall_seconds=round(statistics.median(s for s, _ in v), 3),
                    min_wall_seconds=round(min(s for s, _ in v), 3), worst_peak_rss_kib=max(r for _, r in v))
            for k, v in rows.items()}


def spice_suite(path):
    """Per-case accuracy and medians from `rustspice bench-suite --json` (its own field names)."""
    data = read(path)
    cases = []
    for case in data.get("cases", []):
        row = dict(id=case.get("id"), status=case.get("status"), size=case.get("size"),
                   accuracy=dict(err_rustspice=(case.get("accuracy") or {}).get("err_rs"),
                                 err_ngspice=(case.get("accuracy") or {}).get("err_ng"),
                                 passed=(case.get("accuracy") or {}).get("pass")))
        timing = case.get("timing") or {}
        med = {}
        for c in timing.get("contenders", []):
            med[c["name"]] = dict(e2e_ms=round(c["e2e_us"]["median"] / 1e3, 2) if c.get("e2e_us") else None,
                                  analysis_ms=round(c["analysis_us"]["median"] / 1e3, 2) if c.get("analysis_us") else None,
                                  rss_kib=(c.get("rss_kib") or {}).get("median"), failed_reps=c.get("failed_reps"),
                                  noisy=(c.get("e2e_us") or {}).get("noisy"))
        row["median"] = med
        ng = [v for k, v in med.items() if k.startswith("ngspice") and v["e2e_ms"]]
        rs = med.get("rustspice")
        if ng and rs and rs.get("e2e_ms"):
            best_e2e = min(v["e2e_ms"] for v in ng)
            row["speedup_end_to_end_vs_faster_ngspice"] = round(best_e2e / rs["e2e_ms"], 2)
            if rs.get("analysis_ms") and all(v.get("analysis_ms") for v in ng):
                row["speedup_solve_vs_faster_ngspice"] = round(min(v["analysis_ms"] for v in ng) / rs["analysis_ms"], 2)
        row["reps"] = timing.get("reps_done")
        cases.append(row)
    s = data.get("summary") or {}
    return dict(verdict=s.get("verdict"), counts=s.get("counts"), ngspice=data.get("ngspice"),
                startup_floor=data.get("startup_floor"), cases=cases)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--map", type=Path, help="optional JSON overriding run directory names")
    args = parser.parse_args()
    w = args.work.resolve()
    names = dict(native="native-final", signoff_native="signoff-native", librelane="ll-final/runs/final",
                 librelane_measure="ll-final/measure", signoff_librelane="signoff-librelane",
                 diag_librelane="ll-full/runs/full", diag_signoff="signoff-librelane-bringup",
                 synth="bench-synth", modules="bench-modules", spice="bench-spice")
    if args.map:
        names.update(read(args.map))
    host = read(w / "host.json")
    stamp = args.date.replace("-", "")
    receipt_path = ROOT / f"docs/benchmarks/ibex-sg13g2-ez130-host-{stamp}.json"
    evidence = ROOT / f"docs/benchmarks/evidence/ibex-host-{stamp}"
    shutil.rmtree(evidence, ignore_errors=True)
    evidence.mkdir(parents=True)
    inventory = []

    def keep(name, data=None, source=None):
        target = evidence / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source is not None:
            shutil.copyfile(source, target)
        else:
            target.write_text(json.dumps(data, indent=1) + "\n")
        size = target.stat().st_size
        if size > 2 << 20:
            raise SystemExit(f"evidence file over 2 MiB: {name}")
        inventory.append(dict(path=str(target.relative_to(ROOT / "docs")), sha256=sha(target), bytes=size))
        return target

    # ---------------- RustEDA native ----------------
    o = w / names["native"] / "output"
    nsum = read(o / "summary.json")
    receipts = read(o / "step-receipts.json")
    nmeasure = read(w / names["native"] / "measure/resources.json")
    sn = read(w / names["signoff_native"] / "summary.json")
    snm = read(w / f"{names['signoff_native']}.measure/resources.json")
    ngates = shared_gates(sn)
    parity = maybe(o / "backend/timing/parity.json")
    native_timing = maybe(o / "backend/timing/timing.json")
    producer_gates = {}
    if native_timing:
        producer_gates["timing"] = native_timing.get("status")
    if parity:
        producer_gates["timing_accuracy"] = dict(status=parity.get("status"),
                                                 max_abs_difference_ps=round(parity["max_abs_difference_ns"] * 1e3, 4),
                                                 tolerance_ps=round(parity["tolerance_ns"] * 1e3, 3),
                                                 differences=[dict(source=d["source"], corner=d["corner"], field=d["field"],
                                                                   ruststa_ns=round(d["ruststa_ns"], 6),
                                                                   opensta_ns=round(d["opensta_ns"], 6),
                                                                   abs_difference_ps=round(d["abs_difference_ns"] * 1e3, 4))
                                                              for d in parity["differences"]])
    # Each check is counted once: a shared gate the producer already ran as the identical check
    # (same script, deck, switches and input bytes) is covered by the producer's own step time;
    # the other shared gates complete the flow and their time is added.
    covered = producer_identical_checks(o, w / names["signoff_native"], receipts)
    completion, completion_gates = completion_seconds(sn["resources"], covered)
    # The producer may run the covered checks concurrently (a parallel group); LibreLane's shared
    # gates are run one at a time. Both end-to-end conventions are reported below.
    schedule = check_schedule(receipts, covered)
    prep = gnu_time(w / "prep/sv2v-time.txt")
    nqor_def = run_json([sys.executable, str(HERE / "def_metrics.py"), str(o / "logs/be_def.stdout")])
    threads = recorded_threads(o)
    native = dict(
        configuration="repository recipe (integration/ibex_sg13g2_ez130 at the campaign commit), composed by "
                      "compose_native.py; grouped single-process antenna session; RustROAD "
                      f"{threads_text(threads)}; producer foundry checks on the schedule in "
                      "foundry_check_schedule; unqualified",
        rustroad_threads=threads,
        producer=dict(status=nsum["status"], error=nsum.get("error"), failed_items=nsum.get("failed_items"),
                      steps_completed=len(receipts), manifest_steps=len(read(w / "compose/be/prefix-manifest.json")["steps"]),
                      wall_seconds=nmeasure["wall_seconds"], peak_rss_kib=nmeasure["gnu_time_max_rss_kib"],
                      cgroup_v1_memory_peak_bytes=nmeasure["cgroup_v1_memory_max_usage_bytes"],
                      loadavg_1m_mean=nmeasure["loadavg_1m_mean"], producer_gates=producer_gates,
                      stage_seconds=native_stage_times(receipts)),
        shared_gates=ngates, shared_gate_seconds={k: v["wall_seconds"] for k, v in sn["resources"].items()},
        shared_gate_peak_rss_kib=max((v.get("peak_rss_kib") or 0) for v in sn["resources"].values()),
        completion_gate_seconds=completion, completion_gates=completion_gates,
        producer_identical_checks=covered,
        foundry_check_schedule=schedule,
        end_to_end_seconds=round(prep["wall_seconds"] + nmeasure["wall_seconds"] + completion, 2),
        end_to_end_seconds_serial_checks=round(prep["wall_seconds"] + nmeasure["wall_seconds"] + completion
                                               + schedule["serial_sum_seconds"] - schedule["span_seconds"], 2),
        end_to_end_scope="sv2v preparation + producer (synthesis through extraction, OpenRCX and RustSTA/OpenSTA "
                         "signoff timing, and the foundry main/antenna DRC and LVS when it reaches them, on the "
                         "producer's own schedule: see foundry_check_schedule) + shared gates the producer did "
                         "not run as the identical check (see completion_gates: of foundry main and antenna DRC, "
                         "latch-up, LVS, vendor simulation, synthesis/final equivalence, density); "
                         "end_to_end_seconds_serial_checks charges the producer's covered checks their serial "
                         "sum instead of their elapsed span",
        qor=dict(standard_cell_area_um2=ngates["density"]["standard_cell_area_um2"],
                 final_nonfiller_utilization_percent=ngates["density"]["final_nonfiller_utilization_percent"],
                 macro_inclusive_core_utilization_percent=ngates["density"]["macro_inclusive_core_utilization_percent"],
                 routed_signal_wirelength_um=nqor_def["routed_signal_wirelength_um"], vias=nqor_def["vias"],
                 routed_nets=nqor_def["routed_nets"], components=nqor_def["components"],
                 timing_openrcx=ngates["timing"]["corners"]),
        artifacts={k: dict(sha256=sha(p), bytes=Path(p).stat().st_size) for k, p in
                   dict(gds=o / "backend/ibex_sram_bench.gds", def_=o / "logs/be_def.stdout",
                        netlist_synth=o / "frontend/mapped.v", netlist_final=o / "logs/be_netlist.stdout",
                        spef_native_nom=o / "backend/native.nom.spef").items()},
    )
    bring = maybe(w / "bringup-native-record.json")
    if bring:
        # An earlier complete native run (bring-up, under shared host load) recorded its artifact hashes.
        pairs = dict(gds=o / "backend/ibex_sram_bench.gds", def_=o / "logs/be_def.stdout",
                     netlist_synth=o / "frontend/mapped.v", netlist_final=o / "logs/be_netlist.stdout",
                     netlist_powered=o / "logs/be_powered.stdout", spef_native=o / "backend/native.nom.spef",
                     spef_openrcx=o / "backend/openrcx/nom.spef")
        native["determinism"] = dict(
            reference_run=bring["run"],
            identical={k: sha(p) == bring["artifacts"][k]["sha256"] for k, p in pairs.items() if k in bring["artifacts"]},
            note="artifact identity across two native producer runs (reference_run names the second); the OpenRCX SPEF is written by OpenROAD")
        keep("native/bringup-artifacts.json", data=dict(run=bring["run"], artifacts=bring["artifacts"]))
    keep("native/summary.json", source=o / "summary.json")
    keep("native/step-receipts.json", source=o / "step-receipts.json")
    keep("native/producer-resources.json", source=w / names["native"] / "measure/resources.json")
    if parity:
        keep("native/timing-parity.json", source=o / "backend/timing/parity.json")
    keep("native/shared-gates.json", data=dict(gates=ngates, resources=sn["resources"], measure=snm))
    keep("native/def-metrics.json", data=nqor_def)

    # ---------------- LibreLane ----------------
    run = w / names["librelane"]
    lmeasure = read(w / names["librelane_measure"] / "resources.json")
    steps = librelane_steps(run)
    sl = read(w / names["signoff_librelane"] / "summary.json")
    slm = read(w / f"{names['signoff_librelane']}.measure/resources.json")
    lgates = shared_gates(sl)
    layout = read(w / "layout-librelane.json")
    lqor_def = run_json([sys.executable, str(HERE / "def_metrics.py"), layout["def"]])
    lsum_seconds = round(sum(v["wall_seconds"] for v in sl["resources"].values()), 2)
    librelane = dict(
        configuration="LibreLane 3.0.17 Classic flow through Checker.XOR, prepared contract config, DRT_THREADS=4, "
                      "RUN_POST_GRT_DESIGN_REPAIR=true (LibreLane's own post-GRT repair instead of the historical "
                      "RustEDA-authored DRV ECO loop)",
        flow=dict(exit_code=lmeasure["exit_code"], wall_seconds=lmeasure["wall_seconds"],
                  peak_rss_kib=lmeasure["gnu_time_max_rss_kib"],
                  cgroup_v1_memory_peak_bytes=lmeasure["cgroup_v1_memory_max_usage_bytes"],
                  loadavg_1m_mean=lmeasure["loadavg_1m_mean"], steps=len(steps),
                  stage_seconds=librelane_stage_times(steps)),
        shared_gates=lgates, shared_gate_seconds={k: v["wall_seconds"] for k, v in sl["resources"].items()},
        shared_gate_peak_rss_kib=max((v.get("peak_rss_kib") or 0) for v in sl["resources"].values()),
        equivalence_checker_note=checker_reason(w / names["signoff_librelane"] / "logic/synthesis/checker-result.json"),
        end_to_end_seconds=round(prep["wall_seconds"] + lmeasure["wall_seconds"] + lsum_seconds, 2),
        end_to_end_seconds_native_check_schedule=round(
            prep["wall_seconds"] + lmeasure["wall_seconds"] + shared_gate_seconds_on_schedule(sl["resources"], schedule), 2),
        end_to_end_scope="sv2v preparation + LibreLane flow through Checker.XOR + every shared gate (foundry main and "
                         "antenna DRC, OpenRCX, RustSTA/OpenSTA timing, latch-up, LVS, vendor simulation, "
                         "equivalence, density), one gate at a time; end_to_end_seconds_native_check_schedule "
                         "charges the shared gates the native producer covered under the producer's schedule "
                         "(RustEDA foundry_check_schedule: lanes side by side) instead of their serial sum",
        qor=dict(standard_cell_area_um2=lgates["density"]["standard_cell_area_um2"],
                 final_nonfiller_utilization_percent=lgates["density"]["final_nonfiller_utilization_percent"],
                 macro_inclusive_core_utilization_percent=lgates["density"]["macro_inclusive_core_utilization_percent"],
                 routed_signal_wirelength_um=lqor_def["routed_signal_wirelength_um"], vias=lqor_def["vias"],
                 routed_nets=lqor_def["routed_nets"], components=lqor_def["components"],
                 timing_openrcx=lgates["timing"]["corners"]),
        artifacts={k: dict(sha256=sha(layout[v]), bytes=Path(layout[v]).stat().st_size)
                   for k, v in dict(gds="gds", def_="def", netlist_synth="synthesis_netlist",
                                    netlist_final="netlist").items()},
    )
    keep("librelane/step-runtimes.json", data=steps)
    keep("librelane/flow-resources.json", source=w / names["librelane_measure"] / "resources.json")
    keep("librelane/shared-gates.json", data=dict(gates=lgates, resources=sl["resources"], measure=slm))
    keep("librelane/def-metrics.json", data=lqor_def)

    # ---------------- LibreLane prepared-config diagnostic (bring-up) ----------------
    diag = None
    if (w / names["diag_signoff"] / "summary.json").is_file():
        dsum = read(w / names["diag_signoff"] / "summary.json")
        dgates = shared_gates(dsum)
        dsteps = librelane_steps(w / names["diag_librelane"])
        dlayout = read(w / "layout-librelane-bringup.json")
        ddef = run_json([sys.executable, str(HERE / "def_metrics.py"), dlayout["def"]])
        diag = dict(configuration="LibreLane 3.0.17 Classic, prepared contract config unchanged "
                                  "(RUN_POST_GRT_DESIGN_REPAIR=false), DRT_THREADS=4; bring-up run under shared host load, "
                                  "so its times are not benchmark measurements",
                    gates=dgates, detailed_routing_seconds_under_load=next(
                        (s["seconds"] for s in dsteps if s["step"].endswith("detailedrouting")), None),
                    qor=dict(standard_cell_area_um2=dgates["density"]["standard_cell_area_um2"],
                             routed_signal_wirelength_um=ddef["routed_signal_wirelength_um"], vias=ddef["vias"],
                             timing_openrcx=dgates["timing"]["corners"]))
        keep("librelane-prepared-config-diagnostic.json", data=diag)

    # ---------------- Module head-to-heads ----------------
    modules = {}
    synth_rows = synth_bench(w / names["synth"] / "runs.tsv")
    lib = Path(host["ez130_root"]) / "lib"
    sram = Path(host["ihp_root"]) / "libs.ref/sg13g2_sram/lib"
    qor = run_json([sys.executable, str(HERE / "netlist_qor.py"),
                    "--netlist", f"rustsys={w / names['synth'] / 'rustsys_flow-1/mapped.v'}",
                    "--netlist", f"yosys_librelane={w / names['synth'] / 'yosys_librelane-1/mapped.v'}",
                    "--yosys", "/opt/eda/yosys/bin/yosys", "--sta", "/opt/eda/openroad/bin/sta",
                    "--lib-tt", str(lib / "ez130_8t_tt_1p20v_25c.lib"),
                    "--lib-tt", str(sram / "RM_IHPSG13_1P_1024x32_c2_bm_bist_typ_1p20V_25C.lib"),
                    "--lib-ss", str(lib / "ez130_8t_ss_1p08v_125c.lib"),
                    "--lib-ss", str(sram / "RM_IHPSG13_1P_1024x32_c2_bm_bist_slow_1p08V_125C.lib"),
                    "--sdc", str(w / "prep/constraints.sdc")])
    for v in qor.values():
        v["yosys_stat"].pop("by_type", None)
    modules["synthesis"] = dict(
        task="prepared Ibex RTL (sv2v) to EZ130 TT + SRAM Liberty mapped netlist",
        runs=synth_rows, qor=qor,
        equivalence=dict(rustsys=native and ngates["synthesis_equivalence"]["status"],
                         yosys_librelane=lgates["synthesis_equivalence"]["status"]))
    keep("modules/synthesis.json", data=modules["synthesis"])
    mb = read(w / names["modules"] / "module-bench.json")
    for key in ("extraction", "timing", "gds"):
        modules[key] = mb.get(key)
    pool_note = ("RustROAD sizes its thread pool from --threads (default 2; RAYON_NUM_THREADS does not size it); "
                 "module_bench.py sets --threads to each listed value on the recorded extract command, so the _t2 "
                 f"and _t4 rows ran 2 and 4 threads (the flow's be_extract: {thread_value_text(threads['be_extract'])}). "
                 "OpenRCX used set_thread_count 2 and 4.")
    if modules.get("extraction"):
        modules["extraction"]["thread_note"] = pool_note
    if modules.get("gds"):
        modules["gds"]["thread_note"] = (f"RustROAD export-gds replays the recorded be_gds command ("
                                         f"{thread_value_text(threads['be_gds'])}); "
                                         "KLayout's LEF/DEF reader is single-process.")
    if parity:
        modules["timing"]["agreement"] = producer_gates["timing_accuracy"]
    keep("modules/replay.json", data=mb)
    spice_json = w / names["spice"] / "suite.json"
    if spice_json.is_file():
        modules["spice"] = dict(decks=maybe(w / names["spice"] / "decks/index.json"), suite=spice_suite(spice_json))
        keep("modules/spice-suite.json", source=spice_json) if spice_json.stat().st_size <= 2 << 20 else None
        if (w / names["spice"] / "suite.md").is_file():
            keep("modules/spice-suite.md", source=w / names["spice"] / "suite.md")
    modules["placement_cts_routing"] = dict(
        rustroad=native["producer"]["stage_seconds"], openroad=librelane["flow"]["stage_seconds"],
        qor=dict(rustroad=native["qor"], openroad=librelane["qor"]))

    # ---------------- Ratios under the publication rule ----------------
    n_ok = passed({k: v for k, v in ngates.items() if k in GATES})
    l_ok = passed({k: v for k, v in lgates.items() if k in GATES})
    parity_ok = bool(parity) and parity.get("status") == "passed"
    complete = all(n_ok.values()) and parity_ok and all(l_ok.values()) and lmeasure["exit_code"] == 0
    ratios = dict(
        eligibility=("both flows completed every gate" if complete else
                     "not eligible: a flow failed or left a gate unverified; see gates"),
        # Like for like: both flows' foundry checks on the native producer's schedule, and both serial.
        end_to_end_convention=("end_to_end_speedup charges LibreLane's shared main/antenna DRC and LVS on the native "
                               "producer's check schedule (foundry_check_schedule); end_to_end_speedup_serial_checks "
                               "charges both flows those checks one after another"),
        end_to_end_speedup=(round(librelane["end_to_end_seconds_native_check_schedule"] / native["end_to_end_seconds"], 3)
                            if complete else None),
        end_to_end_speedup_serial_checks=(round(librelane["end_to_end_seconds"]
                                                / native["end_to_end_seconds_serial_checks"], 3) if complete else None),
        standard_cell_area_ratio=(round(native["qor"]["standard_cell_area_um2"] / librelane["qor"]["standard_cell_area_um2"], 4)
                                  if complete else None),
        wirelength_ratio=(round(native["qor"]["routed_signal_wirelength_um"] / librelane["qor"]["routed_signal_wirelength_um"], 4)
                          if complete else None))

    tools = dict(
        rustsys=tool(host["tools"]["rustsys"], [host["tools"]["rustsys"], "--version"]),
        ruststa=tool(host["tools"]["ruststa"], [host["tools"]["ruststa"], "--version"]),
        rustroad=tool(host["tools"]["rustroad"], [host["tools"]["rustroad"], "--version"]),
        rusteda_workspace_cli=tool(host["tools"]["rusteda"], [host["tools"]["rusteda"], "--version"]),
        rustspice=tool(ROOT / ".work/target/RustSpice/release/rustspice", [str(ROOT / ".work/target/RustSpice/release/rustspice"), "--version"]),
        yosys=tool("/opt/eda/yosys/bin/yosys", ["/opt/eda/yosys/bin/yosys", "-V"]),
        openroad=tool("/opt/eda/openroad/bin/openroad", ["/opt/eda/openroad/bin/openroad", "-version"]),
        opensta=tool("/opt/eda/openroad/bin/sta", None) | dict(version="OpenSTA 3.1.0 (OpenROAD 26Q3 build)"),
        klayout=tool("/opt/eda/klayout/bin/klayout", ["/opt/eda/klayout/bin/klayout", "-v"]),
        magic=tool("/opt/eda/magic-vlsi/bin/magic", None) | dict(version="8.3.689"),
        netgen=tool("/opt/eda/netgen-vlsi/bin/netgen", None) | dict(version="1.5.325"),
        iverilog=tool("/opt/eda/iverilog/bin/iverilog", None) | dict(version="13.0"),
        ngspice=tool("/opt/eda/ngspice/bin/ngspice", None) | dict(version="ngspice-45"),
        sv2v=tool("/opt/eda/sv2v/bin/sv2v", ["/opt/eda/sv2v/bin/sv2v", "--version"]),
        librelane=dict(version="3.0.17", source="PyPI wheel; OpenROAD/Yosys/KLayout/Magic/Netgen from nixpkgs"))
    # Host facts and engine build commits are captured once on the campaign host (host-facts.json);
    # sampling the machine that merely regenerates the receipt would misreport the measurement host.
    facts = read(w / "host-facts.json")
    engines = facts["engine_commits"]
    keep("host-facts.json", source=w / "host-facts.json")
    contract = read(ROOT / "integration/ibex_sg13g2_ez130/contract.json")
    ez = Path(host["ez130_root"])
    ez_files = {k: dict(contract_sha256=v, host_sha256=sha(ez / k.split("/", 1)[1]),
                        byte_identical=sha(ez / k.split("/", 1)[1]) == v)
                for k, v in contract["files"].items() if k.startswith("ez130/")}
    receipt = dict(
        schema="rusteda.ibex-host-benchmark.v1", date=args.date,
        status="completed" if complete else "comparison_incomplete",
        summary=("Ibex SG13G2/EZ130 RTL-to-GDS on a 4-vCPU cloud host: RustEDA native flow against LibreLane 3.0.17 "
                 "with the same prepared inputs and the same independent gate scripts, plus module head-to-heads."),
        design=dict(family="Ibex", top="ibex_sram_bench", rtl_source="lowRISC Ibex v2.1.0, default RV32IMC",
                    rtl_commit="38c070939183cc10940b66c8e9e04eeca6b65470", rtl_sha256=sha(w / "prep/rtl/design.v"),
                    rtl_note="sv2v 0.0.13.1 output; the 2026-10-30 campaign's file was 8a8fe84b… (different sv2v build)",
                    testbench_sha256=sha(w / "prep/tb.sv"), sram_instances_required=2,
                    sram_instances=["u_imem", "u_dmem"], sram_macro="RM_IHPSG13_1P_1024x32_c2_bm_bist"),
        technology=dict(process="IHP SG13G2", pdk_commit="5cccb161f7492697cfa52eb14dc03beb00bdca9e",
                        standard_cell_library="EZ130 8T v1.1 (tech LEF and GDS reconstructed; see ez130_files)",
                        ez130_files=ez_files,
                        ez130_summary=(f"{sum(v['byte_identical'] for v in ez_files.values())} of {len(ez_files)} "
                                       "contract-pinned EZ130 files byte-identical; SG13G2 tech LEF and GDS reconstructed; "
                                       "the catalog-pinned FF Liberty is also byte-identical"),
                        corners=["tt_1p20v_25c", "ss_1p08v_125c"], rc_corner="nominal",
                        clock_period_ns=100, contract_sha256_repository=sha(ROOT / "integration/ibex_sg13g2_ez130/contract.json")),
        host=facts["host"] | dict(facts_source=facts["source"]),
        preparation=dict(seconds=prep["wall_seconds"], peak_rss_kib=prep["peak_rss_kib"], tool="sv2v 0.0.13.1"),
        tools=tools, engine_commits=engines,
        implementations=dict(RustEDA=native, LibreLane=librelane),
        diagnostics=dict(librelane_prepared_config=diag),
        gate_pass=dict(RustEDA=dict(n_ok, timing_accuracy=parity_ok), LibreLane=l_ok),
        modules=modules, ratios=ratios,
        deviations=[
            "EZ130 SG13G2 tech LEF and cell GDS reconstructed from public sources (not byte-identical to the pinned archive)",
            "nixpkgs tool builds (OpenROAD 26Q3, Yosys 0.70) instead of LibreLane's pinned OpenROAD dcf36133 / Yosys 0.62",
            "native recipe composed without component-qualification evidence: eight derived qualification gates not produced",
            "native recipe is the repository recipe of the campaign commit; the R20/R21 recipe inputs are not published",
            "host-local helper patches: thread allow-lists (4), cgroup-v1 ownership, RC-profile rules path, density DEF parser",
            "LibreLane without the historical RustEDA-authored repair loop; its own post-GRT design repair enabled",
            f"{facts['host']['logical_cpus']} vCPUs ({facts['host']['cpu']}) instead of 16 logical CPUs of an i9-13900HX",
        ],
        publication_rule=("Only complete independently checked runs with frozen matching inputs may populate "
                          "end-to-end comparative ratios. Module head-to-heads report each contender's measured time "
                          "on the identical task. Failed runs are preserved and reported; missing values stay null."),
        evidence_inventory=inventory)
    receipt_path.write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps(dict(receipt=str(receipt_path), size=receipt_path.stat().st_size, evidence=len(inventory),
                          status=receipt["status"], ratios=ratios), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

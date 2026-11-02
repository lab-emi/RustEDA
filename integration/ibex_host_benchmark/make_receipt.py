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
GATES = ("drc", "antenna", "lvs", "timing", "simulation", "synthesis_equivalence",
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
        "frontend STA/ECO rounds (RustSTA, ECO helpers)": lambda i: i.startswith("fe_") and any(
            t in i for t in ("_init", "_import", "_run", "_graph", "_drv", "_endpoints", "_hold", "bundle", "eco_",
                             "estimate", "caps_", "profiles", "context", "physical_config", "validate", "pins")),
        "placement and CTS (RustROAD)": lambda i: i.startswith("fe_") and any(
            t in i for t in ("pdk_import", "_place", "_cts", "_export", "_seed", "_def", "inspect")),
        "routing (RustROAD)": lambda i: i == "be_route",
        "post-route repair": lambda i: i in ("be_postroute", "be_postroute_inputs"),
        "antenna repair and verification": lambda i: i.startswith("be_antenna"),
        "GDS/DEF/netlist export": lambda i: i in ("be_inspect", "be_def", "be_netlist", "be_powered", "be_gds"),
        "native extraction (RustROAD)": lambda i: i == "be_extract",
        "OpenRCX extraction": lambda i: i in ("be_openrcx", "be_rcx_inputs"),
        "signoff timing (RustSTA + OpenSTA)": lambda i: i in ("be_timing", "be_timing_inputs"),
        "foundry DRC, LVS, macro contract": lambda i: i in ("be_main", "be_antenna_table", "be_lvs", "be_lvs_inputs",
                                                            "be_macro_contract"),
    }
    out = {k: 0.0 for k in groups}
    other = 0.0
    for row in receipts:
        sid, wall = row["id"], row.get("wall_seconds") or 0.0
        for key, test in groups.items():
            if test(sid):
                out[key] += wall
                break
        else:
            other += wall
    out["other (renders, profiles, physical inputs)"] = other
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
    producer_ran = set(Path(p).name.replace(".command.json", "") for p in glob.glob(str(o / "logs/*.command.json")))
    # Gates the producer did not reach are completed by the shared scripts; their time is added.
    completion = [g for g in ("drc", "antenna", "lvs", "simulation", "logic", "density")]
    completion_seconds = round(sum(sn["resources"][g]["wall_seconds"] for g in completion if g in sn["resources"]), 2)
    prep = gnu_time(w / "prep/sv2v-time.txt")
    nqor_def = run_json([sys.executable, str(HERE / "def_metrics.py"), str(o / "logs/be_def.stdout")])
    native = dict(
        configuration="published repository recipe (run16-class), composed by compose_native.py; grouped single-process "
                      "antenna session; 4 threads (routing/extraction), 2-thread helper stages as recorded; unqualified",
        producer=dict(status=nsum["status"], error=nsum.get("error"), failed_items=nsum.get("failed_items"),
                      steps_completed=len(receipts), manifest_steps=len(read(w / "compose/be/prefix-manifest.json")["steps"]),
                      wall_seconds=nmeasure["wall_seconds"], peak_rss_kib=nmeasure["gnu_time_max_rss_kib"],
                      cgroup_v1_memory_peak_bytes=nmeasure["cgroup_v1_memory_max_usage_bytes"],
                      loadavg_1m_mean=nmeasure["loadavg_1m_mean"], producer_gates=producer_gates,
                      stage_seconds=native_stage_times(receipts)),
        shared_gates=ngates, shared_gate_seconds={k: v["wall_seconds"] for k, v in sn["resources"].items()},
        shared_gate_peak_rss_kib=max((v.get("peak_rss_kib") or 0) for v in sn["resources"].values()),
        completion_gate_seconds=completion_seconds,
        end_to_end_seconds=round(prep["wall_seconds"] + nmeasure["wall_seconds"] + completion_seconds, 2),
        end_to_end_scope="sv2v preparation + producer (synthesis through extraction, OpenRCX and RustSTA/OpenSTA "
                         "signoff timing) + shared gates the producer did not reach (foundry main and antenna DRC, "
                         "LVS, vendor simulation, synthesis/final equivalence, density)",
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
        end_to_end_scope="sv2v preparation + LibreLane flow through Checker.XOR + every shared gate (foundry main and "
                         "antenna DRC, OpenRCX, RustSTA/OpenSTA timing, LVS, vendor simulation, equivalence, density)",
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
        end_to_end_speedup=(round(librelane["end_to_end_seconds"] / native["end_to_end_seconds"], 3) if complete else None),
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
    engines = {m: subprocess.run(["git", "-C", str(ROOT / "modules" / m), "rev-parse", "HEAD"], capture_output=True,
                                 text=True).stdout.strip() for m in ("RustSys", "RustSTA", "RustROAD", "RustSpice",
                                                                     "RustEDA-Common")}
    contract = read(ROOT / "integration/ibex_sg13g2_ez130/contract.json")
    ez = Path(host["ez130_root"])
    ez_files = {k: dict(contract_sha256=v, host_sha256=sha(ez / k.split("/", 1)[1]),
                        byte_identical=sha(ez / k.split("/", 1)[1]) == v)
                for k, v in contract["files"].items() if k.startswith("ez130/")}
    mem = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text())[1]) * 1024
    cpu = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())[1].strip()
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
                        ez130_files=ez_files, corners=["tt_1p20v_25c", "ss_1p08v_125c"], rc_corner="nominal",
                        clock_period_ns=100, contract_sha256_repository=sha(ROOT / "integration/ibex_sg13g2_ez130/contract.json")),
        host=dict(cpu=cpu, logical_cpus=os.cpu_count(), memory_bytes=mem, kernel=platform.release(),
                  cgroup="v1 memory controller; each timed stage alone in its own child group",
                  allocation="all 4 vCPUs to each flow; RustEDA 8 GiB cap (+2 GiB memsw), LibreLane 10 GiB cap; no swap device",
                  sampling="one timed sample per flow; module stages one warm-up plus three measured repetitions"),
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
            "native recipe is the repository's run16-class recipe; the R20/R21 recipe inputs are not published",
            "host-local helper patches: thread allow-lists (4), cgroup-v1 ownership, RC-profile rules path, density DEF parser",
            "LibreLane without the historical RustEDA-authored repair loop; its own post-GRT design repair enabled",
            "4 vCPUs (Xeon 2.1 GHz) instead of 16 logical CPUs of an i9-13900HX",
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

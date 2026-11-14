# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Apply the shared signoff gates of a gate profile to one final layout.

Called by `integration/ibex_host_benchmark/signoff_layout.py --profile <gates.json>`. The profile
names every deck, rule file and collateral file by a path relative to the PDK root and pins it by
SHA-256; the host map names the PDK root, the prepared benchmark inputs, the macro baselines and
the tools. Nothing else is read from the host. The same call judges any flow's layout:

  {gds, def, netlist, powered_netlist, synthesis_netlist}

Each gate runs as its own process under GNU time. `summary.json` has the IHP driver's schema
(`gates.<name>.{exit_code, report}`, `resources.<name>.{exit_code, wall_seconds, peak_rss_kib}`),
plus the profile identity. A gate with several engines (SKY130 `drc`: Magic and KLayout) reports
each engine under `report.engines` and passes when all pass.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import common  # noqa: E402

MIB = common.MIB
ORDER = ("drc", "antenna", "extraction", "timing", "lvs", "simulation", "logic", "density")
IHP_RECIPE = ROOT / "integration/ibex_sg13g2_ez130"


def load(host_path, profile_path):
    host = common.read_json(host_path, 2 * MIB)
    profile = common.Profile(profile_path, host["pdk_root"], host.get("prepared"))
    tools = {name: common.identity(path) for name, path in host["tools"].items()}
    return host, profile, tools


def klayout_plan(profile, tools, spec, host, mode, top, gds):
    """Plan of one KLayout deck run; `spec` is the engine entry of the profile."""
    deck_files = [profile.file(path) for path in profile.matching(spec.get("includes", []))] if spec.get("includes") else []
    return dict(schema="rusteda.pdk-gate.klayout-drc.v1", mode=mode, top=top, gds=gds, deck=profile.file(spec["deck"]),
                deck_files=deck_files, switches=spec["switches"], top_switch=spec["top_switch"],
                threads_switch=spec["threads_switch"], threads=int(host.get("gate_threads", 2)),
                timeout_seconds=int(spec.get("timeout_seconds", 14400)),
                tools={k: tools[k] for k in ("klayout", "gnu_time")})


def magic_fields(profile, host):
    magic = profile["magic"]
    macros = profile["macros"]
    return dict(magicrc=profile.file(magic["magicrc"]), tech_files=[profile.file(p) for p in magic["tech_files"]],
                pdk_root=str(profile.root), abstract_lefs=[profile.file(spec["lef"]) for spec in macros.values()],
                abstract_cells=sorted(macros))


def baseline_dir(host, gate, engine, master) -> Path:
    return Path(host["macro_baselines"]).resolve() / f"{gate}_{engine}" / master


def floorplan(profile, prepared) -> dict:
    """Die, macro halo and macro placements of the reference configuration both flows share."""
    config = common.read_json(prepared / "config.json", 4 * MIB)
    halo = config["FP_MACRO_HORIZONTAL_HALO"]
    common.require(config["DESIGN_NAME"] == profile["top"] and halo == config["FP_MACRO_VERTICAL_HALO"], "REFERENCE_CONFIGURATION")
    macros = {}
    for master, spec in config["MACROS"].items():
        common.require(master in profile["macros"], f"REFERENCE_MACRO_NOT_IN_PROFILE: {master}")
        common.require(sorted(spec["instances"]) == sorted(profile["macros"][master]["instances"]), "REFERENCE_MACRO_INSTANCES")
        for name, place in spec["instances"].items():
            macros[name] = dict(cell=master, location_um=place["location"], orientation=place["orientation"])
    return dict(die_um=config["DIE_AREA"], macro_halo_um=halo, macros=macros,
                source=common.identity(prepared / "config.json"))


def main(args) -> int:
    host, profile, tools = load(args.host, args.profile)
    layout = common.read_json(args.layout, MIB)
    prepared = Path(host["prepared"]).resolve(strict=True)
    work = args.workspace.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("REVISION_CONFLICT: empty workspace required")
    (work / "plans").mkdir(parents=True)
    (work / "logs").mkdir()
    pins = profile.verify_all()
    top = profile["top"]
    macros = profile["macros"]
    collateral = profile["collateral"]
    python = tools["python"]["path"]
    final = {k: common.identity(v) for k, v in layout.items()}
    files = dict(sdc=common.identity(prepared / "constraints.sdc"), rtl=common.identity(prepared / "rtl/design.v"),
                 macro_bb=common.identity(prepared / "rtl/sram_bb.v"), tb=common.identity(profile.dir / "tb.sv"))
    common.require(len(macros) == 1, "ONE_MACRO_MASTER_PER_PROFILE")  # the corner file keys hold one macro library
    master, macro = next(iter(macros.items()))
    liberty = dict(std_tt=profile.file(collateral["liberty"]["tt"]), std_ss=profile.file(collateral["liberty"]["ss"]),
                   ram_tt=profile.file(macro["liberty"]["tt"]), ram_ss=profile.file(macro["liberty"]["ss"]))
    cell_lefs = [profile.file(p) for p in collateral["cell_lefs"]]
    macro_lefs = [profile.file(spec["lef"]) for spec in macros.values()]
    cells_lef = work / "cells.lef"
    cells_lef.write_text("\n".join(Path(record["path"]).read_text() for record in cell_lefs))
    policy = common.identity(IHP_RECIPE / "signoff_timing.py")
    summary = dict(schema="rusteda.host-shared-signoff.v1", layout=final, gates={}, resources={},
                   profile=dict(name=profile["name"], pdk=profile["pdk"], scl=profile["scl"],
                                file=common.identity(profile.path), pinned_files=len(pins),
                                decisions=profile["decisions"], prepared=str(prepared)))

    def save():
        common.save_json(work / "summary.json", summary)

    def run(name, argv, seconds):
        time_file = work / "logs" / f"{name}.time"
        tick = time.monotonic()
        with (work / "logs" / f"{name}.log").open("w") as log:
            result = subprocess.run([tools["gnu_time"]["path"], "-v", "-o", str(time_file), *map(str, argv)],
                                    stdout=log, stderr=subprocess.STDOUT, timeout=seconds)
        text = time_file.read_text() if time_file.exists() else ""
        rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
        summary["resources"][name] = dict(exit_code=result.returncode, wall_seconds=round(time.monotonic() - tick, 3),
                                          peak_rss_kib=int(rss[1]) if rss else None)
        save()
        return result.returncode

    def helper(name, script, plan, seconds):
        path = work / "plans" / f"{name}.json"
        common.save_json(path, plan)
        code = run(name, [python, HERE / script, "--inputs", path, "--workspace", work / name, "--base-rev", "empty"], seconds)
        report = work / name / "summary.json"
        return code, (common.read_json(report, 256 * MIB) if report.exists() else None)

    def engine_plan(gate, spec):
        engine = spec["engine"]
        if engine == "klayout":
            plan = klayout_plan(profile, tools, spec, host, "layout", top, final["gds"])
            plan["macros"] = {}
            for name, entry in macros.items():
                base = baseline_dir(host, gate, engine, name)
                plan["macros"][name] = dict(gds=profile.file(entry["gds"]), instances=len(entry["instances"]),
                                            baseline=dict(report=common.identity(base / "drc.lyrdb"),
                                                          summary=common.identity(base / "summary.json")))
            return "klayout_drc.py", plan
        common.require(engine == "magic", f"ENGINE: {engine}")
        plan = dict(top=top, gds=final["gds"], timeout_seconds=int(spec.get("timeout_seconds", 14400)),
                    tools={k: tools[k] for k in ("magic", "gnu_time")}, **magic_fields(profile, host))
        if gate == "drc":
            plan.update(schema="rusteda.pdk-gate.magic-drc.v1", style=spec["style"])
            return "magic_drc.py", plan
        plan.update(schema="rusteda.pdk-gate.magic-antenna.v1")
        return "magic_antenna.py", plan

    gates = set((args.gates or ",".join(ORDER)).split(","))
    unknown = gates - set(ORDER)
    if unknown:
        raise SystemExit(f"unknown gates for a profile run: {sorted(unknown)}")
    for gate in ("drc", "antenna"):
        if gate not in gates:
            continue
        engines, codes, names = {}, [], []
        for spec in profile["gates"][gate]["engines"]:
            name = f"{gate}_{spec['engine']}"
            script, plan = engine_plan(gate, spec)
            code, report = helper(name, script, plan, int(spec.get("timeout_seconds", 14400)) + 3900)
            engines[spec["engine"]] = report
            codes.append(code)
            names.append(name)
        report = dict(status="passed" if all(c == 0 for c in codes) else "failed", engines=engines,
                      violations=sum((r or {}).get("violations") or 0 for r in engines.values()),
                      macro_interior=sum((r or {}).get("macro_interior") or 0 for r in engines.values()))
        if gate == "drc":
            rules = profile["gates"]["drc"]["latchup_rules"]
            found = {}
            for entry in engines.values():
                entry = entry or {}
                table = entry["gating_rules"] if "gating_rules" in entry else entry.get("categories") or {}
                for rule, count in table.items():
                    if rule in rules:
                        found[rule] = found.get(rule, 0) + count
            report["latchup"] = dict(rules=rules, violations=found,
                                     note="tap-distance rules are part of the foundry DRC; no separate latch-up gate")
        summary["gates"][gate] = dict(exit_code=max(codes), report=report)
        summary["resources"][gate] = dict(exit_code=max(codes),
                                          wall_seconds=round(sum(summary["resources"][n]["wall_seconds"] for n in names), 3),
                                          peak_rss_kib=max((summary["resources"][n]["peak_rss_kib"] or 0) for n in names))
        save()
    spef = None
    if "extraction" in gates or "timing" in gates:
        plan = dict(schema="rusteda.pdk-gate.openrcx.v1", top=top,
                    files=dict(tech_lef=profile.file(collateral["tech_lef"]), lefs=[*cell_lefs, *macro_lefs],
                               rules_nom=profile.file(profile["gates"]["extraction"]["rules"])),
                    tools={k: tools[k] for k in ("openroad", "gnu_time")},
                    budgets=dict(threads=2, timeout_seconds=900, max_spef_bytes=256 * MIB, max_nets=200000,
                                 max_log_bytes=32 * MIB))
        plan["files"]["def"] = final["def"]
        path = work / "plans" / "extraction.json"
        common.save_json(path, plan)
        code = run("extraction", [python, HERE / "openrcx_extract.py", "--inputs", path, "--workspace", work / "extraction",
                                  "--base-rev", "empty"], 1000)
        result = work / "extraction/result.json"
        summary["gates"]["extraction"] = dict(exit_code=code, report=common.read_json(result) if result.exists() else None)
        if code == 0 and (work / "extraction/nom.spef").exists():
            spef = common.identity(work / "extraction/nom.spef")
        save()
    if "timing" in gates and spef:
        plan = dict(schema="rusteda.pdk-gate.timing.v1", top=top,
                    files=dict(netlist=final["netlist"], sdc=files["sdc"], spef_openrcx=spef,
                               physical_lef=common.identity(cells_lef), **liberty),
                    tools={k: tools[k] for k in ("rusteda", "ruststa", "opensta", "gnu_time", "python")},
                    scripts=dict(reference_timing=common.identity(IHP_RECIPE / "reference_timing.py"), policy=policy),
                    budgets=dict(threads=2, timeout_seconds=180, max_input_bytes=128 * MIB, max_output_bytes=256 * MIB,
                                 max_state_gib=2, max_limit_rows=100000, max_checks=100000),
                    scope=dict(mode="single_reference_openrcx", rc_corner="nom", cppr="off", precision="f64", backend="cpu",
                               clock_model="propagated", wire_model="spef_rc", tolerance_ns=.001),
                    macro_instances=profile.macro_instances(), corners=profile["corners"], decisions=profile["decisions"])
        code, report = helper("timing", "timing.py", plan, 1800)
        summary["gates"]["timing"] = dict(exit_code=code, report=report)
        save()
    if "lvs" in gates:
        spec = profile["gates"]["lvs"]
        plan = dict(schema="rusteda.pdk-gate.netgen-lvs.v1", top=top, gds=final["gds"],
                    powered_netlist=final["powered_netlist"], netgen_setup=profile.file(spec["netgen_setup"]),
                    cell_spice=[profile.file(p) for p in spec["cell_spice"]],
                    macros={name: len(entry["instances"]) for name, entry in macros.items()},
                    netgen_env=spec.get("netgen_env", {}), timeout_seconds=int(spec.get("timeout_seconds", 7200)),
                    tools={k: tools[k] for k in ("magic", "netgen", "gnu_time")}, **magic_fields(profile, host))
        code, report = helper("lvs", "netgen_lvs.py", plan, 2 * int(spec.get("timeout_seconds", 7200)) + 300)
        summary["gates"]["lvs"] = dict(exit_code=code, report=report)
        save()
    if "simulation" in gates:
        spec = profile["gates"]["simulation"]
        plan = dict(schema="rusteda.pdk-gate.simulation.v1", top=top,
                    files=dict(netlist=final["netlist"], tb=files["tb"], rtl=files["rtl"],
                               physical_lef=common.identity(cells_lef), physical_policy=policy, **liberty),
                    vendor=[profile.file(p) for p in spec["vendor"]], model_edits=spec.get("model_edits", []),
                    defines=spec.get("defines", []),
                    include_dirs=[str(profile.locate(p)) for p in spec.get("include_dirs", [])],
                    macros={name: entry["instances"] for name, entry in macros.items()}, pass_line=spec["pass_line"],
                    tools={k: tools[k] for k in ("yosys", "iverilog", "vvp", "gnu_time")})
        code, report = helper("simulation", "simulation.py", plan, 3600)
        summary["gates"]["simulation"] = dict(exit_code=code, report=report)
        save()
    if "logic" in gates:
        spec = profile["gates"]["logic"]
        plan = dict(schema="rusteda.pdk-gate.logic.v1", top=top, rtl=files["rtl"],
                    synthesis_netlist=final["synthesis_netlist"], final_netlist=final["netlist"],
                    library=liberty["std_tt"], macro_bb=files["macro_bb"], macro=master, macro_instances=macro["instances"],
                    tie_cells=spec["tie_cells"],
                    physical=dict(physical_lef=common.identity(cells_lef), policy=policy, **liberty),
                    relation_checker=common.identity(ROOT / "modules/RustSys/verification/benchmarks/open-hardware/large_lec.py"),
                    tools={k: tools[k] for k in ("yosys", "abc", "checker", "gnu_time", "python")}, limits=spec["limits"],
                    expected=spec.get("expected", {}))
        code, report = helper("logic", "logic.py", plan, 7200)
        reports = {k: (work / "logic" / f"{k}.json") for k in ("synthesis_equivalence", "final_equivalence")}
        summary["gates"]["logic"] = dict(exit_code=code, report=dict(
            summary=report, **{k: common.read_json(v, 64 * MIB) if v.exists() else None for k, v in reports.items()}))
        save()
    if "density" in gates:
        import density  # noqa: E402
        tick = time.monotonic()
        try:
            plan = floorplan(profile, prepared)
            result = density.utilization(Path(final["def"]["path"]),
                                         [Path(r["path"]) for r in (*cell_lefs, *macro_lefs)], plan,
                                         int(profile["gates"]["density"]["max_percent"]), top)
            result["reference_configuration"] = plan["source"]
        except (common.GateError, OSError, KeyError, ValueError) as error:
            result = dict(status="failed", error=str(error))
        summary["gates"]["density"] = dict(exit_code=0 if result.get("status") == "passed" else 1, report=result)
        summary["resources"]["density"] = dict(exit_code=0, wall_seconds=round(time.monotonic() - tick, 3), peak_rss_kib=None)
    for relative in pins:
        common.require(common.identity(pins[relative]["path"]) == pins[relative], f"PROFILE_FILE_CHANGED_DURING_RUN: {relative}")
    top_level = [name for name in summary["resources"] if name in ORDER]
    summary["total_wall_seconds"] = round(sum(summary["resources"][name]["wall_seconds"] for name in top_level), 3)
    save()
    print(json.dumps({k: v["exit_code"] for k, v in summary["gates"].items()}))
    return 0

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Apply the Ibex campaign's independent gate scripts to one final layout.

The scripts are the recipe's own (`integration/ibex_sg13g2_ez130`):
`signoff_drc.py` (complete foundry main and antenna tables), `reference_extract.py`
(OpenRCX nominal SPEF), `reference_timing.py` (RustSTA and OpenSTA, TT/SS, on the
OpenRCX SPEF), `signoff_lvs.py` (full-device KLayout LVS with real SRAM devices),
`reference_simulation.py` (vendor-model Icarus simulation), `signoff_logic.py`
(synthesis and final equivalence), `final_density.utilization` (final
non-filler density) and `tap_coverage_runner.py` (latch-up: the independent
LU.a/LU.b tap-coverage certificate; the foundry main deck run by the `drc` gate
checks LU.b only, LU.a is in the maximal deck). The input documents are built exactly as
`reference_flow.py` builds them. Each gate runs under GNU time; the driver
writes `summary.json` with every gate verdict, wall time and peak RSS.

Run inside a producer-owned memory cgroup (see `run_signoff.sh`): the LVS and
logic helpers require the RustEDA worker-ownership protocol.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
TOP = "ibex_sram_bench"
MACRO = "RM_IHPSG13_1P_1024x32_c2_bm_bist"
MIB = 1 << 20
# tap_coverage.py ceilings, as in the joint tap proof (native_flow.TAP_LIMITS).
TAP_LIMITS = dict(cells=16384, hierarchy_edges=250000, instance_visits=4000000,
                  input_shapes=2000000, vertices=8000000, shape_visits=8000000,
                  vertex_visits=32000000, domains=4096, anchors=250000, bin_entries=1000000,
                  candidates=4096, boxes=16000000, distance_checks=200000000, pending=64, depth=32)


def identity(path) -> dict:
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest, "bytes": path.stat().st_size}


def read(path):
    return json.loads(Path(path).read_text())


def latchup_plan(gds: dict, workspace: Path) -> dict:
    """The latch-up gate's `tap_coverage_runner.py` config for one final GDS (identity record).

    Every diffusion point must be within 20 um of a contacted tie of its well (LU.a: n-well to
    n-tap, LU.b: substrate to p-tap). The conservative box certificate fails on any box it cannot
    prove; the SRAM macro subtrees are excluded as targets, as in the joint proof."""
    return dict(schema="rusteda.tap-coverage.v1", gds=gds, workspace=str(workspace), base_revision="empty",
                top=TOP, excluded_cells={MACRO: 2}, limits=TAP_LIMITS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True,
                        help="JSON: {gds, def, netlist, powered_netlist, synthesis_netlist}")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--gates", default="drc,antenna,latchup,extraction,timing,lvs,simulation,logic,density")
    args = parser.parse_args()
    host = read(args.host)
    layout = read(args.layout)
    recipe = Path(host["recipe_dir"]).resolve()
    prep = Path(host["prepared"]).resolve()
    ez = Path(host["ez130_root"]).resolve()
    ihp = Path(host["ihp_root"]).resolve()
    sram = ihp / "libs.ref/sg13g2_sram"
    tools = {k: identity(v) for k, v in host["tools"].items()}
    signoff, sram_lvs, ez_lvs = (Path(host[k]).resolve() for k in ("signoff_deck", "sram_lvs", "ez_lvs"))
    work = args.workspace.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("REVISION_CONFLICT: empty workspace required")
    (work / "plans").mkdir(parents=True)
    (work / "logs").mkdir()
    files = dict(
        sdc=identity(prep / "constraints.sdc"), tb=identity(prep / "tb.sv"), rtl=identity(prep / "rtl/design.v"),
        macro_bb=identity(prep / "rtl/sram_bb.v"),
        std_tt=identity(ez / "lib/ez130_8t_tt_1p20v_25c.lib"), std_ss=identity(ez / "lib/ez130_8t_ss_1p08v_125c.lib"),
        ram_tt=identity(sram / f"lib/{MACRO}_typ_1p20V_25C.lib"), ram_ss=identity(sram / f"lib/{MACRO}_slow_1p08V_125C.lib"),
        std_lef=identity(ez / "lef/ez130_8t.lef"), tech_lef=identity(ez / "lef/ez130_sg13g2_tech.lef"),
        ram_lef=identity(sram / f"lef/{MACRO}.lef"),
        rules_nom=identity(ihp / "libs.tech/librelane/openrcx/IHP_rcx_patterns.rules"),
        vendor_0=identity(ez / "verilog/ez130_8t.v"), vendor_1=identity(sram / f"verilog/{MACRO}.v"),
        vendor_2=identity(sram / "verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v"),
        physical_policy=identity(recipe / "signoff_timing.py"),
    )
    final = {k: identity(v) for k, v in layout.items()}
    summary = dict(schema="rusteda.host-shared-signoff.v1", layout=final, gates={}, resources={})

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
        (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        return result.returncode

    def helper(name, script, data, seconds):
        plan = work / "plans" / f"{name}.json"
        plan.write_text(json.dumps(data, indent=2) + "\n")
        return run(name, [tools["python"]["path"], recipe / script, "--inputs", plan,
                          "--workspace", work / name, "--base-rev", "empty"], seconds)

    gates = set(args.gates.split(","))
    for name, table in (("drc", "main"), ("antenna", "antenna")):
        if name in gates:
            code = run(name, [tools["python"]["path"], recipe / "signoff_drc.py", "--gds", final["gds"]["path"],
                              "--profile", signoff / "provenance.json", "--tool", tools["appimage"]["path"],
                              "--table", table, "--workspace", work / name, "--base-rev", "empty"], 7200)
            report = work / name / "summary.json"
            summary["gates"][name] = dict(exit_code=code, report=read(report) if report.exists() else None)
    if "latchup" in gates:
        plan = work / "plans" / "latchup.json"
        plan.write_text(json.dumps(latchup_plan(final["gds"], work / "latchup"), indent=2) + "\n")
        appimage = tools["appimage"]
        code = run("latchup", [tools["python"]["path"], recipe / "tap_coverage_runner.py", "--config", plan,
                               "--workspace", work / "latchup", "--base-rev", "empty", "--tool", appimage["path"],
                               "--tool-sha256", appimage["sha256"], "--tool-bytes", appimage["bytes"]], 900)
        reports = {k: work / "latchup" / f"{k}.json" for k in ("controller", "summary")}
        summary["gates"]["latchup"] = dict(exit_code=code, report={k: read(v) if v.exists() else None
                                                                    for k, v in reports.items()})
    spef = None
    if "extraction" in gates or "timing" in gates:
        data = dict(schema="rusteda.reference-extraction-inputs.v1", top=TOP,
                    files=dict(def_=final["def"], tech_lef=files["tech_lef"], cell_lef=files["std_lef"],
                               ram_lef=files["ram_lef"], rules_nom=files["rules_nom"]),
                    tools={k: tools[k] for k in ("appimage", "gnu_time")},
                    budgets=dict(threads=2, timeout_seconds=900, max_spef_bytes=128 * MIB, max_nets=100000,
                                 max_log_bytes=32 * MIB))  # exact recorded budget
        data["files"]["def"] = data["files"].pop("def_")
        code = helper("extraction", "reference_extract.py", data, 1000)
        result = work / "extraction/result.json"
        summary["gates"]["extraction"] = dict(exit_code=code, report=read(result) if result.exists() else None)
        if (work / "extraction/nom.spef").exists():
            spef = identity(work / "extraction/nom.spef")
    if "timing" in gates and spef:
        tf = {k: files[k] for k in ("sdc", "std_tt", "ram_tt", "std_ss", "ram_ss")}
        tf.update(netlist=final["netlist"], spef_openrcx=spef, physical_lef=files["std_lef"])
        data = dict(schema="rusteda.reference-timing-inputs.v1", top=TOP, files=tf,
                    tools={k: tools[k] for k in ("rusteda", "ruststa", "opensta", "gnu_time")},
                    policy=files["physical_policy"],
                    budgets=dict(threads=2, timeout_seconds=180, max_input_bytes=128 * MIB, max_output_bytes=256 * MIB,
                                 max_state_gib=2, max_limit_rows=100000, max_checks=100000),
                    scope=dict(mode="single_reference_openrcx", rc_corner="nom", cppr="off", precision="f64",
                               backend="cpu", clock_model="propagated", wire_model="spef_rc", tolerance_ns=.001))
        code = helper("timing", "reference_timing.py", data, 1800)
        result = work / "timing/reference-timing.json"
        summary["gates"]["timing"] = dict(exit_code=code, report=read(result) if result.exists() else None)
    if "lvs" in gates:
        data = dict(schema="rusteda.device-lvs-inputs.v1", top=TOP, gds=final["gds"]["path"],
                    powered_netlist=final["powered_netlist"]["path"], deck=str(sram_lvs / "sg13g2.lvs"),
                    cdl=[str(ez / "cdl/ez130_8t.cdl"), str(sram_lvs / "sram.cdl"), str(ez_lvs / "spacers.cdl")],
                    interfaces=str(recipe / "lvs_interfaces.json"), macro=MACRO, macro_count=2,
                    macro_qualification=str(Path(host["sram_qualification"]).resolve()),
                    tools={"appimage": tools["appimage"]["path"], "yosys": tools["yosys"]["path"],
                           "time": tools["gnu_time"]["path"]},
                    limits=dict(memory_mib=4096, swap_mib=512, deadline_seconds=3600, threads=2))
        code = helper("lvs", "signoff_lvs.py", data, 3700)
        result = work / "lvs/summary.json"
        summary["gates"]["lvs"] = dict(exit_code=code, report=read(result) if result.exists() else None)
    if "simulation" in gates:
        sf = {k: files[k] for k in ("rtl", "tb", "std_tt", "std_ss", "ram_tt", "ram_ss",
                                     "vendor_0", "vendor_1", "vendor_2", "physical_policy")}
        sf.update(netlist=final["netlist"], physical_lef=files["std_lef"])
        data = dict(schema="rusteda.reference-simulation-inputs.v1", files=sf,
                    tools={k: tools[k] for k in ("yosys", "iverilog", "vvp", "gnu_time")})
        code = helper("simulation", "reference_simulation.py", data, 1200)
        result = work / "simulation/simulation.json"
        summary["gates"]["simulation"] = dict(exit_code=code, report=read(result) if result.exists() else None)
    if "logic" in gates:
        data = dict(schema="rusteda.logic-signoff-inputs.v1", top=TOP, rtl=files["rtl"]["path"],
                    synthesis_netlist=final["synthesis_netlist"]["path"], final_netlist=final["netlist"]["path"],
                    library=files["std_tt"]["path"], macro_bb=files["macro_bb"]["path"], macro=MACRO,
                    macro_instances=["u_imem", "u_dmem"], tb=files["tb"]["path"],
                    vendor_verilog=[files[f"vendor_{i}"]["path"] for i in range(3)],
                    vendor_include_dirs=[str(Path(files["vendor_1"]["path"]).parent)],
                    relation_checker=str(ROOT / "modules/RustSys/verification/benchmarks/open-hardware/large_lec.py"),
                    tools={k: tools[v]["path"] for k, v in dict(yosys="yosys", abc="abc", checker="checker",
                                                                 iverilog="iverilog", vvp="vvp", time="gnu_time").items()},
                    physical_lef=files["std_lef"]["path"],
                    physical_libraries={k: files[k]["path"] for k in ("std_tt", "ram_tt", "std_ss", "ram_ss")},
                    limits=dict(memory_mib=4096, swap_mib=512, deadline_seconds=1800, threads=2,
                                max_conflicts=2000000, discovery_work=16000000000, discovery_rounds=32),
                    expected_hashes=dict(rtl=files["rtl"]["sha256"], library=files["std_tt"]["sha256"],
                                         macro_bb=files["macro_bb"]["sha256"], tb=files["tb"]["sha256"],
                                         synthesis_netlist=final["synthesis_netlist"]["sha256"],
                                         final_netlist=final["netlist"]["sha256"]))
        code = helper("logic", "signoff_logic.py", data, 2000)
        reports = {k: (work / "logic" / f"{k}.json") for k in ("synthesis_equivalence", "final_equivalence", "summary")}
        summary["gates"]["logic"] = dict(exit_code=code, report={k: read(v) if v.exists() else None
                                                                  for k, v in reports.items()})
    if "density" in gates:
        sys.path.insert(0, str(recipe))
        import final_density  # noqa: E402  (the recipe's own checker)
        combined = work / "density-cells.lef"
        combined.write_text((ez / "lef/ez130_8t.lef").read_text() + "\n" + (sram / f"lef/{MACRO}.lef").read_text())
        tick = time.monotonic()
        result = final_density.utilization(Path(final["def"]["path"]), combined,
                                           read(recipe / "native-physical.json"),
                                           read(prep / "contract.json")["common"], 70)
        summary["gates"]["density"] = dict(exit_code=0 if result.get("status") == "passed" else 1, report=result)
        summary["resources"]["density"] = dict(exit_code=0, wall_seconds=round(time.monotonic() - tick, 3),
                                               peak_rss_kib=None)
    summary["total_wall_seconds"] = round(sum(r["wall_seconds"] for r in summary["resources"].values()), 3)
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v["exit_code"] for k, v in summary["gates"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

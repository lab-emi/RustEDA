#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Compose the native Ibex SG13G2/EZ130 recipe for a host without the original
campaign's local artifacts.

This driver writes the three composer inputs that the original campaign kept
only in its local `.work` directory (`rusteda.native-frontend-inputs.v1`,
`rusteda.native-backend-inputs.v1`) and calls the recipe's own composers:
`native_frontend.py build` and `native_backend.py`. Every file and tool is
recorded by absolute path, SHA-256 and size; the composers re-verify them.

The full sixteen-gate composer (`native_flow.py`) also requires the
component-qualification evidence of the original host
(`rusteda.ihp-component-qualification.v1`). No generator for it is published
and it must not be fabricated, so this driver stops at the backend prefix
manifest (`be_macro_contract`). Logic equivalence and vendor simulation are
run separately by `run_native.py` with the recipe's `signoff_logic.py`.

Usage (all paths absolute or relative to the repository root):

    python3 integration/ibex_host_benchmark/compose_native.py \
        --host host.json --workspace .work/ibex-host/compose --base-rev empty
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

# The recorded R20 frontend selection (campaigns/early-20261101/schedule.json, step fe_context).
SELECTION = {
    "top": "ibex_sram_bench", "primary_clock": "clk_i", "clock_sink_pin": "CK",
    "gating_cells": ["AND2X3"],
    "buffers": [{"cell": f"BUFX{n}", "input": "A", "output": "Y"}
                for n in (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 44, 48, 64)],
    "primary_buffer": "BUFX24",
    "reset": {"pin": "RB", "leaf": "BUFX8", "trunk": "BUFX16"},
    "delay_cells": ["DLY1X2"], "target_fanout": 9, "cap_fraction": 0.8,
    "hold_margin_ns": 0.075, "setup_margin_ns": 0.05,
    "port_diode": {"cell": "ANTENNA2", "pin": "A"},
    "electrical": {
        "driver_cells": ["BUFX3", "BUFX4", "BUFX6", "BUFX8", "BUFX12", "BUFX16"],
        "sram_buffer": "BUFX4", "sram_clock_buffer": "BUFX8",
        "sram_masters": ["RM_IHPSG13_1P_1024x32_c2_bm_bist"],
        "load_fraction": 0.6, "max_wire_um": 1200, "antenna_cells": ["ANTENNA2"],
        "drv_margin": 0.15, "hold_drv_margin": 0.1,
        "clock_balance": {"cell": "DLY1X2", "max_stages": 32, "margin_ns": 0.1, "wire_pf": 0.002},
    },
    "launch_chain_max": 32,
}


def identity(path: Path) -> dict:
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest, "bytes": path.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", type=Path, required=True, help="host path map (see host.example.json)")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    if args.base_rev != "empty" or (args.workspace.exists() and any(args.workspace.iterdir())):
        raise SystemExit("REVISION_CONFLICT: composition requires --base-rev empty and an empty workspace")
    host = json.loads(args.host.read_text())
    recipe = Path(host["recipe_dir"]).resolve()          # integration/ibex_sg13g2_ez130 (or a patched host copy)
    prep = Path(host["prepared"]).resolve()               # prepare.py workspace
    ez = Path(host["ez130_root"]).resolve()
    ihp = Path(host["ihp_root"]).resolve()                # .../IHP-Open-PDK/ihp-sg13g2
    road = ROOT / "modules/RustROAD/scripts"
    sram = ihp / "libs.ref/sg13g2_sram"
    macro = "RM_IHPSG13_1P_1024x32_c2_bm_bist"
    work = args.workspace.resolve()
    work.mkdir(parents=True, exist_ok=True)

    fe_files = {
        "rtl": prep / "rtl/design.v", "sdc": prep / "constraints.sdc",
        "std_tt": ez / "lib/ez130_8t_tt_1p20v_25c.lib", "std_ss": ez / "lib/ez130_8t_ss_1p08v_125c.lib",
        "ram_tt": sram / f"lib/{macro}_typ_1p20V_25C.lib", "ram_ss": sram / f"lib/{macro}_slow_1p08V_125C.lib",
        "tech_lef": ez / "lef/ez130_sg13g2_tech.lef", "cell_lef": Path(host["combined_cell_lef"]),
        "std_lef": ez / "lef/ez130_8t.lef",
        "floorplan": recipe / "native-floorplan.json", "physical": recipe / "native-physical.json",
        "routing": recipe / "native-routing.json", "interfaces": recipe / "lvs_interfaces.json",
        "ecos": recipe / "native_ecos.py", "frontend": recipe / "native_frontend.py",
        "prepare": recipe / "native_frontend_inputs.py",
        "delay_helper": road / "build_delay_cells.py", "sizing_helper": road / "build_sizing_profile.py",
        "liberty_reader": road / "liberty_lite.py",
        "pin_caps_source": ROOT / "modules/RustEDA-Common/crates/formats/rusteda-formats/examples/export_pin_caps.rs",
        "producer": recipe / "producer.py", "estimator": recipe / "estimate_spef.py",
        "rc_policy": recipe / "rc-estimate-policy.json",
    }
    tools = host["tools"]
    fe_tools = {k: tools[k] for k in ("rustsys", "rusteda", "ruststa", "rustroad", "python",
                                      "gnu_time", "xxhsum", "pin_caps")}
    threads = int(host["threads"])
    scope = {"threads": threads, "cpu_affinity": list(host["cpu_affinity"]), "max_log_bytes": 64 << 20,
             "memory_mib": 8192, "max_memory_bytes": 8 << 30, "max_stdout_artifact_bytes": 1536 << 20,
             "wire_model": "ideal_diagnostic", "cppr": "off"}
    fe = {"schema": "rusteda.native-frontend-inputs.v1",
          "files": {k: identity(v) for k, v in fe_files.items()},
          "tools": {k: identity(Path(v)) for k, v in fe_tools.items()},
          "scope": scope, "selection": SELECTION}
    (work / "frontend-inputs.json").write_text(json.dumps(fe, indent=2) + "\n")
    python = sys.executable
    subprocess.run([python, str(recipe / "native_frontend.py"), "build", "--config", str(work / "frontend-inputs.json"),
                    "--workspace", str(work / "fe"), "--base-rev", "empty"], check=True)

    signoff, sram_lvs, ez_lvs = (Path(host[k]).resolve() for k in ("signoff_deck", "sram_lvs", "ez_lvs"))
    be_files = {
        "physical_prepare": recipe / "native_physical_inputs.py", "rc_builder": Path(host["rc_builder"]),
        "reference_extract": recipe / "reference_extract.py", "timing_runner": recipe / "signoff_timing.py",
        "postroute_runner": recipe / "native_postroute.py", "lvs_runner": recipe / "signoff_lvs.py",
        "drc_runner": recipe / "signoff_drc.py", "macro_contract": recipe / "macro_contract.py",
        "std_gds": ez / "gds/ez130_8t.gds", "ram_gds": sram / f"gds/{macro}.gds",
        "layer_map": ihp / "libs.tech/klayout/tech/sg13g2.map",
        "rules_nom": ihp / "libs.tech/librelane/openrcx/IHP_rcx_patterns.rules",
        "magic_tech": ihp / "libs.tech/magic/ihp-sg13g2.tech",
        "magic_extract": ihp / "libs.tech/magic/ihp-sg13g2-extract.tech",
        "ram_lef": sram / f"lef/{macro}.lef",
        "drc_profile": signoff / "provenance.json", "drc_deck": signoff / "klayout/tech/drc/ihp-sg13g2.drc",
        "antenna_deck": signoff / "klayout/tech/drc/rule_decks/antenna.drc",
        "lvs_deck": sram_lvs / "sg13g2.lvs", "std_cdl": ez / "cdl/ez130_8t.cdl", "ram_cdl": sram_lvs / "sram.cdl",
        "filler_cdl": ez_lvs / "spacers.cdl", "sram_qualification": Path(host["sram_qualification"]),
        "lvs_interfaces": recipe / "lvs_interfaces.json", "antenna_runner": recipe / "native_antenna.py",
        "antenna_fields": recipe / "report_fields.py", "contract": prep / "contract.json",
    }
    if host.get("antenna_frozen"):
        # The grouped single-process antenna session (R20 lineage): helpers frozen by
        # antenna_session/freeze.py, plus the qualified integer group policy.
        frozen = Path(host["antenna_frozen"]).resolve()
        be_files.update({
            "antenna_runner": frozen / "native_antenna_groups.py", "antenna_legacy": frozen / "native_antenna.py",
            "antenna_fields": frozen / "report_fields.py", "antenna_verifier": frozen / "verify.py",
            "antenna_source_manifest": frozen / "manifest.json",
            "antenna_group_policy": frozen / "antenna-group-policy.json",
            "antenna_session_policy": frozen / "antenna-session-policy.json",
        })
    be = {"schema": "rusteda.native-backend-inputs.v1",
          "frontend_fragment": identity(work / "fe/fragment.json"),
          "files": {k: identity(v) for k, v in be_files.items()},
          "tools": {k: identity(Path(tools[k])) for k in ("appimage", "opensta", "yosys")},
          "pdk_root": str(ihp.parent),
          # The producer's foundry checks (be_main, be_antenna -> be_lvs) run as two concurrent
          # two-core lanes on a four-CPU host (producer.signoff_parallel_group); host.json can opt out.
          "parallel_signoff": bool(host.get("parallel_signoff", threads == 4))}
    (work / "backend-inputs.json").write_text(json.dumps(be, indent=2) + "\n")
    subprocess.run([python, str(recipe / "native_backend.py"), "--inputs", str(work / "backend-inputs.json"),
                    "--workspace", str(work / "be"), "--base-rev", "empty"], check=True)
    print(json.dumps({"status": "composed_unqualified", "manifest": str(work / "be/prefix-manifest.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Render the native recipe's logic-signoff inputs and print them.

`native_flow.py` renders `backend/logic-inputs.json` (step `nf_logic_inputs`)
and runs `signoff_logic.py` (step `nf_logic`) for synthesis equivalence, final
equivalence and vendor-model simulation. The prefix manifest used on hosts
without component-qualification evidence stops before those steps, so this
helper writes the same document for a completed producer output directory.
The caller runs `signoff_logic.py --inputs <file>` inside the producer-owned
memory cgroup (see `run_native.sh`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MACRO = "RM_IHPSG13_1P_1024x32_c2_bm_bist"


def sha(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="producer output directory")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--synthesis-netlist", default="frontend/mapped.v")
    parser.add_argument("--final-netlist", default="logs/be_netlist.stdout")
    args = parser.parse_args()
    host = json.loads(args.host.read_text())
    prep = Path(host["prepared"]).resolve()
    ez = Path(host["ez130_root"]).resolve()
    sram = Path(host["ihp_root"]).resolve() / "libs.ref/sg13g2_sram"
    out = args.output.resolve()
    tools = host["tools"]
    rtl, lib, bb, tb = prep / "rtl/design.v", ez / "lib/ez130_8t_tt_1p20v_25c.lib", prep / "rtl/sram_bb.v", prep / "tb.sv"
    synth, final = out / args.synthesis_netlist, out / args.final_netlist
    doc = dict(
        schema="rusteda.logic-signoff-inputs.v1", top="ibex_sram_bench", rtl=str(rtl),
        synthesis_netlist=str(synth), final_netlist=str(final), library=str(lib), macro_bb=str(bb),
        macro=MACRO, macro_instances=["u_imem", "u_dmem"], tb=str(tb),
        vendor_verilog=[str(ez / "verilog/ez130_8t.v"), str(sram / f"verilog/{MACRO}.v"),
                        str(sram / "verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v")],
        vendor_include_dirs=[str(sram / "verilog")],
        relation_checker=str(ROOT / "modules/RustSys/verification/benchmarks/open-hardware/large_lec.py"),
        tools=dict(yosys=tools["yosys"], abc=tools["abc"], checker=tools["checker"],
                   iverilog=tools["iverilog"], vvp=tools["vvp"], time=tools["gnu_time"]),
        physical_lef=str(ez / "lef/ez130_8t.lef"),
        physical_libraries=dict(std_tt=str(lib), ram_tt=str(sram / f"lib/{MACRO}_typ_1p20V_25C.lib"),
                                std_ss=str(ez / "lib/ez130_8t_ss_1p08v_125c.lib"),
                                ram_ss=str(sram / f"lib/{MACRO}_slow_1p08V_125C.lib")),
        limits=dict(memory_mib=4096, swap_mib=512, deadline_seconds=1200, threads=2, stage_seconds=180,
                    cec_seconds=160, max_conflicts=2_000_000, discovery_work=16_000_000_000, discovery_rounds=32),
        expected_hashes=dict(rtl=sha(rtl), library=sha(lib), macro_bb=sha(bb), tb=sha(tb),
                             synthesis_netlist=sha(synth), final_netlist=sha(final)),
    )
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(json.dumps(doc, indent=2) + "\n")
    print(json.dumps({"status": "rendered", "inputs": str(args.destination)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

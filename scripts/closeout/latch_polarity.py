#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded dual-PDK transistor check of transparent-latch data polarity.

This checks logic polarity, not latch timing accuracy. Run inside a 1 GiB
memory scope and a 120 second timeout; measured peak child RSS is <400 MiB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="fresh evidence directory")
    parser.add_argument("--road", type=Path, default=ROOT/"modules/RustROAD")
    args = parser.parse_args()
    road = args.road.resolve()
    sys.path.insert(0, str(road/"scripts"))
    sys.path.insert(0, str(road/"scripts/signoff"))
    import rtl2gds_design as D
    from manifest import tree_digest
    from verify_logic import bounded_text
    from verify_physical import PhysicalHarness

    work = args.out.resolve()
    work.mkdir(parents=True, exist_ok=False)
    result = dict(schema="rusteda.latch-polarity.v1", status="unverified",
                  scope="transparent-latch data polarity only; not timing parity", rows=[])
    try:
        config = json.loads((road/"fixtures/flow/signoff-pinned.json").read_text())
        tool = D.load_tools(road/"fixtures/flow/rtl2gds-tools.json", {})["ngspice"]
        if tool["status"] != "passed":
            raise D.GateError("Ngspice must match its pinned executable")
        ngspice = Path(tool["path"])
        harness = PhysicalHarness(work, argparse.Namespace(threads=1, memory_mib=768, timeout_seconds=60))
        for tag, cell in (("sky130", "sky130_fd_sc_hd__dlxtp_1"),
                          ("gf180", "gf180mcu_fd_sc_mcu7t5v0__latq_1")):
            pdk = config["pdks"][tag]
            base = Path(pdk["host_root"])
            cell_file = base/pdk["files"]["cell_spice"]["path"]
            if D.sha256_file(cell_file) != pdk["files"]["cell_spice"]["sha256"]:
                raise D.GateError("PDK cell SPICE differs from its pin")
            match = re.search(r"^\.subckt\s+"+re.escape(cell)+r"\s+(.+)$",
                              bounded_text(cell_file, 64*1024**2), re.I|re.M)
            if not match:
                raise D.GateError("latch transistor model is absent")
            ports = match[1].split()
            supply = 1.8 if tag == "sky130" else 3.3
            model = base/"libs.tech/ngspice"/("sky130.lib.spice" if tag == "sky130" else "sm141064.ngspice")
            corner = "tt" if tag == "sky130" else "typical"
            mapping = dict(D="d", E="e", G="e", GATE="e", Q="q", VDD="vdd", VNW="vdd",
                           VPW="0", VSS="0", VPWR="vdd", VPB="vdd", VGND="0", VNB="0")
            lines = ["Transparent latch data-polarity oracle", f'.lib "{model}" {corner}',
                     f'.include "{cell_file}"', ".temp 25", ".option reltol=1e-7",
                     f"Vdd vdd 0 {supply}", f"Ve e 0 {supply}",
                     f"Vd d 0 PULSE(0 {supply} 5n .1n .1n 5n 20n)",
                     "Xl "+" ".join(mapping[x] for x in ports)+" "+cell, "Cl q 0 .01p",
                     ".tran .005n 18n", ".meas tran q_low FIND v(q) AT=3n",
                     ".meas tran q_high FIND v(q) AT=8n", ".meas tran q_low_after FIND v(q) AT=15n",
                     ".control", "set numdgt=17", "run", "quit", ".endc", ".end"]
            if tag == "gf180":
                lines.insert(1, f'.include "{base}/libs.tech/ngspice/design.ngspice"')
            deck = work/(tag+".spice")
            deck.write_text("\n".join(lines)+"\n")
            receipt = harness.run(tag, [str(ngspice), "-n", "-b", str(deck)], file_limit_bytes=1024**2)
            log = bounded_text(Path(receipt["stdout"]), 1024**2)+bounded_text(Path(receipt["stderr"]), 1024**2)
            values = {k:float(v) for k,v in re.findall(
                r"(?m)^(q_low|q_high|q_low_after)\s*=\s*([-+.eE\d]+)", log)}
            ok = (receipt["exit_code"] == 0 and set(values) == {"q_low", "q_high", "q_low_after"}
                  and 0 <= values["q_low"] < .1*supply and values["q_high"] > .9*supply
                  and 0 <= values["q_low_after"] < .1*supply)
            result["rows"].append(dict(pdk=tag, cell=cell, status="passed" if ok else "unverified",
                corner="tt", spice_model_corner=corner, temperature_c=25, load_pf=.01,
                enable="held_high", wire_model="transistor model with lumped output load",
                supply_v=supply, measurements_v=values, ngspice_sha256=tool["actual_sha256"],
                model_tree=tree_digest(base/"libs.tech/ngspice"), cell_spice_sha256=D.sha256_file(cell_file),
                deck_sha256=D.sha256_file(deck), receipt=receipt))
        if len(result["rows"]) == 2 and all(r["status"] == "passed" for r in result["rows"]):
            result["status"] = "passed"
    except (D.GateError, OSError, ValueError, KeyError) as error:
        result["reason"] = str(error)
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (work/"result.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps(dict(status=result["status"], result=str(work/"result.json"))))
    return 0 if result["status"] == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())

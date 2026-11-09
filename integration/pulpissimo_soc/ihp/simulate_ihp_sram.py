#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Simulate the IHP tc_sram adapter against the official macro behavioral model.

The synthesis wrapper declares the macro as a blackbox. For this smoke test the
blackbox declaration is removed and the official `-DFUNCTIONAL` Verilog model is
compiled instead. The test covers two macros (bank selection) and a byte-masked
write. It is an adapter test, not a PULPissimo SoC simulation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--ihp-verilog", type=Path, required=True, help="sg13g2_sram/verilog directory")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--tb", type=Path, default=Path(__file__).resolve().parent / "sim" / "tb.v")
    parser.add_argument("--verilator", default="verilator")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()

    wrapper = args.wrapper.read_text()
    if "module tc_sram #(" not in wrapper:
        raise SystemExit("wrapper does not contain the tc_sram adapter")
    sim_wrapper = wrapper[wrapper.index("module tc_sram #("):]
    args.workdir.mkdir(parents=True, exist_ok=True)
    (args.workdir / "tc_sram_sim.v").write_text(sim_wrapper)
    (args.workdir / "tb.v").write_text(args.tb.read_text())

    macro = args.ihp_verilog / "RM_IHPSG13_1P_1024x32_c2_bm_bist.v"
    core = args.ihp_verilog / "RM_IHPSG13_1P_core_behavioral_bm_bist.v"
    for path in (macro, core, args.workdir / "tb.v"):
        if not path.is_file():
            raise SystemExit(f"missing {path}")
    command = [
        args.verilator, "--binary", "--timing", "-j", str(args.jobs), "-Wno-fatal",
        "--top", "tb", "--Mdir", str(args.workdir / "obj_dir"),
        str(args.workdir / "tb.v"), str(args.workdir / "tc_sram_sim.v"),
        str(macro), str(core), "-DFUNCTIONAL",
    ]
    subprocess.run(command, check=True, cwd=args.workdir)
    run = subprocess.run([str(args.workdir / "obj_dir" / "Vtb")], text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    print(run.stdout, end="")
    if run.returncode != 0 or "PASS" not in run.stdout:
        return 1
    record = {
        "schema": "rusteda.pulpissimo-ihp-adapter-simulation.v1",
        "result": "pass",
        "wrapper": {"path": str(args.wrapper.resolve()), "sha256": digest(args.wrapper)},
        "macro_verilog": {"path": str(macro.resolve()), "sha256": digest(macro)},
        "core_verilog": {"path": str(core.resolve()), "sha256": digest(core)},
        "command": command,
        "coverage": ["16384-word bank (16 macros)", "write/read slice 0 (address 5)", "write/read slice 1 (address 1029)", "write/read slice 11 (address 11269)", "byte-masked write (address 7)"],
    }
    (args.workdir / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run the SRAM-only KLayout LVS and write the macro qualification record.

The Ibex LVS gate (`signoff_lvs.py`, `check_lvs.py`) requires a qualification
record proving that the original SRAM GDS matches its CDL under the same deck.
The original record was a local artifact of the first campaign host. This
helper regenerates it from an actual run: KLayout extracts the macro GDS with
the prepared `sg13g2.lvs` deck and compares it with the prepared SRAM CDL.
The record is `qualified` only when every circuit pair matches, extraction
reports no errors and the expanded device classes agree on both sides.

Run it with a Python that imports `klayout.db` (the multi-call `python3`).
Bounded to one macro, 4096 circuit definitions and 5,000,000 expanded devices.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import time

import klayout.db as db

MACRO = "RM_IHPSG13_1P_1024x32_c2_bm_bist"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def device_counts(root) -> dict:
    memo, active, objects = {}, set(), 0

    def visit(circuit, depth):
        nonlocal objects
        if depth >= 128 or circuit.name in active:
            raise ValueError("macro hierarchy cycle/depth budget exceeded")
        if circuit.name in memo:
            return memo[circuit.name]
        if len(memo) >= 4096:
            raise ValueError("macro circuit budget exceeded")
        active.add(circuit.name)
        counts = Counter()
        for device in circuit.each_device():
            objects += 1
            counts[device.device_class().name.lower()] += 1
        for sub in circuit.each_subcircuit():
            objects += 1
            counts.update(visit(sub.circuit_ref(), depth + 1))
        if objects > 5_000_000 or sum(counts.values()) > 5_000_000:
            raise ValueError("macro expanded-device budget exceeded")
        active.remove(circuit.name)
        memo[circuit.name] = counts
        return counts

    return dict(visit(root, 0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--klayout", required=True, help="KLayout executable (or multi-call wrapper)")
    parser.add_argument("--klayout-prefix", default="", help="optional multi-call argv[1], e.g. klayout")
    parser.add_argument("--deck", type=Path, required=True, help="prepared sg13g2.lvs")
    parser.add_argument("--gds", type=Path, required=True, help="original SRAM GDS")
    parser.add_argument("--cdl", type=Path, required=True, help="prepared SRAM CDL")
    parser.add_argument("--macro", default=MACRO)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.workspace.exists() and any(args.workspace.iterdir()):
        raise SystemExit("REVISION_CONFLICT: workspace must be empty")
    args.workspace.mkdir(parents=True, exist_ok=True)
    work = args.workspace.resolve()
    command = [args.klayout] + ([args.klayout_prefix] if args.klayout_prefix else []) + ["-b", "-r", str(args.deck)]
    switches = dict(threads=str(args.threads), run_mode="deep", no_net_names="false", spice_comments="false",
                    net_only="false", top_lvl_pins="false", no_simplify="false", no_series_res="false",
                    no_parallel_res="false", combine_devices="false", purge="false", purge_nets="false",
                    topcell=args.macro, ignore_top_ports_mismatch="false", implicit_nets="",
                    input=str(args.gds.resolve()), schematic=str(args.cdl.resolve()),
                    report=str(work / "sram.lvsdb"), log=str(work / "deck.log"),
                    target_netlist=str(work / "extracted.cir"))
    for key, value in switches.items():
        command += ["-rd", f"{key}={value}"]
    start = time.monotonic()
    with (work / "klayout.stdout").open("w") as out:
        result = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT, timeout=3600)
    wall = time.monotonic() - start
    lvs = db.LayoutVsSchematic()
    lvs.read(str(work / "sram.lvsdb"))
    errors = [str(e)[:4096] for e in lvs.each_error()][:128]
    circuits, pair = [], None
    for cp in lvs.xref().each_circuit_pair():
        a, b = cp.first(), cp.second()
        circuits.append(dict(layout=a.name if a else None, reference=b.name if b else None,
                             status=str(cp.status())))
        if a is not None and a.name.casefold() == args.macro.casefold():
            pair = (a, b)
    counts = {}
    pins = None
    if pair and pair[1] is not None:
        counts = dict(layout=device_counts(pair[0]), reference=device_counts(pair[1]))
        pins = dict(layout=pair[0].pin_count(), reference=pair[1].pin_count())
    all_match = bool(circuits) and all(c["status"] == "Match" for c in circuits)
    qualified = (result.returncode == 0 and all_match and not errors and pair is not None
                 and counts.get("layout") == counts.get("reference") and pins
                 and pins["layout"] == pins["reference"])
    record = dict(
        schema="rusteda.sram-lvs-qualification.v1",
        status="qualified" if qualified else "failed",
        macro=args.macro,
        extraction_errors=errors,
        all_circuits_match=all_match,
        top_pin_count=pins["layout"] if pins and pins["layout"] == pins["reference"] else None,
        expanded_device_counts_each_side=counts.get("layout") if qualified else None,
        expanded_device_counts=counts,
        circuits=len(circuits),
        frozen={args.deck.name: sha256(args.deck), args.cdl.name: sha256(args.cdl)},
        inputs={"gds": dict(path=str(args.gds.resolve()), sha256=sha256(args.gds))},
        klayout_exit_code=result.returncode,
        lvs_wall_seconds=round(wall, 3),
        database_sha256=sha256(work / "sram.lvsdb"),
        scope="SRAM-only device LVS, regenerated on this host by an actual KLayout run",
    )
    (work / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({k: record[k] for k in ("status", "all_circuits_match", "top_pin_count",
                                             "expanded_device_counts_each_side", "lvs_wall_seconds")}))
    return 0 if qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Read a real KLayout LVS database and enforce the complete Ibex interface.

Run with the pinned KLayout Python runtime under a 2 GiB / 120 s OS guard.
At most4096 circuit pairs,100000 pin pairs and128 stored diagnostics. Macro
counts use a bounded DP (4096 definitions/500000 stored objects/128 depth,
64 device classes). Two immutable netlists, one worker, <64 MiB extra scratch.
No paths or flattened device graphs are materialized. Reference JSON <=16 MiB;
LVS database <=256 MiB. This is a read-only verdict checker, not an LVS engine.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import klayout.db as db


def read(path, limit):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"missing or oversized artifact: {path}")
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("artifact grew beyond budget")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--top", default="ibex_sram_bench")
    parser.add_argument("--macro", default="RM_IHPSG13_1P_1024x32_c2_bm_bist")
    parser.add_argument("--macro-count", type=int, default=2)
    parser.add_argument("--macro-qualification", type=Path,
                        help="Required for SRAM: pinned original-device qualification JSON")
    args = parser.parse_args()
    if not 0 <= args.macro_count <= 16:
        raise ValueError("macro instance budget is 0..16")
    if not args.database.is_file() or args.database.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("LVS database file budget exceeded")
    raw = read(args.reference, 16 * 1024 * 1024)
    reference = json.loads(raw)
    expected = reference["top_ports"]
    if len(expected) > 100000 or len(expected) != len({p.casefold() for p in expected}):
        raise ValueError("invalid reference top port list")
    lvs = db.LayoutVsSchematic()
    lvs.read(str(args.database))
    errors = []
    for error in lvs.each_error():
        if len(errors) >= 128:
            raise ValueError("LVS extraction diagnostics exceeded budget (rejected)")
        errors.append(str(error)[:4096])
    qualification, qualification_hash = None, None
    if args.macro_count and args.macro_qualification is None:
        raise ValueError("SRAM device qualification is required; a matching empty block is insufficient")
    if args.macro_qualification is not None:
        qraw = read(args.macro_qualification, 16 * 1024 * 1024)
        qualification = json.loads(qraw)
        qualification_hash = hashlib.sha256(qraw).hexdigest()
        if (qualification.get("status") != "qualified"
                or qualification.get("macro") != args.macro
                or qualification.get("extraction_errors") != []):
            raise ValueError("original SRAM device qualification is incomplete")
    circuits, top, pins = [], None, []
    macro_instances, reference_macro_instances = 0, 0
    macro_pair = None
    for cp in lvs.xref().each_circuit_pair():
        if len(circuits) >= 4096:
            raise ValueError("LVS circuit pair budget exceeded")
        a, b = cp.first(), cp.second()
        circuits.append(dict(layout=a.name if a else None, reference=b.name if b else None,
                             status=str(cp.status())))
        if a is not None and a.name.casefold() == args.macro.casefold():
            if macro_pair is not None:
                raise ValueError("duplicate macro circuit pair")
            macro_pair = (a, b)
        if a is None or a.name != args.top:
            continue
        if top is not None:
            raise ValueError("duplicate top circuit pair")
        top = dict(layout_pins=a.pin_count(), reference_pins=b.pin_count() if b else None,
                   status=str(cp.status()))
        for pair in lvs.xref().each_pin_pair(cp):
            if len(pins) >= 100000:
                raise ValueError("LVS pin pair budget exceeded")
            left, right = pair.first(), pair.second()
            pins.append(dict(layout=left.name() if left else None,
                             reference=right.name() if right else None,
                             status=str(pair.status())))
        for circuit, source in ((a, "layout"), (b, "reference")):
            if circuit is None:
                continue
            count = 0
            for sub in circuit.each_subcircuit():
                count += 1
                if count > 100000:
                    raise ValueError("top subcircuit budget exceeded")
                if sub.circuit_ref().name.casefold() == args.macro.casefold():
                    if source == "layout":
                        macro_instances += 1
                    else:
                        reference_macro_instances += 1
    pin_identity = (
        len(pins) == len(expected)
        and top is not None
        and top["layout_pins"] == top["reference_pins"] == len(expected)
        and all(p["status"] == "Match" and p["layout"] and p["reference"]
                and p["layout"].casefold() == p["reference"].casefold() for p in pins)
        and {p["layout"].casefold() for p in pins if p["layout"]} == {p.casefold() for p in expected}
    )
    macro_counts, macro_devices_verified = {}, args.macro_count == 0 and qualification is None
    if qualification is not None and macro_pair is not None:
        # Topological DP over shared circuit definitions, never flattened device
        # paths. Count repeated instances by adding a cached small class vector.
        objects = 0
        def device_counts(root):
            memo, active = {}, set()
            def visit(circuit, depth):
                nonlocal objects
                key = circuit.name
                if depth >= 128 or key in active:
                    raise ValueError("macro circuit hierarchy cycle/depth budget exceeded")
                if key in memo:
                    return memo[key]
                if len(memo) >= 4096:
                    raise ValueError("macro circuit count budget exceeded")
                active.add(key)
                counts = Counter()
                for device in circuit.each_device():
                    objects += 1
                    if objects > 500000:
                        raise ValueError("macro stored-object budget exceeded")
                    counts[device.device_class().name.lower()] += 1
                    if len(counts) > 64:
                        raise ValueError("macro device-class budget exceeded")
                for sub in circuit.each_subcircuit():
                    objects += 1
                    if objects > 500000:
                        raise ValueError("macro hierarchy work budget exceeded")
                    counts.update(visit(sub.circuit_ref(), depth + 1))
                    if len(counts) > 64 or sum(counts.values()) > 5_000_000:
                        raise ValueError("macro expanded-device budget exceeded")
                active.remove(key)
                memo[key] = counts
                return counts
            return dict(visit(root, 0))
        a, b = macro_pair
        if a is not None and b is not None:
            macro_counts = dict(layout=device_counts(a), reference=device_counts(b))
            expected_counts = qualification["expanded_device_counts_each_side"]
            macro_devices_verified = (macro_counts["layout"] == macro_counts["reference"] == expected_counts
                                      and a.pin_count() == b.pin_count() == qualification["top_pin_count"])
    passed = bool(circuits and top and all(c["status"] == "Match" for c in circuits)
                  and not errors and pin_identity and macro_instances == args.macro_count
                  and reference_macro_instances == args.macro_count and macro_devices_verified)
    with args.database.open("rb") as stream:
        database_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    result = dict(status="passed" if passed else "failed", scope="full device-level LVS",
                  top=args.top, top_pair=top, top_port_identity=pin_identity,
                  top_pin_pairs=pins, macro_instances=macro_instances,
                  reference_macro_instances=reference_macro_instances,
                  expected_macro_instances=args.macro_count, circuits=circuits,
                  macro_devices_verified=macro_devices_verified,
                  macro_expanded_device_counts=macro_counts,
                  macro_qualification_sha256=qualification_hash,
                  extraction_errors=errors, database_sha256=database_hash,
                  reference_report_sha256=hashlib.sha256(raw).hexdigest(),
                  reference_cdl_sha256=reference["reference_sha256"],
                  provenance_scope="requires the pinned extraction deck and source input bindings",
                  diagnostics_truncated=False)
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(json.dumps(dict(status="unverified", error=str(error))))
        raise SystemExit(2) from error

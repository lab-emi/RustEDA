#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Check final routed utilization against an explicit owner acceptance policy.

Requires the existing final placement qualification and its hash-bound DEF/GDS.
This does not qualify routing, DRC or LVS independently. Coordinates and areas
are integer DBU. Memory: streaming DEF <=128MiB/500k components, LEF <=8MiB,
<=4096 masters, expected <256MiB RSS. Run with a cgroup/deadline guard.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, DecimalException
import fcntl
import hashlib
import json
from pathlib import Path
import re
import resource
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path, cap=16 << 20):
    require(path.is_file() and path.stat().st_size <= cap, "JSON byte budget")
    return json.loads(path.read_text())


def identity(path, cap=512 << 20):
    path = path.resolve(strict=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= cap, "file byte budget")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ino) ==
            (after.st_size, after.st_mtime_ns, after.st_ino), "source changed")
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def dbu(value, units):
    try:
        number = Decimal(str(value)) * units
    except DecimalException as error:
        raise ValueError("invalid or out-of-range geometry number") from error
    require(number.is_finite() and abs(number) <= 1 << 50
            and number == number.to_integral_value(),
            "geometry is not integer DBU")
    return int(number)


def lef_sizes(path, units):
    require(path.stat().st_size <= 8 << 20, "LEF byte budget")
    sizes, current = {}, None
    for line in path.read_text().splitlines():
        start = re.fullmatch(r"\s*MACRO\s+(\S+)\s*", line)
        if start:
            require(current is None and len(sizes) < 4096, "LEF macro nesting/count")
            current = start[1]
            require(current not in sizes, "duplicate LEF master")
        elif current and re.fullmatch(r"\s*END\s+" + re.escape(current) + r"\s*", line):
            require(current in sizes, "LEF master has no SIZE")
            current = None
        elif current and line.strip().startswith("SIZE "):
            match = re.fullmatch(r"\s*SIZE\s+([0-9.eE+-]+)\s+BY\s+([0-9.eE+-]+)\s*;\s*", line)
            require(match is not None and current not in sizes, "unsupported/duplicate LEF SIZE")
            width, height = dbu(match[1], units), dbu(match[2], units)
            require(width > 0 and height > 0, "nonpositive LEF footprint")
            sizes[current] = (width, height)
    require(current is None and sizes, "unfinished/empty LEF")
    return sizes


def components(path):
    require(path.stat().st_size <= 128 << 20, "DEF byte budget")
    names, counts, fixed = set(), Counter(), {}
    active, seen, statement, declared, units, top = False, False, "", None, None, None
    with path.open() as stream:
        while line := stream.readline(65537):
            require(len(line) <= 65536, "DEF line budget")
            if line.startswith("DESIGN "):
                require(top is None, "duplicate DEF DESIGN")
                top = line.split()[1]
            if line.startswith("UNITS DISTANCE MICRONS "):
                require(units is None, "duplicate DEF units")
                units = int(line.split()[3])
            if line.startswith("COMPONENTS "):
                require(not seen, "duplicate DEF components")
                declared = int(line.split()[1])
                require(0 < declared <= 500000, "DEF component count budget")
                active, seen = True, True
                continue
            if line.startswith("END COMPONENTS"):
                require(active and not statement.strip(), "unfinished DEF component")
                active = False
                # Only placement is interpreted here. A later power-net record
                # may contain every supply pin on one very long DEF line.
                break
            if not active:
                continue
            statement += line
            require(len(statement) <= 65536, "DEF statement budget")
            if ";" not in line:
                continue
            match = re.fullmatch(r"\s*-\s+(\S+)\s+(\S+)\s+\+\s+(FIXED|PLACED)\s*"
                                 r"\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(N|FS)\s*;\s*", statement)
            require(match is not None, "unsupported DEF component")
            name, master, status, x, y, orientation = match.groups()
            require(name not in names and len(names) < declared, "duplicate/excess component")
            names.add(name)
            counts[master] += 1
            if status == "FIXED":
                require(len(fixed) < 32, "fixed macro count budget")
                fixed[name] = dict(master=master, x=int(x), y=int(y), orientation=orientation)
            statement = ""
    require(not active and seen and len(names) == declared, "DEF count/section mismatch")
    require(top == "ibex_sram_bench" and units == 1000, "unexpected final top or DBU")
    return units, declared, counts, fixed


def utilization(def_path, lef_path, physical, common, maximum):
    require(type(maximum) is int and 1 <= maximum <= 95, "invalid density limit")
    units, count, counts, fixed = components(def_path)
    sizes = lef_sizes(lef_path, units)
    require(set(counts) <= sizes.keys(), "missing final component footprint")
    require(set(fixed) == set(common["macros"]), "final SRAM instance set differs")
    core = [dbu(x, units) for x in common["core_um"]]
    core_area = (core[2] - core[0]) * (core[3] - core[1])
    require(core_area > 0, "nonpositive core area")
    halo = dbu(common["macro_halo_um"], units)
    require(halo >= 0, "negative macro halo")
    macro_area, reserved, boxes = 0, 0, []
    for name, actual in fixed.items():
        expected = common["macros"][name]
        require(actual["master"] == expected["cell"] and
                [actual["x"], actual["y"]] == [dbu(x, units) for x in expected["location_um"]]
                and actual["orientation"] == expected["orientation"], "final SRAM placement differs")
        width, height = sizes[actual["master"]]
        macro_area += width * height
        box = [actual["x"] - halo, actual["y"] - halo,
               actual["x"] + width + halo, actual["y"] + height + halo]
        require(core[0] <= box[0] < box[2] <= core[2] and
                core[1] <= box[1] < box[3] <= core[3], "macro halo outside core")
        require(all(box[2] <= old[0] or old[2] <= box[0] or
                    box[3] <= old[1] or old[3] <= box[1] for old in boxes), "overlapping macro halos")
        boxes.append(box)
        reserved += (box[2] - box[0]) * (box[3] - box[1])
    filler_masters = set(physical["filler_cells"])
    macro_masters = {x["master"] for x in fixed.values()}
    require(not filler_masters & macro_masters, "macro misclassified as filler")
    require(all(counts[m] == sum(x["master"] == m for x in fixed.values())
                for m in macro_masters), "additional unfixed SRAM instance")
    filler_area = sum(counts[m] * sizes[m][0] * sizes[m][1] for m in counts if m in filler_masters)
    total_area = sum(n * sizes[m][0] * sizes[m][1] for m, n in counts.items())
    occupied = total_area - filler_area - macro_area
    available = core_area - reserved
    require(available > 0 and occupied >= 0, "invalid available/occupied area")
    passed = occupied * 100 <= maximum * available
    return dict(status="passed" if passed else "failed", max_percent=maximum,
                final_nonfiller_utilization_percent=100 * occupied / available,
                macro_inclusive_core_utilization_percent=100 * (occupied + macro_area) / core_area,
                area_dbu2=dict(core=core_area, macros=macro_area, macro_halos_inclusive=reserved,
                               available=available, final_nonfiller_standard_cells=occupied,
                               reported_separately_filler_decap=filler_area),
                dbu_per_um=units, component_count=count, masters=dict(sorted(counts.items())),
                excluded_gap_fill_masters=sorted(filler_masters),
                definition="all final functional/CTS/ECO/tap footprint divided by core minus real macro halos; only explicit gap filler/decap masters excluded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    config = read(args.inputs, 1 << 20)
    require(config["schema"] == "rusteda.final-density-inputs.v1", "input schema")
    files = config["files"]
    require(set(files) == {"placement", "cell_lef", "physical", "contract", "policy"}, "file roles")
    for item in files.values():
        require(identity(Path(item["path"])) == item, "stale input")
    placement = read(Path(files["placement"]["path"]))
    require(placement["status"] == "passed" and placement.get("independent_reports"),
            "FINAL_LAYOUT_REQUIRED: passed final placement qualification missing")
    root = Path(config["run_root"]).resolve(strict=True)
    artifacts = {k: placement["artifacts"][k] for k in ("def", "gds")}
    for item in artifacts.values():
        path = Path(item["path"]).resolve(strict=True)
        require(path.is_relative_to(root) and identity(path) == item, "final artifact binding differs")
    policy = read(Path(files["policy"]["path"]), 1 << 20)
    require(policy["evaluation_stage"] == "after_final_signal_routing_and_all_cts_hold_drv_antenna_ecos",
            "pre-route density cannot satisfy final acceptance")
    output = args.workspace
    output.mkdir(parents=True, exist_ok=True)
    with (output / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == "empty" and not any(p.name != "workspace.lock" for p in output.iterdir()),
                "REVISION_CONFLICT")
        result = utilization(Path(artifacts["def"]["path"]), Path(files["cell_lef"]["path"]),
                             read(Path(files["physical"]["path"])),
                             read(Path(files["contract"]["path"]))["common"], policy["max_percent"])
        require(result["component_count"] == placement["component_count"], "qualified component count differs")
        require(all(identity(Path(item["path"])) == item for item in [*files.values(), *artifacts.values()]),
                "inputs changed during density check")
        result.update(schema="rusteda.final-density-result.v1", scope="final_routed_qualified_layout",
                      files=files, artifacts=artifacts, checker=identity(Path(__file__)),
                      corner="geometry_only", wire_model="actual_final_routed_layout",
                      unconstrained_endpoints=None)
        (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        (output / "HEAD").write_text("rev1\n")
        print(json.dumps(result))
        return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, MemoryError) as error:
        print(json.dumps(dict(status="failed", error=str(error))), file=sys.stderr)
        raise SystemExit(2)

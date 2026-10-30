#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Prepare exactly five EZ130 PG-only spacer abstract contracts from pinned LEF.

The original LEF must explicitly declare CORE SPACER, exactly VDD/VSS INOUT
pins and POWER/GROUND uses. The generated source contract does not assert that
the real GDS contains zero active devices. Complete extracted LVS must still
reject any extra MOS, port short/open, or other layout discrepancy. DECAPs and
SRAMs always require their original full device CDL; this tool cannot stub them.

One <=8 MiB LEF snapshot/100000 lines, five <=64 KiB blocks; no graph expansion.
Peak estimate <64 MiB for the pinned471 KiB source; recommended OS cap512 MiB
and30s. Only an empty, exclusively locked revisioned workspace may be written.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re


LEF_SHA256 = "841070a0650462ac9d14ec042347d0c518f5c78bc55ccbb06c2d5b3f518f55d4"
OUTPUT_SHA256 = "0053af338324459d3973bdcc6b7ce2ab186a0dfcca730a4935286125f2677c36"
ALLOWED = ("FILLER1", "FILLER2", "FILLER4", "FILLER8", "FILLER16")
MAX_BYTES = 8 * 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path: Path) -> bytes:
    require(path.is_file() and path.stat().st_size <= MAX_BYTES, f"missing/oversized LEF: {path}")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "LEF byte budget exceeded")
    return raw


def spacer_contract(text: str, name: str) -> dict:
    require(name in ALLOWED, f"unsupported spacer {name}: only five exact FILLER names allowed")
    pattern = r"^MACRO " + re.escape(name) + r"\s*\n.*?^END " + re.escape(name) + r"\s*$"
    blocks = list(re.finditer(pattern, text, re.M | re.S))
    require(len(blocks) == 1, f"{name}: expected exactly one complete LEF MACRO")
    block = blocks[0][0]
    require(len(block.encode()) <= 65536, f"{name}: LEF block budget exceeded")
    require(bool(re.search(r"\bCLASS\s+CORE\s+SPACER\s*;", block)), f"{name}: CORE SPACER required")
    pins = re.findall(r"^\s+PIN\s+(\S+)\s*$", block, re.M)
    require(len(pins) == 2 and set(pins) == {"VDD", "VSS"},
            f"{name}: exactly VDD/VSS required; signal, body or duplicate pins are unsupported")
    for pin, use in (("VDD", "POWER"), ("VSS", "GROUND")):
        part = re.search(r"^\s+PIN " + pin + r"\s*\n(.*?)^\s+END " + pin + r"\s*$", block, re.M | re.S)
        require(part is not None, f"{name}.{pin}: incomplete PIN block")
        require(bool(re.search(r"\bDIRECTION\s+INOUT\s*;", part[1])), f"{name}.{pin}: INOUT required")
        require(bool(re.search(r"\bUSE\s+" + use + r"\s*;", part[1])), f"{name}.{pin}: USE {use} required")
    return dict(name=name, lef_class="CORE SPACER", lef_pin_order=pins,
                source_contract_pin_order=["VDD", "VSS"],
                original_block_sha256=digest(block.encode()),
                interpretation="PG-only LEF abstract contract; not a layout active-device measurement")


def prepare(lef: Path) -> tuple[str, dict]:
    raw = read(lef)
    require(digest(raw) == LEF_SHA256, "pinned original EZ130 LEF hash mismatch")
    text = raw.decode("utf-8")
    require(text.count("\n") <= 100000, "LEF line budget exceeded")
    cells = [spacer_contract(text, name) for name in ALLOWED]
    rows = ["* Explicit original-LEF CORE SPACER PG-only contracts; no active device ignored.\n"]
    for name in ALLOWED:
        rows.append(f".SUBCKT {name} VDD VSS\n*.PININFO VDD:B VSS:B\n.ENDS {name}\n\n")
    result = "".join(rows)
    require(digest(result.encode()) == OUTPUT_SHA256, "pinned spacer contract output hash mismatch")
    report = dict(status="prepared_not_lvs_verified", revision="rev1",
                  original_lef=str(lef.resolve()), original_lef_sha256=LEF_SHA256,
                  original_lef_bytes=len(raw), allowed_exact_names=list(ALLOWED),
                  cells=cells, output_cdl_sha256=OUTPUT_SHA256,
                  lvs_status="unchecked", actual_layout_active_device_status="unchecked",
                  layout_validation_requirement="full extracted LVS must reject any extra active device or broken PG connection",
                  decap_and_sram_policy="use original complete device CDL; never generate an abstract contract here",
                  bounds=dict(input_bytes=MAX_BYTES, input_lines=100000, macro_blocks=5, block_bytes=65536),
                  peak_memory_estimate="<64MiB for pinned source, one worker; recommended512MiB/30s OS guard")
    return result, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    parser.add_argument("--lef", type=Path, required=True, help="original hash-pinned EZ130 standard-cell LEF")
    args = parser.parse_args()
    require(args.base_rev == "empty", "REVISION_CONFLICT: preparation requires --base-rev empty")
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(not any(path.name != "workspace.lock" for path in args.workspace.iterdir()),
                "REVISION_CONFLICT: preparation requires an empty workspace")
        output, report = prepare(args.lef)
        report["generator_sha256"] = digest(Path(__file__).read_bytes())
        (args.workspace / "spacers.cdl").write_text(output, encoding="utf-8", newline="")
        (args.workspace / "provenance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (args.workspace / "HEAD").write_text("rev1\n", encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("status", "revision", "output_cdl_sha256", "lvs_status")}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from error

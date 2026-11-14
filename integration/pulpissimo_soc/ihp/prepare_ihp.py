#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Build the PULPissimo IHP SG13G2 input and macro Liberty for RustSys.

The prepared SKY130 Verilog is technology-independent except for the
behavioral ``tc_sram`` module. This script replaces that module with the IHP
SRAM-macro adapter, copies the official macro Liberty and marks the macro cell
``is_macro_cell`` so RustSys preserves it as an opaque hard macro. It never
modifies the PDK in place and records every hash in a manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

MACRO = "RM_IHPSG13_1P_1024x32_c2_bm_bist"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def replace_tc_sram(source: str, wrapper: str) -> str:
    start = source.index("module tc_sram (")
    end = source.index("\nendmodule", start) + len("\nendmodule")
    if source.count("module tc_sram (") != 1:
        raise SystemExit("the prepared input must contain exactly one tc_sram module")
    return source[:start] + wrapper + source[end:]


def mark_macro(liberty: str, cell: str) -> str:
    needle = f"cell({cell}) {{"
    if needle not in liberty:
        raise SystemExit(f"macro cell {cell} not found in Liberty")
    if "is_macro_cell" not in liberty:
        liberty = liberty.replace(needle, needle + "\n  is_macro_cell : true;", 1)
    return liberty


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="prepared pulpissimo_soc.v")
    parser.add_argument("--wrapper", type=Path, required=True, help="tc_sram_ihp.v")
    parser.add_argument("--macro-lib", type=Path, required=True, help="official SRAM macro Liberty")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--out-lib", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--macro", default=MACRO)
    args = parser.parse_args()

    for path, name in ((args.input, "input"), (args.wrapper, "wrapper"), (args.macro_lib, "macro Liberty")):
        if not path.is_file():
            raise SystemExit(f"missing {name}: {path}")

    source = args.input.read_text()
    wrapper = args.wrapper.read_text()
    if f"module {args.macro} (" not in wrapper:
        raise SystemExit(f"wrapper does not declare blackbox module {args.macro}")
    result = replace_tc_sram(source, wrapper)
    if result.count("module tc_sram #(") != 1:
        raise SystemExit("expected exactly one IHP tc_sram adapter")

    liberty = mark_macro(args.macro_lib.read_text(), args.macro)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.out_lib.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result)
    args.out_lib.write_text(liberty)

    manifest = {
        "schema": "rusteda.pulpissimo-ihp-input.v1",
        "pdk": "ihp-sg13g2",
        "library": "ez130_8t",
        "macro": args.macro,
        "macro_capacity_words": 1024,
        "macro_data_width": 32,
        "changes": [
            "replace the behavioral tc_sram module with the IHP SRAM-macro adapter",
            "add is_macro_cell to the official macro Liberty copy (PDK untouched)",
        ],
        "source_input": {
            "path": str(args.input.resolve()),
            "bytes": args.input.stat().st_size,
            "sha256": digest(args.input),
        },
        "wrapper": {
            "path": str(args.wrapper.resolve()),
            "bytes": args.wrapper.stat().st_size,
            "sha256": digest(args.wrapper),
        },
        "macro_lib_source": {
            "path": str(args.macro_lib.resolve()),
            "bytes": args.macro_lib.stat().st_size,
            "sha256": digest(args.macro_lib),
        },
        "output": {
            "path": str(args.output.resolve()),
            "bytes": args.output.stat().st_size,
            "sha256": digest(args.output),
            "tc_sram_modules": len(re.findall(r"^module tc_sram #\(", result, re.M)),
        },
        "out_lib": {
            "path": str(args.out_lib.resolve()),
            "bytes": args.out_lib.stat().st_size,
            "sha256": digest(args.out_lib),
        },
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Locate SRAM macro views; this does not certify a physical flow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from .toolchain import digest


DEFAULT_MACROS = {
    "sky130": "sram22_256x32m4w8",
    "gf180": "gf180mcu_fd_ip_sram__sram512x8m8wm1",
    "ihp": "RM_IHPSG13_1P_1024x32_c2_bm_bist",
}


def macro_views(pdk, source, name, corner, root):
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("macro name must contain only letters, numbers and underscores")
    if pdk == "sky130" and source == "openram":
        if not (name.startswith("sram_") or name.startswith("sky130_sram_")):
            raise ValueError("openram selection expects a SKY130 OpenRAM macro")
        views = {kind: root / kind / f"{name}.{ext}" for kind, ext in
                 (("gds", "gds"), ("lef", "lef"), ("verilog", "v"), ("spice", "spice"))}
        views["liberty"] = root / "lib" / f"{name}_{corner.upper()}_1p8V_25C.lib"
    elif pdk == "sky130":
        if not name.startswith("sram22_"):
            raise ValueError("sky130 selection expects an SRAM22 macro")
        directory = root / name
        suffix = {"tt": "tt_025C_1v80", "ss": "ss_100C_1v60", "ff": "ff_n40C_1v95"}[corner]
        views = {"gds_gz": directory / f"{name}.gds.gz", "lef": directory / f"{name}.lef",
                 "spice": directory / f"{name}.spice", "verilog": directory / f"{name}.v",
                 "liberty": directory / f"{name}_{suffix}.lib"}
    elif pdk == "gf180":
        if not name.startswith("gf180mcu_fd_ip_sram__sram"):
            raise ValueError("gf180 selection expects a foundry SRAM macro")
        suffix = {"tt": "tt_025C_3v30", "ss": "ss_125C_3v00", "ff": "ff_n40C_3v60"}[corner]
        views = {kind: root / kind / f"{name}.{ext}" for kind, ext in
                 (("gds", "gds"), ("lef", "lef"), ("verilog", "v"), ("cdl", "cdl"))}
        views["liberty"] = root / "lib" / f"{name}__{suffix}.lib"
    else:
        if not re.fullmatch(r"RM_IHPSG13_[12]P_[A-Za-z0-9_]+", name):
            raise ValueError("ihp selection expects an IHP SG13G2 SRAM macro")
        suffix = {"tt": "typ_1p20V_25C", "ss": "slow_1p08V_125C", "ff": "fast_1p32V_m55C"}[corner]
        views = {kind: root / kind / f"{name}.{ext}" for kind, ext in
                 (("gds", "gds"), ("lef", "lef"), ("verilog", "v"), ("cdl", "cdl"))}
        views["liberty"] = root / "lib" / f"{name}_{suffix}.lib"
        port = "1P" if name.startswith("RM_IHPSG13_1P_") else "2P"
        variant = "_bm_bist" if name.endswith("_bm_bist") else ""
        ideal = "_ideal" if port == "2P" else ""
        views["verilog_support"] = root / "verilog" / f"RM_IHPSG13_{port}_core_behavioral{variant}{ideal}.v"
    return views


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rusteda sram", description=__doc__)
    parser.add_argument("pdk", choices=tuple(DEFAULT_MACROS))
    parser.add_argument("--macro", help="Macro name; defaults to a representative installed macro")
    parser.add_argument("--source", choices=("sram22", "openram"),
                        help="SKY130 macro library (default: sram22)")
    parser.add_argument("--corner", choices=("tt", "ff", "ss"), default="tt")
    parser.add_argument("--root", type=Path, help="Override the installed macro library directory")
    args = parser.parse_args(argv)
    if args.source and args.pdk != "sky130":
        parser.error("--source applies only to SKY130")
    source = args.source or ("sram22" if args.pdk == "sky130" else "foundry")
    pdk_root = Path.home() / "pdks"
    roots = {"sky130": (pdk_root / "sram-libraries/sram22-sky130" if source == "sram22"
                         else pdk_root / "sky130A/libs.ref/sky130_sram_macros"),
             "gf180": pdk_root / "gf180mcuD/libs.ref/gf180mcu_fd_ip_sram",
             "ihp": pdk_root / "ihp-open-pdk/ihp-sg13g2/libs.ref/sg13g2_sram"}
    name = args.macro or ("sram_1rw1r_32_256_8_sky130" if source == "openram"
                          else DEFAULT_MACROS[args.pdk])
    try:
        views = macro_views(args.pdk, source, name, args.corner, args.root or roots[args.pdk])
        result = {key: {"path": str(path.resolve()), "sha256": digest(path)}
                  for key, path in views.items()}
    except (OSError, ValueError) as error:
        print(f"SRAM collateral unavailable: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"pdk": args.pdk, "source": source, "macro": name, "corner": args.corner,
                      "integration_status": "unverified", "views": result}, indent=2))
    return 0

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Prepare the Ibex + SRAM benchmark inputs for one PDK profile (benchmark harness, not product logic).

Writes into a fresh workspace: rtl/design.v (sv2v of the pinned Ibex sources, the profile's wrapper and a
blackbox declaration of its SRAM macro), constraints.sdc and pnr.sdc (the IHP benchmark's constraints with the
profile's driving cell and port list), config.json (the LibreLane reference configuration with absolute paths)
and preparation.json (every input with its SHA-256).

usage: prepare.py --profile gf180mcu|sky130hd --workspace DIR --ibex-source DIR --pdk-root DIR
                  --gnu-time PATH [--sv2v sv2v]
"""
import argparse, hashlib, json, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IHP = HERE.parent / "ibex_sg13g2_ez130"
IBEX_REV = "38c070939183cc10940b66c8e9e04eeca6b65470"


def sha(p):
    with open(p, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def blackbox(profile, pdk):
    """A declaration of the macro's interface, cut from the PDK's own Verilog; no behaviour is substituted."""
    m = profile["macro"]
    text = (pdk / m["verilog"]).read_text()
    start = text.index("module " + m["name"])
    end = text.index(m["blackbox_end"], start)
    body = text[start:end]
    if "endmodule" not in body:
        body += "endmodule\n"
    return "// %s interface from the PDK; no implementation substituted.\n(* blackbox *)\n%s" % (m["name"], body)


def prepare_rtl(profile, pdk, ibex, w, sv2v, gnu_time):
    if subprocess.check_output(["git", "-C", str(ibex), "rev-parse", "HEAD"], text=True).strip() != IBEX_REV:
        raise SystemExit("Ibex source revision mismatch")
    if subprocess.check_output(["git", "-C", str(ibex), "status", "--porcelain"], text=True).strip():
        raise SystemExit("Ibex source must be clean")
    prim = ibex / "vendor/lowrisc_ip/ip/prim/rtl"
    generic = ibex / "vendor/lowrisc_ip/ip/prim_generic/rtl"
    dest = w / "rtl"
    dest.mkdir()
    sources = [prim / (n + ".sv") for n in ("prim_cipher_pkg", "prim_secded_pkg", "prim_mubi_pkg", "prim_util_pkg",
                                             "prim_ram_1p_pkg")]
    sources += [ibex / "rtl/ibex_pkg.sv"]
    for name in ("buf", "clock_gating", "flop"):
        bound = dest / f"prim_{name}.sv"
        bound.write_text((generic / f"prim_generic_{name}.sv").read_text()
                         .replace(f"module prim_generic_{name}", f"module prim_{name}"))
        sources.append(bound)
    sources.append(prim / "prim_lfsr.sv")
    exclude = {"ibex_pkg.sv", "ibex_tracer_pkg.sv", "ibex_tracer.sv", "ibex_core_tracing.sv", "ibex_top_tracing.sv"}
    sources += sorted(p for p in (ibex / "rtl").glob("*.sv") if p.name not in exclude)
    includes = [ibex / "rtl", prim, ibex / "vendor/lowrisc_ip/dv/sv/dv_utils"]
    bb = dest / "sram_bb.v"
    bb.write_text(blackbox(profile, pdk))
    bench = w / "ibex_sram_bench.sv"
    shutil.copyfile(HERE / profile["dir"] / "ibex_sram_bench.sv", bench)
    sources += [bb, bench]
    cmd = [sv2v, "-DSYNTHESIS", "--top=ibex_sram_bench", *[f"-I{p}" for p in includes], *map(str, sources),
           "+RTS", "-N2", "-M1536m", "-RTS"]
    (w / "rtl-command.json").write_text(json.dumps(cmd, indent=2))
    with (dest / "design.v").open("w") as out, (w / "sv2v.log").open("w") as err:
        subprocess.run([gnu_time, "-v", "-o", str(w / "sv2v-time.txt"), *cmd], stdout=out, stderr=err, check=True,
                       timeout=180)
    return sources


def prepare_sdc(profile, w):
    """The IHP benchmark's constraints, with this library's driving cell and without ports the wrapper lacks."""
    drv = profile["driving_cell"]
    out = []
    for line in (IHP / "constraints.sdc").read_text().splitlines():
        if any("{%s}" % p in line for p in profile["absent_ports"]):
            continue
        line = line.replace("-lib_cell BUFX4 -pin {Y}", "-lib_cell %s -pin {%s}" % (drv["cell"], drv["pin"]))
        out.append(line)
    text = "\n".join(out) + "\n"
    assert "BUFX4" not in text and drv["cell"] in text
    (w / "constraints.sdc").write_text(text)
    for f in ("pnr.sdc", "cppr_off.tcl"):
        shutil.copyfile(IHP / f, w / f)


def prepare_config(profile, pdk, w):
    text = (HERE / profile["dir"] / "config.json.in").read_text()
    text = text.replace("@WORKSPACE@", str(w)).replace("@PDK@", str(pdk))
    cfg = json.loads(text)
    for files in _paths(cfg):
        if not Path(files).exists():
            raise SystemExit(f"configured file is missing: {files}")
    (w / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    pdn = HERE / profile["dir"] / "pdn.tcl"
    if pdn.exists():
        shutil.copyfile(pdn, w / "pdn.tcl")


def _paths(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _paths(v)
    elif isinstance(node, list):
        for v in node:
            yield from _paths(v)
    elif isinstance(node, str) and node.startswith("/") and not node.endswith(("pnr.sdc", "constraints.sdc",
                                                                               "cppr_off.tcl", "pdn.tcl", "design.v")):
        yield node


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", required=True, choices=["gf180mcu", "sky130hd"])
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--ibex-source", type=Path, required=True)
    ap.add_argument("--pdk-root", type=Path, required=True, help="directory that holds the PDK variant directory")
    ap.add_argument("--gnu-time", required=True)
    ap.add_argument("--sv2v", default="sv2v")
    a = ap.parse_args()
    profile = json.loads((HERE / a.profile / "profile.json").read_text())
    profile["dir"] = a.profile
    w = a.workspace.resolve()
    if w.exists():
        raise SystemExit("workspace must not exist")
    w.mkdir(parents=True)
    pdk = (a.pdk_root / profile["pdk"]).resolve()
    sv2v = shutil.which(a.sv2v)
    if not sv2v or not Path(a.gnu_time).exists():
        raise SystemExit("required sv2v or GNU time executable is unavailable")
    sources = prepare_rtl(profile, pdk, a.ibex_source.resolve(), w, sv2v, a.gnu_time)
    prepare_sdc(profile, w)
    prepare_config(profile, pdk, w)
    record = {
        "schema": "rusteda.ibex-pdk-preparation.v1", "profile": a.profile, "pdk": profile["pdk"],
        "pdk_root": str(a.pdk_root.resolve()), "pdk_sources": (pdk / "SOURCES").read_text().strip()
        if (pdk / "SOURCES").exists() else None,
        "ibex_revision": IBEX_REV,
        "sv2v": {"path": sv2v, "version": subprocess.check_output([sv2v, "--version"], text=True).strip(),
                 "sha256": sha(Path(sv2v).resolve())},
        "inputs": [{"path": str(p), "sha256": sha(p)} for p in sources],
        "outputs": {n: sha(w / n) for n in ("rtl/design.v", "constraints.sdc", "pnr.sdc", "config.json")},
    }
    (w / "preparation.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record["outputs"], indent=2))


if __name__ == "__main__":
    sys.exit(main())

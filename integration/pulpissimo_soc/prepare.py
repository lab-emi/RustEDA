#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Prepare the pinned PULPissimo SoC synthesis input.

The upstream PULPissimo tree uses SystemVerilog interfaces, packages, Bender
targets and simulation-only assertion/fatal blocks.  This script produces one
flat Verilog file that both RustSys and an external reference can read:

1. ``bender checkout`` and ``bender script flist-plus`` for the ASIC target.
2. Copy the selected sources and include files, stripping ``translate_off``
   blocks (explicitly non-synthesizable).
3. Apply the two dependency-source adaptations recorded in the manifest.
4. Convert with sv2v and remove identity cast helper functions, fatal
   simulation blocks and modules that are unreachable for the selected
   Ibex, no-FPU configuration.

This is a benchmark input-preparation step, not an EDA synthesis pass.  It
never edits the upstream source tree; all changes happen in ``--workdir``.
The output is reproducible from the pinned source and the exact tool paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PINNED_COMMIT = "bfc3d9a1ef72443464509a22d948f0a6f5b65b4c"
PINNED_BENDER_LOCK = "8b236f7fa49dbc7910da8fc431419383e759ea431d231733f7477c80d5315da7"
BENDER_TARGETS = (
    "asic",
    "tech_cells_generic_include_tc_clk",
    "tech_cells_generic_include_tc_sram",
)
SOURCE_SUFFIXES = (".sv", ".v", ".svh", ".vh")
SKIP_SOURCES = re.compile(
    r"/(tb_fs_handler|dpi_models|tb_driver)\.(sv|v)$"
    r"|/pad_functional\.sv$"
)
FPU_MODULE = re.compile(r"^(fpnew_.*|cv32e40p_fp_wrapper)$")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(argv, cwd):
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        sys.stderr.write(result.stderr[-4000:])
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(map(str, argv[:8]))} ...")
    return result.stdout


def checkout_dependencies(source: Path, bender: Path) -> None:
    if not (source / ".bender" / "git" / "checkouts").is_dir():
        run([str(bender), "checkout"], source)
    # Bender 0.32.1 treats the trailing comma in this old package manifest as
    # part of the filename.  Fix the checkout copy, not the upstream file.
    for manifest in source.glob(".bender/git/checkouts/adv_dbg_if-*/Bender.yml"):
        text = manifest.read_text()
        fixed = text.replace("      - rtl/adbg_axi_biu.sv,\n", "      - rtl/adbg_axi_biu.sv\n")
        if fixed != text:
            manifest.write_text(fixed)


def generate_flist(source: Path, bender: Path, work: Path) -> Path:
    argv = [str(bender), "script", "flist-plus"]
    for target in BENDER_TARGETS:
        argv += ["-t", target]
    text = run(argv, source)
    path = work / "pulpissimo-asic.flist"
    path.write_text(text)
    return path


def strip_translate_off(text: str) -> tuple[str, int]:
    out = []
    inside = False
    blocks = 0
    for line in text.splitlines(keepends=True):
        low = line.lower()
        if "pragma translate_off" in low:
            inside = True
            blocks += 1
            out.append("\n" if line.endswith("\n") else "")
            continue
        if "pragma translate_on" in low:
            inside = False
            out.append("\n" if line.endswith("\n") else "")
            continue
        out.append("\n" if inside and line.endswith("\n") else ("" if inside else line))
    return "".join(out), blocks


def mapped_source(source: Path, prepared: Path, raw: str) -> Path:
    """Map a Bender path (native or container ``/work/pulpissimo``) to the prepared tree."""
    if raw.startswith("/work/pulpissimo/"):
        relative = Path(raw[len("/work/pulpissimo/"):])
    else:
        path = Path(raw)
        if not path.is_absolute():
            path = source / path
        try:
            relative = path.resolve().relative_to(source)
        except ValueError as error:
            raise SystemExit(f"Bender path escapes the PULPissimo source: {raw}") from error
    return prepared / relative


def prepare_sources(source: Path, flist: Path, work: Path, wrapper: Path) -> tuple[Path, list[Path], dict]:
    prepared = work / "src"
    if prepared.exists():
        shutil.rmtree(prepared)
    prepared.mkdir(parents=True)
    copied = 0
    for dirpath, dirnames, filenames in os.walk(source):
        if "/.git" in dirpath or "/.bender/git/db" in dirpath:
            dirnames[:] = []
            continue
        for name in filenames:
            if not name.endswith(SOURCE_SUFFIXES):
                continue
            src = Path(dirpath) / name
            dst = prepared / src.relative_to(source)
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8", errors="surrogateescape")
            text, _ = strip_translate_off(text)
            dst.write_text(text, encoding="utf-8", errors="surrogateescape")
            copied += 1

    # The SCM module is unreachable in this configuration, but sv2v resolves
    # bindings for every source module.  Drop the three port/parameter
    # connections that its current manifest does not declare.
    scm = next(prepared.glob(".bender/git/checkouts/scm-*/latch_scm/register_file_1w_64b_1r_32b.sv"), None)
    if scm is None:
        raise SystemExit("SCM register file not found in prepared sources")
    text = scm.read_text()
    text = text.replace(
        "              .ADDR_WIDTH ( WADDR_WIDTH ), // 5,\n",
        "              // .ADDR_WIDTH: absent from register_file_1r_1w_1row\n",
    )
    text = re.sub(r"^\s*\.ReadAddr\s*\(.*$", "              // .ReadAddr: absent from register_file_1r_1w_1row", text, flags=re.M)
    text = re.sub(r"^\s*\.WriteAddr\s*\(.*$", "              // .WriteAddr: absent from register_file_1r_1w_1row", text, flags=re.M)
    scm.write_text(text)

    wrapper_dst = prepared / "hw" / "pulpissimo_soc_wrapper.sv"
    shutil.copyfile(wrapper, wrapper_dst)

    selected = []
    include_dirs = set()
    for line in flist.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("+define+"):
            continue
        if line.startswith("+incdir+"):
            include_dirs.add(mapped_source(source, prepared, line[len("+incdir+"):]))
            continue
        path = mapped_source(source, prepared, line)
        if path.suffix in (".vhd", ".vhdl") or SKIP_SOURCES.search(str(path)):
            continue
        selected.append(path)
    clock_gate = prepared / ".bender/git/checkouts/cv32e40p-703290f15a6e8974/bhv/cv32e40p_sim_clock_gate.sv"
    if clock_gate.is_file() and clock_gate not in selected:
        selected.append(clock_gate)
    selected.append(wrapper_dst)
    # Include every selected file's directory and its parent, plus the Bender
    # +incdir set.  This reproduces the resolution used by the campaign.
    for path in selected:
        include_dirs.add(path.parent)
        include_dirs.add(path.parent.parent)
    return prepared, selected, {
        "copied_source_files": copied,
        "selected_source_files": len(selected),
        "include_dirs": len(include_dirs),
        "include_dir_list": sorted(str(p) for p in include_dirs),
    }


def convert(source: Path, prepared: Path, selected: list[Path], include_dirs: list[str], sv2v: Path, output: Path) -> None:
    argv = [
        str(sv2v),
        "-DSYNTHESIS",
        "-DTARGET_SYNTHESIS",
        *[f"-I{path}" for path in include_dirs],
        "--top=pulpissimo_soc_wrapper",
        *[str(path) for path in selected],
        "-w", str(output),
    ]
    run(argv, source)


def strip_identity_casts(text: str) -> tuple[str, int]:
    calls = 0
    while True:
        out = []
        pos = 0
        removed = 0
        while True:
            match = re.search(r"sv2v_cast_[A-Za-z0-9_]+(?=\()", text[pos:])
            if not match:
                out.append(text[pos:])
                break
            start = pos + match.start()
            out.append(text[pos:start])
            open_at = pos + match.end()
            depth = 0
            cursor = open_at
            while cursor < len(text):
                if text[cursor] == "(":
                    depth += 1
                elif text[cursor] == ")":
                    depth -= 1
                    if depth == 0:
                        cursor += 1
                        break
                cursor += 1
            if depth:
                raise SystemExit("unbalanced sv2v cast")
            out.append(text[open_at + 1:cursor - 1])
            removed += 1
            pos = cursor
        text = "".join(out)
        calls += removed
        if removed == 0:
            break
    # Remove the now-unused identity-cast function declarations.
    lines = text.splitlines(keepends=True)
    out = []
    index = 0
    functions = 0
    while index < len(lines):
        match = re.match(
            r"^\s*function\s+(?:automatic\s+)?(?:signed\s+)?\[[^\]]*\]\s+([A-Za-z0-9_$]+)",
            lines[index],
        )
        if match and "sv2v_cast_" in match.group(1):
            while index < len(lines) and not re.match(r"^\s*endfunction\b", lines[index]):
                index += 1
            index += 1
            functions += 1
            continue
        out.append(lines[index])
        index += 1
    return "".join(out), calls + functions


def remove_fatal_initials(text: str) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    out = []
    index = 0
    removed = 0
    while index < len(lines):
        if re.search(r"\binitial\s+begin\b", lines[index]):
            depth = 0
            end = index
            fatal = False
            while end < len(lines):
                for token in re.findall(r"\b(begin|end)\b", lines[end]):
                    depth += 1 if token == "begin" else -1
                if "$finish" in lines[end] or "$fatal" in lines[end]:
                    fatal = True
                if depth == 0 and end > index:
                    break
                end += 1
            if fatal:
                index = end + 1
                removed += 1
                continue
        out.append(lines[index])
        index += 1
    return "".join(out), removed


def fix_default_index(text: str) -> tuple[str, int]:
    pattern = re.compile(
        r"^(\s*)localparam \[.*?sv2v_uu_i_addr_decode_ext_default_idx_i_0 = 1'sb0;$",
        re.M,
    )
    text, count = pattern.subn(
        r"\1localparam [sv2v_uu_i_addr_decode_IdxWidth - 1:0] sv2v_uu_i_addr_decode_ext_default_idx_i_0 = 1'sb0;",
        text,
    )
    return text, count


def fix_sv2v_apb_interface(text: str) -> tuple[str, int]:
    """Repair the dropped APB interface object in the sv2v wrapper.

    sv2v inlines `soc_domain` into the wrapper but leaves module-scope
    references to the `APB.Master` interface instance.  Connect the inlined
    `pulp_soc` ports directly to the wrapper's plain APB ports and remove the
    stale interface assignments.  This is a converter repair, not a design
    change: the same signals cross the wrapper boundary.
    """
    outputs = ("paddr", "psel", "penable", "pwrite", "pwdata", "pstrb")
    inputs = ("prdata", "pready", "pslverr")
    count = 0
    for signal in outputs:
        old = f"pulpissimo_soc_wrapper.apb_chip_ctrl_master.{signal}"
        new = f"apb_{signal}_o"
        count += text.count(old)
        text = text.replace(old, new)
    for signal in inputs:
        old = f"pulpissimo_soc_wrapper.apb_chip_ctrl_master.{signal}"
        new = f"apb_{signal}_i"
        count += text.count(old)
        text = text.replace(old, new)
    # pprot has no wrapper port; leave the output unconnected.
    pattern = re.compile(
        r"^\s*\.apb_chip_ctrl_master_pprot_o\(pulpissimo_soc_wrapper\.apb_chip_ctrl_master\.pprot\),\n",
        re.M,
    )
    text, removed = pattern.subn("", text)
    count += removed
    # The trailing interface assignments duplicate the direct connections.
    pattern = re.compile(r"^\s*assign\s+apb_[a-z_]+\s*=\s*apb_chip_ctrl_master\.[a-z_]+;\n", re.M)
    text, removed = pattern.subn("", text)
    count += removed
    pattern = re.compile(r"^\s*assign\s+apb_chip_ctrl_master\.[a-z_]+\s*=\s*[^;]+;\n", re.M)
    text, removed = pattern.subn("", text)
    count += removed
    return text, count


def prune_fpu_modules(text: str) -> tuple[str, list[str]]:
    lines = text.splitlines(keepends=True)
    out = []
    removed = []
    index = 0
    while index < len(lines):
        match = re.match(r"^module\s+([A-Za-z0-9_$]+)", lines[index])
        if match:
            name = match.group(1)
            end = index
            while end < len(lines) and not re.match(r"^endmodule\b", lines[end]):
                end += 1
            if FPU_MODULE.match(name):
                removed.append(name)
                index = end + 1
                continue
            out.extend(lines[index:end + 1])
            index = end + 1
            continue
        out.append(lines[index])
        index += 1
    return "".join(out), removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="pinned PULPissimo checkout")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--bender", type=Path, required=True)
    parser.add_argument("--sv2v", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, default=Path(__file__).with_name("pulpissimo_soc_wrapper.sv"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    source = args.source.resolve()
    work = args.workdir.resolve()
    output = (args.output or (work / "pulpissimo_soc.v")).resolve()
    wrapper = args.wrapper.resolve()
    work.mkdir(parents=True, exist_ok=True)

    head = run(["git", "-C", str(source), "rev-parse", "HEAD"], source).strip()
    if head != PINNED_COMMIT:
        raise SystemExit(f"PULPissimo HEAD {head} is not the pinned {PINNED_COMMIT}")
    lock = source / "Bender.lock"
    if digest(lock) != PINNED_BENDER_LOCK:
        raise SystemExit(f"Bender.lock hash {digest(lock)} is not the pinned {PINNED_BENDER_LOCK}")

    checkout_dependencies(source, args.bender)
    flist = generate_flist(source, args.bender, work)
    prepared, selected, stats = prepare_sources(source, flist, work, wrapper)
    convert(source, prepared, selected, stats["include_dir_list"], args.sv2v, output)

    text = output.read_text(encoding="utf-8", errors="surrogateescape")
    text, casts = strip_identity_casts(text)
    text, fatals = remove_fatal_initials(text)
    text, default_indexes = fix_default_index(text)
    text, apb_repairs = fix_sv2v_apb_interface(text)
    text, pruned = prune_fpu_modules(text)
    # Make the embedded elaboration messages independent of the caller's
    # work directory; they are simulation strings, not logic.
    text = text.replace(str(prepared), "<PULPISSIMO_SRC>")
    if not text.endswith("\n"):
        text += "\n"
    output.write_text(text, encoding="utf-8", errors="surrogateescape")

    manifest = {
        "schema": "rusteda.pulpissimo-synthesis-input.v1",
        "source": {
            "name": "pulp-platform/pulpissimo",
            "commit": PINNED_COMMIT,
            "bender_lock_sha256": PINNED_BENDER_LOCK,
            "bender_targets": list(BENDER_TARGETS),
        },
        "tools": {
            "bender_version": "0.32.1",
            "sv2v_path": str(args.sv2v),
            "sv2v_sha256": digest(args.sv2v),
        },
        "configuration": {
            "top": "pulpissimo_soc_wrapper",
            "core": "ibex",
            "CORE_TYPE": 1,
            "USE_XPULP": 0,
            "USE_FPU": 0,
            "USE_ZFINX": 0,
            "USE_HWPE": 0,
            "SIM_STDOUT": 0,
            "excluded": "pads, pad multiplexing, clock generation and FLL",
            "defines": ["SYNTHESIS", "TARGET_SYNTHESIS"],
        },
        "preparation": {
            **{key: value for key, value in stats.items() if key != "include_dir_list"},
            "identity_cast_substitutions_or_functions": casts,
            "removed_fatal_initial_blocks": fatals,
            "default_index_repairs": default_indexes,
            "apb_interface_repairs": apb_repairs,
            "removed_unreachable_modules": pruned,
            "non_synthesizable_translate_off_blocks_stripped": True,
            "scm_binding_adaptation": "register_file_1w_64b_1r_32b.sv",
        },
        "output": {
            "path": str(output),
            "bytes": output.stat().st_size,
            "sha256": digest(output),
        },
        "flist_sha256": digest(flist),
        "wrapper_sha256": digest(wrapper),
    }
    manifest_path = work / "input.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({
        "status": "prepared",
        "output": str(output),
        "sha256": manifest["output"]["sha256"],
        "bytes": manifest["output"]["bytes"],
        "manifest": str(manifest_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

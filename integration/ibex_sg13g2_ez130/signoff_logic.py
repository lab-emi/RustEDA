#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Fresh original-RTL event CEC and original-vendor SRAM simulation.

Input schema: rusteda.logic-signoff-inputs.v1. Required paths: rtl,
synthesis_netlist, final_netlist, library, macro_bb, tb, relation_checker;
top/macro/macro_instances; vendor_verilog and vendor_include_dirs arrays; tools
{yosys,abc,checker,iverilog,vvp,time}. Optional physical_lef requires the four
original physical_libraries {std_tt,ram_tt,std_ss,ram_ss}. Optional limits and
expected_hashes bind resource/input policy. A saved cut, relation or pass is
never an input. Every run creates its gold cut from this raw RTL.

The proof is the existing independent Rust RTL interpreter's TWO-STATE
X/Z-to-zero event semantics: arbitrary original states and inputs, every
implementation initial state covered, no reset/case assumptions. It does not
claim four-state refinement, macro internals equivalence or physical timing.
The original supplier simulation separately exercises both real SRAMs.

Estimate: one worker/corner, <=100k gates, <=1M connected pin bits, serial
parser/model/ABC; expected <2 GiB. Hard caps: 16 MiB RTL/NL, 64 MiB JSON,
65536 states, 64M ANDs per proof side, bounded discovery work/conflicts. The
relation verifier has <=128 MiB variable scratch. Private <=4 GiB scope or
inherited owned producer cgroup; pure-CPU children also have an address cap.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import signoff_lvs as common


MIB = 1024**2
SCHEMA = "rusteda.logic-signoff-inputs.v1"
MACRO = "RM_IHPSG13_1P_1024x32_c2_bm_bist"
WIDTHS = dict(A_CLK=1, A_MEN=1, A_WEN=1, A_REN=1, A_ADDR=10, A_DIN=32,
              A_DLY=1, A_DOUT=32, A_BM=32, A_BIST_CLK=1, A_BIST_EN=1,
              A_BIST_MEN=1, A_BIST_WEN=1, A_BIST_REN=1, A_BIST_ADDR=10,
              A_BIST_DIN=32, A_BIST_BM=32)
RELATION_SOURCES = ("large_lec.py", "run.py", "transition.py", "lec.py", "equiv.py",
                    "rtl_semantics.py", "library_models.py", "lec_canonical.py", "designs.json")
SCOPE = dict(reference="original RTL independent interpreter and external ABC",
             rtl_unknown_literals="X/Z-as-zero; distinct from four-state equality",
             initial_states="all original RTL states arbitrary; all gate initial states covered",
             reset_assumptions=[], case_assumptions=[], input_assumptions=[],
             macro_scope="all functional SRAM interface bits, opaque across the memory boundary",
             wire_model="none", corner="logic", four_state_refinement="unverified")


def read_text(path, limit):
    common.identity(path, limit)
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("text input grew beyond byte budget")
    return raw.decode("utf-8")


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(manifest_path, output):
    config = common.read_json(manifest_path, 2 * MIB)
    required = {"schema", "top", "rtl", "synthesis_netlist", "final_netlist", "library", "macro_bb", "macro",
                "macro_instances", "tb", "vendor_verilog", "vendor_include_dirs", "relation_checker", "tools"}
    optional = {"limits", "expected_hashes", "physical_lef", "physical_libraries"}
    if not isinstance(config, dict) or not required <= set(config) or set(config) - required - optional:
        raise ValueError("expected complete logic-signoff manifest fields")
    if (config["schema"] != SCHEMA or config["top"] != "ibex_sram_bench" or config["macro"] != MACRO
            or config["macro_instances"] != ["u_imem", "u_dmem"]):
        raise ValueError("unsupported logical Ibex/SRAM interface profile")
    job = dict(config)
    extra_limits = dict(stage_seconds=180, cec_seconds=160, max_conflicts=2_000_000,
                        discovery_work=16_000_000_000, discovery_rounds=32)
    given = config.get("limits", {})
    if not isinstance(given, dict):
        raise ValueError("invalid logic limits")
    common_keys = {"memory_mib", "swap_mib", "deadline_seconds", "threads"}
    if set(given) - common_keys - set(extra_limits):
        raise ValueError("unknown logic resource limit")
    job["limits"] = common.limits({k: v for k, v in given.items() if k in common_keys})
    extra_limits.update({k: v for k, v in given.items() if k in extra_limits})
    bounds = dict(stage_seconds=(1, 600), cec_seconds=(1, 600), max_conflicts=(1, 5_000_000),
                  discovery_work=(1, 16_000_000_000), discovery_rounds=(1, 32))
    for key, (low, high) in bounds.items():
        if type(extra_limits[key]) is not int or not low <= extra_limits[key] <= high:
            raise ValueError(f"logic budget {key} must be {low}..{high}")
    job["logic_limits"] = extra_limits
    if job["limits"]["memory_mib"] < 3072:
        raise ValueError("logic admission estimates require at least 3072 MiB")
    sources = {"manifest": common.identity(manifest_path, 2 * MIB)}
    caps = dict(rtl=16 * MIB, synthesis_netlist=16 * MIB, final_netlist=16 * MIB,
                library=32 * MIB, macro_bb=16 * 1024, tb=MIB, relation_checker=MIB)
    for key, cap in caps.items():
        job[key] = common.path_value(config[key])
        sources[key] = common.identity(job[key], cap)
    if not isinstance(config["vendor_verilog"], list) or not 1 <= len(config["vendor_verilog"]) <= 16:
        raise ValueError("bounded original vendor Verilog source list required")
    job["vendor_verilog"] = [common.path_value(value) for value in config["vendor_verilog"]]
    total = 0
    for index, path in enumerate(job["vendor_verilog"]):
        record = common.identity(path, 32 * MIB)
        total += record["bytes"]
        sources[f"vendor_{index}"] = record
    if total > 64 * MIB:
        raise ValueError("total vendor Verilog byte budget exceeded")
    if (not isinstance(config["vendor_include_dirs"], list)
            or not 1 <= len(config["vendor_include_dirs"]) <= 8):
        raise ValueError("bounded vendor include-directory list required")
    job["vendor_include_dirs"] = [common.path_value(value) for value in config["vendor_include_dirs"]]
    if any(not Path(path).is_dir() for path in job["vendor_include_dirs"]):
        raise ValueError("vendor include location is not a directory")
    # Includes used by the original SRAM are fixed source inputs too. Reject
    # missing/unlisted or dynamic includes instead of leaving hidden dependencies.
    pending = list(job["vendor_verilog"]) + [job["tb"]]
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        if len(seen) >= 64:
            raise ValueError("vendor include-file count budget exceeded")
        seen.add(path)
        text = read_text(path, 32 * MIB)
        for include in re.findall(r'`include\s+"([^"\r\n]+)"', text):
            relative = Path(include)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe vendor include reference")
            choices = [Path(path).parent / relative] + [Path(p) / relative for p in job["vendor_include_dirs"]]
            matches = {str(p.resolve()) for p in choices if p.is_file()}
            if len(matches) != 1:
                raise ValueError("missing or ambiguous vendor include: " + include)
            child = matches.pop()
            if child not in seen:
                if len(pending) + len(seen) >= 64:
                    raise ValueError("vendor include expansion budget exceeded")
                pending.append(child)
            sources["include_" + hashlib.sha256(child.encode()).hexdigest()[:16]] = common.identity(child, 8 * MIB)
        if re.search(r'`include\s+(?!")\S', text):
            raise ValueError("dynamic vendor include is unsupported")
    if not isinstance(config["tools"], dict) or set(config["tools"]) != {"yosys", "abc", "checker", "iverilog", "vvp", "time"}:
        raise ValueError("explicit checker/Yosys/ABC/Icarus/GNUtime executables required")
    job["tools"] = {key: common.path_value(path) for key, path in config["tools"].items()}
    for key, path in job["tools"].items():
        if not os.access(path, os.X_OK):
            raise ValueError("missing executable: " + key)
        sources["tool_" + key] = common.identity(path)
    physical = config.get("physical_lef")
    if physical is not None:
        if not isinstance(config.get("physical_libraries"), dict) or set(config["physical_libraries"]) != {"std_tt", "ram_tt", "std_ss", "ram_ss"}:
            raise ValueError("PG-only contracts require original TT/SS standard/macro libraries")
        job["physical_lef"] = common.path_value(physical)
        sources["physical_lef"] = common.identity(job["physical_lef"], 32 * MIB)
        job["physical_libraries"] = {key: common.path_value(value) for key, value in config["physical_libraries"].items()}
        for key, path in job["physical_libraries"].items():
            sources["physical_" + key] = common.identity(path, 32 * MIB)
        if sources["physical_std_tt"]["sha256"] != sources["library"]["sha256"]:
            raise ValueError("physical standard TT library differs from logical library")
    elif config.get("physical_libraries") is not None:
        raise ValueError("physical libraries supplied without physical LEF")
    expected = config.get("expected_hashes", {})
    if not isinstance(expected, dict) or set(expected) - set(sources):
        raise ValueError("unknown expected logic input identity")
    for key, digest in expected.items():
        if sources[key]["sha256"] != digest:
            raise ValueError("logic input hash mismatch: " + key)
    helpers = output / "inputs"
    helpers.mkdir()
    job["helpers"] = {}
    names = ["signoff_logic.py", "signoff_lvs.py", "macro_contract.py"]
    if physical:
        names.append("signoff_timing.py")
    for name in names:
        source = Path(__file__).resolve().parent / name
        original = common.identity(source, 2 * MIB)
        target = helpers / name
        shutil.copyfile(source, target)
        copied = common.identity(target)
        if copied["sha256"] != original["sha256"]:
            raise ValueError("helper changed during snapshot")
        sources["source_" + name], sources["snapshot_" + name] = original, copied
        job["helpers"][name] = str(target)
    relations = Path(job["relation_checker"])
    if relations.name != "large_lec.py":
        raise ValueError("expected the tracked large_lec.py relation verifier")
    (helpers / "relation-sources").mkdir()
    for name in RELATION_SOURCES:
        source = relations.parent / name
        sources["relation_" + name] = common.identity(source, 2 * MIB)
        target = helpers / "relation-sources" / name
        shutil.copyfile(source, target)
        if common.identity(target)["sha256"] != sources["relation_" + name]["sha256"]:
            raise ValueError("relation helper source changed during snapshot")
        sources["relation_snapshot_" + name] = common.identity(target)
    # The existing relation library discovers its owning module checkout. It is
    # imported there, read-only, with its full helper source set pinned; the
    # immutable copies above retain exactly what was executed for audit.
    owner_root = next((p for p in relations.parents if (p / "Cargo.toml").is_file()
                       and (p / "verification/benchmarks/open-hardware/designs.json").is_file()), None)
    if owner_root is None:
        raise ValueError("relation verifier has no owning module checkout")
    sources["relation_module_manifest"] = common.identity(owner_root / "Cargo.toml", MIB)
    sources["python"] = common.identity(sys.executable)
    owned = common.inherited_owner()
    if owned:
        sources["worker_ownership"] = common.identity(owned["ownership_record"], 2 * MIB)
        sources["producer_manifest"] = owned["producer_manifest"]
    for spec in sources.values():
        path = Path(spec["path"])
        if path.is_relative_to(output) and not path.is_relative_to(helpers):
            raise ValueError("logic inputs must precede this fresh output workspace")
    info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(info["MemAvailable"].split()[0]) * 1024
    estimate = 2 * 1024**3 + 8 * max(sources[k]["bytes"] for k in ("rtl", "synthesis_netlist", "final_netlist"))
    if estimate > job["limits"]["memory_mib"] * MIB or available < estimate + 512 * MIB:
        raise ValueError("logic memory admission budget exceeded")
    job.update(output=str(output), sources=sources, python=str(Path(sys.executable).resolve()),
               inherited_owner=owned, estimated_peak_bytes=estimate, host_mem_available_bytes=available)
    return job


def masked_comments(text):
    # Preserve byte offsets of the prepared, ASCII Verilog. Strings cannot be
    # functional macro connections; masking them avoids matching comment text.
    pattern = r'//[^\n]*|/\*[\s\S]*?\*/|"(?:[^"\\]|\\.)*"'
    return re.sub(pattern, lambda match: "".join("\n" if c == "\n" else " " for c in match[0]), text)


def close_paren(text, start, limit=16384):
    if text[start] != "(":
        raise ValueError("expected macro/header parenthesis")
    depth = 1
    for pos in range(start + 1, min(len(text), start + limit)):
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if depth == 0:
                return pos
    raise ValueError("unbalanced/oversized macro connection list")


def cut_original(job):
    source = read_text(job["rtl"], 16 * MIB)
    bb = masked_comments(read_text(job["macro_bb"], 16 * 1024))
    if not source.isascii() or not bb.isascii():
        raise ValueError("prepared Ibex cut supports ASCII source only")
    ports = {}
    for match in re.finditer(r"\b(input|output)\s+(?:wire\s+)?(\[(\d+):(\d+)\]\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*;", bb):
        direction, bus, msb, lsb, name = match.groups()
        if name in ports:
            raise ValueError("duplicate SRAM interface port")
        width = abs(int(msb) - int(lsb)) + 1 if msb is not None else 1
        ports[name] = dict(direction=direction, bus=(bus or "").strip(), width=width)
    if ({name: port["width"] for name, port in ports.items()} != WIDTHS
            or any((port["direction"] == "output") != (name == "A_DOUT") for name, port in ports.items())
            or len(re.findall(r"\bmodule\b", bb)) != 1 or re.search(r"\b(assign|always|initial|function|task|inout)\b", bb)):
        raise ValueError("macro BB must be the complete original functional interface, without an implementation")
    view = masked_comments(source)
    definitions = list(re.finditer(r"\bmodule\s+" + re.escape(job["top"]) + r"\s*\(", view))
    if len(definitions) != 1:
        raise ValueError("expected one non-parameterized original top")
    begin = definitions[0].end() - 1
    end = close_paren(view, begin)
    if re.search(r"\b(input|output|inout|wire|reg)\b", view[begin:end]) or view[end + 1:].lstrip()[:1] != ";":
        raise ValueError("expected prepared non-ANSI top interface")
    body_start = end + 1 + len(view[end + 1:]) - len(view[end + 1:].lstrip()) + 1
    body = view[body_start:]
    if len(re.findall(r"\bendmodule\b", body)) != 1:
        raise ValueError("prepared original top must be the last module")
    matches = list(re.finditer(r"\b" + re.escape(job["macro"]) + r"\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", body))
    if len(matches) != 2 or {m[1] for m in matches} != set(job["macro_instances"]):
        raise ValueError("expected exactly the two original named SRAM instances")
    if len(re.findall(r"\b" + re.escape(job["macro"]) + r"\b", body)) != 2:
        raise ValueError("unsupported additional or parameterized macro use")
    headers, declarations, edits = [], [], []
    for match in matches:
        left = body_start + match.start()
        first = body_start + match.end() - 1
        last = close_paren(view, first)
        suffix = re.match(r"\s*;", view[last + 1:])
        if suffix is None:
            raise ValueError("macro instance is not terminated")
        right = last + 1 + suffix.end()
        connections, pos = {}, first + 1
        while pos < last:
            whitespace = re.match(r"[\s,]*", view[pos:last])
            pos += whitespace.end()
            if pos == last:
                break
            named = re.match(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", view[pos:last])
            if named is None or named[1] in connections:
                raise ValueError("complete, distinct named macro connections required")
            opening = pos + named.end() - 1
            closing = close_paren(view, opening)
            if closing >= last or not view[opening + 1:closing].strip():
                raise ValueError("empty or invalid original macro connection")
            connections[named[1]] = source[opening + 1:closing]
            pos = closing + 1
        if set(connections) != set(ports):
            raise ValueError("original SRAM instance interface is incomplete")
        assignments = []
        for name, port in ports.items():
            external = "\\" + match[1] + "." + name + " "
            if external in source:
                raise ValueError("exposed macro port collides with original source")
            headers.append(external)
            declarations.append(f"  {'output' if port['direction'] == 'input' else 'input'} wire {port['bus']} {external};")
            lhs, rhs = (external, connections[name]) if port["direction"] == "input" else (connections[name], external)
            assignments.append(f"  assign {lhs} = {rhs};")
        edits.append(dict(instance=match[1], start=left, end=right, original=source[left:right],
                          replacement="\n".join(assignments)))
    modified = source
    for edit in sorted(edits, key=lambda e: e["start"], reverse=True):
        modified = modified[:edit["start"]] + edit["replacement"] + modified[edit["end"]:]
    # The same two source spans are replaced; every other original byte is kept.
    modified = (modified[:end] + ",\n  " + ",\n  ".join(headers) + "\n);\n"
                + "\n".join(declarations) + "\n" + modified[body_start:])
    output = Path(job["output"])
    (output / "original-cut.v").write_text(modified)
    audit = dict(source=job["sources"]["rtl"], macro_interface=job["sources"]["macro_bb"], edits=edits,
                 result=common.identity(output / "original-cut.v"),
                 scope="only original macro boundaries cut; all other RTL byte ranges retained, no Yosys lowering before the independent RTL proof")
    common.save(output / "original-cut.json", audit)


def stage(job, directory, name, command, seconds=None):
    directory.mkdir(parents=True, exist_ok=True)
    argv = [job["tools"]["time"], "-v", "-o", str(directory / (name + ".time")), *map(str, command)]
    common.save(directory / (name + "-command.json"), argv)
    tick = time.monotonic()
    def child_limits():
        resource.setrlimit(resource.RLIMIT_AS, (job["limits"]["memory_mib"] * MIB,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * MIB,) * 2)
    with (directory / (name + ".log")).open("xb") as log:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   cwd=directory, start_new_session=True, preexec_fn=child_limits,
                                   env={**os.environ, "OMP_NUM_THREADS": str(job["limits"]["threads"]),
                                        "RAYON_NUM_THREADS": str(job["limits"]["threads"]), "RUST_TEST_THREADS": "2"})
        try:
            code = process.wait(timeout=seconds or job["logic_limits"]["stage_seconds"])
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            raise
    record = dict(name=name, exit_code=code, wall_seconds=time.monotonic() - tick,
                  log=common.identity(directory / (name + ".log")))
    job.setdefault("stages", []).append(record)
    if code:
        raise RuntimeError(f"{name} failed with exit {code}")
    return record


def yosys(job, directory, name, script):
    path = directory / (name + ".ys")
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    return stage(job, directory, name, [job["tools"]["yosys"], "-Q", "-T", "-s", path])


def physical_models(job, directory, netlist, raw):
    context = dict(files={"netlist": Path(netlist)}, root=directory, result={})
    if job.get("physical_lef"):
        module = module_from(job["helpers"]["signoff_timing.py"], "rusteda_pinned_timing")
        context["files"].update(physical_lef=Path(job["physical_lef"]),
                                **{k: Path(v) for k, v in job["physical_libraries"].items()})
        module.Run.physical_models(SimpleNamespace(**context))
    else:
        (directory / "physical-only.v").write_text("")
        context["result"]["physical_only_models"] = dict(masters=[], basis="no physical abstract input", functional_models_replaced=False)
    names = set(context["result"]["physical_only_models"]["masters"])
    for cell in raw["modules"][job["top"]]["cells"].values():
        if cell["type"] in names and (cell.get("connections") or cell.get("parameters")):
            raise ValueError("nonempty physical-only instance: " + cell["type"])
    common.save(directory / "physical-models.json", context["result"])
    return directory / "physical-only.v"


def structural(job, directory, netlist):
    q = common.quoted_yosys
    yosys(job, directory, "structural", f"read_verilog {q(netlist)}\nhierarchy -top {job['top']}\nwrite_json {q(directory / 'structural.json')}\n")
    raw = common.read_json(directory / "structural.json", 64 * MIB)
    if set(raw.get("modules", {})) != {job["top"]}:
        raise ValueError("expected a flat mapped logical top")
    module = raw["modules"][job["top"]]
    cells = module.get("cells", {})
    if module.get("processes") or module.get("memories") or not 1 <= len(cells) <= 100000:
        raise ValueError("mapped structural cell budget/scope exceeded")
    pins = 0
    for cell in cells.values():
        pins += sum(len(bits) for bits in cell["connections"].values())
        if pins > 1_000_000:
            raise ValueError("mapped connected-pin budget exceeded")
    return raw


def verify_manifest(module, directory, manifest):
    n, nr = manifest["gate_state_elements"], manifest["rtl_state_bits"]
    kinds, sides = manifest["obligation_kinds"], manifest["sides"]
    if (type(n) is not int or not 1 <= n <= 65536 or nr != 2023
            or manifest["output_bits"] != 384 or len(sides) != 2
            or manifest.get("all_rtl_inputs_and_states_quantified") is not True
            or manifest.get("all_gate_initial_states_covered") is not True
            or manifest.get("reset_assumptions") != [] or manifest.get("case_assumptions") != []
            or manifest.get("initial_state_choices") != [] or manifest["state_pairs"] != n
            or kinds.get("output") != 384 or kinds.get("clock", 0) + kinds.get("enable", 0) != n
            or kinds.get("async-clear") != n or kinds.get("async-preset") != n
            or kinds.get("next-state", 0) + kinds.get("data", 0) != n
            or manifest.get("rtl_unknown_literals") != "X/Z-as-zero; distinct from four-state equality"):
        raise ValueError("complete arbitrary-state/event proof obligations are missing")
    count = sum(kinds.values())
    if count > 1_000_000 or any(type(v) is not int or v < 0 for v in kinds.values()):
        raise ValueError("obligation count/type budget exceeded")
    if (sides[0]["inputs"] != sides[1]["inputs"]
            or not 1 <= sides[0]["inputs"] <= 100000
            or sides[0]["inputs"] != manifest["primary_input_bits"] + nr
            or any(side["outputs"] != count for side in sides)):
        raise ValueError("CEC quantified interface differs from obligation inventory")
    relation = module.verify_relation(directory, manifest)
    hashes = {}
    for name, side in zip(("gold.aig", "gate.aig"), sides, strict=True):
        path = directory / name
        record = common.identity(path, 256 * MIB)
        with path.open("rb") as stream:
            header = stream.readline(256).decode().strip().split()
        if len(header) != 6 or header[0] != "aig":
            raise ValueError("invalid binary AIGER header")
        maximum, inputs, latches, outputs, ands = map(int, header[1:])
        if (latches != 0 or maximum != inputs + ands or inputs != side["inputs"]
                or outputs != count or outputs != side["outputs"] or ands != side["and_nodes"]
                or not 0 <= ands <= 64_000_000 or record["bytes"] != side["bytes"]):
            raise ValueError("AIGER interface or bounded coverage differs from manifest")
        hashes[name] = record
    return dict(relation=relation, aiger=hashes, obligation_count=count)


def prove(job, label, source, gold_interface, relation_module):
    root = Path(job["output"])
    directory = root / label
    directory.mkdir()
    raw = structural(job, directory, source)
    physical = physical_models(job, directory, source, raw)
    del raw
    q = common.quoted_yosys
    physical_metadata = common.read_json(directory / "physical-models.json", MIB)
    physical_names = physical_metadata["physical_only_models"]["masters"]
    # Expand only the explicit, signal-disconnected zero-port contracts. They
    # have no Boolean ports or state; no real macro or vendor function is cut.
    physical_flatten = ("flatten " + " ".join(job["top"] + "/t:" + name for name in physical_names) + "\n") if physical_names else ""
    script = (f"read_verilog -lib {q(job['macro_bb'])}\n"
              f"read_liberty -ignore_miss_func -ignore_miss_data_latch {q(job['library'])}\n"
              f"read_verilog -noblackbox {q(physical)}\nread_verilog {q(source)}\n"
              f"hierarchy -check -top {job['top']}\nwrite_json {q(directory / 'raw.json')}\n"
              + physical_flatten
              +
              f"expose -evert {job['top']}/t:{job['macro']}\n"
              f"flatten {job['top']}/t:TIEHI {job['top']}/t:TIELO\nselect {job['top']}\n"
              f"write_verilog -selected -noattr -norename {q(directory / 'gate.v')}\n"
              f"write_json {q(directory / 'gate.json')}\n")
    yosys(job, directory, "cut", script)
    stage(job, directory, "macros", [job["python"], job["helpers"]["macro_contract.py"], directory / "raw.json"])
    gate = common.read_json(directory / "gate.json", 64 * MIB)["modules"][job["top"]]["ports"]
    interface = lambda ports: {name: (port["direction"], len(port["bits"])) for name, port in ports.items()}
    if interface(gate) != interface(gold_interface):
        raise ValueError("fresh original RTL and this gate netlist expose different complete interfaces")
    config = dict(library=job["library"], design=dict(top=job["top"], files=[str(root / "original-cut.v")]),
                  netlist=str(directory / "gate.v"), export_obligations=str(directory / "obligations"),
                  export_discovery_work=job["logic_limits"]["discovery_work"],
                  export_discovery_rounds=job["logic_limits"]["discovery_rounds"])
    common.save(directory / "checker-config.json", config)
    stage(job, directory, "export", [job["tools"]["checker"], directory / "checker-config.json", directory / "checker-result.json"])
    manifest = common.read_json(directory / "obligations/manifest.json", 2 * MIB)
    verified = verify_manifest(relation_module, directory / "obligations", manifest)
    abc = f"&cec -T {job['logic_limits']['cec_seconds']} -C {job['logic_limits']['max_conflicts']} {q(directory / 'obligations/gold.aig')} {q(directory / 'obligations/gate.aig')};"
    stage(job, directory, "abc", [job["tools"]["abc"], "-c", abc], job["logic_limits"]["cec_seconds"] + 10)
    output = read_text(directory / "abc.log", 16 * MIB)
    if not re.search(r"^Networks are equivalent\.", output, re.M) or "NOT EQUIVALENT" in output:
        raise ValueError("external ABC did not prove every original event/output obligation")
    if verified != verify_manifest(relation_module, directory / "obligations", manifest):
        raise ValueError("obligation artifacts changed during external proof")
    result = dict(status="proven", complete=True, original_rtl_sha256=job["sources"]["rtl"]["sha256"],
                  rtl_sha256=job["sources"]["rtl"]["sha256"], netlist_sha256=common.identity(source)["sha256"],
                  scope=SCOPE, manifest=manifest, verified=verified,
                  fresh_original_cut=common.identity(root / "original-cut.v"),
                  abc_verdict="Networks are equivalent.", physical_models=common.read_json(directory / "physical-models.json", MIB),
                  stages=[row for row in job["stages"] if Path(row["log"]["path"]).parent == directory])
    common.save(root / (label + "_equivalence.json"), result)
    return result


def simulate(job):
    root = Path(job["output"])
    directory = root / "vendor-simulation"
    directory.mkdir()
    # Reuse only the qualified PG-only declarations generated from this final
    # netlist and original LEF; the actual supplier SRAM models are compiled.
    physical = root / "final/physical-only.v"
    command = [job["tools"]["iverilog"], "-g2012", "-s", "tb"]
    for path in job["vendor_include_dirs"]:
        command.extend(["-I", path])
    command.extend(["-o", str(directory / "sim"), job["tb"], job["final_netlist"], str(physical), *job["vendor_verilog"]])
    stage(job, directory, "compile", command, 60)
    stage(job, directory, "simulate", [job["tools"]["vvp"], directory / "sim"], 60)
    text = read_text(directory / "simulate.log", 16 * MIB)
    if not re.search(r"^PASS: real SRAM", text, re.M):
        raise ValueError("original supplier memory test did not report complete success")
    result = dict(status="passed", complete=True, final_netlist_sha256=job["sources"]["final_netlist"]["sha256"],
                  netlist_sha256=job["sources"]["final_netlist"]["sha256"], original_rtl_sha256=job["sources"]["rtl"]["sha256"],
                  scope="original EZ130 + original IHP SRAM vendor models; two banks, byte masks, boundary addresses and Ibex stores 42/43; bounded 200us",
                  simulator=job["sources"]["tool_iverilog"], runtime=job["sources"]["tool_vvp"],
                  testbench=job["sources"]["tb"], log=common.identity(directory / "simulate.log"))
    common.save(root / "simulation.json", result)
    return result


def execute(job):
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * MIB,) * 2)
    tick = time.monotonic()
    root = Path(job["output"])
    report = dict(status="unverified", complete=False, error=None, stages=[])
    job["stages"] = report["stages"]
    try:
        if common.source_changes(job["sources"]):
            raise ValueError("logic source changed before worker start")
        cut_original(job)
        q = common.quoted_yosys
        # This independent external parse is used for interface directions and
        # widths only. The proof gold remains the unlowered original-cut.v.
        yosys(job, root, "gold-interface", f"read_verilog -sv {q(root / 'original-cut.v')}\nhierarchy -check -top {job['top']}\nproc\nwrite_json {q(root / 'gold-interface.json')}\n")
        gold = common.read_json(root / "gold-interface.json", 64 * MIB)["modules"][job["top"]]["ports"]
        sys.path.insert(0, str(Path(job["relation_checker"]).parent))
        relation = module_from(job["relation_checker"], "rusteda_pinned_large_lec")
        failures = {}
        for label, source in (("synthesis", job["synthesis_netlist"]), ("final", job["final_netlist"])):
            try:
                prove(job, label, source, gold, relation)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, MemoryError, subprocess.SubprocessError) as error:
                failures[label] = str(error)
        # A failed/unknown CEC is never promoted, but it must not prevent an
        # independent supplier-model experiment from retaining its own result.
        try:
            simulate(job)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, MemoryError, subprocess.SubprocessError) as error:
            failures["simulation"] = str(error)
        report.update(status="passed" if not failures else "unverified", complete=not failures,
                      failures=failures, error=None if not failures else "; ".join(k + ": " + v for k, v in failures.items()))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, StopIteration, MemoryError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    report.update(wall_seconds=time.monotonic() - tick, cgroup=common.current_cgroup(),
                  cgroup_memory=common.cgroup_sample(common.current_cgroup()))
    common.save(root / "worker-result.json", report)
    return 0 if report["status"] == "passed" else 1


def summarize(job, guard):
    root = Path(job["output"])
    report = dict(schema="rusteda.logic-signoff-result.v1", status="unverified", complete=False, scope=SCOPE,
                  top=job["top"], original_rtl_sha256=job["sources"]["rtl"]["sha256"],
                  synthesis_netlist_sha256=job["sources"]["synthesis_netlist"]["sha256"],
                  final_netlist_sha256=job["sources"]["final_netlist"]["sha256"], sources=job["sources"], resources=guard,
                  estimated_peak_bytes=job["estimated_peak_bytes"], host_mem_available_bytes=job["host_mem_available_bytes"])
    changes = common.source_changes(job["sources"])
    report.update(input_hashes_unchanged=not changes, changed_inputs=changes)
    worker = common.read_json(root / "worker-result.json", 16 * MIB) if (root / "worker-result.json").is_file() else None
    report["worker"] = worker
    if worker:
        peak = worker.get("cgroup_memory", {}).get("memory.peak")
        if peak is not None:
            guard["cgroup_memory_peak_bytes"] = max(guard.get("cgroup_memory_peak_bytes") or 0, peak)
    integrity = (worker and not changes and not guard["deadline_reached"]
                and not guard["interrupted"] and not guard["detached_launcher"] and guard["cleanup_verified"]
                and guard["launcher_exit"] == (0 if worker["status"] == "passed" else 1))
    complete = bool(integrity and worker.get("complete") is True)
    names = {"synthesis_equivalence.json": "proven", "final_equivalence.json": "proven", "simulation.json": "passed"}
    artifacts = {}
    for name, expected in names.items():
        path = root / name
        if not path.is_file():
            common.save(path, dict(status="unverified", complete=False, original_rtl_sha256=report["original_rtl_sha256"],
                                   netlist_sha256=report["synthesis_netlist_sha256" if name.startswith("synthesis") else "final_netlist_sha256"],
                                   error=worker.get("error") if worker else "worker did not complete", scope=SCOPE))
        elif not integrity:
            # A changed input or killed worker invalidates its finished results.
            # Independent completed stages of a normally ended failed run keep
            # their own verdict; the aggregate still requires all three gates.
            raw = common.read_json(path, 16 * MIB)
            path.rename(path.with_name(path.stem + "-stage-result.json"))
            common.save(path, dict(status="unverified", complete=False, original_rtl_sha256=report["original_rtl_sha256"],
                                   netlist_sha256=raw.get("netlist_sha256"), error="whole logic run incomplete or inputs changed", scope=SCOPE))
        doc = common.read_json(path, 16 * MIB)
        complete = bool(complete and doc.get("status") == expected and doc.get("complete") is True)
        artifacts[name] = common.identity(path)
    report.update(status="passed" if complete else "unverified", complete=bool(complete), artifacts=artifacts)
    common.save(root / "summary.json", report)
    (root / "HEAD").write_text("rev1\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--base-rev")
    parser.add_argument("--_worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._worker:
        return execute(common.read_json(args._worker, 4 * MIB))
    if args.inputs is None or args.workspace is None or args.base_rev is None:
        parser.error("--inputs, --workspace and --base-rev required")
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.base_rev != "empty" or any(p.name != "workspace.lock" for p in output.iterdir()):
            raise ValueError("REVISION_CONFLICT: logic signoff requires a new empty workspace")
        try:
            job = prepare(args.inputs.resolve(strict=True), output)
            common.save(output / "job.json", job)
            command = [job["python"], job["helpers"]["signoff_logic.py"], "--_worker", str(output / "job.json")]
            guard = common.guarded(job, command)
            result = summarize(job, guard)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            result = dict(schema="rusteda.logic-signoff-result.v1", status="unverified", complete=False, error=str(error))
            if not (output / "summary.json").exists():
                common.save(output / "summary.json", result)
            (output / "HEAD").write_text("rev1\n")
        print(json.dumps({k: v for k, v in result.items() if k not in ("sources", "worker")}))
        return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps(dict(status="unverified", complete=False, error=str(error))))
        raise SystemExit(2) from error

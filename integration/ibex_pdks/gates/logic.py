#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Equivalence of the synthesis netlist and of the final netlist with the RTL, macros as black boxes.

Input schema `rusteda.pdk-gate.logic.v1`:
  top                 design name
  rtl                 prepared RTL (the top is its last module, non-ANSI header), pinned
  synthesis_netlist, final_netlist   pinned flat gate netlists (without supply pins)
  library             standard-cell TT Liberty, pinned
  macro_bb            black-box declaration of the macro, pinned; macro; macro_instances
  tie_cells           constant cells of the library (flattened so that tied macro pins are constants)
  physical            {physical_lef, std_tt, ram_tt, std_ss, ram_ss, policy}: pinned inputs of the
                      supply-only cell declarations (the pinned policy of `signoff_timing.py`)
  relation_checker    RustSys `large_lec.py` (its helper sources are pinned at run time)
  tools               {yosys, abc, checker, gnu_time, python}
  limits              stage_seconds, cec_seconds, max_conflicts, discovery_work, discovery_rounds, memory_mib
  expected            optional {rtl_state_bits}: a count the proof must reproduce

Method, as in the IHP recipe's `signoff_logic.py`, with the macro interface read from data: the
macro instances are cut out of the RTL text (every functional macro pin becomes a port of the
top, all other RTL bytes are kept), Yosys exposes the same pins of the gate netlist, RustSys'
independent RTL interpreter exports the proof obligations of both sides (every output, and
clock, enable, asynchronous control and next state of every state element, for arbitrary
states and inputs, X and Z read as zero), the state relation is verified, and external ABC
`&cec` must prove the two networks equivalent. The macro interior is opaque. No saved cut,
relation or verdict is an input. Status: `passed` (both proofs), `failed` (ABC found a
difference) or `unverified` (no proof was reached, which is not a counterexample).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCHEMA = "rusteda.pdk-gate.logic.v1"
MIB = common.MIB
RELATION_SOURCES = ("large_lec.py", "run.py", "transition.py", "lec.py", "equiv.py", "rtl_semantics.py",
                    "library_models.py", "lec_canonical.py", "designs.json")
SCOPE = dict(reference="original RTL independent interpreter and external ABC",
             rtl_unknown_literals="X/Z-as-zero; distinct from four-state equality",
             initial_states="all original RTL states arbitrary; all gate initial states covered",
             reset_assumptions=[], case_assumptions=[], input_assumptions=[],
             macro_scope="all functional macro interface bits, opaque across the memory boundary",
             wire_model="none", corner="logic", four_state_refinement="unverified")


def quoted(path) -> str:
    text = str(path)
    common.require('"' not in text and "\\" not in text and "\n" not in text, "YOSYS_PATH")
    return '"' + text + '"'


def masked_comments(text) -> str:
    pattern = r'//[^\n]*|/\*[\s\S]*?\*/|"(?:[^"\\]|\\.)*"'
    return re.sub(pattern, lambda match: "".join("\n" if c == "\n" else " " for c in match[0]), text)


def span(width) -> str:
    """Range of a port declaration; a one-bit port is a scalar, as Yosys writes it."""
    return f"[{width - 1}:0] " if width > 1 else ""


def close_paren(text, start, limit=65536) -> int:
    common.require(text[start] == "(", "EXPECTED_PARENTHESIS")
    depth = 1
    for position in range(start + 1, min(len(text), start + limit)):
        if text[position] == "(":
            depth += 1
        elif text[position] == ")":
            depth -= 1
            if depth == 0:
                return position
    raise common.GateError("UNBALANCED_CONNECTION_LIST")


class Job:
    def __init__(self, config, output):
        self.c, self.root, self.stages = config, output, []
        self.tools = {name: str(common.verify(record, name)) for name, record in config["tools"].items()}
        self.limits = config["limits"]
        self.top, self.macro = config["top"], config["macro"]

    def stage(self, directory, name, command, seconds=None):
        directory.mkdir(parents=True, exist_ok=True)
        argv = [self.tools["gnu_time"], "-v", "-o", str(directory / (name + ".time")), *map(str, command)]
        common.save_json(directory / (name + "-command.json"), argv)
        memory = int(self.limits["memory_mib"]) * MIB

        def child_limits():
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * MIB,) * 2)

        tick = time.monotonic()
        with (directory / (name + ".log")).open("xb") as log:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=directory,
                                       start_new_session=True, preexec_fn=child_limits,
                                       env={**os.environ, "OMP_NUM_THREADS": "2", "RAYON_NUM_THREADS": "2"})
            try:
                code = process.wait(timeout=seconds or int(self.limits["stage_seconds"]))
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                raise
        text = (directory / (name + ".time")).read_text() if (directory / (name + ".time")).is_file() else ""
        rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
        self.stages.append(dict(stage=directory.name + "/" + name, exit_code=code,
                                wall_seconds=round(time.monotonic() - tick, 3), peak_rss_kib=int(rss[1]) if rss else None))
        common.require(code == 0, f"STAGE_FAILED: {directory.name}/{name} exit {code}")

    def yosys(self, directory, name, script):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + ".ys")
        path.write_text(script)
        self.stage(directory, name, [self.tools["yosys"], "-Q", "-T", "-s", path])

    def macro_interface(self):
        """Functional ports of the macro black box, read by Yosys: {name: (direction, width)}; supply ports apart."""
        self.yosys(self.root, "macro-interface", f"read_verilog -lib {quoted(self.c['macro_bb']['path'])}\n"
                   f"write_json {quoted(self.root / 'macro-interface.json')}\n")
        modules = common.read_json(self.root / "macro-interface.json")["modules"]
        common.require(set(modules) == {self.macro}, "MACRO_BB_MODULE")
        ports, supplies = {}, []
        for name, port in modules[self.macro]["ports"].items():
            common.require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is not None and not port.get("upto")
                           and not port.get("offset"), "MACRO_PORT_SHAPE: " + name)
            if port["direction"] == "inout":
                supplies.append(name)
            else:
                ports[name] = (port["direction"], len(port["bits"]))
        common.require(any(d == "output" for d, _ in ports.values()), "MACRO_WITHOUT_OUTPUT")
        black = self.root / "macro-functional-bb.v"
        black.write_text("(* blackbox *)\nmodule " + self.macro + " (" + ", ".join(ports) + ");\n" + "".join(
            f"  {direction} {span(width)}{name};\n" for name, (direction, width) in ports.items()) + "endmodule\n")
        return ports, supplies, black

    def cut_original(self, ports, target, exposed_unused):
        """Replace the macro instances of the RTL top by ports; returns the audit record."""
        source = Path(self.c["rtl"]["path"]).read_text()
        common.require(source.isascii(), "RTL_ASCII")
        view = masked_comments(source)
        definitions = list(re.finditer(r"\bmodule\s+" + re.escape(self.top) + r"\s*\(", view))
        common.require(len(definitions) == 1, "RTL_TOP_DEFINITION")
        begin = definitions[0].end() - 1
        end = close_paren(view, begin)
        common.require(not re.search(r"\b(input|output|inout|wire|reg)\b", view[begin:end])
                       and view[end + 1:].lstrip()[:1] == ";", "RTL_TOP_NON_ANSI_HEADER_REQUIRED")
        body_start = end + 1 + len(view[end + 1:]) - len(view[end + 1:].lstrip()) + 1
        body = view[body_start:]
        common.require(len(re.findall(r"\bendmodule\b", body)) == 1, "RTL_TOP_MUST_BE_LAST_MODULE")
        matches = list(re.finditer(r"\b" + re.escape(self.macro) + r"\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", body))
        instances = self.c["macro_instances"]
        common.require(sorted(m[1] for m in matches) == sorted(instances)
                       and len(re.findall(r"\b" + re.escape(self.macro) + r"\b", body)) == len(instances),
                       "RTL_MACRO_INSTANCES")
        headers, declarations, edits = [], [], []
        for match in matches:
            first = body_start + match.end() - 1
            last = close_paren(view, first)
            suffix = re.match(r"\s*;", view[last + 1:])
            common.require(suffix is not None, "RTL_MACRO_INSTANCE_END")
            connections, position = {}, first + 1
            while position < last:
                position += re.match(r"[\s,]*", view[position:last]).end()
                if position == last:
                    break
                named = re.match(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", view[position:last])
                common.require(named is not None and named[1] not in connections, "RTL_NAMED_CONNECTIONS_REQUIRED")
                opening = position + named.end() - 1
                closing = close_paren(view, opening)
                common.require(closing < last, "RTL_CONNECTION")
                connections[named[1]] = source[opening + 1:closing].strip()
                position = closing + 1
            common.require(set(connections) <= set(ports), "RTL_MACRO_UNKNOWN_OR_SUPPLY_PIN")
            assignments = []
            for name, (direction, width) in ports.items():
                external = "\\" + match[1] + "." + name + " "
                common.require(external not in source, "EXPOSED_PORT_COLLIDES")
                expression = connections.get(name, "")
                if direction == "input":
                    common.require(bool(expression), f"RTL_MACRO_INPUT_UNCONNECTED: {match[1]}.{name}")
                    headers.append(external)
                    declarations.append(f"  output wire {span(width)}{external};")
                    assignments.append(f"  assign {external} = {expression};")
                elif expression:
                    headers.append(external)
                    declarations.append(f"  input wire {span(width)}{external};")
                    assignments.append(f"  assign {expression} = {external};")
                elif (match[1], name) in exposed_unused:
                    headers.append(external)
                    declarations.append(f"  input wire {span(width)}{external};")
            left, right = body_start + match.start(), last + 1 + suffix.end()
            edits.append(dict(instance=match[1], start=left, end=right, original=source[left:right],
                              replacement="\n".join(assignments)))
        modified = source
        for edit in sorted(edits, key=lambda e: e["start"], reverse=True):
            modified = modified[:edit["start"]] + edit["replacement"] + modified[edit["end"]:]
        modified = (modified[:end] + ",\n  " + ",\n  ".join(headers) + "\n);\n" + "\n".join(declarations) + "\n"
                    + modified[body_start:])
        target.write_text(modified)
        return dict(source=self.c["rtl"], edits=edits, result=common.identity(target),
                    scope="only the macro boundaries are cut; every other RTL byte is kept")

    def physical_models(self, directory, netlist):
        physical = self.c["physical"]
        spec = importlib.util.spec_from_file_location("rusteda_pinned_physical_policy", physical["policy"]["path"])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        context = SimpleNamespace(files=dict(netlist=Path(netlist), **{k: Path(physical[k]["path"]) for k in
                                                                        ("physical_lef", "std_tt", "ram_tt", "std_ss", "ram_ss")}),
                                  root=directory, result={})
        module.Run.physical_models(context)
        common.save_json(directory / "physical-models.json", context.result)
        return context.result["physical_only_models"]["masters"]

    def verify_manifest(self, relation, directory, manifest, output_bits):
        n, nr = manifest["gate_state_elements"], manifest["rtl_state_bits"]
        kinds, sides = manifest["obligation_kinds"], manifest["sides"]
        common.require(type(n) is int and 1 <= n <= 65536 and type(nr) is int and nr >= 1
                       and manifest["output_bits"] == output_bits and len(sides) == 2
                       and manifest.get("all_rtl_inputs_and_states_quantified") is True
                       and manifest.get("all_gate_initial_states_covered") is True
                       and manifest.get("reset_assumptions") == [] and manifest.get("case_assumptions") == []
                       and manifest.get("initial_state_choices") == [] and manifest["state_pairs"] == n
                       and kinds.get("output") == output_bits and kinds.get("clock", 0) + kinds.get("enable", 0) == n
                       and kinds.get("async-clear") == n and kinds.get("async-preset") == n
                       and kinds.get("next-state", 0) + kinds.get("data", 0) == n
                       and manifest.get("rtl_unknown_literals") == SCOPE["rtl_unknown_literals"],
                       "PROOF_OBLIGATIONS_INCOMPLETE")
        expected = self.c.get("expected", {}).get("rtl_state_bits")
        common.require(expected is None or nr == expected, f"RTL_STATE_BITS: {nr} != {expected}")
        count = sum(kinds.values())
        common.require(count <= 1_000_000 and all(type(v) is int and v >= 0 for v in kinds.values()), "OBLIGATION_BUDGET")
        common.require(sides[0]["inputs"] == sides[1]["inputs"] and 1 <= sides[0]["inputs"] <= 200000
                       and sides[0]["inputs"] == manifest["primary_input_bits"] + nr
                       and all(side["outputs"] == count for side in sides), "CEC_INTERFACE_DIFFERS")
        relation_record = relation.verify_relation(directory, manifest)
        hashes = {}
        for name, side in zip(("gold.aig", "gate.aig"), sides, strict=True):
            path = directory / name
            record = common.identity(path, 1024 * MIB)
            with path.open("rb") as stream:
                header = stream.readline(256).decode().strip().split()
            common.require(len(header) == 6 and header[0] == "aig", "AIGER_HEADER")
            maximum, inputs, latches, outputs, ands = map(int, header[1:])
            common.require(latches == 0 and maximum == inputs + ands and inputs == side["inputs"] and outputs == count
                           and ands == side["and_nodes"] and 0 <= ands <= 64_000_000 and record["bytes"] == side["bytes"],
                           "AIGER_INTERFACE")
            hashes[name] = record
        return dict(relation=relation_record, aiger=hashes, obligation_count=count)

    def prove(self, label, netlist, ports, black, relation):
        directory = self.root / label
        directory.mkdir()
        self.yosys(directory, "structural", f"read_verilog {quoted(netlist)}\nhierarchy -top {self.top}\n"
                   f"write_json {quoted(directory / 'structural.json')}\n")
        raw = common.read_json(directory / "structural.json", 256 * MIB)
        common.require(set(raw.get("modules", {})) == {self.top}, "NETLIST_NOT_FLAT")
        module = raw["modules"][self.top]
        cells = module.get("cells", {})
        common.require(not module.get("processes") and not module.get("memories") and 1 <= len(cells) <= 500000,
                       "NETLIST_SCOPE")
        physical = self.physical_models(directory, netlist)
        for cell in cells.values():
            common.require(cell["type"] not in physical or not (cell.get("connections") or cell.get("parameters")),
                           "CONNECTED_PHYSICAL_ONLY_INSTANCE: " + cell["type"])
        found = sorted(name for name, cell in cells.items() if cell["type"] == self.macro)
        common.require(found == sorted(self.c["macro_instances"]), f"MACRO_INSTANCE_SET: {found}")
        del raw
        flatten = [self.top + "/t:" + name for name in physical] + [self.top + "/t:" + name for name in self.c["tie_cells"]]
        script = (f"read_verilog -lib {quoted(black)}\n"
                  f"read_liberty -ignore_miss_func -ignore_miss_data_latch {quoted(self.c['library']['path'])}\n"
                  f"read_verilog -noblackbox {quoted(directory / 'physical-only.v')}\nread_verilog {quoted(netlist)}\n"
                  f"hierarchy -check -top {self.top}\n"
                  + ("flatten " + " ".join(flatten) + "\n" if flatten else "")
                  + f"expose -evert {self.top}/t:{self.macro}\nselect {self.top}\n"
                  f"write_verilog -selected -noattr -norename {quoted(directory / 'gate.v')}\n"
                  f"write_json {quoted(directory / 'gate.json')}\n")
        self.yosys(directory, "cut", script)
        gate = common.read_json(directory / "gate.json", 256 * MIB)["modules"][self.top]["ports"]
        interface = lambda table: {name: (port["direction"], len(port["bits"])) for name, port in table.items()}  # noqa: E731
        gate_interface = interface(gate)
        unused = {(instance, name) for instance in self.c["macro_instances"] for name, (direction, _) in ports.items()
                  if direction == "output" and instance + "." + name in gate_interface}
        audit = self.cut_original(ports, directory / "original-cut.v", unused)
        common.save_json(directory / "original-cut.json", audit)
        self.yosys(directory, "gold-interface", f"read_verilog -sv {quoted(directory / 'original-cut.v')}\n"
                   f"hierarchy -check -top {self.top}\nproc\nwrite_json {quoted(directory / 'gold-interface.json')}\n")
        gold_interface = interface(common.read_json(directory / "gold-interface.json", 256 * MIB)["modules"][self.top]["ports"])
        if gate_interface != gold_interface:
            only_gate = sorted(set(gate_interface.items()) - set(gold_interface.items()))[:8]
            only_gold = sorted(set(gold_interface.items()) - set(gate_interface.items()))[:8]
            raise common.GateError(f"INTERFACE_DIFFERS: netlist only {only_gate}; RTL only {only_gold}")
        output_bits = sum(width for direction, width in gold_interface.values() if direction == "output")
        common.save_json(directory / "checker-config.json", dict(
            library=self.c["library"]["path"], design=dict(top=self.top, files=[str(directory / "original-cut.v")]),
            netlist=str(directory / "gate.v"), export_obligations=str(directory / "obligations"),
            export_discovery_work=int(self.limits["discovery_work"]), export_discovery_rounds=int(self.limits["discovery_rounds"])))
        self.stage(directory, "export", [self.tools["checker"], directory / "checker-config.json", directory / "checker-result.json"])
        manifest_path = directory / "obligations" / "manifest.json"
        common.require(manifest_path.is_file(), "OBLIGATIONS_NOT_EXPORTED: the RTL interpreter did not reach a complete "
                       "state relation for this netlist")
        manifest = common.read_json(manifest_path, 2 * MIB)
        verified = self.verify_manifest(relation, directory / "obligations", manifest, output_bits)
        cec = f"&cec -T {int(self.limits['cec_seconds'])} -C {int(self.limits['max_conflicts'])} " \
              f"{quoted(directory / 'obligations/gold.aig')} {quoted(directory / 'obligations/gate.aig')};"
        self.stage(directory, "abc", [self.tools["abc"], "-c", cec], int(self.limits["cec_seconds"]) + 30)
        text = (directory / "abc.log").read_text(errors="replace")
        if "NOT EQUIVALENT" in text:
            raise common.GateError("NOT_EQUIVALENT: external ABC found a difference")
        common.require(re.search(r"^Networks are equivalent\.", text, re.M) is not None, "CEC_UNDECIDED")
        common.require(verified == self.verify_manifest(relation, directory / "obligations", manifest, output_bits),
                       "OBLIGATIONS_CHANGED_DURING_PROOF")
        return dict(status="proven", complete=True, rtl_sha256=self.c["rtl"]["sha256"],
                    netlist_sha256=common.identity(netlist)["sha256"], scope=SCOPE, manifest=manifest, verified=verified,
                    cut=audit["result"], abc_verdict="Networks are equivalent.", physical_only_models=physical,
                    interface_ports=len(gold_interface), output_bits=output_bits)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    config = common.read_json(args.inputs, 2 * MIB)
    output = common.fresh_workspace(args.workspace, args.base_rev)
    summary = dict(schema="rusteda.pdk-gate.logic-result.v1", status="unverified", complete=False, scope=SCOPE)
    tick = time.monotonic()
    try:
        common.require(config.get("schema") == SCHEMA, "INPUT_SCHEMA")
        records = [config[k] for k in ("rtl", "synthesis_netlist", "final_netlist", "library", "macro_bb", "relation_checker")]
        records += list(config["physical"].values())
        for record in records:
            common.verify(record, "input")
        job = Job(config, output)
        relations = Path(config["relation_checker"]["path"])
        common.require(relations.name == "large_lec.py", "RELATION_CHECKER")
        sources = {name: common.identity(relations.parent / name, 4 * MIB) for name in RELATION_SOURCES}
        sys.path.insert(0, str(relations.parent))
        spec = importlib.util.spec_from_file_location("rusteda_pinned_large_lec", relations)
        relation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relation)
        ports, supplies, black = job.macro_interface()
        failures, proofs = {}, {}
        for label, key in (("synthesis", "synthesis_netlist"), ("final", "final_netlist")):
            try:
                proofs[label] = job.prove(label, config[key]["path"], ports, black, relation)
            except (common.GateError, OSError, KeyError, ValueError, TypeError, RuntimeError, MemoryError,
                    subprocess.SubprocessError) as error:
                failures[label] = str(error)
                proofs[label] = dict(status="unverified", complete=False, error=str(error),
                                     rtl_sha256=config["rtl"]["sha256"], netlist_sha256=config[key]["sha256"], scope=SCOPE)
            common.save_json(output / (label + "_equivalence.json"), proofs[label])
        bits = {p["manifest"]["rtl_state_bits"] for p in proofs.values() if p["status"] == "proven"}
        if len(bits) > 1:
            failures["state"] = "RTL state bit counts differ between the two proofs"
        for record in records:
            common.verify(record, "input after run")
        disproven = any(text.startswith("NOT_EQUIVALENT") for text in failures.values())
        summary.update(status="passed" if not failures else "failed" if disproven else "unverified",
                       complete=not failures, failures=failures,
                       top=config["top"], macro=config["macro"], macro_instances=config["macro_instances"],
                       macro_functional_ports={k: list(v) for k, v in ports.items()}, macro_supply_ports=supplies,
                       rtl_sha256=config["rtl"]["sha256"], synthesis_netlist_sha256=config["synthesis_netlist"]["sha256"],
                       final_netlist_sha256=config["final_netlist"]["sha256"],
                       proofs={label: {k: proof.get(k) for k in ("status", "error", "abc_verdict", "output_bits")}
                               | ({"rtl_state_bits": proof["manifest"]["rtl_state_bits"],
                                   "gate_state_elements": proof["manifest"]["gate_state_elements"]}
                                  if proof["status"] == "proven" else {}) for label, proof in proofs.items()},
                       relation_sources=sources, stages=job.stages, tools=config["tools"])
    except (common.GateError, OSError, KeyError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        summary.update(error=str(error))
    summary["wall_seconds"] = round(time.monotonic() - tick, 3)
    common.save_json(output / "summary.json", summary)
    print(json.dumps({k: summary.get(k) for k in ("status", "complete", "failures", "error")}))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

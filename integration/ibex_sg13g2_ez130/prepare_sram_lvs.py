#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Reproduce the qualified, narrowly scoped legacy IHP 1P SRAM LVS overlay.

The original Apache-2.0 PDK is an external, hash-pinned input. This repository
stores the bounded transformations, not a copy of the PDK deck. No GDS geometry,
device connection, port, or transistor parameter is changed. Six reachable
resistor templates receive explicitly evidenced layer/width metadata repairs.
Hierarchy substitutions preserve connectivity and the complete macro contents.
At LVS runtime, a bounded fixed point flattens only current zero-device,
zero-child leaves through the public KLayout API, preserving their wire aliases.
The original device mapping, purge, alignment and full comparison still run.

Inputs: <=2 MiB CDL, <=128 rule files/4 MiB, <=16384 statements/4096 circuits.
Deck include depth<=32, local expansion depth<=16 and <=10000 generated devices
per changed circuit. Peak estimate <64 MiB for these pinned inputs, with a
512 MiB external process cap recommended for include caches and audit strings.
This command only prepares files. A
fresh LVS run, strict ports, extraction-error checks and defect controls remain
required when using the resulting deck on a new layout.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, localcontext
import fcntl
import hashlib
import json
from pathlib import Path
import re


TOP = "RM_IHPSG13_1P_1024x32_c2_bm_bist"
CDL_HASH = "58561ee0e35e4b77191318aa5ac7924e64550d9ae797c9b6d3f35ff25b540302"
GDS_HASH = "4d1178811e3b83a8f6fc85f017fdd8d9b43629f785929ad60dbbaa2a763b96b1"
RULESET_HASH = "50835430121cb7176233ddfd08b0deaea42a915e59df5a92de509785a7fd8ac7"
OUTPUT_CDL_HASH = "71db38d0106a0a25e88ee4fd26bd7c4a335f94c9e3c4fc816c0aaf9fa51fec74"
OUTPUT_DECK_HASH = "f1e4d4e47757c0783b87022a92dc4699c8a7f49e2d67452bce127c75a257a8d3"
UPSTREAM = "https://github.com/IHP-GmbH/IHP-Open-PDK/pull/1121#issuecomment-6424708793"

# Exact source-to-layout names established by complete device-level matching.
# BITKIT_CELL and DLY_MUX stay unmatched, so align unfolds them at their parents.
ALPHA_SUFFIXES = (
    "BITKIT_16x2_CORNER", "BITKIT_EDGE_LR", "BITKIT_16x2_EDGE_LR",
    "BITKIT_16x2_SRAM", "BITKIT_16x2_EDGE_TB", "COLDRV13X4", "WLDRV16X4",
    "DEC04", "CTRL", "COLDEC2", "BLDRV", "COLCTRL2", "COLDRV13_FILL4",
    "COLDRV13_FILL4C2", "DEC01", "DEC00", "BITKIT_TAP", "BITKIT_16x2_TAP",
    "BITKIT_16x2_TAP_LR", "DEC03", "DEC02", "ROWDEC8", "ROWREG8",
)

CONNECTIVITY_PATCH = '''# RustEDA compatibility overlay: old SRAM GDS stores labels on pin datatype2.
# Labels identify nets only; no conductive shapes, device rules or terminals change.
legacy_m1_text = labels(8, 2)
legacy_m2_text = labels(10, 2)
legacy_m3_text = labels(30, 2)
connect(metal1_con, legacy_m1_text)
connect(metal2_con, legacy_m2_text)
connect(metal3_con, legacy_m3_text)
# Every virtual PG join must be physically closed higher in the real hierarchy.
connect_implicit('RM_IHPSG13*', 'VDD!')
connect_implicit('RM_IHPSG13*', 'VSS!')
top_level(true)
max_branch_complexity(500)
'''
EDGE_HINTS = '''# Strict correspondence of existing wordline labels; all connected devices still compared.
    (0...16).each do |i|
      same_nets!('RM_IHPSG13_1P_BITKIT_16x2_EDGE_LR', "LWL<#{i}>", 'RM_IHPSG13_1P_BITKIT_16X2_EDGE_LR', "A_WL<#{i}>")
    end
    '''

EMPTY_LEAF_NORMALIZATION = '''# Authored compatibility normalization over public KLayout APIs only.
# Every chosen definition is strictly0-device/0-child in the current round.
# Wire aliases propagate to callers; no active-device hierarchy is flattened.
normalize_empty_leafs = lambda do |target, label|
  rounds = 0
  objects = 0
  references = 0
  leaf_nets = 0
  expanded_wire_nets = 0
  removed_definitions = 0
  initial_devices = nil
  loop do
    rounds += 1
    error('empty-leaf fixed-point round budget exceeded') if rounds > 128
    definitions = 0
    stored_devices = 0
    selected = []
    target.each_circuit do |circuit|
      definitions += 1
      error('empty-leaf definition budget exceeded') if definitions > 4096
      device_count = 0
      child_count = 0
      circuit.each_device do |_device|
        device_count += 1
        stored_devices += 1
        objects += 1
        error('empty-leaf cumulative-object budget exceeded') if objects > 2000000
      end
      circuit.each_subcircuit do |_sub|
        child_count += 1
        objects += 1
        error('empty-leaf cumulative-object budget exceeded') if objects > 2000000
      end
      next unless device_count.zero? && child_count.zero? && circuit.has_refs?
      next if circuit.name.to_s.casecmp('RM_IHPSG13_1P_1024x32_c2_bm_bist').zero?
      next if circuit.name.to_s.casecmp($topcell.to_s).zero?
      local_refs = 0
      circuit.each_ref do |_ref|
        local_refs += 1
        references += 1
        error('empty-leaf caller-instance budget exceeded') if references > 500000
      end
      local_nets = 0
      circuit.each_net do |_net|
        local_nets += 1
        leaf_nets += 1
        error('empty-leaf net budget exceeded') if leaf_nets > 100000
      end
      expanded_wire_nets += local_refs * local_nets
      error('empty-leaf expanded-wire budget exceeded') if expanded_wire_nets > 5000000
      selected << circuit
    end
    initial_devices = stored_devices if initial_devices.nil?
    error('empty-leaf fixed point changed native devices') unless stored_devices == initial_devices
    break if selected.empty?
    removed_definitions += selected.size
    error('empty-leaf removed-definition budget exceeded') if removed_definitions > 4096
    target.flatten_circuits(selected)
    # The next round enumerates fresh objects; old pin IDs can be renumbered.
  end
  logger.info("Empty wired-leaf normalization [#{label}]: rounds=#{rounds}, definitions=#{removed_definitions}, caller_instances=#{references}, stored_devices=#{initial_devices}, expanded_wire_nets_estimate=#{expanded_wire_nets}")
end
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path: Path, limit: int) -> bytes:
    require(path.is_file() and path.stat().st_size <= limit, f"missing/oversized input: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, f"input byte budget exceeded: {path}")
    return data


def deck_overlay(root: Path) -> tuple[str, dict]:
    inputs, cache = {}, {}
    total_bytes = 0

    def expand(path: Path, stack: tuple[Path, ...] = ()) -> str:
        nonlocal total_bytes
        path = path.resolve()
        require(path.is_relative_to(root) and path not in stack, "rule include escapes root/cycles")
        require(len(stack) < 32, "rule include depth exceeded")
        if path in cache:
            return cache[path]
        raw = read(path, 1024 * 1024)
        total_bytes += len(raw)
        require(total_bytes <= 4 * 1024 * 1024 and len(inputs) < 128, "rule input budget exceeded")
        inputs[path.relative_to(root).as_posix()] = digest(raw)
        result, size = [], 0
        for line in raw.decode("utf-8").splitlines(keepends=True):
            match = re.fullmatch(r"\s*#\s*%include\s+(\S+)\s*", line)
            part = expand(path.parent / match[1], (*stack, path)) if match else line
            size += len(part.encode())
            require(size < 4 * 1024 * 1024, "expanded rule byte budget exceeded")
            result.append(part)
        cache[path] = "".join(result)
        return cache[path]

    text = expand(root / "sg13g2.lvs")
    fingerprint = digest(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode())
    require(len(inputs) == 44 and fingerprint == RULESET_HASH, "pinned PDK rule set hash mismatch")
    before = "logger.info('Starting SG13G2 LVS RF MOS Model Mapping')"
    require(text.count(before) == 1, "RF mapping insertion point changed")
    text = text.replace(before, CONNECTIVITY_PATCH + "\n" + before)
    before = "logger.info('Starting SG13G2 LVS Comparison in strict port mode.')"
    require(text.count(before) == 1, "strict comparison insertion point changed")
    text = text.replace(before, EDGE_HINTS + before)
    for before, after in (
        ("ensure_rfmode_parameter = lambda do |device_class|",
         EMPTY_LEAF_NORMALIZATION + "\nensure_rfmode_parameter = lambda do |device_class|"),
        ("  target_netlist.purge_devices\n  target_netlist.purge",
         "  target_netlist.purge_devices\n"
         "  normalize_empty_leafs.call(target_netlist, 'layout_netlist')\n"
         "  target_netlist.purge"),
        ("  align\n", "  normalize_empty_leafs.call(schematic, 'schematic_netlist')\n  align\n"),
    ):
        require(text.count(before) == 1, "empty-leaf normalization insertion point changed")
        text = text.replace(before, after)
    return text, dict(files=inputs, aggregate_sha256=fingerprint, total_source_bytes=total_bytes,
                     public_api="https://www.klayout.de/doc/manual/lvs_connect.html",
                     changes=["recognize existing legacy metal1/2/3 pin-layer labels",
                              "RM-only VDD!/VSS! must-connect; top_level(true)",
                              "max_branch_complexity(500)",
                              "16 required EDGE wordline net correspondences",
                              "bounded public-API flatten of strictly zero-device/zero-child leaves"],
                     normalization=dict(public_api="Netlist#flatten_circuits",
                                        helper_sha256=digest(EMPTY_LEAF_NORMALIZATION.encode()),
                                        wire_aliases="propagated by native flatten, never deleted",
                                        original_mapping_purge_align_compare_retained=True,
                                        rounds=128, definitions=4096, objects=2000000,
                                        caller_instances=500000, leaf_nets=100000,
                                        expanded_wire_nets=5000000))


def repair_resistors(text: str) -> tuple[str, list]:
    lines = text.splitlines(keepends=True)
    require(len(lines) < 16384, "CDL line budget exceeded")
    cell, changes = None, []
    for index, line in enumerate(lines):
        if line.upper().startswith(".SUBCKT "):
            cell = line.split()[1]
        if not re.match(r"^R\S+", line):
            continue
        fields = line.split()
        width, length = "2.6e-07", "6e-07"
        if cell == "RM_IHPSG13_1024x32_c2_1P_BITKIT_CELL":
            require(fields[0] in ("R0", "R1", "R2"), "unexpected bitcell resistor")
            layer = 3 if fields[0] == "R2" else 2
            width = "2e-07"
        elif cell == "RSC_IHPSG13_MET2RES":
            layer = 2
        elif cell == "RSC_IHPSG13_MET3RES":
            layer = 3
        elif cell == "RSC_IHPSG13_CDLYX1_DUMMY":
            layer = 1
        else:
            continue  # Hash-pinned, unreachable 2P definitions remain untouched.
        require(fields[3:] == ["lvsres", "w=2.6e-07", "l=6e-07"], "resistor template changed")
        with localcontext() as context:
            context.prec = 28
            sheet = Decimal(".110") if layer == 1 else Decimal(".088")
            resistance = sheet * Decimal(length) / Decimal(width)
        after = " ".join(fields[:3]) + f" {resistance} res_metal{layer} w={width} l={length}\n"
        require(fields[:3] == after.split()[:3], "resistor terminal binding changed")
        changes.append(dict(line=index + 1, cell=cell, original=line.rstrip(),
                            replacement=after.rstrip(), terminals_preserved=True,
                            source=UPSTREAM if "BITKIT" in cell else "pinned res_extraction.lvs"))
        lines[index] = after
    require(len(changes) == 6, "expected exactly six reachable resistor repairs")
    return "".join(lines), changes


def local_hierarchy(text: str) -> tuple[str, dict]:
    # Retain every untouched statement's exact original bytes.
    statements = []
    for line in text.splitlines(keepends=True):
        if line.startswith("+"):
            require(bool(statements), "orphan CDL continuation")
            statements[-1][1] += " " + line[1:].strip()
            statements[-1][2] += line
        else:
            statements.append([len(statements), line.strip(), line])
    require(len(statements) < 16384, "CDL statement budget exceeded")
    cells, start, name, ports = {}, None, None, None
    for index, (_, line, _) in enumerate(statements):
        if line.upper().startswith(".SUBCKT "):
            require(start is None, "nested CDL definition")
            fields = line.split(); start, name, ports = index, fields[1], fields[2:]
        elif line.upper().startswith(".ENDS"):
            require(start is not None and name not in cells, "orphan/duplicate CDL definition")
            cells[name] = (ports, statements[start + 1:index]); start = None
    require(start is None and len(cells) < 4096, "CDL definition budget/unclosed cell")
    changed, audit = {}, []
    work = 0

    def expand(line: str, prefix: str, binding: dict, stack: tuple[str, ...] = ()) -> list[str]:
        nonlocal work
        work += 1
        require(work < 20000 and len(stack) < 16, "local expansion work/depth exceeded")
        fields = line.split(); kind = fields[0][0].upper()

        def net(value: str) -> str:
            return binding.get(value, value if value == "0" or value.endswith("!") else prefix + value)

        if kind == "X":
            fields = [field for field in fields if field != "/"]
            target = fields[-1]
            require(target in cells and target not in stack, "missing/cyclic local CDL child")
            formals, body = cells[target]
            require(len(formals) == len(fields[1:-1]), "local CDL terminal arity mismatch")
            child_binding = {pin: net(value) for pin, value in zip(formals, fields[1:-1])}
            result = []
            for _, child, _ in body:
                if not child or child.startswith("*"):
                    continue
                generated = expand(child, prefix + fields[0] + "__", child_binding, (*stack, target))
                require(len(result) + len(generated) < 10000, "local expanded device budget exceeded")
                result.extend(generated)
            return result
        require(kind in ("M", "R", "C"), f"unsupported local CDL statement: {line}")
        count = 4 if kind == "M" else 2
        require(len(fields) > count + 1, "incomplete local device")
        fields[0] = kind + prefix + fields[0][1:]
        for index in range(1, count + 1):
            fields[index] = net(fields[index])
        return [" ".join(fields)]

    for name, (_, body) in cells.items():
        for index, line, _ in body:
            fields = line.split()
            selected = name == "RM_IHPSG13_1024x32_c2_1P_BLDRV" and line.startswith("X")
            selected |= (name == "RM_IHPSG13_1024x32_c2_1P_DEC04" and line.startswith("X")
                         and fields[-1] == "RSC_IHPSG13_CINVX4")
            if selected:
                arguments = [field for field in fields if field != "/"]
                result = expand(line, "__local_", {net: net for net in arguments[1:-1]})
                changed[index] = "\n".join(result) + "\n"
                audit.append(dict(cell=name, instance=line, expanded=result))
    require(len(audit) == 6, "expected five BLDRV children and one DEC04 inverter")
    result = "".join(changed.get(index, raw) for index, _, raw in statements)
    mapping = {"RM_IHPSG13_1024x32_c2_1P_" + suffix: "RM_IHPSG13_1P_" + suffix
               for suffix in ALPHA_SUFFIXES}
    require(all(name in cells for name in mapping), "alpha source name changed")
    pattern = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, sorted(mapping, key=len, reverse=True)))
                         + r")(?![A-Za-z0-9_])")
    result = pattern.sub(lambda match: mapping[match[0]], result)
    require(len(result.encode()) < 4 * 1024 * 1024, "output CDL byte budget exceeded")
    return result, dict(expanded_instances=audit, alpha_mapping=mapping,
                        deliberately_unmatched=["BITKIT_CELL", "DLY_MUX"], expansion_work=work,
                        note="align locally unfolds unmatched circuit contents; no blackboxes")


def prepare(pdk: Path) -> tuple[str, str, dict]:
    pdk = pdk.resolve()
    cdl = pdk / "libs.ref/sg13g2_sram/cdl" / (TOP + ".cdl")
    gds = pdk / "libs.ref/sg13g2_sram/gds" / (TOP + ".gds")
    original = read(cdl, 2 * 1024 * 1024)
    require(digest(original) == CDL_HASH, "pinned original SRAM CDL hash mismatch")
    require(digest(read(gds, 2 * 1024 * 1024)) == GDS_HASH, "pinned original SRAM GDS hash mismatch")
    text, repairs = repair_resistors(original.decode("utf-8"))
    text, hierarchy = local_hierarchy(text)
    deck, rules = deck_overlay((pdk / "libs.tech/klayout/tech/lvs").resolve())
    require(digest(text.encode()) == OUTPUT_CDL_HASH, "qualified output CDL hash mismatch")
    require(digest(deck.encode()) == OUTPUT_DECK_HASH, "qualified output deck hash mismatch")
    report = dict(status="prepared_not_lvs_verified", revision="rev1", macro=TOP,
                  pdk=str(pdk), source_cdl_sha256=CDL_HASH, original_gds_sha256=GDS_HASH,
                  output_cdl_sha256=OUTPUT_CDL_HASH, output_deck_sha256=OUTPUT_DECK_HASH,
                  resistor_repairs=repairs, hierarchy=hierarchy, rules=rules,
                  geometry_changes=False, blackboxes=[], ignored_ports=[],
                  lvs_status="unchecked", drc_status="unchecked",
                  evidence=UPSTREAM, candidate_PR1121_deck_used=False,
                  bounds=dict(cdl_bytes=2 * 1024 * 1024, rule_files=128,
                              total_rule_bytes=4 * 1024 * 1024, statements=16384,
                              circuits=4096, include_depth=32, expansion_depth=16,
                              local_devices_per_expansion=10000, expansion_work=20000),
                  peak_memory_estimate="pinned inputs <64MiB, one process; recommend512MiB OS cap; no graph path enumeration")
    return text, deck, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    parser.add_argument("--pdk", type=Path, required=True, help="original hash-pinned ihp-sg13g2 directory")
    args = parser.parse_args()
    require(args.base_rev == "empty", "REVISION_CONFLICT: preparation requires --base-rev empty")
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(not any(path.name != "workspace.lock" for path in args.workspace.iterdir()),
                "REVISION_CONFLICT: preparation requires an empty workspace")
        cdl, deck, report = prepare(args.pdk)
        report["generator_sha256"] = digest(read(Path(__file__), 128 * 1024))
        (args.workspace / "sram.cdl").write_text(cdl, encoding="utf-8", newline="")
        (args.workspace / "sg13g2.lvs").write_text(deck, encoding="utf-8", newline="")
        (args.workspace / "provenance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (args.workspace / "HEAD").write_text("rev1\n", encoding="utf-8")
        print(json.dumps({key: report[key] for key in
                          ("status", "revision", "output_cdl_sha256", "output_deck_sha256")}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from error

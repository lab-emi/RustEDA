#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Build a bounded, hierarchical device reference from a powered Yosys JSON.

Yosys is used only as an external structural Verilog parser. This adapter never
synthesizes, flattens, invents a memory model, or changes library devices. Cell
bus ranges and EZ130 body-to-supply bindings are explicit inputs. All referenced
CDL children are included, with missing definitions and cycles rejected.

Estimate: <=64 MiB JSON + <=32 MiB CDL text, <=100000 instances / 1000000 pin
bits, <=4096 cell definitions. This 42180-cell fixture measured 165 MiB; budget
1 GiB for preparation. Run under an OS
memory/time guard. This prepares a reference, and does not assert LVS passed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import hashlib
import json
from pathlib import Path


MAX_BYTES = 64 * 1024 * 1024
MAX_PINS = 1_000_000
MAX_INSTANCES = 100_000
MAX_CELLS = 4096


def read(path: Path, limit: int) -> str:
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"missing or oversized input: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"input grew beyond budget: {path}")
    return data.decode("utf-8")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def token(value: str) -> str:
    if not value or not value.isascii() or len(value) > 4096 or any(c.isspace() for c in value):
        raise ValueError(f"invalid SPICE token: {value!r}")
    if value == "0" or any(c in value for c in "\x00;=()\"'") or value[0] in "*+.$":
        raise ValueError(f"unsupported SPICE token: {value!r}")
    return value


def subckts(text: str) -> dict:
    """Keep exact source blocks; parse only headers, pin info and X dependencies."""
    rows = text.splitlines(keepends=True)
    if len(rows) > 200_000:
        raise ValueError("CDL line budget exceeded")
    result = {}
    i = 0
    while i < len(rows):
        words = rows[i].split()
        if not words or words[0].lower() != ".subckt":
            i += 1
            continue
        if len(words) < 2:
            raise ValueError("missing CDL cell name")
        name = token(words[1])
        pins = words[2:]
        begin = i
        i += 1
        while i < len(rows) and rows[i].lstrip().startswith("+"):
            pins.extend(rows[i].lstrip()[1:].split())
            i += 1
        if any("=" in p for p in pins) or len(pins) > 65536:
            raise ValueError(f"unsupported parameterized/oversized CDL header: {name}")
        for pin in pins:
            token(pin)
        if len(pins) != len({p.casefold() for p in pins}):
            raise ValueError(f"duplicate CDL pins in {name}")
        body = []
        while i < len(rows) and not rows[i].lower().lstrip().startswith(".ends"):
            if rows[i].lower().lstrip().startswith(".subckt"):
                raise ValueError(f"nested CDL definition in {name}")
            if rows[i].lstrip().startswith("+"):
                if not body:
                    raise ValueError("orphan CDL continuation")
                body[-1] += " " + rows[i].lstrip()[1:].strip()
            else:
                body.append(rows[i].strip())
            i += 1
        if i == len(rows):
            raise ValueError(f"missing .ENDS for {name}")
        children, child_calls = [], []
        directions = {}
        devices = 0
        for line in body:
            fields = line.split()
            if not fields:
                continue
            if fields[0].upper() == "*.PININFO":
                for entry in fields[1:]:
                    pin, direction = entry.rsplit(":", 1)
                    directions[pin] = direction
            elif fields[0][0].lower() == "x":
                args = [f for f in fields[1:] if f != "/"]
                if not args or any("=" in f for f in args):
                    raise ValueError(f"missing/parameterized child model in {name}")
                if len(children) >= 65536:
                    raise ValueError("CDL child count budget exceeded")
                children.append(token(args[-1]))
                child_calls.append((args[-1], len(args) - 1))
                devices += 1
            elif fields[0][0].lower() in "mdrcqj":
                devices += 1
        key = name.casefold()
        if key in result or len(result) >= MAX_CELLS:
            raise ValueError("duplicate cell or CDL definition budget exceeded")
        result[key] = dict(name=name, pins=pins, directions=directions, children=children,
                           child_calls=child_calls, devices=devices,
                           text="".join(rows[begin:i + 1]))
        i += 1
    return result


def generate(data: dict, top: str, library: dict, buses: dict, body_ties: dict,
             top_buses: dict):
    modules = data.get("modules", {})
    if set(modules) != {top}:
        raise ValueError("expected exactly the requested flat structural top module")
    module = modules[top]
    if module.get("memories") or module.get("processes"):
        raise ValueError("structural powered netlist required")
    cells = module.get("cells", {})
    if not 1 <= len(cells) <= MAX_INSTANCES:
        raise ValueError("instance budget exceeded")
    port_names, names, reserved = [], {}, set()
    if not set(top_buses) <= set(module["ports"]):
        raise ValueError("explicit top bus metadata names an absent port")
    for name, port in module["ports"].items():
        bits = port["bits"]
        declared = top_buses.get(name)
        if declared is None:
            if len(bits) != 1 or "offset" in port or "upto" in port:
                raise ValueError(f"explicit top bus range required for {name}")
        else:
            msb, lsb = declared
            if (type(msb) is not int or type(lsb) is not int
                    or abs(msb - lsb) + 1 != len(bits)):
                raise ValueError(f"top bus width mismatch for {name}")
            if min(msb, lsb) != port.get("offset", 0) or (msb < lsb) != bool(port.get("upto", 0)):
                raise ValueError(f"top bus range/direction mismatch for {name}")
        for k, bit in enumerate(bits):
            index = lsb + (k if msb >= lsb else -k) if declared is not None else None
            label = f"{name}[{index}]" if declared is not None else name
            token(label)
            if type(bit) is not int or bit in names or label.casefold() in reserved:
                raise ValueError("constant or aliased top ports require an explicit LVS interface")
            names[bit] = label
            reserved.add(label.casefold())
            port_names.append(label)
            if len(port_names) > MAX_PINS:
                raise ValueError("top port budget exceeded")
    # Internal names are generated from parser bit IDs, avoiding escaped-name
    # collisions. Original instance labels are retained separately in the map.
    def generated(label):
        if label.casefold() in reserved:
            raise ValueError(f"top port collides with generated internal net: {label}")
        return label

    def net(bit):
        if type(bit) is not int or not 0 <= bit < 2**32:
            raise ValueError("constant/X/Z pin in powered netlist: physical tie cell required")
        return names[bit] if bit in names else generated(f"rusteda_internal_{bit}")

    used, instance_lines, mapping = set(), [], []
    open_outputs, body_bindings, empty_cells = Counter(), Counter(), Counter()
    total_pins = 0
    for index, (instance_name, instance) in enumerate(cells.items()):
        kind = instance["type"]
        sub = library.get(kind.casefold())
        if sub is None or instance.get("parameters"):
            raise ValueError(f"missing CDL cell or unsupported parameters: {kind}")
        ports = instance["connections"]
        total_pins += len(sub["pins"])
        if total_pins > MAX_PINS or sum(map(len, ports.values())) > len(sub["pins"]):
            raise ValueError("connected pin budget/coverage exceeded")
        ranges = buses.get(kind, {})
        expanded = {}
        def put(pin, value):
            if pin in expanded:
                raise ValueError(f"overlapping bus/scalar port: {instance_name}.{pin}")
            expanded[pin] = value
        for pin, bits in ports.items():
            if pin in ranges:
                msb, lsb = ranges[pin]
                if type(msb) is not int or type(lsb) is not int:
                    raise ValueError("integer bus range required")
                if abs(msb - lsb) + 1 != len(bits):
                    raise ValueError(f"bus width mismatch: {instance_name}.{pin}")
                step = 1 if msb >= lsb else -1
                for k, bit in enumerate(bits):
                    put(f"{pin}<{lsb + step * k}>", net(bit))
            elif len(bits) == 1:
                put(pin, net(bits[0]))
            elif not bits and sub["directions"].get(pin) == "O":
                put(pin, generated(f"rusteda_open_{index}_{len(expanded)}"))
                open_outputs[kind] += 1
            else:
                raise ValueError(f"undeclared vector/open port: {instance_name}.{pin}")
        for pin, supply in body_ties.get(kind, {}).items():
            if pin in expanded or supply not in expanded or pin not in sub["pins"]:
                raise ValueError(f"invalid body supply binding: {kind}.{pin}->{supply}")
            expanded[pin] = expanded[supply]
            body_bindings[f"{kind}.{pin}->{supply}"] += 1
        # Structural readers omit `.Y()` entirely. Preserve a disconnected
        # output as its own net, only when the original CDL declares it O.
        for pin in sub["pins"]:
            if pin not in expanded and sub["directions"].get(pin) == "O":
                put(pin, generated(f"rusteda_open_{index}_{len(expanded)}"))
                open_outputs[kind] += 1
        if set(expanded) != set(sub["pins"]):
            raise ValueError(f"{instance_name} pins missing={set(sub['pins']) - set(expanded)} "
                             f"extra={set(expanded) - set(sub['pins'])}")
        used.add(kind.casefold())
        if sub["devices"] == 0:
            empty_cells[kind] += 1
        instance_lines.append(f"Xrusteda_{index} " + " ".join(expanded[p] for p in sub["pins"])
                              + " " + sub["name"] + "\n")
        mapping.append(dict(reference=f"Xrusteda_{index}", instance=instance_name, cell=kind))
    # Iterative bounded DFS: a dependency is processed once and cycles fail.
    order, colors = [], {}
    pending = [(key, False) for key in sorted(used, reverse=True)]
    expansions = 0
    while pending:
        key, finish = pending.pop()
        if finish:
            colors[key] = 2
            order.append(key)
            continue
        if colors.get(key) == 2:
            continue
        if colors.get(key) == 1:
            raise ValueError("recursive CDL hierarchy is unsupported")
        if key not in library:
            raise ValueError(f"unresolved CDL child: {key}")
        for child, arity in library[key]["child_calls"]:
            if child.casefold() not in library or len(library[child.casefold()]["pins"]) != arity:
                raise ValueError(f"CDL child terminal count differs: {key}->{child}")
        colors[key] = 1
        expansions += 1
        children = library[key]["children"]
        if expansions > MAX_CELLS or len(pending) + 1 + len(children) > 65536:
            raise ValueError("CDL hierarchy work budget exceeded")
        pending.append((key, True))
        pending.extend((child.casefold(), False) for child in reversed(children))
    text = "* Powered device-level LVS reference; no black boxes or cell substitutions.\n"
    text += "".join(library[key]["text"] + "\n" for key in order)
    text += f".SUBCKT {token(top)} {' '.join(port_names)}\n"
    text += "".join(instance_lines) + f".ENDS {top}\n"
    return text, dict(status="prepared_not_lvs_verified", top=top, instances=len(cells),
                      top_ports=port_names,
                      connected_pin_bits=total_pins, included_cells=[library[k]["name"] for k in order],
                      retained_deviceless_instances=dict(empty_cells),
                      explicit_body_supply_bindings=dict(body_bindings),
                      disconnected_output_pins=dict(open_outputs), instance_mapping=mapping)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--cdl", type=Path, action="append", required=True)
    parser.add_argument("--interfaces", type=Path, required=True,
                        help="JSON containing cell_buses, top_buses and per-cell body_ties")
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.base_rev != "empty" or any(p.name != "workspace.lock" for p in args.workspace.iterdir()):
            raise ValueError("REVISION_CONFLICT: reference preparation requires an empty workspace")
        raw = read(args.json, MAX_BYTES)
        interfaces_raw = read(args.interfaces, 2 * 1024 * 1024)
        interfaces = json.loads(interfaces_raw)
        if set(interfaces) != {"cell_buses", "body_ties", "top_buses"}:
            raise ValueError("expected explicit cell_buses/body_ties/top_buses metadata")
        library, sources, size = {}, {}, 0
        if not 1 <= len(args.cdl) <= 16:
            raise ValueError("1..16 CDL inputs required")
        for path in args.cdl:
            source = read(path, 32 * 1024 * 1024)
            size += len(source.encode())
            if size > 32 * 1024 * 1024:
                raise ValueError("total CDL byte budget exceeded")
            parsed = subckts(source)
            if set(parsed) & set(library) or len(parsed) + len(library) > MAX_CELLS:
                raise ValueError("duplicate or excessive library cells")
            library.update(parsed)
            sources[str(path.resolve())] = sha(source)
        text, report = generate(json.loads(raw), args.top, library,
                                interfaces["cell_buses"], interfaces["body_ties"],
                                interfaces["top_buses"])
        report.update(source_json_sha256=sha(raw), source_cdl_sha256=sources,
                      interfaces_sha256=sha(interfaces_raw), reference_sha256=sha(text),
                      revision="rev1", ignored_cells=[], macro_blackboxes=[])
        (args.workspace / "reference.cdl").write_text(text)
        (args.workspace / "reference.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.workspace / "HEAD").write_text("rev1\n")
        print(json.dumps({k: v for k, v in report.items() if k != "instance_mapping"}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from error

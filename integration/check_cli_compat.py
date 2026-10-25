#!/usr/bin/env python3
"""Compare frozen pre-migration and post-migration CLI behavior on both baseline PDKs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

VOLATILE = {"created_unix_ms", "time_unix_ms"}


def normalized(value, workspace, binary):
    if isinstance(value, dict):
        return {k: normalized(v, workspace, binary)
                for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [normalized(v, workspace, binary) for v in value]
    if isinstance(value, str):
        return value.replace(str(workspace), "<workspace>").replace(str(binary), "<rusteda>")
    return value


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(binary, workspace, arguments):
    result = subprocess.run(
        ["rusteda", "--threads", "1", "--format", "json", *arguments],
        executable=str(binary),
        stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False,
    )
    if len(result.stdout) + len(result.stderr) > 4 * 1024 * 1024:
        raise RuntimeError("CLI output budget exceeded")
    body = json.loads(result.stdout)
    return {"exit_code": result.returncode,
            "output": normalized(body, workspace, binary)}


def exercise(binary, base, libraries, inputs):
    result = {}
    for pdk, library in libraries.items():
        workspace = base / pdk
        commands = [
            ["--version"],
            ["--help"],
            ["init", "-w", str(workspace)],
            ["import", "-w", str(workspace), "--base-rev", "rev0",
             "--lib", str(library), "--corner", "tt"],
            ["import", "-w", str(workspace), "--base-rev", "rev1",
             "--netlist", str(inputs[pdk])],
            ["show", "-w", str(workspace)],
            ["log", "-w", str(workspace)],
            ["import", "-w", str(workspace), "--base-rev", "rev0",
             "--netlist", str(inputs[pdk])],
            ["import", "-w", str(workspace), "--base-rev", "rev2", "--unknown-option"],
            ["export", "-w", str(workspace), "--netlist", str(workspace / "out.v")],
        ]
        trace = [run(binary, workspace, cmd) for cmd in commands]
        for index in (0, 1, 2, 3, 4, 5, 6, 9):
            if trace[index]["exit_code"] != 0 or not trace[index]["output"]["ok"]:
                raise RuntimeError(f"{pdk}: positive command {index} failed: {trace[index]}")
        for index in (7, 8):
            if trace[index]["exit_code"] == 0 or trace[index]["output"]["ok"]:
                raise RuntimeError(f"{pdk}: negative command {index} unexpectedly passed")
        tables = sorted(workspace.rglob("*.rbin"))
        if len(tables) > 1000 or sum(p.stat().st_size for p in tables) > 256 * 1024 * 1024:
            raise RuntimeError("workspace table budget exceeded")
        history = [normalized(json.loads(line), workspace, binary)
                   for line in (workspace / "history.jsonl").read_text().splitlines()]
        result[pdk] = {
            "trace": trace,
            "tables": {p.relative_to(workspace).as_posix(): file_hash(p) for p in tables},
            "export": (workspace / "out.v").read_text(),
            "history": history,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--sky130-lib", type=Path, required=True)
    parser.add_argument("--gf180-lib", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    libraries = {"sky130": args.sky130_lib.resolve(), "gf180": args.gf180_lib.resolve()}
    inputs = {}
    for name, cell, pin_in, pin_out in (
        ("sky130", "sky130_fd_sc_hd__inv_1", "A", "Y"),
        ("gf180", "gf180mcu_fd_sc_mcu7t5v0__inv_1", "I", "ZN"),
    ):
        path = args.work / (name + ".v")
        path.write_text(
            f"module compat(input a, output y); {cell} u (.{pin_in}(a), .{pin_out}(y)); endmodule\n")
        inputs[name] = path.resolve()
    before = exercise(args.before.resolve(), args.work / "before", libraries, inputs)
    after = exercise(args.after.resolve(), args.work / "after", libraries, inputs)
    (args.work / "before.json").write_text(json.dumps(before, indent=2) + "\n")
    (args.work / "after.json").write_text(json.dumps(after, indent=2) + "\n")
    same = before == after
    report = {
        "ok": same, "scope": "shared_cli_and_workspace_compatibility",
        "commands_per_pdk": 10, "pdks": list(libraries),
        "normalization": sorted(VOLATILE) + ["workspace path", "executable path"],
        "library_hashes": {name: file_hash(path) for name, path in libraries.items()},
        "before_binary": file_hash(args.before), "after_binary": file_hash(args.after),
    }
    (args.work / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())

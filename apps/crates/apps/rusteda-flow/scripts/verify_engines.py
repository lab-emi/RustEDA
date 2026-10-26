#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded, black-box engine calibration; Python is only a development dependency.

No reference-tool source is read. Existing RustSys/RustSTA binaries are never
rebuilt. Missing external tools produce ``unverified`` and exit 3, never a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import time


RTL = """module lane_smoke(input clk, input a, input b, output reg y);
  always @(posedge clk) y <= a ^ b;
endmodule
"""
SDC = """create_clock -name core -period 10 [get_ports clk]
set_input_delay 0.2 -clock core [get_ports {a b}]
set_output_delay 0.2 -clock core [get_ports y]
set_input_transition 0.1 [get_ports {clk a b}]
set_clock_transition 0.1 [get_clocks core]
set_load 0.01 [get_ports y]
"""
MAX_INPUT_BYTES = 64 * 1024**2
MAX_LOG_BYTES = 64 * 1024**2
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256")
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def bounded_text(path: Path, limit: int = MAX_LOG_BYTES) -> str:
    if path.stat().st_size > limit:
        raise RuntimeError(f"file exceeds {limit} byte bound: {path}")
    return path.read_text(errors="replace")


def executable(value: str | None) -> Path | None:
    found = shutil.which(value) if value else None
    return Path(found).resolve() if found else None


def tcl_path(path: Path) -> str:
    # Tcl braced words preserve spaces and do not evaluate substitutions.
    if any(char in str(path) for char in "{}\n\r"):
        raise RuntimeError("verification paths cannot contain braces or newlines")
    return "{" + str(path) + "}"


def yosys_path(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Harness:
    def __init__(self, root: Path, args: argparse.Namespace):
        self.root, self.args = root, args
        self.receipts: list[dict] = []

    def run(self, label: str, command: list[str], *, file_limit_bytes: int = MAX_LOG_BYTES) -> dict:
        out = self.root / "receipts" / label
        out.mkdir(parents=True, exist_ok=False)
        env = os.environ.copy()
        env.update(RUST_TEST_THREADS="2", CARGO_BUILD_JOBS="1",
                   RAYON_NUM_THREADS=str(self.args.threads), LC_ALL="C")

        def limits() -> None:
            memory = self.args.memory_mib * 1024**2
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit_bytes, file_limit_bytes))
            seconds = self.args.timeout_seconds + 2
            resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))

        guarded = ["/usr/bin/time", "-v", "-o", str(out / "time.txt"),
                   "timeout", "--signal=TERM", "--kill-after=2s",
                   f"{self.args.timeout_seconds}s", *command]
        started = time.monotonic()
        with (out / "stdout.txt").open("wb") as stdout, (out / "stderr.txt").open("wb") as stderr:
            result = subprocess.run(guarded, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, preexec_fn=limits, check=False)
        timing = bounded_text(out / "time.txt")
        match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", timing)
        receipt = dict(label=label, command=command, guarded_command=guarded,
                       cwd=str(self.root), exit_code=result.returncode,
                       elapsed_seconds=time.monotonic() - started,
                       peak_rss_kib=int(match[1]) if match else None,
                       limits=dict(address_space_mib=self.args.memory_mib,
                                   timeout_seconds=self.args.timeout_seconds,
                                   output_bytes=file_limit_bytes, threads=self.args.threads,
                                   concurrent_processes=1),
                       stdout=str(out / "stdout.txt"), stderr=str(out / "stderr.txt"))
        write_json(out / "receipt.json", receipt)
        self.receipts.append(receipt)
        if receipt["peak_rss_kib"] is not None and receipt["peak_rss_kib"] >= 2 * 1024**2:
            raise RuntimeError(f"{label}: standard verification process exceeded 2 GiB RSS")
        return receipt

    def json_command(self, label: str, command: list[str], envelope: bool = False) -> dict:
        receipt = self.run(label, command)
        if receipt["exit_code"] != 0:
            raise RuntimeError(f"{label}: process exited {receipt['exit_code']}; see receipt")
        value = json.loads(bounded_text(Path(receipt["stdout"])))
        if envelope and (value.get("schema") != "rusteda.v1" or value.get("ok") is not True
                         or value.get("error") is not None):
            raise RuntimeError(f"{label}: unsuccessful rusteda.v1 envelope")
        return value


def ideal_projection(source: Path, destination: Path) -> dict:
    """Remove only wire-load estimation metadata from a verification-only copy.

    Cell functions and NLDM data are retained byte for byte. This is a small
    Liberty-format fixture transform, not a timing oracle. Fail on unexpected
    group structure instead of stripping arbitrary nested blocks.
    """
    original = bounded_text(source, MAX_INPUT_BYTES)
    if not original.isascii():
        raise RuntimeError("projection requires ASCII baseline Liberty; byte offsets must remain exact")
    pattern = re.compile(r"(?m)^([ \t]*)wire_load(?:_selection)?\s*\([^\n{}]*\)\s*\{")
    spans: list[tuple[int, int]] = []
    for match in pattern.finditer(original):
        end = original.find("}", match.end())
        if end < 0 or "{" in original[match.end():end]:
            raise RuntimeError("unexpected nested wire-load group; projection refused")
        spans.append((match.start(), end + 1))
    attributes = re.compile(
        r"(?m)^[ \t]*default_wire_load(?:_selection|_mode)?\s*:[^;\n]*;[ \t]*")
    spans.extend((match.start(), match.end()) for match in attributes.finditer(original))
    spans.sort()
    previous, pieces = 0, []
    removed = []
    for start, end in spans:
        if start < previous:
            raise RuntimeError("overlapping Liberty projection spans")
        pieces.append(original[previous:start])
        removed.append(dict(start_byte=start, end_byte=end,
                            sha256=hashlib.sha256(original[start:end].encode()).hexdigest()))
        previous = end
    pieces.append(original[previous:])
    projected = "".join(pieces)
    if re.search(r"\b(?:wire_load(?:_selection)?\s*\(|default_wire_load\w*\s*:)", projected):
        raise RuntimeError("unremoved wire-load declaration; projection refused")
    destination.write_text(projected)
    result = dict(purpose="verification_only_ideal_wires", original=str(source),
                  original_sha256=sha256(source), projected=str(destination),
                  projected_sha256=sha256(destination), removed_spans=removed,
                  cell_functions_and_nldm="unchanged", production_library="unmodified")
    write_json(destination.with_suffix(".projection.json"), result)
    return result


def sta_flow(harness: Harness, tag: str, tools: dict, lib: Path, netlist: Path, sdc: Path) -> dict:
    workspace = harness.root / (tag + "_workspace")
    common = ["--workspace", str(workspace), "--threads", str(harness.args.threads),
              "--format", "json", "--color", "never"]
    eda, sta = str(tools["rusteda"]), str(tools["ruststa"])
    init = harness.json_command(tag + "_init", [eda, *common, "init"], True)
    imported = harness.json_command(tag + "_import", [eda, *common, "import", "--base-rev",
        init["new_rev"], "--lib", str(lib), "--corner", "tt", "--netlist", str(netlist),
        "--sdc", str(sdc)], True)
    run = harness.json_command(tag + "_run", [sta, *common, "run", "--base-rev",
        imported["new_rev"], "--corner", "tt", "--backend", "cpu", "--precision", "f64",
        "--max-paths", "8", "--max-state-gib", "1"], True)
    report = harness.json_command(tag + "_report", [sta, *common, "report", "--rev",
        run["new_rev"], "--section", "summary", "--section", "checks", "--section",
        "notes", "--section", "unconstrained", "--limit", "8"], True)
    full = harness.json_command(tag + "_check_full", [sta, *common, "check", "--base-rev",
        run["new_rev"], "--method", "full", "--tolerance", str(harness.args.tolerance_ns)], True)
    both = harness.json_command(tag + "_check_both", [sta, *common, "check", "--base-rev",
        full["new_rev"], "--method", "both", "--tolerance", str(harness.args.tolerance_ns)], True)
    for check in (full, both):
        if check.get("verdict") != "agree" or check.get("mismatches"):
            raise RuntimeError(f"{tag}: STA check failed or undecided")
    return dict(status="passed", workspace=str(workspace), scope=run["scope"],
                completeness=run["completeness"], metrics=run["metrics"],
                diagnostics=run["diagnostics"], endpoints=report["sections"]["checks"]["rows"],
                full_check=dict(verdict=full["verdict"], max_abs_difference_ns=full["max_abs_difference_ns"]),
                both_check=dict(verdict=both["verdict"], max_abs_difference_ns=both["max_abs_difference_ns"]))


def yosys_script(lib: Path, netlist: Path, source: Path) -> str:
    return f"""read_liberty -ignore_miss_func -ignore_miss_dir -ignore_miss_data_latch {yosys_path(lib)}
read_verilog {yosys_path(netlist)}
hierarchy -top lane_smoke
proc
flatten
opt
rename lane_smoke gate
design -stash gate
design -reset
read_verilog -sv {yosys_path(source)}
proc
opt
rename lane_smoke gold
design -stash gold
design -copy-from gold -as gold gold
design -copy-from gate -as gate gate
equiv_make gold gate equiv
hierarchy -top equiv
equiv_simple -seq 2 -undef
equiv_status -assert
"""


def external_cec(harness: Harness, tag: str, yosys: Path | None,
                 lib: Path, netlist: Path, good: Path, bad: Path) -> dict:
    if yosys is None:
        return dict(status="unverified", reason="Yosys executable unavailable")
    records = []
    for control, source in (("good", good), ("bad", bad)):
        script = harness.root / (tag + "_cec_" + control + ".ys")
        script.write_text(yosys_script(lib, netlist, source))
        record = harness.run(tag + "_yosys_" + control, [str(yosys), "-Q", "-T", "-s", str(script)])
        stdout = bounded_text(Path(record["stdout"]))
        stderr = bounded_text(Path(record["stderr"]))
        if control == "good":
            passed = record["exit_code"] == 0 and "Equivalence successfully proven!" in stdout
        else:
            passed = (record["exit_code"] == 1 and "unproven $equiv cells" in stderr
                      and "0 are proven and 1 are unproven" in stdout)
        records.append(dict(control=control, expected_result_observed=passed, receipt=record["label"]))
    return dict(status="passed" if all(item["expected_result_observed"] for item in records) else "failed",
                method="Yosys equiv_simple -seq 2 -undef plus equiv_status -assert", controls=records,
                initial_state="no reset sequence or initialization assumptions")


def extract_endpoints(stdout: str, kind: str) -> dict[str, float]:
    begin, end = "RUSTLANE_" + kind + "_BEGIN", "RUSTLANE_" + kind + "_END"
    if stdout.count(begin) != 1 or stdout.count(end) != 1:
        raise RuntimeError("missing or duplicate OpenSTA report marker")
    section = stdout.split(begin, 1)[1].split(end, 1)[0]
    pattern = re.compile(rf"^(\S+)\s+\([^\n]*?\)\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+\((?:MET|VIOLATED)\)\s*$", re.M)
    rows = {match[1]: float(match[4]) for match in pattern.finditer(section)}
    if set(rows) != {"r0/D", "y"}:
        raise RuntimeError(f"unexpected OpenSTA endpoints: {sorted(rows)}")
    return rows


def external_sta(harness: Harness, tag: str, opensta: Path | None, lib: Path,
                 netlist: Path, sdc: Path, ideal: dict) -> dict:
    if opensta is None:
        return dict(status="unverified", reason="OpenSTA executable unavailable")
    if ideal["scope"].get("wire_model") != "ideal":
        raise RuntimeError("ideal-library projection did not produce ideal-wire RustSTA scope")
    script = harness.root / (tag + "_opensta.tcl")
    script.write_text(f"""read_liberty {tcl_path(lib)}
read_verilog {tcl_path(netlist)}
link_design lane_smoke
read_sdc {tcl_path(sdc)}
report_units
puts "RUSTLANE_SETUP_BEGIN"
report_checks -path_delay max -group_path_count 8 -endpoint_path_count 1 -format end -digits 12
puts "RUSTLANE_SETUP_END"
puts "RUSTLANE_HOLD_BEGIN"
report_checks -path_delay min -group_path_count 8 -endpoint_path_count 1 -format end -digits 12
puts "RUSTLANE_HOLD_END"
puts "RUSTLANE_ELECTRICAL_BEGIN"
report_check_types -max_slew -max_capacitance -max_fanout -violators
puts "RUSTLANE_ELECTRICAL_END"
check_setup -verbose -unconstrained_endpoints -multiple_clock -no_clock -loops -generated_clocks
exit
""")
    record = harness.run(tag + "_opensta", [str(opensta), "-no_init", "-no_splash", "-exit", str(script)])
    stdout = bounded_text(Path(record["stdout"]))
    stderr = bounded_text(Path(record["stderr"]))
    if record["exit_code"] or re.search(r"(?mi)^\s*(?:Error(?:\s+\d+)?:|ERROR:)", stdout + stderr):
        return dict(status="failed", reason="OpenSTA process or script error", receipt=record["label"])
    warnings = [line for line in (stdout + stderr).splitlines() if re.match(r"\s*Warning", line, re.I)]
    # The supplied baseline Liberty libraries use 1 ns. Never compare unnormalized units.
    if not re.search(r"(?mi)^\s*Time\s+1ns\s*$", stdout):
        return dict(status="unverified", reason="OpenSTA time unit was not verified as 1ns",
                    receipt=record["label"])
    reported = dict(setup=extract_endpoints(stdout, "SETUP"), hold=extract_endpoints(stdout, "HOLD"))
    differences = []
    for endpoint in ideal["endpoints"]:
        for kind in ("setup", "hold"):
            actual = endpoint[kind + "_slack_ns"]
            reference = reported[kind][endpoint["endpoint"]]
            if not math.isfinite(actual) or not math.isfinite(reference):
                raise RuntimeError("non-finite external timing result")
            differences.append(dict(endpoint=endpoint["endpoint"], kind=kind,
                                    ruststa_ns=actual, opensta_ns=reference,
                                    absolute_difference_ns=abs(actual - reference)))
    maximum = max(item["absolute_difference_ns"] for item in differences)
    status = "passed" if maximum <= harness.args.tolerance_ns else "failed"
    if warnings:
        status = "unverified"
    return dict(status=status, scope="same_verification_only_ideal_library_on_both_engines",
                corner="tt", wire_model="ideal", clock_model="ideal", parasitics="none",
                unconstrained_endpoints=ideal["completeness"]["unconstrained_endpoints"],
                tolerance_ns=harness.args.tolerance_ns, comparisons=differences,
                max_abs_difference_ns=maximum, warnings=warnings, receipt=record["label"])


def run_pdk(harness: Harness, tag: str, lib: Path, tools: dict, good: Path, bad: Path, sdc: Path) -> dict:
    started = time.monotonic()
    netlist, synth_report, check_report = (harness.root / (tag + suffix)
        for suffix in ("_mapped.v", "_synth.json", "_check_synth.json"))
    base = [str(tools["rustsys"]), "--threads", str(harness.args.threads),
            "--color", "never", "--progress", "off"]
    synth = harness.json_command(tag + "_synth", [*base, "synth", "--top", "lane_smoke",
        "--lib", str(lib), "--out", str(netlist), "--report", str(synth_report), "--print", "json", str(good)])
    check = harness.json_command(tag + "_check_synth", [*base, "check", "synth", "--top", "lane_smoke",
        "--lib", str(lib), "--netlist", str(netlist), "--report", str(check_report), "--print", "json",
        "--budget", str(harness.args.max_conflicts), "--model-budget", str(harness.args.max_conflicts),
        "--sweep-max-candidates", "256", str(good)])
    if check.get("verdict") != "equivalent" or any(check.get(key) for key in ("mismatches", "errors", "undecided", "internal_error")):
        raise RuntimeError(tag + ": synthesis check failed or undecided")
    native = sta_flow(harness, tag + "_original", tools, lib, netlist, sdc)
    projected_lib = harness.root / (tag + "_verification_ideal.lib")
    projection = ideal_projection(lib, projected_lib)
    if projection["original_sha256"] == projection["projected_sha256"] and native["scope"]["wire_model"] == "ideal":
        ideal = native
    else:
        ideal = sta_flow(harness, tag + "_ideal", tools, projected_lib, netlist, sdc)
    cec = external_cec(harness, tag, tools["yosys"], lib, netlist, good, bad)
    sta = external_sta(harness, tag, tools["opensta"], projected_lib, netlist, sdc, ideal)
    statuses = [cec["status"], sta["status"]]
    status = "failed" if "failed" in statuses else "unverified" if "unverified" in statuses else "passed"
    return dict(pdk=tag, status=status, source_lib_sha256=sha256(lib),
                synthesis=dict(status="passed", verdict=check["verdict"], cells=synth["cells"],
                               area=synth["area"], time_unit_ns=synth["time_unit_ns"]),
                original_library_sta=native, ideal_projection=projection, ideal_library_sta=ideal,
                external_cec=cec, external_sta=sta, elapsed_seconds=time.monotonic() - started)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("rustsys", "rusteda", "ruststa", "sky130-lib", "gf180-lib", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("--opensta", default=os.environ.get("OPENSTA", "sta"))
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--max-conflicts", type=int, default=10000)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--memory-mib", type=int, default=2048)
    parser.add_argument("--tolerance-ns", type=float, default=0.001)
    args = parser.parse_args()
    if not (1 <= args.timeout_seconds <= 180 and 64 <= args.memory_mib <= 2048
            and args.max_conflicts > 0 and math.isfinite(args.tolerance_ns) and args.tolerance_ns > 0):
        parser.error("invalid work budget; memory must stay within 64..2048 MiB")
    # Bound the fixture/projector/controller too; children additionally get CPU,
    # wall-clock and file limits in Harness.run.
    memory = args.memory_mib * 1024**2
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=False)
    summary = dict(schema="rustlane-engine-verification.v1", status="unverified", pdks=[],
                   limitation="Pinned RustSTA has no --spef import or --wire-model CLI; extracted-parasitic timing is unsupported.",
                   preflight=subprocess.run(["free", "-h"], text=True, capture_output=True, timeout=5, check=True).stdout,
                   memory_estimate=dict(objects=128, arcs=256, bytes_per_entry=256,
                     corners=1, threads=args.threads, graph_and_tags_mib_upper_bound=2 * args.threads,
                     parser_and_library_mib_upper_bound=640, process_headroom_mib=256,
                     fixture_projection_mib_upper_bound=256, concurrent_processes=1,
                     max_ranked_paths=8, max_sat_conflicts_per_obligation=args.max_conflicts,
                     max_sweep_candidates=256, all_paths_materialized=False))
    write_json(root / "preflight.json", summary)
    tools = {name: executable(getattr(args, name)) for name in ("rustsys", "rusteda", "ruststa", "yosys", "opensta")}
    harness = Harness(root, args)
    summary["tools"] = {}
    versions = dict(rustsys=["--version"], rusteda=["--version"], ruststa=["--version"],
                    yosys=["-V"], opensta=["-version"])
    for name, tool in tools.items():
        if tool is None:
            summary["tools"][name] = dict(status="unavailable")
            continue
        receipt = harness.run(name + "_version", [str(tool), *versions[name]])
        summary["tools"][name] = dict(status="available" if receipt["exit_code"] == 0 else "unusable",
            path=str(tool), entrypoint_sha256=sha256(tool),
            version=bounded_text(Path(receipt["stdout"])).strip(), receipt=receipt["label"])
        if receipt["exit_code"]:
            tools[name] = None
    good, bad, sdc = (root / name for name in ("lane_smoke.sv", "lane_bad.sv", "lane_smoke.sdc"))
    good.write_text(RTL)
    bad.write_text(RTL.replace("a ^ b", "a & b"))
    sdc.write_text(SDC)
    try:
        if not all(tools[name] for name in ("rustsys", "rusteda", "ruststa")):
            summary["reason"] = "required Rust engine executable unavailable"
        else:
            for tag, value in (("sky130", args.sky130_lib), ("gf180", args.gf180_lib)):
                lib = Path(value).resolve()
                if not lib.is_file():
                    summary["pdks"].append(dict(pdk=tag, status="unverified", reason="baseline Liberty unavailable"))
                    continue
                try:
                    summary["pdks"].append(run_pdk(harness, tag, lib, tools, good, bad, sdc))
                except (RuntimeError, ValueError, KeyError, OSError) as error:
                    summary["pdks"].append(dict(pdk=tag, status="failed", error=str(error)))
            statuses = [item["status"] for item in summary["pdks"]]
            summary["status"] = "failed" if "failed" in statuses else "unverified" if "unverified" in statuses else "passed"
    finally:
        summary["receipts"] = harness.receipts
        known_peaks = [item["peak_rss_kib"] for item in harness.receipts if item["peak_rss_kib"] is not None]
        summary["peak_process_rss_kib"] = max(known_peaks) if known_peaks else None
        summary["rss_measurement_status"] = "complete" if len(known_peaks) == len(harness.receipts) else "partial"
        write_json(root / "summary.json", summary)
    print(json.dumps(dict(status=summary["status"], summary=str(root / "summary.json"),
                          peak_process_rss_kib=summary["peak_process_rss_kib"])))
    return dict(passed=0, failed=1, unverified=3)[summary["status"]]


if __name__ == "__main__":
    sys.exit(main())

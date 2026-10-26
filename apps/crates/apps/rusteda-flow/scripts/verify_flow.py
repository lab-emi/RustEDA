#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Independently exercise the actual RustEDA flow executable and its real EDA engines.

Linux, Python 3.11+, /usr/bin/time, and timeout are required. All executable and
baseline-library locations are explicit caller arguments. Reference tools are
black-box executables only; no external implementation source is inspected.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import resource
import shutil
import stat
import subprocess
import sys
import time

from verify_engines import (Harness, RTL, SDC, bounded_text, executable, external_cec,
                            external_sta, ideal_projection, sha256, write_json)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class FlowHarness(Harness):
    def __init__(self, root: Path, args: argparse.Namespace, controller: Path):
        super().__init__(root, args)
        self.controller = controller

    def run(self, label: str, command: list[str], **kwargs) -> dict:
        # Older controllers install FSIZE=max(memory_mib MiB,log cap). Keep the
        # enclosing finite hard cap compatible, rather than provoking EPERM.
        if Path(command[0]) == self.controller:
            kwargs["file_limit_bytes"] = self.args.memory_mib * 1024**2
        return super().run(label, command, **kwargs)

    def lane(self, label: str, workspace: Path, arguments: list[str],
             expected_exit: int = 0) -> dict:
        receipt = self.run(label, [str(self.controller), "flow", "--workspace", str(workspace), *arguments])
        require(receipt["exit_code"] == expected_exit,
                f"{label}: expected exit {expected_exit}, observed {receipt['exit_code']}")
        value = json.loads(bounded_text(Path(receipt["stdout"])))
        require(value.get("schema") == "rustlane.v1", label + ": missing rustlane.v1 schema")
        require(value.get("ok") is (expected_exit == 0), label + ": incorrect success telemetry")
        return value


def checkpoint_snapshot(workspace: Path) -> dict:
    """Bounded filesystem identity check of checkpoints and existing step outputs."""
    result = {"HEAD": sha256(workspace / "HEAD")}
    visited = 0
    for directory in (workspace / "revisions", workspace / "steps"):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            visited += 1
            require(visited <= 4096, "workspace verification traversal budget exhausted")
            if path.is_file():
                require(path.stat().st_size <= 128 * 1024**2, "unexpected oversized tiny-workspace file")
                result[str(path.relative_to(workspace))] = dict(sha256=sha256(path),
                    bytes=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns,
                    mode=stat.S_IMODE(path.stat().st_mode))
    return result


def verify_file(identity: dict, workspace: Path) -> None:
    path = Path(identity["path"]).resolve()
    require(path.is_relative_to(workspace), "artifact escapes its test workspace")
    require(path.is_file() and path.stat().st_size == identity["bytes"], "artifact size mismatch")
    require(sha256(path) == identity["sha256"], "artifact hash mismatch")


def config_for(harness: FlowHarness, pdk: str, lib: Path, tools: dict,
               source: Path, sdc: Path) -> dict:
    return dict(schema_version=1, design_name="lane_smoke", pdk=pdk,
                verilog_files=[str(source)], sdc_file=str(sdc),
                libraries=[dict(path=str(lib), corner="tt")], synthesis_corner="tt",
                flow="synthesis_sta", tools={name: str(tools[name]) for name in
                    ("rustsys", "rusteda", "ruststa")},
                limits=dict(threads=harness.args.threads, memory_mib=harness.args.memory_mib,
                    max_state_gib=1, max_paths=8, timeout_secs=30,
                    max_conflicts=harness.args.max_conflicts, max_file_bytes=64 * 1024**2,
                    max_input_bytes=128 * 1024**2, max_log_bytes=8 * 1024**2))


def successful_flow(harness: FlowHarness, tag: str, config: dict,
                    verify_controls: bool = True) -> tuple[dict, dict, Path]:
    cfg, workspace = harness.root / (tag + ".json"), harness.root / (tag + "_workspace")
    write_json(cfg, config)
    initialized = harness.lane(tag + "_init", workspace,
        ["init", "--config", str(cfg), "--base-rev", "empty"])
    require(initialized.get("new_rev") == "rev0", tag + ": fresh init did not return rev0")
    run = harness.lane(tag + "_run", workspace, ["run", "--base-rev", "rev0"])
    require(run.get("executed_steps") == 3 and run.get("cached_steps") == 0,
            tag + ": initial run did not execute exactly three steps")
    report = harness.lane(tag + "_report", workspace, ["report"])
    require(report.get("rev") == run.get("new_rev"), tag + ": report revision mismatch")
    require(report["summary"]["complete"] is True, tag + ": incomplete flow")
    stages = {item["id"]: item for item in report["stages"]}
    require(set(stages) == {"synthesis", "equivalence", "sta_pre"}, tag + ": incorrect step set")
    require(all(item["status"] == "passed" and item["attempts"] == 1 for item in stages.values()),
            tag + ": incorrect stage status/attempt count")
    for item in report["inputs"]:
        verify_file(item["captured"], workspace)
    processes = []
    for stage in stages.values():
        for artifact in stage["result"]["artifacts"]:
            verify_file(artifact["file"], workspace)
        processes.extend(stage["result"]["processes"])
    require(len(processes) == 6, tag + ": unexpected native child count")
    for process in processes:
        arguments = process["arguments"]
        require("--threads" in arguments and arguments[arguments.index("--threads") + 1] == str(harness.args.threads),
                tag + ": a real native child lacks its explicit thread count")
        require(process["exit_code"] == 0 and not process["timed_out"] and not process["log_limit_exceeded"],
                tag + ": child did not complete normally")
        require(process["peak_rss_kib"] is not None and 0 < process["peak_rss_kib"] < 2 * 1024**2,
                tag + ": child RSS unmeasured or exceeded standard-test bound")
    summary, timing = report["summary"], stages["sta_pre"]["result"]["evidence"]
    require(summary["equivalence_verdict"] == "equivalent", tag + ": CEC did not pass")
    require(summary["timing_verification"]["verdict"] == "agree" and
            summary["timing_verification"]["method"] == "full", tag + ": full timing check did not agree")
    require(summary["analysis_scope"] == timing["scope"] and
            summary["timing_metrics"] == timing["metrics"] and
            summary["timing_completeness"] == timing["completeness"], tag + ": timing envelope changed in transit")
    require(summary["unconstrained_endpoints"] == 0 and summary["analysis_scope"]["corner"] == "tt",
            tag + ": incorrect scope or timing completeness")
    require(summary["drc_status"] == "unchecked" and summary["lvs_status"] == "unchecked"
            and summary["extracted_parasitic_sta"] == "unsupported", tag + ": unsupported physical scope hidden")
    netlist_identity = next(item["file"] for item in stages["synthesis"]["result"]["artifacts"] if item["kind"] == "netlist")
    netlist = Path(netlist_identity["path"])
    result = dict(status="passed", revision=report["rev"], config=str(cfg), workspace=str(workspace),
        executed_steps=run["executed_steps"], cached_steps=run["cached_steps"],
        native_child_processes=len(processes), mapped_netlist=netlist_identity,
        summary=summary, diagnostics=timing["diagnostics"])
    if verify_controls:
        before = checkpoint_snapshot(workspace)
        resumed = harness.lane(tag + "_resume", workspace, ["run", "--base-rev", report["rev"]])
        require(resumed.get("executed_steps") == 0 and resumed.get("cached_steps") == 3,
                tag + ": resume executed work")
        require(resumed.get("new_rev") == report["rev"] and checkpoint_snapshot(workspace) == before,
                tag + ": zero-work resume mutated a checkpoint or step artifact")
        after_resume = harness.lane(tag + "_after_resume", workspace, ["report"])
        require(after_resume["stages"] == report["stages"], tag + ": resume changed attempts or native receipts")
        require(resumed["summary"]["recorded_run_wall_seconds"] == after_resume["summary"]["recorded_run_wall_seconds"],
                tag + ": zero-work resume reports an unpersisted recorded_run_wall_seconds")
        result["resume"] = dict(status="passed", executed_steps=0, cached_steps=3,
            checkpoint_and_artifact_identity="unchanged", new_rev=resumed["new_rev"])
        stale = harness.lane(tag + "_stale", workspace, ["run", "--base-rev", "rev0"], 4)
        require(stale.get("error", {}).get("code") == "REVISION_CONFLICT" and
                checkpoint_snapshot(workspace) == before, tag + ": stale base changed the workspace")
        result["stale_base"] = dict(status="passed", exit_code=4, error=stale["error"], workspace_mutated=False)
        captured = Path(next(item["captured"]["path"] for item in report["inputs"] if item["original"].endswith(".sv")))
        saved, mode = captured.read_bytes(), stat.S_IMODE(captured.stat().st_mode)
        try:
            captured.chmod(mode | stat.S_IWUSR)
            captured.write_bytes(saved + b"// deliberate captured-input identity control\n")
            changed = harness.lane(tag + "_changed_input", workspace,
                ["run", "--base-rev", report["rev"]], 1)
            require(changed.get("error", {}).get("code") == "ARTIFACT_CHANGED" and
                    checkpoint_snapshot(workspace) == before, tag + ": changed captured input was not rejected before mutation")
            result["input_identity"] = dict(status="passed", scope="deliberately modified captured RTL",
                exit_code=1, error=changed["error"], checkpoint_mutated=False)
        finally:
            captured.write_bytes(saved)
            captured.chmod(mode)
        require(sha256(captured) == next(item["captured"]["sha256"] for item in report["inputs"]
            if item["captured"]["path"] == str(captured)), tag + ": control did not restore captured input")
    return result, timing, netlist


def timing_endpoints(harness: FlowHarness, tag: str, timing: dict, ruststa: Path) -> dict:
    report = harness.json_command(tag + "_native_endpoint_report", [str(ruststa), "--workspace",
        timing["child_workspace"], "--threads", str(harness.args.threads), "--format", "json",
        "report", "--rev", timing["child_revision"], "--section", "checks", "--limit", "8"], True)
    return dict(scope=timing["scope"], completeness=timing["completeness"],
                endpoints=report["sections"]["checks"]["rows"])


def negative_flow(harness: FlowHarness, tag: str, config: dict, expected_step: str) -> dict:
    cfg, workspace = harness.root / (tag + ".json"), harness.root / (tag + "_workspace")
    write_json(cfg, config)
    harness.lane(tag + "_init", workspace, ["init", "--config", str(cfg), "--base-rev", "empty"])
    failed = harness.lane(tag + "_run", workspace, ["run", "--base-rev", "rev0"], 1)
    report = harness.lane(tag + "_report", workspace, ["report"])
    require(failed.get("status") == "failed" and failed.get("step") == expected_step,
            tag + ": negative control did not fail at its intended step")
    require(report["summary"]["complete"] is False, tag + ": negative control was reported complete")
    require(all(report["summary"][key] is None for key in
            ("analysis_scope", "timing_metrics", "timing_verification", "unconstrained_endpoints")),
            tag + ": fabricated timing telemetry after failed timing/engine availability")
    stage = next(item for item in report["stages"] if item["id"] == expected_step)
    require(stage["status"] == "failed", tag + ": failed step has a pass claim")
    diagnostics = []
    for artifact in stage["result"]["artifacts"]:
        verify_file(artifact["file"], workspace)
        if artifact["kind"].endswith("_stdout"):
            try:
                value = json.loads(bounded_text(Path(artifact["file"]["path"])))
                diagnostics.extend(value.get("diagnostics", []))
            except ValueError:
                pass
    return dict(status="passed", exit_code=1, rejected_at=expected_step,
                controller_error=failed["error"], summary=report["summary"],
                raw_diagnostics=diagnostics, native_processes=stage["result"]["processes"],
                artifacts=stage["result"]["artifacts"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("rustsys", "rusteda", "ruststa", "sky130-lib", "gf180-lib", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--yosys")
    parser.add_argument("--opensta")
    parser.add_argument("--summary", help="optional copy of the completed summary JSON")
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--memory-mib", type=int, default=2048)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--max-conflicts", type=int, default=10000)
    parser.add_argument("--tolerance-ns", type=float, default=0.001)
    args = parser.parse_args()
    if not (512 <= args.memory_mib <= 2048 and 30 <= args.timeout_seconds <= 180
            and 0 < args.max_conflicts <= 2000000 and math.isfinite(args.tolerance_ns) and args.tolerance_ns > 0):
        parser.error("invalid finite resource/work budgets")
    memory = args.memory_mib * 1024**2
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summary = dict(schema="rustlane-flow-verification.v1", status="unverified",
        date_utc=datetime.now(timezone.utc).isoformat(), cases=[], controls={},
        scope="tiny sequential controller orchestration; native original PDK scopes plus explicit ideal-wire external calibration",
        limitation="No physical-tool, DRC/LVS, SPEF or extracted-parasitic STA validation is claimed.",
        preflight=subprocess.run(["free", "-h"], text=True, capture_output=True, timeout=5, check=True).stdout,
        memory_estimate=dict(objects=128, arcs=256, bytes_per_entry=256, corners=1,
            threads=args.threads, max_graph_tag_mib=2 * args.threads,
            parsed_library_mib_upper_bound=640, controller_snapshot_mib_upper_bound=144,
            process_headroom_mib=256, projection_controller_mib_upper_bound=256,
            concurrent_native_children=1, max_paths=8, max_conflicts=args.max_conflicts,
            all_paths_materialized=False), tools={})
    write_json(root / "preflight.json", summary)
    tools = {name: executable(getattr(args, name)) for name in ("rustsys", "rusteda", "ruststa", "yosys", "opensta")}
    if any(tools[name] is None for name in ("rustsys", "rusteda", "ruststa")):
        summary["reason"] = "required controller/engine executable unavailable"
        write_json(root / "summary.json", summary)
        print(json.dumps(dict(status="unverified", summary=str(root / "summary.json"))))
        return 3
    freeze = root / "frozen"
    freeze.mkdir()
    controller = freeze / "rusteda"
    producer_hash = sha256(tools["rusteda"])
    shutil.copy2(tools["rusteda"], controller)
    require(sha256(controller) == producer_hash, "controller changed while copying; rerun with a stable producer")
    harness = FlowHarness(root, args, controller)
    versions = dict(rustsys=["--version"], rusteda=["--version"],
                    ruststa=["--version"], yosys=["-V"], opensta=["-version"])
    for name, tool in tools.items():
        if tool is None:
            summary["tools"][name] = dict(status="unavailable")
            continue
        record = harness.run(name + "_version", [str(controller if name == "rusteda" else tool), *versions[name]])
        summary["tools"][name] = dict(path=str(tool), sha256=producer_hash if name == "rusteda" else sha256(tool),
            version=bounded_text(Path(record["stdout"])).strip(), exit_code=record["exit_code"])
    summary["controller_frozen_sha256"] = producer_hash
    source, bad, sdc = (root / name for name in ("lane_smoke.sv", "lane_bad.sv", "lane_smoke.sdc"))
    source.write_text(RTL)
    bad.write_text(RTL.replace("a ^ b", "a & b"))
    sdc.write_text(SDC)
    try:
        configs, native = {}, {}
        for tag, pdk, library in (("sky130", "sky130_fd_sc_hd", args.sky130_lib),
                ("gf180", "gf180mcu_fd_sc_mcu7t5v0", args.gf180_lib)):
            lib = Path(library).resolve()
            require(lib.is_file() and lib.stat().st_size <= 64 * 1024**2, tag + ": missing/oversized baseline Liberty")
            config = config_for(harness, pdk, lib, tools, source, sdc)
            configs[tag] = config
            case, timing, netlist = successful_flow(harness, tag, config)
            expected_wire = "wireload_lumped" if tag == "sky130" else "ideal"
            require(timing["scope"]["wire_model"] == expected_wire, tag + ": original library wire scope changed")
            reference = root / (tag + "_direct_mapped.v")
            harness.json_command(tag + "_direct_synthesis", [str(tools["rustsys"]), "--threads",
                str(args.threads), "synth", "--top", "lane_smoke", "--lib", str(lib), "--out", str(reference),
                "--report", str(root / (tag + "_direct_synthesis.json")), "--print", "json", str(source)])
            require(sha256(reference) == case["mapped_netlist"]["sha256"], tag + ": controller changed mapped netlist identity")
            case["direct_mapped_identity"] = dict(status="passed", sha256=sha256(reference))
            case["external_cec"] = external_cec(harness, tag, tools["yosys"], lib, netlist, source, bad)
            native[tag] = (case, timing, netlist)
            summary["cases"].append(case)
        sky_lib = Path(args.sky130_lib).resolve()
        projected = root / "sky130_verification_ideal.lib"
        projection = ideal_projection(sky_lib, projected)
        ideal_config = json.loads(json.dumps(configs["sky130"]))
        ideal_config["libraries"] = [dict(path=str(projected), corner="tt")]
        ideal_case, ideal_timing, ideal_netlist = successful_flow(harness, "sky130_ideal", ideal_config, False)
        ideal_case["ideal_projection"] = projection
        ideal_case["external_sta"] = external_sta(harness, "sky130_ideal", tools["opensta"], projected,
            ideal_netlist, sdc, timing_endpoints(harness, "sky130_ideal", ideal_timing, tools["ruststa"]))
        summary["cases"].append(ideal_case)
        gf_case, gf_timing, gf_netlist = native["gf180"]
        gf_case["external_sta"] = external_sta(harness, "gf180", tools["opensta"], Path(args.gf180_lib).resolve(),
            gf_netlist, sdc, timing_endpoints(harness, "gf180", gf_timing, tools["ruststa"]))
        missing_config = json.loads(json.dumps(configs["sky130"]))
        missing_config["tools"]["rustsys"] = str(root / "deliberately_unavailable_rustsys")
        missing = negative_flow(harness, "missing_tool", missing_config, "synthesis")
        require(missing["controller_error"]["code"] == "TOOL_UNAVAILABLE" and
                missing["summary"]["peak_direct_child_rss_kib"] is None and
                missing["summary"]["equivalence_verdict"] is None and not missing["native_processes"],
                "missing tool control fabricated an execution/metric/verdict")
        summary["controls"]["missing_tool"] = missing
        unsupported_config = json.loads(json.dumps(configs["sky130"]))
        unsupported_sdc = root / "unsupported.sdc"
        unsupported_sdc.write_text(SDC + "set_propagated_clock [get_clocks core]\n")
        unsupported_config["sdc_file"] = str(unsupported_sdc)
        unsupported = negative_flow(harness, "unsupported_sdc", unsupported_config, "sta_pre")
        require(any(item.get("code") == "D0010" and "set_propagated_clock" in item.get("message", "")
                    for item in unsupported["raw_diagnostics"]), "unsupported SDC diagnostic was not retained")
        summary["controls"]["unsupported_sdc"] = unsupported
        external_statuses = [item[key]["status"] for item in summary["cases"]
            for key in ("external_cec", "external_sta") if key in item]
        summary["status"] = "failed" if "failed" in external_statuses else "unverified" if "unverified" in external_statuses else "passed"
    except (RuntimeError, ValueError, KeyError, OSError, StopIteration) as error:
        summary["status"], summary["error"] = "failed", str(error)
    finally:
        known = [item["peak_rss_kib"] for item in harness.receipts if item["peak_rss_kib"] is not None]
        summary["peak_guarded_command_rss_kib"] = max(known) if known else None
        summary["rss_measurement_status"] = "complete" if len(known) == len(harness.receipts) else "partial"
        summary["elapsed_seconds"] = time.monotonic() - started
        summary["receipts"] = harness.receipts
        write_json(root / "summary.json", summary)
        if args.summary:
            write_json(Path(args.summary).resolve(), summary)
    print(json.dumps(dict(status=summary["status"], summary=str(root / "summary.json"),
                         peak_guarded_command_rss_kib=summary["peak_guarded_command_rss_kib"],
                         error=summary.get("error"))))
    return dict(passed=0, failed=1, unverified=3)[summary["status"]]


if __name__ == "__main__":
    sys.exit(main())

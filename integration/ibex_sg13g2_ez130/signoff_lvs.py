#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Fresh, bounded device-LVS signoff for either RTL2GDS producer.

The rusteda.device-lvs-inputs.v1 manifest names the actual final powered Verilog,
GDS, qualified extraction deck, original device CDL, explicit interfaces and
external tools. No saved netlist JSON, reference, database or verdict is an input.
OUTPUT/netlist.json retains the exact external structural parser output.

Controller scratch is <32 MiB. Preparation is bounded to 64 MiB JSON/100000
instances and 32 MiB CDL. The hierarchical engine previously measured <1.5 GiB
on this Ibex; admission estimates include geometry, parser and thread scratch.
Each run owns a <=4 GiB cgroup, independent deadline, and exact-scope KILL guard.
No paths, device expansions or process lists are materialized without caps.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
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


MIB = 1024**2
SCHEMA = "rusteda.device-lvs-inputs.v1"


def read_json(path, limit):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"missing or oversized JSON: {path}")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"JSON grew beyond byte budget: {path}")
    return json.loads(raw)


def identity(path, limit=4 * 1024**3):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    if not path.is_file() or before.st_size > limit:
        raise ValueError(f"input byte budget exceeded: {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(f"input changed while hashing: {path}")
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    temporary.replace(path)


def handle(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("top/macro must be an ASCII Verilog identifier")
    return value


def path_value(value):
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        raise ValueError("invalid input path")
    return str(Path(value).resolve(strict=True))


def limits(value):
    result = dict(memory_mib=4096, swap_mib=512, deadline_seconds=1200, threads=2)
    if not isinstance(value, dict) or set(value) - set(result):
        raise ValueError("unknown resource limit")
    result.update(value)
    bounds = dict(memory_mib=(1024, 4096), swap_mib=(0, 1024),
                  deadline_seconds=(1, 3600), threads=(1, 4))
    for key, (low, high) in bounds.items():
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"resource budget {key} must be {low}..{high}")
    return result


def prepare(manifest_path, output):
    raw = read_json(manifest_path, 2 * MIB)
    required = {"schema", "top", "gds", "powered_netlist", "deck", "cdl", "interfaces",
                "macro", "macro_count", "macro_qualification", "tools"}
    if not isinstance(raw, dict) or not required <= set(raw) or set(raw) - required - {"limits", "expected_hashes"}:
        raise ValueError("expected complete device-LVS manifest fields")
    if raw["schema"] != SCHEMA:
        raise ValueError("unsupported device-LVS manifest schema")
    job = {key: raw[key] for key in ("top", "macro", "macro_count")}
    handle(job["top"])
    handle(job["macro"])
    if type(job["macro_count"]) is not int or not 0 <= job["macro_count"] <= 16:
        raise ValueError("macro-count budget is 0..16")
    job["limits"] = limits(raw.get("limits", {}))
    caps = dict(gds=256 * MIB, powered_netlist=32 * MIB, deck=4 * MIB,
                interfaces=2 * MIB, macro_qualification=16 * MIB)
    sources = {"manifest": identity(manifest_path, 2 * MIB)}
    for key, cap in caps.items():
        if key == "macro_qualification" and raw[key] is None:
            if job["macro_count"]:
                raise ValueError("real SRAM requires original-device qualification")
            job[key] = None
            continue
        job[key] = path_value(raw[key])
        sources[key] = identity(job[key], cap)
    if not isinstance(raw["cdl"], list) or not 1 <= len(raw["cdl"]) <= 16:
        raise ValueError("1..16 original/prepared CDL sources required")
    job["cdl"] = [path_value(p) for p in raw["cdl"]]
    if len(set(job["cdl"])) != len(job["cdl"]):
        raise ValueError("duplicate CDL source")
    for index, path in enumerate(job["cdl"]):
        sources[f"cdl_{index}"] = identity(path, 32 * MIB)
    cdl_bytes = sum(sources[f"cdl_{i}"]["bytes"] for i in range(len(job["cdl"])))
    if cdl_bytes > 32 * MIB:
        raise ValueError("total CDL byte budget exceeded")
    if not isinstance(raw["tools"], dict) or set(raw["tools"]) != {"appimage", "yosys", "time"}:
        raise ValueError("explicit appimage/yosys/time tools required")
    job["tools"] = {key: path_value(value) for key, value in raw["tools"].items()}
    for name, path in job["tools"].items():
        if not os.access(path, os.X_OK):
            raise ValueError(f"missing executable tool: {name}")
        sources["tool_" + name] = identity(path)
    expected = raw.get("expected_hashes", {})
    if not isinstance(expected, dict) or set(expected) - set(sources):
        raise ValueError("unknown expected input hash key")
    for name, digest in expected.items():
        if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest) or sources[name]["sha256"] != digest:
            raise ValueError(f"input hash mismatch: {name}")
    if job["macro_qualification"] is not None:
        qualification = read_json(job["macro_qualification"], 16 * MIB)
        if (qualification.get("status") != "qualified" or qualification.get("macro") != job["macro"]
                or qualification.get("extraction_errors") != []
                or qualification.get("all_circuits_match") is not True
                or qualification.get("top_pin_count") != 193):
            raise ValueError("incomplete original SRAM qualification")
        frozen = qualification.get("frozen", {})
        if not isinstance(frozen, dict) or not 1 <= len(frozen) <= 16:
            raise ValueError("qualification requires bounded frozen deck/CDL identities")
        supplied = {sources["deck"]["sha256"]} | {sources[f"cdl_{i}"]["sha256"] for i in range(len(job["cdl"]))}
        if sources["deck"]["sha256"] not in frozen.values() or not set(frozen.values()) <= supplied:
            raise ValueError("qualified deck or SRAM CDL is not the supplied source")
        job["qualified_frozen_hashes"] = frozen
    # The controller retains snapshots of its first-party orchestration sources;
    # the Apache PDK remains an input, never copied into repository source.
    snapshots = output / "inputs"
    snapshots.mkdir()
    directory = Path(__file__).resolve().parent
    job["helpers"] = {}
    for name in ("signoff_lvs.py", "build_lvs_reference.py", "check_lvs.py"):
        source = directory / name
        source_id = identity(source, 2 * MIB)
        destination = snapshots / name
        shutil.copyfile(source, destination)
        if identity(destination)["sha256"] != source_id["sha256"]:
            raise ValueError("first-party source snapshot changed")
        sources["source_" + name] = source_id
        sources["snapshot_" + name] = identity(destination)
        job["helpers"][name] = str(destination)
    sources["python"] = identity(sys.executable)
    owned = inherited_owner()
    if owned:
        sources["worker_ownership"] = identity(owned["ownership_record"], 2 * MIB)
        sources["producer_manifest"] = owned["producer_manifest"]
    for spec in sources.values():
        if Path(spec["path"]).is_relative_to(output) and not Path(spec["path"]).is_relative_to(snapshots):
            raise ValueError("signoff inputs cannot be generated inside its output workspace")
    # Calibrated on the Ibex SG13G2 run11 layout: a 50.9 MB GDS measured a 1.86 GB LVS cgroup
    # peak (about 21 B per GDS byte above the base) against a 4.23 GB estimate at 64 B/B;
    # 32 B/B keeps 1.4x headroom. The cgroup memory_mib cap still bounds the actual run.
    estimate = (768 * MIB + 32 * sources["gds"]["bytes"] + 4 * sources["powered_netlist"]["bytes"]
                + 12 * cdl_bytes + 128 * MIB * (job["limits"]["threads"] - 1))
    if estimate > job["limits"]["memory_mib"] * MIB:
        raise ValueError(f"BUDGET_EXCEEDED: estimated peak {estimate} exceeds requested memory")
    meminfo = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(meminfo["MemAvailable"].split()[0]) * 1024
    if available < estimate + 512 * MIB:
        raise ValueError("insufficient available host memory for estimated signoff peak")
    job.update(output=str(output), sources=sources, python=str(Path(sys.executable).resolve()),
               estimated_peak_bytes=estimate, host_mem_available_bytes=available,
               schema=SCHEMA, inherited_owner=owned)
    return job


def source_changes(sources):
    changes = []
    for name, spec in sources.items():
        try:
            if identity(spec["path"]) != spec:
                changes.append(name)
        except (OSError, ValueError):
            changes.append(name)
    return changes


def cgroup_sample(group):
    record = {}
    for name in ("memory.peak", "memory.current", "memory.swap.current"):
        try:
            record[name] = int((Path("/sys/fs/cgroup" + group) / name).read_text().strip()) if group else None
        except (OSError, ValueError):
            record[name] = None
    return record


def current_cgroup():
    return next((line.split(":", 2)[2] for line in Path("/proc/self/cgroup").read_text().splitlines()
                 if line.startswith("0::")), None)


def cgroup_pids(group):
    """Bounded inventory for the owned, serial producer scope; never global ps."""
    path = Path("/sys/fs/cgroup" + group) / "cgroup.procs"
    if path.stat().st_size > MIB:
        raise ValueError("owned cgroup PID inventory byte budget exceeded")
    with path.open() as stream:
        pids = set()
        for line in stream:
            if len(pids) >= 4096:
                raise ValueError("owned cgroup PID inventory count budget exceeded")
            pids.add(int(line.strip()))
    return pids


def inherited_owner():
    """Admit only the explicit RustEDA producer ownership protocol."""
    request = os.environ.get("RUSTEDA_WORKER_ID")
    owner = os.environ.get("RUSTEDA_OWNERSHIP")
    if request is None and owner is None:
        return None
    if not request or not re.fullmatch("[a-f0-9]{64}", request) or not owner or not Path(owner).is_absolute():
        raise ValueError("invalid inherited worker identity")
    location = Path(owner).resolve(strict=True) / "native.json"
    record = read_json(location, 2 * MIB)
    if record.get("request_id") != request or record.get("implementation") != "native":
        raise ValueError("inherited native ownership record differs")
    producer = record.get("manifest", {})
    if not isinstance(producer, dict) or identity(producer["path"]) != producer:
        raise ValueError("inherited producer manifest identity differs")
    unit = "rusteda-worker-" + request[:24] + ".scope"
    probe = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "Description", "-p", "ControlGroup"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, timeout=5, check=False)
    fields = dict(line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line)
    group = current_cgroup()
    if (probe.returncode or fields.get("ActiveState") != "active"
            or fields.get("Description") != "RustEDA worker " + request
            or not group or fields.get("ControlGroup") != group):
        raise ValueError("helper is not in the exact owned producer scope")
    root = Path("/sys/fs/cgroup" + group)
    memory = (root / "memory.max").read_text().strip()
    swap = (root / "memory.swap.max").read_text().strip()
    if memory == "max" or not 1024 * MIB <= int(memory) <= 16384 * MIB or swap == "max":
        raise ValueError("inherited producer requires bounded memory and swap")
    return dict(request_id=request, ownership_record=str(location), producer_manifest=producer, unit=unit, cgroup=group,
                memory_max_bytes=int(memory), swap_max_bytes=int(swap))


def run_stage(job, name, command, output_path=None):
    output = Path(job["output"])
    argv = [job["tools"]["time"], "-v", "-o", str(output / (name + ".time")), *command]
    save(output / (name + "-command.json"), argv)
    tick = time.monotonic()
    with (output_path or output / (name + ".log")).open("xb") as log:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                cwd=output, env={**os.environ, "OMP_NUM_THREADS": str(job["limits"]["threads"])})
    record = dict(name=name, exit_code=result.returncode, wall_seconds=time.monotonic() - tick)
    job.setdefault("stages", []).append(record)
    if result.returncode:
        raise RuntimeError(f"{name} failed with exit {result.returncode}")
    return record


def quoted_yosys(path):
    # A Yosys script string is not a shell word; escape it for that grammar.
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def execute(job):
    """Worker, kept inside the scope until its final memory.peak read."""
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * MIB, 256 * MIB))
    output = Path(job["output"])
    report = dict(status="unverified", complete=False, stages=[], error=None)
    job["stages"] = report["stages"]
    tick = time.monotonic()
    try:
        if source_changes(job["sources"]):
            raise ValueError("source identity changed before worker start")
        script = output / "read.ys"
        script.write_text("read_verilog " + quoted_yosys(job["powered_netlist"]) + "\n"
                          + "hierarchy -top " + job["top"] + "\n"
                          + "write_json " + quoted_yosys(output / "netlist.json") + "\n")
        run_stage(job, "yosys", [job["tools"]["yosys"], "-s", str(script)])
        identity(output / "netlist.json", 64 * MIB)
        command = [job["python"], job["helpers"]["build_lvs_reference.py"],
                   "--workspace", str(output / "reference"), "--base-rev", "empty",
                   "--json", str(output / "netlist.json"), "--top", job["top"],
                   "--interfaces", job["interfaces"]]
        for path in job["cdl"]:
            command.extend(["--cdl", path])
        run_stage(job, "reference", command)
        command = [job["tools"]["appimage"], "klayout", "-b", "-r", job["deck"]]
        switches = dict(threads=str(job["limits"]["threads"]), run_mode="deep", no_net_names="false",
                        spice_comments="false", net_only="false", top_lvl_pins="false", no_simplify="false",
                        no_series_res="false", no_parallel_res="false", combine_devices="false", purge="false",
                        purge_nets="false", topcell=job["top"], ignore_top_ports_mismatch="false", implicit_nets="",
                        input=job["gds"], schematic=str(output / "reference/reference.cdl"),
                        report=str(output / "layout.lvsdb"), log=str(output / "deck.log"),
                        target_netlist=str(output / "extracted.cir"))
        for key, value in switches.items():
            command.extend(["-rd", key + "=" + value])
        run_stage(job, "klayout", command)
        identity(output / "layout.lvsdb", 256 * MIB)
        command = [job["tools"]["appimage"], "python3", job["helpers"]["check_lvs.py"],
                   "--database", str(output / "layout.lvsdb"),
                   "--reference", str(output / "reference/reference.json"), "--top", job["top"],
                   "--macro", job["macro"], "--macro-count", str(job["macro_count"])]
        if job["macro_qualification"]:
            command.extend(["--macro-qualification", job["macro_qualification"]])
        # Keep the independent verifier's actual failed JSON as well as successes.
        try:
            run_stage(job, "checker", command, output / "check_lvs.json")
        except RuntimeError:
            verdict = read_json(output / "check_lvs.json", 16 * MIB)
            if verdict.get("status") != "failed":
                raise
        verdict = read_json(output / "check_lvs.json", 16 * MIB)
        if verdict.get("status") not in ("passed", "failed") or verdict.get("diagnostics_truncated") is not False:
            raise ValueError("independent LVS checker did not produce a complete verdict")
        report.update(status=verdict["status"], complete=True, verdict=verdict)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        report["error"] = str(error)
    report.update(wall_seconds=time.monotonic() - tick, cgroup=current_cgroup(),
                  cgroup_memory=cgroup_sample(current_cgroup()))
    save(output / "worker-result.json", report)
    return 0 if report["status"] == "passed" else 1


def scope_state(unit):
    result = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "ControlGroup"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, timeout=5, check=False)
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return fields


def live(state):
    return state.get("ActiveState") in ("active", "activating", "deactivating", "reloading")


def guarded(job, worker_command):
    """Own the exact scope; kill descendants even when a launcher has detached."""
    if job.get("inherited_owner"):
        return guarded_inherited(job, worker_command)
    output = Path(job["output"])
    unit = "rusteda-signoff-" + hashlib.sha256(str(output).encode()).hexdigest()[:20] + ".scope"
    if live(scope_state(unit)):
        raise ValueError("private signoff scope already active")
    budget = job["limits"]
    command = ["systemd-run", "--user", "--scope", "--unit=" + unit,
               "-p", f"MemoryMax={budget['memory_mib']}M", "-p", f"MemorySwapMax={budget['swap_mib']}M",
               "-p", "TasksMax=128", *worker_command]
    save(output / "scope-command.json", command)
    tick = time.monotonic()
    deadline = tick + budget["deadline_seconds"]
    peak, group, detached, timed_out, interrupted = None, None, False, False, False
    state, previous_handlers = {}, {}
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[signum] = signal.signal(signum, stop)
    process = None
    try:
        with (output / "scope.log").open("xb") as log, (output / "cgroup-samples.jsonl").open("x") as samples:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            # <=3605 samples: O(1) sampler memory, bounded disk output.
            for _ in range(budget["deadline_seconds"] + 5):
                state = scope_state(unit)
                group = state.get("ControlGroup") or group
                memory = cgroup_sample(group)
                if memory["memory.peak"] is not None:
                    peak = max(peak or 0, memory["memory.peak"])
                samples.write(json.dumps(dict(elapsed_seconds=time.monotonic() - tick, scope_state=state,
                                              launcher_exit=process.poll(), **memory)) + "\n")
                samples.flush()
                if process.poll() is not None:
                    if not live(state):
                        break
                    detached = True
                    break
                if time.monotonic() >= deadline or interrupted:
                    timed_out = not interrupted
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            else:
                timed_out = True
    finally:
        # This is always our own exact generated unit, never a wildcard or PID guess.
        cleanup = subprocess.run(["systemctl", "--user", "kill", "--kill-whom=all", "--signal=KILL", unit],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, timeout=10, check=False)
        for _ in range(50):
            state = scope_state(unit)
            if not live(state):
                break
            time.sleep(.1)
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    result = dict(resource_mode="private_scope", scope=unit, command=command, deadline_reached=timed_out, interrupted=interrupted,
                  detached_launcher=detached, launcher_exit=process.returncode if process else None,
                  cleanup_verified=state.get("ActiveState") in ("inactive", "failed"), cleanup_state=state,
                  cleanup_output=cleanup.stdout[-4096:], wall_seconds=time.monotonic() - tick,
                  cgroup_memory_peak_bytes=peak,
                  peak_scope="sampled private cgroup memory.peak; worker also reads after all direct children terminate")
    if live(state):
        raise RuntimeError("exact private signoff scope remains active after KILL")
    save(output / "resource.json", result)
    return result


def guarded_inherited(job, worker_command):
    """Stay in the producer cgroup; its controller owns final failure cleanup.

This does not kill a shared parent scope from inside itself. An incomplete run
returns failure so worker_control kills that exact owned scope. On success the
PID inventory must contain no new processes. cgroup memory.peak is cumulative
for the whole producer, including preceding steps, and is labelled as such.
"""
    owner = inherited_owner()
    if owner != job["inherited_owner"]:
        raise ValueError("inherited producer ownership changed")
    group, output = owner["cgroup"], Path(job["output"])
    before = cgroup_pids(group)
    tick = time.monotonic()
    deadline = tick + job["limits"]["deadline_seconds"]
    peak, timed_out, interrupted = None, False, False
    previous_handlers = {}
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[signum] = signal.signal(signum, stop)
    save(output / "scope-command.json", worker_command)
    process = None
    try:
        with (output / "scope.log").open("xb") as log, (output / "cgroup-samples.jsonl").open("x") as samples:
            process = subprocess.Popen(worker_command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            for _ in range(job["limits"]["deadline_seconds"] + 5):
                if current_cgroup() != group:
                    raise ValueError("helper escaped the owned producer cgroup")
                memory = cgroup_sample(group)
                if memory["memory.peak"] is not None:
                    peak = max(peak or 0, memory["memory.peak"])
                samples.write(json.dumps(dict(elapsed_seconds=time.monotonic() - tick,
                                              launcher_exit=process.poll(), **memory)) + "\n")
                samples.flush()
                if process.poll() is not None:
                    break
                if time.monotonic() >= deadline or interrupted:
                    timed_out = not interrupted
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            else:
                timed_out = True
    finally:
        if process is not None and process.poll() is None:
            # Best effort for the direct process group; detached descendants are
            # detected below and the outer owner performs whole-scope KILL.
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    remaining = sorted(cgroup_pids(group) - before)
    result = dict(resource_mode="inherited_owned_worker", scope=owner["unit"], cgroup=group,
                  request_id=owner["request_id"], effective_memory_max_bytes=owner["memory_max_bytes"],
                  effective_swap_max_bytes=owner["swap_max_bytes"], command=worker_command,
                  deadline_reached=timed_out, interrupted=interrupted, detached_launcher=bool(remaining),
                  launcher_exit=process.returncode if process else None, residual_new_pids=remaining,
                  cleanup_verified=not remaining, wall_seconds=time.monotonic() - tick,
                  cgroup_memory_peak_bytes=peak,
                  peak_scope="inherited whole producer cgroup cumulative memory.peak, including preceding steps",
                  failure_cleanup="outer worker_control must KILL and verify its exact owned scope on failure")
    save(output / "resource.json", result)
    return result


def summarize(job, guard):
    output = Path(job["output"])
    report = dict(schema="rusteda.device-lvs-result.v1", status="unverified", complete=False,
                  scope="full device-level LVS", top=job["top"], top_port_identity=None,
                  macro_instances=None, reference_macro_instances=None, macro_devices_verified=None,
                  extraction_errors=None, gds_sha256=job["sources"]["gds"]["sha256"],
                  powered_netlist_sha256=job["sources"]["powered_netlist"]["sha256"],
                  deck_sha256=job["sources"]["deck"]["sha256"],
                  macro_qualification_sha256=job["sources"].get("macro_qualification", {}).get("sha256"),
                  sources=job["sources"], estimated_peak_bytes=job["estimated_peak_bytes"],
                  host_mem_available_bytes=job["host_mem_available_bytes"], resources=guard)
    changes = source_changes(job["sources"])
    report.update(input_hashes_unchanged=not changes, changed_inputs=changes)
    worker = read_json(output / "worker-result.json", 16 * MIB) if (output / "worker-result.json").is_file() else None
    if worker:
        report["worker"] = worker
        peak = worker.get("cgroup_memory", {}).get("memory.peak")
        if peak is not None:
            guard["cgroup_memory_peak_bytes"] = max(guard.get("cgroup_memory_peak_bytes") or 0, peak)
            if guard["resource_mode"] == "private_scope":
                guard["peak_scope"] = "private cgroup memory.peak including worker read after child termination"
    if (worker and worker.get("complete") is True and not changes and not guard["deadline_reached"]
            and not guard["interrupted"] and not guard["detached_launcher"] and guard["cleanup_verified"]
            and guard["launcher_exit"] == (0 if worker["status"] == "passed" else 1)):
        verdict = worker["verdict"]
        report.update(status=verdict["status"], complete=True)
        for key in ("top_port_identity", "macro_instances", "reference_macro_instances", "macro_devices_verified",
                    "extraction_errors", "macro_expanded_device_counts", "database_sha256", "reference_cdl_sha256"):
            report[key] = verdict[key]
        report["artifacts"] = {name: identity(output / name) for name in
                               ("netlist.json", "layout.lvsdb", "check_lvs.json", "reference/reference.cdl", "reference/reference.json")}
    else:
        report["error"] = "signoff incomplete: source change, missing/failed worker, deadline, interruption or detached child"
    save(output / "summary.json", report)
    (output / "HEAD").write_text("rev1\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--base-rev")
    parser.add_argument("--_worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._worker:
        return execute(read_json(args._worker, 4 * MIB))
    if args.inputs is None or args.workspace is None or args.base_rev is None:
        parser.error("--inputs, --workspace and --base-rev are required")
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.base_rev != "empty" or any(p.name != "workspace.lock" for p in output.iterdir()):
            raise ValueError("REVISION_CONFLICT: signoff requires a new empty workspace")
        try:
            job = prepare(args.inputs.resolve(strict=True), output)
            save(output / "job.json", job)
            command = [job["python"], job["helpers"]["signoff_lvs.py"], "--_worker", str(output / "job.json")]
            guard = guarded(job, command)
            result = summarize(job, guard)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            result = dict(schema="rusteda.device-lvs-result.v1", status="unverified", complete=False, error=str(error))
            if not (output / "summary.json").exists():
                save(output / "summary.json", result)
            (output / "HEAD").write_text("rev1\n")
        print(json.dumps({key: value for key, value in result.items() if key not in ("sources", "worker")}))
        return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps(dict(status="unverified", complete=False, error=str(error))))
        raise SystemExit(2) from error

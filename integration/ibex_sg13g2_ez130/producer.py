#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Foreground, revision-bound IHP native-flow worker; no EDA algorithm here.

An explicit hash-pinned manifest supplies bounded commands and JSON templates.
Commands never run through a shell. Every optimization plan is regenerated from
this run's reports; a completed producer still requires the Rust flow engine's
independent gate ingestion before it is called a qualified flow.

<=129 steps, <=288 inputs/tools, <=128 artifacts, <=2 MiB manifest and <=16 MiB
JSON reports, <=65536 template nodes/depth32. Expected controller RAM <128 MiB.
The owning foreground controller provides the cgroup, deadline and exact cleanup.

Legacy manifests charge every stdout/stderr byte to max_log_bytes. An explicit
stdout_artifacts registry may instead classify the exact stdout of a supported
data-export command. Diagnostic stdout and ALL stderr still share the original
64 MiB ceiling. Machine data has independent per-file caps (16/64/128 MiB) and
an explicit aggregate cap <=768 MiB. Successful captures are never truncated.
Capture uses two 64 KiB streaming buffers, not an in-memory report. Raw files
and their FileIDs retain the original logs/<step>.stdout/.stderr locations.
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
import selectors
import signal
import subprocess
import sys
import time


GATES = ("inputs_frozen", "macros", "synthesis_equivalence", "final_equivalence",
         "simulation", "placement", "cts", "power_connectivity", "routing",
         "extraction", "drc", "antenna", "lvs", "timing", "timing_accuracy", "controls")
MAX_FILE = 4 * 1024**3
STDOUT_ARTIFACT_CAPS = {
    "verilog": 16 << 20, "pin_capacitance": 16 << 20,
    "timing_drv": 16 << 20, "timing_endpoints": 16 << 20,
    "timing_hold_fix": 64 << 20, "placement_seed": 64 << 20,
    "def": 128 << 20,
}
MAX_STDOUT_ARTIFACT_BYTES = 1536 << 20
CAPTURE_CHUNK = 64 << 10


def signoff_parallel_group(threads=16):
    """Explicit B4 policy; existing recipes opt in and retain their commands.

    Run16 RSS: main 1.82 GiB, antenna 2.16 GiB, LVS 1.51 GiB,
    timing 0.48 GiB, OpenRCX 0.25 GiB. Reservations include headroom:
    max(2304 + 2560 + 1024) = 5888 MiB, plus <128 MiB controller,
    below the 7168 MiB scheduling cap and enclosing 8192 MiB cgroup.
    <=8 nodes/28 edges, no path materialization; state is O(nodes + edges).

    Four-CPU allocations (4 <= threads < 6) get two disjoint two-core lanes
    over the foundry checks only: `be_main`, and `be_antenna` followed by
    `be_lvs`; OpenRCX and signoff timing stay serial before the group, so
    `timing_accuracy` still gates the checks. Measured 2026-11-04 on the
    electrostatic-placement Ibex layout: main 2.21 GiB, antenna 1.64 GiB, LVS 0.78 GiB peak
    RSS; reservations 2560 + 2304 (or 2560 + 2048) <= 4864 MiB under the same
    7168 MiB cap. Commands, two-thread tool limits and decks are unchanged.
    """
    def command(dependencies, slots, memory):
        return dict(depends_on=dependencies, cpu_slots=slots, memory_mib=memory)

    if 4 <= threads < 6:
        return dict(max_parallel=2, max_memory_mib=7168, steps={
            'be_main': command([], [0, 1], 2560),
            'be_antenna': command([], [2, 3], 2304),
            'be_lvs_inputs': dict(depends_on=[]),
            'be_lvs': command(['be_lvs_inputs', 'be_antenna'], [2, 3], 2048),
        })
    return dict(max_parallel=3, max_memory_mib=7168, steps={
        'be_rcx_inputs': dict(depends_on=[]),
        'be_openrcx': command(['be_rcx_inputs'], [4, 5], 768),
        'be_timing_inputs': dict(depends_on=['be_openrcx']),
        'be_timing': command(['be_timing_inputs'], [4, 5], 1024),
        'be_main': command([], [0, 1], 2304),
        'be_antenna': command([], [2, 3], 2560),
        'be_lvs_inputs': dict(depends_on=[]),
        'be_lvs': command(['be_lvs_inputs', 'be_antenna'], [2, 3], 2048),
    })


def validate_parallel_groups(steps, groups, scope):
    """Validate bounded, contiguous DAG regions before launching any command.

    Dependencies must precede dependants in manifest order, which both guards
    cycles and keeps --stop-after a true serial prefix. CPU slots index the
    physical CPUs already verified by the worker; overlapping lanes serialize.
    Memory reservations are summed for every live child, under the caller's
    hard cgroup limit. This is admission control, not a per-process RSS claim.
    """
    if groups is None:
        return {}
    if not isinstance(groups, list) or not 1 <= len(groups) <= 8:
        raise ValueError('parallel group count budget exceeded')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 129:
        raise ValueError('parallel step count budget exceeded')
    order = {}
    for index, step in enumerate(steps):
        tag = step.get('id')
        if not isinstance(tag, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', tag) or tag in order:
            raise ValueError('invalid or duplicate parallel step handle')
        order[tag] = index
    memory, threads = scope.get('memory_mib'), scope.get('threads')
    if type(memory) is not int or type(threads) is not int:
        raise ValueError('parallel execution requires explicit memory/CPU allocation')
    result, assigned = {}, set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'max_parallel', 'max_memory_mib', 'steps'}:
            raise ValueError('unknown parallel group field')
        width, cap, nodes = group['max_parallel'], group['max_memory_mib'], group['steps']
        if type(width) is not int or not 1 <= width <= 16 or type(cap) is not int or not 1 <= cap <= memory - 128:
            raise ValueError('parallel CPU/memory scheduling cap exceeds allocation')
        if not isinstance(nodes, dict) or not 1 <= len(nodes) <= 128 or not set(nodes) <= set(order):
            raise ValueError('unknown or excessive parallel step')
        indices = sorted(order[tag] for tag in nodes)
        if indices != list(range(indices[0], indices[-1] + 1)) or assigned & set(nodes):
            raise ValueError('parallel groups must be disjoint contiguous manifest regions')
        assigned.update(nodes)
        for tag, request in nodes.items():
            step = steps[order[tag]]
            if not isinstance(request, dict):
                raise ValueError('invalid parallel resource request')
            deps = request.get('depends_on')
            if (not isinstance(deps, list) or len(deps) > 128
                    or any(not isinstance(dep, str) for dep in deps) or len(set(deps)) != len(deps)
                    or any(dep not in nodes or order[dep] >= order[tag] for dep in deps)):
                raise ValueError('parallel dependencies must be unique earlier group steps (no cycles)')
            if step['kind'] == 'render':
                if set(request) != {'depends_on'} or set(step) != {'id', 'kind', 'destination', 'content'}:
                    raise ValueError('invalid parallel render fields')
                continue
            if (step['kind'] != 'command' or set(request) != {'depends_on', 'cpu_slots', 'memory_mib'}
                    or set(step) - {'stdout_artifact'} != {'id', 'kind', 'tool', 'arguments', 'timeout_s'}):
                raise ValueError('parallel commands must be bounded commands')
            slots, reservation = request['cpu_slots'], request['memory_mib']
            if (not isinstance(slots, list) or not slots or len(slots) > threads
                    or any(type(slot) is not int or not 0 <= slot < threads for slot in slots)
                    or len(set(slots)) != len(slots) or type(reservation) is not int or not 1 <= reservation <= cap):
                raise ValueError('parallel CPU/memory request exceeds allocation')
            if (not isinstance(step['arguments'], list) or not 1 <= len(step['arguments']) <= 256
                    or any(not isinstance(arg, str) for arg in step['arguments'])
                    or type(step['timeout_s']) is not int or not 1 <= step['timeout_s'] <= 86400):
                raise ValueError('invalid parallel command arguments/deadline')
        result[steps[indices[0]]['id']] = group
    return result


def stdout_artifact_format(step):
    """Pure allowlist over unexpanded, pinned-native command arguments.

    These are the data streams consumed by the current native recipe. Merely
    naming a tool or using an export-looking suffix does not exempt a command.
    In particular inspect, route, run and Python commands are diagnostics.
    """
    if step.get("kind") != "command":
        return None
    args, tool = step.get("arguments"), step.get("tool")
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        return None

    def fresh_workspace(value):
        if not value.startswith("{output}/"):
            return False
        suffix = value[len("{output}/"):]
        return bool(re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", suffix))

    if tool == "pin_caps" and len(args) == 1 and args[0] in {
            "{files:std_tt}", "{files:std_ss}", "{files:ram_tt}", "{files:ram_ss}"}:
        return "pin_capacitance"
    if tool == "rustroad" and len(args) >= 3 and args[0] == "--workspace" and fresh_workspace(args[1]):
        tail = args[2:]
        if tail in (["export-verilog"], ["export-verilog", "--include-power"]):
            return "verilog"
        if tail == ["export-def"]:
            return "def"
        if tail == ["export-placement-seed"]:
            return "placement_seed"
    if tool == "ruststa" and len(args) >= 3 and args[:2] == ["report", "-w"] and fresh_workspace(args[2]):
        tail = args[3:]
        for corner in ("tt", "ss"):
            prefix = ["--corner", corner, "--threads", "2", "--section"]
            if tail in (prefix + ["drv", "--filter", flt, "--limit", "100000", "--format", "json"] for flt in ("violating", "slack<0.2")):
                return "timing_drv"
            if tail == prefix + ["endpoints", "--limit", "0", "--format", "json"]:
                return "timing_endpoints"
        if tail == ["--threads", "2", "--section", "hold_fix", "--target-margin", "0.5",
                    "--limit", "0", "--format", "json"]:
            return "timing_hold_fix"
    return None


def declare_stdout_artifacts(steps):
    """Return copied steps and explicit stream declarations; no I/O or commands."""
    result, registry = [], {}
    if not isinstance(steps, list) or not 1 <= len(steps) <= 129:
        raise ValueError("stdout artifact step count budget exceeded")
    for original in steps:
        step = dict(original)
        kind = stdout_artifact_format(step)
        if kind is not None:
            tag = step["id"]
            if tag in registry or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", tag):
                raise ValueError("invalid stdout artifact handle")
            step["stdout_artifact"] = tag
            registry[tag] = dict(path=f"logs/{tag}.stdout", format=kind,
                                 max_bytes=STDOUT_ARTIFACT_CAPS[kind])
        elif "stdout_artifact" in step:
            raise ValueError("non-export command cannot declare a stdout artifact")
        result.append(step)
    return result, registry


def validate_stdout_artifacts(steps, registry, scope):
    """Validate exact stream ownership and bounded declarations, before launch."""
    if registry is None:
        if any("stdout_artifact" in step for step in steps):
            raise ValueError("stdout artifact is not registered")
        if "max_stdout_artifact_bytes" in scope:
            raise ValueError("stdout artifact budget requires an explicit registry")
        return dict(mode="legacy_all_streams_are_logs", declared_bytes=0, aggregate_limit_bytes=0,
                    files=0)
    if not isinstance(registry, dict) or not 1 <= len(registry) <= 128:
        raise ValueError("stdout artifact registry count budget exceeded")
    cap = scope.get("max_stdout_artifact_bytes")
    if type(cap) is not int or not 1 <= cap <= MAX_STDOUT_ARTIFACT_BYTES:
        raise ValueError("invalid aggregate stdout artifact byte budget")
    seen, declared = set(), 0
    for step in steps:
        if "stdout_artifact" not in step:
            continue
        handle, tag = step["stdout_artifact"], step.get("id")
        if not isinstance(handle, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", handle) or handle in seen:
            raise ValueError("invalid or multiply referenced stdout artifact handle")
        seen.add(handle)
        spec = registry.get(handle)
        kind = stdout_artifact_format(step)
        if kind is None:
            raise ValueError("non-export command cannot declare a stdout artifact")
        if (not isinstance(spec, dict) or set(spec) != {"path", "format", "max_bytes"}
                or handle != tag or spec["path"] != f"logs/{tag}.stdout" or spec["format"] != kind):
            raise ValueError("stdout artifact must name this exact supported command output")
        size = spec["max_bytes"]
        if type(size) is not int or not 1 <= size <= STDOUT_ARTIFACT_CAPS[kind]:
            raise ValueError("invalid per-file stdout artifact byte budget")
        declared += size
    if seen != set(registry):
        raise ValueError("unreferenced or undeclared stdout artifact")
    if declared > MAX_STDOUT_ARTIFACT_BYTES:
        raise ValueError("declared stdout artifact byte sum exceeds 1536 MiB")
    return dict(mode="explicit_stdout_artifacts", declared_bytes=declared,
                aggregate_limit_bytes=cap, files=len(registry))


def capture_artifact(command, output, environment, out, err, started, seconds,
                     log_bytes, log_budget, artifact_bytes, artifact_budget, file_budget):
    """Stream both raw pipes; all stderr remains diagnostic, including failures.

    The first excessive chunk fails the command. Kill the whole process group
    and retain bounded remaining pipe bytes as failed-run evidence. At most
    one 64 KiB chunk plus <=1 MiB post-kill drain may exceed acceptance caps;
    no over-budget capture can be reported successful or reused as a pass.
    """
    process = subprocess.Popen(command, cwd=output, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        sizes = {"stdout": 0, "stderr": 0}
        failure = None
        with selectors.DefaultSelector() as selector:
            for stream, name, target in ((process.stdout, "stdout", out), (process.stderr, "stderr", err)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (name, target))
            failure, drain_end, drained = None, None, 0
            while selector.get_map() or process.poll() is None:
                if failure is None and time.monotonic() - started > seconds:
                    failure = TimeoutError("step deadline exceeded during stdout artifact capture")
                if failure is not None and drain_end is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    drain_end = time.monotonic() + 2
                if drain_end is not None and (time.monotonic() > drain_end or drained > 1 << 20):
                    failure = RuntimeError("failed capture cleanup exceeded bounded pipe drain")
                    break
                for key, _ in selector.select(.1):
                    name, target = key.data
                    data = os.read(key.fileobj.fileno(), CAPTURE_CHUNK)
                    if not data:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    target.write(data)
                    sizes[name] += len(data)
                    if drain_end is not None:
                        drained += len(data)
                    if failure is None:
                        if log_bytes + sizes["stderr"] > log_budget:
                            failure = ValueError("aggregate log byte budget exceeded (all stderr is diagnostic)")
                        elif sizes["stdout"] > file_budget:
                            failure = ValueError("per-file stdout artifact byte budget exceeded")
                        elif artifact_bytes + sizes["stdout"] > artifact_budget:
                            failure = ValueError("aggregate stdout artifact byte budget exceeded")
                if failure is not None and drain_end is None:
                    continue
            for key in list(selector.get_map().values()):
                key.fileobj.close()
            if failure is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for pipe in (process.stdout, process.stderr):
            if not pipe.closed:
                pipe.close()
        process.wait(timeout=10)
    if failure is not None:
        raise failure
    return process.returncode


def read_json(path, limit=2 * 1024**2):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"missing or oversized JSON: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("JSON byte budget exceeded")
    return json.loads(data)


def identity(path):
    path = path.resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError(f"file budget exceeded: {path}")
    size = path.stat().st_size
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if size != path.stat().st_size:
        raise ValueError("input size changed during hashing")
    return dict(path=str(path), sha256=digest, bytes=size)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".tmp").open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.with_suffix(path.suffix + ".tmp").replace(path)


def relative(value):
    p = Path(value)
    if not value or not p.parts or p.is_absolute() or any(part in (".", "..") for part in p.parts):
        raise ValueError("expected a normal relative output path")
    return p


def cgroup_resources():
    result = dict(memory_peak_bytes=None, swap_peak_bytes=None)
    try:
        entry = next(line for line in Path("/proc/self/cgroup").read_text().splitlines()
                     if line.startswith("0::"))
        location = Path("/sys/fs/cgroup") / entry[3:].lstrip("/")
        for file, key in (("memory.peak", "memory_peak_bytes"),
                          ("memory.swap.peak", "swap_peak_bytes")):
            if (location / file).is_file():
                result[key] = int((location / file).read_text())
    except (OSError, ValueError, StopIteration):
        pass
    return result


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", "--workspace", dest="workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    parser.add_argument("--pdk", choices=("ihp",), required=True)
    parser.add_argument("--rustroad", type=Path, required=True)
    parser.add_argument("--gnu-time", type=Path, required=True)
    parser.add_argument("--stop-after", help="Run a bounded prefix; always leave full-flow qualification unverified")
    args = parser.parse_args()
    request = os.environ.get("RUSTEDA_WORKER_ID", "")
    if not re.fullmatch("[a-f0-9]{64}", request):
        raise ValueError("missing revision-bound worker ownership identity")
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.base_rev != "empty" or any(p.name != "workspace.lock" for p in output.iterdir()):
            raise ValueError("REVISION_CONFLICT: producer requires an empty workspace")
        manifest = read_json(args.manifest)
        if (manifest.get("schema") != "rusteda.ihp-flow.v1"
                or manifest.get("pdk") != "ihp-sg13g2" or manifest.get("library") != "ez130_8t"
                or manifest.get("corners") != ["tt", "ss"]):
            raise ValueError("IHP producer identity mismatch")
        required_fields = {"schema", "name", "pdk", "library", "corners", "files", "tools",
                           "scope", "steps", "gates", "artifacts"}
        if (not required_fields <= set(manifest)
                or set(manifest) - required_fields - {"stdout_artifacts", "parallel_groups"}):
            raise ValueError("unknown or missing producer manifest field")
        steps = manifest["steps"]
        if not 1 <= len(steps) <= 129 or len(manifest["artifacts"]) > 128:
            raise ValueError("step/artifact count budget exceeded")
        if args.stop_after is not None and (
                not re.fullmatch("[a-zA-Z0-9_-]{1,128}", args.stop_after)
                or sum(step.get("id") == args.stop_after for step in steps) != 1):
            raise ValueError("prefix stop target must identify exactly one manifest step")
        records = {}
        for group, limit in (("files", 256), ("tools", 32)):
            entries = manifest[group]
            if not 1 <= len(entries) <= limit:
                raise ValueError("input collection budget exceeded")
            for name, spec in entries.items():
                if not re.fullmatch("[a-zA-Z0-9_-]{1,128}", name):
                    raise ValueError("unsafe input handle")
                path = Path(spec["path"])
                if not path.is_absolute():
                    path = args.project / relative(str(path))
                actual = identity(path)
                if actual["sha256"] != spec["sha256"] or actual["bytes"] != spec["bytes"]:
                    raise ValueError(f"input identity changed: {name}")
                records[group + ":" + name] = actual
        for key, path in (("rustroad", args.rustroad), ("gnu_time", args.gnu_time)):
            if identity(path) != records["tools:" + key]:
                raise ValueError(f"worker argument differs from pinned {key}")
        scope = manifest["scope"]
        log_budget = scope["max_log_bytes"]
        if type(log_budget) is not int or not 1024 <= log_budget <= 64 * 1024**2:
            raise ValueError("invalid aggregate log byte budget")
        stdout_artifacts = manifest.get("stdout_artifacts")
        capture_policy = validate_stdout_artifacts(steps, stdout_artifacts, scope)
        parallel_groups = validate_parallel_groups(steps, manifest.get('parallel_groups'), scope)
        if stdout_artifacts is not None:
            source, running = records.get("files:producer"), identity(Path(__file__))
            # The public flow executes an immutable captured copy of its driver.
            # The declared original was verified above; require identical bytes,
            # without coupling a valid captured copy to the original path.
            if source is None or any(source[key] != running[key] for key in ("sha256", "bytes")):
                raise ValueError("stdout artifact policy must be the actual pinned producer source")
        artifact_budget = capture_policy["aggregate_limit_bytes"]
        cpus = scope["cpu_affinity"]
        threads = scope["threads"]
        if (not 1 <= threads <= 16 or len(cpus) != threads or len(set(cpus)) != threads
                or any(type(c) is not int or c not in os.sched_getaffinity(0) for c in cpus)):
            raise ValueError("invalid physical-core CPU allocation")
        physical = set()
        for cpu in cpus:
            topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
            physical.add(((topology / "physical_package_id").read_text().strip(),
                          (topology / "core_id").read_text().strip()))
        if len(physical) != threads:
            raise ValueError("CPU allocation contains SMT siblings")
        os.sched_setaffinity(0, cpus)
        owner = Path(os.environ["RUSTEDA_OWNERSHIP"])
        owner.mkdir(parents=True, exist_ok=False)
        write_json(owner / "native.json", dict(request_id=request, implementation="native",
                                               manifest=identity(args.manifest)))
        (output / "HEAD").write_text("rev0\n")
        (output / "logs").mkdir()
        (output / "tmp").mkdir()
        nodes = 0

        def output_path(value):
            path = output / relative(value)
            if not path.resolve().is_relative_to(output):
                raise ValueError("output path escapes workspace")
            return path

        def expand(value):
            value = value.replace("{output}", str(output)).replace("{threads}", str(threads))
            for name, record in records.items():
                value = value.replace("{" + name + "}", record["path"])
            if re.search(r"\{(?:files|tools):[^}]*\}", value):
                raise ValueError("unresolved input/tool argument placeholder")
            if len(value) > 65536 or "\x00" in value:
                raise ValueError("argument string budget exceeded")
            return value

        def render(value, depth=0):
            nonlocal nodes
            nodes += 1
            if depth > 32 or nodes > 65536:
                raise ValueError("template depth/node budget exceeded")
            if isinstance(value, dict):
                if set(value) == {"$identity"}:
                    return identity(output_path(value["$identity"]))
                if set(value) == {"$sha256"}:
                    return identity(output_path(value["$sha256"]))["sha256"]
                if set(value) == {"$input"}:
                    return records["files:" + value["$input"]]
                if set(value) == {"$tool"}:
                    return records["tools:" + value["$tool"]]
                return {key: render(item, depth + 1) for key, item in value.items()}
            if isinstance(value, list):
                return [render(item, depth + 1) for item in value]
            return expand(value) if isinstance(value, str) else value

        seen, receipts, log_bytes, artifact_bytes = set(), [], 0, 0
        environment = dict(os.environ, CARGO_BUILD_JOBS="2", RUST_TEST_THREADS="2",
                           OMP_NUM_THREADS=str(threads), RAYON_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS="1",
                           TMPDIR=str(output / "tmp"))
        environment.pop("RUSTROAD_RULE_DUMP", None)  # private containment: path-valued debug writer disabled

        def run_parallel(group):
            """One foreground coordinator; <=16 live process groups, no daemon.

            Ready tasks launch in manifest order with fixed nonoverlapping CPU
            sets. Admission sums live memory reservations, and all live logs
            share the original byte limit. A declared stdout artifact is charged
            to its own per-file and aggregate artifact caps, as in the serial
            path, and overflow fails the group. Receipts are stored in manifest
            order, never completion order. The outer worker cgroup provides the
            hard RAM cap.
            """
            nonlocal nodes, log_bytes, artifact_bytes
            pending = [step for step in steps if step['id'] in group['steps']]
            order = {step['id']: index for index, step in enumerate(pending)}
            running, completed, observed = {}, set(), {}

            def checkpoint():
                ordered = [observed[tag] for tag in sorted(observed, key=order.__getitem__)]
                write_json(output / 'step-receipts.json', receipts + ordered)

            def finish(tag, error=None):
                nonlocal log_bytes, artifact_bytes
                job = running.pop(tag)
                # Kill leftover descendants even when the leader exited. Every
                # group was created by this coordinator, so other sessions are
                # never targeted. GNU time and all tool children inherit it.
                try:
                    os.killpg(job['process'].pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                code = job['process'].wait(timeout=10)
                job['out'].close()
                job['err'].close()
                stdout_id = identity(job['log'].with_suffix('.stdout'))
                stderr_id = identity(job['log'].with_suffix('.stderr'))
                artifact = job['artifact']
                log_bytes += stderr_id['bytes'] + (0 if artifact is not None else stdout_id['bytes'])
                artifact_bytes += stdout_id['bytes'] if artifact is not None else 0
                receipt = dict(id=tag, kind='command', exit_code=code,
                    wall_seconds=time.monotonic() - job['tick'], stdout=stdout_id, stderr=stderr_id,
                    started_seconds=job['tick'] - started, cpu_affinity=job['cpus'],
                    memory_reservation_mib=group['steps'][tag]['memory_mib'],
                    depends_on=group['steps'][tag]['depends_on'])
                if artifact is not None:
                    receipt['stdout_artifact'] = dict(handle=job['handle'], **artifact)
                    if error is None and stdout_id['bytes'] > artifact['max_bytes']:
                        error = 'per-file stdout artifact byte budget exceeded'
                    if error is None and artifact_bytes > artifact_budget:
                        error = 'aggregate stdout artifact byte budget exceeded'
                    if error is not None:
                        code = code or 1
                        receipt['exit_code'] = code
                if error is not None:
                    receipt['capture_error'] = error
                observed[tag] = receipt
                if code == 0 and error is None:
                    completed.add(tag)
                checkpoint()
                return code

            try:
                while pending or running:
                    live_bytes = live_artifacts = 0
                    for job in running.values():
                        live_bytes += job['log'].with_suffix('.stderr').stat().st_size
                        size = job['log'].with_suffix('.stdout').stat().st_size
                        if job['artifact'] is None:
                            live_bytes += size
                        else:
                            live_artifacts += size
                            if size > job['artifact']['max_bytes']:
                                raise ValueError('per-file stdout artifact byte budget exceeded during parallel execution')
                    if log_bytes + live_bytes > log_budget:
                        raise ValueError('aggregate log byte budget exceeded during parallel execution')
                    if artifact_bytes + live_artifacts > artifact_budget:
                        raise ValueError('aggregate stdout artifact byte budget exceeded during parallel execution')
                    for tag in list(running):
                        job = running[tag]
                        if job['process'].poll() is not None:
                            code = finish(tag)
                            if code:
                                raise RuntimeError(f'step failed: {tag}, exit={code}')
                        elif time.monotonic() - job['tick'] > job['seconds']:
                            raise TimeoutError(f'step deadline exceeded: {tag}')
                    for step in list(pending):
                        tag, tick = step['id'], time.monotonic()
                        request = group['steps'][tag]
                        if not set(request['depends_on']) <= completed:
                            continue
                        if step['kind'] == 'render':
                            nodes = 0
                            write_json(output_path(step['destination']), render(step['content']))
                            observed[tag] = dict(id=tag, kind='render', wall_seconds=time.monotonic() - tick)
                            completed.add(tag)
                            pending.remove(step)
                            checkpoint()
                            continue
                        slots = set(request['cpu_slots'])
                        occupied = {slot for other in running for slot in group['steps'][other]['cpu_slots']}
                        memory = sum(group['steps'][other]['memory_mib'] for other in running)
                        if (len(running) >= group['max_parallel'] or slots & occupied
                                or memory + request['memory_mib'] > group['max_memory_mib']):
                            continue
                        selected = [cpus[slot] for slot in request['cpu_slots']]
                        argv = [records['tools:' + step['tool']]['path'],
                                *(expand(arg) for arg in step['arguments'])]
                        log = output / 'logs' / tag
                        command = [str(args.gnu_time), '-v', '-o', str(log.with_suffix('.time')), *argv]
                        write_json(log.with_suffix('.command.json'), dict(argv=command, cwd=str(output),
                            cpu_affinity=selected, memory_reservation_mib=request['memory_mib']))
                        temp = output / 'tmp' / tag
                        temp.mkdir()
                        child_environment = dict(environment, OMP_NUM_THREADS=str(len(selected)),
                            RAYON_NUM_THREADS=str(len(selected)), TMPDIR=str(temp), RUSTEDA_PARALLEL_STEP=tag)
                        out, err = log.with_suffix('.stdout').open('xb'), log.with_suffix('.stderr').open('xb')
                        try:
                            # The coordinator is single-threaded. Set affinity
                            # before exec so every descendant starts with the
                            # same lane. The outer cgroup owns the hard cpuset.
                            process = subprocess.Popen(command, cwd=output, env=child_environment,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True,
                                preexec_fn=lambda selected=selected: os.sched_setaffinity(0, selected))
                        except BaseException:
                            out.close()
                            err.close()
                            raise
                        running[tag] = dict(process=process, log=log, out=out, err=err,
                            cpus=selected, tick=tick, seconds=step['timeout_s'],
                            artifact=stdout_artifacts[step['stdout_artifact']] if 'stdout_artifact' in step else None,
                            handle=step.get('stdout_artifact'))
                        pending.remove(step)
                    if pending and not running:
                        raise ValueError('parallel dependency/resource admission made no progress')
                    if running:
                        time.sleep(.05)
                if log_bytes > log_budget:
                    raise ValueError('aggregate log byte budget exceeded during parallel execution')
            except BaseException as error:
                for tag in list(running):
                    finish(tag, 'parallel group cancelled: ' + str(error))
                raise
            finally:
                receipts.extend(observed[tag] for tag in sorted(observed, key=order.__getitem__))
                seen.update(observed)
                write_json(output / 'step-receipts.json', receipts)

        try:
            for step in steps:
                tag = step["id"]
                group = parallel_groups.get(tag)
                if group is not None and args.stop_after not in group['steps']:
                    run_parallel(group)
                    continue
                if any(tag in entry['steps'] for entry in parallel_groups.values()) and tag in seen:
                    continue
                if not re.fullmatch("[a-zA-Z0-9_-]{1,128}", tag) or tag in seen:
                    raise ValueError("invalid or duplicate step handle")
                seen.add(tag)
                tick = time.monotonic()
                if step["kind"] == "render":
                    if set(step) != {"id", "kind", "destination", "content"}:
                        raise ValueError("unknown render step field")
                    nodes = 0
                    write_json(output_path(step["destination"]), render(step["content"]))
                    receipts.append(dict(id=tag, kind="render", wall_seconds=time.monotonic() - tick))
                    if tag == args.stop_after:
                        break
                    continue
                if step["kind"] != "command":
                    raise ValueError("unsupported step kind")
                command_fields = {"id", "kind", "tool", "arguments", "timeout_s"}
                if set(step) not in (command_fields, command_fields | {"stdout_artifact"}):
                    raise ValueError("unknown command step field")
                argv = [records["tools:" + step["tool"]]["path"]]
                if not 1 <= len(step["arguments"]) <= 256:
                    raise ValueError("command argument count budget exceeded")
                argv.extend(expand(arg) for arg in step["arguments"])
                seconds = step["timeout_s"]
                if type(seconds) is not int or not 1 <= seconds <= 86400:
                    raise ValueError("invalid command deadline")
                log = output / "logs" / tag
                command = [str(args.gnu_time), "-v", "-o", str(log.with_suffix(".time")), *argv]
                write_json(log.with_suffix(".command.json"), dict(argv=command, cwd=str(output)))
                artifact = stdout_artifacts[step["stdout_artifact"]] if "stdout_artifact" in step else None
                code, capture_error = None, None
                try:
                    with log.with_suffix(".stdout").open("xb") as out, log.with_suffix(".stderr").open("xb") as err:
                        if artifact is not None:
                            code = capture_artifact(command, output, environment, out, err, tick, seconds,
                                log_bytes, log_budget, artifact_bytes, artifact_budget, artifact["max_bytes"])
                        else:
                            # Preserve the legacy accounting and execution path: every
                            # stdout/stderr byte is charged, irrespective of file suffix.
                            process = subprocess.Popen(command, cwd=output, env=environment, stdin=subprocess.DEVNULL,
                                                       stdout=out, stderr=err)
                            try:
                                while process.poll() is None:
                                    if time.monotonic() - tick > seconds:
                                        raise TimeoutError(f"step deadline exceeded: {tag}")
                                    if log_bytes + sum(p.stat().st_size for p in (log.with_suffix(".stdout"), log.with_suffix(".stderr"))) > log_budget:
                                        raise ValueError("aggregate log byte budget exceeded")
                                    # Wake on exit; the bound checks still run every 0.2 s.
                                    try:
                                        process.wait(timeout=.2)
                                    except subprocess.TimeoutExpired:
                                        pass
                            except BaseException:
                                process.kill()
                                process.wait(timeout=10)
                                raise
                            code = process.returncode
                except BaseException as error:
                    capture_error = str(error)
                    raise
                finally:
                    stdout_id, stderr_id = identity(log.with_suffix(".stdout")), identity(log.with_suffix(".stderr"))
                    log_bytes += stderr_id["bytes"] + (0 if artifact is not None else stdout_id["bytes"])
                    artifact_bytes += stdout_id["bytes"] if artifact is not None else 0
                    receipt = dict(id=tag, kind="command", wall_seconds=time.monotonic() - tick,
                                   exit_code=code, stdout=stdout_id, stderr=stderr_id)
                    if artifact is not None:
                        receipt["stdout_artifact"] = dict(handle=step["stdout_artifact"], **artifact)
                    if capture_error is not None:
                        receipt["capture_error"] = capture_error
                    receipts.append(receipt)
                    write_json(output / "step-receipts.json", receipts)
                if log_bytes > log_budget:
                    raise ValueError("aggregate log byte budget exceeded")
                if code:
                    raise RuntimeError(f"step failed: {tag}, exit={code}")
                if tag == args.stop_after:
                    break
            # Check pinned sources again; no mutable artifact is mistaken for an input.
            for record in records.values():
                if identity(Path(record["path"])) != record:
                    raise ValueError("producer input changed during execution")
            if args.stop_after is None:
                if set(manifest["gates"]) != set(GATES):
                    raise ValueError("full-flow gate set differs")
                gates = {name: identity(output_path(path)) for name, path in manifest["gates"].items()}
                artifacts = {name: identity(output_path(path)) for name, path in manifest["artifacts"].items()}
                summary = dict(schema="rusteda.ihp-rtl2gds.v1", status="completed", implementation="rusteda",
                               manifest_sha256=identity(args.manifest)["sha256"], scope=scope,
                               gates=gates, artifacts=artifacts, failed_items=[], unverified_items=[])
            else:
                artifacts, missing = {}, []
                for name, path in manifest["artifacts"].items():
                    source = output_path(path)
                    if source.is_file():
                        artifacts[name] = identity(source)
                    else:
                        missing.append(name)
                summary = dict(schema="rusteda.ihp-frontend.v1", status="frontend_completed",
                               implementation="rusteda", stop_after=args.stop_after,
                               manifest_sha256=identity(args.manifest)["sha256"], scope=scope,
                               artifacts=artifacts, unavailable_artifacts=missing, failed_items=[],
                               unverified_items=["complete_flow", "routing", "extraction", "gds", "drc",
                                                 "antenna", "lvs", "extracted_timing", "timing_accuracy", "benchmark"])
            write_json(output / "step-receipts.json", receipts)
            exit_code = 0
        except (OSError, ValueError, KeyError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
            summary = dict(schema="rusteda.ihp-rtl2gds.v1", status="failed", implementation="rusteda",
                           error=str(error), failed_items=[str(error)], unverified_items=["complete_flow"])
            exit_code = 1
        usage = resource.getrusage(resource.RUSAGE_SELF)
        child = resource.getrusage(resource.RUSAGE_CHILDREN)
        summary["capture_accounting"] = dict(**capture_policy,
            diagnostic_bytes=log_bytes, diagnostic_limit_bytes=log_budget,
            stdout_artifact_bytes=artifact_bytes,
            stderr_policy="every stderr byte is diagnostic, without exemptions",
            raw_paths="logs/<step>.stdout and logs/<step>.stderr; preserved and hashed",
            overflow_policy="failed capture; bounded extra chunks retained, never a truncated success")
        summary["resources"] = dict(wall_seconds=time.monotonic() - started,
                                     peak_reaped_process_rss_kib=max(usage.ru_maxrss, child.ru_maxrss),
                                     cgroup=cgroup_resources(), cpu_affinity=cpus, threads=threads,
                                     scope="whole worker including parsing, plans, tool I/O, extraction and signoff")
        write_json(output / "summary.json", summary)
        (output / "HEAD").write_text("rev1\n")
        write_json(owner.parent / "worker-metrics.json", dict(
            elapsed_seconds=summary["resources"]["wall_seconds"],
            peak_rss_kib=summary["resources"]["peak_reaped_process_rss_kib"],
            rss_scope="maximum reaped worker/tool RSS; cgroup peak separately recorded in summary",
            request_id=request, cgroup=summary["resources"]["cgroup"]))
        print(json.dumps(dict(status=summary["status"], output=str(output),
                              qualification="requires_checked_flow_ingestion")))
        return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps(dict(status="failed", error=str(error))))
        raise SystemExit(2) from error

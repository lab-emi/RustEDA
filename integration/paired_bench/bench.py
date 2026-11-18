#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Repeated, counterbalanced RustEDA/LibreLane elapsed-time comparison with in-scope signoff.

    bench.py check     --plan PLAN --workspace DIR
    bench.py run       --plan PLAN --workspace DIR --phase NAME --base-rev REV [--max-samples N]
    bench.py select    --plan PLAN --workspace DIR --base-rev REV
    bench.py summarize --plan PLAN --workspace DIR
    bench.py status    --workspace DIR
    bench.py amend     --plan PLAN --workspace DIR --base-rev REV --reason TEXT
    bench.py invalidate --workspace DIR --base-rev REV --sample ID --reason TEXT
    bench.py note      --workspace DIR --base-rev REV --reason TEXT

Every sample runs its producer, then the shared signoff gates on the producer's own layout, in
two scopes with the plan's CPUs, memory and swap. A quiet-host gate precedes each sample. Each
sample reports seconds to GDS and to every endpoint gate set; an endpoint whose gates did not all
pass has no seconds. `select` applies the plan's pre-declared rule to the screening samples of
the tuned reference flow, and the confirmation phase repeats the selected arm against the
candidate in alternating AB/BA blocks. `summarize` writes `report.json` with per-endpoint
distributions, paired ratios and conservative verdicts (`stats.py`), per-arm output
determinism and the tuning disclosure of both flows. The workspace is append-only: a finished
sample is never rerun, a partial one is kept and refused (`--discard-partial` moves it aside).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import struct
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integration.paired_bench import measure, stats, verdicts  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PLAN_SCHEMA = "rusteda.paired-bench-plan.v1"
SAMPLE_SCHEMA = "rusteda.paired-bench-sample.v1"
REPORT_SCHEMA = "rusteda.paired-bench-report.v1"
HASH_CAP = 4 << 30
GDS_CAP = 1 << 30
LAYOUT_KEYS = ("gds", "def", "netlist", "powered_netlist", "synthesis_netlist")
HANDLE = re.compile(r"[A-Za-z0-9_-]+")


class BenchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


# ----------------------------------------------------------------------------- files and identities

def read(path) -> dict:
    return json.loads(Path(path).read_text())


def write(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    tmp.replace(path)


def sha256(path) -> str:
    path = Path(path)
    if path.stat().st_size > HASH_CAP:
        raise BenchError("INPUT_TOO_LARGE", f"{path} exceeds {HASH_CAP} bytes")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def gds_digest(path) -> str:
    """SHA-256 of the GDS record stream with the BGNLIB/BGNSTR date payloads zeroed, so two
    streams of the same geometry written at different times compare equal."""
    path = Path(path)
    if path.stat().st_size > GDS_CAP:
        raise BenchError("INPUT_TOO_LARGE", f"{path} exceeds {GDS_CAP} bytes")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while header := stream.read(4):
            if len(header) != 4:
                raise BenchError("GDS_TRUNCATED", str(path))
            size, kind, _ = struct.unpack(">HBB", header)
            if size < 4 or size % 2:
                raise BenchError("GDS_RECORD", f"{path}: record size {size}")
            payload = stream.read(size - 4)
            if len(payload) != size - 4:
                raise BenchError("GDS_TRUNCATED", str(path))
            digest.update(header)
            digest.update(bytes(len(payload)) if kind in (1, 5) else payload)
    return digest.hexdigest()


def identity(path, gds=False) -> dict:
    path = Path(path).resolve(strict=True)
    out = {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
    if gds:
        out["dates_masked_sha256"] = gds_digest(path)
    return out


# ----------------------------------------------------------------------------- plan

def expand(value, context: dict):
    """Replace `{root}`, `{sample}`, `{run_tag}` and `{inputs.NAME}` in strings, recursively."""
    if isinstance(value, str):
        def one(match):
            key = match[1]
            if key not in context:
                raise BenchError("PLAN_TEMPLATE", f"unknown placeholder {{{key}}}")
            return str(context[key])
        return re.sub(r"\{([a-z_]+(?:\.[A-Za-z0-9_]+)?)\}", one, value)
    if isinstance(value, list):
        return [expand(v, context) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, context) for k, v in value.items()}
    return value


class Plan:
    def __init__(self, path: Path):
        self.path = Path(path).resolve(strict=True)
        self.data = read(self.path)
        if self.data.get("schema") != PLAN_SCHEMA:
            raise BenchError("PLAN_SCHEMA", f"expected {PLAN_SCHEMA}")
        self.root = Path(self.data.get("root", ROOT)).resolve()
        base = {"root": str(self.root)}
        self.inputs = {name: dict(spec, path=expand(spec["path"], base))
                       for name, spec in self.data.get("inputs", {}).items()}
        self.context = base | {f"inputs.{n}": s["path"] for n, s in self.inputs.items()}
        self.resources = self.data["resources"]
        self.arms = self.data["arms"]
        self.phases = self.data["phases"]
        self.endpoints = self.data["endpoints"]
        self.gates = self.data["signoff"]["gates"]
        for name in [*self.arms, *self.phases, *self.endpoints]:
            if not HANDLE.fullmatch(name):
                raise BenchError("PLAN_HANDLE", f"{name!r} is not [A-Za-z0-9_-]+")
        for name, arm in self.arms.items():
            if arm["flow"] not in ("rusteda", "librelane") or arm["role"] not in ("candidate", "reference"):
                raise BenchError("PLAN_ARM", name)
            missing = set(LAYOUT_KEYS) - set(arm["layout"])
            if missing:
                raise BenchError("PLAN_ARM", f"{name}: layout lacks {sorted(missing)}")
        for name, gates in self.endpoints.items():
            unknown = set(gates) - set(self.gates)
            if unknown:
                raise BenchError("PLAN_ENDPOINT", f"{name}: {sorted(unknown)} not in signoff.gates")

    def ctx(self, **extra) -> dict:
        return self.context | extra

    def verify_inputs(self) -> dict:
        out = {}
        for name, spec in self.inputs.items():
            actual = sha256(spec["path"])
            if actual != spec["sha256"]:
                raise BenchError("INPUT_CHANGED", f"{name}: {spec['path']} is {actual}, plan pins {spec['sha256']}")
            out[name] = {"path": spec["path"], "sha256": actual, "bytes": Path(spec["path"]).stat().st_size}
        return out

    def schedule(self, phase: str, selected: str | None) -> list[dict]:
        spec = self.phases[phase]
        rows = []
        if "samples" in spec:
            for i, arm in enumerate(spec["samples"], 1):
                rows.append(dict(id=f"{phase}-{i:02d}-{arm}", arm=arm, block=None))
        else:
            pair = [selected if a == "@selected" else a for a in spec["pair"]]
            if None in pair:
                raise BenchError("NOT_SELECTED", f"phase {phase} needs `select` first")
            for b, order in enumerate(stats.alternating_blocks(*pair, spec["blocks"]), 1):
                for pos, arm in enumerate(order, 1):
                    rows.append(dict(id=f"{phase}-b{b}-{pos}-{arm}", arm=arm, block=f"b{b}"))
        for row in rows:
            if row["arm"] not in self.arms:
                raise BenchError("PLAN_ARM", f"unknown arm {row['arm']}")
        return rows


def render_librelane_config(plan: Plan, arm: dict) -> dict:
    """Template + substitutions + overlay (`null` removes a key: LibreLane's default applies). Strings
    in the substitutions and the overlay may use the plan's placeholders (`{root}`, `{inputs.NAME}`)."""
    spec = arm["librelane_config"]
    text = Path(expand(spec["template"], plan.ctx())).read_text()
    for key, value in expand(spec.get("substitute", {}), plan.ctx()).items():
        text = text.replace(key, value)
    config = json.loads(text)
    for key, value in expand(spec.get("overlay", {}), plan.ctx()).items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    if "expect_equal" in spec:
        expected = read(expand(spec["expect_equal"], plan.ctx()))
        if expected != config:
            diff = sorted(k for k in set(expected) | set(config) if expected.get(k) != config.get(k))
            raise BenchError("CONFIG_DRIFT", f"rendered config differs from {spec['expect_equal']} in {diff}")
    return config


def config_delta(plan: Plan, name: str) -> dict:
    """Knobs of a LibreLane arm that differ from the plan's published-recipe arm."""
    arm = plan.arms[name]
    base_name = plan.data.get("selection", {}).get("baseline_arm")
    if arm["flow"] != "librelane" or not base_name:
        return {}
    a, b = render_librelane_config(plan, plan.arms[base_name]), render_librelane_config(plan, arm)
    return {k: {"baseline": a.get(k), "arm": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


# ----------------------------------------------------------------------------- workspace

@contextlib.contextmanager
def locked(workspace: Path):
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "workspace.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BenchError("WORKSPACE_LOCKED", str(workspace)) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def head(workspace: Path) -> str:
    path = workspace / "HEAD"
    return path.read_text().strip() if path.exists() else "empty"


def freeze_plan(plan: Plan, workspace: Path) -> None:
    """The first mutating command copies the plan into the workspace; later commands refuse a plan
    whose bytes differ, so arms and the selection rule cannot change after results exist."""
    frozen = workspace / "plan.json"
    if not frozen.exists():
        shutil.copyfile(plan.path, frozen)
    elif sha256(frozen) != sha256(plan.path):
        raise BenchError("PLAN_CHANGED", f"{plan.path} differs from the frozen {frozen}")


AMENDABLE = {"arms", "phases", "estimates", "tuning_disclosure", "description"}


def amendment_problems(old: dict, new: dict) -> list[str]:
    """An amendment may only add arms and append screening samples; everything already declared
    (resources, gates, endpoints, selection rule, existing arms and confirmation) stays identical."""
    problems = [f"{k} changed" for k in sorted((set(old) | set(new)) - AMENDABLE) if old.get(k) != new.get(k)]
    for name, arm in old.get("arms", {}).items():
        if new.get("arms", {}).get(name) != arm:
            problems.append(f"arm {name} changed or removed")
    for name, phase in old.get("phases", {}).items():
        other = new.get("phases", {}).get(name)
        if other is None:
            problems.append(f"phase {name} removed")
        elif "samples" in phase:
            if other.get("samples", [])[:len(phase["samples"])] != phase["samples"] or set(other) != set(phase):
                problems.append(f"phase {name}: samples may only be appended")
        elif other != phase:
            problems.append(f"phase {name} changed")
    return problems


def cmd_amend(plan: Plan, workspace: Path, base_rev: str, reason: str) -> dict:
    with locked(workspace):
        check_base(workspace, base_rev)
        frozen = workspace / "plan.json"
        if (workspace / "selection.json").exists():
            raise BenchError("AMENDMENT_REFUSED", "the selection has been made")
        problems = amendment_problems(read(frozen), plan.data)
        if problems:
            raise BenchError("AMENDMENT_REFUSED", "; ".join(problems))
        old, new = read(frozen), plan.data
        n = len(list(workspace.glob("plan-v*.json"))) + 1
        shutil.copyfile(frozen, workspace / f"plan-v{n}.json")
        row = dict(time=time.strftime("%Y-%m-%dT%H:%M:%S%z"), head=head(workspace), reason=reason,
                   previous=identity(workspace / f"plan-v{n}.json"), amended=identity(plan.path),
                   added_arms=sorted(set(new["arms"]) - set(old["arms"])),
                   added_samples={p: v["samples"][len(old["phases"][p]["samples"]):]
                                  for p, v in new["phases"].items() if "samples" in v and p in old["phases"]})
        shutil.copyfile(plan.path, frozen)
        with (workspace / "amendments.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        row["head"] = advance(workspace)
        log(workspace, "amended", reason=reason, added_arms=row["added_arms"])
        return row


def cmd_invalidate(workspace: Path, base_rev: str, sample_id: str, reason: str) -> dict:
    """Move a finished sample aside (never deleted) with its reason, so it can be rerun. Used only for
    a harness defect; every invalidation is listed in the report."""
    with locked(workspace):
        check_base(workspace, base_rev)
        path = workspace / "samples" / sample_id
        if not HANDLE.fullmatch(sample_id) or not (path / "sample.json").exists():
            raise BenchError("NO_SAMPLE", sample_id)
        n = 1
        while path.with_name(f"{sample_id}.invalid-{n}").exists():
            n += 1
        target = path.with_name(f"{sample_id}.invalid-{n}")
        record = read(path / "sample.json")
        path.rename(target)
        row = dict(time=time.strftime("%Y-%m-%dT%H:%M:%S%z"), head=head(workspace), sample=sample_id,
                   moved_to=str(target), reason=reason,
                   endpoints={k: v.get("seconds") for k, v in record.get("endpoints", {}).items()})
        with (workspace / "invalidated.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        row["head"] = advance(workspace)
        log(workspace, "invalidated", sample=sample_id, reason=reason)
        return row


def cmd_note(workspace: Path, base_rev: str, reason: str) -> dict:
    """Append a correction or remark to the workspace record (`notes.jsonl`, listed in the report).
    Earlier records, including amendment reasons, are never edited."""
    with locked(workspace):
        check_base(workspace, base_rev)
        row = dict(time=time.strftime("%Y-%m-%dT%H:%M:%S%z"), head=head(workspace), note=reason)
        with (workspace / "notes.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        row["head"] = advance(workspace)
        return row


def check_base(workspace: Path, base_rev: str) -> None:
    current = head(workspace)
    if base_rev != current:
        raise BenchError("REVISION_CONFLICT", f"workspace HEAD is {current}, --base-rev {base_rev}")


def advance(workspace: Path) -> str:
    current = head(workspace)
    nxt = "rev1" if current == "empty" else f"rev{int(current[3:]) + 1}"
    (workspace / "HEAD").write_text(nxt + "\n")
    return nxt


def log(workspace: Path, event: str, **fields) -> None:
    row = dict(time=time.strftime("%Y-%m-%dT%H:%M:%S%z"), event=event, **fields)
    with (workspace / "progress.jsonl").open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)


# ----------------------------------------------------------------------------- one sample

def wait_quiet(cfg: dict, cpus: list[int], workspace: Path) -> dict:
    """Wait until `windows` consecutive windows show the host and the allowed CPUs idle and the
    package cool. Never signals another process; on timeout the sample runs and is flagged."""
    zone = measure.package_zone()
    window, windows, cap = cfg["window_seconds"], cfg["windows"], cfg["max_wait_seconds"]
    start = time.monotonic()
    calm, history = 0, []
    before, t0 = measure.cpu_busy(), time.monotonic()
    while True:
        time.sleep(window)
        after, t1 = measure.cpu_busy(), time.monotonic()
        dt = t1 - t0
        host = sum(after[c] - before[c] for c in before) / dt
        allowed = sum(after[c] - before[c] for c in cpus if c in before) / dt
        celsius = measure.package_celsius(zone)
        ok = (host <= cfg["max_host_busy_cpus"] and allowed <= cfg["max_allowed_set_busy_cpus"]
              and (celsius is None or celsius <= cfg["max_package_celsius"]))
        history.append(dict(host_busy_cpus=round(host, 3), allowed_set_busy_cpus=round(allowed, 3),
                            package_celsius=celsius, quiet=ok))
        history = history[-8:]
        calm = calm + 1 if ok else 0
        before, t0 = after, t1
        waited = round(t1 - start, 1)
        if calm >= windows:
            return dict(status="quiet", waited_seconds=waited, last_windows=history[-windows:])
        if t1 - start >= cap:
            log(workspace, "quiet_gate_timeout", waited_seconds=waited, last=history[-1])
            return dict(status="timeout_ran_flagged", waited_seconds=waited, last_windows=history[-windows:])


def resolve_layout(arm: dict, ctx: dict) -> dict:
    out = {}
    for key in LAYOUT_KEYS:
        pattern = expand(arm["layout"][key], ctx)
        found = sorted(glob.glob(pattern))
        if len(found) != 1:
            raise BenchError("LAYOUT_MISSING", f"{key}: {pattern} matched {len(found)} files")
        out[key] = found[0]
    return out


def librelane_diagnostics(run_dir: Path, keys=()) -> dict:
    """Per-step seconds, the antenna loop and the resolved values of `keys` of a LibreLane run."""
    steps = []
    for runtime in sorted(run_dir.glob("[0-9]*-*/runtime.txt")):
        h, m, s = runtime.read_text().strip().split(":")
        steps.append((runtime.parent.name, round(int(h) * 3600 + int(m) * 60 + float(s), 3)))
    loop = {}
    for log_path in run_dir.glob("*-openroad-detailedrouting/openroad-detailedrouting.log"):
        if log_path.stat().st_size > 256 << 20:
            continue
        text = log_path.read_text(errors="replace")
        loop = dict(repair_iterations=len(re.findall(r"Running antenna repair iteration", text)),
                    diodes_inserted=[int(x) for x in re.findall(r"Inserted (\d+) diodes", text)],
                    net_violations_found=[int(x) for x in re.findall(r"Found (\d+) net violations", text)])
    resolved_path = run_dir / "resolved.json"
    resolved = read(resolved_path) if resolved_path.is_file() and resolved_path.stat().st_size < 16 << 20 else {}
    return dict(total_step_seconds=round(sum(s for _, s in steps), 3),
                slowest_steps=sorted(steps, key=lambda r: -r[1])[:8], detailed_route_antenna_loop=loop,
                resolved={k: resolved.get(k) for k in keys})


def environment(plan: Plan, arm: dict, ctx: dict, worker: str | None, ownership: Path | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("RUSTEDA_WORKER_ID", "RUSTEDA_OWNERSHIP")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(expand(arm["producer"].get("env", {}), ctx))
    if worker is not None:
        env["RUSTEDA_WORKER_ID"] = worker
        env["RUSTEDA_OWNERSHIP"] = str(ownership)
    return env


def run_sample(plan: Plan, workspace: Path, row: dict, *, period: float = 2.0, launcher=None) -> dict:
    arm_name, arm = row["arm"], plan.arms[row["arm"]]
    sample = workspace / "samples" / row["id"]
    ctx = plan.ctx(sample=str(sample), run_tag="bench")
    record = dict(schema=SAMPLE_SCHEMA, id=row["id"], arm=arm_name, flow=arm["flow"], role=arm["role"],
                  block=row["block"], status="running")
    record["inputs"] = plan.verify_inputs()
    record["quiet_gate"] = (wait_quiet(plan.data["quiet_host"], plan.resources["cpus"], workspace)
                            if launcher is None else dict(status="skipped_test_launcher"))
    sample.mkdir(parents=True)
    write(sample / "sample.json", record)
    if arm["flow"] == "librelane":
        config = render_librelane_config(plan, arm)
        write(sample / "design" / "config.json", config)
        record["librelane_config"] = identity(sample / "design" / "config.json")
        record["config_delta_from_baseline"] = config_delta(plan, arm_name)
        (sample / "tmp").mkdir()
    worker = secrets.token_hex(32) if arm["producer"].get("worker_scope") else None
    # The producer creates it (OWNERSHIP_EXISTS). A producer that keeps it beside its own output
    # directory names it with `producer.ownership`.
    ownership = Path(expand(arm["producer"].get("ownership", "{sample}/ownership"), ctx)) if worker else None
    res = plan.resources
    log(workspace, "producer_start", sample=row["id"], arm=arm_name)
    producer = measure.run(f"{row['id']}-producer", sample / "producer" / "measure",
                           expand(arm["producer"]["argv"], ctx), cpus=res["cpus"], memory_mib=res["memory_mib"],
                           swap_mib=res["swap_mib"], deadline_seconds=arm["producer"]["deadline_seconds"],
                           worker_id=worker, env=environment(plan, arm, ctx, worker, ownership),
                           cwd=sample / "design" if arm["flow"] == "librelane" else sample,
                           stdout=sample / "producer" / "console.log", period=period, launcher=launcher)
    record["producer"] = dict(exit_code=producer["exit_code"], wall_seconds=producer["wall_seconds"],
                              cpu_seconds=producer["cpu_seconds"], peak_bytes=producer["cgroup_v2_memory_peak_bytes"],
                              swap_peak_bytes=producer["cgroup_v2_swap_peak_bytes"],
                              other_load=producer["other_load"], thermal=producer["thermal"],
                              resources=identity(sample / "producer" / "measure" / "resources.json"))
    log(workspace, "producer_end", sample=row["id"], exit_code=producer["exit_code"],
        wall_seconds=producer["wall_seconds"])
    layout = None
    if producer["exit_code"] == 0:
        try:
            layout = resolve_layout(arm, ctx)
        except BenchError as error:
            record["layout_error"] = str(error)
    if arm["flow"] == "librelane" and (sample / "design" / "runs" / "bench").is_dir():
        record["librelane_diagnostics"] = librelane_diagnostics(sample / "design" / "runs" / "bench",
                                                                record.get("config_delta_from_baseline", {}))
    gds_seconds = producer["wall_seconds"] if layout else None
    if layout:
        record["layout"] = {k: identity(v, gds=(k == "gds")) for k, v in layout.items()}
        write(sample / "layout.json", layout)
        expected = arm.get("expected_outputs", {})
        record["matches_expected"] = {k: record["layout"][k]["sha256"] == v or
                                      record["layout"][k].get("dates_masked_sha256") == v
                                      for k, v in expected.items()}
        signoff = plan.data["signoff"]
        sworker = secrets.token_hex(32)
        sown = sample / "signoff" / "ownership"
        manifest = Path(expand(signoff["ownership_manifest"], ctx))
        write(sown / "native.json", dict(request_id=sworker, implementation="native",
                                         manifest=identity(manifest)))
        senv = {k: v for k, v in os.environ.items() if k not in ("RUSTEDA_WORKER_ID", "RUSTEDA_OWNERSHIP")}
        senv.update(PYTHONDONTWRITEBYTECODE="1", RUSTEDA_WORKER_ID=sworker, RUSTEDA_OWNERSHIP=str(sown))
        log(workspace, "signoff_start", sample=row["id"])
        gate_run = measure.run(f"{row['id']}-signoff", sample / "signoff" / "measure",
                               expand([*signoff["argv"], "--gates", ",".join(plan.gates)], ctx),
                               cpus=res["cpus"], memory_mib=res["memory_mib"], swap_mib=res["swap_mib"],
                               deadline_seconds=signoff["deadline_seconds"], worker_id=sworker, env=senv,
                               cwd=sample, stdout=sample / "signoff" / "console.log", period=period,
                               launcher=launcher)
        summary_path = sample / "signoff" / "gates" / "summary.json"
        summary = read(summary_path) if summary_path.exists() else None
        rows = verdicts.gates(summary, plan.gates)
        record["signoff"] = dict(exit_code=gate_run["exit_code"], wall_seconds=gate_run["wall_seconds"],
                                 peak_bytes=gate_run["cgroup_v2_memory_peak_bytes"],
                                 other_load=gate_run["other_load"], thermal=gate_run["thermal"], gates=rows,
                                 summary=identity(summary_path) if summary else None)
        log(workspace, "signoff_end", sample=row["id"], wall_seconds=gate_run["wall_seconds"],
            verdicts={k: v["verdict"] for k, v in rows.items()})
    else:
        rows = {}
    record["endpoints"] = {"gds": dict(seconds=gds_seconds, blocking={} if gds_seconds else {"producer": "failed"})}
    for name, names in plan.endpoints.items():
        if name != "gds":
            record["endpoints"][name] = verdicts.endpoint(gds_seconds, rows, names)
    limit = plan.data.get("flags", {})
    flags = []
    for part in ("producer", "signoff"):
        load = (record.get(part) or {}).get("other_load") or {}
        if (load.get("mean_other_busy_cpus_on_allowed_set") or 0) > limit.get("max_other_busy_cpus_on_allowed_set", 0.25):
            flags.append(f"{part}_foreign_load_on_allowed_cpus")
        if (load.get("mean_other_busy_cpus_host") or 0) > limit.get("max_other_busy_cpus_host", 4.0):
            flags.append(f"{part}_foreign_load_on_host")
        if ((record.get(part) or {}).get("swap_peak_bytes") or 0) > 0:
            flags.append(f"{part}_swapped")
    if record["quiet_gate"]["status"].startswith("timeout"):
        flags.append("quiet_gate_timeout")
    record["flags"] = flags
    record["status"] = "complete"
    write(sample / "sample.json", record)
    return record


# ----------------------------------------------------------------------------- commands

def completed(workspace: Path) -> dict[str, dict]:
    out = {}
    for path in sorted((workspace / "samples").glob("*/sample.json")):
        data = read(path)
        if data.get("status") == "complete" and path.parent.name == data["id"]:   # not moved-aside copies
            out[data["id"]] = data
    return out


def selected_arm(workspace: Path) -> str | None:
    path = workspace / "selection.json"
    return read(path)["selected"] if path.exists() else None


def preflight(plan: Plan, workspace: Path, rows: list[dict]) -> dict:
    est = plan.data.get("estimates", {})
    seconds = sum(est.get(r["arm"], {}).get("sample_seconds", 0) for r in rows)
    disk = sum(est.get(r["arm"], {}).get("disk_mib", 0) for r in rows)
    peak = max([est.get(r["arm"], {}).get("peak_mib", 0) for r in rows] or [0])
    workspace.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(workspace).free >> 20
    avail = None
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            avail = int(line.split()[1]) >> 10
    out = dict(samples=len(rows), estimated_seconds=seconds, estimated_disk_mib=disk, estimated_peak_mib=peak,
               scope_memory_mib=plan.resources["memory_mib"], free_disk_mib=free, mem_available_mib=avail,
               disk_after_mib=free - disk)
    if peak > plan.resources["memory_mib"]:
        raise BenchError("BUDGET", f"estimated peak {peak} MiB exceeds the scope's {plan.resources['memory_mib']}")
    floor = plan.data.get("budgets", {}).get("min_free_disk_gib", 100) << 10
    if free - disk < floor:
        raise BenchError("DISK_BUDGET", f"{free - disk} MiB would remain, below {floor} MiB")
    if avail is not None and avail < plan.resources["memory_mib"] + 4096:
        raise BenchError("MEMORY_BUDGET", f"{avail} MiB available")
    return out


def cmd_check(plan: Plan, workspace: Path) -> dict:
    inputs = plan.verify_inputs()
    configs = {}
    for name, arm in plan.arms.items():
        if arm["flow"] == "librelane":
            config = render_librelane_config(plan, arm)
            configs[name] = dict(keys=len(config), delta=config_delta(plan, name))
    sel = selected_arm(workspace)
    schedules = {}
    for phase in plan.phases:
        try:
            schedules[phase] = [r["id"] for r in plan.schedule(phase, sel)]
        except BenchError as error:
            schedules[phase] = str(error)
    rows = [r for p in plan.phases if isinstance(schedules[p], list) for r in plan.schedule(p, sel)]
    return dict(inputs=inputs, librelane_configs=configs, schedules=schedules, head=head(workspace),
                preflight=preflight(plan, workspace, rows))


def cmd_run(plan: Plan, workspace: Path, phase: str, base_rev: str, max_samples: int | None,
            discard_partial: bool, *, period: float = 2.0, launcher=None) -> dict:
    with locked(workspace):
        check_base(workspace, base_rev)
        freeze_plan(plan, workspace)
        rows = plan.schedule(phase, selected_arm(workspace))
        done = completed(workspace)
        todo = [r for r in rows if r["id"] not in done]
        preflight(plan, workspace, todo)
        ran = []
        for row in todo[:max_samples]:
            path = workspace / "samples" / row["id"]
            if path.exists():
                if not discard_partial:
                    raise BenchError("PARTIAL_SAMPLE", f"{path} exists without a complete record")
                n = 1
                while path.with_name(f"{row['id']}.partial-{n}").exists():
                    n += 1
                path.rename(path.with_name(f"{row['id']}.partial-{n}"))
            record = run_sample(plan, workspace, row, period=period, launcher=launcher)
            ran.append(dict(id=row["id"], endpoints={k: v["seconds"] for k, v in record["endpoints"].items()},
                            flags=record["flags"]))
            log(workspace, "sample_complete", sample=row["id"], head=advance(workspace))
        return dict(phase=phase, ran=ran, remaining=len(todo) - len(ran), head=head(workspace))


def cmd_select(plan: Plan, workspace: Path, base_rev: str) -> dict:
    with locked(workspace):
        check_base(workspace, base_rev)
        freeze_plan(plan, workspace)
        rule = plan.data["selection"]
        done = completed(workspace)
        rows = [r for r in plan.schedule(rule["from_phase"], None) if plan.arms[r["arm"]]["flow"] == rule["flow"]]
        missing = [r["id"] for r in rows if r["id"] not in done]
        if missing:
            raise BenchError("SCREEN_INCOMPLETE", f"missing {missing}")
        table = []
        for r in rows:
            e = done[r["id"]]["endpoints"]
            table.append(dict(arm=r["arm"], sample=r["id"], gds_seconds=e["gds"]["seconds"],
                              physical_seconds=e[rule["physical_endpoint"]]["seconds"],
                              target_seconds=e[rule["target_endpoint"]]["seconds"],
                              target_blocking=e[rule["target_endpoint"]]["blocking"],
                              physical_blocking=e[rule["physical_endpoint"]]["blocking"]))
        reached = [t for t in table if t["target_seconds"] is not None]
        physical = [t for t in table if t["physical_seconds"] is not None]
        produced = [t for t in table if t["gds_seconds"] is not None]
        if reached:
            pick, reason = min(reached, key=lambda t: t["target_seconds"]), f"fastest to {rule['target_endpoint']}"
        elif physical:
            pick = min(physical, key=lambda t: t["gds_seconds"])
            reason = f"no arm reached {rule['target_endpoint']}; fastest GDS among arms passing {rule['physical_endpoint']}"
        elif produced:
            pick, reason = min(produced, key=lambda t: t["gds_seconds"]), "no arm passed the physical gates; fastest GDS"
        else:
            raise BenchError("NO_ARM", "no screened arm produced a layout")
        data = dict(selected=pick["arm"], reason=reason, rule=rule, candidates=table,
                    deltas={t["arm"]: config_delta(plan, t["arm"]) for t in table})
        write(workspace / "selection.json", data)
        data["head"] = advance(workspace)
        log(workspace, "selected", arm=pick["arm"], reason=reason)
        return data


def determinism(samples: list[dict]) -> dict:
    keys = {}
    for s in samples:
        for k, v in (s.get("layout") or {}).items():
            keys.setdefault(k, set()).add(v["sha256"])
            if "dates_masked_sha256" in v:
                keys.setdefault(f"{k}_dates_masked", set()).add(v["dates_masked_sha256"])
    return {k: dict(distinct=len(v), identical=len(v) == 1) for k, v in sorted(keys.items())}


def host_facts() -> dict:
    model = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                  if line.startswith("model name")), None)
    return dict(cpu_model=model, kernel=platform.release(), cpus_online=os.cpu_count())


def cmd_summarize(plan: Plan, workspace: Path) -> dict:
    done = completed(workspace)
    sel = selected_arm(workspace)
    report = dict(schema=REPORT_SCHEMA, plan=identity(plan.path), plan_name=plan.data["name"], host=host_facts(),
                  resources=plan.resources, endpoints=plan.endpoints, head=head(workspace),
                  tuning_disclosure=plan.data.get("tuning_disclosure", {}), phases={})
    if (workspace / "selection.json").exists():
        report["selection"] = read(workspace / "selection.json")
    for name in ("amendments", "invalidated", "notes"):
        path = workspace / f"{name}.jsonl"
        report[name] = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    for phase in plan.phases:
        try:
            rows = plan.schedule(phase, sel)
        except BenchError as error:
            report["phases"][phase] = dict(status=str(error))
            continue
        samples = [done[r["id"]] for r in rows if r["id"] in done]
        table = [dict(id=s["id"], arm=s["arm"], block=s["block"],
                      seconds={k: v["seconds"] for k, v in s["endpoints"].items()},
                      blocking={k: v["blocking"] for k, v in s["endpoints"].items() if v["blocking"]},
                      producer_peak_bytes=s["producer"]["peak_bytes"], flags=s["flags"],
                      verdicts={k: v["verdict"] for k, v in (s.get("signoff") or {}).get("gates", {}).items()})
                 for s in samples]
        by_arm = {}
        for s in samples:
            by_arm.setdefault(s["arm"], []).append(s)
        arms = {a: dict(samples=len(v), determinism=determinism(v),
                        endpoints={e: stats.describe([s["endpoints"][e]["seconds"] for s in v
                                                      if s["endpoints"][e]["seconds"] is not None])
                                   for e in ["gds", *[x for x in plan.endpoints if x != "gds"]]})
                for a, v in by_arm.items()}
        out = dict(complete=len(samples), scheduled=len(rows), samples=table, arms=arms)
        spec = plan.phases[phase]
        if "pair" in spec and sel is not None:
            pair = [sel if a == "@selected" else a for a in spec["pair"]]
            ref = next(a for a in pair if plan.arms[a]["role"] == "reference")
            cand = next(a for a in pair if plan.arms[a]["role"] == "candidate")
            comparisons = {}
            for e in ["gds", *[x for x in plan.endpoints if x != "gds"]]:
                pick = lambda arm: {s["block"]: s["endpoints"][e]["seconds"] for s in samples if s["arm"] == arm}
                comparisons[e] = stats.compare(pick(ref), pick(cand))
            out.update(reference=ref, candidate=cand, comparisons=comparisons,
                       flagged_samples=[s["id"] for s in samples if s["flags"]])
        report["phases"][phase] = out
    write(workspace / "report.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "run", "select", "summarize", "status", "amend", "invalidate", "note"))
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--phase")
    parser.add_argument("--base-rev")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--reason", help="amend, invalidate: why (recorded in the report)")
    parser.add_argument("--sample", help="invalidate: the sample id")
    parser.add_argument("--discard-partial", action="store_true",
                        help="move an unfinished sample directory aside (never deleted) and rerun it")
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    try:
        if args.command in ("amend", "invalidate", "note") and not (args.reason and args.base_rev):
            raise BenchError("USAGE", f"{args.command} needs --reason and --base-rev")
        if args.command == "status":
            out = dict(head=head(workspace), complete=sorted(completed(workspace)), selected=selected_arm(workspace))
        elif args.command == "note":
            out = cmd_note(workspace, args.base_rev, args.reason)
        elif args.command == "invalidate":
            if not args.sample:
                raise BenchError("USAGE", "invalidate needs --sample")
            out = cmd_invalidate(workspace, args.base_rev, args.sample, args.reason)
        else:
            if args.plan is None:
                raise BenchError("USAGE", "--plan is required")
            plan = Plan(args.plan)
            if args.command in ("run", "select") and not args.base_rev:
                raise BenchError("USAGE", "--base-rev is required for mutating commands")
            if args.command == "check":
                out = cmd_check(plan, workspace)
            elif args.command == "run":
                if args.phase not in plan.phases:
                    raise BenchError("USAGE", f"--phase must be one of {sorted(plan.phases)}")
                out = cmd_run(plan, workspace, args.phase, args.base_rev, args.max_samples, args.discard_partial)
            elif args.command == "select":
                out = cmd_select(plan, workspace, args.base_rev)
            elif args.command == "amend":
                out = cmd_amend(plan, workspace, args.base_rev, args.reason)
            else:
                out = cmd_summarize(plan, workspace)
    except BenchError as error:
        print(json.dumps(dict(status="error", code=error.code, message=str(error))), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

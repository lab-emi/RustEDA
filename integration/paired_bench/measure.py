# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""One measured command in its own transient systemd user scope (cgroup v2).

`run()` places the command in a scope with `MemoryMax`, `MemorySwapMax`, `TasksMax` and
`AllowedCPUs`, under `timeout` and GNU `time -v`, and samples the scope's memory and CPU, the
load average, the CPU package temperature and foreign runnable processes on the allowed CPUs
every `period` seconds from this foreground process (no daemon). It writes
`<outdir>/resources.json` (`rusteda.host-measured-run.v1`, the schema of the 2026-11-10
campaign's `measured_run.py`, plus `thermal`) and returns that record.

With `worker_id` (64 hex digits) the scope is the RustEDA worker scope
`rusteda-worker-<first 24 digits>.scope` whose description the recipe helpers verify; they then
inherit the scope instead of creating their own, so every child stays on the same CPUs.
"""
from __future__ import annotations

import datetime
import glob
import json
import os
from pathlib import Path
import re
import subprocess
import time

GNU_TIME = "/usr/bin/time"
SCHEMA = "rusteda.host-measured-run.v1"


def _show(unit: str) -> dict:
    out = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "ControlGroup",
                          "-p", "Description"], capture_output=True, text=True, timeout=10).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _read_int(path) -> int | None:
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def cpu_busy() -> dict[int, float]:
    """Busy seconds per logical CPU since boot (user+nice+system+irq+softirq+steal), /proc/stat."""
    tick = os.sysconf("SC_CLK_TCK")
    busy = {}
    for line in Path("/proc/stat").read_text().splitlines():
        f = line.split()
        if f and re.fullmatch(r"cpu\d+", f[0]):
            v = [int(x) for x in f[1:9]]
            busy[int(f[0][3:])] = (v[0] + v[1] + v[2] + v[5] + v[6] + v[7]) / tick
    return busy


def package_zone() -> str | None:
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        try:
            if Path(zone, "type").read_text().strip() == "x86_pkg_temp":
                return str(Path(zone, "temp"))
        except OSError:
            continue
    return None


def package_celsius(zone: str | None) -> float | None:
    value = _read_int(zone) if zone else None
    return None if value is None else value / 1000


def throttle_ms() -> int | None:
    return _read_int("/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_total_time_ms")


def foreign_runnable(pinned: set[int], group: Path | None) -> int | None:
    """Runnable processes of other cgroups whose last CPU is in `pinned` (one `ps`, about 20 ms)."""
    own = set()
    if group is not None:
        try:
            own = {int(x) for x in (group / "cgroup.procs").read_text().split()}
        except (OSError, ValueError):
            pass
    try:
        out = subprocess.run(["ps", "-eo", "psr=,stat=,pid=,comm="], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    count = 0
    for line in out.splitlines():
        f = line.split(None, 3)
        if len(f) == 4 and f[1].startswith("R") and int(f[0]) in pinned and int(f[2]) not in own and f[3] != "ps":
            count += 1
    return count


def _gnu_time(text: str) -> dict:
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
    user = re.search(r"User time \(seconds\): ([\d.]+)", text)
    system = re.search(r"System time \(seconds\): ([\d.]+)", text)
    elapsed = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", text)
    wall = None
    if elapsed:
        parts = [float(p) for p in elapsed[1].split(":")]
        wall = parts[-1] + 60 * parts[-2] + (3600 * parts[-3] if len(parts) == 3 else 0)
    return dict(rss_kib=int(rss[1]) if rss else None, wall=wall,
                cpu=float(user[1]) + float(system[1]) if user and system else None)


def run(name: str, outdir: Path, argv: list[str], *, cpus: list[int], memory_mib: int, swap_mib: int,
        deadline_seconds: int, worker_id: str | None = None, env: dict | None = None, cwd: Path | None = None,
        stdout: Path | None = None, period: float = 2.0, launcher: list[str] | None = None) -> dict:
    """Run `argv` measured; see the module docstring. `launcher` replaces the systemd scope prefix
    (tests only: it must still run `timeout ... time -v -o <file> argv`)."""
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    if worker_id is not None:
        if not re.fullmatch("[a-f0-9]{64}", worker_id):
            raise ValueError("invalid worker id")
        unit, description = f"rusteda-worker-{worker_id[:24]}.scope", f"RustEDA worker {worker_id}"
    else:
        unit = f"paired-bench-{re.sub('[^A-Za-z0-9]', '-', name)}-{os.getpid()}.scope"
        description = f"RustEDA paired benchmark {name}"
    time_file = outdir / "time.txt"
    cpu_list = ",".join(map(str, cpus))
    scope = launcher if launcher is not None else [
        "systemd-run", "--user", "--scope", "-q", "--unit", unit, "--description", description,
        "-p", f"MemoryMax={memory_mib}M", "-p", f"MemorySwapMax={swap_mib}M", "-p", "TasksMax=4096",
        "-p", f"AllowedCPUs={cpu_list}"]
    full = [*scope, "timeout", "--signal=TERM", "--kill-after=30s", f"{deadline_seconds}s",
            GNU_TIME, "-v", "-o", str(time_file), *argv]
    (outdir / "command.json").write_text(json.dumps(full, indent=1) + "\n")
    environment = dict(os.environ) if env is None else dict(env)   # `env` is the complete environment
    zone = package_zone()
    started = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    load_start = Path("/proc/loadavg").read_text().split()[:3]
    busy_start, throttle_start = cpu_busy(), throttle_ms()
    temps = []
    tick = time.monotonic()
    sink = stdout.open("w") if stdout is not None else subprocess.DEVNULL
    try:
        process = subprocess.Popen(full, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT
                                   if stdout is not None else None, env=environment, cwd=cwd)
        loads, group, peak, swap_peak, current_peak, samples, own_usec = [], None, None, None, None, 0, None
        foreign, pinned = [], set(cpus)
        try:
            while process.poll() is None:
                if group is None and launcher is None:
                    state = _show(unit)
                    if state.get("Description") == description and state.get("ControlGroup"):
                        group = Path("/sys/fs/cgroup") / state["ControlGroup"].lstrip("/")
                loads.append(float(Path("/proc/loadavg").read_text().split()[0]))
                celsius = package_celsius(zone)
                if celsius is not None:
                    temps.append(celsius)
                samples += 1
                if samples % 5 == 1:
                    value = foreign_runnable(pinned, group)
                    if value is not None:
                        foreign.append(value)
                if group is not None:
                    for file, key in (("memory.peak", "peak"), ("memory.swap.peak", "swap"),
                                      ("memory.current", "current")):
                        value = _read_int(group / file)
                        if value is None:
                            continue
                        if key == "peak":
                            peak = max(peak or 0, value)
                        elif key == "swap":
                            swap_peak = max(swap_peak or 0, value)
                        else:
                            current_peak = max(current_peak or 0, value)
                    try:   # the scope's own CPU time (all its processes, also detached ones)
                        usage = re.search(r"usage_usec (\d+)", (group / "cpu.stat").read_text())
                        own_usec = max(own_usec or 0, int(usage[1])) if usage else own_usec
                    except OSError:
                        pass
                try:
                    process.wait(timeout=period)
                except subprocess.TimeoutExpired:
                    pass
            code = process.returncode
        finally:
            detached = False
            if launcher is None:
                state = _show(unit)
                detached = state.get("ActiveState") == "active" and state.get("Description") == description
                if detached:
                    subprocess.run(["systemctl", "--user", "kill", "--kill-whom=all", "--signal=KILL", unit],
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    finally:
        if stdout is not None:
            sink.close()
    wall = time.monotonic() - tick
    busy_end, throttle_end = cpu_busy(), throttle_ms()
    load_end = Path("/proc/loadavg").read_text().split()[:3]
    timed = _gnu_time(time_file.read_text() if time_file.is_file() else "")
    own_cpu = timed["cpu"]
    if own_usec is not None:   # the last cgroup sample can be up to one period old; take the larger figure
        own_cpu = max(own_cpu or 0.0, own_usec / 1e6)
    host_busy = sum(busy_end[c] - busy_start[c] for c in busy_start)
    set_busy = sum(busy_end[c] - busy_start[c] for c in cpus if c in busy_start)
    other_host = host_busy - own_cpu if own_cpu is not None else None
    other_set = set_busy - own_cpu if own_cpu is not None else None
    interference = dict(
        host_busy_cpu_seconds=round(host_busy, 2), allowed_set_busy_cpu_seconds=round(set_busy, 2),
        own_cpu_seconds=round(own_cpu, 2) if own_cpu is not None else None,
        other_cpu_seconds_host=round(other_host, 2) if other_host is not None else None,
        other_cpu_seconds_on_allowed_set=round(other_set, 2) if other_set is not None else None,
        mean_other_busy_cpus_host=round(other_host / wall, 3) if other_host is not None and wall > 0 else None,
        mean_other_busy_cpus_on_allowed_set=round(other_set / wall, 3) if other_set is not None and wall > 0
        else None,
        note="/proc/stat busy time of all CPUs (and of the allowed set) during the run minus the scope's own CPU "
             "time: work of other processes on this host beside the timed stage")
    record = dict(
        schema=SCHEMA, name=name, command=" ".join(argv), started=started,
        wall_seconds=round(timed["wall"] if timed["wall"] is not None else wall, 3),
        executor_wall_seconds=round(wall, 3), exit_code=code, gnu_time_max_rss_kib=timed["rss_kib"],
        cpu_seconds=round(timed["cpu"], 2) if timed["cpu"] is not None else None,
        unit=unit, cgroup="v2 (systemd user scope)" if launcher is None else "test launcher",
        cgroup_v2_memory_peak_bytes=peak, cgroup_v2_swap_peak_bytes=swap_peak,
        sampled_peak_memory_current_bytes=current_peak,
        cgroup_limit_bytes=memory_mib << 20, cgroup_swap_limit_bytes=swap_mib << 20, allowed_cpus=list(cpus),
        loadavg_at_start=[float(x) for x in load_start], loadavg_at_end=[float(x) for x in load_end],
        foreign_runnable_on_allowed_cpus=dict(
            mean=round(sum(foreign) / len(foreign), 2) if foreign else None, max=max(foreign) if foreign else None,
            samples=len(foreign), period_seconds=5 * period,
            note="runnable processes outside this scope whose last CPU was one of the allowed CPUs (ps psr, stat R)"),
        loadavg_1m_mean=round(sum(loads) / len(loads), 2) if loads else None,
        loadavg_1m_max=round(max(loads), 2) if loads else None, samples=samples, sample_period_seconds=period,
        cpus_online=os.cpu_count(), detached_processes_killed=bool(detached), other_load=interference,
        thermal=dict(package_celsius_max=max(temps) if temps else None,
                     package_celsius_mean=round(sum(temps) / len(temps), 1) if temps else None,
                     package_throttle_ms=(throttle_end - throttle_start)
                     if throttle_end is not None and throttle_start is not None else None))
    (outdir / "resources.json").write_text(json.dumps(record, indent=1) + "\n")
    return record

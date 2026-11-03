#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Module head-to-heads replayed from a completed native producer output.

Each contender re-executes the exact command the flow recorded, on the same
inputs, in a fresh directory, under GNU time: one warm-up and `--reps`
measured runs, interleaved per repetition, at each requested thread count.

* extraction: RustROAD `extract` (native RC, `RAYON_NUM_THREADS`) against
  OpenROAD OpenRCX (`extract_parasitics`, `set_thread_count`) on the same
  routed DEF/workspace; SPEF totals compared afterwards.
* timing: the RustSTA pipeline recorded by `signoff_timing.py` (init, import,
  run, eight reports) against the two OpenSTA processes it runs (TT, SS) on
  the same netlist, Liberty, SDC and native SPEF.
* gds: RustROAD `export-gds` against KLayout's LEF/DEF reader
  (`kl_def2gds.py`) on the same final DEF and cell GDS.

Writes `<out>/module-bench.json`. The host should be otherwise idle.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import time

TIME = "/opt/eda/time/bin/time"
ROOT = Path(__file__).resolve().parents[2]


def with_threads(argv, threads):
    """RustROAD's pool size is the global --threads option (it overrides
    RAYON_NUM_THREADS); set it before the subcommand, replacing any value."""
    argv = list(argv)
    if "--threads" in argv:
        argv[argv.index("--threads") + 1] = str(threads)
    else:
        argv[3:3] = ["--threads", str(threads)]  # after [binary, --workspace, path]
    return argv


def strip_time(argv):
    argv = [str(a) for a in argv]
    if argv and argv[0].endswith("/time") and argv[1] == "-v" and argv[2] == "-o":
        return argv[4:]
    return argv


def measure(argv, cwd, env=None, stdout=None):
    tf = Path(cwd) / f".time-{time.monotonic_ns()}"
    tick = time.monotonic()
    with open(stdout or os.devnull, "w") as out:
        result = subprocess.run([TIME, "-v", "-o", str(tf), *argv], cwd=cwd, stdout=out,
                                stderr=subprocess.STDOUT, env={**os.environ, **(env or {})})
    wall = time.monotonic() - tick
    text = tf.read_text()
    rss = int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)[1])
    user = float(re.search(r"User time \(seconds\): ([\d.]+)", text)[1])
    sys_ = float(re.search(r"System time \(seconds\): ([\d.]+)", text)[1])
    return dict(exit_code=result.returncode, wall_seconds=round(wall, 3), peak_rss_kib=rss,
                cpu_seconds=round(user + sys_, 2))


def summarize(rows):
    ok = [r for r in rows if r["exit_code"] == 0]
    return dict(runs=rows, measured=len(rows), failed=len(rows) - len(ok),
                median_wall_seconds=round(statistics.median(r["wall_seconds"] for r in ok), 3) if ok else None,
                min_wall_seconds=round(min(r["wall_seconds"] for r in ok), 3) if ok else None,
                worst_peak_rss_kib=max(r["peak_rss_kib"] for r in ok) if ok else None)


def spef_totals(path):
    total_c = total_r = 0.0
    nets = 0
    section = None
    cu = 1e-12
    for line in open(path):
        s = line.split()
        if not s:
            continue
        if s[0] == "*C_UNIT":
            cu = float(s[1]) * {"PF": 1e-12, "FF": 1e-15}[s[2].upper()]
        elif s[0] == "*D_NET":
            nets += 1
            section = None
        elif s[0] in ("*CAP", "*RES"):
            section = s[0]
        elif s[0] in ("*CONN", "*END"):
            section = None
        elif section == "*CAP" and s[0].isdigit():
            total_c += float(s[-1]) * cu
        elif section == "*RES" and s[0].isdigit():
            total_r += float(s[-1])
    return dict(nets=nets, total_cap_pf=round(total_c * 1e12, 3), total_res_kohm=round(total_r / 1e3, 3))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="completed native producer output directory")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--threads", default="2,4")
    parser.add_argument("--sections", default="extraction,timing,gds")
    parser.add_argument("--cell-lef", default="/opt/eda/derived/native-combined.lef",
                        help="combined EZ130+SRAM cell LEF used by the flow")
    args = parser.parse_args()
    o = args.output.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    threads = [int(t) for t in args.threads.split(",")]
    sections = set(args.sections.split(","))
    result = dict(schema="rusteda.host-module-bench.v1", producer_output=str(o), reps=args.reps,
                  threads=threads, host_cpus=os.cpu_count())

    if "extraction" in sections:
        native = strip_time(json.loads((o / "logs/be_extract.command.json").read_text())["argv"])
        tcl = (o / "backend/openrcx/extract.tcl").read_text()
        rows = {f"rustroad_extract_t{t}": [] for t in threads} | {f"openrcx_t{t}": [] for t in threads}
        for rep in range(args.reps + 1):
            for t in threads:
                work = out / f"extract-r{rep}-t{t}"
                shutil.rmtree(work, ignore_errors=True)
                work.mkdir(parents=True)
                argv = [a if not a.endswith("native.nom.spef") else str(work / "native.spef") for a in native]
                argv = with_threads(argv, t)
                r1 = measure(argv, work, env={"RAYON_NUM_THREADS": str(t), "OMP_NUM_THREADS": str(t)})
                script = tcl.replace("set_thread_count 2", f"set_thread_count {t}")
                script = re.sub(r'write_spef "[^"]+"', f'write_spef "{work / "openrcx.spef"}"', script)
                (work / "extract.tcl").write_text(script)
                r2 = measure(["/opt/eda/wrap/openroad", "-exit", str(work / "extract.tcl")], work)
                if rep:
                    rows[f"rustroad_extract_t{t}"].append(r1)
                    rows[f"openrcx_t{t}"].append(r2)
                if rep < args.reps or t != threads[-1]:
                    shutil.rmtree(work)  # disk budget: keep only the last outputs
        last = out / f"extract-r{args.reps}-t{threads[-1]}"
        result["extraction"] = dict(contenders={k: summarize(v) for k, v in rows.items()},
                                    spef=dict(rustroad=spef_totals(last / "native.spef"),
                                              openrcx=spef_totals(last / "openrcx.spef")))

    if "timing" in sections:
        tdir = o / "backend/timing"
        logs = tdir / "logs"
        rust_steps = ["native-init", "native-import", "native-run"] + [
            f"native-{c}-{k}" for c in ("tt", "ss") for k in ("summary", "drv", "unconstrained", "notes")]
        sta_steps = ["native-tt-opensta", "native-ss-opensta"]
        rows = {f"ruststa_t{t}": [] for t in threads} | {f"opensta_t{t}": [] for t in threads}
        for rep in range(args.reps + 1):
            for t in threads:
                work = out / f"timing-r{rep}-t{t}"
                shutil.rmtree(work, ignore_errors=True)
                shutil.copytree(tdir, work, ignore=shutil.ignore_patterns("ruststa-*", "logs", "workspace.lock"))
                (work / "logs").mkdir()
                def local(argv):
                    return [a.replace(str(tdir), str(work)) for a in strip_time(argv)]
                total = dict(exit_code=0, wall_seconds=0.0, peak_rss_kib=0, cpu_seconds=0.0)
                for step in rust_steps:
                    argv = local(json.loads((logs / f"{step}-command.json").read_text()))
                    argv = [str(t) if i and argv[i - 1] == "--threads" else a for i, a in enumerate(argv)]
                    r = measure(argv, work, stdout=work / "logs" / f"{step}.stdout")
                    total["exit_code"] |= r["exit_code"]
                    total["wall_seconds"] = round(total["wall_seconds"] + r["wall_seconds"], 3)
                    total["cpu_seconds"] = round(total["cpu_seconds"] + r["cpu_seconds"], 2)
                    total["peak_rss_kib"] = max(total["peak_rss_kib"], r["peak_rss_kib"])
                sta = dict(exit_code=0, wall_seconds=0.0, peak_rss_kib=0, cpu_seconds=0.0)
                for step in sta_steps:
                    argv = local(json.loads((logs / f"{step}-command.json").read_text()))
                    argv = [str(t) if i and argv[i - 1] == "-threads" else a for i, a in enumerate(argv)]
                    r = measure(argv, work, stdout=work / "logs" / f"{step}.stdout")
                    sta["exit_code"] |= r["exit_code"]
                    sta["wall_seconds"] = round(sta["wall_seconds"] + r["wall_seconds"], 3)
                    sta["cpu_seconds"] = round(sta["cpu_seconds"] + r["cpu_seconds"], 2)
                    sta["peak_rss_kib"] = max(sta["peak_rss_kib"], r["peak_rss_kib"])
                if rep:
                    rows[f"ruststa_t{t}"].append(total)
                    rows[f"opensta_t{t}"].append(sta)
                if rep < args.reps or t != threads[-1]:
                    shutil.rmtree(work)
        result["timing"] = dict(contenders={k: summarize(v) for k, v in rows.items()},
                                scope="RustSTA: init+import+run (TT and SS in one run)+8 reports; "
                                      "OpenSTA: one process per corner (read, link, SDC, SPEF, full reports)")

    if "gds" in sections:
        native = strip_time(json.loads((o / "logs/be_gds.command.json").read_text())["argv"])
        prof = o / "backend/profiles"
        script = ROOT / "modules/RustROAD/scripts/signoff/kl_def2gds.py"
        rows = {"rustroad_export_gds": [], "klayout_def2gds": []}
        for rep in range(args.reps + 1):
            work = out / f"gds-r{rep}"
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True)
            argv = [a if not a.endswith("ibex_sram_bench.gds") else str(work / "rustroad.gds") for a in native]
            r1 = measure(argv, work)
            # KLayout selects its reader by file extension; the flow saved the DEF as a .stdout capture.
            (work / "design.def").symlink_to(o / "logs/be_def.stdout")
            r2 = measure(["/opt/eda/wrap/klayout", "-b", "-r", str(script),
                          "-rd", f"def_path={work / 'design.def'}",
                          "-rd", "tlef=/opt/eda/ez130_8t_v1p1/lef/ez130_sg13g2_tech.lef",
                          "-rd", f"clef={args.cell_lef}",
                          "-rd", f"cgds={prof / 'combined-cells.gds'}",
                          "-rd", "map_file=/opt/eda/src/IHP-Open-PDK/ihp-sg13g2/libs.tech/klayout/tech/sg13g2.map",
                          "-rd", f"out={work / 'klayout.gds'}", "-rd", f"report={work / 'klayout.json'}"], work,
                         env={"QT_QPA_PLATFORM": "offscreen"})
            if rep:
                rows["rustroad_export_gds"].append(r1)
                rows["klayout_def2gds"].append(r2)
            if rep < args.reps:
                shutil.rmtree(work)
        last = out / f"gds-r{args.reps}"
        sizes = {k: (last / f).stat().st_size if (last / f).exists() else None
                 for k, f in (("rustroad", "rustroad.gds"), ("klayout", "klayout.gds"))}
        result["gds"] = dict(contenders={k: summarize(v) for k, v in rows.items()}, bytes=sizes)

    (out / "module-bench.json").write_text(json.dumps(result, indent=2) + "\n")
    brief = {s: {k: (v["median_wall_seconds"], v["worst_peak_rss_kib"]) for k, v in result[s]["contenders"].items()}
             for s in ("extraction", "timing", "gds") if s in result}
    print(json.dumps(brief, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

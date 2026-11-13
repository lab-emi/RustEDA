#!/usr/bin/env python3
"""Same-load A/B of the integration run: campaign binaries on the campaign output (base), the integrated binaries on
the integration run's output (cand) and the competitor, in one rotation per repetition, at 2 and 4 threads.

extraction: RustROAD `extract` (each on its own copy of its own routed workspace, so each trusts its own validation
receipt) against OpenRCX; timing: the RustSTA timing section (init, import, run, eight reports) against OpenSTA's TT
and SS processes. Commands are the ones each flow recorded, replayed with module_bench.py's helpers.
usage: ab.py OUT [--reps N] [--sections extraction,timing]
"""
import argparse, hashlib, json, re, shutil, sys
from pathlib import Path

sys.path.insert(0, "/home/user/RustEDA/integration/ibex_host_benchmark")
import module_bench as mb  # noqa: E402

BASE_OUT = Path("/home/user/RustEDA/.work/campaign-20261113/native-final/output")
CAND_OUT = Path("/home/user/RustEDA/.work/kit-runs/int2/native-1/output")
BASE_BIN = Path("/home/user/RustEDA/.work/bins/final-20261113")
CAND_ROAD = Path("/home/user/RustEDA/.work/opt-impl/lane-x1/bin/final/rustroad")
CAND_STA = Path("/home/user/RustEDA/.work/opt-impl/lane-m1/bin/final-m1")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def extraction_argv(out, binary, work, threads):
    argv = mb.strip_time(json.loads((out / "logs/be_extract.command.json").read_text())["argv"])
    argv[0] = str(binary)
    ws = argv[argv.index("--workspace") + 1]
    argv[argv.index("--workspace") + 1] = str(work / "road")
    argv = [a if not a.endswith("native.nom.spef") else str(work / "native.spef") for a in argv]
    return mb.with_threads(argv, threads), Path(ws)


def timing_steps(out, binary, work, threads):
    tdir = out / "backend/timing"
    logs = tdir / "logs"
    steps = ["native-init", "native-import", "native-run"] + [
        f"native-{c}-{k}" for c in ("tt", "ss") for k in ("summary", "drv", "unconstrained", "notes")]
    res = []
    for s in steps:
        argv = [a.replace(str(tdir), str(work)) for a in mb.strip_time(json.loads((logs / f"{s}-command.json").read_text()))]
        argv[0] = str(binary / Path(argv[0]).name)
        argv = [str(threads) if i and argv[i - 1] == "--threads" else a for i, a in enumerate(argv)]
        res.append((s, argv))
    return res


def copy_timing(out, work):
    tdir = out / "backend/timing"
    shutil.copytree(tdir, work, ignore=shutil.ignore_patterns("ruststa-*", "logs", "workspace.lock"))
    (work / "logs").mkdir()
    for script in (work / "reports").glob("native-*-tcl"):
        script.write_text(script.read_text().replace(str(tdir), str(work)))
    return tdir


def run_steps(steps, work):
    total = dict(exit_code=0, wall_seconds=0.0, peak_rss_kib=0, cpu_seconds=0.0)
    for name, argv in steps:
        r = mb.measure(argv, work, stdout=work / "logs" / f"{name}.stdout")
        total["exit_code"] |= r["exit_code"]
        total["wall_seconds"] = round(total["wall_seconds"] + r["wall_seconds"], 3)
        total["cpu_seconds"] = round(total["cpu_seconds"] + r["cpu_seconds"], 2)
        total["peak_rss_kib"] = max(total["peak_rss_kib"], r["peak_rss_kib"])
    return total


def report_hashes(work):
    files = sorted(p for p in (work / "logs").glob("*.stdout") if "opensta" not in p.name)
    return {p.name: sha(p) for p in files}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--threads", default="2,4")
    ap.add_argument("--sections", default="extraction,timing")
    a = ap.parse_args()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    threads = [int(t) for t in a.threads.split(",")]
    sections = a.sections.split(",")
    rows = out / "runs.jsonl"
    # Private copies of each routed workspace, made once (extract reads them; each keeps its own receipt).
    road = {}
    if "extraction" in sections:
        for who, o in (("base", BASE_OUT), ("cand", CAND_OUT)):
            _, ws = extraction_argv(o, "x", out, 4)
            dst = out / f"road-{who}"
            if not dst.exists():
                shutil.copytree(ws, dst)
            road[who] = dst
    tcl = (CAND_OUT / "backend/openrcx/extract.tcl").read_text()
    for rep in range(a.reps + 1):
        for t in threads:
            if "extraction" in sections:
                for who in ("base", "cand", "openrcx"):
                    work = out / (f"x-run-t{t}" if who != "openrcx" else f"x-orcx-t{t}")
                    shutil.rmtree(work, ignore_errors=True)
                    work.mkdir()
                    if who == "openrcx":
                        s = tcl.replace("set_thread_count 2", f"set_thread_count {t}")
                        s = re.sub(r'write_spef "[^"]+"', f'write_spef "{work / "openrcx.spef"}"', s)
                        (work / "extract.tcl").write_text(s)
                        r = mb.measure(["/opt/eda/wrap/openroad", "-exit", str(work / "extract.tcl")], work)
                        h = None
                    else:
                        o, b = (BASE_OUT, BASE_BIN / "rustroad") if who == "base" else (CAND_OUT, CAND_ROAD)
                        argv, _ = extraction_argv(o, b, work, t)
                        (work / "road").symlink_to(road[who])
                        r = mb.measure(argv, work, env={"RAYON_NUM_THREADS": str(t), "OMP_NUM_THREADS": str(t)},
                                       stdout=work / "report.json")
                        h = sha(work / "native.spef")
                    rec = dict(section="extraction", who=who, threads=t, rep=rep, **r, spef_sha256=h)
                    with rows.open("a") as f:
                        f.write(json.dumps(rec) + "\n")
                    shutil.rmtree(work)
            if "timing" in sections:
                for who in ("base", "cand", "opensta"):
                    work = out / (f"t-run-t{t}" if who != "opensta" else f"t-osta-t{t}")
                    shutil.rmtree(work, ignore_errors=True)
                    o = BASE_OUT if who == "base" else CAND_OUT
                    tdir = copy_timing(o, work)
                    if who == "opensta":
                        steps = []
                        for s in ("native-tt-opensta", "native-ss-opensta"):
                            argv = [x.replace(str(tdir), str(work)) for x in
                                    mb.strip_time(json.loads((tdir / "logs" / f"{s}-command.json").read_text()))]
                            argv = [str(t) if i and argv[i - 1] == "-threads" else x for i, x in enumerate(argv)]
                            steps.append((s, argv))
                        r = run_steps(steps, work)
                        h = None
                    else:
                        b = BASE_BIN if who == "base" else CAND_STA
                        r = run_steps(timing_steps(o, b, work, t), work)
                        h = report_hashes(work)
                    rec = dict(section="timing", who=who, threads=t, rep=rep, **r, report_sha256=h)
                    with rows.open("a") as f:
                        f.write(json.dumps(rec) + "\n")
                    shutil.rmtree(work)
    for d in road.values():
        shutil.rmtree(d)
    print("done", rows)


if __name__ == "__main__":
    main()

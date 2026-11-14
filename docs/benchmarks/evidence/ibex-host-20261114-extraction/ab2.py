#!/usr/bin/env python3
"""Same-load A/B of RustROAD `extract` on the 2026-11-13 Ibex layout: the campaign binary, the previous publication
(fe4b286), the integrated second round (8d29a61) and OpenRCX's black-box replay, at 2 and 4 threads.

Each RustROAD binary runs on a private copy of a routed workspace whose validation receipt that same binary wrote in a
kit run (campaign: native-final; fe4b286: int2; 8d29a61: int3), with the campaign's recorded command line and the
campaign's RC profile, and writes its SPEF at one shared work path. The contenders' order shifts by one each
repetition; repetition 0 is a warm-up. usage: ab2.py OUT [--reps N] [--threads 2,4]
"""
import argparse, hashlib, json, re, shutil, sys
from pathlib import Path

sys.path.insert(0, "/home/user/RustEDA/integration/ibex_host_benchmark")
import module_bench as mb  # noqa: E402

R = Path("/home/user/RustEDA/.work")
CAMPAIGN = R / "campaign-20261113/native-final/output"
INT3 = R / "kit-runs/int3/native-1/output"
RC = CAMPAIGN / "backend/profiles/ihp-rc-nom.json"
CONTENDERS = {
    "campaign": (R / "bins/final-20261113/rustroad", CAMPAIGN / "frontend/road-final"),
    "fe4b286": (R / "opt-impl/lane-x1/bin/final/rustroad", R / "kit-runs/int2/keep/frontend/road-final"),
    "8d29a61": (R / "opt-impl/lane-x2/bin/final/rustroad", INT3 / "frontend/road-final"),
}
ORDER = ["campaign", "fe4b286", "8d29a61", "openrcx"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def argv_for(binary, ws, work, threads):
    argv = mb.strip_time(json.loads((CAMPAIGN / "logs/be_extract.command.json").read_text())["argv"])
    argv[0] = str(binary)
    argv[argv.index("--workspace") + 1] = str(ws)
    argv[argv.index("--rc-profile") + 1] = str(RC)
    argv[argv.index("--out") + 1] = str(work / "native.spef")
    return mb.with_threads(argv, threads)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--reps", type=int, default=15)
    ap.add_argument("--threads", default="2,4")
    a = ap.parse_args()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    threads = [int(t) for t in a.threads.split(",")]
    rows = out / "runs.jsonl"
    road = {}
    for who, (_, ws) in CONTENDERS.items():
        dst = out / f"road-{who}"
        if not dst.exists():
            shutil.copytree(ws, dst)
        road[who] = dst
    tcl = (INT3 / "backend/openrcx/extract.tcl").read_text()
    for rep in range(a.reps + 1):
        order = ORDER[rep % 4:] + ORDER[:rep % 4]
        for t in threads:
            for slot, who in enumerate(order):
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
                    binary, _ = CONTENDERS[who]
                    r = mb.measure(argv_for(binary, road[who], work, t), work,
                                   env={"RAYON_NUM_THREADS": str(t), "OMP_NUM_THREADS": str(t)},
                                   stdout=work / "report.json")
                    h = sha(work / "native.spef")
                rec = dict(section="extraction", who=who, threads=t, rep=rep, slot=slot, **r, spef_sha256=h)
                with rows.open("a") as f:
                    f.write(json.dumps(rec) + "\n")
                shutil.rmtree(work)
    for d in road.values():
        shutil.rmtree(d)
    print("done", rows)


if __name__ == "__main__":
    main()

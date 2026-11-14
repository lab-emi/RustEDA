#!/usr/bin/env python3
"""Evidence summary of the second-round extraction follow-up: binaries, the int3 full run's identity with the
campaign and its gates, and the A/B summary of ab2.py's runs. usage: make_summary.py AB_SUMMARY_JSON OUT_JSON"""
import hashlib, json, sys
from pathlib import Path

R = Path("/home/user/RustEDA/.work")
CAMP = R / "campaign-20261113"
W = R / "kit-runs/int3"
OUT = W / "native-1/output"
FILES = ["backend/ibex_sram_bench.gds", "logs/be_def.stdout", "backend/native.nom.spef", "logs/be_netlist.stdout",
         "logs/be_powered.stdout", "frontend/mapped.v"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verdict(gate, entry):
    r = entry.get("report") or {}
    if gate == "latchup":
        return r["controller"]["status"]
    if gate == "logic":
        return {k: r[k]["status"] for k in ("synthesis_equivalence", "final_equivalence")} | {"summary": r["summary"]}
    return r.get("status")


CONSOLE = json.loads([x for x in (W / "native-1.console").read_text().splitlines() if '"steps_completed"' in x][-1])
ab = json.load(open(sys.argv[1]))
# Paired view: per measured rotation, 8d29a61's wall time over each other contender's in the same rotation.
runs = [json.loads(x) for x in (Path(sys.argv[1]).parent / "runs.jsonl").read_text().splitlines()]
paired = {}
for t in sorted({r["threads"] for r in runs}):
    rot = {}
    for r in runs:
        if r["threads"] == t and r["rep"] > 0:
            rot.setdefault(r["rep"], {})[r["who"]] = r["wall_seconds"]
    for who in ("campaign", "fe4b286", "openrcx"):
        q = sorted(v["8d29a61"] / v[who] for v in rot.values())
        paired[f"t{t}/8d29a61_over_{who}"] = dict(rotations=len(q), faster_in=sum(x < 1 for x in q),
                                                   median=round(q[len(q) // 2], 3), min=round(q[0], 3), max=round(q[-1], 3))
ab = {"per_contender": ab, "paired_wall_ratio": paired}
camp_gates = json.load(open(CAMP / "signoff-native/summary.json"))["gates"]
gates = json.load(open(W / "signoff-native/summary.json"))["gates"]
summary = {
    "schema": "rusteda.host-followup.v1",
    "date": "2026-11-14",
    "host": {"cpu": "Intel(R) Xeon(R) Processor @ 2.30GHz", "logical_cpus": 4,
             "note": "the 4-vCPU VM of the follow-up of 2026-11-13"},
    "engines": {"RustROAD": {"campaign": "f90f2dd (binary of the campaign)",
                             "previous": "fe4b28610fa1d2ce728fa5456e0b1fa891d821f7",
                             "candidate": "8d29a61 (same sources as 7f076f5, rebased onto the docs commit db55258)"},
                "RustSTA": {"candidate": "de210a66e5cef12e9e0cb11eec8a2e11ee77f0ba (unchanged since the follow-up)"}},
    "binaries_sha256": {
        "rustroad": sha(R / "opt-impl/lane-x2/bin/final/rustroad"),
        "previous_rustroad": sha(R / "opt-impl/lane-x1/bin/final/rustroad"),
        "campaign_rustroad": sha(R / "bins/final-20261113/rustroad"),
        "ruststa": sha(R / "opt-impl/lane-m1/bin/final-m1/ruststa"),
        "rusteda": sha(R / "opt-impl/lane-m1/bin/final-m1/rusteda"),
        "rusteda_flow": sha(R / "bins/final-20261113/rusteda-flow"),
    },
    "full_run": {
        **{k: CONSOLE[k] for k in ("steps_completed", "steps_total", "wall_seconds")},
        "executor": "rusteda flow exec (campaign binary)",
        "inputs": "the 2026-11-13 campaign prepared inputs, recipe and host copy",
        "outputs_identical_to_campaign": {f: {"campaign": sha(CAMP / "native-final/output" / f), "followup": sha(OUT / f)}
                                          for f in FILES},
        "shared_gates": {g: {"exit_code": v["exit_code"], "status": verdict(g, v),
                             "campaign_status": verdict(g, camp_gates[g])}
                         for g, v in gates.items()},
    },
    "ab": {"protocol": "ab2.py: per repetition and thread count, the campaign binary, fe4b286, 8d29a61 and OpenRCX's "
                       "black-box replay, in an order shifted by one each repetition; repetition 0 is a warm-up and is "
                       "excluded; heavy and build locks held; GNU time",
           "summary": ab},
}
json.dump(summary, open(sys.argv[2], "w"), indent=1)
print(json.dumps(summary["full_run"], indent=1))

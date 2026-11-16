# SPDX-License-Identifier: Apache-2.0
"""Generate a bounded ranked metal repair plan from fresh native SKY130 reports."""
from argparse import ArgumentParser
import json
import os
from pathlib import Path
import re
import sys

from generate import identity, load, run_stage, save

SCRIPT = Path(__file__).resolve().parent
METAL_LAYERS = {"li1", "met1", "met2", "met3", "met4"}
THRESHOLD = 1.0261111111111112
MAX_NETS = 20_000
MAX_CANDIDATES = 128
MAX_PARTITION_BYTES = 128 << 20


def rank(partition, inventory, model):
    covered = set()
    scores = {}
    for entry in inventory["reports"]:
        report_path = Path(entry["report"]).resolve(strict=True)
        assert report_path.is_relative_to(partition)
        assert 0 < report_path.stat().st_size <= MAX_PARTITION_BYTES
        assert identity(report_path)["sha256"] == entry["report_sha256"]
        report = load(report_path)
        assert report["workspace_revision"] == inventory["revision"]
        selection = report["selection"]
        assert selection["source_sha256"] == inventory["report_source_sha256"]
        bound_model, = (source for source in report["sources"] if source["role"] == "antenna_cut_area")
        assert bound_model["sha256"] == model["sha256"] and bound_model["path"] == model["path"]
        names = set(selection["canonical_nets"])
        assert 0 < len(names) <= 2048 and names.isdisjoint(covered)
        covered.update(names)
        assert len(covered) <= MAX_NETS
        for row in report["checks"]:
            name = row["net"]
            assert name in names
            if (row["layer"] not in METAL_LAYERS or row["kind"].startswith("whole_net")
                    or row["limit"] <= 0 or not row["violated"]):
                continue
            ratio = row["ratio"] / row["limit"]
            if ratio >= THRESHOLD:
                scores[name] = max(ratio, scores.get(name, 0.0))
                assert len(scores) <= MAX_CANDIDATES
        del report
    assert len(covered) == inventory["nets"] and scores
    return sorted(scores, key=lambda name: (-scores[name], name))


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument("--workspace", type=Path, required=True)
    cli.add_argument("--producer", type=Path, required=True)
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--native", type=Path, required=True)
    cli.add_argument("--native-sha256", required=True)
    cli.add_argument("--model", type=Path, required=True)
    cli.add_argument("--model-sha256", required=True)
    args = cli.parse_args()
    assert len(os.sched_getaffinity(0)) == 2
    workspace = args.workspace.resolve(strict=True)
    producer_path = args.producer.resolve(strict=True)
    out = args.out.resolve()
    assert not out.is_relative_to(workspace)
    out.mkdir()
    (out / "measure").mkdir()
    rev = (workspace / "HEAD").read_text().strip()
    assert re.fullmatch(r"rev[0-9]+", rev)
    source = load(producer_path)
    assert source.get("final_revision", rev) == rev
    gds = source["artifacts"]["gds"]
    assert identity(gds["path"]) == gds
    design = workspace / "revisions" / rev / "design.json"
    routes = workspace / "revisions" / rev / "routes.rbin"
    tool = identity(args.native)
    model = identity(args.model)
    assert tool["sha256"] == args.native_sha256 and model["sha256"] == args.model_sha256
    source_inputs = dict(workspace=str(workspace), revision=rev, producer=identity(producer_path), gds=gds,
                         route=identity(routes), design=identity(design), tool=tool, model=model,
                         threshold=THRESHOLD, sources={name:identity(SCRIPT/name)
                                                        for name in ("ranked_metal.py", "generate.py", "batch.py")})
    save(out / "inputs.json", source_inputs)
    parts = out / "native-partition"
    batch_stage = run_stage(out, "native-batch",
                            [sys.executable, SCRIPT/"batch.py", workspace, tool["path"], parts, model["path"]], 150)
    complete = parts / "complete.json"
    inventory = load(complete)
    assert (inventory["revision"] == rev and inventory["route_sha256"] == source_inputs["route"]["sha256"]
            and inventory["model_sha256"] == model["sha256"])
    order = rank(parts, inventory, model)
    groups = [sorted(order[i:i+2]) for i in range(0, len(order), 2)]
    plan = {
        "schema": "rustroad.antenna-group-repair.v1",
        "output": str(out / "checked"),
        "max_rounds": 1,
        "deadline_seconds": 440,
        "full_rows": False,
        "policy": {"group_names": 2, "max_groups": len(groups), "max_attempts": len(groups),
                   "max_total_jumpers": 136, "max_commands": 220,
                   "max_report_bytes": 128 << 20, "max_cumulative_work": 20_000_000_000},
        "ranked": {"groups": groups, "cut_area_model": model["path"],
                   "source_sha256": inventory["report_source_sha256"], "max_planner_work": 90_000_000},
    }
    assert identity(routes) == source_inputs["route"] and identity(design) == source_inputs["design"]
    assert (workspace / "HEAD").read_text().strip() == rev
    assert identity(gds["path"]) == gds and identity(producer_path) == source_inputs["producer"]
    assert identity(model["path"]) == model
    save(out / "plan.json", plan)
    result = dict(scope="Fresh native metal selection through typed grouped plan",
                  inputs=identity(out/"inputs.json"), batch=identity(complete), plan=identity(out/"plan.json"),
                  candidates=len(order), groups=len(groups), source_preserved=True,
                  affinity=sorted(os.sched_getaffinity(0)), stage=batch_stage)
    save(out / "result.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()

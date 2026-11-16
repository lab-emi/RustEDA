# SPDX-License-Identifier: Apache-2.0
"""Generate a fresh source-bound horizontal V3 plan without external verdicts."""
from argparse import ArgumentParser
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from time import monotonic

SCRIPT=Path(__file__).resolve().parent

def identity(p):
    p=Path(p).resolve(strict=True)
    with p.open("rb") as f:
        return dict(path=str(p),bytes=p.stat().st_size,sha256=file_digest(f,"sha256").hexdigest())

def save(path,obj):
    with path.open("x") as f:
        json.dump(obj,f,indent=2)
        f.write("\n")

def load(path):
    return json.loads(path.read_bytes())

def run_stage(out,name,argv,limit):
    prefix=out/"measure"
    start=monotonic()
    save(prefix/f"{name}.argv.json",list(map(str,argv)))
    with (prefix/f"{name}.stdout").open("xb") as log,(prefix/f"{name}.stderr").open("xb") as err:
        result=subprocess.run(["/usr/bin/time","-v","-o",prefix/f"{name}.time",*argv],stdout=log,stderr=err,
                              stdin=subprocess.DEVNULL,timeout=limit,env=os.environ|{"PYTHONDONTWRITEBYTECODE":"1"})
    elapsed=monotonic()-start
    r=dict(stage=name,wall_seconds=elapsed,exit_code=result.returncode)
    save(prefix/f"{name}.json",r)
    if result.returncode!=0:
        raise SystemExit(f"{name} failed; see {prefix/name}.stderr")
    return r

def main():
    begin=monotonic()
    cli=ArgumentParser(description=__doc__)
    cli.add_argument("--workspace",type=Path,required=True)
    cli.add_argument("--producer",type=Path,required=True)
    cli.add_argument("--out",type=Path,required=True)
    cli.add_argument("--native",type=Path,required=True)
    cli.add_argument("--native-sha256",required=True)
    cli.add_argument("--model",type=Path,required=True)
    cli.add_argument("--model-sha256",required=True)
    cli.add_argument("--routing-config",type=Path,required=True)
    cli.add_argument("--threshold",type=float,required=True)
    args=cli.parse_args()
    assert 0<args.threshold<=2
    assert len(os.sched_getaffinity(0))==2
    workspace=args.workspace.resolve(strict=True)
    producer=args.producer.resolve(strict=True)
    out=args.out.resolve()
    assert not out.is_relative_to(workspace)
    out.mkdir()
    (out/"measure").mkdir()
    revision=(workspace/"HEAD").read_text().strip()
    assert re.fullmatch(r"rev[0-9]+",revision)
    revdir=workspace/"revisions"/revision
    routes=revdir/"routes.rbin"
    design=revdir/"design.json"
    source=load(producer)
    assert source.get("final_revision",revision)==revision
    artifacts={k:source["artifacts"][k] for k in ("gds","def","netlist")}
    assert all(identity(v["path"])==v for v in artifacts.values())
    executable=identity(args.native)
    model=identity(args.model)
    assert executable["sha256"]==args.native_sha256 and model["sha256"]==args.model_sha256
    sw=("generate.py","batch.py","components.py","scout.py","topology.py","route_geometry.py")
    inputs=dict(workspace=str(workspace),output=str(out),revision=revision,producer=identity(producer),
                source_artifacts=artifacts,source_route=identity(routes),source_design=identity(design),native=executable,
                model=model,routing_config=identity(args.routing_config),threshold=args.threshold,
                source_scripts={name:identity(SCRIPT/name) for name in sw})
    binding=out/"inputs.json"
    save(binding,inputs)
    parts=out/"native-partition"
    stages=[
        run_stage(out,"native-batch",
                  [sys.executable,SCRIPT/"batch.py",workspace,executable["path"],parts,model["path"]],150),
        run_stage(out,"native-components",[sys.executable,SCRIPT/"components.py",binding],60),
        run_stage(out,"native-horizontal-scout",[sys.executable,SCRIPT/"scout.py",binding],60),
    ]
    complete=load(parts/"complete.json")
    assert complete["revision"]==revision and complete["route_sha256"]==inputs["source_route"]["sha256"]
    final=out/"scout/lower-offset-plan.json"
    full_plan=load(final)
    assert full_plan["source_sha256"]==complete["report_source_sha256"]
    assert full_plan["cut_area_model"]==model["path"]
    assert full_plan["move_lower_support_cut"] and len(full_plan["candidates"])<=64
    assert (workspace/"HEAD").read_text().strip()==revision
    assert identity(routes)==inputs["source_route"] and identity(design)==inputs["source_design"]
    assert all(identity(v["path"])==v for v in artifacts.values())
    assert identity(producer)==inputs["producer"] and identity(model["path"])==model
    proof=dict(scope="Fresh bounded component proof through native typed horizontal lower-cut plan generation. No external marker input, physical application or end-to-end timing.",
               input_binding=identity(binding),batch=identity(parts/"complete.json"),
               cohort=identity(out/"components/native-cohort.json"),scout=identity(out/"scout/native-seal.json"),
               plan=identity(final),candidates=len(full_plan["candidates"]),source_survived=True,
               affinity=sorted(os.sched_getaffinity(0)),stages=stages,wall_seconds=monotonic()-begin)
    save(out/"result.json",proof)
    print(json.dumps(proof),flush=True)

if __name__=="__main__":
    main()

# SPDX-License-Identifier: Apache-2.0
"""Capture exactly the same bounded selected reports on one locked snapshot."""
import json
from hashlib import file_digest
import mmap
from pathlib import Path
import subprocess
import sys

workspace, binary, out, model = [Path(x).resolve() for x in sys.argv[1:]]
out.mkdir()
def sha(path):
    with path.open("rb") as f:
        return file_digest(f,"sha256").hexdigest()
def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + chr(10))
head = (workspace/"HEAD").read_text().strip()
routes = workspace/"revisions"/head/"routes.rbin"
before = sha(routes)
profile_sha = sha(model)
args = [str(binary), "--workspace", str(workspace), "--threads", "2"]
with (out/"inventory.json").open("xb") as f:
    subprocess.run([*args,"antenna-inventory"], stdout=f, stdin=subprocess.DEVNULL,
                   timeout=60, check=True)
inv = json.loads((out/"inventory.json").read_bytes())
assert inv["workspace_revision"] == head
names = [x["canonical_name"] for x in inv["nets"]]
assert inv["routed_logical_net_count"] == len(names) == len(set(names))
assert 0 < len(names) <= 20_000
groups = [sorted(names[i:i + 2048]) for i in range(0,len(names),2048)]
plan = {
    "schema":"rustroad.antenna-check-batch.v1",
    "revision":head,
    "groups":groups,
    "max_group_nets":2048,
    "analysis":{
        "max_shapes_per_net":200000,
        "max_total_shapes":2000000,
        "max_pair_checks":200000000,
        "max_union_cells":4000000,
        "max_rows":100000,
        "max_work":250000000,
        "report_memory_bytes":134217728,
    },
    "safety_margin":0.10,
    "whole_net":True,
    "cut_area_model":str(model),
    "max_work":len(groups)*250000000,
    "max_output_bytes":1024**3,
    "snapshot_memory_bytes":2*1024**3,
}
write_json(out/"batch-plan.json",plan)
with (out/"batch.json").open("xb") as stream:
    subprocess.run([*args,"check-antenna-batch","--plan",str(out/"batch-plan.json")],
                   stdout=stream, stdin=subprocess.DEVNULL, timeout=120, check=True)
receipts = []
fingerprint = None
prefix = b'{"schema":"rustroad.antenna-check.v1","status":'
with (out/"batch.json").open("rb") as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as payload:
    assert len(payload) <= plan["max_output_bytes"] and payload[:1]==b"[" and payload[-2:]==b"]\n"
    at=1
    for i, names_in_group in enumerate(groups):
        assert payload[at:at+len(prefix)] == prefix
        next_index=payload.find(prefix, at+len(prefix))
        if next_index<0:
            assert i+1 == len(groups)
            end=len(payload)-2
        else:
            assert payload[next_index-1:next_index] == b","
            end=next_index-1
        assert 0 < end-at <= 128<<20
        obj=payload[at:end]
        entry=json.loads(obj)
        assert entry["workspace_revision"] == head
        assert {x["net"] for x in entry["selection"]["coverage"]} == set(names_in_group)
        policies=[r for r in entry["sources"] if r["sha256"]==profile_sha]
        assert len(policies)==1
        assert {r["sha256"] for r in inv["sources"]} <= {r["sha256"] for r in entry["sources"]}
        current=entry["selection"]["source_sha256"]
        if fingerprint is None:
            fingerprint=current
        assert fingerprint==current
        tag=2048*i
        selection=out/f"selection-{tag:05d}.json"
        report=out/f"report-{tag:05d}.json"
        write_json(selection,names_in_group)
        report.write_bytes(obj)
        receipts.append({"selection":str(selection),"report":str(report),"report_sha256":sha(report)})
        del obj,entry
        at=next_index
    assert at==-1
assert sha(routes)==before and (workspace/"HEAD").read_text().strip()==head
write_json(out/"complete.json",{
    "revision":head,"route_sha256":before,"nets":len(names),"reports":receipts,
    "model_sha256":profile_sha,"report_source_sha256":fingerprint,
})

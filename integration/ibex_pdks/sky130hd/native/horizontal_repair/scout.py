# SPDX-License-Identifier: Apache-2.0
"""Read only on exact grouped26 workspace and its sealed native cohort."""
from collections import Counter, defaultdict
import importlib.util
import json
from pathlib import Path
import re
import sys

import topology as t

SCRIPT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("routes",SCRIPT/"route_geometry.py")
g=importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
inputs=g.read(Path(sys.argv[1]).resolve(strict=True))
HERE=Path(inputs["output"])/"scout"
HERE.mkdir()
WORKSPACE=Path(inputs["workspace"])
REV=WORKSPACE/"revisions"/inputs["revision"]
COHORT=Path(inputs["output"])/"components/native-cohort.json"

def dump(path,data):
    with path.open("x") as f:
        json.dump(data,f,indent=2)
        f.write("\n")

def rect(p):
    return tuple(p[k] for k in ("lx","ly","ux","uy")) if isinstance(p,dict) else tuple(p)

def buckets(r):
    s=8160
    for i in range(r[0]//s,r[2]//s+1):
        for j in range(r[1]//s,r[3]//s+1):
            yield i,j

status=g.read(g.bound(inputs["producer"]))
assert status.get("final_revision",inputs["revision"])==inputs["revision"]
assert (WORKSPACE/"HEAD").read_text().strip()==inputs["revision"]
art={k:status["artifacts"][k] for k in ("def","gds")}
art["netlist_final"]=status["artifacts"]["netlist"]
assert art["gds"]==inputs["source_artifacts"]["gds"]
layout=HERE/"layout.json"
dump(layout,{"artifacts":art})
cap=g.RouteCapture(REV,layout)
cohort=g.read(COHORT)
assert cohort["revision"]==inputs["revision"] and cohort["routes"]==cap.route
assert 0 <= cohort["statistics"]["weighted_upper_contacts"] <= 64
ref=Path(inputs["output"])/"native-partition/complete.json"
manifest=g.read(ref)
assert manifest["route_sha256"]==cap.route["sha256"] and manifest["revision"]==inputs["revision"]
profile=g.bound(cohort["profile"])
assert cohort["profile"]["sha256"]==manifest["model_sha256"]
first=Path(manifest["reports"][0]["report"])
assert g.identity(first)["sha256"]==manifest["reports"][0]["report_sha256"]
native=g.read(first)
model_path,=[x["path"] for x in native["sources"] if x["sha256"]==manifest["model_sha256"]]
provider=g.identity(Path(model_path))
assert provider==inputs["model"] and provider["sha256"]==manifest["model_sha256"]
assert native["selection"]["source_sha256"]==manifest["report_source_sha256"]
del native
overlap_limit=g.read(profile)["overlap_dbu"]
assert overlap_limit==750
route_cfg=g.read(g.bound(cohort["source_config"]))
spacing={r["name"]:r["spacing"] for r in route_cfg["layers"] if r["name"] in t.LAYERS}
spacing["via3"]=next(r["cut_spacing"] for r in route_cfg["vias"] if r["name"]=="M3M4_PR")
assert spacing=={"met3":300,"met4":300,"via3":200}
assert all(cap.widths[l]==300 for l in ("met3","met4"))
tracks={}
with g.bound(art["def"]).open() as f:
    for line in f:
        if line.startswith("COMPONENTS "):
            break
        match=re.fullmatch(r"TRACKS ([XY]) (\d+) DO (\d+) STEP (\d+) LAYER (met[34]) ;\s*",line)
        if match:
            tracks[match[5],match[1]]=int(match[2]),int(match[4])
assert tracks["met3","Y"]==(340,680) and tracks["met4","X"]==(460,920)
meta=g.read(REV/"design.json")
die=rect(meta["design"]["die_area"])
phys=meta["physical"]
supply=phys["state"]["supply"]
ports=phys["state"]["io_pins"]
vias={r["name"]:r["geometries"] for r in phys["technology"]["vias"]}
masters={r["name"]:r for r in phys["connectivity"]["masters"]}
cells,_=g.def_sites(Path(art["def"]["path"]))
macros=[]
for n in ("u_dmem","u_imem"):
    m,x,y,o=cells[n]
    assert o in ("N","FS")
    w,h=masters[m]["size"]
    macros.append((x,y,x+w,y+h))
stdcell_obs=sum(1 for n,m in masters.items() for r in m["obstructions"] if r["layer"] in t.LAYERS and "sram" not in n.lower())
assert stdcell_obs==0
del meta,phys,masters,cells

stats=Counter()
offers=[]
net_ids={name:i for i,name in enumerate(cap.names)}
for compid,c in enumerate(cohort["components"]):
    assert c["binding"]=="exact_route_component_reconstructed" and not c["diffusions"]
    for w in c["weighted"]:
        stats["weighted_stacks"]+=1
        for item in t.options(cap,c["net"],w,tracks,overlap_limit,stats):
            if any(r[0]<die[0] or r[1]<die[1] or r[2]>die[2] or r[3]>die[3] for _,r in item["additions_dbu"]):
                stats["outside_die"]+=1
                continue
            if any(t.overlap(t.bloated(r,400),b) for _,r in item["additions_dbu"] for b in macros):
                stats["macro_box_rejection"]+=1
                continue
            reduction=(w["weight"]-1)*c["normalized_per_weighted_via4_equivalent"]
            item.update(component_id=compid,net_id=net_ids[c["net"]],receiver_pins=c["gates"],cut_weight=w["weight"],
                        upper_normalized_reduction=reduction,projected_after=c["before_model_norm"]-reduction,
                        upper_has_static_variant=bool(w["simple_clear_signal_options"]))
            offers.append(item)
assert len(offers)<=4*64
eligible_buckets={(l,i,j) for option in offers for l,r in option["additions_dbu"]
                  for i,j in buckets(t.bloated(r,400 if l!="via3" else 200))}
assert len(eligible_buckets)<=10000
boxes=defaultdict(set)
refs=0
def index(kind,owner,l,r):
    global refs
    for i,j in buckets(r):
        t.charge()
        key=l,i,j
        if key in eligible_buckets:
            boxes[key].add((kind,owner,r))
            refs+=1
            assert refs<=250000
for n,rows in cap.raw.items():
    for row in rows:
        for l,r in t.views(cap,row):
            stats["signal_geometry_screened"]+=1
            assert stats["signal_geometry_screened"]<=800000
            index("signal",n,l,r)
for r in supply["wires"]:
    if r["layer"] in t.LAYERS:
        assert r["width"]%2==0
        index("power_wire",r["polarity"],r["layer"],t.wire(r["start"],r["end"],r["width"]))
        stats["power_wires_in_scope"]+=1
for v in supply["vias"]:
    for pad in vias[v["name"]]:
        if pad["layer"] in t.LAYERS:
            assert pad["kind"]=="rect"
            index("power_via",v["polarity"],pad["layer"],t.rect_at(rect(pad["rect"]),v["at"]))
            stats["power_via_rectangles_in_scope"]+=1
for p in supply["ports"]:
    if p["layer"] in t.LAYERS:
        index("power_port",p["polarity"],p["layer"],rect(p["rect"]))
        stats["power_ports_in_scope"]+=1
for pin in ports:
    if pin["layer"] in t.LAYERS:
        index("io_port",pin["name"],pin["layer"],rect(pin["rect"]))
        stats["io_ports_in_scope"]+=1

def spacer(l,r):
    if l=="via3":
        return spacing[l]
    return 400 if min(r[2]-r[0],r[3]-r[1])>=3000 else spacing[l]

for row in offers:
    hits=set()
    net=row["typed"]["net"]
    for layer,shape in row["additions_dbu"]:
        for i,j in buckets(t.bloated(shape,400 if layer!="via3" else 200)):
            for purpose,name,foreign in boxes.get((layer,i,j),()):
                if purpose=="signal" and name==net:
                    continue
                rule=spacer(layer,foreign)
                if t.overlap(t.bloated(shape,rule),foreign):
                    hits.add((purpose,name,layer,foreign,rule,t.overlap(shape,foreign)))
    row["blocker_count"]=len(hits)
    row["blocker_kinds"]=sorted({x[0] for x in hits})
    row["blocker_samples"]=[dict(source=k,owner=n,layer=l,box_dbu=b,spacing_dbu=s,intersects=i)
                            for k,n,l,b,s,i in sorted(hits)[:16]]
    for kind in row["blocker_kinds"]:
        stats[kind+"_blocked_candidates"]+=1

def rank(o):
    p=o["typed"]
    return (o["upper_has_static_variant"],-o["upper_normalized_reduction"],
            o["projected_after"],o["added_centerline_dbu"],abs(o["met4_x_pitch_steps"]),p["net"],p["old_at"],p["new_at"])

ranked=sorted(offers,key=rank)
clear=[r for r in ranked if r["blocker_count"]==0]
sites={}
for r in clear:
    p=r["typed"]
    sites.setdefault((p["net"],tuple(p["old_at"])),r)
primary=list(sites.values())
assert len(primary)<=64
plan=HERE/"lower-offset-plan.json"
dump(plan,dict(schema="rustroad.antenna-via-offsets.v1",source_sha256=manifest["report_source_sha256"],
               cut_area_model=model_path,move_lower_support_cut=True,candidates=[r["typed"] for r in primary]))
counts=dict(static_offers=len(ranked),static_clear_offers=len(clear),unique_clear_sites=len(primary),
            unique_clear_nets=len({r["typed"]["net"] for r in primary}),
            clear_sites_without_old_upper_static_route=sum(not r["upper_has_static_variant"] for r in primary))
sources=dict(parent=cap.binding(),native_cohort=g.identity(COHORT),native_partition=g.identity(ref),
             process=provider,cohort_process_copy=g.identity(profile),scout=g.identity(Path(__file__)),topology=g.identity(Path(t.__file__)),
             typed_plan=g.identity(plan))
dump(HERE/"native-seal.json",dict(scope="Native-only horizontal lower support scout with one or two M4 x-pitches with exact old retained endpoints, same-net geometry and frozen signal/supply halo checks. Static proposals, full validator and electrical requalification pending.",
    sources=sources,limits=dict(comparison_budget=t.MAX_WORK,max_variants=256,max_primary_sites=64,
                      max_index_refs=250000,max_signal_rectangles=800000,max_net_route_records=2048),
    tested_work=t.work,index_refs=refs,statistics=dict(stats),counts=counts,spacing_dbu=spacing,supply_full_inventory={k:len(supply[k]) for k in ("wires","vias","ports")},
    macro_conservative_exclusion_dbu=macros,track_steps=[-2,-1,1,2],axis='x',pitch_dbu=920,primary=primary,all_offers=ranked))
print(json.dumps(dict(counts=counts,stats=stats,work=t.work)))

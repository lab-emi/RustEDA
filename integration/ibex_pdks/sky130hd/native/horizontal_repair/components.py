# SPDX-License-Identifier: Apache-2.0
"""Read-only exact via4 contact projection on grouped69, without marker selection."""
from collections import Counter, defaultdict
from hashlib import file_digest
import importlib.util
import json
from pathlib import Path
import re
import sys

SCRIPT=Path(__file__).resolve().parent
HELPER=SCRIPT/"route_geometry.py"
spec=importlib.util.spec_from_file_location("route_geom",HELPER)
g=importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
read,idn,bind=g.read,g.identity,g.bound
inputs=read(Path(sys.argv[1]).resolve(strict=True))
HERE=Path(inputs["output"])/"components"
HERE.mkdir()
WORKSPACE=Path(inputs["workspace"])
source_path=Path(inputs["producer"]["path"])
policy=Path(inputs["model"]["path"])
part=Path(inputs["output"])/"native-partition/complete.json"
REVISION=inputs["revision"]
PHASES=("li1","mcon","met1","via","met2","via2","met3","via3","met4","via4")
ABOVE=("li1","met1","met2","met3","met4")
BELOW=("met1","met2","met3","met4",None)
ADJ=tuple(zip(PHASES[1::2],ABOVE,BELOW,strict=True))
LOCAL=2720
MAX_BRIDGE_LENGTH=5600
MAX_SELECTED_NETS=128
MAX_SHAPES=4096
MAX_CHECKS=50_000_000
BIN=8160
checks=0


def charge(count):
    global checks
    checks+=count
    assert checks<MAX_CHECKS


def overlap(a,b):
    return max(a[0],b[0])<min(a[2],b[2]) and max(a[1],b[1])<min(a[3],b[3])


def touch(a,b):
    return max(a[0],b[0])<=min(a[2],b[2]) and max(a[1],b[1])<=min(a[3],b[3])


def ext(a,pad):
    return (a[0]-pad,a[1]-pad,a[2]+pad,a[3]+pad)


def point(rect):
    return ((rect[0]+rect[2])//2,(rect[1]+rect[3])//2)


def shift(rect,x,y):
    return (rect[0]+x,rect[1]+y,rect[2]+x,rect[3]+y)


def wire(a,b,width):
    assert a[0]==b[0] or a[1]==b[1]
    return ext((min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])),width//2)


def dist(a,b):
    return abs(a[0]-b[0])+abs(a[1]-b[1])


def boxes(raw,layers):
    typ,idx,*arg=raw
    if typ==2:
        x,y=arg
        for l,r in cap.via_shapes[idx]:
            if l in layers:
                yield l,shift(r,x,y),raw
    else:
        l=cap.layers[idx]
        if l not in layers:
            return
        if typ==1:
            r=wire(tuple(arg[:2]),tuple(arg[2:]),cap.widths[l])
        else:
            assert typ==4
            r=tuple(arg)
        yield l,r,raw


PORT_POS=re.compile(r"\+ (?:PLACED|FIXED) \( (-?\d+) (-?\d+) \) (\w+)")
PORT_BOX=re.compile(r"\+ LAYER (\w+) \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)")


def term_rect(identity,site):
    if identity[0]=="port":
        pos=PORT_POS.search(site[0])
        assert pos and pos[3]=="N",(identity,site)
        x,y=map(int,pos.groups()[:2])
        boxes_found=PORT_BOX.findall(site[0])
        assert boxes_found,(identity,site)
        for layer,a,b,c,d in boxes_found:
            if layer in PHASES:
                yield layer,shift(tuple(map(int,(a,b,c,d))),x,y)
        return
    master,x,y,orientation=site
    assert orientation in ("N","FS"),(identity,site)
    r,h=pins[master,identity[2]]
    for shape in r:
        assert shape["kind"]=="rect",(master,shape)
        layer=shape["layer"]
        if layer not in PHASES:
            continue
        a,b,c,d=shape["rect"]
        if orientation=="FS":
            b,d=h-d,h-b
        yield layer,shift((a,b,c,d),x,y)


class Union:
    def __init__(self,n):
        self.parents=list(range(n))
    def find(self,i):
        while self.parents[i]!=i:
            self.parents[i]=self.parents[self.parents[i]]
            i=self.parents[i]
        return i
    def join(self,a,b):
        x,y=self.find(a),self.find(b)
        if x!=y:
            self.parents[max(x,y)]=min(x,y)


def phase(net):
    shape_db=[]
    groups=defaultdict(list)
    refs={}
    def add(layer,r,kind):
        i=len(shape_db)
        shape_db.append((layer,r,kind))
        groups[layer].append(i)
        return i
    for raw in cap.raw[net]:
        for layer,r,kind in boxes(raw,PHASES):
            add(layer,r,kind)
    for identity,site in cap.terminals[net]:
        label=("/".join(identity[1:]) if identity[0]=="instance" else "PIN/"+identity[1])
        ids=[add(l,r,None) for l,r in term_rect(identity,site)]
        if ids:
            refs[label]=ids
    assert len(shape_db)<=MAX_SHAPES,(net,len(shape_db))
    u=Union(len(shape_db))
    for ids in refs.values():
        for i in ids[1:]:
            u.join(ids[0],i)
    for layer,ids in groups.items():
        ids.sort(key=lambda i:shape_db[i][1])
        for a,i in enumerate(ids):
            rect=shape_db[i][1]
            for j in ids[a+1:]:
                charge(1)
                rr=shape_db[j][1]
                if rr[0]>rect[2]:
                    break
                if touch(rect,rr):
                    u.join(i,j)
    for cut,lower,upper in ADJ:
        for i in groups[cut]:
            r=shape_db[i][1]
            for layer in (lower,upper):
                if layer is None:
                    continue
                for j in groups[layer]:
                    charge(1)
                    s=shape_db[j][1]
                    if s[0]>=r[2]:
                        break
                    if overlap(r,s):
                        u.join(i,j)
    return shape_db,groups,refs,u


def scale(p,q):
    dx,dy=abs(p[0]-q[0]),abs(p[1]-q[1])
    if max(dx,dy)>=OVERLAP:
        return 1
    return 4-int(dx>=CONTAIN)-int(dy>=CONTAIN)


def track_candidates(pos,vias,net):
    x,y=pos
    matches={}
    m4pieces=[]
    m5runs=[]
    for raw in cap.raw[net]:
        for layer,r,_ in boxes(raw,{"met4"}):
            if raw!=vias:
                m4pieces.append(r)
        t,idx,*arg=raw
        if t==1 and cap.layers[idx]=="met5":
            a,b,c,d=arg
            if b==d==y and a<=x<=c:
                m5runs.append((a,c))
    old_m4=next(r for l,r,_ in boxes(vias,{"met4"}))
    prior_contacts=[r for r in m4pieces if touch(r,old_m4)]
    if not prior_contacts:
        return [],len(m5runs)
    for a,c in m5runs:
        lo=max(a,x-LOCAL)
        hi=min(c,x+LOCAL)
        start=lo+(TRACK_ORIGIN-lo)%TRACK_PITCH
        for nx in range(start,hi+1,TRACK_PITCH):
            p=(nx,y)
            if p==pos:
                continue
            dx=abs(nx-x)
            if dx>LOCAL:
                continue
            past=[wire(pos,p,WIDTH4)]
            alternatives=[("direct",past)]
            for sy in (-1,1):
                other=y+sy*TRACK_PITCH
                alternatives.append(("one_dogleg",[wire(pos,(x,other),WIDTH4),
                     wire((x,other),(nx,other),WIDTH4),wire((nx,other),p,WIDTH4)]))
            for mode,route_boxes in alternatives:
                length=dx if mode=="direct" else dx+2*TRACK_PITCH
                if length>MAX_BRIDGE_LENGTH:
                    continue
                pads=[(layer,shift(rect,nx,y)) for layer,rect in cap.via_shapes[vias[1]] if layer in ("met4","met5","via4")]
                added=[("met4",r) for r in route_boxes]+pads
                if not all(any(l=="met4" and overlap(rr,prior) for l,rr in added) for prior in prior_contacts):
                    continue
                matches[(p,mode,tuple(added))]=length
    return [(p,mode,added,length) for (p,mode,added),length in sorted(matches.items())],len(m5runs)


def bins(r):
    return [(i,j) for i in range(r[0]//BIN,r[2]//BIN+1) for j in range(r[1]//BIN,r[3]//BIN+1)]


source=read(source_path)
assert idn(source_path)==inputs["producer"]
rev=REVISION
assert source.get("final_revision",rev)==rev and (WORKSPACE/"HEAD").read_text().strip()==rev
for key in ("gds","def","netlist"):
    bind(source["artifacts"][key])
manifest=read(part)
assert manifest["revision"]==rev and 0 < manifest["nets"] <= 20000
wsp=WORKSPACE/"revisions"/rev
assert idn(wsp/"routes.rbin")["sha256"]==manifest["route_sha256"]==inputs["source_route"]["sha256"]
assert idn(policy)==inputs["model"] and idn(policy)["sha256"]==manifest["model_sha256"]
geometry_policy=read(policy)
OVERLAP,CONTAIN=geometry_policy["overlap_dbu"],geometry_policy["containment_dbu"]
assert (OVERLAP,CONTAIN)==(750,430)
CUTOFF=inputs["threshold"]
assert 0 < CUTOFF <= 2.0
refs={}
selected=defaultdict(list)
natives=set()
for entry in manifest["reports"]:
    path=Path(entry["report"])
    rec=idn(path)
    assert rec["sha256"]==entry["report_sha256"]
    refs[str(path)]=rec
    report=read(path)
    assert report["workspace_revision"]==rev
    assert report["selection"]["source_sha256"]==manifest["report_source_sha256"]
    provider,=[entry for entry in report["sources"] if entry["role"]=="antenna_cut_area"]
    assert provider["path"]==str(policy) and provider["sha256"]==manifest["model_sha256"]
    cov={r["net"] for r in report["selection"]["coverage"]}
    assert cov.isdisjoint(natives)
    natives.update(cov)
    for row in report["checks"]:
        if row["layer"]=="via4" and row["kind"]=="area" and row["limit"]>0 and row["ratio"]/row["limit"]>=CUTOFF:
            assert row["net"] in cov and row["gates"]
            selected[row["net"]].append(row)
    del report
assert len(natives)==manifest["nets"] and len(selected)<=MAX_SELECTED_NETS
art=dict(gds=source["artifacts"]["gds"],def_=source["artifacts"]["def"],netlist_final=source["artifacts"]["netlist"])
art["def"]=art.pop("def_")
view=HERE/"layout-view.json"
with view.open("x") as f:
    json.dump(dict(artifacts=art),f)
cap=g.RouteCapture(wsp,view)
metadata=read(wsp/"design.json")
masters=metadata["physical"]["connectivity"]["masters"]
pins={(m["name"],d["name"]):([rect for port in d["ports"] for rect in port],m["size"][1])
      for m in masters for d in m["pins"]}
WIDTH4=cap.widths["met4"]
expect_units=metadata["physical"]["technology"]["dbu_per_micron"]
assert expect_units==1000 and WIDTH4==300
masters_by_name={m["name"]:m for m in masters}
fixed_macro_boxes=[]
for name,(master,x,y,orientation) in sorted(g.def_sites(Path(art["def"]["path"]))[0].items()):
    m=masters_by_name[master]
    if "BLOCK" not in (m.get("class") or ""):
        continue
    w,h=m["size"]
    fixed_macro_boxes.append((x,y,x+w,y+h))
DIE=tuple(metadata["design"]["die_area"][k] for k in ("lx","ly","ux","uy"))
del metadata,masters
tracks=[]
with Path(art["def"]["path"]).open() as f:
    for line in f:
        if line.startswith("COMPONENTS "):
            break
        if m:=re.fullmatch(r"TRACKS X (\d+) DO (\d+) STEP (\d+) LAYER met4 ;\s*",line):
            tracks.append((int(m[1]),int(m[3])))
assert tracks==[(460,920)],tracks
TRACK_ORIGIN,TRACK_PITCH=tracks[0]
config_path=bind(inputs["routing_config"])
config=read(config_path)
SPACING={r["name"]:r["spacing"] for r in config["layers"] if r["name"] in ("met4","met5")}
SPACING["via4"]=next(r["cut_spacing"] for r in config["vias"] if r["name"]=="M4M5_PR")
assert SPACING=={"met4":300,"met5":1600,"via4":800}

components=[]
all_probes={}
regions=set()
for n,native_rows in sorted(selected.items()):
    shapes,groups,refs_by_pin,partition=phase(n)
    for row in native_rows:
        gate_ids=[refs_by_pin[pin][0] for pin in row["gates"] if pin in refs_by_pin]
        if len(gate_ids)!=len(row["gates"]) or len({partition.find(i) for i in gate_ids})!=1:
            components.append(dict(net=n,gates=row["gates"],binding="unresolved_native_terminal_root"))
            continue
        root=partition.find(gate_ids[0])
        low={shapes[i][1] for i in groups["via3"] if partition.find(i)==root}
        high={shapes[i][1]:shapes[i][2] for i in groups["via4"] if partition.find(i)==root}
        low_xy=[point(l) for l in sorted(low)]
        # Road score uses maximum local weight in exactly this process root.
        weights={}
        for rect in sorted(high):
            assert rect[2]-rect[0]==rect[3]-rect[1]==800
            coords=point(rect)
            neighbors=[q for q in low_xy if scale(coords,q)>1]
            assert len(neighbors)<=1
            weights[rect]=(scale(coords,neighbors[0]),neighbors[0]) if neighbors else (1,None)
        logical_area=len(high)*.64
        weighted_area=sum(v[0] for v in weights.values())*.64
        if not(abs(row["metal_um2"]-logical_area)<1e-8 and
               abs(row.get("effective_metal_um2",row["metal_um2"])-weighted_area)<1e-8):
            components.append(dict(net=n,gates=row["gates"],binding="raw_or_weighted_area_mismatch",
                                   raw_area=logical_area,native_area=row["metal_um2"],
                                   modeled_area=weighted_area,native_effective=row.get("effective_metal_um2")))
            continue
        clean=dict(net=n,gates=row["gates"],binding="exact_route_component_reconstructed",
                   diffusions=row["diffusions"],raw_via4=len(high),before_model_norm=row["ratio"]/row["limit"],
                   no_bonus_model_norm=(logical_area/row["denominator_um2"])/row["limit"],
                   normalized_per_weighted_via4_equivalent=(.64/row["denominator_um2"])/row["limit"],
                   weighted=[])
        for rect,(weight,lower) in sorted(weights.items()):
            if weight==1:
                continue
            upper=point(rect)
            via=high[rect]
            assert via and via[0]==2 and tuple(via[2:])==upper
            key=(n,*upper)
            if key not in all_probes:
                all_lower={tuple(raw[2:]) for raw in cap.raw[n] if raw[0]==2 and
                           any(l=="via3" for l,_ in cap.via_shapes[raw[1]])}
                sites,m5=track_candidates(upper,via,n)
                probes=[]
                for p,mode,added,length in sites:
                    if any(scale(p,q)>1 for q in all_lower):
                        continue
                    if any(any(overlap(bb,macro) for macro in fixed_macro_boxes) or
                           not(DIE[0]<=bb[0]<bb[2]<=DIE[2] and DIE[1]<=bb[1]<bb[3]<=DIE[3])
                           for _,bb in added):
                        continue
                    for layer,bb in added:
                        regions.update((layer,*ij) for ij in bins(ext(bb,SPACING[layer])))
                    probes.append((p,mode,added,length))
                all_probes[key]=(via,m5,probes)
            clean["weighted"].append(dict(upper=upper,neighbor_lower=lower,weight=weight,aligned=upper==lower))
        components.append(clean)
assert len(regions)<=20_000
assert sum(len(v[2]) for v in all_probes.values())<=4000

nearby=defaultdict(set)
for n,raw_records in cap.raw.items():
    for raw in raw_records:
        for layer,r,rec in boxes(raw,SPACING):
            for i,j in bins(r):
                charge(1)
                if (layer,i,j) in regions:
                    nearby[(layer,i,j)].add((n,r,rec))
assert sum(len(v) for v in nearby.values())<=500_000

for c in components:
    for upper in c.get("weighted",[]):
        n=c["net"]
        orig,m5,prospects=all_probes[n,*upper["upper"]]
        options=[]
        for p,mode,added,length in prospects:
            cleared=True
            for layer,r in added:
                halo=ext(r,SPACING[layer])
                for i,j in bins(halo):
                    hits=nearby.get((layer,i,j),())
                    charge(len(hits))
                    for owner,br,raw in hits:
                        # Same-net conductors may overlap; a distinct via4 cut
                        # still needs its cut-to-cut spacing on the same net.
                        if raw==orig and owner==n:
                            continue
                        if (owner!=n or layer=="via4") and overlap(halo,br):
                            cleared=False
                            break
                    if not cleared:
                        break
                if not cleared:
                    break
            if cleared:
                options.append(dict(xy=p,kind=mode,m4_added_centerline_dbu=length))
        upper["met5_supporting_centerline_segments"]=m5
        upper["topology_grid_options"]=len(prospects)
        upper["simple_clear_signal_options"]=options
    if c["binding"]=="exact_route_component_reconstructed":
        changed=[r for r in c["weighted"] if r["simple_clear_signal_options"]]
        c["has_at_least_one_signal_clear_move"]=bool(changed)
        c["optimistic_norm_if_signal_clear_weights_removed"]=c["before_model_norm"]-sum(
            r["weight"]-1 for r in changed)*c["normalized_per_weighted_via4_equivalent"]

summary=Counter()
for c in components:
    summary[c["binding"]]+=1
    if c["binding"]!="exact_route_component_reconstructed":
        continue
    if c["weighted"]:
        summary["components_with_aligned_or_overlap"]+=1
    if any(r["aligned"] for r in c["weighted"]):
        summary["components_with_aligned"]+=1
    if any(not r["aligned"] for r in c["weighted"]):
        summary["components_with_noncoincident_overlap"]+=1
    summary["weighted_upper_contacts"]+=len(c["weighted"])
    summary["weighted_contacts_with_topology_grid_options"]+=sum(w["topology_grid_options"]>0 for w in c["weighted"])
    summary["weighted_contacts_with_signal_clear_options"]+=sum(bool(w["simple_clear_signal_options"]) for w in c["weighted"])
    summary["components_with_signal_clear_option"]+=c["has_at_least_one_signal_clear_move"]
    summary["component_still_high_unweighted"]=summary["component_still_high_unweighted"]+int(c["no_bonus_model_norm"]>=CUTOFF)
    summary["component_can_drop_below_cutoff_in_model_if_all_weights_removed"]+=c["no_bonus_model_norm"]<CUTOFF
    summary["component_can_drop_below_cutoff_under_signal_screen"]+=c["optimistic_norm_if_signal_clear_weights_removed"]<CUTOFF
out=dict(scope="Read-only native SKY130 high-via4 component reconstruction. Model offsets preserve existing M5 trunk and local M4 terminals by bounded centerline screens. Only actual signal route rectangles, original V4 cut spacing, die and two macro boxes used in geometric rejection.",
         artifacts=art,source=idn(source_path),native_reports=refs,profile=idn(policy),
         revision=rev,routes=cap.route,source_config=idn(config_path),native_batch_manifest=idn(part),
         thresholds=dict(native_cutoff=CUTOFF,weighted_overlap_dbu=OVERLAP,via4_shift_limit_dbu=LOCAL,
                         layer_base_spacing=SPACING,max_bridge_length_dbu=MAX_BRIDGE_LENGTH),
         limits=dict(native_high_nets=len(selected),components=len(components),work_pairs=checks),
         statistics=summary,components=components,
         limitations=["Simple clearance is not native legality. Wide spacing, minimum shape maintenance, PDN other than macro boxes, EOL, multiple offsets simultaneously, timing and final DRC remain unchecked.",
                      "Optimistic per-component ratio subtracts only predicted weight. Does not assert fabricated path changed or unchanged."])
with (HERE/"native-cohort.json").open("x") as f:
    json.dump(out,f,indent=2)
    f.write("\n")
print(json.dumps(dict(summary=summary,nets=len(selected),components=len(components))))

# SPDX-License-Identifier: Apache-2.0
"""Retained centerline and pad tests for a bounded horizontal lower-cut shift."""
from collections import defaultdict

LAYERS={"met3","met4","via3"}
MASTER_NAMES={"M3M4_PR","M3M4_PR_R"}
MAX_WORK=45_000_000
work=0

def charge(n=1):
    global work
    work+=n
    assert work<=MAX_WORK

def rect_at(r,p):
    return (r[0]+p[0],r[1]+p[1],r[2]+p[0],r[3]+p[1])

def bloated(r,w):
    return (r[0]-w,r[1]-w,r[2]+w,r[3]+w)

def wire(a,b,w):
    return bloated((min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])),w//2)

def overlap(a,b,edge=False):
    charge()
    x=min(a[2],b[2])-max(a[0],b[0])
    y=min(a[3],b[3])-max(a[1],b[1])
    return x>=0 and y>=0 and (x>0 or y>0) if edge else x>0 and y>0

def on_segment(p,r):
    return all(min(a,b)<=v<=max(a,b) for v,a,b in zip(p,r[2:4],r[4:6]))

def separation(a,b):
    charge()
    return max(abs(a[0]-b[0]),abs(a[1]-b[1]))

def views(cap,r):
    t,i,*v=r
    if t==2:
        for l,geo in cap.via_shapes[i]:
            if l in LAYERS:
                yield l,rect_at(geo,v)
    else:
        l=cap.layers[i]
        if l in LAYERS:
            yield l,(wire(v[:2],v[2:],cap.widths[l]) if t==1 else tuple(v))

def options(cap,net,contact,tracks,overlap_limit,stats):
    old=tuple(contact["neighbor_lower"])
    upper=tuple(contact["upper"])
    records=cap.raw[net]
    if len(records)>2048:
        stats["bounded_net_route_records_skip"]+=1
        return []
    cuts=[r for r in records if r[0]==2 and cap.vias[r[1]] in MASTER_NAMES and tuple(r[2:])==old]
    if len(cuts)!=1:
        stats["old_v3_not_unique_supported_master"]+=1
        return []
    old_cut=cuts[0]
    uppers=[tuple(r[2:]) for r in records if r[0]==2 and any(l=="via4" for l,_ in cap.via_shapes[r[1]])]
    if sorted(v for v in uppers if separation(v,old)<overlap_limit)!=[upper]:
        stats["ambiguous_old_v3_support"]+=1
        return []
    if any((x,y)==old and l in LAYERS for _,l,x,y in cap.contacts[net]):
        stats["old_site_signal_access"]+=1
        return []
    x0,x_step=tracks["met4","X"]
    y0,y_step=tracks["met3","Y"]
    if (old[0]-x0)%x_step or (old[1]-y0)%y_step:
        stats["old_v3_off_track_intersection"]+=1
        return []
    old_mask=dict(views(cap,old_cut))
    assert set(old_mask)==LAYERS
    fixed=defaultdict(list)
    anchors=defaultdict(list)
    for r in records:
        if r==old_cut:
            continue
        for layer,rect in views(cap,r):
            fixed[layer].append((r,rect))
            if layer=="via3":
                continue
            if (r[0]==1 and on_segment(old,r)) or (r[0]==2 and tuple(r[2:])==old):
                anchors[layer].append((r,rect))
    if not all(anchors[l] for l in ("met3","met4")):
        stats["no_proved_retained_center_contact"]+=1
        return []
    result=[]
    for offset in (-2,-1,1,2):
        stats["displacements_considered"]+=1
        new=(old[0]+offset*x_step,old[1])
        if any(separation(v,new)<overlap_limit for v in uppers):
            stats["new_v3_in_v4_overlap_window"]+=1
            continue
        new_mask=dict(views(cap,(2,old_cut[1],*new)))
        if any(overlap(bloated(new_mask["via3"],200),rect) for _,rect in fixed["via3"]):
            stats["same_net_via3_cut_spacing"]+=1
            continue
        added=list(new_mask.items())
        links=[]
        proof={}
        for l in ("met3","met4"):
            through=[(r,rect) for r,rect in anchors[l] if r[0]==1 and on_segment(new,r)]
            leg=wire(old,new,cap.widths[l]) if not through else None
            if leg is not None:
                added.append((l,leg))
                links.append(dict(layer=l,start=old,end=new))
            known={r for r,_ in anchors[l]}
            # Preserve old-pad-only neighbors and avoid accidentally merging
            # an unrelated same-net route island on either affected layer.
            for r,rect in fixed[l]:
                if r in known:
                    continue
                retained=any(overlap(rect,s,True) for _,s in anchors[l])
                touches_old=overlap(rect,old_mask[l],True)
                touches_link=leg is not None and overlap(rect,leg,True)
                if touches_old and not (retained or touches_link):
                    stats["detaches_old_pad_neighbor"]+=1
                    break
                if (touches_link or overlap(rect,new_mask[l],True)) and not (retained or touches_old):
                    stats["joins_distinct_same_layer_island"]+=1
                    break
            else:
                support=through or anchors[l]
                proof[l]=dict(kind="retained_wire_through_both" if through else "linked_old_to_new",
                              exact_retained_records=[dict(route_row=r,rectangle_dbu=rect) for r,rect in support])
                continue
            break
        if len(proof)!=2:
            continue
        length=len(links)*abs(offset*x_step)
        assert length<=6000
        result.append(dict(typed=dict(net=net,via=cap.vias[old_cut[1]],old_at=old,new_at=new,links=links),
                           old_v3_raw=old_cut,frozen_v4=upper,old_v3_shape_dbu=old_mask,
                           new_v3_shape_dbu=new_mask,endpoint_proof=proof,additions_dbu=added,
                           center_gap_before_dbu=separation(old,upper),center_gap_after_dbu=separation(new,upper),
                           met4_x_pitch_steps=offset,added_centerline_dbu=length))
    return result

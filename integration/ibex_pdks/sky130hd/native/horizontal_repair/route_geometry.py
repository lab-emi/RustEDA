# SPDX-License-Identifier: Apache-2.0
"""Read only the bounded schema-six route geometry used by the scout."""
from collections import Counter, defaultdict
from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
import struct

ROW = struct.Struct("<8q")
MAX_FILE = 128 << 20
MAX_ROUTE_ROWS = 1_200_000
MAX_SCOPE_ROWS = 20_000
NETWORK_FIELDS = ("wires", "vias", "access", "patches", "nets")
COMP = re.compile(r"^-\s+(\S+)\s+(\S+)\s+\+\s+(?:PLACED|FIXED|COVER)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)\s*;$")
ITEM = re.compile(r"^-\s+(\S+)")


def read(path):
    assert 0 < path.stat().st_size <= MAX_FILE, path
    return json.loads(path.read_bytes())


def identity(path):
    assert 0 < path.stat().st_size <= MAX_FILE, path
    with path.open("rb") as stream:
        return dict(path=str(path), bytes=path.stat().st_size, sha256=file_digest(stream, "sha256").hexdigest())


def bound(record):
    path = Path(record["path"])
    assert identity(path) == record, path
    return path


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def canonical(text):
    return re.sub(r"\\(.)",r"\1",text)


def def_sites(path):
    section=None
    cells,ports={},{}
    with path.open() as f:
        for raw in f:
            line=raw.strip()
            if line.startswith("COMPONENTS "):
                section="components"
            elif line=="END COMPONENTS":
                section=None
            elif line.startswith("PINS "):
                section="pins"
            elif line=="END PINS":
                return cells,ports
            elif section and (item:=ITEM.match(line)):
                name=canonical(item[1])
                if section=="components":
                    parsed=COMP.fullmatch(line)
                    assert parsed and name not in cells,(name,line)
                    _,master,x,y,orient=parsed.groups()
                    cells[name]=(master,int(x),int(y),orient)
                else:
                    assert line.endswith(";") and name not in ports,(name,line)
                    ports[name]=" ".join(line.split())
    raise ValueError(f"incomplete DEF placement: {path}")


class RouteCapture:
    def __init__(self, rev, producer_path):
        self.producer=identity(producer_path)
        producer=read(producer_path)
        self.artifacts={k:producer["artifacts"][k] for k in ("def","gds","netlist_final")}
        for artifact in self.artifacts.values():
            bound(artifact)
        meta=rev/"design.json"
        self.design=identity(meta)
        data=read(meta)
        assert data["revision"]==rev.name and data["format_version"]==6
        physical=data["physical"]
        model=physical["connectivity"]
        route=physical["routing"]
        technique=physical["technology"]
        assert route["scope"]=="all" and technique["dbu_per_micron"]==1000
        self.tech_digest=digest({k:technique[k] for k in ("dbu_per_micron","layers","vias")})
        self.layers=route["layers"]
        self.vias=route["via_names"]
        self.widths={r["name"]:r.get("width") for r in technique["layers"]}
        by_name={v["name"]:v["geometries"] for v in technique["vias"]}
        self.via_shapes=[tuple((g["layer"],tuple(g["rect"])) for g in by_name[name])
                         for name in self.vias]
        table=route["table"]
        route_path=rev/table["file"]
        self.route=identity(route_path)
        assert self.route["sha256"]==table["sha256"] and self.route["bytes"]==table["bytes"]
        assert self.route["bytes"]%ROW.size==0 and self.route["bytes"]//ROW.size<=MAX_ROUTE_ROWS

        self.names=tuple(net["name"] for net in model["nets"])
        assert len(set(self.names))==len(self.names)<=MAX_SCOPE_ROWS
        cells,ports=def_sites(Path(self.artifacts["def"]["path"]))
        definitions=data["design"]["cells"]
        self.contacts={}
        self.raw=defaultdict(list)
        self.terminals=defaultdict(list)
        pins={}
        ignored=Counter()
        count=Counter()
        last=0
        for pin,p in enumerate(model["pins"]):
            owner=p["owner"]
            n=p["net"]
            if n is None or model["nets"][n]["use_"] not in ("signal","clock"):
                continue
            if owner["kind"]=="instance":
                i=owner["instance"]
                inst=definitions[i]["name"]
                master=model["masters"][model["instance_masters"][i]]
                stable=("instance",inst,master["pins"][owner["terminal"]]["name"])
                if inst not in cells:
                    continue
                location=cells[inst]
                assert master["name"]==location[0]
            else:
                assert owner["kind"]=="port"
                port=model["ports"][owner["port"]]["bits"][owner["bit"]]
                stable=("port",port)
                if port not in ports:
                    continue
                location=(ports[port],)
            pins[pin]=stable
            self.terminals[self.names[n]].append((stable,location))
        del data,physical,model,route,technique,definitions,cells,ports
        with route_path.open("rb") as stream:
            for block in iter(lambda:stream.read(1<<20),b""):
                for kind,net,a,b,c,d,e,_ in ROW.iter_unpack(block):
                    assert last<=kind<=5 and kind>=1 and net>=0
                    last=kind
                    count[kind]+=1
                    if net>=len(self.names):
                        ignored[kind]+=1
                        continue
                    name=self.names[net]
                    if kind==1:
                        assert (b,c)!=(d,e) and (b==d or c==e)
                        if (b,c)>(d,e):
                            b,c,d,e=d,e,b,c
                        self.raw[name].append((kind,a,b,c,d,e))
                    elif kind==2:
                        self.raw[name].append((kind,a,b,c))
                    elif kind==3:
                        assert a in pins,(rev,name,a)
                        self.contacts.setdefault(name,[]).append((pins[a],self.layers[b],c,d))
                    elif kind==4:
                        assert b<d and c<e
                        self.raw[name].append((kind,a,b,c,d,e))
        assert all(count[i]==table[k] for i,k in enumerate(NETWORK_FIELDS,1))
        for values in (*self.raw.values(),*self.terminals.values(),*self.contacts.values()):
            values.sort()
        assert self.raw.keys()<=self.terminals.keys() and self.raw.keys()<=self.contacts.keys()
        self.ignored_routing_records=dict(ignored)

    def binding(self):
        return dict(producer=self.producer,artifacts=self.artifacts,design=self.design,route=self.route,
                    technology=self.tech_digest,ignored_synthetic_records=self.ignored_routing_records)

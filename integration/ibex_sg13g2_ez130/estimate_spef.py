#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Placement-based RC estimate as a detailed SPEF (pre-route timing only).

usage: estimate_spef.py --def PLACED.def --lef CELLS.lef --policy POLICY.json
                        --workspace NEW --base-rev empty

writes NEW/estimate.spef and NEW/result.json, then HEAD rev1.

Each signal net becomes a rectilinear minimum spanning tree over its pin
locations (cell placement + oriented LEF pin centre, DEF port location),
rooted at its driver. With the optional policy macro_blockage, an edge whose
every monotone path crosses a CLASS BLOCK macro is measured around the macros
(shortest 4-connected path on a grid_um raster of the die). Every tree edge is
then stretched by an explicit detour factor and split into segments of at
most max_segment_um, each a pi section
of r_ohm_per_um / c_pf_per_um. The constants are calibrated against routed
and extracted designs of the same technology and are recorded, with their
evidence, in the policy (schema rusteda.rc-estimate-policy.v1). The output is
an estimate for pre-route optimization, never signoff parasitics.

Names follow the native extractor's SPEF conventions (name map, escaped
non-alphanumerics, instance pins as *<index>:<pin>). Work is bounded by
max_nets, max_pins_per_net and max_nodes; exceeding a bound fails.
"""
import argparse
from collections import deque
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import resource
import sys

ORIENT = {
    'N': lambda x, y, w, h: (x, y), 'S': lambda x, y, w, h: (w-x, h-y),
    'FN': lambda x, y, w, h: (w-x, y), 'FS': lambda x, y, w, h: (x, h-y),
    'W': lambda x, y, w, h: (h-y, x), 'E': lambda x, y, w, h: (y, w-x),
    'FW': lambda x, y, w, h: (y, x), 'FE': lambda x, y, w, h: (h-y, w-x),
}


def fail(text):
    raise ValueError(text)


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def escape(name):
    return ''.join(c if c.isascii() and (c.isalnum() or c in '_[]') else '\\'+c for c in name)


def read_lef(path):
    """MACRO -> (w_um, h_um, {pin: (cx_um, cy_um, direction)}) for signal pins."""
    macros, macro, pin, info = {}, None, None, None
    rects = []
    with open(path) as f:
        for raw in f:
            t = raw.split()
            if not t:
                continue
            if t[0] == 'MACRO':
                macro = t[1]; info = macros[macro] = [0.0, 0.0, {}]
            elif macro is None:
                continue
            elif t[0] == 'SIZE' and pin is None:
                info[0], info[1] = float(t[1]), float(t[3])
            elif t[0] == 'PIN':
                pin, direction, use, rects = t[1], 'INPUT', 'SIGNAL', []
            elif pin is not None and t[0] == 'DIRECTION':
                direction = t[1].rstrip(';')
            elif pin is not None and t[0] == 'USE':
                use = t[1].rstrip(';')
            elif pin is not None and t[0] == 'RECT':
                v = [float(x) for x in t[1:5]]
                rects.append(((v[0]+v[2])/2, (v[1]+v[3])/2))
            elif pin is not None and t[0] == 'END' and len(t) > 1 and t[1] == pin:
                if use not in ('POWER', 'GROUND') and rects:
                    rects.sort()
                    info[2][pin] = (*rects[len(rects)//2], direction)
                pin = None
            elif t[0] == 'END' and len(t) > 1 and t[1] == macro:
                macro = None
    return macros


def read_def(path, max_nets, max_pins):
    comps, ports, nets, scale, design = {}, {}, [], None, None
    section, cur, port = None, None, None
    with open(path) as f:
        for line in f:
            if section is None:
                if line.startswith('DESIGN '):
                    design = line.split()[1]
                elif line.startswith('UNITS DISTANCE MICRONS'):
                    scale = float(line.split()[3])
                elif line.startswith('COMPONENTS'):
                    section = 'C'
                elif line.startswith('PINS'):
                    section = 'P'
                elif line.startswith('NETS'):
                    section = 'N'
                continue
            if line.startswith('END '):
                if section == 'N' and cur:
                    nets.append(cur)
                section, cur = None, None
                continue
            if section == 'C':
                m = re.match(r'\s*- (\S+) (\S+) \+ (?:PLACED|FIXED|COVER) \( (-?\d+) (-?\d+) \) (\S+)', line)
                if m:
                    comps[m.group(1)] = (m.group(2), int(m.group(3)), int(m.group(4)), m.group(5))
            elif section == 'P':
                m = re.match(r'\s*- (\S+)', line)
                if m:
                    port = [m.group(1), 'INPUT', None, None]
                    ports[port[0]] = port
                if port:
                    d = re.search(r'DIRECTION (\w+)', line)
                    if d:
                        port[1] = d.group(1)
                    r = re.search(r'LAYER \S+ \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)', line)
                    if r:
                        port[2] = tuple(int(x) for x in r.groups())
                    p = re.search(r'(?:PLACED|FIXED) \( (-?\d+) (-?\d+) \)', line)
                    if p:
                        port[3] = (int(p.group(1)), int(p.group(2)))
            elif section == 'N':
                if line.startswith('- '):
                    if cur:
                        nets.append(cur)
                    head = line[2:].split(None, 1)
                    cur = [head[0], re.findall(r'\( (\S+) (\S+) \)', head[1].split('+')[0] if len(head) > 1 else '')]
                    if len(nets) >= max_nets:
                        fail('BUDGET: net count')
                elif cur and line.lstrip().startswith('(') and 'ROUTED' not in line and 'NEW' not in line:
                    cur[1] += re.findall(r'\( (\S+) (\S+) \)', line.split('+')[0])
                if cur and len(cur[1]) > max_pins:
                    fail('BUDGET: pins of net '+cur[0])
    if scale is None or design is None:
        fail('DEF units or design name missing')
    return comps, ports, nets, scale, design


def read_blocks(path):
    """CLASS BLOCK macro -> (w_um, h_um); hard macros block the signal layers."""
    blocks, macro, size, cls = {}, None, None, None
    with open(path) as f:
        for raw in f:
            t = raw.split()
            if not t:
                continue
            if t[0] == 'MACRO':
                macro, size, cls = t[1], None, None
            elif macro is None:
                continue
            elif t[0] == 'CLASS' and cls is None:
                cls = t[1].rstrip(';')
            elif t[0] == 'SIZE' and size is None:
                size = (float(t[1]), float(t[3]))
            elif t[0] == 'END' and len(t) > 1 and t[1] == macro:
                if cls == 'BLOCK' and size:
                    blocks[macro] = size
                macro = None
    return blocks


def read_die(path):
    """DIEAREA (two-point form) in DBU, read from the DEF header."""
    with open(path) as f:
        for line in f:
            m = re.match(r'DIEAREA \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)', line)
            if m:
                return tuple(int(v) for v in m.groups())
            if line.startswith('COMPONENTS'):
                break
    fail('DEF DIEAREA missing')


class Blockages:
    """Placed CLASS BLOCK macros as routing obstacles for estimated wires (um).

    Edges whose bounding box misses every macro interior keep their Manhattan
    length. Otherwise a monotone raster path inside the box keeps it too; only
    an edge every monotone path of which is cut (a macro or a macro row spans
    the box) is measured by breadth-first search over the die raster. Pins on
    a macro edge start from their nearest free cell. Work is bounded per
    search by the raster size and in total by max_searches.
    """
    def __init__(self, comps, blocks, die, scale, grid, max_searches):
        self.g = grid; self.max_searches = max_searches; self.searches = 0; self.cache = {}
        self.detoured = 0; self.extra_um = 0.0
        self.x0, self.y0 = die[0]/scale, die[1]/scale
        self.nx = max(1, math.ceil((die[2]-die[0])/scale/grid)); self.ny = max(1, math.ceil((die[3]-die[1])/scale/grid))
        if self.nx*self.ny > 4_000_000:
            fail('BUDGET: blockage raster')
        self.rects = []
        for master, x, y, orient in comps.values():
            if master not in blocks:
                continue
            w, h = blocks[master]
            if orient in ('W', 'E', 'FW', 'FE'):
                w, h = h, w
            self.rects.append((x/scale, y/scale, x/scale+w, y/scale+h))
        self.blocked = bytearray(self.nx*self.ny)
        for lx, ly, ux, uy in self.rects:
            for j in range(max(0, int((ly-self.y0)/grid)), min(self.ny, math.ceil((uy-self.y0)/grid))):
                cy = self.y0+(j+0.5)*grid
                if not ly < cy < uy:
                    continue
                for i in range(max(0, int((lx-self.x0)/grid)), min(self.nx, math.ceil((ux-self.x0)/grid))):
                    if lx < self.x0+(i+0.5)*grid < ux:
                        self.blocked[j*self.nx+i] = 1

    def cell(self, p):
        i = min(self.nx-1, max(0, int((p[0]-self.x0)/self.g))); j = min(self.ny-1, max(0, int((p[1]-self.y0)/self.g)))
        return i, j

    def free(self, c):
        """Nearest unblocked cell of c and its distance in cells (pins on macro edges)."""
        i, j = c
        if not self.blocked[j*self.nx+i]:
            return c, 0
        seen = {c}; todo = deque([(c, 0)])
        while todo:
            (i, j), d = todo.popleft()
            for a, b in ((i+1, j), (i-1, j), (i, j+1), (i, j-1)):
                if 0 <= a < self.nx and 0 <= b < self.ny and (a, b) not in seen:
                    if not self.blocked[b*self.nx+a]:
                        return (a, b), d+1
                    seen.add((a, b)); todo.append(((a, b), d+1))
        fail('blockage raster has no free cell')

    def monotone(self, a, b):
        """Is there a raster path from a to b moving only towards b?"""
        si = 1 if b[0] >= a[0] else -1; sj = 1 if b[1] >= a[1] else -1
        w = abs(b[0]-a[0])+1; h = abs(b[1]-a[1])+1
        row = [False]*w
        for dj in range(h):
            j = a[1]+sj*dj; prev = False
            for di in range(w):
                i = a[0]+si*di
                ok = not self.blocked[j*self.nx+i] and ((di == 0 and dj == 0) or row[di] or prev)
                row[di] = prev = ok
        return row[-1]

    def bfs(self, a, b):
        dist = {a: 0}; todo = deque([a])
        while todo:
            c = todo.popleft()
            if c == b:
                return dist[c]
            i, j = c
            for n in ((i+1, j), (i-1, j), (i, j+1), (i, j-1)):
                if 0 <= n[0] < self.nx and 0 <= n[1] < self.ny and n not in dist and not self.blocked[n[1]*self.nx+n[0]]:
                    dist[n] = dist[c]+1; todo.append(n)
        fail('no estimated path around blockages')

    def length(self, p, q):
        """Rectilinear wire length from p to q (um) around hard macros."""
        manhattan = abs(p[0]-q[0])+abs(p[1]-q[1])
        lx, ux = min(p[0], q[0]), max(p[0], q[0]); ly, uy = min(p[1], q[1]), max(p[1], q[1])
        if not any(r[0] < ux and lx < r[2] and r[1] < uy and ly < r[3] for r in self.rects):
            return manhattan
        (a, da), (b, db) = self.free(self.cell(p)), self.free(self.cell(q))
        key = (a, b) if a <= b else (b, a)
        if key not in self.cache:
            if self.monotone(a, b):
                self.cache[key] = None
            else:
                self.searches += 1
                if self.searches > self.max_searches:
                    fail('BUDGET: blockage searches')
                self.cache[key] = self.bfs(a, b)*self.g
        steps = self.cache[key]
        if steps is None:
            return manhattan
        around = max(manhattan, steps+(da+db)*self.g)
        self.detoured += 1; self.extra_um += around-manhattan
        return around


def blockages_for(policy, lef, def_path, comps, scale):
    """The policy's macro blockage model for a placed DEF, or None (legacy)."""
    spec = policy.get('macro_blockage')
    if spec is None:
        return None
    grid, searches = float(spec['grid_um']), int(spec['max_searches'])
    if not (1 <= grid <= 50 and 1 <= searches <= 1_000_000) or set(spec) != {'grid_um', 'max_searches'}:
        fail('policy macro_blockage outside qualified bounds')
    return Blockages(comps, read_blocks(lef), read_die(def_path), scale, grid, searches)


def tree_length(points, edges, blockages):
    if blockages is None:
        return sum(abs(points[a][0]-points[b][0])+abs(points[a][1]-points[b][1]) for a, b in edges)
    return sum(blockages.length(points[a], points[b]) for a, b in edges)


def mst(points):
    """Prim's rectilinear MST rooted at point 0; returns [(parent, child)]."""
    n = len(points); best = [math.inf]*n; parent = [0]*n; used = [False]*n; best[0] = 0; edges = []
    for _ in range(n):
        i = min((j for j in range(n) if not used[j]), key=lambda j: (best[j], j))
        used[i] = True
        if i:
            edges.append((parent[i], i))
        xi, yi = points[i]
        for j in range(n):
            if not used[j]:
                d = abs(xi-points[j][0])+abs(yi-points[j][1])
                if d < best[j]:
                    best[j], parent[j] = d, i
    return edges


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for a in ('def', 'lef', 'policy', 'workspace'):
        ap.add_argument('--'+a, required=True, type=Path)
    ap.add_argument('--base-rev', required=True)
    args = ap.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (3 << 30, 3 << 30))
    args.workspace.mkdir(parents=True, exist_ok=True)
    lock = (args.workspace/'workspace.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.base_rev != 'empty' or any(x.name != 'workspace.lock' for x in args.workspace.iterdir()):
        fail('REVISION_CONFLICT: estimate needs a fresh empty workspace')
    args.out, args.report = args.workspace/'estimate.spef', args.workspace/'result.json'
    policy = json.loads(args.policy.read_text())
    if policy.get('schema') != 'rusteda.rc-estimate-policy.v1':
        fail('policy schema')
    r_um, c_um, detour = float(policy['r_ohm_per_um']), float(policy['c_pf_per_um']), float(policy['detour'])
    seg = float(policy['max_segment_um'])
    if not (0 < r_um < 100 and 0 < c_um < 1e-3 and 1 <= detour <= 3 and 1 <= seg <= 500):
        fail('policy values outside qualified bounds')
    limits = policy['limits']
    macros = read_lef(args.lef)
    comps, ports, nets, scale, design = read_def(getattr(args, 'def'), int(limits['max_nets']), int(limits['max_pins_per_net']))
    blockages = blockages_for(policy, args.lef, getattr(args, 'def'), comps, scale)
    net_names = [n for n, _ in nets]
    instances, inst_index = [], {}
    for _, conns in nets:
        for c, _ in conns:
            if c != 'PIN' and c not in inst_index:
                inst_index[c] = len(net_names)+1+len(instances); instances.append(c)
    out = [f'*SPEF "IEEE 1481-1999"', f'*DESIGN "{design}"', '*DATE "1970-01-01 00:00:00 (fixed for byte reproducibility)"',
           '*VENDOR "RustEDA"', '*PROGRAM "estimate_spef.py"', '*VERSION "1"', '*DESIGN_FLOW "NAME_SCOPE LOCAL" "PIN_CAP NONE"',
           '*DIVIDER /', '*DELIMITER :', '*BUS_DELIMITER []', '*T_UNIT 1 NS', '*C_UNIT 1 PF', '*R_UNIT 1 OHM', '*L_UNIT 1 HENRY', '', '*NAME_MAP']
    out += [f'*{i+1} {escape(n)}' for i, n in enumerate(net_names)]
    out += [f'*{inst_index[n]} {escape(n)}' for n in instances]
    out += ['', '*PORTS'] + [f'{escape(p)} {"O" if d[1] == "OUTPUT" else "I" if d[1] == "INPUT" else "B"}' for p, d in ports.items()]
    nodes_total, stats, missing = 0, [], 0
    for ni, (name, conns) in enumerate(nets):
        pts, conn = [], []
        for c, p in conns:
            if c == 'PIN':
                port = ports.get(p)
                if port is None or port[3] is None:
                    missing += 1; continue
                x, y = port[3]
                if port[2]:
                    x += (port[2][0]+port[2][2])/2; y += (port[2][1]+port[2][3])/2
                driver = port[1] == 'INPUT'
                pts.append((x/scale, y/scale)); conn.append((escape(p), 'O' if port[1] == 'OUTPUT' else 'I', None, driver))
                continue
            master, ox, oy, orient = comps[c]
            w, h, pins = macros[master]
            if p not in pins:
                missing += 1; continue
            px, py, direction = pins[p]
            dx, dy = ORIENT[orient](px, py, w, h)
            pts.append((ox/scale+dx, oy/scale+dy))
            code = 'O' if direction == 'OUTPUT' else 'B' if direction == 'INOUT' else 'I'
            conn.append((f'*{inst_index[c]}:{escape(p)}', code, master, direction == 'OUTPUT'))
        if not conn:
            continue
        root = next((i for i, x in enumerate(conn) if x[3]), 0)
        order = [root]+[i for i in range(len(pts)) if i != root]
        pts = [pts[i] for i in order]; conn = [conn[i] for i in order]
        caps = [0.0]*len(conn); res = []; node = len(conn); length = 0.0; labels = [x[0] for x in conn]
        for a, b in mst(pts):
            dist = tree_length(pts, [(a, b)], blockages)*detour
            length += dist
            k = max(1, math.ceil(dist/seg)); piece = dist/k; prev = a
            for s in range(k):
                nxt = b if s == k-1 else node
                if nxt == node:
                    caps.append(0.0); labels.append(f'*{ni+1}:{node}'); node += 1
                caps[prev] += c_um*piece/2; caps[nxt] += c_um*piece/2
                res.append((prev, nxt, max(r_um*piece, 1e-3))); prev = nxt
        nodes_total += len(caps)
        if nodes_total > int(limits['max_nodes']):
            fail('BUDGET: estimated RC nodes')
        rendered = [f'{v:.5e}' if v else '0' for v in caps]
        total = sum(float(v) for v in rendered)
        out += ['', f'*D_NET *{ni+1} {total:.9e}', '*CONN']
        for label, code, master, _ in conn:
            out.append(f'*P {label} {code}' if master is None else f'*I {label} {code} *D {escape(master)}')
        out.append('*CAP')
        out += [f'{i+1} {labels[i]} {rendered[i]}' for i in range(len(caps))]
        out.append('*RES')
        out += [f'{i+1} {labels[a]} {labels[b]} {r:.5e}' for i, (a, b, r) in enumerate(res)]
        out.append('*END')
        stats.append((name, length, total))
    args.out.write_text('\n'.join(out)+'\n')
    report = dict(schema='rusteda.rc-estimate-result.v1', scope='placement_estimate_not_signoff',
                  inputs={k: dict(path=str(getattr(args, k)), sha256=sha(getattr(args, k))) for k in ('def', 'lef', 'policy')},
                  output=dict(path=str(args.out), sha256=sha(args.out)), nets=len(stats), rc_nodes=nodes_total,
                  unplaced_connections=missing, total_wire_um=round(sum(s[1] for s in stats), 3),
                  total_cap_pf=round(sum(s[2] for s in stats), 6), policy=policy,
                  macro_blockage=None if blockages is None else dict(
                      macros=len(blockages.rects), detoured_edges=blockages.detoured, searches=blockages.searches,
                      extra_wire_um=round(blockages.extra_um, 3)))
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    (args.workspace/'HEAD').write_text('rev1\n')
    print(json.dumps({k: report[k] for k in ('nets', 'rc_nodes', 'unplaced_connections', 'total_wire_um', 'total_cap_pf', 'macro_blockage')}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        raise SystemExit(2)

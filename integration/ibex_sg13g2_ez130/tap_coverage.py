#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Independent conservative diffusion-to-contacted-tie coverage certificate.

Run as a first-party Python macro in the pinned KLayout binary. This imports no
tap insertion code and uses no row, lattice, gap or tap-instance assumption.
Public IHP layout rules Rev.0.4 sections4.2 and7.2 define the derived layers and
20um limit. Targets/anchors exclude explicitly named SRAM subtrees;
FULL well/body geometry remains, including those subtrees. Foundry main LU.b
and maximal LU.a full-layout checks and macro identity/LVS remain separate.

All geometry and distance arithmetic is integer DBU. A bounded box certificate
uses L1 distance to actual contacted tie points in the same connected domain.
The triangle inequality proves every point of each accepted box is <=20um
away; this is conservative for Euclidean distance. Failure means unverified
coverage, not a fabricated foundry finding. No sampling-only pass is possible.

LU.a additionally measures the distance inside the n-well, as the foundry rule
grows the ties inside NWell: in a non-rectangular domain every target box must
also pass a second certificate whose paths stay inside the closed domain
(WellCorridor). Every certified path is a real path inside the well, so its
length is at least the in-well L1 distance, which is at least the straight-line
one; this certificate runs after the first and only adds failures. LU.b keeps
the straight-line distance, as the foundry LU.b grows the ties without a well.

Geometry is processed by native KLayout region operations: source shapes are
validated and counted once per master cell and weighted by exact occurrence
counts, layers are flattened by the region constructor, contacts are reduced
to those touching tie diffusion before any Boolean, and each domain's targets
and ties are clipped from a bounding-box window. Every derived target and tie
region is the same point set as the plain expression it replaces, and each
domain's targets and ties are merged again before use (component_region): a
target box is the bounding box of one connected diffusion and a tie point is
one vertex of one connected contacted tie, however cells, instances or the
operations above cut or repeat them. Pieces that touch at a corner count as
connected: such a tie gives one point, and such a target is certified as the
box of the whole and as the boxes of its edge-connected parts.

Fixture estimate: <2GiB based on merged source geometry (eight source regions),
derived domains/dopants, target/tie/intersection temporaries and the reader;
measured peak RSS below 0.35GiB on the Ibex final layouts (0.57GiB with the
per-shape flattening that the native regions replaced). Aggregated hierarchy
state<128MiB (including dictionary growth), anchors/bins<96MiB, one
worker. Caps are not a generic admission proof for native Boolean intermediates:
they allocate before output inspection. Caller MUST supply4GiB/1GiB and600s
guards and GNU peak RSS; native allocation/time failure means unverified.
Tiny controls use1GiB/60s. Python traversals have explicit count/work caps.
"""
import bisect
from collections import deque
from decimal import Decimal
import fcntl
import hashlib
import heapq
import json
from pathlib import Path
import re
import time


CEILINGS = dict(cells=16384, hierarchy_edges=250000, instance_visits=4000000,
                input_shapes=2000000, vertices=8000000, shape_visits=8000000,
                vertex_visits=32000000, domains=4096, anchors=250000,
                bin_entries=1000000, candidates=4096, boxes=16000000,
                distance_checks=200000000, pending=64, depth=32)


class CoverageLimit(ValueError):
    pass


class Budget:
    def __init__(self, limits):
        if set(limits) != set(CEILINGS) or any(type(v) is not int or not 1 <= v <= CEILINGS[k]
                                             for k, v in limits.items()):
            raise ValueError('INVALID_COVERAGE_LIMITS')
        self.limits, self.used = limits, {}

    def charge(self, name, count=1):
        value = self.used.get(name, 0) + count
        if value > self.limits[name]:
            raise CoverageLimit('COVERAGE_BUDGET:' + name)
        self.used[name] = value

    def charge_hierarchy_record(self):
        # Raw scans and expanded occurrences are different bounded quantities;
        # keep both counters instead of overwriting one with the other.
        name = 'hierarchy_instance_records'
        value = self.used.get(name, 0) + 1
        if value > self.limits['instance_visits']:
            raise CoverageLimit('COVERAGE_BUDGET:' + name)
        self.used[name] = value


class TiePoints:
    def __init__(self, points, distance, budget):
        self.distance, self.budget, self.bins = distance, budget, {}
        for x, y in points:
            budget.charge('anchors')
            budget.charge('bin_entries')
            self.bins.setdefault((x // distance, y // distance), []).append((x, y))

    def nearest(self, x, y):
        best, candidates = self.distance + 1, 0
        bx, by = x // self.distance, y // self.distance
        for ix in range(bx - 1, bx + 2):
            for iy in range(by - 1, by + 2):
                for px, py in self.bins.get((ix, iy), ()):
                    candidates += 1
                    if candidates > self.budget.limits['candidates']:
                        raise CoverageLimit('COVERAGE_BUDGET:candidates')
                    self.budget.charge('distance_checks')
                    best = min(best, abs(px - x) + abs(py - y))
        return best

    def reaches(self, x, y, threshold):
        """Whether nearest(x, y) <= threshold, stopping at the first tie point that proves it."""
        if threshold < 0:
            return False
        bx, by = x // self.distance, y // self.distance
        cells = [self.bins.get((bx, by), ())] + [self.bins.get((ix, iy), ()) for ix in range(bx - 1, bx + 2)
                                                  for iy in range(by - 1, by + 2) if (ix, iy) != (bx, by)]
        if sum(map(len, cells)) > self.budget.limits['candidates']:
            raise CoverageLimit('COVERAGE_BUDGET:candidates')
        checks = 0
        for cell in cells:
            for px, py in cell:
                checks += 1
                if abs(px - x) + abs(py - y) <= threshold:
                    self.budget.charge('distance_checks', checks)
                    return True
        self.budget.charge('distance_checks', checks)
        return False

    def candidates(self, x, y):
        """Every tie point within the limit of (x, y) as (distance, x, y), nearest first."""
        bx, by = x // self.distance, y // self.distance
        cells = [self.bins.get((ix, iy), ()) for ix in range(bx - 1, bx + 2) for iy in range(by - 1, by + 2)]
        checks = sum(map(len, cells))
        if checks > self.budget.limits['candidates']:
            raise CoverageLimit('COVERAGE_BUDGET:candidates')
        self.budget.charge('distance_checks', checks)
        found = [(distance, px, py) for cell in cells for px, py in cell
                 for distance in (abs(px - x) + abs(py - y),) if distance <= self.distance]
        found.sort()
        return found


def span(ax, ay, bx, by):
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


class WellCorridor:
    """Certified paths inside one closed, non-rectangular well domain polygon.

    The closure of the domain's bounding box minus the domain is given twice as
    closed rectangles: cut into horizontal slabs (`rows`, no vertical cut lines) and
    into vertical slabs (`columns`, no horizontal cut lines); rectangles on the
    bounding box edge reach 1 DBU past it. A box inside the bounding box lies in the
    closed domain iff it overlaps no such rectangle in the open sense; a vertical
    segment is tested against `rows`, a horizontal one against `columns`, so a cut
    line is never mistaken for well, and a point uses the exact `inside` test.
    A box whose bounding box with a point q lies in the domain reaches q by a
    monotone in-well path of exactly the L1 distance. Reflex corners of the domain
    are relays: Dijkstra over monotone hops gives each corner the length of an
    in-well path to a contacted tie point, so every certified length is that of a
    real in-well path: never below the in-well or straight-line L1 distance.
    """

    def __init__(self, frame, rows, columns, inside, corners, points, budget):
        self.frame, self.inside, self.points, self.budget = frame, inside, points, budget
        self.limit = points.distance
        self.slabs = []
        for holes in (rows, columns):
            holes = sorted(holes)
            self.slabs.append((holes, [hole[0] for hole in holes],
                               max((hole[2] - hole[0] for hole in holes), default=0)))
        corners = sorted(set(corners))
        index = {}
        for number, (x, y) in enumerate(corners):
            budget.charge('anchors')
            budget.charge('bin_entries')
            index.setdefault((x // self.limit, y // self.limit), []).append(number)
        reach, queue = [None] * len(corners), []
        for number, (x, y) in enumerate(corners):
            for distance, px, py in points.candidates(x, y):
                if self.contains(*span(x, y, px, py)):
                    queue.append((distance, number))
                    break
        heapq.heapify(queue)
        while queue:
            distance, number = heapq.heappop(queue)
            if reach[number] is not None:
                continue
            reach[number] = distance
            x, y = corners[number]
            for ix in range(x // self.limit - 1, x // self.limit + 2):
                for iy in range(y // self.limit - 1, y // self.limit + 2):
                    for other in index.get((ix, iy), ()):
                        if reach[other] is not None:
                            continue
                        ox, oy = corners[other]
                        total = distance + abs(ox - x) + abs(oy - y)
                        if total <= self.limit and self.contains(*span(x, y, ox, oy)):
                            heapq.heappush(queue, (total, other))
        self.relays = {}
        for number, (x, y) in enumerate(corners):
            if reach[number] is not None:
                self.relays.setdefault((x // self.limit, y // self.limit), []).append((x, y, reach[number]))

    def contains(self, lx, ly, ux, uy):
        """Whether the closed box [lx,ux]x[ly,uy] lies in the closed domain."""
        fl, fb, fr, ft = self.frame
        if lx < fl or ly < fb or ux > fr or uy > ft:
            return False
        if lx == ux and ly == uy:
            self.budget.charge('distance_checks')
            return self.inside(lx, ly)
        holes, lefts, widest = self.slabs[1 if ly == uy else 0]
        for number in range(bisect.bisect_left(lefts, lx - widest), len(holes)):
            hl, hb, hr, ht = holes[number]
            if hl > ux:
                break
            self.budget.charge('distance_checks')
            if hl < ux and lx < hr and hb < uy and ly < ht:
                return False
        return True

    def clear(self, box):
        """No hole within the limit of the box. Then every hop that the straight-line
        certificate accepted for this box (to a tie point within the limit, inside the
        domain's bounding box) already stays in the well, so that certificate suffices."""
        lx, ly, ux, uy = (box[0] - self.limit, box[1] - self.limit, box[2] + self.limit, box[3] + self.limit)
        holes, lefts, widest = self.slabs[0]
        for number in range(bisect.bisect_left(lefts, lx - widest), len(holes)):
            hl, hb, hr, ht = holes[number]
            if hl > ux:
                break
            if hl < ux and lx < hr and hb < uy and ly < ht:
                return False
        return True

    def reach(self, box, x, y, radius):
        """(accepted, length of the shortest certified path from the centre, or limit+1)."""
        lx, ly, ux, uy = box
        options = self.points.candidates(x, y)
        for total, qx, qy in options:  # tie points first: most boxes need no relay
            if total + radius > self.limit:
                break
            if self.contains(min(lx, qx), min(ly, qy), max(ux, qx), max(uy, qy)):
                return True, total
        for ix in range(x // self.limit - 1, x // self.limit + 2):
            for iy in range(y // self.limit - 1, y // self.limit + 2):
                for wx, wy, length in self.relays.get((ix, iy), ()):
                    self.budget.charge('distance_checks')
                    total = abs(wx - x) + abs(wy - y) + length
                    if total <= self.limit:
                        options.append((total, wx, wy))
        options.sort()
        for total, qx, qy in options:
            if total + radius > self.limit:
                break
            if self.contains(min(lx, qx), min(ly, qy), max(ux, qx), max(uy, qy)):
                return True, total
        for total, qx, qy in options:
            if self.contains(*span(x, y, qx, qy)):
                return False, total
        return False, self.limit + 1


def certify_box(box, points, budget, corridor=None):
    """Sufficient certificate over the entire closed box, with bounded DFS.

    With a WellCorridor, every accepted path also stays inside the well domain."""
    pending = [(box, 0)]
    while pending:
        budget.charge('boxes')
        (lx, ly, ux, uy), depth = pending.pop()
        x, y = (lx + ux) // 2, (ly + uy) // 2
        radius = max(x - lx, ux - x) + max(y - ly, uy - y)
        if corridor is None:
            if points.reaches(x, y, points.distance - radius):
                continue
            distance = points.nearest(x, y)
            if distance > points.distance:
                return dict(box=[lx, ly, ux, uy], center=[x, y],
                            nearest_l1_distance_dbu_at_least=distance)
        else:
            accepted, distance = corridor.reach((lx, ly, ux, uy), x, y, radius)
            if accepted:
                continue
            if distance > points.distance:
                return dict(box=[lx, ly, ux, uy], center=[x, y],
                            diagnostic='NO_CERTIFIED_IN_WELL_PATH_WITHIN_LIMIT')
        if depth >= budget.limits['depth'] or lx == ux and ly == uy:
            raise CoverageLimit('COVERAGE_BUDGET:depth')
        if max(ux - lx, uy - ly) <= 1:
            return dict(box=[lx, ly, ux, uy], diagnostic='CONTINUOUS_DBU_BOX_UNCERTIFIED')
        if ux - lx >= uy - ly:
            children = [((lx, ly, x, uy), depth + 1), ((x, ly, ux, uy), depth + 1)]
        else:
            children = [((lx, ly, ux, y), depth + 1), ((lx, y, ux, uy), depth + 1)]
        if len(pending) + 2 > budget.limits['pending']:
            raise CoverageLimit('COVERAGE_BUDGET:pending')
        pending.extend(children)
    return None


def identity(path, cap=64 << 20):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    if not path.is_file() or before.st_size > cap:
        raise ValueError('COVERAGE_FILE_BYTE_BUDGET')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('COVERAGE_INPUT_CHANGED')
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def hierarchy(layout, top, excluded, budget):
    """Count occurrences using bounded distinct dependencies, not raw-edge lists.

    Every raw instance is inspected under the existing instance_visits ceiling.
    hierarchy_edges caps stored parent/child pairs; duplicate multiplicities add
    exactly. Geometry traversal still visits the original native hierarchy.
    """
    cells, edges, indegree = {}, {}, {}
    for cell in layout.each_cell():
        budget.charge('cells')
        key = cell.cell_index()
        cells[key], edges[key], indegree[key] = cell, {}, 0
    # Each of at most instance_visits raw records contributes at most that many
    # occurrences. This permits unreachable aggregate edges larger than a
    # reachable occurrence cap while bounding their integer representation.
    multiplicity_bound = budget.limits['instance_visits'] ** 2
    for key, cell in cells.items():
        for instance in cell.each_inst():
            budget.charge_hierarchy_record()
            if instance.is_complex():
                raise ValueError('COVERAGE_COMPLEX_TRANSFORM_UNSUPPORTED')
            child = instance.cell_index
            multiplicity = instance.na * instance.nb if instance.is_regular_array() else 1
            if child not in cells or not 1 <= multiplicity <= budget.limits['instance_visits']:
                raise ValueError('INVALID_COVERAGE_HIERARCHY')
            previous = edges[key].get(child, 0)
            if previous > multiplicity_bound - multiplicity:
                raise CoverageLimit('COVERAGE_BUDGET:hierarchy_multiplicity')
            if not previous:
                budget.charge('hierarchy_edges')
                indegree[child] += 1
            edges[key][child] = previous + multiplicity
    ready = deque(key for key, degree in indegree.items() if degree == 0)
    order = []
    while ready:
        key = ready.popleft()
        order.append(key)
        for child in edges[key]:
            indegree[child] -= 1
            if not indegree[child]:
                ready.append(child)
    if len(order) != len(cells):
        raise ValueError('CYCLIC_COVERAGE_HIERARCHY')
    full, selected = {top.cell_index(): 1}, {top.cell_index(): 1}
    for key in order:
        for child, multiplier in edges[key].items():
            for counts, stop in ((full, False), (selected, key in excluded)):
                if not stop and counts.get(key, 0):
                    existing = counts.get(child, 0)
                    if multiplier > (budget.limits['instance_visits'] - existing) // counts[key]:
                        raise CoverageLimit('COVERAGE_BUDGET:instance_visits')
                    counts[child] = existing + counts[key] * multiplier
    total = sum(full.values())
    if total > budget.limits['instance_visits']:
        raise CoverageLimit('COVERAGE_BUDGET:instance_visits')
    budget.used['instance_visits'] = total
    budget.used.setdefault('hierarchy_instance_records', 0)
    budget.used.setdefault('hierarchy_edges', 0)
    return cells, full, selected


LAYERS = dict(active=(1, 0), psd=(14, 0), nsd=(7, 0), nsd_block=(7, 21),
              nwell=(31, 0), pwell_block=(46, 21), pwell_draw=(46, 0), contact=(6, 0))
FULL_HIERARCHY = ('nwell', 'pwell_block', 'pwell_draw')


def region_inventory(region, budget):
    """Polygons and vertices of a region as stored, counted natively."""
    semantics = region.merged_semantics
    region.merged_semantics = False
    count, vertices = region.count(), region.edges().count()
    region.merged_semantics = semantics
    budget.charge('shape_visits', count)
    budget.charge('vertex_visits', vertices)
    if count > budget.limits['input_shapes'] or vertices > budget.limits['vertices']:
        raise CoverageLimit('COVERAGE_REGION_SIZE_BUDGET')
    return dict(polygons=count, vertices=vertices)


def source_layer(pya, cells, counts, skipped, layer, budget):
    """Validate every reachable shape of one layer and charge its flattened counts.

    Each master cell is checked once and weighted by its exact occurrence count;
    `skipped` cells (the excluded macros, for target and tie layers) are not visited,
    as the recursive iterator that flattens the layer does not visit them.
    hierarchy() rejected every complex instance, so each occurrence applies a unit
    orthogonal transform, which keeps shape class, rectilinearity and vertex count.
    """
    other = pya.Shapes.SEdges | pya.Shapes.SEdgePairs | pya.Shapes.SPoints | pya.Shapes.SUserObjects
    shapes_total = vertices_total = 0
    for key, cell in cells.items():
        occurrences, shapes = counts.get(key, 0), cell.shapes(layer)
        if not occurrences or key in skipped or shapes.is_empty():
            continue
        if next(iter(shapes.each(pya.Shapes.SPaths)), None) is not None:
            raise ValueError('COVERAGE_PATH_GEOMETRY_UNSUPPORTED')
        if next(iter(shapes.each(other)), None) is not None:
            raise ValueError('COVERAGE_SHAPE_CLASS_UNSUPPORTED')
        raw = pya.Region(shapes)  # boxes and polygons; texts are not geometry
        raw.merged_semantics = False
        if not raw.non_rectilinear().is_empty():
            raise ValueError('COVERAGE_NONRECTILINEAR_GEOMETRY_UNSUPPORTED')
        shapes_total += raw.count() * occurrences
        vertices_total += raw.edges().count() * occurrences
        if shapes_total > budget.limits['input_shapes'] or vertices_total > budget.limits['vertices']:
            raise CoverageLimit('COVERAGE_BUDGET:input_shapes')
    budget.charge('input_shapes', shapes_total)
    budget.charge('vertices', vertices_total)
    return dict(source_shapes=shapes_total, source_vertices=vertices_total)


def partition(region, frames, size, budget):
    """Polygons of `region` grouped by every domain frame that their bounding box meets.

    `frames` are the domains' bounding boxes. A polygon with positive area inside a
    domain meets its frame, so (group & domain) is exactly (region & domain)."""
    grid, groups = {}, [[] for _ in frames]
    for number, (fl, fb, fr, ft) in enumerate(frames):
        for ix in range(fl // size, fr // size + 1):
            for iy in range(fb // size, ft // size + 1):
                budget.charge('bin_entries')
                grid.setdefault((ix, iy), []).append(number)
    for polygon in region.each():
        box = polygon.bbox()
        seen = set()
        for ix in range(box.left // size, box.right // size + 1):
            for iy in range(box.bottom // size, box.top // size + 1):
                for number in grid.get((ix, iy), ()):
                    fl, fb, fr, ft = frames[number]
                    if number not in seen and fl <= box.right and box.left <= fr and fb <= box.top and box.bottom <= ft:
                        seen.add(number)
                        groups[number].append(polygon)
    return groups


def component_region(pya, group, polygon, alone):
    """Merged polygons of (region & polygon) for one domain, from that domain's group.

    `group` holds every polygon of the region whose bounding box meets the domain's frame, so
    (group & polygon) is the same point set as (region & polygon). `alone`: no other domain
    frame meets any polygon of the region, which is already clipped to the domains, so all of
    it lies in this domain and no clip is needed. The pieces may overlap, abut or repeat
    (cell pieces, repeated instances, a clip that returns its input pieces): the final merge
    of a fresh region makes one polygon per connected part, corner contacts included."""
    pieces = pya.Region(group)
    if not alone:
        pieces = pya.Region(list((pieces & pya.Region(polygon)).each()))
    pieces.min_coherence = False
    pieces.merge()
    return pieces


def corner_part_boxes(pya, region, known):
    """Bounding boxes of the edge-connected parts of polygons that join parts at a corner only.

    `region` is a component_region(). Empty when no polygon touches itself at a corner (the part
    count then equals the polygon count); boxes in `known` are left out."""
    parts = region.dup()
    parts.min_coherence = True
    parts.merge()
    if parts.count() == region.count():
        return []
    boxes = {(box.left, box.bottom, box.right, box.top) for box in (part.bbox() for part in parts.each())}
    return sorted(boxes - known)


def slab_rectangles(pya, region, transform, frame):
    """Closed rectangles of `region` cut into horizontal slabs in `transform`'s frame."""
    fl, fb, fr, ft = frame
    rectangles = []
    for piece in region.transformed(transform).each():
        for part in piece.decompose_trapezoids(pya.Polygon.TD_simple):
            if not part.is_box():
                raise ValueError('COVERAGE_WELL_DECOMPOSITION')
            box = part.bbox().transformed(transform.inverted())
            rectangles.append((box.left - (box.left == fl), box.bottom - (box.bottom == fb),
                               box.right + (box.right == fr), box.top + (box.top == ft)))
    return rectangles


def well_corridor(pya, polygon, points, budget):
    box = polygon.bbox()
    frame = (box.left, box.bottom, box.right, box.top)
    outside = pya.Region(box) - pya.Region(polygon)
    rows = slab_rectangles(pya, outside, pya.Trans(), frame)
    columns = slab_rectangles(pya, outside, pya.Trans(pya.Trans.M45), frame)
    if len(rows) + len(columns) > budget.limits['anchors']:
        raise CoverageLimit('COVERAGE_BUDGET:anchors')
    corners = [(x, y) for hl, hb, hr, ht in rows for x in (hl, hr) for y in (hb, ht)
               if polygon.inside(pya.Point(x, y))]
    return WellCorridor(frame, rows, columns, lambda x, y: polygon.inside(pya.Point(x, y)),
                        corners, points, budget)


def geometry_check(config, budget):
    import pya  # Available only inside the pinned black-box reference binary.
    layout = pya.Layout()
    layout.read(config['gds']['path'])
    top = layout.cell(config['top'])
    top_cells = iter(layout.top_cells())
    if top is None or next(top_cells, None) != top or next(top_cells, None) is not None:
        raise ValueError('COVERAGE_TOP_CELL_MISMATCH')
    dbu = Decimal(str(layout.dbu))
    distance = Decimal(20) / dbu
    if distance != distance.to_integral_value() or not 1 <= distance <= 1000000:
        raise ValueError('COVERAGE_DBU_NOT_SUPPORTED')
    distance = int(distance)
    excluded = {}
    for name, expected in config['excluded_cells'].items():
        if not re.fullmatch('[A-Za-z0-9_-]+', name) or type(expected) is not int or not 1 <= expected <= 16:
            raise ValueError('COVERAGE_EXCLUSION_SCHEMA')
        cell = layout.cell(name)
        if cell is None:
            raise ValueError('COVERAGE_EXCLUSION_MISSING')
        excluded[cell.cell_index()] = expected
    cells, full, selected = hierarchy(layout, top, excluded, budget)
    if any(full.get(key, 0) != expected for key, expected in excluded.items()):
        raise ValueError('COVERAGE_EXCLUSION_COUNT')
    indices = {name: layout.layer(*spec) for name, spec in LAYERS.items()}
    for name, layer in indices.items():
        counts = full if name in FULL_HIERARCHY else selected
        estimated = sum(cell.shapes(layer).size() * counts.get(key, 0) for key, cell in cells.items())
        if estimated > budget.limits['input_shapes']:
            raise CoverageLimit('COVERAGE_INPUT_SHAPE_ESTIMATE:' + name)
    regions, inventory, store = {}, {}, pya.DeepShapeStore()
    for name, layer in indices.items():
        counts = full if name in FULL_HIERARCHY else selected
        inventory[name] = source_layer(pya, cells, counts, () if name in FULL_HIERARCHY else excluded,
                                       layer, budget)
        if name == 'contact':
            continue  # flattened below, only where it can touch tie diffusion
        iterator = top.begin_shapes_rec(layer)
        if name not in FULL_HIERARCHY:
            iterator.unselect_cells(list(excluded))
        if name in FULL_HIERARCHY:
            # Merged per cell variant, then flattened and merged once more: the same merged
            # polygons as merging the flattened layer, with repeated cells merged only once.
            deep = pya.Region(iterator, store)
            deep.merge()
            region = pya.Region()
            region.insert(deep)
            region.min_coherence = True
            region.merge()
            inventory[name].update(region_inventory(region, budget))
            regions[name] = region
        else:
            regions[name] = pya.Region(iterator, store)  # dopant Booleans run per cell variant
    frame = pya.Region(top.bbox())
    pwell = (frame - regions['nwell'] - regions['pwell_block']) | regions['pwell_draw']
    p_active = regions['active'] & regions['psd']
    # active lies inside frame, so this is active & ((frame - psd - nsd_block) | nsd).
    n_active = ((regions['active'] - regions['psd']) - regions['nsd_block']) | (regions['active'] & regions['nsd'])
    rules = []
    for rule, domain, target, tie_diffusion, in_well in (
            ('LU.a', regions['nwell'], p_active, n_active, True),
            ('LU.b', pwell, n_active, p_active, False)):
        domain.min_coherence = True
        domain.merge()
        rules.append([rule, domain, region_inventory(domain, budget), target & domain, tie_diffusion & domain, in_well])
    # Only contacts touching tie diffusion can intersect it; the iterator delivers every
    # contact that overlaps or touches the union, so (contacts & tie diffusion) is exact.
    tie_window = rules[0][4] | rules[1][4]  # kept referenced while the iterator is in use
    iterator = top.begin_shapes_rec(indices['contact'])
    iterator.region = tie_window
    iterator.unselect_cells(list(excluded))
    contacts = pya.Region(iterator)
    contacts.merged_semantics = False
    reports = {}
    for rule, domain, domain_inventory, target, tie_diffusion, in_well in rules:
        ties = contacts & tie_diffusion
        region_inventory(target, budget)
        region_inventory(ties, budget)
        polygons = list(domain.each())
        frames = [(b.left, b.bottom, b.right, b.top) for b in (poly.bbox() for poly in polygons)]
        target_groups = partition(target, frames, distance, budget)
        tie_groups = partition(ties, frames, distance, budget)
        report = dict(status='passed', domains=0, target_polygons=0, tie_points=0,
                      uncertified_boxes=[], uncertified_count=0, in_well_domains=0,
                      in_well_uncertified_boxes=[], in_well_uncertified_count=0,
                      distance='in_well_L1_upper_bound_and_straight_L1' if in_well else 'straight_L1',
                      domain_inventory=domain_inventory)
        target_domains = sum(1 for group in target_groups if group)
        tie_domains = sum(1 for group in tie_groups if group)
        for number, poly in enumerate(polygons):
            budget.charge('domains')
            if not target_groups[number]:
                continue  # no target polygon meets this domain's frame
            targets = component_region(pya, target_groups[number], poly, target_domains == 1)
            if targets.is_empty():
                continue
            report['domains'] += 1
            anchors = []
            if tie_groups[number]:
                for anchor in component_region(pya, tie_groups[number], poly, tie_domains == 1).each():
                    # A polygon vertex is an actual contacted-tie point. Using
                    # that point is stronger than using an arbitrary bbox center.
                    point = next(anchor.each_point_hull())
                    if len(anchors) >= budget.limits['anchors']:
                        raise CoverageLimit('COVERAGE_BUDGET:anchors')
                    anchors.append((point.x, point.y))
            report['tie_points'] += len(anchors)
            points = TiePoints(anchors, distance, budget)
            corridor = None
            if in_well and not poly.is_box():
                corridor = well_corridor(pya, poly, points, budget)
                report['in_well_domains'] += 1
            certified = set()
            for target_poly in targets.each():
                report['target_polygons'] += 1
                budget.charge('shape_visits')
                box = target_poly.bbox()
                box = (box.left, box.bottom, box.right, box.top)
                certified.add(box)
                failure = certify_box(box, points, budget)
                if failure is not None:
                    report['status'] = 'unverified'
                    report['uncertified_count'] += 1
                    if len(report['uncertified_boxes']) < 32:
                        report['uncertified_boxes'].append(failure)
                elif corridor is not None and not corridor.clear(box):
                    # Only diffusion points need an in-well path: certify the target's own
                    # rectangles, each inside the domain, not its bounding box.
                    parts = [box] if target_poly.is_box() else [
                        (r.left, r.bottom, r.right, r.top) for r in
                        (part.bbox() for part in target_poly.decompose_trapezoids(pya.Polygon.TD_simple))]
                    failure = next((f for f in (certify_box(part, points, budget, corridor) for part in parts)
                                    if f is not None), None)
                    if failure is not None:
                        report['status'] = 'unverified'
                        report['in_well_uncertified_count'] += 1
                        if len(report['in_well_uncertified_boxes']) < 32:
                            report['in_well_uncertified_boxes'].append(failure)
            # A diffusion joined at a corner was certified above as one box; its parts are
            # certified on their own as well, so both readings of such a diffusion are proven.
            for box in corner_part_boxes(pya, targets, certified):
                budget.charge('shape_visits')
                failure = certify_box(box, points, budget)
                if failure is not None:
                    report['status'] = 'unverified'
                    report['uncertified_count'] += 1
                    if len(report['uncertified_boxes']) < 32:
                        report['uncertified_boxes'].append(failure)
        if not report['target_polygons'] or not report['tie_points']:
            report['status'] = 'unverified'
        reports[rule] = report
    return dict(status='passed' if all(r['status'] == 'passed' for r in reports.values()) else 'unverified',
                rules=reports, dbu_um=str(dbu), max_distance_dbu=distance, layers=inventory)


def run(config_path):
    config_path = Path(config_path).resolve(strict=True)
    config_id = identity(config_path, 1 << 20)
    config = json.loads(config_path.read_bytes())
    if set(config) != {'schema', 'gds', 'workspace', 'base_revision', 'top', 'excluded_cells', 'limits'} or (
            config['schema'] != 'rusteda.tap-coverage.v1' or not re.fullmatch('[A-Za-z0-9_-]+', config['top'])):
        raise ValueError('COVERAGE_CONFIG_SCHEMA')
    # This project certificate names exactly two SRAMs. Arbitrary
    # caller-selected exclusions must not turn a missing tie into a pass.
    allowed_macros = {'RM_IHPSG13_1P_1024x32_c2_bm_bist': 2}
    if config['excluded_cells'] not in ({}, allowed_macros):
        raise ValueError('COVERAGE_EXCLUSION_SCOPE')
    output = Path(config['workspace']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if config['base_revision'] != 'empty' or any(p.name != 'workspace.lock' for p in output.iterdir()):
            raise ValueError('REVISION_CONFLICT')
        gds = identity(config['gds']['path'])
        if gds != config['gds']:
            raise ValueError('COVERAGE_GDS_IDENTITY')
        source = identity(__file__, 1 << 20)
        budget = Budget(config['limits'])
        started = time.monotonic()
        try:
            report = geometry_check(config, budget)
        except (CoverageLimit, ValueError) as error:
            report = dict(status='unverified', diagnostic=str(error))
        if (identity(gds['path']) != gds or identity(config_path, 1 << 20) != config_id
                or identity(__file__, 1 << 20) != source):
            raise ValueError('COVERAGE_SOURCE_CHANGED')
        report.update(schema='rusteda.tap-coverage-result.v1', gds=gds, config=config_id,
                      checker=source, limits=budget.limits, work=budget.used,
                      hierarchy_budget_scope=dict(
                          hierarchy_edges='distinct_stored_parent_child_dependencies',
                          hierarchy_instance_records_limit=budget.limits['instance_visits'],
                          instance_visits='expanded_full_hierarchy_occurrences'),
                      excluded_cells=config['excluded_cells'], scope='nonexcluded_diffusion_contact_targets_full_well_domains',
                      excluded_macro_content_status='unchecked_requires_separate_macro_contract_and_full_lvs',
                      geometry_scope='rectilinear_polygons_unit_orthogonal_transforms_no_paths',
                      criterion='conservative_complete_box_L1_contacted_tie_certificate',
                      lu_a_distance='also_inside_nwell_monotone_paths_via_certified_reflex_corners',
                      full_foundry_latchup_status='unchecked', wall_seconds=time.monotonic() - started)
        text = json.dumps(report, indent=2, allow_nan=False) + '\n'
        if len(text.encode()) > 4 << 20:
            raise ValueError('COVERAGE_REPORT_BYTE_BUDGET')
        (output / 'summary.json').write_text(text)
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps({key: report[key] for key in ('schema', 'status', 'wall_seconds', 'work')}))
        return report['status']


if 'config' in globals():
    if run(globals()['config']) != 'passed':
        raise RuntimeError('COVERAGE_UNVERIFIED: see bound workspace summary')

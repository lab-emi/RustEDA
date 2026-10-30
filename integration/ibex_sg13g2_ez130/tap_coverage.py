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

Fixture estimate: <2GiB based on merged source geometry (eight source regions),
derived domains/dopants, target/tie/intersection temporaries and the reader;
measured baseline peak0.9GiB. Hierarchy state<18MiB, anchors/bins<96MiB, one
worker. Caps are not a generic admission proof for native Boolean intermediates:
they allocate before output inspection. Caller MUST supply4GiB/1GiB and600s
guards and GNU peak RSS; native allocation/time failure means unverified.
Tiny controls use1GiB/60s. Python traversals have explicit count/work caps.
"""
from collections import deque
from decimal import Decimal
import fcntl
import hashlib
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


def certify_box(box, points, budget):
    """Sufficient certificate over the entire closed box, with bounded DFS."""
    pending = [(box, 0)]
    while pending:
        budget.charge('boxes')
        (lx, ly, ux, uy), depth = pending.pop()
        x, y = (lx + ux) // 2, (ly + uy) // 2
        distance = points.nearest(x, y)
        radius = max(x - lx, ux - x) + max(y - ly, uy - y)
        if distance + radius <= points.distance:
            continue
        if distance > points.distance:
            return dict(box=[lx, ly, ux, uy], center=[x, y],
                        nearest_l1_distance_dbu_at_least=distance)
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
    cells, edges, indegree = {}, {}, {}
    for cell in layout.each_cell():
        budget.charge('cells')
        key = cell.cell_index()
        cells[key], edges[key], indegree[key] = cell, [], 0
    for key, cell in cells.items():
        for instance in cell.each_inst():
            budget.charge('hierarchy_edges')
            if instance.is_complex():
                raise ValueError('COVERAGE_COMPLEX_TRANSFORM_UNSUPPORTED')
            child = instance.cell_index
            multiplicity = instance.na * instance.nb if instance.is_regular_array() else 1
            if child not in cells or not 1 <= multiplicity <= budget.limits['instance_visits']:
                raise ValueError('INVALID_COVERAGE_HIERARCHY')
            edges[key].append((child, multiplicity))
            indegree[child] += 1
    ready = deque(key for key, degree in indegree.items() if degree == 0)
    order = []
    while ready:
        key = ready.popleft()
        order.append(key)
        for child, _ in edges[key]:
            indegree[child] -= 1
            if not indegree[child]:
                ready.append(child)
    if len(order) != len(cells):
        raise ValueError('CYCLIC_COVERAGE_HIERARCHY')
    full, selected = {top.cell_index(): 1}, {top.cell_index(): 1}
    for key in order:
        for child, multiplier in edges[key]:
            for counts, stop in ((full, False), (selected, key in excluded)):
                if not stop and counts.get(key, 0):
                    value = counts.get(child, 0) + counts[key] * multiplier
                    if value > budget.limits['instance_visits']:
                        raise CoverageLimit('COVERAGE_BUDGET:instance_visits')
                    counts[child] = value
    total = sum(full.values())
    if total > budget.limits['instance_visits']:
        raise CoverageLimit('COVERAGE_BUDGET:instance_visits')
    budget.used['instance_visits'] = total
    return cells, full, selected


def inspect_region(region, budget):
    count = vertices = 0
    for poly in region.each():
        count += 1
        vertices += poly.num_points()
        budget.charge('shape_visits')
        budget.charge('vertex_visits', poly.num_points())
        if count > budget.limits['input_shapes'] or vertices > budget.limits['vertices']:
            raise CoverageLimit('COVERAGE_REGION_SIZE_BUDGET')
    return dict(polygons=count, vertices=vertices)


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
    layers = dict(active=(1, 0), psd=(14, 0), nsd=(7, 0), nsd_block=(7, 21),
                  nwell=(31, 0), pwell_block=(46, 21), pwell_draw=(46, 0), contact=(6, 0))
    indices = {name: layout.layer(*spec) for name, spec in layers.items()}
    for name, layer in indices.items():
        counts = full if name in ('nwell', 'pwell_block', 'pwell_draw') else selected
        estimated = sum(cell.shapes(layer).size() * counts.get(key, 0) for key, cell in cells.items())
        if estimated > budget.limits['input_shapes']:
            raise CoverageLimit('COVERAGE_INPUT_SHAPE_ESTIMATE:' + name)
    regions, inventory = {}, {}
    for name, layer in indices.items():
        region = pya.Region()
        iterator = top.begin_shapes_rec(layer)
        if name not in ('nwell', 'pwell_block', 'pwell_draw'):
            iterator.unselect_cells(list(excluded))
        while not iterator.at_end():
            shape = iterator.shape()
            if shape.is_box():
                poly = pya.Polygon(shape.box)
            elif shape.is_polygon():
                poly = shape.polygon
            elif shape.is_path():
                raise ValueError('COVERAGE_PATH_GEOMETRY_UNSUPPORTED')
            elif shape.is_text():
                iterator.next()
                continue
            else:
                raise ValueError('COVERAGE_SHAPE_CLASS_UNSUPPORTED')
            budget.charge('input_shapes')
            budget.charge('vertices', poly.num_points())
            if not poly.is_rectilinear():
                raise ValueError('COVERAGE_NONRECTILINEAR_GEOMETRY_UNSUPPORTED')
            transform = iterator.trans()
            if transform.is_complex():
                raise ValueError('COVERAGE_COMPLEX_TRANSFORM_UNSUPPORTED')
            # Unit orthogonal transforms are exactly representable by Trans;
            # do not round arbitrary-angle/scaled geometry through ICplxTrans.
            region.insert(poly.transformed(transform.s_trans()))
            iterator.next()
        iterator._destroy()
        region.min_coherence = True
        region.merge()
        inventory[name] = inspect_region(region, budget)
        regions[name] = region
    frame = pya.Region(top.bbox())
    pwell = (frame - regions['nwell'] - regions['pwell_block']) | regions['pwell_draw']
    nsd = (frame - regions['psd'] - regions['nsd_block']) | regions['nsd']
    p_active = regions['active'] & regions['psd']
    n_active = regions['active'] & nsd
    reports = {}
    for rule, domain, target, ties in (
            ('LU.a', regions['nwell'], p_active, n_active & regions['contact']),
            ('LU.b', pwell, n_active, p_active & regions['contact'])):
        domain.min_coherence = True
        domain.merge()
        domain_inventory = inspect_region(domain, budget)
        target, ties = target & domain, ties & domain
        inspect_region(target, budget)
        inspect_region(ties, budget)
        report = dict(status='passed', domains=0, target_polygons=0, tie_points=0,
                      uncertified_boxes=[], domain_inventory=domain_inventory)
        for poly in domain.each():
            budget.charge('domains')
            component = pya.Region(poly)
            targets = target & component
            if targets.is_empty():
                continue
            report['domains'] += 1
            anchors = []
            for anchor in (ties & component).each():
                # A polygon vertex is an actual contacted-tie point. Using
                # that point is stronger than using an arbitrary bbox center.
                point = next(anchor.each_point_hull())
                if len(anchors) >= budget.limits['anchors']:
                    raise CoverageLimit('COVERAGE_BUDGET:anchors')
                anchors.append((point.x, point.y))
            report['tie_points'] += len(anchors)
            points = TiePoints(anchors, distance, budget)
            for target_poly in targets.each():
                report['target_polygons'] += 1
                budget.charge('shape_visits')
                box = target_poly.bbox()
                failure = certify_box((box.left, box.bottom, box.right, box.top), points, budget)
                if failure is not None:
                    report['status'] = 'unverified'
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
                      excluded_cells=config['excluded_cells'], scope='nonexcluded_diffusion_contact_targets_full_well_domains',
                      excluded_macro_content_status='unchecked_requires_separate_macro_contract_and_full_lvs',
                      geometry_scope='rectilinear_polygons_unit_orthogonal_transforms_no_paths',
                      criterion='conservative_complete_box_L1_contacted_tie_certificate',
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

"""Coverage-certificate mathematics/budget controls, not physical signoff.

Estimated <64MiB, one worker, <=250 bounded random boxes, <=10k small exact
distance queries and <=80 tiny DAGs with <=4096 explicit oracle visits each.
Raw-record budget fixtures stream <=13 records. In-well corridor fixtures are
<=40 random rectilinear domains on grids of <=12x8 cells with half-unit oracles.

LayoutControls parses GDS: it writes about 300 control layouts and 120 random
hierarchical layouts (each under 20 kB) and runs the checker of 2026-11-05
(tests/fixtures/tap_coverage/checker_20261105.py), the same with merged domain
components, and the current checker on all of them in one KLayout batch process
(<1 GiB, about 15 s). It needs the pinned tool that tap_coverage_runner.py
takes as --tool: RUSTEDA_TEST_KLAYOUT=/path/to/multicall; without it the class
is skipped and the layout controls are unverified.
"""
from collections import Counter, deque
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from integration.ibex_sg13g2_ez130 import tap_coverage
from integration.ibex_sg13g2_ez130.tap_coverage import (
    Budget, CEILINGS, CoverageLimit, TiePoints, WellCorridor, certify_box, hierarchy, partition)

FIXTURES = Path(__file__).parent / 'fixtures' / 'tap_coverage'


def fixture_module(name):
    spec = importlib.util.spec_from_file_location('tap_coverage_fixture_' + name, FIXTURES / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REFERENCE = fixture_module('checker_20261105')  # the certificate as run in the 2026-11-05 campaign
LAYOUTS = fixture_module('layouts')


def budget(**changes):
    return Budget(dict(CEILINGS, **changes))


class CoverageMath(unittest.TestCase):
    def test_closed_box_uses_triangle_bound(self):
        work = budget()
        points = TiePoints([(0, 0)], 20, work)
        self.assertIsNone(certify_box((-5, -5, 5, 5), points, work))
        self.assertIsNotNone(certify_box((21, 0, 22, 1), points, work))

    def test_union_of_ties_can_certify_after_bounded_split(self):
        work = budget()
        points = TiePoints([(0, 0), (20, 0)], 10, work)
        self.assertIsNone(certify_box((0, 0, 20, 0), points, work))
        self.assertEqual(work.used['boxes'], 3)

    def test_continuous_gap_between_integer_coordinates_cannot_pass(self):
        # Both integer endpoints are covered, but x=0.5 is1.5 from either tie.
        # A subdivision at mid+1 would silently skip this open strip.
        work = budget()
        points = TiePoints([(-1, 0), (2, 0)], 1, work)
        self.assertIsNotNone(certify_box((0, 0, 1, 0), points, work))

    def test_every_pass_covers_dense_independent_half_dbu_grid(self):
        generator = random.Random(20261030)
        for number in range(250):
            lx, ly = generator.randint(-10, 0), generator.randint(-10, 0)
            ux, uy = generator.randint(0, 10), generator.randint(0, 10)
            anchors = [(generator.randint(-12, 12), generator.randint(-12, 12)) for _ in range(6)]
            distance = generator.randint(4, 20)
            work = budget()
            result = certify_box((lx, ly, ux, uy), TiePoints(anchors, distance, work), work)
            if result is None:
                for x in range(2 * lx, 2 * ux + 1):
                    for y in range(2 * ly, 2 * uy + 1):
                        self.assertLessEqual(min(abs(x - 2 * px) + abs(y - 2 * py)
                                                 for px, py in anchors), 2 * distance, number)

    def test_spatial_query_matches_exhaustive_distance_at_negative_boundaries(self):
        generator = random.Random(20261031)
        anchors = [(generator.randint(-100, 100), generator.randint(-100, 100)) for _ in range(50)]
        work = budget()
        points = TiePoints(anchors, 20, work)
        for x in (-61, -60, -59, -21, -20, -19, -1, 0, 1, 19, 20, 21, 59, 60, 61):
            for y in range(-60, 61, 5):
                expected = min(21, min(abs(x - px) + abs(y - py) for px, py in anchors))
                self.assertEqual(points.nearest(x, y), expected)

    def test_empty_tie_set_never_passes(self):
        work = budget()
        points = TiePoints([], 20, work)
        self.assertIsNotNone(certify_box((0, 0, 1, 1), points, work))

    def test_query_storage_work_and_pending_caps_fail_before_growth(self):
        with self.assertRaisesRegex(CoverageLimit, 'anchors'):
            TiePoints([(0, 0), (1, 0)], 10, budget(anchors=1))
        with self.assertRaisesRegex(CoverageLimit, 'bin_entries'):
            TiePoints([(0, 0), (1, 0)], 10, budget(bin_entries=1))
        for name in ('candidates', 'distance_checks'):
            work = budget(**{name: 1})
            points = TiePoints([(0, 0), (1, 0)], 10, work)
            with self.assertRaisesRegex(CoverageLimit, name):
                points.nearest(0, 0)
        for name, limit in (('boxes', 1), ('pending', 1), ('depth', 1)):
            work = budget(**{name: limit})
            points = TiePoints([(0, 0), (20, 0), (40, 0)], 10, work)
            with self.assertRaisesRegex(CoverageLimit, name):
                certify_box((0, 0, 40, 0), points, work)

    def test_limits_are_exact_positive_bounded_integers(self):
        for value in (True, 1.5, 0, -1, CEILINGS['boxes'] + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                budget(boxes=value)
        missing = copy.copy(CEILINGS)
        missing.pop('boxes')
        with self.assertRaises(ValueError):
            Budget(missing)


class EarlyExitQueries(unittest.TestCase):
    def test_reaches_and_candidates_match_exhaustive_distances(self):
        generator = random.Random(20261112)
        anchors = [(generator.randint(-100, 100), generator.randint(-100, 100)) for _ in range(60)]
        points = TiePoints(anchors, 20, budget())
        for _ in range(400):
            x, y = generator.randint(-90, 90), generator.randint(-90, 90)
            exact = sorted((abs(x - px) + abs(y - py), px, py) for px, py in anchors)
            for threshold in (-1, 0, 3, 11, 20):
                self.assertEqual(points.reaches(x, y, threshold), points.nearest(x, y) <= threshold)
                self.assertEqual(points.reaches(x, y, threshold), bool(exact) and exact[0][0] <= threshold)
            self.assertEqual(points.candidates(x, y), [row for row in exact if row[0] <= 20])

    def test_reaches_keeps_the_candidate_cap_of_nearest(self):
        points = TiePoints([(0, 0), (1, 0), (2, 0)], 10, budget(candidates=2))
        for query in (lambda: points.nearest(0, 0), lambda: points.reaches(0, 0, 5),
                      lambda: points.candidates(0, 0)):
            with self.assertRaisesRegex(CoverageLimit, 'candidates'):
                query()

    def test_certificate_results_and_box_counts_equal_the_reference_checker(self):
        generator = random.Random(20261113)
        outcomes = Counter()
        for number in range(250):
            anchors = [(generator.randint(-30, 30), generator.randint(-30, 30)) for _ in range(generator.randint(0, 12))]
            distance = generator.randint(3, 40)
            lx, ly = generator.randint(-25, 5), generator.randint(-25, 5)
            box = (lx, ly, lx + generator.randint(0, 24), ly + generator.randint(0, 24))
            results = []
            for module in (tap_coverage, REFERENCE):
                work = module.Budget(dict(CEILINGS))
                try:
                    result = module.certify_box(box, module.TiePoints(anchors, distance, work), work)
                except ValueError as error:
                    result = str(error)
                results.append((result, work.used['boxes']))
            self.assertEqual(results[0], results[1], number)
            outcomes['accepted' if results[0][0] is None else 'refused'] += 1
        self.assertGreater(min(outcomes['accepted'], outcomes['refused']), 40)

    def test_more_tie_points_never_refuse_an_accepted_box(self):
        generator = random.Random(20261117)
        accepted = 0
        for number in range(300):
            anchors = [(generator.randint(-30, 30), generator.randint(-30, 30)) for _ in range(generator.randint(1, 8))]
            more = anchors + [(generator.randint(-30, 30), generator.randint(-30, 30)) for _ in range(generator.randint(1, 6))]
            distance = generator.randint(3, 40)
            lx, ly = generator.randint(-25, 5), generator.randint(-25, 5)
            box = (lx, ly, lx + generator.randint(0, 24), ly + generator.randint(0, 24))
            for module in (tap_coverage, REFERENCE):
                work = module.Budget(dict(CEILINGS))
                if module.certify_box(box, module.TiePoints(anchors, distance, work), work) is None:
                    accepted += 1
                    self.assertIsNone(module.certify_box(box, module.TiePoints(more, distance, work), work), number)
        self.assertGreater(accepted, 100)

    def test_reference_fixture_is_the_recorded_checker(self):
        record = json.loads((FIXTURES / 'provenance.json').read_text())['files']['checker_20261105.py']
        data = (FIXTURES / 'checker_20261105.py').read_bytes()
        self.assertEqual((hashlib.sha256(data).hexdigest(), len(data)), (record['sha256'], record['bytes']))


def grid_domain(generator, width, height):
    """A random connected set of unit cells (closed union is the domain)."""
    cells = {(generator.randrange(width), generator.randrange(height))}
    for _ in range(generator.randint(width * height // 3, width * height * 2 // 3)):
        x, y = generator.choice(sorted(cells))
        dx, dy = generator.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
        if 0 <= x + dx < width and 0 <= y + dy < height:
            cells.add((x + dx, y + dy))
    return cells


def corridor_inputs(cells, scale):
    """Frame, row/column hole rectangles (1 past the frame edge), inside test and reflex corners."""
    xs, ys = [x for x, _ in cells], [y for _, y in cells]
    fl, fb, fr, ft = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    frame = (fl * scale, fb * scale, fr * scale, ft * scale)
    holes = {(x, y) for x in range(fl, fr) for y in range(fb, ft)} - cells

    def runs(primary, secondary, key):
        rectangles = []
        for b in secondary:
            a = primary[0]
            while a < primary[-1] + 1:
                if key(a, b) in holes:
                    start = a
                    while a < primary[-1] + 1 and key(a, b) in holes:
                        a += 1
                    rectangles.append((start, a, b))
                else:
                    a += 1
        return rectangles

    def extend(l, b, r, t):
        l, b, r, t = l * scale, b * scale, r * scale, t * scale
        return (l - (l == frame[0]), b - (b == frame[1]), r + (r == frame[2]), t + (t == frame[3]))

    rows = [extend(a0, b, a1, b + 1) for a0, a1, b in runs(range(fl, fr), range(fb, ft), lambda a, b: (a, b))]
    columns = [extend(b, a0, b + 1, a1) for a0, a1, b in runs(range(fb, ft), range(fl, fr), lambda a, b: (b, a))]

    def inside(x, y):  # closed union of the domain's cells
        return any((cx, cy) in cells for cx in (x // scale, (x - 1) // scale) for cy in (y // scale, (y - 1) // scale)
                   if cx * scale <= x <= cx * scale + scale and cy * scale <= y <= cy * scale + scale)

    corners = [(x, y) for l, b, r, t in rows for x in (l, r) for y in (b, t) if inside(x, y)]
    return frame, rows, columns, inside, corners


def half_points(box):
    lx, ly, ux, uy = box
    return [(x / 2, y / 2) for x in range(2 * lx, 2 * ux + 1) for y in range(2 * ly, 2 * uy + 1)]


def in_closed_cells(cells, scale, x, y):
    return any((cx, cy) in cells and cx * scale <= x <= cx * scale + scale and cy * scale <= y <= cy * scale + scale
               for cx in (int(x // scale), int(x // scale) - 1) for cy in (int(y // scale), int(y // scale) - 1))


def geodesic(cells, scale, sources, frame):
    """In-domain L1 distance on the half-unit lattice from every source (exact for integer polygons)."""
    distance, queue = {}, deque()
    for x, y in sources:
        distance[(2 * x, 2 * y)] = 0
        queue.append((2 * x, 2 * y))
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in distance or not (2 * frame[0] <= nx <= 2 * frame[2] and 2 * frame[1] <= ny <= 2 * frame[3]):
                continue
            mid = ((x + nx) / 4, (y + ny) / 4)
            if in_closed_cells(cells, scale, nx / 2, ny / 2) and in_closed_cells(cells, scale, *mid):
                distance[(nx, ny)] = distance[(x, y)] + 1
                queue.append((nx, ny))
    return distance  # in half units


class InWellCorridor(unittest.TestCase):
    def test_contains_matches_closed_domain_on_half_unit_grid(self):
        generator = random.Random(20261114)
        for number in range(40):
            cells = grid_domain(generator, generator.randint(3, 12), generator.randint(2, 8))
            frame, rows, columns, inside, corners = corridor_inputs(cells, 4)
            corridor = WellCorridor(frame, rows, columns, inside, corners, TiePoints([], 20, budget()), budget())
            for _ in range(60):
                lx = generator.randint(frame[0], frame[2]); ux = generator.randint(lx, min(frame[2], lx + 12))
                ly = generator.randint(frame[1], frame[3]); uy = generator.randint(ly, min(frame[3], ly + 12))
                for box in ((lx, ly, ux, uy), (lx, ly, lx, uy), (lx, ly, ux, ly), (lx, ly, lx, ly)):
                    expected = all(in_closed_cells(cells, 4, x, y) for x, y in half_points(box))
                    self.assertEqual(corridor.contains(*box), expected, (number, box))

    def test_every_accepted_box_has_an_in_domain_path_within_the_limit(self):
        generator = random.Random(20261115)
        accepted = refused = 0
        for number in range(30):
            cells = grid_domain(generator, generator.randint(4, 12), generator.randint(3, 8))
            frame, rows, columns, inside, corners = corridor_inputs(cells, 4)
            ties = [(x * 4 + generator.randint(0, 4), y * 4 + generator.randint(0, 4))
                    for x, y in generator.sample(sorted(cells), min(2, len(cells)))]
            limit = generator.randint(8, 24)
            work = budget()
            points = TiePoints(ties, limit, work)
            corridor = WellCorridor(frame, rows, columns, inside, corners, points, work)
            reach = geodesic(cells, 4, ties, frame)
            for cx, cy in generator.sample(sorted(cells), min(6, len(cells))):
                box = (cx * 4, cy * 4, cx * 4 + generator.randint(0, 4), cy * 4 + generator.randint(0, 4))
                try:
                    result = certify_box(box, points, work, corridor)
                except CoverageLimit:
                    continue
                if result is None:
                    accepted += 1
                    straight = certify_box(box, points, budget())
                    self.assertIsNone(straight, (number, box))  # in-well acceptance implies straight-line
                    for x, y in half_points(box):
                        self.assertLessEqual(reach.get((int(2 * x), int(2 * y)), 10 ** 9), 2 * limit, (number, box, x, y))
                else:
                    refused += 1
        self.assertGreater(accepted, 20)
        self.assertGreater(refused, 5)

    def test_path_around_a_notch_is_found_through_reflex_corners(self):
        # 30x4 strip with a 3 wide, 2 deep notch from the top between tie and target.
        cells = {(x, y) for x in range(30) for y in range(4)} - {(x, y) for x in range(12, 15) for y in range(2, 4)}
        frame, rows, columns, inside, corners = corridor_inputs(cells, 1)
        work = budget()
        points = TiePoints([(9, 3)], 20, work)
        corridor = WellCorridor(frame, rows, columns, inside, corners, points, work)
        self.assertFalse(corridor.contains(9, 3, 17, 4))
        self.assertIsNone(certify_box((16, 3, 17, 4), points, work, corridor))
        tight = TiePoints([(9, 3)], 9, budget())  # the straight distance (9) fits, the in-well one (11) does not
        self.assertIsNone(certify_box((17, 3, 17, 3), tight, budget()))
        corridor = WellCorridor(frame, rows, columns, inside, corners, tight, budget())
        self.assertIsNotNone(certify_box((17, 3, 17, 3), tight, budget(), corridor))

    def test_clear_box_needs_no_in_well_pass(self):
        cells = {(x, y) for x in range(60) for y in range(4)} - {(x, 3) for x in range(40, 43)}
        frame, rows, columns, inside, corners = corridor_inputs(cells, 1)
        corridor = WellCorridor(frame, rows, columns, inside, corners, TiePoints([(1, 1)], 20, budget()), budget())
        self.assertTrue(corridor.clear((2, 0, 3, 4)))
        self.assertFalse(corridor.clear((21, 0, 22, 4)))


class DomainPartition(unittest.TestCase):
    def test_each_polygon_joins_every_frame_its_bounding_box_meets(self):
        generator = random.Random(20261116)
        frames = [(x, y, x + generator.randint(0, 50), y + generator.randint(0, 50))
                  for x, y in ((generator.randint(0, 200), generator.randint(0, 200)) for _ in range(25))]
        polygons = []
        for _ in range(300):
            x, y = generator.randint(-10, 260), generator.randint(-10, 260)
            box = SimpleNamespace(left=x, bottom=y, right=x + generator.randint(0, 30), top=y + generator.randint(0, 30))
            polygons.append(SimpleNamespace(bbox=lambda box=box: box))
        groups = partition(SimpleNamespace(each=lambda: iter(polygons)), frames, 20, budget())
        for number, (fl, fb, fr, ft) in enumerate(frames):
            expected = [p for p in polygons if fl <= p.bbox().right and p.bbox().left <= fr
                        and fb <= p.bbox().top and p.bbox().bottom <= ft]
            self.assertEqual(groups[number], expected)


MERGE_MARKERS = (
    ('            targets = target & component\n', '            targets = merged_components(pya, target & component, True)\n'),
    ('            for anchor in (ties & component).each():\n',
     '            for anchor in merged_components(pya, ties & component, False).each():\n'),
    ('def geometry_check(config, budget):\n',
     'def merged_components(pya, region, with_corner_parts):\n'
     '    merged = pya.Region(list(region.each()))\n'
     '    merged.min_coherence = False\n'
     '    merged.merge()\n'
     '    parts = pya.Region(list(merged.each()))\n'
     '    parts.min_coherence = True\n'
     '    parts.merge()\n'
     '    if not with_corner_parts or parts.count() == merged.count():\n'
     '        return merged\n'
     '    extra = {str(part.bbox()): part for part in parts.each()}\n'
     '    for polygon in merged.each():\n'
     '        extra.pop(str(polygon.bbox()), None)\n'
     '    both = pya.Region(list(merged.each()) + list(extra.values()))\n'
     '    both.merged_semantics = False\n'
     '    return both\n\n\n'
     'def geometry_check(config, budget):\n'))


def calls(row, mode='straight'):
    """The certify_box calls of one run as a multiset of (box, tie points, accepted)."""
    return Counter((tuple(call[:4]), tuple(map(tuple, row['sets'][call[4]])), call[5]) for call in row['calls'][mode])


def verdict(row):
    return row['diagnostic'] or row['status']


class LayoutControls(unittest.TestCase):
    """The checker of 2026-11-05 and the current one on the same layouts, call by call.

    `reference` is the fixture as recorded. `merged` is the fixture with one change, the only
    intended difference of the current checker's straight-line certificate: each domain's targets
    and ties are merged again before use, pieces that touch at a corner are joined, and a target
    joined that way is certified as the box of the whole and as the boxes of its edge-connected
    parts. `current` must list the same calls as `merged` on every layout. The reference certifies
    either the parts or the whole of such a target (which one depends on whether an operand of its
    Booleans happens to be a single rectangle) against at least the same tie points, so `current`
    examines every box that `reference` examines and never passes where `reference` does not.
    """
    RANDOM_SEEDS = range(20261112, 20261112 + 120)
    # joined at a corner: the larger box is certified too, stricter by design
    CORNER_CONTROLS = {'targets_corner_touch', 'corner_pair_on_limit', 'corner_pair_on_limit_one_psd',
                       'corner_touch_joined_by_old', 'corner_pair_on_limit_joined_by_old'}
    IN_WELL_CONTROLS = {'inwell_u_far', 'inwell_comb'}  # straight line passes, no path inside the well

    @classmethod
    def setUpClass(cls):
        tool = os.environ.get('RUSTEDA_TEST_KLAYOUT')
        if not tool or not Path(tool).is_file():
            raise unittest.SkipTest('pinned KLayout tool unavailable (RUSTEDA_TEST_KLAYOUT); layout controls unverified')
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        merged = (FIXTURES / 'checker_20261105.py').read_text()
        for old, new in MERGE_MARKERS:
            assert merged.count(old) == 1, old
            merged = merged.replace(old, new)
        (root / 'merged.py').write_text(merged)
        cases = []

        def add(name, cells, excluded):
            path = root / (name + '.gds')
            path.write_bytes(LAYOUTS.gds_bytes(cells))
            cases.append(dict(name=name, gds=str(path), top=LAYOUTS.TOP, excluded={LAYOUTS.MACRO: 2} if excluded else {}))

        for name, spec in LAYOUTS.CONTROLS.items():
            add(name, LAYOUTS.layout(name), spec['excluded'])
            if not name.startswith('empty_'):  # a second domain per rule would make an empty layout non-empty
                add(name + '+far', LAYOUTS.layout(name, far=True), spec['excluded'])
        for seed in cls.RANDOM_SEEDS:
            cells, macro = LAYOUTS.random_layout(seed)
            add('random-%d' % seed, cells, macro)
            if macro and seed % 2:
                cases.append(dict(cases[-1], name='random-%d-all' % seed, excluded={}))
        (root / 'cases.json').write_text(json.dumps(cases))
        sources = dict(reference=FIXTURES / 'checker_20261105.py', merged=root / 'merged.py',
                       current=Path(tap_coverage.__file__).resolve())
        subprocess.run([tool, 'klayout', '-b', '-r', str(FIXTURES / 'driver.py'), '-rd', 'cases=%s' % (root / 'cases.json'),
                        '-rd', 'out=%s' % (root / 'out.json'),
                        '-rd', 'srcs=' + ','.join('%s=%s' % item for item in sources.items())],
                       check=True, stdin=subprocess.DEVNULL, timeout=600)
        cls.rows = {row['name']: row for row in json.loads((root / 'out.json').read_text())}
        cls.controls = {name: LAYOUTS.CONTROLS[name.split('+')[0]] for name in cls.rows if not name.startswith('random-')}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_control_has_its_expected_verdict_from_both_checkers(self):
        self.assertGreater(len(self.controls), 280)
        for name, spec in self.controls.items():
            with self.subTest(control=name):
                self.assertEqual(verdict(self.rows[name]['current']), spec['expect'], spec['note'])
                self.assertEqual(verdict(self.rows[name]['reference']),
                                 spec['expect_old_far' if name.endswith('+far') else 'expect_old'], spec['note'])

    def test_current_checker_never_passes_a_layout_that_the_reference_fails(self):
        passed = 0
        for name, row in self.rows.items():
            with self.subTest(layout=name):
                if row['current']['status'] == 'passed':
                    passed += 1
                    self.assertEqual(row['reference']['status'], 'passed')
                    self.assertEqual(row['merged']['status'], 'passed')
        self.assertGreater(passed, 80)

    def test_straight_line_calls_equal_the_reference_with_merged_components(self):
        """Same boxes against the same tie points: every geometry shortcut is the same point sets."""
        compared = 0
        for name, row in self.rows.items():
            with self.subTest(layout=name):
                self.assertEqual(row['current']['diagnostic'], row['merged']['diagnostic'])
                self.assertEqual(calls(row['current']), calls(row['merged']))
                self.assertEqual({rule: (entry['domains'], entry['ties']) for rule, entry in row['current']['rules'].items()},
                                 {rule: (entry['domains'], entry['ties']) for rule, entry in row['merged']['rules'].items()})
                compared += len(row['current']['calls']['straight'])
        self.assertGreater(compared, 5000)

    def test_controls_without_corner_contacts_equal_the_recorded_reference(self):
        for name in self.controls:
            if name.split('+')[0] not in self.CORNER_CONTROLS:
                with self.subTest(control=name):
                    self.assertEqual(calls(self.rows[name]['current']), calls(self.rows[name]['reference']))
                    self.assertEqual(self.rows[name]['current']['diagnostic'], self.rows[name]['reference']['diagnostic'])

    def test_every_reference_box_is_examined_against_a_subset_of_its_tie_points(self):
        """Where the calls differ from the recorded reference, the current checker still examines
        every box of the reference, each against a subset of the reference's tie points. More tie
        points never turn an accepted box into a refused one (EarlyExitQueries), so a layout that
        the current checker accepts is accepted by the reference: stricter, never looser."""
        differing = 0
        for name, row in self.rows.items():
            reference, current = calls(row['reference']), calls(row['current'])
            if reference == current:
                continue
            differing += 1
            with self.subTest(layout=name):
                by_box = {}
                for box, points, _ in current:
                    by_box.setdefault(box, []).append(set(points))
                for box, points, _ in reference:
                    self.assertTrue(any(other <= set(points) for other in by_box.get(box, ())), box)
        self.assertGreater(differing, 5)  # the random layouts do contain corner contacts

    def test_corner_pair_on_the_limit_is_refused_like_its_parts(self):
        """The whole is accepted and each 1 nm part is refused; the reference examines the parts, or
        the whole where its Boolean with a non-rectangular well joins them. The current checker does both."""
        whole, parts = (10401, 1399, 10403, 1401), {(10401, 1400, 10402, 1401), (10402, 1399, 10403, 1400)}
        for name, reference_boxes in (('corner_pair_on_limit', parts), ('corner_pair_on_limit+far', parts),
                                      ('corner_pair_on_limit_one_psd', parts), ('corner_pair_on_limit_one_psd+far', parts),
                                      ('corner_pair_on_limit_joined_by_old', {whole}),
                                      ('corner_pair_on_limit_joined_by_old+far', parts)):
            with self.subTest(control=name):
                row = self.rows[name]
                outcome = {tuple(call[:4]): call[5] for call in row['current']['calls']['straight']}
                self.assertTrue(outcome[whole])
                self.assertEqual({outcome[part] for part in parts}, {False})
                reference = {tuple(call[:4]) for call in row['reference']['calls']['straight']}
                self.assertEqual(reference & (parts | {whole}), reference_boxes)

    def test_in_well_controls_fail_only_the_in_well_certificate(self):
        for name in self.controls:
            row = self.rows[name]['current']
            if name.split('+')[0] in self.IN_WELL_CONTROLS:
                with self.subTest(control=name):
                    self.assertEqual(self.rows[name]['reference']['status'], 'passed')
                    self.assertEqual(row['status'], 'unverified')
                    self.assertTrue(all(call[5] for call in row['calls']['straight']))
                    self.assertTrue(any(not call[5] for call in row['calls']['inwell']))
                    self.assertEqual(row['rules']['LU.b']['status'], 'passed')
            elif name.split('+')[0] in ('inwell_u_near', 'inwell_notch'):
                with self.subTest(control=name):
                    self.assertEqual(row['status'], 'passed')
                    self.assertTrue(row['calls']['inwell'] and all(call[5] for call in row['calls']['inwell']))

    def test_tie_point_is_the_lower_left_vertex_of_the_merged_contacted_tie(self):
        for name, point, absent in (('contact_partly_limit', (1000, 1400), (920, 1400)),
                                    ('contact_on_butted_junction', (2000, 1400), (1920, 1400)),
                                    ('contact_on_well_edge', (1400, 0), (1400, -80)),
                                    ('overlap_shifted_ties', (1400, 1400), (1480, 1400)),
                                    ('tie_across_cell_seam', (1420, 1400), (1500, 1400)),
                                    ('property_contact_merge', (1400, 1400), (1480, 1400))):
            for variant in (name, name + '+far'):
                with self.subTest(control=variant):
                    points = {tuple(p) for group in self.rows[variant]['current']['sets'].values() for p in group}
                    self.assertIn(point, points)
                    self.assertNotIn(absent, points)

    def test_random_layouts_exercise_passes_failures_and_refusals(self):
        rows = [row for name, row in self.rows.items() if name.startswith('random-')]
        self.assertGreater(sum(row['reference']['status'] == 'passed' for row in rows), 15)
        self.assertGreater(sum(row['reference']['status'] != 'passed' and not row['reference']['diagnostic'] for row in rows), 40)
        self.assertGreater(sum(bool(row['reference']['diagnostic']) for row in rows), 2)
        self.assertGreater(sum(bool(row['current']['calls']['inwell']) for row in rows), 5)


class FakeCell:
    def __init__(self, key, children):
        self.key, self.children = key, children

    def cell_index(self):
        return self.key

    def each_inst(self):
        children = self.children() if callable(self.children) else self.children
        for value in children:
            if isinstance(value, tuple):
                child, multiplier = value
                yield SimpleNamespace(cell_index=child, na=multiplier, nb=1,
                                      is_regular_array=lambda: True, is_complex=lambda: False)
            else:
                yield value


def instance(child, na=1, nb=1, regular=False, complex_transform=False, inspected=None):
    def is_complex():
        if inspected is not None:
            inspected.append(child)
        return complex_transform
    return SimpleNamespace(cell_index=child, na=na, nb=nb,
                           is_regular_array=lambda: regular, is_complex=is_complex)


def explicit_occurrences(graph, excluded, visit_cap=4096):
    """Independent bounded expansion oracle, used only for tiny acyclic graphs."""
    if not 1 <= len(graph) <= 8 or sum(len(edges) for edges in graph.values()) > 32 or any(
            child not in graph or child <= parent or not 1 <= multiplier <= 8
            for parent, edges in graph.items() for child, multiplier in edges):
        raise ValueError('TINY_ORACLE_DAG_INPUT')
    pending, full, selected, visited = [(0, True)], {}, {}, 0
    while pending:
        key, eligible = pending.pop()
        visited += 1
        if visited > visit_cap:
            raise ValueError('TINY_ORACLE_VISIT_BUDGET')
        full[key] = full.get(key, 0) + 1
        if eligible:
            selected[key] = selected.get(key, 0) + 1
        for child, multiplier in graph[key]:
            if len(pending) + multiplier > visit_cap:
                raise ValueError('TINY_ORACLE_PENDING_BUDGET')
            pending.extend((child, eligible and key not in excluded) for _ in range(multiplier))
    return full, selected


class HierarchyBudgets(unittest.TestCase):
    def layout(self, graph):
        cells = [FakeCell(key, children) for key, children in graph.items()]
        return SimpleNamespace(each_cell=lambda: iter(cells)), cells[0]

    def test_cycle_guard_and_multiplicity_dp_without_path_enumeration(self):
        layout, top = self.layout({0: [(1, 2), (2, 3)], 1: [(3, 4)], 2: [(3, 5)], 3: []})
        _, full, selected = hierarchy(layout, top, {1}, budget())
        self.assertEqual(full, {0: 1, 1: 2, 2: 3, 3: 23})
        self.assertEqual(selected, {0: 1, 1: 2, 2: 3, 3: 15})
        layout, top = self.layout({0: [(1, 1)], 1: [(0, 1)]})
        with self.assertRaisesRegex(ValueError, 'CYCLIC'):
            hierarchy(layout, top, {}, budget())

    def test_hierarchy_object_and_exponential_visit_budgets(self):
        graph = {0: [(1, 100)], 1: [(2, 100)], 2: [(3, 100)], 3: []}
        for changes, diagnostic in (({'cells': 1}, 'cells'), ({'hierarchy_edges': 1}, 'hierarchy_edges'),
                                     ({'instance_visits': 10000}, 'instance_visits')):
            layout, top = self.layout(graph)
            with self.subTest(changes=changes), self.assertRaisesRegex(CoverageLimit, diagnostic):
                hierarchy(layout, top, {}, budget(**changes))

    def test_complex_instance_is_rejected_before_recursive_geometry(self):
        top = FakeCell(0, [])
        top.each_inst = lambda: iter([SimpleNamespace(is_complex=lambda: True)])
        layout = SimpleNamespace(each_cell=lambda: iter([top]))
        with self.assertRaisesRegex(ValueError, 'COMPLEX_TRANSFORM_UNSUPPORTED'):
            hierarchy(layout, top, {}, budget())

    def test_duplicate_dependencies_preserve_arrays_and_inspect_every_transform(self):
        inspected = []
        graph = {0: [instance(1, inspected=inspected),
                     instance(2, inspected=inspected),
                     instance(1, na=2, nb=3, regular=True, inspected=inspected),
                     instance(1, inspected=inspected)], 1: [], 2: []}
        layout, top = self.layout(graph)
        work = budget()
        _, full, selected = hierarchy(layout, top, {}, work)
        self.assertEqual(full, {0: 1, 1: 8, 2: 1})
        self.assertEqual(selected, full)
        self.assertEqual(inspected, [1, 2, 1, 1])
        self.assertEqual(work.used['hierarchy_edges'], 2)
        self.assertEqual(work.used['hierarchy_instance_records'], 4)
        self.assertEqual(work.used['instance_visits'], 10)

    def test_duplicate_dependency_dp_matches_independent_bounded_expansion(self):
        generator = random.Random(20261031)
        for number in range(80):
            graph = {key: [(generator.randrange(key + 1, 6), generator.randint(1, 2))
                           for _ in range(2)] if key < 5 else [] for key in range(6)}
            excluded = {key for key in range(6) if generator.randrange(4) == 0}
            expected_full, expected_selected = explicit_occurrences(graph, excluded)
            layout, top = self.layout(graph)
            work = budget()
            _, full, selected = hierarchy(layout, top, excluded, work)
            self.assertEqual(full, expected_full, number)
            self.assertEqual(selected, expected_selected, number)
            pairs = {(parent, child) for parent, children in graph.items() for child, _ in children}
            self.assertEqual(work.used['hierarchy_edges'], len(pairs), number)
            self.assertEqual(work.used['hierarchy_instance_records'], 10, number)
            self.assertEqual(work.used['instance_visits'], sum(expected_full.values()), number)

    def test_macro_exclusion_stops_only_its_selected_paths(self):
        graph = {0: [(1, 2), (1, 1), (2, 3)], 1: [(3, 2), (3, 1)],
                 2: [(3, 4)], 3: [(4, 2)], 4: []}
        expected = explicit_occurrences(graph, {1})
        layout, top = self.layout(graph)
        _, full, selected = hierarchy(layout, top, {1}, budget())
        self.assertEqual((full, selected), expected)
        self.assertEqual(full[3], 21)
        self.assertEqual(selected[3], 12)
        self.assertEqual(full[4], 42)
        self.assertEqual(selected[4], 24)

    def test_self_parallel_and_unreachable_cycles_are_all_rejected(self):
        for graph in ({0: [(0, 1), (0, 2)]},
                      {0: [(1, 1), (1, 2)], 1: [(0, 1)]},
                      {0: [], 1: [(2, 1), (2, 2)], 2: [(1, 1)]}):
            layout, top = self.layout(graph)
            with self.subTest(graph=graph), self.assertRaisesRegex(ValueError, 'CYCLIC'):
                hierarchy(layout, top, {1}, budget())

    def test_unreachable_and_excluded_raw_records_are_still_validated(self):
        for reachable in (False, True):
            for bad, diagnostic in ((instance(2, complex_transform=True), 'COMPLEX_TRANSFORM'),
                                    (instance(99), 'INVALID_COVERAGE_HIERARCHY'),
                                    (instance(2, na=0, nb=2, regular=True), 'INVALID_COVERAGE_HIERARCHY')):
                graph = {0: [(1, 1)] if reachable else [], 1: [instance(2), bad], 2: []}
                layout, top = self.layout(graph)
                with self.subTest(reachable=reachable, diagnostic=diagnostic), self.assertRaisesRegex(ValueError, diagnostic):
                    hierarchy(layout, top, {1}, budget())

    def test_unique_edge_limit_ignores_duplicate_storage_but_rejects_new_pair(self):
        graph = {0: [(1, 1), (2, 1), (1, 2), (3, 1)], 1: [], 2: [], 3: [], 4: []}
        layout, top = self.layout(graph)
        work = budget(hierarchy_edges=3)
        hierarchy(layout, top, {}, work)
        self.assertEqual(work.used['hierarchy_edges'], 3)
        self.assertEqual(work.used['hierarchy_instance_records'], 4)
        graph[0].append((4, 1))
        layout, top = self.layout(graph)
        work = budget(hierarchy_edges=3)
        with self.assertRaisesRegex(CoverageLimit, 'hierarchy_edges'):
            hierarchy(layout, top, {}, work)
        self.assertEqual(work.used['hierarchy_edges'], 3)
        self.assertEqual(work.used['hierarchy_instance_records'], 5)

    def test_raw_scan_limit_is_separate_and_streamed_with_small_limits(self):
        for records, rejected in ((12, False), (13, True)):
            graph = {0: [], 1: lambda: (instance(2) for _ in range(records)), 2: []}
            layout, top = self.layout(graph)
            work = budget(hierarchy_edges=1, instance_visits=12)
            if rejected:
                with self.assertRaisesRegex(CoverageLimit, 'hierarchy_instance_records'):
                    hierarchy(layout, top, {}, work)
            else:
                _, full, _ = hierarchy(layout, top, {}, work)
                self.assertEqual(full, {0: 1})
            self.assertEqual(work.used['hierarchy_instance_records'], 12)
            self.assertEqual(work.used['hierarchy_edges'], 1)

    def test_unreachable_aggregate_can_exceed_expanded_cap_without_losing_precision(self):
        graph = {0: [], 1: [(2, 8), (2, 8)], 2: []}
        layout, top = self.layout(graph)
        work = budget(instance_visits=8)
        _, full, selected = hierarchy(layout, top, {}, work)
        self.assertEqual((full, selected), ({0: 1}, {0: 1}))
        self.assertEqual(work.used['hierarchy_instance_records'], 2)
        self.assertEqual(work.used['hierarchy_edges'], 1)
        graph[0] = [(1, 1)]
        layout, top = self.layout(graph)
        with self.assertRaisesRegex(CoverageLimit, 'instance_visits'):
            hierarchy(layout, top, {}, budget(instance_visits=8))

    def test_expanded_total_limit_includes_root_and_each_individually_small_child(self):
        layout, top = self.layout({0: [(1, 3), (2, 3)], 1: [], 2: []})
        with self.assertRaisesRegex(CoverageLimit, 'instance_visits'):
            hierarchy(layout, top, {}, budget(instance_visits=6))

    def test_empty_dependencies_report_zero_scan_and_edge_counts(self):
        layout, top = self.layout({0: []})
        work = budget()
        _, full, selected = hierarchy(layout, top, {}, work)
        self.assertEqual((full, selected), ({0: 1}, {0: 1}))
        self.assertEqual(work.used['hierarchy_edges'], 0)
        self.assertEqual(work.used['hierarchy_instance_records'], 0)
        self.assertEqual(work.used['instance_visits'], 1)

    def test_tiny_expansion_oracle_has_its_own_budgets(self):
        with self.assertRaisesRegex(ValueError, 'TINY_ORACLE_PENDING_BUDGET'):
            explicit_occurrences({0: [(1, 3)], 1: []}, {}, visit_cap=2)
        with self.assertRaisesRegex(ValueError, 'TINY_ORACLE_DAG_INPUT'):
            explicit_occurrences({0: [(0, 1)]}, {})


if __name__ == '__main__':
    unittest.main()

"""Coverage-certificate mathematics/budget controls, not physical signoff.

Estimated <64MiB, one worker, <=250 bounded random boxes and <=10k small exact
distance queries. No GDS is parsed here; KLayout controls are separate.
"""
import copy
import random
from types import SimpleNamespace
import unittest

from integration.ibex_sg13g2_ez130.tap_coverage import (
    Budget, CEILINGS, CoverageLimit, TiePoints, certify_box, hierarchy)


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


class FakeCell:
    def __init__(self, key, children):
        self.key, self.children = key, children

    def cell_index(self):
        return self.key

    def each_inst(self):
        for child, multiplier in self.children:
            yield SimpleNamespace(cell_index=child, na=multiplier, nb=1,
                                  is_regular_array=lambda: True, is_complex=lambda: False)


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


if __name__ == '__main__':
    unittest.main()

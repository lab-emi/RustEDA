"""Coverage-certificate mathematics/budget controls, not physical signoff.

Estimated <64MiB, one worker, <=250 bounded random boxes, <=10k small exact
distance queries and <=80 tiny DAGs with <=4096 explicit oracle visits each.
Raw-record budget fixtures stream <=13 records. No GDS is parsed here;
KLayout controls are separate.
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

"""Pins that tie the native recipe's files to the code that checks them; metadata only.

Estimate<64MiB: two small JSON files, one tiny backend fixture, one worker. No reference tool,
KLayout or RustROAD is run. Each control fails when a recipe file changes without the code that
pins it (or the reverse), before any producer run reaches that code.
"""
import hashlib
import json
from pathlib import Path
import unittest

from integration.ibex_host_benchmark import signoff_layout
from integration.ibex_sg13g2_ez130 import native_backend, native_flow, qualify_native as q, tap_coverage
from tests import test_ihp_antenna_session as legacy

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'integration/ibex_sg13g2_ez130'
T1 = ROOT / 'tests/fixtures/foundry_first_t1'
GAP = '"native_tap_max_gap_um": '


class ContractPins(unittest.TestCase):
    def test_qualifier_pins_the_recipe_contract(self):
        # nf_qualify (calibration_evidence) accepts the common native contract only by this pin.
        raw = (RECIPE / 'contract.json').read_bytes()
        self.assertEqual((q.CONTRACT_SHA256, q.CONTRACT_BYTES), (hashlib.sha256(raw).hexdigest(), len(raw)))

    def test_t1_fixture_is_the_recipe_contract_with_the_t1_gap(self):
        # The joint tap proof's frozen T1 contract differs from the recipe's contract only in the
        # native clear gap (30 um); every other byte, the RC calibration included, is shared.
        recipe = (RECIPE / 'contract.json').read_text().splitlines()
        t1 = (T1 / 'contract.json').read_text().splitlines()
        self.assertEqual(len(recipe), len(t1))
        differ = [(a, b) for a, b in zip(recipe, t1) if a != b]
        self.assertEqual(len(differ), 1)
        self.assertEqual(differ[0][1].strip(), GAP + '30,')
        self.assertTrue(differ[0][0].strip().startswith(GAP))
        native = json.loads((RECIPE / 'contract.json').read_text())['common']['native_tap_max_gap_um']
        self.assertNotEqual(native, 30)
        raw = (T1 / 'contract.json').read_bytes()
        self.assertEqual((hashlib.sha256(raw).hexdigest(), len(raw)), native_flow.TAP_CONTRACT)
        self.assertEqual(native_flow.TAP_CONTRACT, (q.TAP_CONTRACT_SHA256, q.CONTRACT_BYTES))

    def test_recipe_contract_declares_the_physical_lattice(self):
        native = json.loads((RECIPE / 'contract.json').read_text())['common']
        physical = json.loads((RECIPE / 'native-physical.json').read_text())['tap']
        self.assertEqual(native['native_tap_max_gap_um'] * 1000, physical['max_gap'])


class GdsStyle(unittest.TestCase):
    def setUp(self):
        self.fixture = legacy.AntennaSessionProtocol()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_qualifier_early_export_uses_the_final_stream_style(self):
        # The early export must reproduce be_gds's bytes, so both copies of the options agree.
        self.assertEqual(q.GDS_STYLE, native_backend.GDS_STYLE)
        self.assertEqual(native_backend.GDS_STYLE, ('--merge-shapes', 'rectangles', '--vias', 'flat'))

    def test_be_gds_writes_the_final_stream_style_in_both_post_route_orders(self):
        for order in native_backend.POSTROUTE_ORDERS:
            with self.subTest(order=order):
                backend, _ = self.fixture.backend(order=order)
                steps = self.fixture.by_id(backend)
                args = steps['be_gds']['arguments']
                self.assertEqual(steps['be_gds']['tool'], 'rustroad')
                self.assertEqual(args[2], 'export-gds')
                self.assertEqual(tuple(args[-len(native_backend.GDS_STYLE):]), native_backend.GDS_STYLE)
                self.assertEqual(sum(args.count(flag) for flag in ('--merge-shapes', '--vias')), 2)


class LatchupGate(unittest.TestCase):
    def test_limits_are_the_tap_coverage_ceilings_of_the_joint_proof(self):
        self.assertEqual(signoff_layout.TAP_LIMITS, native_flow.TAP_LIMITS)
        self.assertEqual(signoff_layout.TAP_LIMITS, q.TAP_LIMITS)
        self.assertEqual(signoff_layout.TAP_LIMITS, tap_coverage.CEILINGS)
        tap_coverage.Budget(signoff_layout.TAP_LIMITS)

    def test_plan_binds_the_gds_and_excludes_only_the_two_sram_macros(self):
        gds = dict(path='/layout/final.gds', sha256='a' * 64, bytes=1)
        plan = signoff_layout.latchup_plan(gds, Path('/work/latchup'))
        self.assertEqual(plan, dict(schema='rusteda.tap-coverage.v1', gds=gds, workspace='/work/latchup',
                                    base_revision='empty', top='ibex_sram_bench',
                                    excluded_cells={'RM_IHPSG13_1P_1024x32_c2_bm_bist': 2},
                                    limits=native_flow.TAP_LIMITS))
        # tap_coverage_runner.py accepts only an empty base revision and its own workspace.
        self.assertEqual(plan['base_revision'], 'empty')
        json.dumps(plan, allow_nan=False)


if __name__ == '__main__':
    unittest.main()

"""Opt-in composition controls, metadata only; never EDA qualification.

Estimate<128MiB: two five-file source bundles, <=128 tiny recipe nodes,
<2MiB JSON, one worker. Guard1GiB/60s; no graph engine or reference tool.
"""
import copy
from pathlib import Path
import unittest

from integration.ibex_sg13g2_ez130 import native_flow, producer
from integration.ibex_sg13g2_ez130.foundry_first import freeze, protocol as p
from tests import test_ihp_antenna_session as legacy

LOGIC_LIMITS, GATES = legacy.LOGIC_LIMITS, legacy.GATES


class FoundryComposition(unittest.TestCase):
    def setUp(self):
        self.old = legacy.AntennaSessionProtocol()
        self.old.setUp()
        self.root = self.old.root
        self.bundle = self.root / 'foundry-source'
        self.manifest = freeze.freeze(self.bundle)
        self.records = p.read(p.record(self.manifest))['sources']

    def tearDown(self):
        self.old.tearDown()

    def assembled(self, opt_in=True, threads=16, extra=0):
        backend, _ = self.old.backend()
        backend['scope']['threads'] = threads
        for step in backend['steps']:
            if step['id'] in {'be_antenna_inputs', 'be_postroute_inputs'}:
                step['content']['limits']['threads'] = threads
        for i in range(extra):
            backend['steps'].insert(-1, dict(id='protocol_extra_' + str(i), kind='command', tool='python',
                arguments=['protocol-only-never-execute'], timeout_s=1))
        files = dict(backend['files'], **{name: self.old.marker for name in native_flow.FILES})
        if opt_in:
            files.update(self.records)
            files['foundry_first_sources'] = self.manifest
        tools = dict(backend['tools'], **{name: self.old.marker for name in native_flow.TOOLS})
        return native_flow.assemble(backend, files, tools, LOGIC_LIMITS)

    def test_default_has_no_new_proofs_steps_or_policy_side_effects(self):
        legacy = self.assembled(False)
        by = {s['id']: s for s in legacy['steps']}
        self.assertFalse(any(tag.startswith('be_ff_') for tag in by))
        self.assertEqual(by['be_postroute_inputs']['content']['schema'], 'rusteda.native-postroute-inputs.v1')
        self.assertEqual(by['be_antenna_inputs']['content']['schema'], 'rusteda.native-antenna-inputs.v2')
        self.assertEqual(by['be_extract']['tool'], 'rustroad')
        self.assertEqual(by['nf_qualification_inputs']['content']['schema'], 'rusteda.native-qualification-inputs.v2')
        self.assertEqual(set(legacy['gates']), GATES)
        self.assertNotIn('parallel_groups', legacy)

    def test_opt_in_retains_all_sixteen_obligations_and_original_native_limits(self):
        old, new = self.assembled(False), self.assembled(True)
        self.assertEqual(old['gates'], new['gates'])
        self.assertEqual(old['artifacts'], new['artifacts'])
        self.assertEqual(old['scope'], new['scope'])
        self.assertEqual(len(new['steps']), len(old['steps']) + 2)
        by = {s['id']: s for s in new['steps']}
        cfg = by['be_antenna_inputs']['content']
        old_by = {s['id']: s for s in old['steps']}
        self.assertEqual(cfg['legacy_inputs'], old_by['be_antenna_inputs']['content'])
        self.assertIn('--foundry-first', by['be_postroute']['arguments'])
        self.assertEqual(by['be_extract']['tool'], 'python')
        self.assertNotIn('be_antenna_verify_inputs', by)
        self.assertEqual(by['nf_qualification_inputs']['content']['schema'],
                         'rusteda.native-foundry-first-qualification-inputs.v1')
        producer.validate_parallel_groups(new['steps'], new['parallel_groups'], new['scope'])
        producer.validate_stdout_artifacts(new['steps'], new['stdout_artifacts'], new['scope'])

    def test_both_gds_exports_use_same_private_effective_cell_lef_and_geometry_inputs(self):
        new = self.assembled()
        by = {s['id']: s for s in new['steps']}
        for flag in ['--cell-lef', '--cell-gds', '--layer-map']:
            values = []
            for tag in ['be_ff_export', 'be_gds']:
                args = by[tag]['arguments']
                self.assertEqual(args.count(flag), 1)
                values.append(args[args.index(flag) + 1])
            self.assertEqual(values[0], values[1])
            self.assertTrue(values[0].startswith('{output}/backend/postroute/effective-inputs/'))
        cfg = by['be_antenna_inputs']['content']
        self.assertEqual(cfg['extract_argv'][cfg['extract_argv'].index('--rc-profile') + 1],
                         '{output}/backend/postroute/effective-inputs/rc-profile.json')
        self.assertIn('cell_lef', by['be_postroute_inputs']['content']['foundry_first'])

    def test_early_tables_overlap_on_disjoint_lanes_and_two_cpu_mode_serializes(self):
        for threads in [2, 16]:
            new = self.assembled(threads=threads)
            group = next(g for g in new['parallel_groups'] if 'be_ff_main' in g['steps'])
            main = set(group['steps']['be_ff_main']['cpu_slots'])
            ant = set(group['steps']['be_ff_antenna']['cpu_slots'])
            self.assertEqual(bool(main & ant), threads == 2)
            self.assertLessEqual(sum(v['memory_mib'] for v in group['steps'].values()), group['max_memory_mib'])
            assigned = {tag for g in new['parallel_groups'] for tag in g['steps']}
            self.assertTrue({'be_postroute', 'be_antenna_repair', 'be_antenna_verify', 'be_extract',
                             'be_main', 'be_antenna'} <= assigned)

    def test_existing_step_cap_is_not_enlarged(self):
        old = self.assembled(False)
        extras = 126 - len(old['steps'])
        exact = self.assembled(extra=extras)
        self.assertEqual(len(exact['steps']), 128)
        with self.assertRaisesRegex(ValueError, 'STEP_BUDGET'):
            self.assembled(extra=extras + 1)

    def test_partial_bundle_and_boolean_policy_are_rejected(self):
        from integration.ibex_sg13g2_ez130.foundry_first import runner
        for role in self.records:
            files = dict(self.records, foundry_first_sources=self.manifest)
            files.pop(role)
            with self.assertRaises(ValueError):
                runner.bound_sources(files)
        files = dict(self.records)
        with self.assertRaisesRegex(ValueError, 'INCOMPLETE'):
            runner.bound_sources(files)


if __name__ == '__main__':
    unittest.main()

"""Future guarded joint-proof metadata controls; never an EDA oracle.

Upfront estimate<160MiB, one test worker, <=128 tiny recipe records and
<=256KiB fixture bytes. Caller1GiB/60s, pinned GNUtime. These tests do not run
KLayout/RustROAD or qualify physical geometry. Source preparation may precede
authorization to import/execute this module.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import native_flow as flow, qualify_native as q
from integration.ibex_sg13g2_ez130.foundry_first import verify


class JointProofMetadata(unittest.TestCase):
    def test_policy_contracts_agree_across_composer_and_qualifier(self):
        for name in ('TAP_SCHEMA', 'TAP_ROLES', 'TAP_POLICY', 'TAP_LIMITS', 'TAP_PATHS', 'TAP_WORKER'):
            self.assertEqual(getattr(flow, name), getattr(q, name))
        self.assertEqual(flow.TAP_CONTRACT, (q.TAP_CONTRACT_SHA256, q.CONTRACT_BYTES))
        self.assertNotEqual(q.TAP_CONTRACT_SHA256, q.CONTRACT_SHA256)

    def test_absent_joint_policy_retains_legacy_admission(self):
        self.assertFalse(flow.joint_tap({}))
        self.assertFalse(flow.joint_tap({'foundry_first_policy': {}}))

    def test_every_partial_or_unknown_tap_source_set_is_rejected_before_io(self):
        for missing in flow.TAP_ROLES:
            files = {name: {} for name in flow.TAP_ROLES - {missing}}
            files['foundry_first_policy'] = {}
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'complete'):
                flow.joint_tap(files)
        files = {name: {} for name in flow.TAP_ROLES | {'tap_acceptance'}}
        files['foundry_first_policy'] = {}
        with self.assertRaisesRegex(ValueError, 'complete'):
            flow.joint_tap(files)

    def test_original_tap_mode_cannot_silently_replace_c7(self):
        with self.assertRaisesRegex(ValueError, 'complete'):
            flow.joint_tap({name: {} for name in flow.TAP_ROLES})

    def test_integer_policy_is_strict_not_python_numeric_equality(self):
        marker = dict(path='/not-opened/tap_capture.py', sha256='a' * 64, bytes=1)
        files = {name: marker for name in flow.TAP_ROLES | {'foundry_first_policy', 'drc_runner'}}
        for key in ('native_max_clear_gap_dbu', 'nominal_phase_dbu', 'foundry_diffusion_tie_max_um'):
            policy = copy.deepcopy(flow.TAP_POLICY)
            policy[key] = float(policy[key])
            with mock.patch.object(flow, 'verify_record'), mock.patch.object(flow, 'read', return_value=policy):
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'policy differs'):
                    flow.joint_tap(files)

    def context(self, schema):
        return dict(schema=schema, run_root='/not-opened', files={}, tools={},
                    artifacts={}, reports={}, native_reports={}, controls='/not-opened')

    def test_c7_or_legacy_schema_cannot_ignore_tap_policy(self):
        for schema in ('rusteda.native-qualification-inputs.v1',
                       'rusteda.native-qualification-inputs.v2',
                       'rusteda.native-foundry-first-qualification-inputs.v1'):
            context = self.context(schema)
            context['files']['tap_policy'] = {}
            with self.subTest(schema=schema), self.assertRaisesRegex(ValueError, 'tap policy/schema'):
                q.qualify(context, Path('/not-written'))

    def test_joint_schema_requires_both_complete_policy_sets_before_io(self):
        context = self.context(q.TAP_SCHEMA)
        with self.assertRaisesRegex(ValueError, 'context fields'):
            q.qualify(context, Path('/not-written'))
        context['tap_checks'] = q.TAP_PATHS
        with self.assertRaisesRegex(ValueError, 'tap policy/schema'):
            q.qualify(context, Path('/not-written'))
        context['files'] = {name: {} for name in q.TAP_ROLES}
        with self.assertRaisesRegex(ValueError, 'foundry policy/schema'):
            q.qualify(context, Path('/not-written'))

    def test_three_serial_templates_bind_final_artifact_and_effective_profile(self):
        tools = dict(appimage=dict(path='/bound-tool', sha256='a' * 64, bytes=23))
        steps = flow.joint_tap_steps({}, tools, dict(gds='backend/final-retained.gds'))
        self.assertEqual([s['id'] for s in steps],
                         ['nf_tap_latchup', 'nf_tap_coverage_inputs', 'nf_tap_coverage'])
        self.assertEqual([s.get('timeout_s') for s in steps], [900, None, 600])
        args = steps[0]['arguments']
        self.assertEqual(args[args.index('--gds') + 1], '{output}/backend/final-retained.gds')
        self.assertEqual(args[args.index('--profile') + 1],
                         '{output}/backend/postroute/effective-inputs/foundry/provenance.json')
        self.assertEqual(steps[1]['content']['gds'], {'$identity': 'backend/final-retained.gds'})
        self.assertEqual(steps[1]['content']['limits'], flow.TAP_LIMITS)
        self.assertNotIn('parallel_groups', steps[0])
        self.assertNotIn('3072', json.dumps(steps))

    def test_new_json_reader_rejects_duplicate_nonfinite_depth_and_size(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'metadata.json'
            for value in ('{"x":0,"x":1}', '{"x":NaN}', '{"x":1e999}', '{"x":-1e999}',
                          '[' * 34 + '0' + ']' * 34):
                path.write_text(value)
                with self.subTest(value=value[:24]), self.assertRaises(ValueError):
                    q.tap_read(path)
            path.write_text('"' + 'x' * 32 + '"')
            with self.assertRaisesRegex(ValueError, 'budget'):
                q.tap_read(path, 16)

    def test_new_json_reader_rejects_node_expansion_with_fixed_input_size(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'metadata.json'
            path.write_text('[' + ','.join('0' for _ in range(65536)) + ']')
            with self.assertRaisesRegex(ValueError, 'pending budget'):
                q.tap_read(path)

    def test_raw_rule_scope_and_actual_finding_are_not_summary_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'drc.lyrdb'
            for rule, finding, expected in [('LU.a', '', True), ('LU.b', '', False),
                                           ('LU.a', '<items><item/></items>', False)]:
                path.write_text('<report-database><categories><category><name>' + rule
                                + '</name></category></categories>' + finding + '</report-database>')
                record = q.tap_identity(path)
                if expected:
                    self.assertEqual(q.tap_raw_zero(record, 'LU.a', root, verify), ['LU.a'])
                else:
                    with self.assertRaises(ValueError):
                        q.tap_raw_zero(record, 'LU.a', root, verify)
            path.write_text('<!DOCTYPE report-database [<!ENTITY x "LU.a">]>'
                            '<report-database><categories><category><name>&x;</name>'
                            '</category></categories></report-database>')
            with self.assertRaisesRegex(ValueError, 'DECLARATION'):
                q.tap_raw_zero(q.tap_identity(path), 'LU.a', root, verify)


if __name__ == '__main__':
    unittest.main()

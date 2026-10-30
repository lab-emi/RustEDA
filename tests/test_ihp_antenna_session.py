"""Single-session antenna wiring controls, never EDA qualification.

Estimate <128 MiB: six <=1 MiB helper files, <64 KiB symbolic recipes and tiny
metadata-only qualifier fixtures, one test worker. No EDA command, graph pass,
physical oracle or manufactured signoff evidence is executed. Run guarded at
1 GiB/60s; protocol fixtures are confined to disposable temporary directories.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import native_backend, native_flow, producer, qualify_native
from integration.ibex_sg13g2_ez130.antenna_session import freeze


GATES = {'drc', 'antenna', 'lvs', 'timing', 'timing_accuracy', 'synthesis_equivalence',
         'final_equivalence', 'simulation', 'inputs_frozen', 'macros', 'placement',
         'cts', 'power_connectivity', 'routing', 'extraction', 'controls'}
LOGIC_LIMITS = dict(memory_mib=4096, swap_mib=512, deadline_seconds=1200, threads=2,
                   stage_seconds=180, cec_seconds=160, max_conflicts=2_000_000,
                   discovery_work=16_000_000_000, discovery_rounds=32)


class AntennaSessionProtocol(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='antenna-session-protocol-')
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'frozen-helpers'
        self.manifest = freeze.freeze(self.bundle)
        self.group_policy = self.write('group-policy.json', native_backend.GROUP_POLICY)
        self.marker = self.write('protocol-only-input.txt', 'protocol fixture; never execute as a tool\n')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value) + '\n')
        return native_backend.identity(path)

    def grouped_files(self, session=True):
        files = dict(antenna_runner=native_backend.identity(self.bundle / 'native_antenna_groups.py'),
            antenna_legacy=native_backend.identity(self.bundle / 'native_antenna.py'),
            antenna_fields=native_backend.identity(self.bundle / 'report_fields.py'),
            antenna_verifier=native_backend.identity(self.bundle / 'verify.py'),
            antenna_source_manifest=self.manifest, antenna_group_policy=self.group_policy)
        if session:
            files['antenna_session_policy'] = native_backend.identity(self.bundle / 'antenna-session-policy.json')
        return files

    def backend(self, session=True, grouped=True):
        files = {name: self.marker for name in native_backend.REQUIRED}
        files.update(self.grouped_files(session) if grouped else {})
        if session and not grouped:
            files['antenna_session_policy'] = native_backend.identity(self.bundle / 'antenna-session-policy.json')
        files['contract'] = self.write('contract.json', dict(common=dict(
            rc_calibration_rules='ihp/libs.tech/librelane/openrcx/IHP_rcx_patterns.rules',
            rc_calibration_sha256=files['rules_nom']['sha256'])))
        road = '{output}/frontend/road-final'
        steps = [dict(id='fe_synth', kind='command', tool='rustsys', arguments=['protocol_only'], timeout_s=1),
            dict(id='fe_final_pdk_import', kind='command', tool='rustroad', timeout_s=1,
                 arguments=['--workspace', road, 'import-pdk', '--base-rev', 'rev0',
                            '--netlist', '{output}/frontend/prects.v']),
            dict(id='fe_final_place', kind='command', tool='rustroad', timeout_s=1,
                 arguments=['--workspace', road, 'place', '--base-rev', 'rev1']),
            dict(id='fe_final_cts', kind='command', tool='rustroad', timeout_s=1,
                 arguments=['--workspace', road, 'clock-tree', '--base-rev', 'rev2']),
            dict(id='fe_protocol_export', kind='command', tool='rustroad', timeout_s=1,
                 arguments=['--workspace', road, 'export-def'])]
        steps, streams = producer.declare_stdout_artifacts(steps)
        front_files = {name: self.marker for name in ('rtl', 'sdc', 'std_tt', 'std_ss',
                       'ram_tt', 'ram_ss', 'tech_lef', 'cell_lef', 'std_lef')}
        front_files['producer'] = native_backend.identity(Path(producer.__file__))
        front = dict(schema='rusteda.native-frontend-fragment.v1', files=front_files,
            tools={name: self.marker for name in ('python', 'rustsys', 'rustroad', 'rusteda', 'ruststa', 'gnu_time')},
            steps=steps, stdout_artifacts=streams,
            scope=dict(threads=2, memory_mib=8192, max_stdout_artifact_bytes=1536 << 20),
            outputs=dict(final_workspace='frontend/road-final'))
        config = dict(schema='rusteda.native-backend-inputs.v1', frontend_fragment=self.write('frontend.json', front),
            files=files, tools={name: self.marker for name in ('appimage', 'opensta', 'yosys')},
            pdk_root='/protocol-only/no-pdk-is-read')
        return native_backend.build(config)

    def assembled(self, session=True, grouped=True):
        backend, _ = self.backend(session, grouped)
        files = dict(backend['files'], **{name: self.marker for name in native_flow.FILES})
        tools = dict(backend['tools'], **{name: self.marker for name in native_flow.TOOLS})
        return native_flow.assemble(backend, files, tools, LOGIC_LIMITS)

    @staticmethod
    def by_id(recipe):
        return {step['id']: step for step in recipe['steps']}

    def test_explicit_pinned_policy_selects_session_in_all_three_helpers(self):
        files = self.grouped_files()
        for helper in (native_backend, native_flow, qualify_native):
            with self.subTest(helper=helper.__name__):
                self.assertTrue(helper.single_process_antenna(files))
                self.assertFalse(helper.single_process_antenna({}))

    def test_policy_requires_exact_identity_semantics_and_integer_process_count(self):
        expected = json.loads((self.bundle / 'antenna-session-policy.json').read_text())
        mutations = [dict(expected, actual_native_processes=value) for value in (True, 1.0, 0, 2)]
        mutations += [dict(expected, execution_mode='per_operation_processes'),
                      dict(expected, schema='unreviewed'), dict(expected, extra='unreviewed')]
        for index, value in enumerate(mutations):
            files = self.grouped_files()
            files['antenna_session_policy'] = self.write(f'policy-{index}.json', value)
            for helper in (native_backend, native_flow, qualify_native):
                with self.subTest(value=value, helper=helper.__name__), self.assertRaises(ValueError):
                    helper.single_process_antenna(files)
        files = self.grouped_files()
        files['antenna_session_policy'] = dict(files['antenna_session_policy'], sha256='0' * 64)
        for helper in (native_backend, native_flow, qualify_native):
            with self.subTest(helper=helper.__name__), self.assertRaises(ValueError):
                helper.single_process_antenna(files)

    def test_session_requires_explicit_complete_grouped_source_pins(self):
        with self.assertRaisesRegex(ValueError, 'requires the explicit grouped policy'):
            self.backend(session=True, grouped=False)
        files = self.grouped_files()
        files.pop('antenna_verifier')
        for helper in (native_backend, native_flow):
            with self.subTest(helper=helper.__name__), self.assertRaisesRegex(ValueError, 'incomplete explicit'):
                helper.grouped_antenna(files)
        files = self.grouped_files()
        (self.bundle / 'native_antenna.py').write_text('tampered helper\n')
        for helper in (native_backend, native_flow):
            with self.subTest(helper=helper.__name__), self.assertRaisesRegex(ValueError, 'source changed'):
                helper.grouped_antenna(files)

    def test_backend_renders_exact_v3_controller_file_identity_without_replacing_foundry_gate(self):
        backend, prefix = self.backend()
        steps = self.by_id(backend)
        self.assertEqual(steps['be_antenna_verify_inputs']['content'], dict(
            schema='rusteda.antenna-v3-independent-verifier-inputs.v1',
            lineage={'$identity': 'backend/antenna-repair/lineage.json'}, tool={'$tool': 'rustroad'},
            source_manifest={'$input': 'antenna_source_manifest'},
            design_workspace='{output}/backend/postroute/road', evidence_kind='actual_native_artifacts',
            controller_inputs={'$identity': 'backend/antenna-inputs.json'}))
        self.assertEqual(steps['be_antenna_inputs']['content']['schema'], 'rusteda.native-antenna-inputs.v2')
        self.assertEqual(backend['gates']['antenna'], 'backend/antenna/summary.json')
        self.assertEqual(prefix['gates'], {})
        self.assertEqual(steps['be_antenna_verify']['timeout_s'], 180)

    def test_flow_propagates_session_binding_and_keeps_all_sixteen_gates(self):
        manifest = self.assembled()
        self.assertEqual(set(manifest['gates']), GATES)
        steps = self.by_id(manifest)
        qualification = steps['nf_qualification_inputs']['content']
        self.assertEqual(qualification['schema'], 'rusteda.native-qualification-inputs.v2')
        self.assertEqual(qualification['native_reports']['antenna_verification'],
                         'backend/antenna-verification/result.json')
        self.assertEqual(qualification['files']['antenna_session_policy'], {'$input': 'antenna_session_policy'})
        self.assertEqual(manifest['gates']['antenna'], 'backend/antenna/summary.json')
        order = [step['id'] for step in manifest['steps']]
        self.assertLess(order.index('be_antenna_repair'), order.index('be_antenna_verify'))
        self.assertLess(order.index('be_antenna_verify'), order.index('be_gds'))
        self.assertEqual(len(set(order)), len(order))

    def test_flow_rejects_missing_wrong_or_foreign_controller_identity_render(self):
        backend, _ = self.backend()
        files = dict(backend['files'], **{name: self.marker for name in native_flow.FILES})
        tools = dict(backend['tools'], **{name: self.marker for name in native_flow.TOOLS})
        for mutation in ('missing', 'foreign', 'schema'):
            changed = copy.deepcopy(backend)
            content = self.by_id(changed)['be_antenna_verify_inputs']['content']
            if mutation == 'missing':
                content.pop('controller_inputs')
            elif mutation == 'foreign':
                content['controller_inputs'] = {'$identity': 'backend/other-inputs.json'}
            else:
                content['schema'] = 'rusteda.antenna-v2-independent-verifier-inputs.v1'
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'verifier input/source/scope differs'):
                native_flow.assemble(changed, files, tools, LOGIC_LIMITS)

    def test_no_session_preserves_grouped_v2_recipe_except_explicit_opt_in_fields(self):
        session, _ = self.backend(True)
        legacy, _ = self.backend(False)
        session['files'].pop('antenna_session_policy')
        content = self.by_id(session)['be_antenna_verify_inputs']['content']
        content.pop('controller_inputs')
        content['schema'] = 'rusteda.antenna-v2-independent-verifier-inputs.v1'
        self.assertEqual(session, legacy)
        old = self.assembled(False)
        self.assertEqual(set(old['gates']), GATES)
        self.assertNotIn('controller_inputs', self.by_id(old)['be_antenna_verify_inputs']['content'])
        ungrouped = self.assembled(False, False)
        self.assertNotIn('be_antenna_verify', self.by_id(ungrouped))
        self.assertEqual(self.by_id(ungrouped)['be_antenna_inputs']['content']['schema'],
                         'rusteda.native-antenna-inputs.v1')
        self.assertEqual(set(ungrouped['gates']), GATES)

    def qualifier_fixture(self, session=True):
        # These tiny objects exercise metadata binding only. They are not raw
        # EDA reports and never enter a real flow or component qualification.
        root = self.root / ('session-run' if session else 'legacy-run')
        workspace = root / 'road'
        for revision, parent in [('rev4', 'rev3'), ('rev5', 'rev4')]:
            self.write(str((workspace / 'revisions' / revision / 'design.json').relative_to(self.root)),
                       dict(format_version=6, revision=revision, parent_revision=parent, stage='placed'))
        self.write(str((workspace / 'HEAD').relative_to(self.root)), 'rev5\n')
        snapshot = self.write(str((root / 'head.txt').relative_to(self.root)), 'rev5\n')
        files = self.grouped_files(session)
        files.update(flow_qualifier=qualify_native.identity(Path(qualify_native.__file__)),
                     tech_lef=self.marker, cell_lef=self.marker)
        tools = {name: self.marker for name in ('rustroad', 'python', 'gnu_time')}
        revisions = {revision: dict(revision=revision, parent_revision=parent,
            design_file=qualify_native.identity(workspace / 'revisions' / revision / 'design.json'))
            for revision, parent in [('rev4', 'rev3'), ('rev5', 'rev4')]}
        lineage = dict(schema=f'rusteda.native-antenna-lineage.v{3 if session else 2}',
            scope='supported_standard_cell_subset', route_scope='all_signal_nets', status='unresolved',
            tool=tools['rustroad'], workspace=str(workspace), initial_revision='rev4', final_revision='rev5',
            initial=revisions['rev4'], final=revisions['rev5'], workspace_head=snapshot)
        lineage_id = self.write(str((root / 'backend/antenna-repair/lineage.json').relative_to(self.root)), lineage)
        controller = self.write(str((root / 'backend/antenna-inputs.json').relative_to(self.root)), {'protocol_only': True})
        config = dict(schema=f'rusteda.antenna-v{3 if session else 2}-independent-verifier-inputs.v1',
            lineage=lineage_id, tool=tools['rustroad'], source_manifest=files['antenna_source_manifest'],
            design_workspace=str(workspace), evidence_kind='actual_native_artifacts')
        if session:
            config['controller_inputs'] = controller
        config_id = self.write(str((root / 'backend/antenna-verification-inputs.json').relative_to(self.root)), config)
        verification = dict(schema=f'rusteda.antenna-v{3 if session else 2}-independent-lineage-review.v1',
            status='metadata_lineage_verified_requires_full_signoff', evidence_kind='actual_native_artifacts',
            whole_layout_antenna_status='unchecked', benchmark='not_run', verifier_source=files['antenna_verifier'],
            tool=tools['rustroad'], source_manifest=files['antenna_source_manifest'], policy=qualify_native.GROUP_POLICY,
            lineage=lineage_id, native_analysis_status='partial', routed_named_nets=129, final_group_count=2,
            source_lefs=[dict(role=role, path=self.marker['path'], sha256=self.marker['sha256'])
                         for role in ('technology_lef', 'cell_lef')], input_manifest=config_id,
            workspace=str(workspace), initial_revision='rev4', final_revision='rev5',
            initial=revisions['rev4'], final=revisions['rev5'], workspace_head=snapshot,
            resources=dict(address_space_limit_bytes=2 << 30, peak_rss_kib_self=128))
        if session:
            verification.update(execution_mode='single_native_process', actual_native_processes=1,
                                logical_operations=7)
        positions = ['fe_final_cts', 'be_route', 'be_antenna_repair', 'be_antenna_verify_inputs',
                     'be_antenna_verify', 'be_inspect', 'be_def', 'be_netlist', 'be_powered', 'be_gds', 'be_extract']
        stderr = self.write(str((root / 'logs/be_antenna_verify.stderr').relative_to(self.root)), '')
        receipts = [dict(id=tag, kind='render' if tag == 'be_antenna_verify_inputs' else 'command', exit_code=0)
                    for tag in positions]
        def sync():
            config_id = self.write(str((root / 'backend/antenna-verification-inputs.json').relative_to(self.root)), config)
            verification['input_manifest'] = config_id
            self.write(str((root / 'backend/antenna-verification/result.json').relative_to(self.root)), verification)
            stdout = self.write(str((root / 'logs/be_antenna_verify.stdout').relative_to(self.root)), verification)
            for row in receipts:
                if row['id'] == 'be_antenna_verify':
                    row.update(stdout=stdout, stderr=stderr)
            self.write(str((root / 'step-receipts.json').relative_to(self.root)), receipts)
        sync()
        self.write(str((root / 'logs/be_antenna_verify.time').relative_to(self.root)),
                   'Maximum resident set size (kbytes): 128\nExit status: 0\n')
        self.write(str((root / 'logs/be_antenna_verify.command.json').relative_to(self.root)),
                   dict(argv=[tools['gnu_time']['path'], '-v', '-o', str(root / 'logs/be_antenna_verify.time'),
                        tools['python']['path'], files['antenna_verifier']['path'], '--inputs',
                        str(root / 'backend/antenna-verification-inputs.json'), '--workspace',
                        str(root / 'backend/antenna-verification'), '--base-rev', 'empty'], cwd=str(root)))
        def qualify():
            return qualify_native.antenna_lineage_v2(lineage, verification, root,
                {'revision': 'rev4'}, {'revision': 'rev5'}, {'revision': 'rev3'},
                files, tools, Path(lineage_id['path']))
        return verification, config, sync, qualify

    def test_qualifier_binds_v3_controller_config_and_retains_legacy_output_shape(self):
        verification, config, sync, qualify = self.qualifier_fixture()
        result = qualify()
        self.assertEqual(result['execution_mode'], 'single_native_process')
        self.assertEqual(result['actual_native_processes'], 1)
        self.assertEqual(result['logical_operations'], 7)
        config['controller_inputs'] = self.marker
        sync()
        with self.assertRaisesRegex(ValueError, 'verifier config differs'):
            qualify()
        _, _, _, legacy_qualify = self.qualifier_fixture(False)
        legacy = legacy_qualify()
        self.assertFalse({'execution_mode', 'actual_native_processes', 'logical_operations'} & legacy.keys())

    def test_qualifier_rejects_wrong_review_schema_process_and_logical_counts(self):
        verification, _, sync, qualify = self.qualifier_fixture()
        mutations = [('schema', 'rusteda.antenna-v2-independent-lineage-review.v1'),
                     ('actual_native_processes', 0), ('actual_native_processes', 2),
                     ('actual_native_processes', True), ('actual_native_processes', 1.0),
                     ('logical_operations', 0), ('logical_operations', True), ('logical_operations', 4097),
                     ('final_group_count', 1)]
        original = copy.deepcopy(verification)
        for key, value in mutations:
            verification.clear()
            verification.update(copy.deepcopy(original))
            verification[key] = value
            sync()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                qualify()

    def test_freeze_preserves_exact_bytes_and_never_overwrites_or_nests_sources(self):
        source = Path(freeze.__file__).resolve().parent
        manifest = json.loads(Path(self.manifest['path']).read_text())
        self.assertEqual(manifest['status'], 'prepared_not_qualified')
        self.assertEqual({Path(row['path']).name for row in manifest['sources']}, set(freeze.FILES))
        for name in freeze.FILES:
            self.assertEqual((self.bundle / name).read_bytes(), (source / name).read_bytes())
        before = {name: (self.bundle / name).read_bytes() for name in freeze.FILES}
        for destination in (self.bundle, source, source / 'forbidden-new-child'):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, 'REVISION_CONFLICT'):
                freeze.freeze(destination)
        self.assertEqual(before, {name: (self.bundle / name).read_bytes() for name in freeze.FILES})

    def test_freeze_detects_copy_or_source_tampering_before_manifest_commit(self):
        original_copy = freeze.shutil.copyfile
        destination = self.root / 'corrupted-copy'
        def corrupt(source, target):
            original_copy(source, target)
            Path(target).write_bytes(Path(target).read_bytes() + b'corrupted')
        with mock.patch.object(freeze.shutil, 'copyfile', side_effect=corrupt), \
                self.assertRaisesRegex(ValueError, 'helper source changed'):
            freeze.freeze(destination)
        self.assertFalse((destination / 'manifest.json').exists())
        # Model a concurrently modified *private source copy*, preserving the
        # canonical helper bundle and every other test's source snapshots.
        private_source = self.root / 'private-source'
        private_source.mkdir()
        for name in freeze.FILES:
            original_copy(Path(freeze.__file__).resolve().parent / name, private_source / name)
        fake_module_path = private_source / 'freeze.py'
        fake_module_path.write_text('protocol source locator only\n')
        def mutate(source, target):
            original_copy(source, target)
            Path(source).write_bytes(Path(source).read_bytes() + b'changed')
        destination = self.root / 'source-changed'
        with mock.patch.object(freeze, '__file__', str(fake_module_path)), \
                mock.patch.object(freeze.shutil, 'copyfile', side_effect=mutate), \
                self.assertRaisesRegex(ValueError, 'helper source changed'):
            freeze.freeze(destination)
        self.assertFalse((destination / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()

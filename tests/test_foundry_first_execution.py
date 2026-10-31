"""Bounded mock-execution provenance controls, never EDA qualification.

Upfront estimate<128MiB: seven tiny revision files, <=16 small metadata files,
one capture-only executor. Guard1GiB/60s CPU28–29. Qualified engine identities
below test metadata matching only; no executable/reference tool is launched.
Missing original sixteen gate reports deliberately prevent flow qualification.
"""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import producer
from integration.ibex_sg13g2_ez130.foundry_first import protocol as p, runner, verify
from tests import test_foundry_first_protocol as foundation


class FoundryExecution(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FullInputProtocol()
        self.fixture.setUp()
        self.root, self.ws = self.fixture.root, self.fixture.ws
        self.inputs = self.root / 'inputs.json'
        p.write_new(self.inputs, dict(protocol_only=True))
        self.base = self.root / 'backend/foundry-first'
        self.base.mkdir(parents=True)
        self.initial = self.base / 'initial.gds'
        self.initial.write_bytes(b'protocol fixture; never physical GDS')
        self.profile = self.root / 'effective-rc.json'
        self.profile.write_text('{"protocol_only":true}')
        self.profile.chmod(0o444)
        self.argv = list(self.fixture.argv)
        self.argv[self.argv.index('--rc-profile') + 1] = str(self.profile)
        self.argv[self.argv.index('--out') + 1] = str(self.root / 'final.spef')
        self.tool = dict(path=self.argv[0], sha256='f47c5a7526855a4a5a106dd2ed42c03c74a2ace0ed48457b6942be1ec2d0110c', bytes=58686880)
        self.c = dict(run_root=str(self.root), workspace=str(self.ws), initial_gds=str(self.initial),
            final_gds=str(self.root / 'final.gds'), initial_revision='rev4', extract_argv=self.argv,
            scope=dict(threads=16), tools=dict(rustroad=self.tool, python=dict(path='/unexecuted/python')),
            files=dict(antenna_runner=dict(path='/unexecuted/legacy-runner')), legacy_inputs=dict(protocol_only=True))
        self.source = self.root / 'backend/postroute-inputs.json'
        recipe = list(self.argv[3:])
        recipe[recipe.index('--out') + 1] = '@SPEF@'
        p.write_new(self.source, dict(schema='rusteda.native-postroute-reuse-inputs.v1',
            tools=dict(rustroad=self.tool), recipe=dict(extract=recipe), limits=dict(threads=16), foundry_first={}))
        self.post = dict(input_manifest=p.identity(self.source),
            effective_rc=dict(source=p.identity(self.profile), effective=p.identity(self.profile)),
            extract_inputs=[], commands=[], rounds=[])
        self.snapshot = p.snapshot(self.ws)
        self.d = dict(mode='foundry_clean_keep_revision', final_snapshot=self.snapshot, final_revision='rev4')
        self.effective = mock.patch.object(runner, 'effective', return_value=p.identity(self.profile))
        self.effective.start()
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.output = self.root / 'execution'
        self.output.mkdir()

    def tearDown(self):
        self.env.stop()
        self.effective.stop()
        self.fixture.tearDown()

    def checks(self, main=0, antenna=0):
        for table, count in [('main', main), ('antenna', antenna)]:
            output = self.base / ('pre-' + table)
            output.mkdir()
            p.write_new(output / 'result.json', dict(schema='rusteda.foundry-precheck.v1', table=table,
                input_manifest=p.identity(self.inputs), gds=p.identity(self.initial), complete=True, violations=count))

    def legacy(self, c, output, tag, argv, seconds):
        target = self.root / 'backend/antenna-repair'
        target.mkdir()
        p.write_new(target / 'lineage.json', dict(final_revision='rev4', native_analysis_status='partial',
                                                unresolved_supported_nets=285))
        return dict(argv=argv, exit_code=0, capture_error=None)

    def actual_extract(self, c, output, tag, argv, seconds):
        destination = Path(argv[argv.index('--out') + 1])
        destination.write_text('protocol-only SPEF bytes; never physical evidence')
        raw = output / 'actual.stdout'
        p.write_new(raw, dict(schema='rustroad.extract.v1', status='ok', workspace_revision='rev4',
            output=p.identity(destination), rc_profile=p.identity(self.profile), scope=dict(corner='nom')))
        stderr = output / 'actual.stderr'
        stderr.touch()
        return dict(argv=argv, exit_code=0, capture_error=None, stdout=p.identity(raw), stderr=p.identity(stderr))

    def capture(self):
        source = self.root / 'source.spef'
        source.write_text('protocol-only prior SPEF; never physical evidence')
        source.chmod(0o444)
        raw = self.root / 'prior.stdout'
        p.write_new(raw, dict(schema='rustroad.extract.v1', status='ok', workspace_revision='rev4',
            output=p.identity(source), rc_profile=p.identity(self.profile), scope=dict(corner='nom')))
        stderr = self.root / 'prior.stderr'
        stderr.touch()
        prior_argv = list(self.argv)
        prior_argv[prior_argv.index('--out') + 1] = str(source)
        row = dict(argv=prior_argv, exit_code=0, stdout=p.identity(raw), stderr=p.identity(stderr))
        cap = dict(schema='rusteda.actual-full-extract-inputs.v1', status='captured', snapshot=self.snapshot,
            source_workspace=str(self.ws), tool=self.tool, profile=p.identity(self.profile),
            environment=p.extraction_environment(16), argument_profile=p.argument_profile(prior_argv, self.tool),
            command=row, raw_report=p.identity(raw), output=p.identity(source))
        path = self.root / 'capture.json'
        p.write_new(path, cap)
        self.post.update(extract_inputs=[p.identity(path)], commands=[row],
                         rounds=[dict(workspace=str(self.ws), check=dict(spef=p.identity(source)))])
        return cap

    def extraction(self, command):
        with mock.patch.object(runner, 'decision', return_value=self.d), mock.patch.object(runner, 'command', side_effect=command) as calls:
            result = runner.extract(self.c, self.post, self.inputs, self.output, None)
        return result, calls

    def test_clean_checks_keep_real_revision_and_explicit_unchecked_native(self):
        self.checks()
        with mock.patch.object(runner, 'command') as calls:
            result = runner.decide(self.c, self.post, self.inputs, self.output, None)
        calls.assert_not_called()
        self.assertEqual(result['mode'], 'foundry_clean_keep_revision')
        self.assertEqual(result['initial_snapshot'], result['final_snapshot'])
        self.assertEqual(result['final_revision'], 'rev4')
        self.assertEqual(result['actual_native_processes'], 0)
        self.assertEqual(result['native_analysis_status'], 'unchecked')
        self.assertIsNone(result['unresolved_supported_nets'])

    def test_dirty_check_runs_actual_legacy_once_without_erasing_native_telemetry(self):
        self.checks(antenna=1)
        with mock.patch.object(runner, 'command', side_effect=self.legacy) as calls:
            result = runner.decide(self.c, self.post, self.inputs, self.output, None)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result['mode'], 'legacy_repair')
        self.assertEqual(result['actual_native_processes'], 1)
        self.assertEqual(result['unresolved_supported_nets'], 285)
        self.assertEqual(result['native_analysis_status'], 'partial')

    def test_missing_or_noninteger_precheck_falls_back_to_actual_legacy(self):
        for state in ['missing', True, 0.0]:
            with self.subTest(state=state):
                if state == 'missing':
                    self.checks()
                    (self.base / 'pre-antenna/result.json').unlink()
                else:
                    p.write_new(self.base / 'pre-antenna/result.json', dict(schema='rusteda.foundry-precheck.v1',
                        table='antenna', input_manifest=p.identity(self.inputs), gds=p.identity(self.initial),
                        complete=True, violations=state))
                target = self.root / 'backend/antenna-repair'
                if target.exists():
                    (target / 'lineage.json').unlink()
                    target.rmdir()
                if (self.output / 'legacy-inputs.json').exists():
                    (self.output / 'legacy-inputs.json').unlink()
                with mock.patch.object(runner, 'command', side_effect=self.legacy) as calls:
                    result = runner.decide(self.c, self.post, self.inputs, self.output, None)
                self.assertEqual(calls.call_count, 1)
                self.assertEqual(result['mode'], 'legacy_repair')
                if state != 'missing':
                    (self.base / 'pre-antenna/result.json').unlink()

    def test_complete_match_reuses_actual_prior_bytes_with_zero_new_native_processes(self):
        cap = self.capture()
        result, calls = self.extraction(self.actual_extract)
        calls.assert_not_called()
        self.assertEqual(result['mode'], 'artifact_reuse')
        self.assertEqual(result['actual_native_processes'], 0)
        self.assertEqual(result['raw_report'], cap['raw_report'])
        self.assertEqual(result['source'], cap['output'])
        self.assertTrue(p.same_bytes(result['output'], cap['output']))
        self.assertNotEqual(result['output']['path'], cap['output']['path'])
        with mock.patch.object(runner, 'decision', return_value=self.d):
            self.assertEqual(verify.extraction_review(result, self.c, self.post, self.inputs, result['output'])['scope']['corner'], 'nom')
            for value in [False, 0.0]:
                bad = dict(result, actual_native_processes=value)
                with self.assertRaisesRegex(ValueError, 'COUNTER'):
                    verify.extraction_review(bad, self.c, self.post, self.inputs, result['output'])

    def test_missing_capture_or_raw_receipt_is_an_actual_full_extraction(self):
        result, calls = self.extraction(self.actual_extract)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result['mode'], 'actual_native_execution')
        self.assertEqual(result['reuse_miss_reason'], 'missing_capture_receipt')
        Path(result['output']['path']).unlink()
        Path(result['raw_report']['path']).unlink()
        (self.output / 'actual.stderr').unlink()
        cap = self.capture()
        Path(cap['raw_report']['path']).unlink()
        result, calls = self.extraction(self.actual_extract)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result['reuse_miss_reason'], 'missing_prior_report_or_spef')

    def test_changed_request_executes_full_actual_and_keeps_miss_truthful(self):
        self.capture()
        self.argv[self.argv.index('--min-coupling-ff') + 1] = '0.2'
        result, calls = self.extraction(self.actual_extract)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result['mode'], 'actual_native_execution')
        self.assertEqual(result['actual_native_processes'], 1)
        self.assertEqual(result['reuse_miss_reason'], 'unsupported_or_changed_complete_inputs')
        with mock.patch.object(runner, 'decision', return_value=self.d):
            verify.extraction_review(result, self.c, self.post, self.inputs, result['output'])
            raw = p.read(result['raw_report']['path'])
            Path(result['raw_report']['path']).unlink()
            p.write_new(result['raw_report']['path'], dict(raw, scope=dict(corner='wrong')))
            bad = copy.deepcopy(result)
            bad['raw_report'] = bad['command']['stdout'] = p.identity(result['raw_report']['path'])
            with self.assertRaisesRegex(ValueError, 'RAW_BINDING'):
                verify.extraction_review(bad, self.c, self.post, self.inputs, result['output'])

    def test_capture_not_an_actual_selected_postroute_command_is_rejected(self):
        self.capture()
        self.post['commands'] = []
        with self.assertRaisesRegex(ValueError, 'ACTUAL_COMMAND_MEMBERSHIP'):
            self.extraction(self.actual_extract)
        self.post['commands'] = [p.read(p.record(self.post['extract_inputs'][0]))['command']]
        self.post['rounds'][-1]['workspace'] = str(self.root / 'historical')
        with self.assertRaisesRegex(ValueError, 'FINAL_SELECTED_CHECK'):
            self.extraction(self.actual_extract)

    def test_repair_never_reuses_a_prior_spef(self):
        self.capture()
        self.d['mode'] = 'legacy_repair'
        result, calls = self.extraction(self.actual_extract)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result['reuse_miss_reason'], 'repair_requires_full_extraction')

    def test_final_gds_content_change_rejects_early_check_binding(self):
        self.checks()
        self.d.update(initial_gds=p.identity(self.initial), checks={table: p.identity(self.base / ('pre-'+table) / 'result.json') for table in ['main', 'antenna']})
        Path(self.c['final_gds']).write_bytes(b'different layout bytes')
        with mock.patch.object(runner, 'decision', return_value=self.d), mock.patch.object(runner, 'command') as calls:
            with self.assertRaisesRegex(ValueError, 'FINAL_GDS_CHANGED'):
                runner.final_table(self.c, self.post, self.inputs, self.output, 'main')
        calls.assert_not_called()

    def actual_postroute_receipt(self):
        # Metadata-only fixture, never an execution/EDA pass. The helper under
        # test must bind the full input/result to these exact capture bytes.
        actual_root = self.root / 'output'
        actual_root.mkdir()
        self.c['run_root'] = str(actual_root)
        source = actual_root / 'backend/postroute-inputs.json'
        source.parent.mkdir()
        p.write_new(source, p.read(self.source))
        self.source = source
        self.post['input_manifest'] = p.identity(source)
        driver = self.root / 'postroute-driver-fixture'
        driver.write_text('unexecuted fixture')
        self.c['files']['postroute_runner'] = p.identity(driver)
        self.c['tools']['gnu_time'] = dict(path='/unexecuted/gnu-time')
        self.c['files']['producer'] = p.identity(producer.__file__)
        self.c['scope'].update(memory_mib=8192, cpu_affinity=list(range(16)))
        self.post.update(schema='rusteda.native-postroute-reuse-result.v1', status='clean',
            final_workspace=str(self.ws), final_revision='rev4', resized=[],
            policy_source=p.identity(driver), capture_policy={})
        target = actual_root / 'backend/postroute'
        target.mkdir()
        result = target / 'result.json'
        p.write_new(result, self.post)
        self.c['postroute_result'] = p.identity(result)
        summary = {key: self.post[key] for key in ('schema', 'status', 'final_workspace', 'final_revision', 'resized')}
        summary.update(input_manifest=self.post['input_manifest'], result=self.c['postroute_result'])
        logs = actual_root / 'logs'
        logs.mkdir()
        stdout, stderr = logs / 'be_postroute.stdout', logs / 'be_postroute.stderr'
        p.write_new(stdout, summary)
        stderr.touch()
        argv = ['/unexecuted/gnu-time', '-v', '-o', str(logs / 'be_postroute.time'),
            self.c['tools']['python']['path'], str(driver), '--inputs', str(self.source),
            '--workspace', str(target), '--base-rev', 'empty', '--foundry-first']
        p.write_new(logs / 'be_postroute.command.json', dict(argv=argv, cwd=str(actual_root),
            cpu_affinity=list(range(16)), memory_reservation_mib=7168))
        receipts = [dict(id='be_postroute_inputs', kind='render'),
            dict(id='be_postroute', kind='command', exit_code=0, wall_seconds=0.01,
                stdout=p.identity(stdout), stderr=p.identity(stderr), started_seconds=0.0,
                cpu_affinity=list(range(16)), memory_reservation_mib=7168, depends_on=[])]
        p.write_new(actual_root / 'step-receipts.json', receipts)
        recipe = self.root / 'actual-manifest.json'
        p.write_new(recipe, dict(schema='rusteda.ihp-flow.v1', files=self.c['files'],
            tools=self.c['tools'], scope=self.c['scope'],
            steps=[dict(id='be_postroute', kind='command', tool='python', arguments=argv[5:], timeout_s=60)],
            parallel_groups=[dict(max_parallel=1, max_memory_mib=7168,
                steps=dict(be_postroute=dict(depends_on=[], cpu_slots=list(range(16)), memory_mib=7168)))]))
        owner = self.root / 'ownership'
        owner.mkdir()
        p.write_new(owner / 'native.json', dict(request_id='a' * 64, implementation='native', manifest=p.identity(recipe)))
        os.environ.update(RUSTEDA_WORKER_ID='a' * 64, RUSTEDA_OWNERSHIP=str(owner))
        return stdout, receipts

    def test_full_postroute_input_and_result_require_actual_producer_stdout_binding(self):
        stdout, receipts = self.actual_postroute_receipt()
        self.assertEqual(verify.postroute_authority(self.c, self.post), p.read(self.source))
        bad = copy.deepcopy(self.post)
        bad['commands'] = [dict(fabricated=True)]
        with self.assertRaisesRegex(ValueError, 'FULL_RESULT_CHANGED'):
            verify.postroute_authority(self.c, bad)
        changed = p.read(stdout)
        changed.pop('result')
        stdout.unlink()
        p.write_new(stdout, changed)
        receipts[-1]['stdout'] = p.identity(stdout)
        receipt_path = Path(self.c['run_root']) / 'step-receipts.json'
        receipt_path.unlink()
        p.write_new(receipt_path, receipts)
        with self.assertRaisesRegex(ValueError, 'STDOUT_SUBSTITUTION'):
            verify.postroute_authority(self.c, self.post)

    def test_selected_capture_must_match_actual_controller_recipe(self):
        self.capture()
        changed = p.read(self.source)
        changed['recipe']['extract'][changed['recipe']['extract'].index('--max-extract-work')+1] = '1'
        self.source.unlink()
        p.write_new(self.source, changed)
        self.post['input_manifest'] = p.identity(self.source)
        with self.assertRaisesRegex(ValueError, 'ACTUAL_RECIPE_CHANGED'):
            self.extraction(self.actual_extract)

    def test_initial_and_final_shared_gds_reject_before_any_checker_execution(self):
        for key in ['initial_gds', 'final_gds']:
            with self.subTest(key=key):
                target = Path(self.c[key])
                if not target.exists():
                    target.write_bytes(b'protocol-only GDS')
                target.chmod(0o644)
                alias = self.root / ('mutable-' + key)
                os.link(target, alias)
                with mock.patch.object(runner, 'decision', return_value=self.d), mock.patch.object(runner, 'command') as calls:
                    with self.assertRaisesRegex(ValueError, 'SHARED_INODE'):
                        if key == 'initial_gds':
                            runner.precheck(self.c, self.post, self.inputs, self.output, 'main')
                        else:
                            runner.final_table(self.c, self.post, self.inputs, self.output, 'main')
                calls.assert_not_called()
                self.assertEqual(alias.stat().st_mode & 0o777, 0o644)
                alias.unlink()

    def test_raw_xml_scope_counters_and_entity_rejection(self):
        path = self.root / 'scope.xml'
        path.write_text('<report-database><categories><category><name>one</name></category><category><name>two</name></category></categories><items><item/><item/></items></report-database>')
        self.assertEqual(verify.xml_scope(path), (['one', 'two'], 2))
        for text in ['<!DOCTYPE x [<!ENTITY bad "x">]><report-database/>',
                     '<report-database><categories><category><name>one</name></category><category><name>one</name></category></categories></report-database>']:
            path.write_text(text)
            with self.assertRaises(ValueError):
                verify.xml_scope(path)
        for value in [True, 1.0, -1]:
            with self.assertRaisesRegex(ValueError, 'COUNTER'):
                verify.integer(value)


if __name__ == '__main__':
    unittest.main()

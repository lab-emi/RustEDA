"""Joint metadata admission and genuine stdlib producer controls, never EDA.

Estimate<160MiB: <=128 tiny nodes, <=512KiB source/config snapshots, <=2 Python
children and one test worker; caller1GiB/512MiB/120s. Real producer controls use
only the two allocated test CPUs. The 16-CPU/128-step recipe is checked as
metadata, not misrepresented as an actual 16-CPU or physical execution.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import native_flow as flow, producer, qualify_native as q
from integration.ibex_sg13g2_ez130.foundry_first import freeze, protocol as p, verify
from integration.ibex_sg13g2_ez130.foundry_first import runner
from tests import test_foundry_first_composition as composition
from tests import test_foundry_first_producer as actual


class JointActualSerial(unittest.TestCase):
    def setUp(self):
        self.f = actual.ActualProducerMetadata()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.files = dict(producer=p.identity(self.f.source))
        guard = Path(flow.__file__).with_name('tap_guard.py')
        self.files['tap_guard'] = p.identity(guard)
        code = "import json,sys;from pathlib import Path;Path('control.json').write_text('{\"status\":\"passed\"}');print(json.dumps({'metadata_only':True,'argv':sys.argv[1:]}))"
        for role in ('tap_latchup_runner', 'tap_coverage_runner'):
            path = self.f.root / (role + '.py')
            path.write_text(code)
            self.files[role] = p.identity(path)
        self.tools = dict(self.f.tools, appimage=self.f.tools['python'])
        self.steps = flow.joint_tap_steps(self.files, self.tools, dict(gds='backend/final.gds'))
        self.data = dict(schema='rusteda.ihp-flow.v1', name='joint_actual_serial_metadata_only',
            pdk='ihp-sg13g2', library='ez130_8t', corners=['tt', 'ss'], files=self.files,
            tools=self.tools, scope=self.f.scope, artifacts={},
            gates={name: 'control.json' for name in producer.GATES},
            steps=[dict(id='be_macro_contract', kind='render', destination='backend/final.gds',
                        content=dict(metadata_only_not_gds=True)), *self.steps])
        self.f.manifest.write_text(json.dumps(self.data))
        result = subprocess.run([self.f.python, str(self.f.source), '--project', str(self.f.root),
            '--manifest', str(self.f.manifest), '--workspace', str(self.f.output), '--base-rev', 'empty',
            '--pdk', 'ihp', '--rustroad', self.f.python, '--gnu-time', str(self.f.timer)],
            env=self.f.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.c = dict(run_root=str(self.f.output), files=self.files, tools=self.tools, scope=self.f.scope)
        if os.environ.get('RUSTEDA_JOINT_CONTROL_OUTPUT'):
            dest = Path(os.environ['RUSTEDA_JOINT_CONTROL_OUTPUT']) / self.id().split('.')[-1]
            dest.mkdir()
            sources = [self.f.source, self.f.manifest, self.f.owner / 'native.json',
                       *self.f.output.rglob('*')]
            records = []
            for index, path in enumerate(path for path in sources if path.is_file()):
                self.assertLess(index, 64)
                original = p.identity(path)
                self.assertLessEqual(original['bytes'], 1 << 20)
                target = dest / (str(index) + '-' + path.name)
                shutil.copyfile(path, target)
                records.append(dict(original=original, frozen=p.identity(target)))
            (dest / 'ACTUAL.json').write_text(json.dumps(dict(
                scope='actual stdlib producer twoCPU metadata control; not physical or16CPU run',
                cpu_affinity=self.f.scope['cpu_affinity'], files=records), indent=2) + '\n')

    def argv(self, tag):
        step = next(step for step in self.steps if step['id'] == tag)
        values = [self.tools['python']['path'], *step['arguments']]
        for i, value in enumerate(values):
            value = value.replace('{output}', str(self.f.output))
            for namespace, records in (('files', self.files), ('tools', self.tools)):
                for role, record in records.items():
                    value = value.replace('{' + namespace + ':' + role + '}', record['path'])
            values[i] = value
        return values

    def consume(self, tag):
        argv = self.argv(tag)
        with mock.patch.dict(os.environ, self.f.env, clear=True):
            return verify.producer_command(self.c, None, tag, argv,
                dict(metadata_only=True, argv=argv[3:]))

    def test_genuine_two_serial_tags_have_original_two_field_commands(self):
        for tag in ('nf_tap_latchup', 'nf_tap_coverage'):
            with self.subTest(tag=tag):
                self.assertEqual(self.consume(tag)['exit_code'], 0)
                self.assertEqual(set(p.read(self.f.output / 'logs' / (tag + '.command.json'))), {'argv', 'cwd'})

    def test_real_serial_argv_cwd_unknown_and_scheduled_telemetry_reject(self):
        for tag in ('nf_tap_latchup', 'nf_tap_coverage'):
            path = self.f.output / 'logs' / (tag + '.command.json')
            original = p.read(path)
            for change in (lambda v: v['argv'].append('--unexpected'),
                           lambda v: v.update(cwd=str(self.f.root)),
                           lambda v: v.update(cpu_affinity=self.f.scope['cpu_affinity']),
                           lambda v: v.update(memory_reservation_mib=4096),
                           lambda v: v.update(unknown=True)):
                value = copy.deepcopy(original)
                change(value)
                path.write_text(json.dumps(value))
                with self.subTest(tag=tag, change=change), self.assertRaises(ValueError):
                    self.consume(tag)
                path.write_text(json.dumps(original))

    def test_real_serial_source_ownership_capture_and_nonzero_reject(self):
        tag = 'nf_tap_latchup'
        path = self.f.output / 'step-receipts.json'
        rows = p.read(path)
        for field, value in [('exit_code', 1), ('unknown', 0), ('cpu_affinity', [28, 29])]:
            changed = copy.deepcopy(rows)
            next(row for row in changed if row['id'] == tag)[field] = value
            path.write_text(json.dumps(changed))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.consume(tag)
        path.write_text(json.dumps(rows))
        stdout = self.f.output / 'logs' / (tag + '.stdout')
        original = stdout.read_bytes()
        stdout.write_text('{"metadata_only":false}')
        with self.assertRaises(ValueError):
            self.consume(tag)
        stdout.write_bytes(original)
        raw = self.f.source.read_bytes()
        self.f.source.write_bytes(raw + b'\n# source change\n')
        with self.assertRaises(ValueError):
            self.consume(tag)
        self.f.source.write_bytes(raw)
        owner = self.f.owner / 'native.json'
        value = p.read(owner)
        value['request_id'] = 'b' * 64
        owner.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.consume(tag)


class JointAdmission(unittest.TestCase):
    def setUp(self):
        self.f = composition.FoundryComposition()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.root = self.f.root
        self.backend, _ = self.f.old.backend()
        self.backend['scope'].update(threads=16,
            cpu_affinity=[0, 2, 4, 6, 8, 10, 12, 14, 16, 17, 18, 19, 20, 21, 22, 23])
        for step in self.backend['steps']:
            if step['id'] in {'be_antenna_inputs', 'be_postroute_inputs'}:
                step['content']['limits']['threads'] = 16
        self.backend['parallel_groups'] = [producer.signoff_parallel_group()]
        self.files = dict(self.backend['files'], **{name: self.f.old.marker for name in flow.FILES})
        self.files['routing'] = self.f.old.marker
        self.files.update(self.f.records)
        self.files['foundry_first_sources'] = self.f.manifest
        self.tools = dict(self.backend['tools'], **{name: self.f.old.marker for name in flow.TOOLS})
        base = flow.assemble(self.backend, self.files, self.tools, composition.LOGIC_LIMITS)
        for i in range(125 - len(base['steps'])):
            self.backend['steps'].insert(-1, dict(id='metadata_padding_' + str(i), kind='render',
                destination='padding/' + str(i) + '.json', content=dict(metadata_only=True)))
        folder = self.root / 'joint-helpers'
        folder.mkdir()
        # Approved source is a fixed first-party fixture, never run by these
        # tests. It is not the old failed T1 worker present in this branch.
        frozen = Path(os.environ.get('RUSTEDA_TEST_TAP_SOURCE_DIR', str(Path(flow.__file__).parent)))
        for role, name in [('tap_coverage_worker', 'tap_coverage.py'),
                           ('tap_coverage_runner', 'tap_coverage_runner.py'),
                           ('tap_latchup_runner', 'signoff_latchup.py'),
                           ('tap_latchup_common', 'signoff_drc.py')]:
            shutil.copyfile(frozen / name, folder / name)
            self.files[role] = p.identity(folder / name)
        worker = self.files['tap_coverage_worker']
        self.assertEqual((worker['sha256'], worker['bytes']), flow.TAP_WORKER,
                         'explicit first-party hierarchy-fixed test source required')
        self.files['drc_runner'] = self.files['tap_latchup_common']
        self.files['tap_guard'] = p.identity(Path(flow.__file__).with_name('tap_capture.py'))
        for role, data in [('tap_policy', flow.TAP_POLICY),
                           ('physical', dict(tap=dict(max_gap=30000, pattern=dict(kind='staggered', phase=15000))))]:
            path = folder / (role + '.json')
            path.write_text(json.dumps(data))
            self.files[role] = p.identity(path)
        # The joint proof pins its own original T1 contract (30 um native clear gap), frozen as a
        # fixture: it is not the native recipe's contract.json, which declares the recipe's own
        # tap lattice and may change without touching the T1 proof.
        contract = folder / 'contract.json'
        shutil.copyfile(self.t1_contract(), contract)
        self.files['contract'] = p.identity(contract)
        self.assertEqual((self.files['contract']['sha256'], self.files['contract']['bytes']), flow.TAP_CONTRACT)
        self.assertEqual(json.loads(contract.read_text())['common']['native_tap_max_gap_um'], 30)
        deck = folder / 'opaque-maximal-byte-fixture'
        deck.write_bytes(b'only a metadata byte fixture; not a reference deck')
        self.files['tap_latchup_deck'] = p.identity(deck)

    def t1_contract(self):
        # Own-source frozen copy of the T1 contract, checked against its provenance record.
        folder = Path(__file__).parent / 'fixtures/foundry_first_t1'
        record = json.loads((folder / 'provenance.json').read_text())['files']['contract.json']
        self.assertEqual((record['sha256'], record['bytes']), flow.TAP_CONTRACT)
        return folder / 'contract.json'

    def legacy_source(self, name):
        # Own-source compatibility baseline, independent of Git clone depth/history.
        hashes = {'native_flow.py': 'e7eb65e991473d95e33327d4235c8e22dce6cbb5f07d5a0661a2ab8c330f99b8', 'runner.py': '817d007415f2f9ab83ab41b1e053566a02d82dc886a908010cb1dc2232b745c2'}
        source = (Path(__file__).parent / 'fixtures/foundry_first_4596' / name).read_bytes()
        self.assertLessEqual(len(source), 1 << 20)
        self.assertEqual(hashlib.sha256(source).hexdigest(), hashes[name])
        return source

    def assembled(self):
        return flow.assemble(copy.deepcopy(self.backend), self.files, self.tools, composition.LOGIC_LIMITS)

    def test_real_shaped_joint_recipe_is128_serial_and_original16_b4_scope(self):
        manifest = self.assembled()
        self.assertEqual(len(manifest['steps']), 128)
        self.assertEqual(len(manifest['gates']), 16)
        self.assertEqual(len(manifest['parallel_groups']), 6)
        self.assertIn(producer.signoff_parallel_group(), manifest['parallel_groups'])
        context = next(s for s in manifest['steps'] if s['id'] == 'nf_qualification_inputs')['content']
        self.assertEqual(context['schema'], q.TAP_SCHEMA)
        self.assertEqual(context['tap_checks'], q.TAP_PATHS)
        self.assertFalse(any(k.startswith('nf_tap_') for g in manifest['parallel_groups'] for k in g['steps']))

    def test_legacy_and_standalone_c7_serialized_bytes_equal4596_with_identical_inputs(self):
        source = self.legacy_source('native_flow.py')
        baseline = self.root / 'baseline4596-native-flow.py'
        baseline.write_bytes(source)
        spec = importlib.util.spec_from_file_location('joint_default_baseline', baseline)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        backend, _ = self.f.old.backend()
        for opt_in in (False, True):
            files = dict(backend['files'], **{name: self.f.old.marker for name in flow.FILES})
            if opt_in:
                files.update(self.f.records)
                files['foundry_first_sources'] = self.f.manifest
            old = module.assemble(copy.deepcopy(backend), files, self.tools, composition.LOGIC_LIMITS)
            new = flow.assemble(copy.deepcopy(backend), files, self.tools, composition.LOGIC_LIMITS)
            old_raw = (json.dumps(old, indent=2, allow_nan=False) + '\n').encode()
            new_raw = (json.dumps(new, indent=2, allow_nan=False) + '\n').encode()
            self.assertEqual(old_raw, new_raw, 'default serialized recipe differs from4596aca')
            if os.environ.get('RUSTEDA_JOINT_CONTROL_OUTPUT'):
                dest = Path(os.environ['RUSTEDA_JOINT_CONTROL_OUTPUT'])
                name = 'standalone-c7' if opt_in else 'legacy'
                for suffix, raw in [('4596', old_raw), ('candidate', new_raw)]:
                    with (dest / (name + '-' + suffix + '.json')).open('xb') as stream:
                        stream.write(raw)

    def test_standalone_c7_compose_function_bytes_equal_exact4596_implementation(self):
        source = self.legacy_source('runner.py')
        baseline = self.root / 'baseline4596-runner.py'
        baseline.write_bytes(source)
        name = 'integration.ibex_sg13g2_ez130.foundry_first.joint_composition_baseline'
        spec = importlib.util.spec_from_file_location(name, baseline)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        backend, _ = self.f.old.backend()
        files = dict(backend['files'], **{key: self.f.old.marker for key in flow.FILES})
        legacy = flow.assemble(backend, files, self.tools, composition.LOGIC_LIMITS)
        legacy['files'].update(self.f.records)
        legacy['files']['foundry_first_sources'] = self.f.manifest
        # A pure default-transform comparison with IDENTICAL declared inputs.
        # Actual source/authentication is not claimed by this unit control.
        old = module.compose(copy.deepcopy(legacy))
        new = runner.compose(copy.deepcopy(legacy))
        old_raw = (json.dumps(old, indent=2, allow_nan=False) + '\n').encode()
        new_raw = (json.dumps(new, indent=2, allow_nan=False) + '\n').encode()
        self.assertEqual(old_raw, new_raw)
        if os.environ.get('RUSTEDA_JOINT_CONTROL_OUTPUT'):
            dest = Path(os.environ['RUSTEDA_JOINT_CONTROL_OUTPUT'])
            for suffix, raw in [('4596', old_raw), ('candidate', new_raw)]:
                with (dest / ('pure-compose-' + suffix + '.json')).open('xb') as stream:
                    stream.write(raw)

    def test_native_recipe_contract_is_not_the_t1_contract(self):
        # The native recipe's contract.json declares the native lattice of native-physical.json;
        # the joint proof never accepts it in place of its pinned T1 contract.
        recipe = Path(flow.__file__).parent
        native = json.loads((recipe / 'contract.json').read_text())['common']
        physical = json.loads((recipe / 'native-physical.json').read_text())['tap']
        self.assertEqual(native['native_tap_max_gap_um'] * 1000, physical['max_gap'])
        self.assertEqual(native['tap_rule_max_distance_um'], 20)
        self.files['contract'] = p.identity(recipe / 'contract.json')
        with self.assertRaisesRegex(ValueError, 'exact original T1 contract'):
            self.assembled()
        self.files['contract'] = p.identity(self.t1_contract())
        self.assertEqual(len(self.assembled()['steps']), 128)

    def test_wrong_frozen_worker_and_source_sibling_reject(self):
        worker = Path(self.files['tap_coverage_worker']['path'])
        raw = worker.read_bytes()
        worker.write_bytes(raw + b'\n# changed\n')
        self.files['tap_coverage_worker'] = p.identity(worker)
        with self.assertRaisesRegex(ValueError, 'hierarchy-fixed'):
            self.assembled()
        worker.write_bytes(raw)
        self.files['tap_coverage_worker'] = p.identity(worker)
        elsewhere = self.root / 'wrong-sibling.py'
        elsewhere.write_bytes(raw)
        self.files['tap_coverage_worker'] = p.identity(elsewhere)
        with self.assertRaisesRegex(ValueError, 'sibling'):
            self.assembled()

    def test_joint_wrong_scope_changed_b4_or129th_step_reject(self):
        original = copy.deepcopy(self.backend)
        self.backend['scope']['threads'] = 2
        with self.assertRaisesRegex(ValueError, 'sixteen-CPU'):
            self.assembled()
        self.backend = copy.deepcopy(original)
        self.backend['parallel_groups'][0]['steps']['be_main']['memory_mib'] -= 1
        with self.assertRaisesRegex(ValueError, 'B4'):
            self.assembled()
        self.backend = copy.deepcopy(original)
        self.backend['steps'].insert(-1, dict(id='one_too_many', kind='render',
            destination='one_too_many.json', content=dict(metadata_only=True)))
        with self.assertRaises(ValueError):
            self.assembled()

    def test_joint_metadata_consumer_exact_template_scope_and_role_pins(self):
        manifest = self.assembled()
        root = self.root / 'output'
        root.mkdir()
        (root.parent / 'ownership').mkdir()
        (root.parent / 'ownership/native.json').write_text('{"manifest":{"static_metadata_fixture":true}}')
        (root / 'logs').mkdir()
        for tag in ('nf_tap_latchup', 'nf_tap_coverage'):
            (root / 'logs' / (tag + '.command.json')).write_text('{}')
        context = dict(run_root=str(root), files=self.files, tools=self.tools,
                       artifacts=manifest['artifacts'])
        artifacts = dict(gds=dict(path=str(root / manifest['artifacts']['gds'])))
        helper = types.SimpleNamespace(producer_manifest=lambda c: (manifest, manifest['parallel_groups']),
            producer_command=lambda *a: dict(metadata_control_only=True), p=p)
        receipts = [dict(id='nf_tap_latchup', kind='command'),
                    dict(id='nf_tap_coverage_inputs', kind='render', wall_seconds=0.01),
                    dict(id='nf_tap_coverage', kind='command')]
        (root / 'step-receipts.json').write_text(json.dumps(receipts))
        for relative in (q.TAP_PATHS['latchup_execution'], q.TAP_PATHS['coverage_execution']):
            target = root / relative; target.parent.mkdir(parents=True); target.write_text('{}')
        # Producer receipt authenticity is exercised separately by the actual
        # two-CPU serial tests; this callback tests 16-CPU recipe admission only.
        with mock.patch.object(q, 'tap_execution_proof', return_value=({},dict(
                static_fixture_only=True,execution_result=dict(static_fixture_only=True)))):
            q.tap_producer_proof(context, artifacts, {}, None, helper)
        original = copy.deepcopy(manifest)
        for mutate in [lambda m: m['scope'].update(threads=True),
                       lambda m: m['scope'].update(memory_mib=8193),
                       lambda m: next(s for s in m['steps'] if s['id'] == 'nf_tap_latchup')['arguments'].append('unknown'),
                       lambda m: m['files'].update(tap_guard=self.f.old.marker)]:
            manifest.clear(); manifest.update(copy.deepcopy(original)); mutate(manifest)
            with self.assertRaises(ValueError), mock.patch.object(q, 'tap_execution_proof', return_value=({},{})):
                q.tap_producer_proof(context, artifacts, {}, None, helper)
        manifest.clear(); manifest.update(copy.deepcopy(original))
        for mutation in (lambda rows: rows[1].update(wall_seconds=True),
                         lambda rows: rows[1].update(wall_seconds=float('inf')),
                         lambda rows: rows[1].update(artifact={'invented': True}),
                         lambda rows: rows.pop(1), lambda rows: rows.reverse()):
            changed = copy.deepcopy(receipts); mutation(changed)
            (root / 'step-receipts.json').write_text(json.dumps(changed))
            with self.assertRaises(ValueError), mock.patch.object(q, 'tap_execution_proof', return_value=({},dict(
                    static_fixture_only=True,execution_result=dict(static_fixture_only=True)))):
                q.tap_producer_proof(context, artifacts, {}, None, helper)

    def routing_context(self):
        root = self.root / 'routing-output'
        root.mkdir()
        files = dict(self.files, flow_qualifier=p.identity(Path(q.__file__)))
        original = self.root / 'routing-template.json'
        original.write_text(json.dumps(dict(limits=dict(replay=dict(
            path='{output}/backend/route-budget-error.json')), max_work=100)))
        files['routing'] = p.identity(original)
        routing = root / 'frontend/physical/routing.json'
        routing.parent.mkdir(parents=True)
        private = str(root / 'backend/route-budget-error.json')
        routing.write_text(json.dumps(dict(limits=dict(replay=dict(path=private)), max_work=100)))
        audit = dict(schema='rusteda.private-routing-writer-audit.v1', status='passed',
            run_root=str(root), template_mode=False, config=p.identity(routing),
            writers=[dict(field='limits/replay/path', path=private)])
        for name, relative in q.TAP_PATHS.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(audit if name == 'routing_audit' else {}))
        return dict(run_root=str(root), files=files, tools=self.tools,
                    tap_checks=q.TAP_PATHS), root, routing

    def test_private_routing_writer_contains_only_declared_path_change(self):
        context, root, routing = self.routing_context()
        class ReachedNextBinding(Exception):
            pass
        class StopBeforeAnyGds(dict):
            def __getitem__(self, name):
                raise ReachedNextBinding(name)
        with self.assertRaises(ReachedNextBinding):
            q.verify_tap_checks(context, {}, StopBeforeAnyGds(), {}, lambda v: root / v, None)

    def test_private_routing_writer_foreign_unknown_changed_or_stale_reject(self):
        context, root, routing = self.routing_context()
        audit_path = root / q.TAP_PATHS['routing_audit']
        original_audit = p.read(audit_path)
        original_routing = routing.read_text()
        for mutate in [lambda a: a.update(template_mode=True),
                       lambda a: a['writers'][0].update(field='unknown/path'),
                       lambda a: a['writers'][0].update(path=str(self.root / 'foreign.json')),
                       lambda a: a['config'].update(sha256='0' * 64)]:
            audit = copy.deepcopy(original_audit); mutate(audit)
            audit_path.write_text(json.dumps(audit))
            with self.assertRaises(ValueError):
                q.verify_tap_checks(context, {}, {}, {}, lambda v: root / v, None)
        audit_path.write_text(json.dumps(original_audit))
        changed = json.loads(original_routing); changed['max_work'] = 101
        routing.write_text(json.dumps(changed))
        audit = copy.deepcopy(original_audit); audit['config'] = p.identity(routing)
        audit_path.write_text(json.dumps(audit))
        with self.assertRaisesRegex(ValueError, 'algorithm knobs'):
            q.verify_tap_checks(context, {}, {}, {}, lambda v: root / v, None)

    def test_private_routing_unknown_literal_writer_cannot_hide_in_both_inputs(self):
        context, root, routing = self.routing_context()
        template = Path(context['files']['routing']['path'])
        for path in (template, routing):
            data = json.loads(path.read_text())
            data['unexpected_writer'] = dict(output_path=str(self.root / 'foreign-output.json'))
            path.write_text(json.dumps(data))
        context['files']['routing'] = p.identity(template)
        audit_path = root / q.TAP_PATHS['routing_audit']
        audit = p.read(audit_path); audit['config'] = p.identity(routing)
        audit_path.write_text(json.dumps(audit))
        class UndeclaredWriterNotRejected(Exception):
            pass
        class StopBeforeAnyGds(dict):
            def __getitem__(self, name):
                raise UndeclaredWriterNotRejected(name)
        with self.assertRaisesRegex(ValueError, 'routing writer'):
            q.verify_tap_checks(context, {}, StopBeforeAnyGds(), {}, lambda v: root / v, None)

    def test_routing_inventory_work_depth_count_and_wrong_template_are_bounded(self):
        root = self.root.resolve()
        good = dict(limits=dict(replay=dict(path='{output}/backend/route-budget-error.json')))
        self.assertEqual(len(q.tap_routing_inventory(good, root, True)), 1)
        value = copy.deepcopy(good); value['padding'] = [0] * 16384
        with self.assertRaisesRegex(ValueError, 'budget'):
            q.tap_routing_inventory(value, root, True)
        value = 0
        for _ in range(33):
            value = dict(deep=value)
        with self.assertRaisesRegex(ValueError, 'budget'):
            q.tap_routing_inventory(value, root, True)
        value = copy.deepcopy(good); value['track_router'] = copy.deepcopy(good)
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            q.tap_routing_inventory(value, root, True)
        value = copy.deepcopy(good); value['limits']['replay']['path'] = '/foreign.json'
        with self.assertRaisesRegex(ValueError, 'template'):
            q.tap_routing_inventory(value, root, True)


if __name__ == '__main__':
    unittest.main()

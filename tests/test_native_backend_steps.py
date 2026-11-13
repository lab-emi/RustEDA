"""Native backend steps: composition, and parity of `rustroad postroute-repair` with the Python runner.

The composition tests are pure recipe construction on symbolic inputs. The
parity tests need a RustROAD executable that has the flow-step commands
(`RUSTEDA_TEST_RUSTROAD=/path/to/rustroad`); without it they are skipped. They
run the Python post-route runner and the native command in the same
protocol-only fixture (the stand-in tools of test_postroute_screen.py, never
EDA) and compare the two result records field for field.

Estimate <64 MiB, a few hundred small files, about 150 short child processes;
guard 1 GiB/120 s.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import native_backend, native_flow, native_postroute, producer
from integration.ibex_sg13g2_ez130.antenna_session import freeze

import test_postroute_screen as screen

RUSTROAD = os.environ.get('RUSTEDA_TEST_RUSTROAD')
STEP_IDS = ('be_profiles', 'be_antenna_repair', 'be_antenna_verify', 'be_postroute')


class NativeStepComposition(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-backend-steps-')
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'frozen-helpers'
        self.manifest = freeze.freeze(self.bundle)
        self.marker = self.write('protocol-only-input.txt', 'protocol fixture; never execute as a tool\n')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value) + '\n')
        return native_backend.identity(path)

    def python_files(self):
        files = {name: self.marker for name in native_backend.REQUIRED}
        files.update(antenna_runner=native_backend.identity(self.bundle / 'native_antenna_groups.py'),
                     antenna_legacy=native_backend.identity(self.bundle / 'native_antenna.py'),
                     antenna_fields=native_backend.identity(self.bundle / 'report_fields.py'),
                     antenna_verifier=native_backend.identity(self.bundle / 'verify.py'),
                     antenna_source_manifest=self.manifest)
        return files

    def native_files(self):
        return {name: self.marker for name in native_backend.REQUIRED - native_backend.PYTHON_STEP_FILES}

    def backend(self, files, **extra):
        files = dict(files)
        files.setdefault('antenna_group_policy', self.write('group-policy.json', native_backend.GROUP_POLICY))
        files.setdefault('antenna_session_policy',
                         native_backend.identity(self.bundle / 'antenna-session-policy.json'))
        files = {key: value for key, value in files.items() if value is not None}
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
                      arguments=['--workspace', road, 'clock-tree', '--base-rev', 'rev2'])]
        steps, streams = producer.declare_stdout_artifacts(steps + [
            dict(id='fe_protocol_export', kind='command', tool='rustroad', timeout_s=1,
                 arguments=['--workspace', road, 'export-def'])])
        front_files = {name: self.marker for name in ('rtl', 'sdc', 'std_tt', 'std_ss', 'ram_tt', 'ram_ss',
                                                       'tech_lef', 'cell_lef', 'std_lef')}
        front_files['producer'] = native_backend.identity(Path(producer.__file__))
        front = dict(schema='rusteda.native-frontend-fragment.v1', files=front_files,
                     tools={name: self.marker for name in ('python', 'rustsys', 'rustroad', 'rusteda', 'ruststa',
                                                           'gnu_time')},
                     steps=steps, stdout_artifacts=streams,
                     scope=dict(threads=2, memory_mib=8192, max_stdout_artifact_bytes=1536 << 20),
                     outputs=dict(final_workspace='frontend/road-final'))
        config = dict(schema='rusteda.native-backend-inputs.v1', frontend_fragment=self.write('frontend.json', front),
                      files=files, tools={name: self.marker for name in ('appimage', 'opensta', 'yosys')},
                      pdk_root='/protocol-only/no-pdk-is-read', **extra)
        return native_backend.build(config)

    @staticmethod
    def by_id(recipe):
        return {step['id']: step for step in recipe['steps']}

    def test_native_steps_are_rustroad_commands_and_pin_no_python_helper(self):
        fragment, prefix = self.backend(self.native_files(), backend_steps='native')
        steps = self.by_id(prefix)
        out = '{output}/backend/'
        self.assertEqual({tag: (steps[tag]['tool'], steps[tag]['arguments'], steps[tag]['timeout_s']) for tag in STEP_IDS}, {
            'be_profiles': ('rustroad', ['physical-inputs', '--inputs', out + 'physical-inputs.json',
                                         '--out-dir', out + 'profiles'], 180),
            'be_antenna_repair': ('rustroad', ['antenna-session', '--inputs', out + 'antenna-inputs.json',
                                               '--out-dir', out + 'antenna-repair'], 1860),
            'be_antenna_verify': ('rustroad', ['verify-antenna-lineage', '--inputs',
                                               out + 'antenna-verification-inputs.json', '--out-dir',
                                               out + 'antenna-verification'], 180),
            'be_postroute': ('rustroad', ['postroute-repair', '--inputs', out + 'postroute-inputs.json',
                                          '--out-dir', out + 'postroute'], 5460)})
        self.assertFalse(native_backend.PYTHON_STEP_FILES & set(prefix['files']))
        self.assertFalse((native_backend.V2_FILES - {'antenna_group_policy'}) & set(prefix['files']))
        physical = steps['be_physical_inputs']['content']
        self.assertEqual(physical['schema'], 'rusteda.native-physical-inputs.v2')
        self.assertNotIn('rc_builder', physical['files'])
        self.assertEqual(steps['be_antenna_inputs']['content']['schema'], 'rusteda.native-antenna-inputs.v2')
        self.assertEqual(steps['be_antenna_verify_inputs']['content'], dict(
            schema='rusteda.antenna-v3-independent-verifier-inputs.v2',
            lineage={'$identity': 'backend/antenna-repair/lineage.json'}, tool={'$tool': 'rustroad'},
            design_workspace='{output}/frontend/road-final', evidence_kind='actual_native_artifacts',
            controller_inputs={'$identity': 'backend/antenna-inputs.json'}))
        postroute = steps['be_postroute_inputs']['content']
        self.assertEqual(postroute['schema'], 'rusteda.native-postroute-inputs.v3')
        self.assertEqual(set(postroute['tools']), {'rustroad', 'rusteda', 'ruststa'})
        self.assertEqual(set(postroute['antenna']), {'inputs', 'lineage', 'verifier_inputs', 'verification'})
        # The step order, every other step and the producer manifest shape are the Python recipe's.
        python, python_prefix = self.backend(self.python_files())
        self.assertEqual([s['id'] for s in prefix['steps']], [s['id'] for s in python_prefix['steps']])
        changed = set(STEP_IDS) | {'be_physical_inputs', 'be_antenna_verify_inputs', 'be_postroute_inputs'}
        other = self.by_id(python_prefix)
        self.assertEqual({tag: step for tag, step in steps.items() if tag not in changed},
                         {tag: step for tag, step in other.items() if tag not in changed})
        self.assertEqual(set(prefix), set(python_prefix))
        self.assertEqual(fragment['backend_steps'], 'native')
        producer.validate_stdout_artifacts(prefix['steps'], prefix['stdout_artifacts'], prefix['scope'])

    def test_python_steps_stay_the_default_and_are_unchanged_by_the_explicit_value(self):
        default = self.backend(self.python_files())
        explicit = self.backend(self.python_files(), backend_steps='python')
        self.assertEqual(default, explicit)
        self.assertNotIn('backend_steps', default[0])
        steps = self.by_id(default[1])
        self.assertEqual({steps[tag]['tool'] for tag in STEP_IDS}, {'python'})

    def test_native_steps_reject_what_they_cannot_run(self):
        cases = [
            (self.native_files(), dict(backend_steps='native', postroute_order=native_backend.LEGACY_ORDER),
             'require the screen_after_extract order'),
            (self.python_files(), dict(backend_steps='native'), 'pin no Python step helper'),
            (dict(self.native_files(), antenna_session_policy=None), dict(backend_steps='native'),
             'grouped single-process antenna session policies'),
            (dict(self.native_files(), antenna_group_policy=self.write('wrong-policy.json', dict(
                native_backend.GROUP_POLICY, max_attempts=65))), dict(backend_steps='native'),
             'must match qualified integer budgets'),
            (self.native_files(), dict(backend_steps='rust'), 'backend_steps must be one of'),
            (self.native_files(), dict(), 'backend collateral incomplete'),
        ]
        for files, extra, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.backend(files, **extra)

    def test_qualification_composer_refuses_a_native_step_fragment(self):
        fragment, _ = self.backend(self.native_files(), backend_steps='native')
        files = dict(fragment['files'], **{name: self.marker for name in native_flow.FILES})
        tools = dict(fragment['tools'], **{name: self.marker for name in native_flow.TOOLS})
        with self.assertRaisesRegex(ValueError, 'binds the Python backend steps'):
            native_flow.assemble(fragment, files, tools, dict(memory_mib=4096))


def indented(body):
    return textwrap.indent(body.replace("'--workspace'", "'--out-dir'"), '    ')


# The pinned rustroad of a native recipe also runs the antenna session and its review.
NATIVE_ROAD = ('\nif sys.argv[1:2] == [\'antenna-session\']:\n' + indented(screen.ANTENNA) + '    sys.exit(0)\n'
               'if sys.argv[1:2] == [\'verify-antenna-lineage\']:\n' + indented(screen.VERIFY) + '    sys.exit(0)\n'
               + screen.ROAD)
VOLATILE = ('input_manifest', 'policy_source', 'resources')


@unittest.skipUnless(RUSTROAD, 'set RUSTEDA_TEST_RUSTROAD to a rustroad with the flow-step commands')
class PostrouteRepairParity(unittest.TestCase):
    def fixture(self, violate=False, sticky=False):
        with mock.patch.object(screen, 'ROAD', NATIVE_ROAD):
            f = screen.ScreenFixture(violate, sticky)
        self.addCleanup(f.temp.cleanup)
        return f

    def native(self, f, **changes):
        """Run the native command on the v3 form of the fixture's inputs."""
        path = f.inputs(**changes)
        value = json.loads(path.read_text())
        value['schema'] = 'rusteda.native-postroute-inputs.v3'
        del value['tools']['python'], value['antenna']['runner'], value['antenna']['verifier']
        path.write_text(json.dumps(value))
        done = subprocess.run([RUSTROAD, 'postroute-repair', '--inputs', str(path), '--out-dir',
                               str(f.root / 'backend/postroute')], capture_output=True, text=True, timeout=120)
        return done, json.loads((f.root / 'backend/postroute/result.json').read_text())

    def both(self, f, **changes):
        reference = f.run(**changes)
        layout = (f.root / 'backend/postroute/result.json').read_text()
        os.rename(f.root / 'backend/postroute', f.root / 'backend/postroute.python')
        os.unlink(f.root / 'backend/native.nom.spef')
        done, result = self.native(f, **changes)
        self.assertEqual((done.returncode, done.stderr), (0, ''))
        # The record is laid out exactly as the Python runner writes it.
        text = (f.root / 'backend/postroute/result.json').read_text()
        self.assertEqual(text, json.dumps(result, indent=2) + '\n')
        self.assertEqual(layout, json.dumps(reference, indent=2) + '\n')
        self.assertEqual(json.loads(done.stdout), {key: result[key] for key in (
            'schema', 'status', 'final_workspace', 'final_revision', 'resized')})
        return reference, result

    def assert_same_record(self, reference, result, f):
        """Everything but provenance, run times and the two antenna command lines is equal, in order."""
        python, antenna = reference['tools'].pop('python'), {}
        for record in (reference, result):
            for key in VOLATILE:
                self.assertIn(key, record)
                del record[key]
            for row in record['commands']:
                self.assertIs(type(row.pop('wall_seconds')), float)
                if row['name'].startswith('antenna'):
                    antenna.setdefault(row['name'], []).append(row.pop('argv'))
        self.assertEqual(json.dumps(reference), json.dumps(result))
        for name, (old, new) in antenna.items():
            verb = 'verify-antenna-lineage' if name.endswith('-verify') else 'antenna-session'
            self.assertEqual(old[0], python['path'])
            self.assertEqual(new[:3], [f.tools['rustroad']['path'], verb, '--inputs'])
            self.assertEqual((old[3], old[5]), (new[3], new[5]))
            self.assertEqual((old[2], old[4], new[4]), ('--inputs', '--workspace', '--out-dir'))

    def test_clean_screen_links_the_trial_like_the_python_runner(self):
        f = self.fixture()
        reference, result = self.both(f)
        self.assertEqual((result['status'], result['final_source']), ('clean', 'trial_link'))
        final, trial = f.root / 'backend/postroute/road', f.trial
        for relative in ['revisions/rev5/routes.rbin', 'revisions/rev4/design.json', 'receipts/rev5.json']:
            self.assertEqual(os.stat(final / relative).st_ino, os.stat(trial / relative).st_ino)
        for fresh in ['HEAD', 'workspace.lock']:
            self.assertNotEqual(os.stat(final / fresh).st_ino, os.stat(trial / fresh).st_ino)
        self.assertEqual(os.stat(f.root / 'backend/native.nom.spef').st_ino,
                         os.stat(f.root / 'backend/trial.native.nom.spef').st_ino)
        self.assert_same_record(reference, result, f)
        self.assertEqual(f.review(result)[2]['status'], 'clean')

    def test_forced_resize_rebuilds_repairs_verifies_and_rescreens_like_the_python_runner(self):
        f = self.fixture(violate=True)
        reference, result = self.both(f)
        self.assertEqual((result['status'], result['final_source'], result['resized']),
                         ('repaired', 'eco_rebuild', {'g1': 'BUFX2'}))
        names = [row['name'] for row in result['commands']]
        for name in ['apply0', 'build0-route', 'antenna1', 'antenna1-verify', 'check1-extract', 'check1-sta-run']:
            self.assertIn(name, names)
        python = f.root / 'backend/postroute.python'
        for name in ['plan0.json', 'equal-width-sizing.json', 'antenna1-inputs.json',
                     'antenna1-verification-inputs.json', 'check1/tt-physical.lib', 'netlist0.v']:
            self.assertEqual((f.root / 'backend/postroute' / name).read_bytes(), (python / name).read_bytes(), name)
        self.assert_same_record(reference, result, f)
        self.assertEqual(f.review(result)[2], dict(source='eco_rebuild', status='repaired', resized=1, screens=2,
                                                   trial_revision='rev5'))

    def test_round_budget_exhaustion_is_reported_like_the_python_runner(self):
        f = self.fixture(violate=True, sticky=True)
        policy = dict(near_limit_ratio=0.9, target_ratio=0.8, max_rounds=1, max_resizes=256)
        reference, result = self.both(f, policy=policy)
        self.assertEqual(result['status'], 'round_budget_exhausted')
        self.assertEqual([[op['cell'] for op in r['resizes']] for r in result['rounds']], [['BUFX2'], ['BUFX4']])
        self.assert_same_record(reference, result, f)

    def test_drift_is_rejected_with_a_coded_error_and_an_error_record(self):
        f = self.fixture()
        command = f.root / 'logs/be_extract.command.json'
        value = json.loads(command.read_text())
        value['argv'].insert(-2, '--extract-query-workers')
        command.write_text(json.dumps(value))
        with self.assertRaisesRegex(native_postroute.Failure, 'argv differs'), \
                contextlib.redirect_stdout(io.StringIO()):
            f.run()
        shutil.rmtree(f.root / 'backend/postroute')
        done, record = self.native(f)
        self.assertEqual(done.returncode, 2)
        error = json.loads(done.stderr)
        self.assertEqual((error['status'], error['code']), ('error', 'POSTROUTE'))
        self.assertIn('extraction argv differs from the recipe template', error['message'])
        self.assertEqual(record['status'], 'error')
        self.assertFalse((f.root / 'backend/native.nom.spef').exists())
        # One run per output directory.
        again = subprocess.run([RUSTROAD, 'postroute-repair', '--inputs', str(f.root / 'backend/postroute-inputs.json'),
                                '--out-dir', str(f.root / 'backend/postroute')], capture_output=True, text=True)
        self.assertEqual(json.loads(again.stderr)['code'], 'REVISION_CONFLICT')


if __name__ == '__main__':
    unittest.main()

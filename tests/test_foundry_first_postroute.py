"""Actual-call provenance controls with a capture-only executor, never EDA.

Estimate<64MiB: seven tiny raw files, small JSON, one mock executor/worker.
Use the same1GiB/60s guard as the byte protocol controls.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import native_postroute
from integration.ibex_sg13g2_ez130.foundry_first import protocol as p


class PostrouteCapture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='foundry-capture-fixture-')
        self.root = Path(self.temp.name)
        self.ws = self.root / 'road'
        rev = self.ws / 'revisions/rev4'
        rev.mkdir(parents=True)
        (self.ws / 'workspace.lock').touch()
        (self.ws / 'HEAD').write_text('rev4\n')
        for name in p.REV_FILES:
            (rev / name).write_text('protocol fixture, never physical evidence')
        (rev / 'design.json').write_text('{"format_version":6,"revision":"rev4","parent_revision":"rev3","stage":"placed","design":{}}')
        self.out = self.root / 'postroute'
        self.out.mkdir()
        rc = self.root / 'rc.json'
        rc.write_text('{"protocol_only":true}')
        frozen = p.freeze_file(rc, self.out / 'effective-rc.json', self.out, 1024)
        tool = self.root / 'unexecuted-tool-fixture'
        tool.write_text('metadata control only; no EDA executable')
        r = self.runner = native_postroute.Runner.__new__(native_postroute.Runner)
        r.reuse, r.protocol, r.frozen_rc = True, p, frozen
        r.c = dict(tools={'rustroad': p.identity(tool)}, limits={'threads': 16})
        r.out, r.commands, r.extract_inputs = self.out, [], []
        self.args = ['--threads', '16', '--max-expansions', '8000000', 'extract',
            '--rc-profile', frozen['effective']['path'], '--out', str(self.root / 'out.spef'),
            '--corner', 'nom', '--max-spef-mib', '256', '--extract-memory-mib', '3584',
            '--max-extract-shapes', '4000000', '--max-rc-nodes', '2000000', '--max-rc-elements', '30000000',
            '--max-coupling-caps', '10000000', '--max-extract-work', '100000000000',
            '--max-extract-candidates', '65536', '--min-coupling-ff', '0.1',
            '--extract-query-workers', '16', '--extract-query-batch-runs', '8', '--extract-query-scratch-mib', '32']
        r.run = mock.Mock(side_effect=self.capture_only)
        # Test-only metadata admission: this file is never executed and cannot qualify EDA.
        self.engines = mock.patch.dict(p.KNOWN_ENGINES, {r.c['tools']['rustroad']['sha256']:
            dict(bytes=r.c['tools']['rustroad']['bytes'], queries='explicit_queries_required')})
        self.engines.start()

    def tearDown(self):
        self.engines.stop()
        self.temp.cleanup()

    def capture_only(self, name, argv, cap):
        argv = [str(v) for v in argv]
        out = Path(argv[argv.index('--out') + 1])
        out.write_text('protocol fixture, never usable SPEF')
        report = self.out / (name + '.stdout')
        p.write_new(report, dict(schema='rustroad.extract.v1', status='ok', workspace_revision='rev4',
            output=p.identity(out), rc_profile=self.runner.frozen_rc['effective']))
        error = self.out / (name + '.stderr')
        error.touch()
        self.runner.commands.append(dict(name=name, argv=argv, exit_code=0, wall_seconds=0.0,
                                         stdout=p.identity(report), stderr=p.identity(error)))
        return report

    def run_capture(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            return self.runner.road('check0-extract', self.ws, self.args)

    def test_actual_returned_raw_report_and_complete_inputs_are_captured(self):
        returned = self.run_capture()
        self.assertEqual(self.runner.run.call_count, 1)
        self.assertEqual(len(self.runner.extract_inputs), 1)
        value = p.read(p.record(self.runner.extract_inputs[0]))
        self.assertEqual(value['schema'], 'rusteda.actual-full-extract-inputs.v1')
        self.assertEqual(value['command'], self.runner.commands[0])
        self.assertEqual(value['raw_report'], p.identity(returned))
        self.assertEqual(value['snapshot'], p.snapshot(self.ws))
        self.assertEqual(value['environment']['RAYON_NUM_THREADS'], '16')
        self.assertEqual(value['argument_profile']['cli_threads'], 16)

    def test_unqualified_defaults_miss_and_execute_once(self):
        index = self.args.index('--threads')
        del self.args[index:index+2]
        self.run_capture()
        self.assertEqual(self.runner.run.call_count, 1)
        value = p.read(p.record(self.runner.extract_inputs[0]))
        self.assertEqual(value['schema'], 'rusteda.full-extract-inputs-miss.v1')
        self.assertEqual(value['reason'], 'EXTRACT_DEFAULTS_UNQUALIFIED')

    def test_post_execution_miss_never_reexecutes_a_mutated_request(self):
        before = p.snapshot(self.ws)
        with mock.patch.object(p, 'snapshot_locked', side_effect=[before, p.Miss('post-call change')]):
            with self.assertRaisesRegex(native_postroute.Failure, 'changed to unsupported'):
                self.run_capture()
        self.assertEqual(self.runner.run.call_count, 1)
        self.assertEqual(self.runner.extract_inputs, [])

    def test_default_legacy_dispatch_does_not_add_capture_or_change_argv(self):
        self.runner.reuse = False
        self.run_capture()
        self.assertEqual(self.runner.run.call_count, 1)
        self.assertEqual(self.runner.extract_inputs, [])
        self.assertEqual(self.runner.commands[0]['argv'],
            [self.runner.c['tools']['rustroad']['path'], '--workspace', str(self.ws), *self.args])

    def test_pinned_protocol_executes_only_verified_bounded_source_bytes(self):
        source = self.root / 'tiny-protocol.py'
        source.write_text('value = 17\n')
        pin = p.identity(source)
        self.assertEqual(native_postroute.pinned_protocol(pin).value, 17)
        for key, value in [('bytes', True), ('bytes', float(pin['bytes'])), ('sha256', '0' * 64)]:
            with self.assertRaises(native_postroute.Failure):
                native_postroute.pinned_protocol(dict(pin, **{key: value}))
        alias = self.root / 'alias-protocol.py'
        alias.symlink_to(source)
        with self.assertRaisesRegex(native_postroute.Failure, 'symlink'):
            native_postroute.pinned_protocol(dict(pin, path=str(alias)))

    def test_opt_in_cli_checks_ancestor_symlink_before_mkdir(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'escape').symlink_to(outside, target_is_directory=True)
        cfg = self.root / 'new-inputs.json'
        p.write_new(cfg, dict(schema='rusteda.native-postroute-reuse-inputs.v1', foundry_first=dict(
            protocol=p.identity(Path(p.__file__)), run_root=str(self.root))))
        result = subprocess.run([sys.executable, native_postroute.__file__, '--inputs', str(cfg),
            '--workspace', str(self.root / 'escape/not-created'), '--base-rev', 'empty', '--foundry-first'],
            capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('WRITER_SYMLINK', result.stderr)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

"""Byte/provenance controls only; never EDA qualification.

Upfront estimate<128MiB: seven tiny revision files, one64KiB hash buffer,
<2MiB protocol JSON, one worker. Run1GiB/60s CPU28–29. No geometry/model
oracle or reference tool is executed; fixtures explicitly cannot qualify a flow.
"""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130.foundry_first import protocol as p


class FullInputProtocol(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='foundry-first-protocol-')
        self.root = Path(self.temp.name)
        self.ws = self.root / 'road'
        self.rev = self.ws / 'revisions/rev4'
        self.rev.mkdir(parents=True)
        (self.ws / 'workspace.lock').touch()
        (self.ws / 'HEAD').write_text('rev4\n')
        for name in p.REV_FILES:
            (self.rev / name).write_bytes(b'protocol fixture; never physical evidence\n')
        self.header = dict(format_version=6, revision='rev4', parent_revision='rev3', stage='placed')
        self.metadata()
        self.argv = ['/qualified/protocol-only-engine', '--workspace', str(self.ws), '--threads', '16',
            '--max-expansions', '8000000', 'extract', '--extract-query-workers', '16',
            '--extract-query-batch-runs', '8', '--extract-query-scratch-mib', '32', '--rc-profile', str(self.root / 'rc.json'),
            '--corner', 'nom', '--out', str(self.root / 'source.spef'), '--max-spef-mib', '256',
            '--extract-memory-mib', '3584', '--max-extract-shapes', '4000000', '--max-rc-nodes', '2000000',
            '--max-rc-elements', '30000000', '--max-coupling-caps', '10000000',
            '--max-extract-work', '100000000000', '--max-extract-candidates', '65536', '--min-coupling-ff', '0.1']

    def tearDown(self):
        self.temp.cleanup()

    def metadata(self):
        (self.rev / 'design.json').write_text(json.dumps(dict(self.header, design={'protocol_only': True}), separators=(',', ':')))

    def test_complete_seven_file_copy_matches_only_by_full_bytes_and_real_revision(self):
        expected = p.snapshot(self.ws)
        import shutil
        clone = self.root / 'clone'
        shutil.copytree(self.ws, clone)
        self.assertEqual(expected, p.snapshot(clone))
        for name in p.REV_FILES:
            path = clone / 'revisions/rev4' / name
            old = path.read_bytes()
            path.write_bytes(old + b' ')
            self.assertNotEqual(expected, p.snapshot(clone), name)
            path.write_bytes(old)
        (clone / 'HEAD').write_text('rev5\n')
        with self.assertRaises((ValueError, FileNotFoundError)):
            p.snapshot(clone)

    def test_unknown_or_missing_actual_file_is_a_miss(self):
        (self.rev / 'unexpected.rbin').touch()
        with self.assertRaisesRegex(p.Miss, 'INVENTORY'):
            p.snapshot(self.ws)
        (self.rev / 'unexpected.rbin').unlink()
        (self.rev / 'pins.rbin').unlink()
        with self.assertRaisesRegex(p.Miss, 'INVENTORY'):
            p.snapshot(self.ws)

    def test_unknown_schema_and_noncanonical_or_nested_prefix_are_misses(self):
        self.header['format_version'] = 7
        self.metadata()
        with self.assertRaisesRegex(p.Miss, 'SCHEMA'):
            p.snapshot(self.ws)
        (self.rev / 'design.json').write_text('{"nested":{"format_version":6,"revision":"rev4","parent_revision":"rev3","stage":"placed"}}')
        with self.assertRaisesRegex(p.Miss, 'HEADER'):
            p.snapshot(self.ws)
        (self.rev / 'design.json').write_text('{"padding":"' + 'x' * 65536 + '","format_version":6}')
        with self.assertRaisesRegex(p.Miss, 'HEADER'):
            p.snapshot(self.ws)

    def test_revision_handle_and_metadata_revision_conflicts_reject(self):
        (self.ws / 'HEAD').write_text('../escape\n')
        with self.assertRaisesRegex(ValueError, 'HEAD_HANDLE'):
            p.snapshot(self.ws)
        (self.ws / 'HEAD').write_text('rev4\n')
        self.header['revision'] = 'rev5'
        self.metadata()
        with self.assertRaisesRegex(ValueError, 'REVISION_CONFLICT'):
            p.snapshot(self.ws)

    def test_symlinks_reject_before_resolution_including_ancestors(self):
        target = self.root / 'real-data'
        target.write_text('protocol fixture')
        path = self.rev / 'pins.rbin'
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'REGULAR|SYMLINK'):
            p.snapshot(self.ws)
        alias = self.root / 'alias'
        alias.symlink_to(self.ws, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'SYMLINK'):
            p.snapshot(alias)

    def test_shared_inode_is_not_claimed_immutable(self):
        path = self.rev / 'pins.rbin'
        os.link(path, self.root / 'mutable-alias')
        with self.assertRaisesRegex(p.Miss, 'SHARED_INODE'):
            p.snapshot(self.ws)

    def test_snapshot_total_is_admitted_before_hashing(self):
        with mock.patch.object(p, 'SNAPSHOT_CAP', 1), mock.patch.object(p, 'prefix', return_value=self.header):
            with self.assertRaisesRegex(p.Miss, 'BYTE_BUDGET'):
                p.snapshot(self.ws)

    def test_owned_copy_is_private_immutable_and_not_an_alias(self):
        source = self.root / 'source.json'
        source.write_text('{"protocol_only":true}\n')
        owner = self.root / 'effective'
        owner.mkdir()
        proof = p.freeze_file(source, owner / 'rc.json', owner, 1024)
        p.immutable(proof['effective'], owner, 1024)
        self.assertTrue(p.same_bytes(proof['source'], proof['effective']))
        self.assertNotEqual(source.stat().st_ino, (owner / 'rc.json').stat().st_ino)
        source.write_text('source may change; engines only read the frozen copy')
        p.immutable(proof['effective'], owner, 1024)
        with self.assertRaisesRegex(ValueError, 'EXISTS'):
            p.freeze_file(source, source, self.root, 1024)
        (owner / 'rc.json').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'NOT_FROZEN'):
            p.immutable(proof['effective'], owner, 1024)

    def test_gds_seal_rejects_shared_or_symlink_inode_before_chmod(self):
        outside = self.root / 'outside.gds'
        outside.write_bytes(b'protocol-only GDS')
        outside.chmod(0o644)
        owner = self.root / 'owner'
        owner.mkdir()
        owned = owner / 'alias.gds'
        os.link(outside, owned)
        with self.assertRaisesRegex(ValueError, 'SHARED_INODE'):
            p.seal_owned(owned, owner, 1024)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o644)
        owned.unlink()
        owned.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'SYMLINK'):
            p.seal_owned(owned, owner, 1024)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o644)
        owned.unlink()
        owned.write_bytes(b'private copy')
        value = p.seal_owned(owned, owner, 1024)
        p.immutable(value, owner, 1024)

    def test_only_two_path_tokens_are_normalized(self):
        actual = list(self.argv)
        actual[2] = '/private/clone'
        actual[actual.index('--out') + 1] = '/private/final.spef'
        self.assertEqual(p.extraction_request(self.argv), p.extraction_request(actual))
        for option in ['--rc-profile', '--threads', '--max-extract-work', '--min-coupling-ff']:
            changed = list(actual)
            changed[changed.index(option) + 1] += '-changed'
            self.assertNotEqual(p.extraction_request(self.argv), p.extraction_request(changed), option)
        with self.assertRaisesRegex(p.Miss, 'OPTION'):
            p.extraction_request(actual + ['--unknown', '1'])
        with self.assertRaisesRegex(p.Miss, 'DEFAULTS'):
            p.extraction_request(actual[:-2])

    def test_cache_match_is_strict_and_does_not_emit_a_report(self):
        snapshot = p.snapshot(self.ws)
        tool = {'path': self.argv[0], 'sha256': 'f47c5a7526855a4a5a106dd2ed42c03c74a2ace0ed48457b6942be1ec2d0110c', 'bytes': 58686880}
        profile = {'path': str(self.root / 'rc.json'), 'sha256': 'b' * 64, 'bytes': 5}
        environment = {'RAYON_NUM_THREADS': '16'}
        capture = dict(schema='rusteda.actual-full-extract-inputs.v1', snapshot=snapshot,
            tool=tool, profile=profile, environment=environment, argument_profile=p.argument_profile(self.argv, tool),
            command={'exit_code': 0, 'argv': self.argv})
        self.assertTrue(p.reuse_match(capture, snapshot, self.argv, tool, profile, environment))
        for key in ['snapshot', 'tool', 'profile', 'environment']:
            changed = copy.deepcopy(capture)
            if key == 'snapshot':
                changed[key]['files'][0]['sha256'] = '0' * 64
            elif key in {'tool', 'profile'}:
                changed[key]['sha256'] = '0' * 64
            else:
                changed[key] = dict(changed[key], changed='mismatch')
            self.assertFalse(p.reuse_match(changed, snapshot, self.argv, tool, profile, environment), key)
        for value in [True, 0.0, 1]:
            changed = copy.deepcopy(capture)
            changed['command']['exit_code'] = value
            with self.assertRaisesRegex(ValueError, 'FAILED'):
                p.reuse_match(changed, snapshot, self.argv, tool, profile, environment)

    def test_qualified_serial_defaults_are_bound_to_the_exact_elf_without_argv_changes(self):
        argv = list(self.argv)
        for option in ['--threads', '--extract-query-workers', '--extract-query-batch-runs', '--extract-query-scratch-mib']:
            index = argv.index(option)
            del argv[index:index+2]
        before = list(argv)
        tool = dict(path=argv[0], sha256='c2ae7177ad0a55ab4527bbe63b5c7f38fca91a31c50367cb2a2887bc0ce895cb', bytes=58259096)
        result = p.argument_profile(argv, tool)
        self.assertEqual(result['cli_threads'], 2)
        self.assertEqual(result['cli_threads_origin'], 'qualified_elf_default')
        self.assertEqual(result['queries'], 'qualified_serial_no_query_flags')
        p.extraction_request(argv, tool)
        self.assertEqual(argv, before)
        for change in [dict(tool, sha256='a' * 64), dict(tool, bytes=tool['bytes']+1)]:
            with self.assertRaisesRegex(p.Miss, 'UNREVIEWED'):
                p.extraction_request(argv, change)
        for value in [True, 2.0]:
            changed = copy.deepcopy(result)
            changed['cli_threads'] = value
            with self.assertRaisesRegex(ValueError, 'COUNTER'):
                p.validate_argument_profile(changed)
        result['other_omitted_defaults']['max_cells'] = 250000.0
        with self.assertRaisesRegex(ValueError, 'DEFAULT_TYPE'):
            p.validate_argument_profile(result)

    def test_inventory_overflow_stops_at_sixty_five_entries(self):
        original = Path.iterdir
        seen = []
        def entries(path):
            if path == self.rev:
                for i in range(1000000):
                    seen.append(i)
                    yield path / ('entry' + str(i))
            else:
                yield from original(path)
        with mock.patch.object(Path, 'iterdir', entries):
            with self.assertRaisesRegex(p.Miss, 'FILE_COUNT'):
                p.snapshot(self.ws)
        self.assertEqual(len(seen), 65)

    def test_destination_ancestor_symlink_rejects_before_any_file_creation(self):
        outside = self.root / 'outside'
        outside.mkdir()
        owner = self.root / 'owner'
        owner.mkdir()
        (owner / 'escape').symlink_to(outside, target_is_directory=True)
        source = self.root / 'input'
        source.write_text('immutable copy fixture')
        for action in [lambda: p.freeze_file(source, owner / 'escape/no-write', owner, 1024),
                       lambda: p.private_path(owner / 'escape/nested/no-write', owner, create_parents=True)]:
            with self.assertRaisesRegex(ValueError, 'SYMLINK'):
                action()
            self.assertEqual(list(outside.iterdir()), [])

    def test_extract_token_as_an_option_value_is_not_an_operation(self):
        argv = list(self.argv)
        argv.remove('extract')
        argv[argv.index('--corner') + 1] = 'extract'
        with self.assertRaisesRegex(p.Miss, 'OPERATION'):
            p.extraction_request(argv)

    def test_qualified_query_flag_names_and_explicit_global_threads_required(self):
        for option in ['--threads', '--extract-query-workers', '--extract-query-batch-runs', '--extract-query-scratch-mib']:
            argv = list(self.argv)
            index = argv.index(option)
            del argv[index:index+2]
            with self.assertRaisesRegex(p.Miss, 'DEFAULTS'):
                p.extraction_request(argv)
        argv = [v.replace('--extract-query-workers', '--extract-workers') for v in self.argv]
        with self.assertRaisesRegex(p.Miss, 'OPTION'):
            p.extraction_request(argv)

    def test_snapshot_counter_equality_never_accepts_boolean_or_float(self):
        snapshot = p.snapshot(self.ws)
        bad = copy.deepcopy(snapshot)
        bad['metadata']['format_version'] = 6.0
        with self.assertRaisesRegex(ValueError, 'METADATA'):
            p.validate_snapshot(bad)
        for value in [True, float(snapshot['files'][0]['bytes'])]:
            bad = copy.deepcopy(snapshot)
            bad['files'][0]['bytes'] = value
            with self.assertRaisesRegex(ValueError, 'COUNTER'):
                p.validate_snapshot(bad)

    def test_growing_inputs_stop_at_admitted_bytes_before_more_hash_copy_or_json_work(self):
        source = self.root / 'growing'
        source.write_bytes(b'abc')
        original_open = Path.open
        readers = []
        class Growing:
            def __init__(self, stream):
                self.stream, self.count = stream, 0
            def __enter__(self):
                readers.append(self)
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def read(self, count):
                self.count += count
                return b'x' * count
        def opened(path, *args, **kwargs):
            value = original_open(path, *args, **kwargs)
            return Growing(value) if path == source else value
        before = p.identity(source)
        with mock.patch.object(Path, 'open', opened):
            with self.assertRaisesRegex(ValueError, 'CHANGED'):
                p.identity(source, 1024)
            self.assertEqual(readers[-1].count, 4)
            with self.assertRaisesRegex(ValueError, 'JSON_BYTE'):
                p.read(source, 8)
            self.assertEqual(readers[-1].count, 9)
            with mock.patch.object(p, 'identity', return_value=before):
                with self.assertRaisesRegex(ValueError, 'CHANGED'):
                    p.freeze_file(source, self.root / 'partial-copy', self.root, 1024)
            self.assertEqual(readers[-1].count, 4)
        self.assertEqual((self.root / 'partial-copy').stat().st_size, 3)

    def test_opaque_profile_inventory_copies_exact_bytes_without_interpreting_code(self):
        source = self.root / 'prepared'
        target = source / 'klayout/tech/drc/opaque-fixture'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'opaque synthetic fixture, never executable reference code')
        profile = source / 'provenance.json'
        p.write_new(profile, dict(original_views_modified=False,
            files={'tech/drc/opaque-fixture': {'prepared_sha256': p.identity(target)['sha256']}}))
        owner = self.root / 'frozen'
        owner.mkdir()
        result = p.freeze_profile(p.identity(profile), owner / 'foundry', owner)
        self.assertEqual(p.read(result['effective']['path']), p.read(profile))
        self.assertEqual(len(result['files']), 1)
        self.assertTrue(p.same_bytes(result['files'][0]['source'], result['files'][0]['effective']))
        p.immutable(result['files'][0]['effective'], owner, 1024)
        self.assertNotEqual(target.stat().st_ino, Path(result['files'][0]['effective']['path']).stat().st_ino)

    def test_duplicate_json_nonfinite_and_boolean_policy_limits_reject(self):
        path = self.root / 'bad.json'
        for raw in ['{"a":1,"a":2}', '{"a":NaN}']:
            path.write_text(raw)
            with self.assertRaises(ValueError):
                p.read(path)
        for value in [True, float(p.POLICY['max_snapshot_files'])]:
            policy = dict(p.POLICY, max_snapshot_files=value)
            with self.assertRaises(ValueError):
                p.validate_policy(policy)


if __name__ == '__main__':
    unittest.main()

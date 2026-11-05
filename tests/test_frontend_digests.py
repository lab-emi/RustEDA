# SPDX-License-Identifier: Apache-2.0
"""SHA-256 memo and flow digest cache of the native frontend helpers, and the pin-view memo.

A digest is reused only while the file keeps its identity and only on a local file system; pinned
inputs outside the run tree are shared through <run>/frontend/sha256-cache.json, files inside the
run tree never are, an entry is never replaced, and validate's final check hashes every entry again."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import native_ecos as ecos
from integration.ibex_sg13g2_ez130 import native_frontend_inputs as fi


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FlowDigests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.root = base/'run'; (self.root/'frontend').mkdir(parents=True)
        self.pinned = base/'pdk.lib'; self.pinned.write_bytes(b'library (x) { }\n' * 1000)
        self.dynamic = self.root/'logs.stdout'; self.dynamic.write_bytes(b'{"ok": true}\n')
        self.fresh_process()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.fresh_process)

    def fresh_process(self):
        fi.MEMO.clear(); fi.FLOW = None; fi.FS_TYPES.clear()
        ecos.DIGEST_MEMO.clear(); ecos.DIGEST_FLOW = None; ecos.FILE_SYSTEMS.clear()
        # The test files' file system counts as local whatever it is (test_file_system_types
        # checks the classification itself).
        if hasattr(self, 'root'):
            dev = os.stat(self.root).st_dev
            fi.FS_TYPES[dev] = 'ext4'; ecos.FILE_SYSTEMS[dev] = 'ext4'

    def later(self):
        # Hash "two seconds later", so the files' status is old enough to be shared.
        now = time.time_ns() + 2_000_000_000
        return mock.patch.object(fi.time, 'time_ns', return_value=now)

    def test_pinned_inputs_are_shared_and_dynamic_ones_are_not(self):
        fi.open_flow(self.root)
        with self.later():
            self.assertEqual(fi.rec(self.pinned)['sha256'], sha(self.pinned))
            self.assertEqual(fi.rec(self.dynamic)['sha256'], sha(self.dynamic))
        fi.save_flow()
        cache = json.loads((self.root/'frontend/sha256-cache.json').read_text())
        self.assertEqual(cache['root'], str(self.root))
        self.assertEqual(set(cache['entries']), {str(self.pinned)})
        # The next process takes the pinned digest from the cache without reading the file, and
        # hashes the file inside the run tree again.
        self.fresh_process(); fi.open_flow(self.root)
        with mock.patch.object(fi.hashlib, 'file_digest', side_effect=AssertionError('hashed')):
            self.assertEqual(fi.rec(self.pinned)['sha256'], sha(self.pinned))
        calls = []
        real = hashlib.file_digest
        with mock.patch.object(fi.hashlib, 'file_digest', side_effect=lambda *a: calls.append(1) or real(*a)):
            self.assertEqual(fi.rec(self.dynamic)['sha256'], sha(self.dynamic))
        self.assertEqual(len(calls), 1)
        # A cached digest of a file whose status just changed is not written.
        self.fresh_process(); fi.open_flow(self.root)
        other = self.pinned.with_name('new.lib'); other.write_bytes(b'x')
        fi.rec(other); fi.save_flow()
        cache = json.loads((self.root/'frontend/sha256-cache.json').read_text())
        self.assertNotIn(str(other), cache['entries'])

    def test_a_changed_file_is_hashed_again(self):
        fi.open_flow(self.root)
        with self.later():
            before = fi.rec(self.pinned)['sha256']
        fi.save_flow()
        # The same size, other bytes: the identity (mtime, ctime) changes.
        data = bytearray(self.pinned.read_bytes()); data[0] ^= 1
        old = os.stat(self.pinned).st_mtime_ns
        self.pinned.write_bytes(bytes(data))
        # Whatever the file system's timestamp granularity, the write is seen.
        os.utime(self.pinned, ns=(old + 1_000_000_000, old + 1_000_000_000))
        for _ in range(2):
            self.fresh_process(); fi.open_flow(self.root)
            self.assertEqual(fi.rec(self.pinned)['sha256'], sha(self.pinned))
            self.assertNotEqual(fi.rec(self.pinned)['sha256'], before)
        # A replacement by rename (another inode) is hashed again within one process too.
        self.pinned.with_name('tmp').write_bytes(b'replaced')
        os.replace(self.pinned.with_name('tmp'), self.pinned)
        self.assertEqual(fi.rec(self.pinned)['sha256'], sha(self.pinned))

    def test_a_same_size_write_that_restores_the_times_is_hashed_again(self):
        # Size, inode and mtime stay; only ctime, which no user can set, tells the write.
        fi.open_flow(self.root)
        with self.later():
            before = fi.rec(self.pinned)['sha256']
        fi.save_flow()
        self.assertEqual(ecos.sha(self.pinned), before)
        old = os.stat(self.pinned)
        time.sleep(0.05)  # past any coarse timestamp tick
        data = bytearray(self.pinned.read_bytes()); data[0] ^= 1
        with self.pinned.open('r+b') as stream:
            stream.write(bytes(data))
        os.utime(self.pinned, ns=(old.st_atime_ns, old.st_mtime_ns))
        new = os.stat(self.pinned)
        self.assertEqual((new.st_ino, new.st_size, new.st_mtime_ns), (old.st_ino, old.st_size, old.st_mtime_ns))
        self.assertNotEqual(new.st_ctime_ns, old.st_ctime_ns)
        # The same process, then a new one with the flow cache, for both helpers.
        self.assertEqual(fi.rec(self.pinned)['sha256'], sha(self.pinned))
        self.assertEqual(ecos.sha(self.pinned), sha(self.pinned))
        self.fresh_process(); fi.open_flow(self.root)
        ecos.load_digest_cache(self.root/'frontend/sha256-cache.json')
        self.assertIsNotNone(ecos.DIGEST_FLOW)
        self.assertNotEqual(sha(self.pinned), before)
        self.assertEqual(fi.rec(self.pinned)['sha256'], sha(self.pinned))
        self.assertEqual(ecos.sha(self.pinned), sha(self.pinned))

    def test_entries_stay_and_the_final_check_hashes_them_again(self):
        fi.open_flow(self.root)
        with self.later():
            good = fi.rec(self.pinned)['sha256']
        fi.save_flow()
        path = self.root/'frontend/sha256-cache.json'
        # A later process never replaces an entry, whatever it holds for the path.
        self.fresh_process(); fi.open_flow(self.root)
        fi.FLOW['entries'][str(self.pinned)] = dict(identity=[0, 0, 0, 0, 0], sha256='1' * 64); fi.FLOW['dirty'] = True
        fi.save_flow()
        self.assertEqual(json.loads(path.read_text())['entries'][str(self.pinned)]['sha256'], good)
        # validate's check passes on a true cache and records it.
        self.fresh_process(); fi.open_flow(self.root)
        out = self.root/'result'; out.mkdir()
        fi.check_flow(out)
        record = json.loads((out/'digest-check.json').read_text())
        self.assertEqual(record['checked'], {str(self.pinned): good})
        self.assertEqual(record['cache'], dict(path=str(path), sha256=sha(path), bytes=path.stat().st_size))
        # A forged entry with the file's identity is served (as designed), and the final check
        # finds it.
        data = json.loads(path.read_text()); data['entries'][str(self.pinned)]['sha256'] = '0' * 64
        path.write_text(json.dumps(data))
        self.fresh_process(); fi.open_flow(self.root)
        self.assertEqual(fi.rec(self.pinned)['sha256'], '0' * 64)
        (out/'digest-check.json').unlink()
        with self.assertRaisesRegex(ValueError, 'differs from the files'):
            fi.check_flow(out)
        # So is a cached file that is gone, and a cache file that is not one.
        data['entries'][str(self.pinned)]['sha256'] = good
        data['entries'][str(self.root.parent/'gone.lib')] = dict(identity=[1, 2, 3, 4, 5], sha256='2' * 64)
        path.write_text(json.dumps(data))
        self.fresh_process(); fi.open_flow(self.root)
        with self.assertRaisesRegex(ValueError, 'gone.lib'):
            fi.check_flow(out)
        path.write_text(json.dumps(dict(schema='other', root=str(self.root), entries={})))
        self.fresh_process(); fi.open_flow(self.root)
        with self.assertRaisesRegex(ValueError, 'malformed'):
            fi.check_flow(out)

    def test_files_on_other_file_systems_are_hashed_every_time(self):
        fi.open_flow(self.root)
        dev = os.stat(self.pinned).st_dev
        fi.FS_TYPES[dev] = 'nfs'; ecos.FILE_SYSTEMS[dev] = 'nfs'
        calls = []
        real = hashlib.file_digest
        count = lambda *a: calls.append(1) or real(*a)
        with self.later(), mock.patch.object(fi.hashlib, 'file_digest', side_effect=count):
            for _ in range(2):
                self.assertEqual(fi.rec(self.pinned)['sha256'], sha(self.pinned))
                self.assertEqual(ecos.sha(self.pinned), sha(self.pinned))
        self.assertEqual(len(calls), 4)
        fi.save_flow()
        self.assertFalse((self.root/'frontend/sha256-cache.json').exists(), 'nothing shared')

    def test_file_system_types(self):
        mountinfo = Path(self.temp.name)/'mountinfo'
        mountinfo.write_text('22 1 254:0 / / rw,relatime shared:1 - ext4 /dev/vda rw\n'
                             '30 22 0:51 / /net rw - nfs4 server:/x rw\n'
                             '31 22 0:52 / /merged rw - overlay overlay rw\n')
        stat = lambda major, minor: mock.Mock(st_dev=os.makedev(major, minor))
        for module, local, types in [(fi, fi.local, fi.FS_TYPES), (ecos, ecos.local_file, ecos.FILE_SYSTEMS)]:
            types.clear()
            with mock.patch.object(module, 'MOUNTINFO', str(mountinfo)):
                self.assertEqual([local(stat(*d)) for d in [(254, 0), (0, 51), (0, 52), (9, 9)]], [True, False, True, False])
            types.clear()
            with mock.patch.object(module, 'MOUNTINFO', str(mountinfo.with_name('missing'))):
                self.assertFalse(local(stat(254, 0)))
            types.clear()
        # The real table: /proc itself is not a local file system.
        self.assertFalse(fi.local(os.stat('/proc/self/mountinfo')))

    def test_malformed_or_foreign_caches_are_ignored(self):
        path = self.root/'frontend/sha256-cache.json'
        stat = os.stat(self.pinned)
        wrong = dict(identity=fi.identity(stat), sha256='0' * 64)
        for text in ['not json', json.dumps(dict(schema='other', root=str(self.root), entries={str(self.pinned): wrong})),
                     json.dumps(dict(schema=fi.FLOW_SCHEMA, root='/elsewhere', entries={str(self.pinned): wrong})),
                     json.dumps(dict(schema=fi.FLOW_SCHEMA, root=str(self.root), entries={str(self.pinned): dict(identity=[1], sha256='0' * 64)}))]:
            path.write_text(text)
            self.fresh_process(); fi.open_flow(self.root)
            self.assertEqual(fi.rec(self.pinned)['sha256'], sha(self.pinned), text[:40])
            ecos.load_digest_cache(path)
            self.assertTrue(ecos.DIGEST_FLOW is None or str(self.pinned) not in ecos.DIGEST_FLOW['entries'], text[:40])
            self.assertEqual(ecos.sha(self.pinned), sha(self.pinned))

    def test_the_planner_reads_the_cache_and_rechecks_in_full(self):
        fi.open_flow(self.root)
        with self.later():
            fi.rec(self.pinned)
        fi.save_flow(); self.fresh_process()
        ecos.load_digest_cache(self.root/'frontend/sha256-cache.json')
        self.assertIsNotNone(ecos.DIGEST_FLOW)
        with mock.patch.object(ecos.hashlib, 'file_digest', side_effect=AssertionError('hashed')):
            self.assertEqual(ecos.sha(self.pinned), sha(self.pinned))
        # The final input check reads the file whatever the memo holds.
        ecos.DIGEST_MEMO[str(self.pinned)] = (fi.identity(os.stat(self.pinned)), '0' * 64)
        self.assertEqual(ecos.sha(self.pinned), '0' * 64)
        self.assertEqual(ecos.sha_fresh(self.pinned), sha(self.pinned))
        # A cache somewhere else than <root>/frontend/sha256-cache.json is not used.
        moved = self.root/'cache.json'; moved.write_text((self.root/'frontend/sha256-cache.json').read_text())
        self.fresh_process(); ecos.load_digest_cache(moved)
        self.assertIsNone(ecos.DIGEST_FLOW)


class PinViews(unittest.TestCase):
    def test_merged_views_are_computed_once_and_copied_out(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        rows = {'tt': 'BUF\tA\tInput\t0.002\tnone\t1\tfalse\tnone\nBUF\tY\tOutput\tnone\t0.1\t0\tfalse\t8\n',
                'ss': 'BUF\tA\tInput\t0.003\tnone\t1\tfalse\tnone\nBUF\tY\tOutput\tnone\t0.08\t0\tfalse\t6\n'}
        corners = {}
        for corner, text in rows.items():
            table = Path(temp.name)/f'{corner}.tsv'; table.write_text(text)
            lib = Path(temp.name)/f'{corner}.lib'; lib.write_text(corner)
            corners[corner] = dict(pin_tables=[dict(path=table.name, sha256=sha(table), library=dict(path=lib.name, sha256=sha(lib)))])
        manifest = Path(temp.name)/'inputs.json'
        manifest.write_text(json.dumps(dict(schema='rusteda.native-eco-inputs.v1', corners=corners)))
        args = mock.Mock(max_work=10000, max_connections=1000)
        pins = ecos.pin_views(ecos.Inputs(manifest, args), ['tt', 'ss'])
        a = pins('BUF', 'A'); a['cap'] = -1
        self.assertEqual(pins('BUF', 'A'), dict(cap=0.003, limit=None, fanout=1.0, clock=False, direction='Input', max_fanout=float('inf')))
        self.assertEqual(pins('BUF', 'Y'), dict(cap=None, limit=0.08, fanout=0.0, clock=False, direction='Output', max_fanout=6.0))
        with self.assertRaises(ecos.Failure):
            pins('BUF', 'Z')


if __name__ == '__main__':
    unittest.main()

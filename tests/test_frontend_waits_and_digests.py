#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Exit-woken child waits (producer, ECO helper) and the threaded digests of the frontend helpers.

The waits must return at a child's exit, not at the next polling tick, and still time out; the
threaded digests must be exactly the serial ones (every file read in full), leave the same memo
and flow-cache entries as serial hashing, and let a missing file fail where the serial code reads
it. Bounded: a handful of short-lived children and files of at most 4 MiB.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import native_ecos as ecos
from integration.ibex_sg13g2_ez130 import native_frontend_inputs as fi
from integration.ibex_sg13g2_ez130 import producer


def sleeper(seconds):
    return subprocess.Popen([sys.executable, '-c', f'import time; time.sleep({seconds})'])


class ExitWaits(unittest.TestCase):
    def test_producer_wait_returns_at_exit_and_times_out_while_running(self):
        process = sleeper(.05)
        waiter = producer.exit_waiter(process)
        try:
            started = time.monotonic()
            code = None
            while code is None:
                code = producer.wait_exit(process, waiter, 5)
            self.assertEqual(code, 0)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            if waiter is not None:
                os.close(waiter)
        process = sleeper(5)
        waiter = producer.exit_waiter(process)
        try:
            started = time.monotonic()
            self.assertIsNone(producer.wait_exit(process, waiter, .1))
            self.assertLess(time.monotonic() - started, 1)
        finally:
            process.kill(); process.wait()
            if waiter is not None:
                os.close(waiter)

    def test_eco_child_wait_returns_at_exit_and_times_out(self):
        started = time.monotonic()
        self.assertEqual(ecos.wait_child(sleeper(.05), 5), 0)
        self.assertLess(time.monotonic() - started, 2)
        process = sleeper(5)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                ecos.wait_child(process, .1)
        finally:
            process.kill(); process.wait()


class ThreadedDigests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root = base / 'run'; (self.root / 'frontend').mkdir(parents=True)
        self.files = []
        for index in range(6):
            path = (base if index % 2 else self.root) / f'f{index}.bin'
            path.write_bytes(os.urandom(1 << 20) * (index % 4 + 1))
            self.files.append(path)
        fi.MEMO.clear(); fi.FLOW = None
        self.addCleanup(fi.MEMO.clear)
        self.addCleanup(setattr, fi, 'FLOW', None)

    def test_fresh_digests_are_the_full_serial_digests(self):
        digests = ecos.fresh_digests([str(p) for p in self.files] + [str(self.files[0])])
        self.assertEqual(list(digests), [str(p) for p in self.files])
        for path in self.files:
            self.assertEqual(digests[str(path)], hashlib.sha256(path.read_bytes()).hexdigest())
        missing = self.root / 'missing.bin'
        with self.assertRaises(OSError):
            ecos.fresh_digests([str(self.files[0]), str(missing)])

    def test_prefetch_leaves_what_serial_hashing_leaves(self):
        # The test files' file system counts as local, and they are hashed "two seconds later", so
        # that pinned ones may be shared (test_frontend_digests checks both rules themselves).
        fi.FS_TYPES[os.stat(self.root).st_dev] = 'ext4'
        self.addCleanup(fi.FS_TYPES.clear)
        later = mock.patch.object(fi.time, 'time_ns', return_value=time.time_ns() + 2_000_000_000)
        with later:
            fi.open_flow(self.root)
            fi.prefetch(self.files + [self.root / 'missing.bin'])
            threaded = (dict(fi.MEMO), dict(fi.FLOW['entries']))
            fi.MEMO.clear(); fi.open_flow(self.root)
            for path in self.files:
                fi.digest(path)
        self.assertEqual(threaded, (dict(fi.MEMO), dict(fi.FLOW['entries'])))
        # Pinned inputs outside the run tree are shared through the flow cache, run-tree files not.
        self.assertEqual(set(threaded[1]), {str(p) for p in self.files if not p.is_relative_to(self.root)})
        with self.assertRaises(OSError):
            fi.rec(self.root / 'missing.bin')


if __name__ == '__main__':
    unittest.main()

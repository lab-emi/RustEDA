#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Unit tests of the producer's foundry-check parallel groups (`integration/ibex_sg13g2_ez130/producer.py`).

`producer.signoff_parallel_group(threads)` is the sixteen-CPU B4 group by default and a two-lane
group over the foundry checks on a four-CPU allocation; `native_flow.check_parallel_groups`
accepts a backend's groups only when they are the group for that backend's own allocation.
"""
from __future__ import annotations

import copy
import unittest

from integration.ibex_sg13g2_ez130 import native_flow, producer

B4 = {'be_rcx_inputs', 'be_openrcx', 'be_timing_inputs', 'be_timing', 'be_main', 'be_antenna', 'be_lvs_inputs',
      'be_lvs'}


def command(tag):
    return dict(id=tag, kind='command', tool='python', arguments=['{files:producer}', tag], timeout_s=1800)


def render(tag):
    return dict(id=tag, kind='render', destination=tag + '.json', content={})


# The backend tail in manifest order (native_backend.py).
STEPS = [command('be_gds'), command('be_extract'), render('be_rcx_inputs'), command('be_openrcx'),
         render('be_timing_inputs'), command('be_timing'), command('be_main'), command('be_antenna'),
         render('be_lvs_inputs'), command('be_lvs'), command('be_macro_contract')]


def scope(threads):
    return dict(threads=threads, cpu_affinity=list(range(threads)), memory_mib=8192)


class Groups(unittest.TestCase):
    def test_default_is_the_sixteen_cpu_b4_group(self):
        group = producer.signoff_parallel_group()
        self.assertEqual(group, producer.signoff_parallel_group(16))
        self.assertEqual(producer.signoff_parallel_group(6), group)
        self.assertEqual(set(group['steps']), B4)
        self.assertEqual(group['max_parallel'], 3)
        producer.validate_parallel_groups(STEPS, [group], scope(16))

    def test_four_cpu_group_runs_main_beside_antenna_then_lvs(self):
        group = producer.signoff_parallel_group(4)
        self.assertEqual(producer.signoff_parallel_group(5), group)
        self.assertEqual(group['max_parallel'], 2)
        steps = group['steps']
        self.assertEqual(set(steps), {'be_main', 'be_antenna', 'be_lvs_inputs', 'be_lvs'})
        self.assertEqual(steps['be_main']['cpu_slots'], [0, 1])
        self.assertEqual(steps['be_antenna']['cpu_slots'], steps['be_lvs']['cpu_slots'])
        self.assertTrue(set(steps['be_main']['cpu_slots']).isdisjoint(steps['be_antenna']['cpu_slots']))
        self.assertIn('be_antenna', steps['be_lvs']['depends_on'])
        # Every pair that can run together stays under the scheduling cap.
        live = steps['be_main']['memory_mib'] + max(steps['be_antenna']['memory_mib'], steps['be_lvs']['memory_mib'])
        self.assertLessEqual(live, group['max_memory_mib'])
        producer.validate_parallel_groups(STEPS, [group], scope(4))
        with self.assertRaises(ValueError):
            producer.validate_parallel_groups(STEPS, [group], scope(2))
        with self.assertRaises(ValueError):
            producer.validate_parallel_groups(STEPS, [producer.signoff_parallel_group()], scope(4))

    def test_native_flow_accepts_only_the_group_of_the_backend_allocation(self):
        for threads in (4, 16):
            backend = dict(scope=scope(threads), parallel_groups=[producer.signoff_parallel_group(threads)])
            native_flow.check_parallel_groups(backend, STEPS, producer)
        for threads, other in ((4, 16), (16, 4)):
            backend = dict(scope=scope(threads), parallel_groups=[producer.signoff_parallel_group(other)])
            with self.assertRaisesRegex(Exception, 'concurrency differs'):
                native_flow.check_parallel_groups(backend, STEPS, producer)
        backend = dict(scope=scope(4), parallel_groups=[copy.deepcopy(producer.signoff_parallel_group(4))])
        backend['parallel_groups'][0]['steps']['be_main']['memory_mib'] += 1
        with self.assertRaisesRegex(Exception, 'concurrency differs'):
            native_flow.check_parallel_groups(backend, STEPS, producer)


if __name__ == '__main__':
    unittest.main()

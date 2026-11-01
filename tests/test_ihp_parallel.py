"""Bounded process-level scheduler controls; no EDA correctness claim.

Estimate: <=4 children x <32 MiB, <=12 tiny files; no PDKs, paths or cuts.
Run with one test worker under a 1 GiB memory / 60 second wall guard.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import producer, signoff_lvs


class ParallelSignoffProtocol(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / 'output'
        self.script = Path(producer.__file__).resolve()
        physical, self.cpus = set(), []
        for cpu in sorted(os.sched_getaffinity(0)):
            topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
            key = tuple((topology / name).read_text() for name in ('physical_package_id', 'core_id'))
            if key not in physical:
                physical.add(key)
                self.cpus.append(cpu)
            if len(self.cpus) == 2:
                break
        self.timer = Path(os.environ.get('RUSTEDA_TEST_GNU_TIME', '/usr/bin/time'))
        if len(self.cpus) != 2 or not self.timer.is_file():
            self.skipTest('two physical CPUs and GNU time required; scheduler execution unverified')
        self.group = dict(max_parallel=2, max_memory_mib=128, steps={
            'first': dict(depends_on=[], cpu_slots=[0], memory_mib=32),
            'second': dict(depends_on=[], cpu_slots=[1], memory_mib=32)})
        self.data = dict(schema='rusteda.ihp-flow.v1', name='protocol_only', pdk='ihp-sg13g2',
            library='ez130_8t', corners=['tt', 'ss'],
            files={'producer': producer.identity(self.script)},
            tools={'python': producer.identity(Path(sys.executable)),
                   'rustroad': producer.identity(Path(sys.executable)),
                   'gnu_time': producer.identity(self.timer)},
            scope=dict(threads=2, cpu_affinity=self.cpus, memory_mib=256, max_log_bytes=8192),
            steps=[self.command('first'), self.command('second'),
                   dict(id='after', kind='render', destination='control.json', content={'protocol_only': True})],
            parallel_groups=[self.group], gates={name: 'control.json' for name in producer.GATES}, artifacts={})

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def command(tag, code=None):
        if code is None:
            code = ("import json,os,time; from pathlib import Path; "
                    f"Path('{tag}.start').write_text(str(time.monotonic())); "
                    "time.sleep(.15); "
                    f"Path('{tag}.json').write_text(json.dumps(dict(cpus=sorted(os.sched_getaffinity(0)),"
                    "ended=time.monotonic())))")
        return dict(id=tag, kind='command', tool='python', arguments=['-c', code], timeout_s=5)

    def run_worker(self, *extra):
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps(self.data))
        result = subprocess.run([sys.executable, str(self.script), '--project', str(self.root),
            '--manifest', str(manifest), '--workspace', str(self.output), '--base-rev', 'empty',
            '--pdk', 'ihp', '--rustroad', sys.executable, '--gnu-time', str(self.timer), *extra],
            env=dict(os.environ, RUSTEDA_WORKER_ID='a' * 64, RUSTEDA_OWNERSHIP=str(self.root / 'owner')),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        return result

    def report(self, tag):
        return json.loads((self.output / f'{tag}.json').read_text())

    def test_ready_children_overlap_with_disjoint_inherited_cpu_sets(self):
        result = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first, second = self.report('first'), self.report('second')
        starts = [float((self.output / f'{tag}.start').read_text()) for tag in ('first', 'second')]
        self.assertLess(max(starts), min(first['ended'], second['ended']))
        self.assertEqual(first['cpus'], self.cpus[:1])
        self.assertEqual(second['cpus'], self.cpus[1:])
        receipts = json.loads((self.output / 'step-receipts.json').read_text())
        self.assertEqual([row['id'] for row in receipts], ['first', 'second', 'after'])

    def test_dependencies_wait_for_successful_producer(self):
        self.group['steps']['second']['depends_on'] = ['first']
        result = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreater(float((self.output / 'second.start').read_text()), self.report('first')['ended'])

    def test_summed_memory_reservations_serialize_children(self):
        for request in self.group['steps'].values():
            request['memory_mib'] = 80
        result = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreater(float((self.output / 'second.start').read_text()), self.report('first')['ended'])

    def test_shared_cpu_lane_serializes_children(self):
        self.group['steps']['second']['cpu_slots'] = [0]
        result = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreater(float((self.output / 'second.start').read_text()), self.report('first')['ended'])

    def test_failure_cancels_owned_descendants_and_never_runs_qualification(self):
        self.data['steps'][0] = self.command('first', 'import time; time.sleep(.2); raise SystemExit(7)')
        child = "from pathlib import Path; import time; time.sleep(1); Path('escaped').touch()"
        self.data['steps'][1] = self.command('second',
            f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(5)')
        result = self.run_worker()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.output / 'control.json').exists())
        time.sleep(1)
        self.assertFalse((self.output / 'escaped').exists())
        summary = json.loads((self.output / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'failed')
        self.assertIn('exit=7', summary['error'])

    def test_failed_dependency_never_launches_its_dependent(self):
        self.group['steps']['second']['depends_on'] = ['first']
        self.data['steps'][0] = self.command('first', 'raise SystemExit(7)')
        result = self.run_worker()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.output / 'second.start').exists())
        self.assertFalse((self.output / 'control.json').exists())

    def test_deadline_cancels_parallel_children_and_blocks_following_steps(self):
        self.data['steps'][0] = self.command('first', 'import time; time.sleep(5)')
        self.data['steps'][0]['timeout_s'] = 1
        result = self.run_worker()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('step deadline exceeded: first', (self.output / 'summary.json').read_text())
        self.assertFalse((self.output / 'control.json').exists())

    def test_aggregate_log_budget_counts_simultaneous_and_fast_exit_streams(self):
        self.data['scope']['max_log_bytes'] = 1024
        for index, tag in enumerate(('first', 'second')):
            self.data['steps'][index] = self.command(tag, "print('x' * 600)")
        result = self.run_worker()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('aggregate log byte budget', (self.output / 'summary.json').read_text())

    def test_stop_after_inside_group_remains_a_serial_prefix(self):
        result = self.run_worker('--stop-after', 'first')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.output / 'first.json').exists())
        self.assertFalse((self.output / 'second.start').exists())
        self.assertEqual(json.loads((self.output / 'summary.json').read_text())['status'], 'frontend_completed')

    def test_invalid_graph_and_resource_requests_fail_before_launch(self):
        original = copy.deepcopy(self.group)
        mutations = [lambda g: g['steps']['first'].update(depends_on=['second']),
                     lambda g: g['steps']['first'].update(cpu_slots=[2]),
                     lambda g: g['steps']['first'].update(memory_mib=129),
                     lambda g: g.update(max_memory_mib=256),
                     lambda g: g['steps'].update(missing=dict(depends_on=[]))]
        for mutate in mutations:
            group = copy.deepcopy(original)
            mutate(group)
            with self.subTest(group=group), self.assertRaises(ValueError):
                producer.validate_parallel_groups(self.data['steps'], [group], self.data['scope'])

    def test_lvs_inventory_excludes_sibling_parallel_lanes(self):
        owner = dict(cgroup='/owned', parallel_step='be_lvs', cpu_affinity=[4, 6])
        def affinity(pid):
            if pid == 4:
                raise ProcessLookupError()
            return {1: {4, 6}, 2: {0, 2}, 3: {4}}[pid]
        with mock.patch.object(signoff_lvs, 'cgroup_pids', return_value={1, 2, 3, 4}), \
                mock.patch.object(signoff_lvs.os, 'sched_getaffinity', side_effect=affinity):
            self.assertEqual(signoff_lvs.owned_pids(owner), {1, 3})
            self.assertEqual(signoff_lvs.owned_pids(dict(cgroup='/owned')), {1, 2, 3, 4})


if __name__ == '__main__':
    unittest.main()

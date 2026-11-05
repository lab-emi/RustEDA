#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""The frontend prefix as one producer parallel group on a four-CPU allocation
(`native_frontend.recipe` / `frontend_parallel_group`).

The dependencies are written next to each step in the composer. This test derives the files each
step writes and reads from its arguments (and from the helpers' fixed file conventions) on its
own, and checks that every step transitively depends on the last earlier writer of everything it
reads, and that a step writing a workspace depends on every earlier step that used it: the graph
can only run a step after the steps it would have followed in the serial prefix whose files it
touches. No EDA tool runs.
"""
from __future__ import annotations

import copy
import unittest

from integration.ibex_sg13g2_ez130 import native_flow
from integration.ibex_sg13g2_ez130 import native_frontend as fe
from integration.ibex_sg13g2_ez130 import producer

OUT = '{output}/'
# Commands that commit a new revision of the workspace they name (the others only read it).
WRITERS = {('rusteda', 'init'), ('rusteda', 'import'), ('ruststa', 'run'), ('rustsys', 'synth'),
           ('rustroad', 'import-pdk'), ('rustroad', 'place'), ('rustroad', 'clock-tree')}
READERS = {('rusteda', 'export'), ('ruststa', 'report'), ('rustroad', 'export-verilog'),
           ('rustroad', 'export-def'), ('rustroad', 'export-placement-seed'), ('rustroad', 'inspect')}
# Shared by design: the Liberty compile cache (files written under temporary names and renamed) and
# the helpers' flow digest cache (merged under a lock; an entry is never replaced).
SHARED = {'frontend/lib-cache', 'frontend/sha256-cache.json'}


def config():
    return dict(files={k: dict(path='/x/' + k, sha256='0' * 64, bytes=1) for k in fe.FILE_KEYS},
                tools={k: dict(path='/x/' + k, sha256='0' * 64, bytes=1) for k in fe.TOOL_KEYS},
                scope=dict(threads=4, cpu_affinity=[0, 1, 2, 3], memory_mib=8192, max_log_bytes=64 << 20,
                           max_stdout_artifact_bytes=1536 << 20),
                selection=dict(top='top'))


def value(args, *flags):
    for flag in flags:
        if flag in args:
            return args[args.index(flag) + 1]
    return None


def rel(path):
    return path[len(OUT):] if path.startswith(OUT) else None


def uses(step):
    """(written, read): run-tree paths relative to {output}."""
    if step['kind'] == 'render':
        return {step['destination']}, set()
    tag, tool, args = step['id'], step['tool'], step['arguments']
    written, read = {'logs/' + tag + '.stdout'}, set()
    paths = {rel(a) for a in args if rel(a)}
    workspace = rel(value(args, '-w', '--workspace') or '')
    if tool == 'python':
        written |= {workspace} | ({rel(value(args, '--eco-workspace'))} if '--eco-workspace' in args else set())
        read |= paths - written
        command = args[1] if args[0] == '{files:prepare}' else None
        if command == 'bundle':
            op, timing, graph = value(args, '--operation'), value(args, '--timing-tag'), rel(value(args, '--graph'))
            read |= {graph, 'frontend/profiles', 'frontend/sta-' + timing}
            read |= {f'logs/fe_caps_{c}_{k}.stdout' for c in ('tt', 'ss') for k in ('std', 'ram')}
            read |= {f'logs/fe_{timing}_{c}_drv.stdout' for c in ('tt', 'ss')}
            if op in ('electrical', 'hold'):
                read |= {f'logs/fe_{timing}_seed.stdout', f'logs/fe_{timing}_def.stdout', 'frontend/estimate-' + timing,
                         f'logs/fe_{timing}_hold.stdout'} | {f'logs/fe_{timing}_{c}_endpoints.stdout' for c in ('tt', 'ss')}
            if op == 'hold':
                read.add('frontend/eco-electrical')
            if op != 'reset':
                read.add('frontend/bundle-reset')
        elif command == 'pins':
            read.add('frontend/physical')
    elif tool == 'pin_caps':
        read |= paths                             # pinned Liberty only; the table goes to stdout
    else:
        sub = next((a for a in args if (tool, a) in WRITERS | READERS), None)
        assert sub is not None, step
        (written if (tool, sub) in WRITERS else read).add(workspace)
        if (tool, sub) == ('rusteda', 'export'):  # the netlist or graph it exports
            written.add(rel(value(args, '--netlist')))
        read |= paths - {workspace} - written
    return {p for p in written if p} - SHARED, {p for p in read if p} - SHARED


def overlaps(a, b):
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


class FrontendGroup(unittest.TestCase):
    def setUp(self):
        self.steps, self.lanes = fe.recipe(config(), True)
        self.group = fe.frontend_parallel_group(self.steps, self.lanes)

    def test_group_is_a_valid_producer_group_of_the_whole_prefix_but_validate(self):
        steps, _ = producer.declare_stdout_artifacts(copy.deepcopy(self.steps))
        producer.validate_parallel_groups(steps, [self.group], config()['scope'])
        self.assertEqual(list(self.group['steps']), [s['id'] for s in self.steps][:-1])
        self.assertEqual(self.steps[-1]['id'], 'fe_frontend_validate')
        self.assertLessEqual(self.group['max_parallel'], 4)
        # Any four members fit the scheduling cap together.
        biggest = sorted((r.get('memory_mib', 0) for r in self.group['steps'].values()), reverse=True)[:4]
        self.assertLessEqual(sum(biggest), self.group['max_memory_mib'])

    def closure(self):
        """{tag: every step it waits for, directly or through others}."""
        deps = {tag: set(req['depends_on']) for tag, req in self.group['steps'].items()}
        closure = {}
        for tag in self.group['steps']:          # manifest order: dependencies come first
            closure[tag] = set(deps[tag]).union(*(closure[d] for d in deps[tag]))
        return closure

    def test_every_step_follows_the_writers_of_what_it_reads(self):
        closure = self.closure()
        history = []                              # (tag, written, read) of the earlier steps
        for step in self.steps[:-1]:
            written, read = uses(step)
            for earlier, w, r in history:
                touches = any(overlaps(p, q) for p in read for q in w)          # read after write
                touches |= any(overlaps(p, q) for p in written for q in w | r)   # write after write/read
                if touches:
                    self.assertIn(earlier, closure[step['id']], f"{step['id']} must follow {earlier}")
            history.append((step['id'], written, read))

    def test_steps_that_may_run_together_never_touch_what_the_other_writes(self):
        # The same rule stated over the pairs the graph leaves unordered (they may run at once):
        # neither writes a path the other reads or writes. The timing tools commit a workspace
        # under an exclusive lock, and the ECO helpers read an STA workspace's reports under a
        # non-blocking shared lock (native_ecos.Inputs.report fails at once instead of waiting),
        # so this is what keeps a concurrent schedule from failing on, or reading, a workspace
        # another step is writing.
        closure, used = self.closure(), {s['id']: uses(s) for s in self.steps[:-1]}
        tags = list(used)
        for i, a in enumerate(tags):
            for b in tags[i + 1:]:
                if a in closure[b] or b in closure[a]:
                    continue
                (wa, ra), (wb, rb) = used[a], used[b]
                clash = [(p, q) for p in wa for q in wb | rb if overlaps(p, q)]
                clash += [(p, q) for p in wb for q in ra if overlaps(p, q)]
                self.assertEqual(clash, [], f'{a} and {b} may run together')
        # In particular, every step that writes the STA workspace an ECO round reads is done before
        # the round starts.
        for op, tag in (('reset', 'mapped'), ('fanout', 'reset'), ('primary-clock', 'fanout'),
                        ('electrical', 'temporary'), ('hold', 'repair')):
            eco = 'fe_eco_' + op
            self.assertEqual(value(next(s['arguments'] for s in self.steps if s['id'] == eco), '--timing-tag'), tag)
            writers = {t for t, (w, _) in used.items() if 'frontend/sta-' + tag in w}
            self.assertEqual({'fe_' + tag + '_init', 'fe_' + tag + '_import', 'fe_' + tag + '_run'}, writers)
            self.assertLessEqual(writers, closure[eco])

    def test_two_corner_sta_runs_take_all_four_cpus_and_the_rest_keep_their_arguments(self):
        serial, _ = fe.recipe(config(), False)
        self.assertEqual([s['id'] for s in serial], [s['id'] for s in self.steps])
        for a, b in zip(serial, self.steps):
            if b['id'].endswith('_run'):
                self.assertEqual(value(a['arguments'], '--threads'), '2')
                self.assertEqual(value(b['arguments'], '--threads'), '4')
                self.assertEqual(self.group['steps'][b['id']]['cpu_slots'], [0, 1, 2, 3])
                b = dict(b, arguments=a['arguments'])
            self.assertEqual(a, b)

    def test_one_process_bundles_and_plans_each_eco_round(self):
        ids = [s['id'] for s in self.steps]
        self.assertFalse(any(i.startswith('fe_bundle_') for i in ids))
        for op in ('reset', 'fanout', 'primary-clock', 'electrical', 'hold'):
            args = next(s['arguments'] for s in self.steps if s['id'] == 'fe_eco_' + op)
            self.assertEqual(args[:2], ['{files:prepare}', 'bundle'])
            self.assertEqual(value(args, '--operation'), op)
            self.assertEqual(value(args, '--workspace'), OUT + 'frontend/bundle-' + op)
            self.assertEqual(value(args, '--eco-workspace'), OUT + 'frontend/eco-' + op)
            self.assertEqual(value(args, '--digest-cache'), OUT + 'frontend/sha256-cache.json')
        # One deadline for both stages: the two former steps' 180 s each, at every allocation.
        for four in (True, False):
            steps, _ = fe.recipe(config(), four)
            self.assertEqual({s['timeout_s'] for s in steps if s['id'].startswith('fe_eco_')}, {360})
            self.assertEqual(fe.ECO_STEP_SECONDS, 2 * 180)

    def test_native_flow_accepts_frontend_groups_before_the_signoff_group_only(self):
        tail = [dict(id=t, kind='command', tool='python', arguments=['{files:producer}', t], timeout_s=1800)
                for t in ('be_route', 'be_main', 'be_antenna')] + [dict(id='be_lvs_inputs', kind='render',
                destination='x.json', content={}), dict(id='be_lvs', kind='command', tool='python',
                arguments=['{files:producer}', 'be_lvs'], timeout_s=1800)]
        steps, _ = producer.declare_stdout_artifacts(copy.deepcopy(self.steps) + tail)
        scope = config()['scope']
        signoff = producer.signoff_parallel_group(4)
        native_flow.check_parallel_groups(dict(scope=scope, parallel_groups=[self.group, signoff]), steps, producer)
        native_flow.check_parallel_groups(dict(scope=scope, parallel_groups=[signoff]), steps, producer)
        native_flow.check_parallel_groups(dict(scope=scope, parallel_groups=[self.group]), steps, producer)
        for groups in ([signoff, self.group], [self.group, producer.signoff_parallel_group(16)], []):
            with self.assertRaisesRegex(Exception, 'concurrency differs'):
                native_flow.check_parallel_groups(dict(scope=scope, parallel_groups=groups), steps, producer)
        # A frontend group that reaches into the backend is not a frontend group.
        bad = copy.deepcopy(self.group)
        bad['steps']['be_route'] = dict(depends_on=[], cpu_slots=[0], memory_mib=64)
        with self.assertRaises(Exception):
            native_flow.check_parallel_groups(dict(scope=scope, parallel_groups=[bad, signoff]), steps, producer)


if __name__ == '__main__':
    unittest.main()

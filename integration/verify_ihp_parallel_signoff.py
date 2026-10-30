#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Replay serial and parallel signoff on copies of frozen Ibex artifacts.

This is a signoff scheduling comparison, not a fresh RTL-to-GDS qualification.
It never writes the source run. Each sample includes copying/hashing inputs,
OpenRCX, TT/SS timing/parity, complete main/antenna decks and full-device LVS.

Estimate: KLayout main 1.82 GiB + antenna 2.16 GiB + timing 0.48 GiB,
plus <128 MiB coordinator, approximately 4.6 GiB physical RAM; reservations
total <=5888 MiB. Each sample owns an 8 GiB / 2 GiB swap cgroup, six distinct
physical CPUs, and a 1800 second deadline. The pair holds the root scale lock.
Comparison is streamed with a bounded line buffer (4 MiB), never a graph oracle.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integration.ibex_sg13g2_ez130 import producer

COPIES = ('backend/ibex_sram_bench.gds', 'logs/be_def.stdout', 'logs/be_netlist.stdout',
          'logs/be_powered.stdout', 'backend/native.nom.spef')
REPORTS = ('backend/main/drc.lyrdb', 'backend/antenna/drc.lyrdb', 'backend/openrcx/nom.spef',
           'backend/lvs/layout.lvsdb', 'backend/lvs/netlist.json',
           'backend/lvs/reference/reference.cdl', 'backend/lvs/reference/reference.json',
           'backend/lvs/check_lvs.json', 'backend/timing/timing.json', 'backend/timing/parity.json')
HELPERS = dict(reference_extract='reference_extract.py', timing_runner='signoff_timing.py',
               drc_runner='signoff_drc.py', lvs_runner='signoff_lvs.py', lvs_interfaces='lvs_interfaces.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def referenced(value, files, tools):
    if isinstance(value, dict):
        if set(value) == {'$input'}:
            files.add(value['$input'])
        elif set(value) == {'$tool'}:
            tools.add(value['$tool'])
        else:
            for item in value.values():
                referenced(item, files, tools)
    elif isinstance(value, list):
        for item in value:
            referenced(item, files, tools)
    elif isinstance(value, str):
        files.update(re.findall(r'\{files:([A-Za-z0-9_-]+)\}', value))
        tools.update(re.findall(r'\{tools:([A-Za-z0-9_-]+)\}', value))


def prepare(source, source_manifest, output, cpus):
    original = producer.read_json(source_manifest)
    policy = producer.signoff_parallel_group()
    steps = [copy.deepcopy(step) for step in original['steps'] if step['id'] in policy['steps']]
    require(len(steps) == len(policy['steps']), 'source recipe omits B4 signoff steps')
    files, tools = set(), {'python', 'rustroad', 'gnu_time'}
    referenced(steps, files, tools)
    for step in steps:
        if step['kind'] == 'command':
            tools.add(step['tool'])
    snapshots = output / 'sources'
    snapshots.mkdir()
    directory = ROOT / 'integration/ibex_sg13g2_ez130'
    for name in {'producer.py', 'signoff_lvs.py', 'build_lvs_reference.py', 'check_lvs.py', *HELPERS.values()}:
        shutil.copyfile(directory / name, snapshots / name)
    records = {key: producer.identity(snapshots / HELPERS[key]) if key in HELPERS
               else original['files'][key] for key in files}
    records['producer'] = producer.identity(snapshots / 'producer.py')
    # Imported/snapshotted first-party helper dependencies are also whole-worker
    # immutable pins, even when no direct argument names them.
    for index, path in enumerate(sorted(snapshots.iterdir())):
        records[f'replay_helper_{index}'] = producer.identity(path)
    identities = {}
    arguments = ['-c', 'from pathlib import Path; import shutil,sys; '
        '[(Path(dst).parent.mkdir(parents=True,exist_ok=True),shutil.copyfile(src,dst)) '
        'for src,dst in zip(sys.argv[1::2],sys.argv[2::2])]']
    for index, relative in enumerate(COPIES):
        role = f'replay_input_{index}'
        identities[relative] = records[role] = producer.identity(source / relative)
        require(records[role]['bytes'] <= 128 << 20, 'replay input byte budget')
        arguments.extend(['{files:' + role + '}', '{output}/' + relative])
    copy_step = dict(id='replay_copy', kind='command', tool='python', arguments=arguments, timeout_s=60)
    last = dict(id='replay_complete', kind='render', destination='replay-complete.json',
                content=dict(status='signoff_replay_only', full_flow='unverified'))
    scope = dict(threads=len(cpus), cpu_affinity=cpus, memory_mib=8192, max_log_bytes=64 << 20)
    manifest = dict(schema='rusteda.ihp-flow.v1', name='ibex_signoff_replay', pdk='ihp-sg13g2',
        library='ez130_8t', corners=['tt', 'ss'], files=records,
        tools={key: original['tools'][key] for key in tools}, scope=scope,
        steps=[copy_step, *steps, last], gates={}, artifacts={str(index): path for index, path in enumerate(COPIES)})
    producer.validate_parallel_groups(manifest['steps'], [policy], scope)
    for record in [*manifest['files'].values(), *manifest['tools'].values()]:
        require(producer.identity(Path(record['path'])) == record, 'stale source input: ' + record['path'])
    for mode in ('serial', 'parallel'):
        sample = copy.deepcopy(manifest)
        if mode == 'parallel':
            sample['parallel_groups'] = [policy]
        producer.write_json(output / (mode + '-manifest.json'), sample)
    return manifest, identities


def execute(output, mode, manifest):
    path = output / (mode + '-manifest.json')
    identity = producer.identity(path)
    request = hashlib.sha256((str(output) + mode + identity['sha256']).encode()).hexdigest()
    timer = manifest['tools']['gnu_time']['path']
    cpus = ','.join(map(str, manifest['scope']['cpu_affinity']))
    command = ['systemd-run', '--user', '--scope', '--unit=rusteda-worker-' + request[:24] + '.scope',
        '--description=RustEDA worker ' + request, '-p', 'MemoryMax=8G', '-p', 'MemorySwapMax=2G',
        '-p', 'AllowedCPUs=' + cpus, '-p', 'TasksMax=256', 'timeout', '1800s',
        timer, '-v', '-o', str(output / (mode + '.time')),
        manifest['tools']['python']['path'], str(output / 'sources/producer.py'),
        '--project', str(ROOT), '--manifest', str(path), '--workspace', str(output / mode),
        '--base-rev', 'empty', '--pdk', 'ihp', '--rustroad', manifest['tools']['rustroad']['path'],
        '--gnu-time', timer, '--stop-after', 'replay_complete']
    producer.write_json(output / (mode + '-command.json'), command)
    tick = time.monotonic()
    unit = 'rusteda-worker-' + request[:24] + '.scope'
    try:
        with (output / (mode + '.log')).open('xb') as log:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, RUSTEDA_WORKER_ID=request, RUSTEDA_OWNERSHIP=str(output / (mode + '-owner'))),
                timeout=1830)
    finally:
        # Match the production foreground owner's exact-unit cleanup even when
        # an inner helper uses setsid. Never target a wildcard or another run.
        cleanup = subprocess.run(['systemctl', '--user', 'kill', '--kill-whom=all', '--signal=KILL', unit],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        producer.write_json(output / (mode + '-cleanup.json'),
            dict(unit=unit, returncode=cleanup.returncode, stdout=cleanup.stdout, stderr=cleanup.stderr))
    require(result.returncode == 0, mode + ' signoff replay failed; inspect ' + str(output / (mode + '.log')))
    summary = producer.read_json(output / mode / 'summary.json')
    require(summary['status'] == 'frontend_completed' and 'complete_flow' in summary['unverified_items'],
            'replay must retain its incomplete full-flow scope')
    for relative in COPIES:
        actual = producer.identity(output / mode / relative)
        pinned = next(record for record in manifest['files'].values() if record['path'].endswith('/' + relative))
        require(all(actual[key] == pinned[key] for key in ('sha256', 'bytes')), 'replay input changed: ' + relative)
    return dict(wall_seconds=time.monotonic() - tick, summary=producer.identity(output / mode / 'summary.json'),
                resources=summary['resources'])


def normalized_digest(path, replacements):
    require(path.stat().st_size <= 256 << 20, 'report byte budget')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while line := stream.readline((4 << 20) + 1):
            require(len(line) <= 4 << 20, 'report line byte budget')
            if path.name == 'nom.spef' and line.startswith(b'*DATE '):
                line = b'*DATE "<run timestamp>"\n'
            for original, canonical in replacements:
                line = line.replace(original, canonical)
            digest.update(line)
    return digest.hexdigest()


def compare(output):
    substitutions = [[(str(output / mode).encode(), b'<sample>')] for mode in ('serial', 'parallel')]
    results = []
    for relative in REPORTS:
        paths = [output / mode / relative for mode in ('serial', 'parallel')]
        identities = [producer.identity(path) for path in paths]
        digests = [normalized_digest(path, replacements) for path, replacements in zip(paths, substitutions)]
        require(digests[0] == digests[1], 'signoff report content differs: ' + relative)
        for replacements, identity in zip(substitutions, identities):
            replacements.append((identity['sha256'].encode(), digests[0].encode()))
        results.append(dict(report=relative, serial=identities[0], parallel=identities[1],
                            normalized_sha256=digests[0]))
    for mode in ('serial', 'parallel'):
        for table in ('main', 'antenna', 'lvs'):
            report = producer.read_json(output / mode / 'backend' / table / 'summary.json', 16 << 20)
            require(report['status'] == 'passed' and report['complete'] is True, 'signoff gate not complete')
            if table != 'lvs':
                require(report['violations'] == 0, 'foundry violations')
        for report in ('timing', 'parity'):
            require(producer.read_json(output / mode / 'backend/timing' / (report + '.json'))['status'] == 'passed',
                    'timing/parity did not pass')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--source-output', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    parser.add_argument('--scale-lock', type=Path, required=True)
    parser.add_argument('--cpus', required=True, help='six distinct physical CPU IDs, comma separated')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    cpus = [int(value) for value in args.cpus.split(',')]
    require(len(cpus) == len(set(cpus)) == 6, 'six unique physical CPUs required')
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(path.name != 'workspace.lock' for path in output.iterdir()),
                'REVISION_CONFLICT')
        shutil.copyfile(Path(__file__), output / 'verification-source.py')
        manifest, identities = prepare(args.source_output.resolve(strict=True), args.source_manifest.resolve(strict=True),
                                       output, cpus)
        result = dict(schema='rusteda.parallel-signoff-replay.v1', status='prepared_not_executed',
            source_manifest=producer.identity(args.source_manifest), source_inputs=identities,
            policy=producer.identity(output / 'verification-source.py'), full_flow='unverified', benchmark='unverified',
            normalization=['exact sample workspace prefixes', 'OpenRCX SPEF *DATE line',
                           'raw SHA roots replaced only after their complete normalized bytes compare equal'])
        producer.write_json(output / 'result.json', result)
        if args.execute:
            with args.scale_lock.open('a') as scale:
                fcntl.flock(scale, fcntl.LOCK_EX)
                result['samples'] = {mode: execute(output, mode, manifest) for mode in ('serial', 'parallel')}
            result['reports'] = compare(output)
            result['status'] = 'signoff_reports_match'
            producer.write_json(output / 'result.json', result)
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps(dict(status=result['status'], result=str(output / 'result.json'))))


if __name__ == '__main__':
    main()

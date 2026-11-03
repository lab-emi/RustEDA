#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Bounded native antenna repair with exact parent-to-child revision evidence.

This integration helper selects every violating standard-cell net, excluding
each net touching an actual BLOCK instance. The engine checks supported-model
coverage itself. Partial macro diagnostics are preserved; only the original31
foundry antenna rules on the final GDS can qualify the whole layout.

Estimate: <=64MiB connectivity JSON (<768MiB decoded) and <=32MiB selected
report metadata, one serial native child estimated2GiB, at most3 repair rounds.
No graph/path enumeration. Raw report cap256MiB; all large check arrays are
streamed rather than decoded. Caller owns a memory-limited cgroup/deadline.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time

from report_fields import fields, prefix_fields


HEADER = {'schema', 'status', 'workspace_revision', 'scope', 'safety_margin', 'nets', 'violations', 'lef_violations', 'sources', 'unsupported'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path, cap=64 << 20):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= cap, 'JSON byte budget')
    return json.loads(path.read_bytes())


def identity(path, cap=4 << 30):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= cap, 'file byte budget')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'file changed while hashing')
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def head(workspace):
    path = workspace/'HEAD'
    require(path.stat().st_size <= 128, 'HEAD byte budget')
    value = path.read_text().strip()
    require(re.fullmatch('[A-Za-z0-9_-]{1,64}', value), 'unsafe revision')
    return value


def metadata(workspace, revision):
    """Native formats4..6 put their revision header before the immutable IR."""
    path = workspace/'revisions'/revision/'design.json'
    record = prefix_fields(path, {'format_version', 'revision', 'parent_revision', 'stage'})
    require(type(record['format_version']) is int and record['format_version'] in (4, 5, 6)
        and record['revision'] == revision, 'native revision header differs')
    return dict(revision=revision, parent_revision=record['parent_revision'],
                design_file=identity(path, 512 << 20))


def macro_nets(model):
    require(set(model) == {'layers', 'masters', 'instance_masters', 'ports', 'nets', 'pins'}, 'native connectivity schema')
    masters, instances, nets, pins = (model[key] for key in ('masters', 'instance_masters', 'nets', 'pins'))
    require(len(masters) <= 4096 and len(instances) <= 100000 and len(nets) <= 100000
        and len(pins) <= 1000000, 'native connectivity object budget')
    names = [row['name'] for row in nets]
    require(len(set(names)) == len(names) and all(isinstance(name, str) and 0 < len(name) <= 4096 for name in names), 'native net identity')
    block = set()
    for index, master in enumerate(instances):
        require(type(master) is int and 0 <= master < len(masters), 'master index')
        if masters[master]['class'] == 'BLOCK':
            block.add(index)
    touches = set()
    for pin in pins:
        net, owner = pin['net'], pin['owner']
        require(net is None or type(net) is int and 0 <= net < len(nets), 'pin net index')
        if owner['kind'] == 'instance':
            require(type(owner['instance']) is int and 0 <= owner['instance'] < len(instances), 'pin instance index')
            if owner['instance'] in block and net is not None:
                touches.add(names[net])
        else:
            require(owner['kind'] == 'port', 'unknown pin owner')
    return set(names), touches


class Run:
    def __init__(self, config, output):
        require(set(config) == {'schema', 'design_workspace', 'initial_revision', 'tool', 'limits'}
            and config['schema'] == 'rusteda.native-antenna-inputs.v1', 'native antenna input schema')
        self.c, self.output = config, output
        self.design = Path(config['design_workspace']).resolve(strict=True)
        self.tool = config['tool']
        require(identity(self.tool['path']) == self.tool, 'native tool identity differs')
        limits = config['limits']
        require(set(limits) == {'threads', 'max_rounds', 'max_selected_nets', 'max_work', 'max_expansions', 'deadline_seconds'}, 'antenna limits')
        require(type(limits['threads']) is int and limits['threads'] in (2, 4, 16)
            and type(limits['max_rounds']) is int and 1 <= limits['max_rounds'] <= 3
            and limits['max_selected_nets'] == 100000 and limits['max_work'] == 200000000
            and limits['max_expansions'] == 8000000 and limits['deadline_seconds'] == 1800, 'antenna work/memory bounds')
        require(head(self.design) == config['initial_revision'], 'REVISION_CONFLICT')
        self.initial = metadata(self.design, config['initial_revision'])
        self.started, self.commands = time.monotonic(), []

    def command(self, name, args, cap=256 << 20):
        output = self.output/(name+'.json')
        error = self.output/(name+'.stderr')
        argv = [self.tool['path'], '--workspace', str(self.design), '--threads', str(self.c['limits']['threads']),
                '--max-expansions', '8000000', *map(str, args)]
        save(self.output/(name+'-command.json'), argv)
        tick, count = time.monotonic(), 0
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
        try:
            with output.open('xb') as out, error.open('xb') as err, selectors.DefaultSelector() as poll:
                for pipe, target, maximum in ((process.stdout, out, cap), (process.stderr, err, 16 << 20)):
                    os.set_blocking(pipe.fileno(), False)
                    poll.register(pipe, selectors.EVENT_READ, (target, maximum, 0))
                while poll.get_map():
                    require(time.monotonic()-tick <= 600 and time.monotonic()-self.started <= 1800, 'native antenna deadline')
                    for key, _ in poll.select(.1):
                        chunk = os.read(key.fileobj.fileno(), 16384)
                        if not chunk:
                            poll.unregister(key.fileobj)
                            continue
                        target, maximum, used = key.data
                        used += len(chunk)
                        require(used <= maximum, 'native report byte budget')
                        count += len(chunk)
                        target.write(chunk)
                        poll.modify(key.fileobj, selectors.EVENT_READ, (target, maximum, used))
                code = process.wait(timeout=10)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.kill()
                process.wait(timeout=10)
        record = dict(command=argv, exit_code=code, stdout=identity(output), stderr=identity(error),
            wall_seconds=time.monotonic()-tick, output_bytes=count)
        self.commands.append(record)
        require(code == 0, 'native antenna command failed: '+name)
        return output

    def check(self, name, revision):
        path = self.command(name, ['check-antenna', '--safety-margin', '0.1', '--max-work', '200000000', '--max-rows', '250000'])
        report = fields(path, HEADER)
        require(report['schema'] == 'rustroad.antenna-check.v1' and report['workspace_revision'] == revision
            and report['scope']['routing_scope'] == 'all_signal_nets', 'antenna report revision/scope differs')
        require(head(self.design) == revision, 'antenna check mutated the workspace')
        require(len(report['nets']) <= 100000 and len(report['unsupported']) <= 100000, 'antenna metadata row budget')
        save(self.output/(name+'-header.json'), report)
        return path, report

    def run(self):
        rounds, attempts, excluded = [], [], set()
        current = self.c['initial_revision']
        last_check = 'check-0'
        before_path, before = self.check(last_check, current)
        remaining = set()
        for index in range(self.c['limits']['max_rounds']):
            model_path = self.command('model-'+str(index), ['export-connectivity'], 64 << 20)
            model = read(model_path)
            names, touches = macro_nets(model)
            del model
            violating = {row['net'] for row in before['nets'] if row['violated'] is True}
            require(violating <= names, 'report net absent from actual current connectivity')
            excluded |= violating & touches
            remaining = violating-touches
            if not remaining:
                break
            selection = self.output/('selected-'+str(index)+'.json')
            save(selection, sorted(remaining))
            base = current
            repair_path = self.command('repair-'+str(index), ['repair-antenna', '--base-rev', base,
                '--antenna-report', before_path, '--selected-nets', selection, '--safety-margin', '0.1',
                '--max-work', '200000000', '--max-jumpers', '4096', '--max-jumpers-per-net', '16',
                '--max-positions', '512', '--max-candidates', '32'])
            repair = fields(repair_path, {'schema', 'status', 'base_revision', 'revision', 'routing_scope',
                'repair_scope', 'selected_nets', 'requires_full_revalidation', 'before', 'after'})
            require(repair['schema'] == 'rustroad.antenna-repair.v1' and repair['base_revision'] == base
                and repair['routing_scope'] == 'all_signal_nets' and repair['repair_scope'] == 'selected_supported_nets'
                and repair['selected_nets'] == sorted(remaining) and repair['requires_full_revalidation'] is True,
                'repair source/scope differs')
            current = head(self.design)
            require(repair['revision'] == current, 'repair revision differs from actual HEAD')
            child = metadata(self.design, current)
            if current != base:
                require(child['parent_revision'] == base, 'repair child parent differs')
            attempt = dict(parent_revision=base, base_revision=base, revision=current,
                before_report=identity(before_path), before_header=identity(self.output/('check-'+str(index)+'-header.json')),
                model=identity(model_path), selected=identity(selection), repair_report=identity(repair_path), child=child)
            attempts.append(attempt)
            if current != base:
                rounds.append(attempt)
            last_check = 'check-'+str(index+1)
            before_path, before = self.check(last_check, current)
            if current == base:
                break
        else:
            # Last check is always fresh, even after exhausting the round cap.
            pass
        remaining = {row['net'] for row in before['nets'] if row['violated'] is True}
        model_path = self.command('model-final', ['export-connectivity'], 64 << 20)
        names, touches = macro_nets(read(model_path))
        require(remaining <= names, 'final report/connectivity mismatch')
        excluded |= remaining & touches
        supported_remaining = remaining-touches
        require(identity(self.tool['path']) == self.tool, 'native tool changed during repair')
        require(head(self.design) == current, 'final native HEAD differs')
        final = metadata(self.design, current)
        snapshot = self.output/'final-head.txt'
        snapshot.write_text(current+'\n')
        result = dict(schema='rusteda.native-antenna-lineage.v1',
            status='unresolved' if supported_remaining else 'complete', scope='supported_standard_cell_subset',
            workspace=str(self.design), initial_revision=self.c['initial_revision'], final_revision=current,
            initial= self.initial, final=final, workspace_head=identity(snapshot), tool=self.tool,
            route_scope='all_signal_nets', rounds=rounds, attempts=attempts, final_report=identity(before_path),
            final_header=identity(self.output/(last_check+'-header.json')),
            final_model=identity(model_path), excluded_macro_touch=sorted(excluded),
            unresolved_supported_nets=sorted(supported_remaining), requires_full_revalidation=True,
            whole_layout_antenna_status='unchecked', commands=self.commands,
            wall_seconds=time.monotonic()-self.started)
        save(self.output/'lineage.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output/'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock' for p in output.iterdir()), 'REVISION_CONFLICT')
        try:
            result = Run(read(args.inputs, 2 << 20), output).run()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            save(output/'failure.json', dict(status='failed', complete=False, error=str(error)))
            print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
            return 1
        (output/'HEAD').write_text('rev1\n')
        print(json.dumps({key: result[key] for key in ('schema', 'status', 'initial_revision', 'final_revision', 'requires_full_revalidation')}))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())

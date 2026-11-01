#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run the original vendor-model test on the actual reference final netlist.

This bounded simulation gate makes no sequential-equivalence claim. Controller
estimate <768 MiB including a <=64 MiB external structural JSON (100k cells,
1M pin bits); one external worker, <1 GiB estimated RSS. Caller supplies a
memory-limited scope and whole-flow deadline. Each subprocess has a 180s/16MiB
log budget. Only original LEF-declared PG-only cells receive empty declarations;
functional cells and both SRAMs always use original vendor implementations.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import macro_contract


FILES = {'rtl', 'netlist', 'tb', 'physical_lef', 'std_tt', 'std_ss', 'ram_tt', 'ram_ss',
         'vendor_0', 'vendor_1', 'vendor_2', 'physical_policy'}
TOOLS = {'yosys', 'iverilog', 'vvp', 'gnu_time'}
POLICY_SHA256 = '551298f6ededb81ee9d97bfdcde64383d4245da374b6e90b60462aa66e76bec3'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(path, cap=4 << 30):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= cap, 'file byte budget')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'source changed while hashing')
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def external(command, output, name, timer):
    argv = [timer, '-v', '-o', str(output / (name+'.time')), *map(str, command)]
    save(output / (name+'-command.json'), argv)
    tick, count = time.monotonic(), 0
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        with (output / (name+'.log')).open('xb') as log, selectors.DefaultSelector() as poll:
            os.set_blocking(process.stdout.fileno(), False)
            poll.register(process.stdout, selectors.EVENT_READ)
            while poll.get_map():
                require(time.monotonic()-tick <= 180, 'vendor subprocess deadline')
                for key, _ in poll.select(.1):
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        poll.unregister(key.fileobj)
                        continue
                    count += len(chunk)
                    require(count <= 16 << 20, 'vendor subprocess log byte budget')
                    log.write(chunk)
            code = process.wait(timeout=5)
        require(code == 0, 'vendor subprocess failed: '+name)
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
    return dict(stage=name, exit_code=code, wall_seconds=time.monotonic()-tick, log_bytes=count)


def run(config, output):
    require(set(config) == {'schema', 'files', 'tools'}
            and config['schema'] == 'rusteda.reference-simulation-inputs.v1', 'simulation schema')
    files, tools = config['files'], config['tools']
    require(set(files) == FILES and set(tools) == TOOLS, 'simulation input/tool set')
    for group, cap in ((files, 32 << 20), (tools, 4 << 30)):
        for record in group.values():
            require(identity(record['path'], cap) == record, 'simulation source identity differs')
    require(files['physical_policy']['sha256'] == POLICY_SHA256, 'physical-only declaration policy differs')
    paths = {key: Path(value['path']) for key, value in files.items()}
    def quoted(value):
        return '"'+str(value).replace('\\', '\\\\').replace('"', '\\"')+'"'
    script = output / 'structural.ys'
    script.write_text('read_verilog '+quoted(paths['netlist'])+'\nhierarchy -top ibex_sram_bench\nwrite_json '
                      +quoted(output/'netlist.json')+'\n')
    stages = [external([tools['yosys']['path'], '-s', script], output, 'structural', tools['gnu_time']['path'])]
    identity(output/'netlist.json', 64 << 20)
    raw = json.loads((output/'netlist.json').read_bytes())
    require(set(raw.get('modules', {})) == {'ibex_sram_bench'}, 'mapped top must be flat')
    cells = raw['modules']['ibex_sram_bench'].get('cells', {})
    require(1 <= len(cells) <= 100000, 'mapped instance budget')
    require(sum(sum(len(bits) for bits in cell['connections'].values()) for cell in cells.values()) <= 1000000,
            'mapped pin-bit budget')
    spec = importlib.util.spec_from_file_location('rusteda_reference_physical_policy', paths['physical_policy'])
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    context = SimpleNamespace(files={key: paths[key] for key in
        ('physical_lef', 'netlist', 'std_tt', 'std_ss', 'ram_tt', 'ram_ss')}, root=output, result={})
    policy.Run.physical_models(context)
    names = set(context.result['physical_only_models']['masters'])
    for cell in cells.values():
        require(cell['type'] not in names or not (cell.get('connections') or cell.get('parameters')),
                'PG-only declaration would replace a connected instance')
    save(output/'physical-models.json', context.result)
    save(output/'macro-contract.json', macro_contract.check(output/'netlist.json'))
    command = [tools['iverilog']['path'], '-g2012', '-s', 'tb', '-I', str(paths['vendor_1'].parent),
        '-o', str(output/'sim'), str(paths['tb']), str(paths['netlist']), str(output/'physical-only.v'),
        *[str(paths['vendor_'+str(i)]) for i in range(3)]]
    stages.append(external(command, output, 'compile', tools['gnu_time']['path']))
    stages.append(external([tools['vvp']['path'], output/'sim'], output, 'simulate', tools['gnu_time']['path']))
    text = (output/'simulate.log').read_text()
    require('PASS: real SRAM banks, byte masks, boundary addresses, Ibex program stores 42 and 43' in text,
            'original vendor test did not finish')
    for group in (files, tools):
        for record in group.values():
            require(identity(record['path']) == record, 'simulation input changed during run')
    result = dict(status='passed', complete=True, netlist_sha256=files['netlist']['sha256'],
        final_netlist_sha256=files['netlist']['sha256'], original_rtl_sha256=files['rtl']['sha256'],
        testbench=files['tb'], simulator=tools['iverilog'], runtime=tools['vvp'],
        log=identity(output/'simulate.log'), scope='original vendor simulation, two SRAM banks and Ibex program; no formal equivalence')
    save(output/'simulation.json', result)
    sources = dict(files, final_netlist=files['netlist'])
    save(output/'summary.json', dict(status='passed', complete=True, input_hashes_unchanged=True,
        sources=sources, tools=tools, stages=stages, artifacts={'simulation.json': identity(output/'simulation.json')},
        formal_equivalence='unverified'))


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
        require(args.inputs.stat().st_size <= 2 << 20, 'manifest byte budget')
        try:
            run(json.loads(args.inputs.read_bytes()), output)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            if not (output/'summary.json').exists():
                save(output/'summary.json', dict(status='failed', complete=False, error=str(error),
                    formal_equivalence='unverified'))
            (output/'HEAD').write_text('rev1\n')
            print(json.dumps(dict(status='failed', complete=False, error=str(error))), file=sys.stderr)
            return 1
        (output/'HEAD').write_text('rev1\n')
        print(json.dumps(dict(status='passed', complete=True, formal_equivalence='unverified')))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())

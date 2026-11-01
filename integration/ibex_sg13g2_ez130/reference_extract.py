#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run independent nominal OpenRCX on this run's exact native DEF.

Original technology/cell/macro LEF and original calibration data are hash-pinned.
The extraction algorithm is explicitly version1, matching the pinned IHP PDK
default flow in black-box mode controls. Version2 is a distinct model consumer.
The caller supplies a cgroup and whole-worker deadline. Controller RAM <64 MiB;
external estimate <=1.5 GiB for 100k instances/1M nets/128 MiB SPEF, two threads.
No missing parasitic net is padded. Timing annotation and LVS remain separate
mandatory completeness checks; an extraction exit code is not signoff.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identity(path, limit=4 << 30):
    path = path.resolve(strict=True)
    require(path.is_file() and path.stat().st_size <= limit, 'file byte budget')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=str(path), sha256=digest, bytes=path.stat().st_size)


def read(path):
    require(path.is_file() and path.stat().st_size <= 2 << 20, 'JSON byte budget')
    return json.loads(path.read_text())


def quote(value):
    value = str(value)
    require('\x00' not in value and len(value) <= 4096, 'Tcl literal budget')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace(
        '[', '\\[').replace(']', '\\]').replace('\n', '\\n') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    args.workspace = args.workspace.resolve()
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock'
                for p in args.workspace.iterdir()), 'REVISION_CONFLICT')
        config = read(args.inputs)
        require(set(config) == {'schema', 'top', 'files', 'tools', 'budgets'}
                and config['schema'] == 'rusteda.reference-extraction-inputs.v1', 'input schema')
        require(config['top'] == 'ibex_sram_bench', 'explicit top identity')
        require(set(config['files']) == {'def', 'tech_lef', 'cell_lef', 'ram_lef', 'rules_nom'}
                and set(config['tools']) == {'appimage', 'gnu_time'}, 'explicit RC input/tool set')
        require(config['budgets'] == dict(threads=2, timeout_seconds=900,
                max_spef_bytes=128 << 20, max_nets=100000, max_log_bytes=32 << 20), 'RC budgets')
        for group in ('files', 'tools'):
            for key, record in config[group].items():
                require(identity(Path(record['path']), (4 << 30) if group == 'tools' else 128 << 20)
                        == record, 'input changed: ' + key)
        files, tools = config['files'], config['tools']
        spef = args.workspace / 'nom.spef'
        lines = ['set_thread_count 2']
        for key in ('tech_lef', 'cell_lef', 'ram_lef'):
            lines.append('read_lef ' + quote(files[key]['path']))
        lines += ['read_def ' + quote(files['def']['path']),
                  'if {[[ord::get_db_block] getName] ne "ibex_sram_bench"} {error "DEF top differs"}',
                  'define_process_corner -ext_model_index 0 nom',
                  'extract_parasitics -version 1 -ext_model_file ' + quote(files['rules_nom']['path']) + ' -corner_cnt 1',
                  'write_spef ' + quote(spef)]
        script = args.workspace / 'extract.tcl'
        script.write_text('if {[catch {\n' + '\n'.join(lines)
            + '\n} message options]} {\n puts stderr $message\n puts stderr [dict get $options -errorinfo]\n exit 2\n}\nexit 0\n')
        command = [tools['gnu_time']['path'], '-v', '-o', str(args.workspace / 'extract.time'),
                   tools['appimage']['path'], 'openroad', '-exit', str(script)]
        (args.workspace / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
        start = time.monotonic()
        log_path = args.workspace / 'extract.log'
        with log_path.open('xb') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                while process.poll() is None:
                    require(time.monotonic() - start <= 900, 'OpenRCX deadline exceeded')
                    require(log_path.stat().st_size <= 32 << 20, 'OpenRCX log budget')
                    if spef.exists():
                        require(spef.stat().st_size <= 128 << 20, 'SPEF byte budget')
                    time.sleep(.2)
            except BaseException:
                process.kill()
                process.wait(timeout=10)
                raise
        require(process.returncode == 0, 'OpenRCX nonzero exit: ' + str(process.returncode))
        output = identity(spef, 128 << 20)
        nets, ends, design = 0, 0, None
        with spef.open() as stream:
            for line in stream:
                require(len(line) <= 65536, 'SPEF line byte budget')
                if line.startswith('*DESIGN '):
                    require(design is None, 'duplicate SPEF design declaration')
                    design = line.split(None, 1)[1].strip().strip('"')
                if line.startswith('*D_NET '):
                    nets += 1
                    require(nets <= 100000, 'SPEF net budget')
                if line.strip() == '*END':
                    ends += 1
        require(design == config['top'] and nets > 0 and ends == nets, 'incomplete or foreign SPEF')
        for group in ('files', 'tools'):
            for key, record in config[group].items():
                require(identity(Path(record['path'])) == record, 'input changed during extraction: ' + key)
        result = dict(status='extracted_not_qualified', extraction_finished=True,
                      scope='geometry present in supplied DEF; active full-net coverage is not yet verified',
                      top=design, rc_corner='nom',
                      source='openrcx', spef=output, annotated_net_records=nets,
                      extraction_options=dict(algorithm_version=1, via_merge='not_overridden',
                                              coupling_threshold='not_overridden'),
                      files=files, tools=tools, wall_seconds=time.monotonic() - start,
                      unverified=['active_net_annotation', 'LVS', 'setup', 'hold', 'DRV', 'timing_accuracy'])
        (args.workspace / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        (args.workspace / 'HEAD').write_text('rev1\n')
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        raise SystemExit(2)

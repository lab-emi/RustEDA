#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Additional unchanged foundry maximal deck with witnessed LU.a scope.

The original complete main deck remains mandatory for LU.b and all original
rules. This extra oracle never replaces it. Names/status alone cannot prove
applicability: the complete XML category inventory must contain LU.a. All other
findings remain fatal. No reference/deck implementation is inspected or edited.

Upfront controller estimate<128MiB (<=64MiB XML, streamed <=500k items, <=4096
categories, <=16MiB tool log). Actual placement-deck peak<1.6GiB; reserve4GiB
plus1GiB swap and900s under caller guard for routed geometry. Native internal
allocations are contained by that guard. No graph/path enumeration here.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import xml.etree.ElementTree as ET


def category_inventory(path):
    names = []
    if path.stat().st_size > 64 << 20:
        raise ValueError('LATCHUP_REPORT_BYTE_BUDGET')
    for _, node in ET.iterparse(path, events=('end',)):
        if node.tag == 'category':
            name = node.findtext('name')
            if name:
                if len(names) >= 4096 or len(name.encode()) > 4096:
                    raise ValueError('LATCHUP_CATEGORY_BUDGET')
                names.append(name)
                node.clear()
        elif node.tag == 'item':
            node.clear()
    if len(names) != len(set(names)):
        raise ValueError('LATCHUP_DUPLICATE_CATEGORY')
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gds', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--tool', type=Path, required=True)
    parser.add_argument('--top', default='ibex_sram_bench')
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]+', args.top):
        raise ValueError('LATCHUP_UNSAFE_TOP_HANDLE')
    module_path = Path(__file__).with_name('signoff_drc.py').resolve(strict=True)
    spec = importlib.util.spec_from_file_location('pinned_drc_common', module_path)
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.base_rev != 'empty' or any(p.name != 'workspace.lock' for p in output.iterdir()):
            raise ValueError('REVISION_CONFLICT')
        tick = time.monotonic()
        _, profile_id, _ = common.verify_profile(args.profile.resolve(strict=True), 'main')
        profile = json.loads(Path(profile_id['path']).read_bytes())
        relative = 'tech/drc/rule_decks/sg13g2_maximal.drc'
        deck = args.profile.resolve().parent / 'klayout' / relative
        deck_id = common.identity(deck)
        if profile['files'].get(relative, {}).get('prepared_sha256') != deck_id['sha256']:
            raise ValueError('LATCHUP_DECK_NOT_IN_PINNED_PROFILE')
        gds, tool = common.identity(args.gds), common.identity(args.tool)
        source, common_id = common.identity(Path(__file__)), common.identity(module_path)
        command = [tool['path'], 'klayout', '-b', '-r', deck_id['path']]
        for value in ('threads=2', 'run_mode=deep', 'topcell=' + args.top,
                      'input=' + gds['path'], 'report=' + str(output / 'drc.lyrdb'),
                      'log=' + str(output / 'deck.log')):
            command.extend(['-rd', value])
        (output / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            count = 0
            with (output / 'stdout.log').open('xb') as stream:
                while chunk := process.stdout.read(65536):
                    count += len(chunk)
                    if count > 16 << 20:
                        raise ValueError('LATCHUP_TOOL_LOG_BYTE_BUDGET')
                    stream.write(chunk)
            code = process.wait(timeout=10)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
        report = dict(schema='rusteda.foundry-latchup-result.v1', status='unverified',
                      complete=False, violations=None, required_rule_categories=['LU.a'],
                      engine_exit_code=code, scope='complete_foundry_maximal_additional_to_original_main',
                      original_main_status='unchecked_required_separately', gds=gds, tool=tool,
                      profile=profile_id, deck=deck_id, runner=source, common_runner=common_id)
        if code == 0:
            total, findings, samples = common.findings(output / 'drc.lyrdb')
            categories = category_inventory(output / 'drc.lyrdb')
            if common.verify_profile(args.profile.resolve(strict=True), 'main')[1] != profile_id:
                raise ValueError('LATCHUP_PROFILE_CHANGED')
            for record in (gds, tool, source, common_id, deck_id):
                if common.identity(Path(record['path'])) != record:
                    raise ValueError('LATCHUP_EXECUTION_INPUT_CHANGED')
            applicable = 'LU.a' in categories
            report.update(status=('passed' if total == 0 else 'failed') if applicable else 'unverified',
                          complete=applicable, violations=total, categories=categories,
                          findings=findings, samples=samples, raw_report=common.identity(output / 'drc.lyrdb'))
        report['wall_seconds'] = time.monotonic() - tick
        (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps({key: report[key] for key in ('schema', 'status', 'complete', 'violations', 'wall_seconds')}))
        return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())

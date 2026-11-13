#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Joint-only typed output capture; existing checker code remains unchanged.

Reviewed-recipe controller estimate128MiB,256MiB AS soft guard;2MiB manifest/
4MiB JSON/64KiB chunks/16MiB logs. Child restores the original4GiB AS cap.
One real Python child; same4GiB AS/original product ownership. Native process
count is not inferred. Detached-descendant failure cleanup belongs to the
original whole-product owner. No sibling source is imported or executed here.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import subprocess
import sys
import time

WORKER = ('5c08015c126e1855e75afef06308b40444c5bcba842909e4b0462f790c1dee45', 37719)


def need(value, message):
    if not value:
        raise ValueError('JOINT_CAPTURE_' + message)


def identity(path, cap):
    path = Path(path).resolve(strict=True); before = path.stat()
    need(path.is_file() and before.st_size <= cap, 'FILE_BUDGET')
    count, digest = 0, hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(min(65536, cap - count + 1)):
            count += len(chunk); need(count <= cap, 'FILE_GREW'); digest.update(chunk)
    after = path.stat()
    need(count == before.st_size == after.st_size and
         (before.st_dev, before.st_ino, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_mtime_ns), 'FILE_CHANGED')
    return dict(path=str(path), sha256=digest.hexdigest(), bytes=count)


def read(path, cap=4 << 20):
    before = identity(path, cap)
    with Path(before['path']).open('rb') as stream:
        raw = stream.read(cap + 1)
    need(len(raw) == before['bytes'] and hashlib.sha256(raw).hexdigest() == before['sha256']
         and identity(path, cap) == before, 'JSON_CHANGED')
    def unique(items):
        result = {}
        for key, value in items:
            need(key not in result, 'JSON_DUPLICATE'); result[key] = value
        return result
    def number(value):
        value = float(value); need(math.isfinite(value), 'JSON_NONFINITE'); return value
    def constant(_value):
        raise ValueError('JOINT_CAPTURE_JSON_NONFINITE')
    result = json.loads(raw, object_pairs_hook=unique, parse_float=number, parse_constant=constant)
    pending, nodes = [(result, 0)], 0
    while pending:
        value, depth = pending.pop(); nodes += 1
        need(nodes <= 65536 and depth <= 32, 'JSON_WORK')
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        for child in children:
            need(nodes + len(pending) < 65536, 'JSON_PENDING'); pending.append((child, depth + 1))
    return result


def record(value, cap):
    need(isinstance(value, dict) and set(value) == {'path', 'sha256', 'bytes'} and
         isinstance(value['path'], str) and len(value['path']) <= 4096 and
         Path(value['path']).is_absolute() and type(value['bytes']) is int and
         0 <= value['bytes'] <= cap and re.fullmatch('[0-9a-f]{64}', value['sha256']), 'FILE_ID')
    need(identity(value['path'], cap) == value, 'PIN_CHANGED'); return value


def ownership(root):
    owner = root.parent / 'ownership'; request = os.environ.get('RUSTEDA_WORKER_ID', '')
    need(re.fullmatch('[0-9a-f]{64}', request) and os.environ.get('RUSTEDA_OWNERSHIP') == str(owner), 'OWNER_REQUIRED')
    native = read(owner / 'native.json', 65536)
    need(set(native) == {'request_id', 'implementation', 'manifest'} and
         native['request_id'] == request and native['implementation'] == 'native', 'OWNER_CHANGED')
    mid = record(native['manifest'], 2 << 20); manifest = read(mid['path'], 2 << 20)
    need(manifest['schema'] == 'rusteda.ihp-flow.v1' and len(manifest['files']) <= 256
         and len(manifest['tools']) <= 32 and len(manifest['steps']) <= 129, 'MANIFEST_BUDGET')
    return native, mid, manifest


def selected_argv(root, manifest, tag):
    rows = [s for s in manifest['steps'] if s['id'] == tag]; need(len(rows) == 1, 'STEP_REQUIRED'); step = rows[0]
    need(set(step) == {'id', 'kind', 'tool', 'arguments', 'timeout_s'} and step['kind'] == 'command'
         and step['tool'] == 'python' and 1 <= len(step['arguments']) <= 256 and
         type(step['timeout_s']) is int and step['timeout_s'] == (900 if tag == 'nf_tap_latchup' else 600), 'STEP_SHAPE')
    total = 0
    def expand(value):
        nonlocal total
        need(isinstance(value, str) and len(value.encode()) <= 65536 and '\x00' not in value, 'ARGUMENT')
        pieces, size, start = [], 0, 0
        def append(piece):
            nonlocal size
            need(isinstance(piece, str) and len(piece) <= 65536, 'ARGUMENT'); size += len(piece.encode())
            need(size <= 65536 and total + size <= 1 << 20, 'ARGUMENT_TOTAL'); pieces.append(piece)
        for match in re.finditer(r'\{output\}|\{threads\}|\{(files|tools):([A-Za-z0-9_-]+)\}', value):
            append(value[start:match.start()]); append(str(root) if match[0] == '{output}' else
                str(manifest['scope']['threads']) if match[0] == '{threads}' else manifest[match[1]][match[2]]['path'])
            start = match.end()
        append(value[start:]); total += size; result = ''.join(pieces)
        need(not re.search(r'\{(?:files|tools):', result), 'ARGUMENT_UNRESOLVED'); return result
    return [expand(manifest['tools']['python']['path']), *map(expand, step['arguments'])], step['timeout_s']


def capture(argv, output, deadline):
    available = deadline - time.monotonic()
    need(available > 0, 'INSUFFICIENT_CHILD_TIME')
    def child_limits():
        resource.setrlimit(resource.RLIMIT_AS, (4096 << 20, 4096 << 20))
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True, preexec_fn=child_limits)
    tick, count = time.monotonic(), 0; paths = {n: output / (n + '.log') for n in ('stdout', 'stderr')}
    try:
        with selectors.DefaultSelector() as selector, paths['stdout'].open('xb') as stdout, paths['stderr'].open('xb') as stderr:
            for source, target in ((child.stdout, stdout), (child.stderr, stderr)):
                selector.register(source, selectors.EVENT_READ, target)
            while selector.get_map():
                need(time.monotonic() < deadline, 'CHILD_DEADLINE')
                for key, _ in selector.select(.1):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj); key.fileobj.close(); continue
                    count += len(data); need(count <= 16 << 20, 'CHILD_LOG_BUDGET'); key.data.write(data)
            code = child.wait(timeout=max(.01, deadline - time.monotonic()))
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=5)
        for stream in (child.stdout, child.stderr):
            if stream is not None:
                stream.close()
    return dict(argv=argv, pid=child.pid, exit_code=code, wall_seconds=time.monotonic() - tick,
                available_seconds=available,
                stdout=identity(paths['stdout'], 16 << 20), stderr=identity(paths['stderr'], 16 << 20))


def run(runner_path, arguments):
    tick = time.monotonic(); root = Path.cwd().resolve(strict=True)
    native, mid, manifest = ownership(root); files, tools = manifest['files'], manifest['tools']
    guard = record(files['tap_guard'], 1 << 20); need(identity(__file__, 1 << 20) == guard, 'CAPTURE_SOURCE')
    runner_path = Path(runner_path).resolve(strict=True)
    roles = [r for r in ('tap_latchup_runner', 'tap_coverage_runner') if files[r]['path'] == str(runner_path)]
    need(len(roles) == 1, 'RUNNER_ROLE'); role = roles[0]; latchup = role == 'tap_latchup_runner'
    tag = 'nf_tap_latchup' if latchup else 'nf_tap_coverage'; argv, seconds = selected_argv(root, manifest, tag)
    python = record(tools['python'], 256 << 20)
    need(identity(sys.executable, 256 << 20) == python and
         argv == [python['path'], guard['path'], str(runner_path), *arguments], 'ACTUAL_ARGV')
    parser = argparse.ArgumentParser(allow_abbrev=False)
    for flag in ('workspace', 'tool'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    if latchup:
        parser.add_argument('--gds', type=Path, required=True); parser.add_argument('--profile', type=Path, required=True)
        parser.add_argument('--top', required=True)
    else:
        parser.add_argument('--config', type=Path, required=True); parser.add_argument('--tool-sha256', required=True)
        parser.add_argument('--tool-bytes', type=int, required=True)
    args = parser.parse_args(arguments); work = root / ('backend/latchup' if latchup else 'backend/tap-coverage')
    need(args.base_rev == 'empty' and args.workspace.resolve() == work and not work.exists(), 'FRESH_CHECKER_WORKSPACE')
    sources = dict(guard=guard, runner=record(files[role], 1 << 20)); inputs = {}
    if latchup:
        need(args.top == 'ibex_sram_bench', 'TOP'); sources['common'] = record(files['tap_latchup_common'], 1 << 20)
        inputs.update(gds=identity(args.gds, 64 << 20), profile=identity(args.profile, 2 << 20))
        need(Path(inputs['profile']['path']) == root / 'backend/postroute/effective-inputs/foundry/provenance.json'
             or inputs['profile'] == files['drc_profile'], 'PROFILE_PATH')
        inputs['deck'] = identity(args.profile.resolve().parent / 'klayout/tech/drc/rule_decks/sg13g2_maximal.drc', 256 << 20)
    else:
        sources['worker'] = record(files['tap_coverage_worker'], 1 << 20)
        need((sources['worker']['sha256'], sources['worker']['bytes']) == WORKER, 'WORKER_SOURCE')
        inputs['config'] = identity(args.config, 1 << 20)
        need(Path(inputs['config']['path']) == root / 'backend/tap-coverage-inputs.json', 'CONFIG_PATH')
        configuration = read(args.config, 1 << 20); inputs['gds'] = record(configuration['gds'], 64 << 20)
        need(configuration['workspace'] == str(work) and configuration['base_revision'] == 'empty', 'CONFIG_WORKSPACE')
    tool = record(tools['appimage'], 2 << 30)
    need(identity(args.tool, 2 << 30) == tool and (latchup or
         (args.tool_sha256, args.tool_bytes) == (tool['sha256'], tool['bytes'])), 'TOOL')
    output = root / ('backend/latchup-execution' if latchup else 'backend/tap-coverage-execution'); output.mkdir()
    with (output / 'workspace.lock').open('x') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        child = capture([python['path'], str(runner_path), *arguments], output, tick + seconds - 10)
        need(type(child['exit_code']) is int and child['exit_code'] == 0, 'CHILD_NONZERO')
        outputs = dict(summary=identity(work / 'summary.json', 4 << 20), head=identity(work / 'HEAD', 128))
        need(Path(outputs['head']['path']).read_bytes() == b'rev1\n', 'CHECKER_HEAD')
        summary = read(outputs['summary']['path']); need(summary['status'] == 'passed', 'SEMANTIC_STATUS')
        if latchup:
            outputs.update(raw_report=identity(work / 'drc.lyrdb', 64 << 20), command=identity(work / 'command.json', 65536))
            need(summary['gds'] == inputs['gds'] and summary['profile'] == inputs['profile'] and
                 summary['deck'] == inputs['deck'] and summary['tool'] == tool, 'LATCHUP_BINDING')
            need(read(child['stdout']['path'], 2 << 20) == {k: summary[k] for k in
                 ('schema', 'status', 'complete', 'violations', 'wall_seconds')}, 'LATCHUP_STDOUT')
        else:
            outputs['controller'] = identity(work / 'controller.json', 4 << 20); controller = read(outputs['controller']['path'])
            need(controller['status'] == 'passed' and type(controller['exit_code']) is int and controller['exit_code'] == 0
                 and controller['report'] == outputs['summary'] and controller['config'] == inputs['config'] and
                 controller['worker'] == sources['worker'] and controller['tool'] == tool and
                 summary['gds'] == inputs['gds'] and summary['config'] == inputs['config'], 'COVERAGE_BINDING')
        for value, cap in [(mid, 2 << 20), (python, 256 << 20), (tool, 2 << 30),
                           *[(v, 1 << 20) for v in sources.values()], *[(v, 256 << 20) for v in inputs.values()]]:
            record(value, cap)
        need(ownership(root)[0] == native, 'OWNER_CHANGED_AFTER_CHILD')
        result = dict(schema='rusteda.joint-tap-execution.v1', status='captured_passed_requires_joint_qualification',
            producer_step=tag, input_manifest=mid, ownership=identity(root.parent / 'ownership/native.json', 65536),
            source_pins=sources, tools=dict(python=python, appimage=tool), inputs=inputs, outputs=outputs, child=child,
            actual_python_children=1, actual_native_processes=None, command_deadline_seconds=seconds,
            cleanup_reserve_seconds=10,
            native_process_count_scope='not inferred; exact existing checker/controller command is separately bound',
            wall_seconds=time.monotonic() - tick)
        raw = json.dumps(result, indent=2, allow_nan=False) + '\n'; need(len(raw.encode()) <= 65536, 'RESULT_BUDGET')
        need(time.monotonic() < tick + seconds, 'OVERALL_DEADLINE')
        with (output / 'result.json').open('x') as stream:
            stream.write(raw)
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps(dict(schema='rusteda.joint-tap-emission.v1', status=result['status'],
            producer_step=tag, execution_result=identity(output / 'result.json', 65536)), allow_nan=False))


if __name__ == '__main__':
    try:
        need(len(sys.argv) >= 2, 'RUNNER_REQUIRED'); resource.setrlimit(resource.RLIMIT_AS, (256 << 20, 4096 << 20))
        run(sys.argv[1], sys.argv[2:])
    except (OSError, ValueError, KeyError, TypeError, MemoryError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(status='unverified', error=str(error))), file=sys.stderr); raise SystemExit(2)

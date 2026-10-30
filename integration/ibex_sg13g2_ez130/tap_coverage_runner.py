#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Daemonless pinned runner for the independent tap coverage macro.

The caller supplies cgroup, memory, wall and log guards. This controller adds
<32MiB metadata; native geometry estimate/limits are in tap_coverage.py. The
worker exclusively owns workspace.lock and verifies the empty base revision.
Both the process exit and bound semantic report must pass. No report is inferred
from a zero reference-tool exit. Macro identity/full foundry gates are separate.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess


def identity(path, cap):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    if not path.is_file() or before.st_size > cap:
        raise ValueError('TAP_COVERAGE_FILE_BUDGET')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('TAP_COVERAGE_FILE_CHANGED')
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    parser.add_argument('--tool', type=Path, required=True)
    parser.add_argument('--tool-sha256', required=True)
    parser.add_argument('--tool-bytes', type=int, required=True)
    args = parser.parse_args()
    config_id = identity(args.config, 1 << 20)
    config = json.loads(Path(config_id['path']).read_bytes())
    workspace = args.workspace.resolve()
    if (args.base_rev != 'empty' or config.get('base_revision') != args.base_rev
            or Path(config['workspace']).resolve() != workspace):
        raise ValueError('REVISION_CONFLICT_OR_WORKSPACE_MISMATCH')
    if not re.fullmatch('[a-f0-9]{64}', args.tool_sha256) or not 0 < args.tool_bytes <= 2 << 30:
        raise ValueError('TAP_COVERAGE_TOOL_IDENTITY_SCHEMA')
    tool = identity(args.tool, 2 << 30)
    if (tool['sha256'], tool['bytes']) != (args.tool_sha256, args.tool_bytes):
        raise ValueError('TAP_COVERAGE_TOOL_IDENTITY')
    worker = identity(Path(__file__).with_name('tap_coverage.py'), 1 << 20)
    controller = identity(__file__, 1 << 20)
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if any(path.name != 'workspace.lock' for path in workspace.iterdir()):
            raise ValueError('REVISION_CONFLICT')
    command = [tool['path'], 'klayout', '-b', '-r', worker['path'], '-rd', 'config=' + config_id['path']]
    result = subprocess.run(command, stdin=subprocess.DEVNULL)
    for record, cap in ((tool, 2 << 30), (worker, 1 << 20), (controller, 1 << 20), (config_id, 1 << 20)):
        if identity(record['path'], cap) != record:
            raise ValueError('TAP_COVERAGE_EXECUTION_INPUT_CHANGED')
    if identity(config['gds']['path'], 64 << 20) != config['gds']:
        raise ValueError('TAP_COVERAGE_GDS_CHANGED')
    with (workspace / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        report_id = identity(workspace / 'summary.json', 4 << 20)
        report = json.loads(Path(report_id['path']).read_bytes())
        if (report.get('checker') != worker or report.get('config') != config_id
                or report.get('gds') != config['gds'] or (workspace / 'HEAD').read_text() != 'rev1\n'):
            raise ValueError('TAP_COVERAGE_REPORT_BINDING')
        passed = (result.returncode == 0 and report.get('status') == 'passed'
                  and report.get('schema') == 'rusteda.tap-coverage-result.v1'
                  and set(report.get('rules', {})) == {'LU.a', 'LU.b'}
                  and all(row.get('status') == 'passed' for row in report['rules'].values()))
        receipt = dict(schema='rusteda.tap-coverage-controller.v1', status='passed' if passed else 'unverified',
                       command=command, exit_code=result.returncode, tool=tool, worker=worker,
                       controller=controller, config=config_id, report=report_id)
        with (workspace / 'controller.json').open('x') as stream:
            json.dump(receipt, stream, indent=2, allow_nan=False)
            stream.write('\n')
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())

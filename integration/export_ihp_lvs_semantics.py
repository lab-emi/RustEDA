#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Export complete LVS netlists through KLayout's documented public API.

Run via the pinned AppImage's Python, under a 3 GiB / 180 s cgroup guard.
Upfront estimate: <=128 MiB database * 16 decode overhead + 256 MiB for
two <=128 MiB serialized netlists, one worker/corner, below 3 GiB. There is
no path enumeration, flattening, device filtering or equivalence hint here.
Raw database FileIDs remain distinct even when complete netlist exports match.

Public API documentation (not implementation source):
https://www.klayout.de/doc/code/class_LayoutVsSchematic.html
https://www.klayout.de/doc/code/class_Netlist.html#to_s
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import time

import klayout.db as db


def identity(path):
    path = path.resolve(strict=True)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=str(path), sha256=digest, bytes=path.stat().st_size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.base_rev != 'empty' or any(path.name != 'workspace.lock' for path in output.iterdir()):
            raise ValueError('REVISION_CONFLICT')
        if not args.database.is_file() or args.database.stat().st_size > 128 << 20:
            raise ValueError('LVS database exceeds 128 MiB input budget')
        source, policy = identity(args.database), identity(Path(__file__))
        started = time.monotonic()
        data = db.LayoutVsSchematic()
        data.read(source['path'])
        errors = []
        for error in data.each_error():
            if len(errors) >= 128 or len(str(error)) > 4096:
                raise ValueError('extraction diagnostic budget exceeded')
            errors.append(str(error))
        if errors:
            raise ValueError('database contains extraction errors')
        exports = {}
        for label, netlist in (('extracted', data.netlist()), ('reference', data.reference)):
            if netlist is None:
                raise ValueError('database omits complete ' + label + ' netlist')
            circuits = 0
            for _ in netlist.each_circuit():
                circuits += 1
                if circuits > 4096:
                    raise ValueError('circuit count budget exceeded')
            text = netlist.to_s()
            if not text or len(text) > 128 << 20:
                raise ValueError('complete netlist export exceeds byte budget')
            path = output / (label + '.netlist')
            path.write_text(text)
            exports[label] = dict(file=identity(path), circuits=circuits,
                                  scope='complete hierarchical netlist; no flattening/filtering/hints')
            del text
        if identity(args.database) != source or identity(Path(__file__)) != policy:
            raise ValueError('source changed during API export')
        result = dict(schema='rusteda.ihp-lvs-semantic-export.v1', status='exported_not_compared',
            database=source, policy=policy, klayout_version=db.__version__, exports=exports,
            extraction_errors=errors, wall_seconds=time.monotonic() - started,
            public_api=['LayoutVsSchematic.read', 'LayoutToNetlist.netlist',
                        'LayoutVsSchematic.reference', 'Netlist.to_s'], full_flow='unverified')
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps(dict(status=result['status'], output=str(output))))


if __name__ == '__main__':
    main()

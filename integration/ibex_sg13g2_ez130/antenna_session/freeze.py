#!/usr/bin/env python3
"""Freeze the bounded single-process antenna helpers into a new run directory."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

FILES = ('native_antenna_groups.py', 'native_antenna_groups_legacy.py',
         'native_antenna.py', 'report_fields.py', 'verify.py', 'antenna-session-policy.json')


def identity(path):
    if path.is_symlink():
        raise ValueError('helper source must not be a symlink')
    path = path.resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 1 << 20:
        raise ValueError('helper file exceeds the source byte/type bound')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=str(path), sha256=digest, bytes=path.stat().st_size)


def freeze(destination):
    source = Path(__file__).resolve().parent
    destination = destination.resolve()
    if destination.exists() or destination == source or source in destination.parents:
        raise ValueError('REVISION_CONFLICT: helper destination must be new and outside its sources')
    # Six files, each <=1 MiB; streaming hashing/copying keeps scratch <2 MiB.
    before = {name: identity(source / name) for name in FILES}
    destination.mkdir(parents=True)
    records = []
    for name, original in before.items():
        target = destination / name
        shutil.copyfile(source / name, target)
        copied = identity(target)
        if copied['sha256'] != original['sha256'] or identity(source / name) != original:
            raise ValueError('helper source changed while freezing')
        records.append(dict(original=original['path'], **copied))
    manifest = destination / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema='rusteda.private-source-review.v1',
        status='prepared_not_qualified', sources=records,
        scope='single native process; independently verified typed operations; foundry signoff separate'),
        indent=2) + '\n')
    return identity(manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', choices=['empty'], required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace), indent=2))

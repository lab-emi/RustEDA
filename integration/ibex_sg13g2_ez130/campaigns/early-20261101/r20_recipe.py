#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Import, verify and select the exact R20/R21 recipe inputs.

R20's inputs_frozen gate pins every recipe file it read by path, SHA-256 and
size. Five of them differ from the run16 recipe in the parent directory. Those
files keep their pinned identities, so they live beside the run16 files under
the names in RECIPE. No command here accepts a byte that differs from the pin.

  import [--source-root DIR]  copy the pinned files from the campaign host
                              (DIR replaces the recorded checkout root)
  verify [--manifest M]       check the recipe files and, with M, require the
                              composed manifest's files.<role> to be R20's
  overlay --config C --output O
                              rewrite a composer config's files.<role> records
                              to the verified R20 files
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
RECIPE_DIR = HERE.parents[1]
REPO = HERE.parents[3]
EVIDENCE = REPO / 'docs/benchmarks/evidence/ibex-20261101'
FROZEN = [EVIDENCE / f'{run}-early-sta/gates/inputs_frozen.json' for run in ('R20', 'R21')]
RECORDED_ROOT = Path('/home/cgao/git/RustEDA')
# role -> name in the recipe directory (run16 names stay pinned by older receipts)
RECIPE = {'physical': 'native-physical-r20.json', 'routing': 'native-routing-r20.json',
          'prepare': 'native_frontend_inputs_r20.py', 'rc_policy': 'rc-estimate-policy-r20.json',
          'contract': 'contract-r20.json'}
COMPOSED = ('physical', 'routing', 'prepare')
CAP = 1 << 20


def fail(text):
    raise SystemExit(f'r20_recipe: {text}')


def pins():
    """role -> (sha256, bytes, recorded paths); R20 and R21 must agree."""
    out = {}
    for path in FROZEN:
        files = json.loads(path.read_text())['files']
        for role in RECIPE:
            rec = files[role]
            old = out.get(role)
            if old and old[:2] != (rec['sha256'], rec['bytes']):
                fail(f'{role}: R20 and R21 pin different bytes')
            paths = old[2] if old else []
            out[role] = (rec['sha256'], rec['bytes'], paths + [rec['path']] * (rec['path'] not in paths))
    return out


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(path):
    data = Path(path).read_bytes()
    return dict(path=str(Path(path).resolve()), sha256=digest(data), bytes=len(data))


def check(role, path, pin):
    """'match', 'missing' or 'mismatch' for one recipe file."""
    if not path.is_file():
        return 'missing'
    if path.stat().st_size > CAP:
        return 'mismatch'
    rec = identity(path)
    return 'match' if (rec['sha256'], rec['bytes']) == pin[:2] else 'mismatch'


def import_files(source_root, recipe_dir, roles):
    table = pins()
    report = {}
    for role in roles:
        pin, dest = table[role], recipe_dir / RECIPE[role]
        state = check(role, dest, pin)
        if state == 'match':
            report[role] = 'already present'
            continue
        if state == 'mismatch':
            fail(f'{dest} exists with other bytes; refusing to overwrite')
        tried = []
        for recorded in pin[2]:
            source = source_root / Path(recorded).relative_to(RECORDED_ROOT)
            if not source.is_file():
                tried.append(f'{source} (missing)')
                continue
            if source.stat().st_size != pin[1]:
                tried.append(f'{source} ({source.stat().st_size} B, pinned {pin[1]} B)')
                continue
            data = source.read_bytes()
            if digest(data) != pin[0]:
                tried.append(f'{source} (sha256 {digest(data)}, pinned {pin[0]})')
                continue
            temp = dest.with_name(dest.name + '.tmp')
            temp.write_bytes(data)
            os.replace(temp, dest)
            report[role] = f'imported from {source}'
            break
        else:
            report[role] = 'not imported: ' + '; '.join(tried)
    return report


def verify(recipe_dir, manifest=None):
    table = pins()
    report = {role: check(role, recipe_dir / name, table[role]) for role, name in RECIPE.items()}
    ok = all(state == 'match' for state in report.values())
    result = dict(recipe=report)
    if manifest is not None:
        files = json.loads(manifest.read_text())['files']
        composed = {}
        for role in RECIPE:
            if role not in files:
                if role in COMPOSED:
                    composed[role] = 'absent'
                continue
            rec, pin = files[role], table[role]
            recorded = (rec['sha256'], rec['bytes']) == pin[:2]
            composed[role] = 'match' if recorded and check(role, Path(rec['path']), pin) == 'match' else 'mismatch'
        result['manifest'] = composed
        ok = ok and all(state == 'match' for state in composed.values())
    result['ok'] = ok
    return result


def overlay(config, recipe_dir):
    table = pins()
    files = config['files']
    for role, name in RECIPE.items():
        if role in files:
            path = recipe_dir / name
            if check(role, path, table[role]) != 'match':
                fail(f'{path} is not the pinned R20 {role}; run import first')
            files[role] = identity(path)
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['import', 'verify', 'overlay'])
    parser.add_argument('--recipe-dir', type=Path, default=RECIPE_DIR)
    parser.add_argument('--source-root', type=Path, default=RECORDED_ROOT)
    parser.add_argument('--role', action='append', choices=sorted(RECIPE))
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'import':
        report = import_files(args.source_root, args.recipe_dir, args.role or list(RECIPE))
        print(json.dumps(report, indent=2))
        return 0 if all(not state.startswith('not imported') for state in report.values()) else 1
    if args.command == 'verify':
        result = verify(args.recipe_dir, args.manifest)
        print(json.dumps(result, indent=2))
        return 0 if result['ok'] else 1
    if args.config is None or args.output is None:
        fail('overlay needs --config and --output')
    config = overlay(json.loads(args.config.read_text()), args.recipe_dir)
    args.output.write_text(json.dumps(config, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Freeze explicit foundry-first helpers/data into a new private source bundle.

Five <=1MiB effective inputs,64KiB streaming, controller estimate<32MiB.
This prepares protocol sources, never qualifies a tool or launches EDA.
"""
from pathlib import Path
import argparse
import json

try:
    from . import protocol as p
    from .runner import ROLES
except ImportError:
    import protocol as p
    from runner import ROLES


def freeze(destination):
    destination = Path(destination).absolute()
    owner = p.real_path(destination.parent)
    p.private_path(destination, owner)
    p.need(not destination.exists(), 'REVISION_CONFLICT')
    source = p.real_path(Path(__file__).parent)
    p.need(not destination.is_relative_to(source), 'SOURCE_DESTINATION_ALIAS')
    destination.mkdir()
    policy = destination / 'policy.json'
    p.write_new(policy, p.POLICY)
    policy.chmod(0o444)
    records = {}
    for role, filename in ROLES.items():
        if filename != 'policy.json':
            p.freeze_file(source / filename, destination / filename, destination, 1 << 20)
        records[role] = p.identity(destination / filename, 1 << 20)
    manifest = destination / 'manifest.json'
    p.write_new(manifest, dict(schema='rusteda.foundry-first-source-bundle.v1', sources=records))
    manifest.chmod(0o444)
    return p.identity(manifest, 1 << 20)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', choices=['empty'], required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace)))

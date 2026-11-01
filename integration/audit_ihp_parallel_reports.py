#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Audit full independent report contents without hiding an LVS DB mismatch.

<=128 raw timing files, <=2 MiB timing JSON/65536 nodes/depth32, <=256 MiB
per streamed report/4 MiB per line. Estimate <64 MiB, one worker/corner.
Run under a 1 GiB/120 s guard. A differing .lvsdb remains unverified; this
diagnostic never reports complete B4 acceptance or a fresh whole-flow pass.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path

from verify_ihp_parallel_signoff import COPIES, REPORTS, normalized_digest
from ibex_sg13g2_ez130 import producer


def require(value, message):
    if not value:
        raise ValueError(message)


def file_ids(value, depth=0, state=None):
    state = [0] if state is None else state
    state[0] += 1
    require(depth <= 32 and state[0] <= 65536, 'JSON node/depth budget')
    if isinstance(value, dict):
        if set(value) == {'path', 'sha256', 'bytes'}:
            yield value
        else:
            for item in value.values():
                yield from file_ids(item, depth + 1, state)
    elif isinstance(value, list):
        for item in value:
            yield from file_ids(item, depth + 1, state)


def audit(replay):
    roots = [replay / mode for mode in ('serial', 'parallel')]
    replacements = [[(str(root).encode(), b'<sample>')] for root in roots]
    canonical_ids, evidence = [{}, {}], []
    timings = [producer.read_json(root / 'backend/timing/timing.json') for root in roots]
    leaf_records = []
    for root, timing in zip(roots, timings):
        records = {}
        for record in file_ids(timing):
            path = Path(record['path'])
            require(path.is_relative_to(root), 'unexpected external timing report FileID')
            require(producer.identity(path) == record, 'stale timing report binding')
            records[str(path.relative_to(root))] = record
        require(1 <= len(records) <= 128, 'timing report file count budget')
        leaf_records.append(records)
    require(set(leaf_records[0]) == set(leaf_records[1]), 'timing report inventory differs')
    for relative in sorted(leaf_records[0]):
        hashes = [normalized_digest(root / relative, replace) for root, replace in zip(roots, replacements)]
        require(hashes[0] == hashes[1], 'raw timing report content differs: ' + relative)
        row = dict(report=relative, raw=[records[relative] for records in leaf_records], normalized_sha256=hashes[0])
        evidence.append(row)
        for index in range(2):
            canonical_ids[index][row['raw'][index]['path']] = dict(path=relative, sha256=hashes[0],
                bytes='verified complete content; raw byte lengths retained separately')
            replacements[index].append((row['raw'][index]['sha256'].encode(), hashes[0].encode()))

    raw_databases = [producer.identity(root / 'backend/lvs/layout.lvsdb') for root in roots]
    database_equal = raw_databases[0]['sha256'] == raw_databases[1]['sha256']
    for relative in REPORTS:
        if relative == 'backend/lvs/layout.lvsdb':
            continue
        if relative == 'backend/timing/timing.json':
            canonical = []
            for index, value in enumerate(timings):
                def normalize(item):
                    if isinstance(item, dict):
                        if set(item) == {'path', 'sha256', 'bytes'}:
                            return canonical_ids[index][item['path']]
                        return {key: normalize(child) for key, child in item.items()}
                    if isinstance(item, list):
                        return [normalize(child) for child in item]
                    if isinstance(item, str):
                        raw = item.encode()
                        for before, after in replacements[index]:
                            raw = raw.replace(before, after)
                        return raw.decode()
                    return item
                canonical.append(normalize(value))
            require(canonical[0] == canonical[1], 'complete timing report values differ')
            hashes = [hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                      for value in canonical]
        elif relative == 'backend/lvs/check_lvs.json':
            checks = [producer.read_json(root / relative) for root in roots]
            for index, value in enumerate(checks):
                require(value['database_sha256'] == raw_databases[index]['sha256'], 'stale LVS database root')
                # Retain actual roots in the result. This field is explicitly
                # unresolved, never silently normalized into a byte-parity pass.
                value['database_sha256'] = '<UNVERIFIED: different raw database identity>'
            strings = [json.dumps(value, sort_keys=True) for value in checks]
            hashes = []
            for string, replace in zip(strings, replacements):
                raw = string.encode()
                for before, after in replace:
                    raw = raw.replace(before, after)
                hashes.append(hashlib.sha256(raw).hexdigest())
            require(hashes[0] == hashes[1], 'full LVS checker evidence differs beyond database identity')
        else:
            hashes = [normalized_digest(root / relative, replace) for root, replace in zip(roots, replacements)]
            require(hashes[0] == hashes[1], 'independent report content differs: ' + relative)
        identities = [producer.identity(root / relative) for root in roots]
        evidence.append(dict(report=relative, raw=identities, normalized_sha256=hashes[0]))
        for replace, identity in zip(replacements, identities):
            replace.append((identity['sha256'].encode(), hashes[0].encode()))
    for relative in COPIES:
        identities = [producer.identity(root / relative) for root in roots]
        require(all(identities[0][key] == identities[1][key] for key in ('sha256', 'bytes')),
                'physical input bytes changed: ' + relative)
    return dict(status='full_report_contents_match_except_unverified_database_identity',
        b4_acceptance='unverified', full_flow='unverified', reports=evidence,
        raw_lvs_databases=raw_databases, raw_database_bytes_equal=database_equal,
        lvs_semantic_signature='unverified',
        unresolved_fields=['backend/lvs/check_lvs.json.database_sha256; actual roots retained and verified'],
        normalization=['exact workspace prefixes', 'SPEF DATE',
            'generated timing FileIDs only after complete raw report/Tcl comparison and raw SHA/size verification',
            'JSON object key ordering; every value retained'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(path.name != 'workspace.lock' for path in output.iterdir()),
                'REVISION_CONFLICT')
        result = audit(args.replay.resolve(strict=True))
        result['policy'] = producer.identity(Path(__file__))
        producer.write_json(output / 'result.json', result)
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps(dict(status=result['status'], b4_acceptance=result['b4_acceptance'], result=str(output / 'result.json'))))


if __name__ == '__main__':
    main()

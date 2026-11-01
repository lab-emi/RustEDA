#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Prepare fresh, byte-preserving GDS views and nominal native RC collateral.

No historical layout or optimization plan is an input. The original GDS
structures are copied verbatim after unit/conflict checks. The first-party
Road RC profile builder consumes only pinned, original PDK data files.

Peak estimate <128 MiB for this 1.8 MiB library bundle. Hard caps: 32 MiB GDS,
1M records, 8192 structures, 2 MiB other inputs, 512 MiB address space.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import signal
import struct
import sys


KEYS = {"std_gds", "ram_gds", "layer_map", "tech_lef", "rules_nom", "magic_tech",
        "magic_extract", "rc_builder"}
LAYERS = ("Metal1", "Via1", "Metal2", "Via2", "Metal3", "Via3", "Metal4", "Via4",
          "Metal5", "TopVia1", "TopMetal1", "TopVia2", "TopMetal2")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identity(path):
    path = path.resolve(strict=True)
    require(path.is_file(), "regular input file required")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return dict(path=str(path), sha256=digest, bytes=path.stat().st_size)


def read(path):
    require(path.is_file() and path.stat().st_size <= 2 << 20, "JSON byte budget")
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def bundle(sources, output):
    blocks, units, header = {}, None, None
    records, total = 0, 0
    for path in sources:
        total += path.stat().st_size
        require(total <= 32 << 20, "aggregate original GDS byte budget")
        data = path.read_bytes()
        offset, start, name, current_units, header_end = 0, None, None, None, None
        end_library = False
        while offset < len(data):
            records += 1
            require(records <= 1000000 and offset + 4 <= len(data), "GDS record budget/truncation")
            size, kind, dtype = struct.unpack_from('>HBB', data, offset)
            require(size >= 4 and size % 2 == 0 and offset + size <= len(data), "malformed GDS record")
            payload = data[offset + 4:offset + size]
            if kind == 3:
                require(dtype == 5 and len(payload) == 16 and current_units is None, "GDS units record")
                current_units, header_end = payload, offset + size
            if kind == 5:
                require(start is None and current_units is not None, "nested/unscaled GDS structure")
                start = offset
            if kind == 6:
                require(start is not None and name is None, "misplaced GDS structure name")
                name = payload.rstrip(b'\0').decode('ascii')
                require(0 < len(name) <= 512, "GDS structure name budget")
            if kind == 7:
                require(start is not None and name is not None, "unmatched GDS ENDSTR")
                raw = data[start:offset + size]
                require(name not in blocks or blocks[name] == raw, "conflicting original GDS structure")
                blocks[name] = raw
                require(len(blocks) <= 8192, "GDS structure count budget")
                start, name = None, None
            if kind == 4:
                require(start is None and not any(data[offset + size:]), "GDS trailing/unfinished structure")
                end_library = True
                break
            offset += size
        require(end_library and current_units is not None, "incomplete GDS library")
        require(units is None or units == current_units, "GDS units differ; implicit rescale forbidden")
        units = current_units
        if header is None:
            header = data[:header_end]
    require(header is not None and blocks, "empty GDS bundle")
    result = header + b''.join(blocks[key] for key in sorted(blocks)) + b'\x00\x04\x04\x00'
    require(len(result) <= 32 << 20, "GDS output byte budget")
    output.write_bytes(result)
    return dict(sources=[identity(p) for p in sources], output=identity(output),
                structures=len(blocks), records=records,
                scope="byte-identical original structures; EZ header; no geometric edits")


def layer_map(path):
    data = path.read_text()
    require(len(data) <= 2 << 20, "layer-map byte budget")
    digest = identity(path)['sha256']
    rows = []
    for number, line in enumerate(data.splitlines(), 1):
        columns = line.split()
        if len(columns) == 4 and not columns[0].startswith('#'):
            try:
                rows.append((columns[0], columns[1], int(columns[2]), int(columns[3]), number))
            except ValueError:
                pass  # Unrelated public map constructs are not part of this explicit projection.
    require(len(rows) <= 4096, "layer-map row budget")
    entries = []
    for name in LAYERS:
        own = [r for r in rows if r[0] == name or r[0] == 'NAME' and r[1] == name + '/PIN']
        require(own, "missing mapped layer " + name)
        cut = 'Via' in name
        entry = dict(lef=name, kind='cut' if cut else 'routing', net=[], special_net=[],
                     via=[], pin=[], label=None,
                     cite=[dict(source='ihp_map', sha256=digest, lines=[r[4] for r in own])])
        for _, purpose, layer, datatype, _ in own:
            if purpose == name + '/PIN':
                entry['label'] = [layer, datatype]
                continue
            for token, key in (('NET', 'net'), ('SPNET', 'special_net'), ('VIA', 'via'), ('PIN', 'pin')):
                if token in purpose.split(',') and not (cut and key != 'via'):
                    if [layer, datatype] not in entry[key]:
                        entry[key].append([layer, datatype])
        require(entry['via'] and (cut or entry['net'] and entry['special_net']
                and entry['pin'] and entry['label']), "incomplete mapped geometry: " + name)
        entries.append(entry)
    boundary = [r for r in rows if r[0] == 'DIEAREA']
    require(len(boundary) == 1, "unique die boundary map required")
    boundary = boundary[0]
    return dict(schema='rustroad.gds-layer-map.v1', pdk='ihp-sg13g2', library='ez130_8t_sram',
                sources=[dict(id='ihp_map', role='klayout_lefdef_layer_map', **identity(path))],
                layers=entries, boundary=dict(layer=[boundary[2], boundary[3]],
                cite=[dict(source='ihp_map', sha256=digest, lines=[boundary[4]])]))


def prepare(config, output):
    require(set(config) == {'schema', 'files', 'pdk_root'}
            and config['schema'] == 'rusteda.native-physical-inputs.v1', 'input schema')
    require(set(config['files']) == KEYS, 'explicit PDK input set required')
    paths = {key: Path(record['path']) for key, record in config['files'].items()}
    for key, path in paths.items():
        require(identity(path) == config['files'][key], 'input changed: ' + key)
        require(path.stat().st_size <= (32 << 20 if key.endswith('_gds') else 2 << 20),
                'input byte budget: ' + key)
    pdk = Path(config['pdk_root']).resolve(strict=True) / 'ihp-sg13g2'
    choices = {(pdk / ('libs.tech/librelane/openrcx/' + name)).resolve()
               for name in ('IHP_rcx_patterns.rules', 'ihp-sg13g2.nom.magic.rules')}
    require(paths['rules_nom'].resolve() in choices
            and paths['magic_tech'].resolve() == (pdk / 'libs.tech/magic/ihp-sg13g2.tech').resolve()
            and paths['magic_extract'].resolve() == (pdk / 'libs.tech/magic/ihp-sg13g2-extract.tech').resolve(),
            'RC PDK root differs from pinned data')
    write(output / 'gds-inputs.json', bundle([paths['std_gds'], paths['ram_gds']], output / 'combined-cells.gds'))
    write(output / 'gds-map.json', layer_map(paths['layer_map']))
    spec = importlib.util.spec_from_file_location('rusteda_pinned_rc_builder', paths['rc_builder'])
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.PDKS['ihp']['technology_lef'] = str(paths['tech_lef'].resolve())
    # Both original calibrations are explicit, auditable inputs. The benchmark
    # contract selects the PDK default patterns; the historical Magic control
    # remains reproducible with its original source profile builder.
    profile = builder.build('ihp', 'nom', pdk.parent, 'openrcx-pdk', paths['rules_nom'].resolve())
    expected = {identity(paths[k])['sha256'] for k in ('rules_nom', 'magic_tech', 'magic_extract', 'tech_lef')}
    require({source['sha256'] for source in profile['sources']} == expected, 'RC profile sources differ')
    (output / 'ihp-rc-nom.json').write_text(builder.render(profile))
    for key, path in paths.items():
        require(identity(path) == config['files'][key], 'input changed during preparation: ' + key)
    result = dict(status='prepared_not_qualified', inputs=config['files'],
                  artifacts={key: identity(output / name) for key, name in
                  [('cell_gds', 'combined-cells.gds'), ('gds_map', 'gds-map.json'),
                   ('rc_nom', 'ihp-rc-nom.json')]},
                  unverified=['layout', 'routing', 'DRC', 'antenna', 'LVS', 'extracted_timing', 'benchmark'])
    write(output / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    signal.alarm(180)
    args.workspace = args.workspace.resolve()
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock'
                for p in args.workspace.iterdir()), 'REVISION_CONFLICT')
        result = prepare(read(args.inputs), args.workspace)
        (args.workspace / 'HEAD').write_text('rev1\n')
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        raise SystemExit(2)

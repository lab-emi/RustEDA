#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Generate source-local reference ECOs from bounded current-run public reports.

Headroom is an optimization target, never a relaxed signoff constraint. The
census must contain canonical STA net names and exclude both propagated clocks
and OpenDB CLOCK nets. Original TT/SS DRV obligations remain mandatory.
Planner peak <128 MiB: <=8 MiB census, two <=16 MiB cap tables processed serially,
<=100k census rows and <=8192 plan rows. No timing graph or paths are built.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import resource
import sys

from reference_ecos import build, identity, read, require


def prepare(config, census_path, cap_paths, evidence, output, capacitance_only=False):
    require(config.get('schema') == 'rusteda.reference-drv-inputs.v1', 'input schema')
    files = config['files'].copy()
    for record in files.values():
        require(identity(Path(record['path'])) == record, 'source input changed')
    original = read(Path(files['drv_bindings']['path']))
    require(original['netlist'] == files['netlist'] and original['sdc'] == files['sdc']
            and original['cppr'] == 'off', 'original report binding mismatch')
    require(census_path.stat().st_size <= 8 << 20, 'census byte budget')
    census, total_names = {}, 0
    with census_path.open() as stream:
        for number, line in enumerate(stream, 1):
            require(number <= 100000 and len(line) <= 16384, 'census row budget')
            values = line.rstrip('\n').split('\t')
            require(len(values) == 8, 'census columns')
            pin, master, net, sigtype, clock, logic, diodes, loads = values
            require(all(0 < len(x) <= 4096 and '\x00' not in x for x in (pin, master, net)), 'census names')
            total_names += len(pin) + len(master) + len(net)
            require(total_names <= 8 << 20, 'census name budget')
            clock, logic, diodes, loads = map(int, (clock, logic, diodes, loads))
            require(clock in (0, 1) and 0 <= logic <= loads <= 100000
                    and 0 <= diodes and logic + diodes == loads, 'census counts')
            require(pin not in census, 'duplicate census driver')
            census[pin] = dict(pin=pin, master=master, net=net, clock=clock,
                               sigtype=sigtype, loads=loads, diodes=diodes)
    fans = []
    for pin, row in sorted(census.items()):
        if capacitance_only:
            break
        if row['clock'] or row['sigtype'] == 'CLOCK' or row['loads'] <= 4:
            continue
        require(row['loads'] <= 40, 'headroom hierarchy would exceed original root fanout10')
        fans.append(dict(kind='max_fanout', pin=pin, master=row['master'], net=row['net'],
                         report_values=[10, row['loads'], 10-row['loads'],
                                        '(VIOLATED)' if row['loads'] > 10 else '(MET)'], engine='public_odb'))
    bindings = {}
    for corner in ('tt', 'ss'):
        cap_path = cap_paths[corner]
        require(cap_path.stat().st_size <= 16 << 20, 'cap table byte budget')
        caps, count = {}, 0
        with cap_path.open() as stream:
            for line in stream:
                require(len(line) <= 16384, 'cap report line budget')
                values = line.split()
                if len(values) != 5 or values[-1] not in ('(MET)', '(VIOLATED)'):
                    continue
                count += 1
                require(count <= 100000, 'cap table row budget')
                pin = values[0]
                limit, actual, slack = map(float, values[1:4])
                require(0 < limit < 10000 and 0 <= actual < 10000
                        and -10000 < slack < 10000, 'cap table numeric budget')
                if actual <= limit * 0.8:
                    continue
                require(pin in census, 'headroom cap missing public instance binding: ' + pin)
                row = census[pin]
                if row['clock'] or row['sigtype'] == 'CLOCK':
                    continue
                caps[pin] = dict(kind='max_capacitance', pin=pin, master=row['master'],
                                 net=row['net'], report_values=values[1:], engine='opensta')
        require(count > 1000, 'incomplete full-design cap census')
        joined = {(row['kind'], row['pin']): row for row in [*caps.values(), *fans]}
        for row in original['bindings'][corner]:
            if capacitance_only and (row['kind'], row['pin']) not in joined:
                require(row['pin'] in census, 'original DRV has no public driver binding')
                actual = census[row['pin']]
                require(all(actual[k] == row[k] for k in ('pin', 'net', 'master'))
                        and not actual['clock'] and actual['sigtype'] != 'CLOCK',
                        'original DRV/public driver mismatch')
                joined[row['kind'], row['pin']] = row
            require((row['kind'], row['pin']) in joined,
                    'original DRV obligation omitted: ' + row['pin'])
            planned = joined[row['kind'], row['pin']]
            require(all(planned[k] == row[k] for k in ('pin', 'net', 'master')), 'census/report mismatch')
        bindings[corner] = sorted(joined.values(), key=lambda row: (row['kind'], row['pin']))
    derived = {k: original[k] for k in ('schema', 'netlist', 'sdc', 'spef', 'cppr')}
    if capacitance_only:
        for key in ('native_extra', 'annotation_scope'):
            if key in original:
                derived[key] = original[key]
    derived['bindings'] = bindings
    derived['planning'] = dict(mode='headroom', evidence=evidence)
    path = output / 'headroom-bindings.json'
    path.write_text(json.dumps(derived, indent=2) + '\n')
    files['drv_bindings'] = identity(path)
    policy = dict(prefix='lneco21', buffer='BUFX8', upsize={'BUFX1':'BUFX4'},
                  max_fanout_sinks=4, max_repairs=8192, max_pin_visits=1000000,
                  fanout_placement='driver')
    if capacitance_only:
        policy = dict(config['policy'])
        require(policy.get('fanout_placement') == 'cluster'
                and policy.get('preserve_placement') is True
                and policy.get('incremental_global_route') is True,
                'capacitance headroom requires incremental placement preservation')
    new_config = dict(schema=config['schema'], files=files, policy=policy)
    (output / 'inputs.json').write_text(json.dumps(new_config, indent=2) + '\n')
    result = build(new_config, output)
    result['headroom'] = dict(fanout=None if capacitance_only else 4, capacitance_percent=80,
                             mode='capacitance_only' if capacitance_only else 'capacitance_and_all_fanout',
                             original_max_fanout=10, constraints_unchanged=True,
                             census_drivers=len(census), clock_drivers=sum(bool(r['clock']) for r in census.values()),
                             provenance=evidence)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--census', required=True, type=Path)
    parser.add_argument('--tt-cap', required=True, type=Path)
    parser.add_argument('--ss-cap', required=True, type=Path)
    parser.add_argument('--census-script', required=True, type=Path)
    parser.add_argument('--ss-script', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--base-rev', required=True)
    parser.add_argument('--capacitance-only', action='store_true',
                        help='Keep original fanout obligations and add only 80-percent capacitance headroom')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    args.workspace = args.workspace.resolve()
    args.workspace.mkdir(exist_ok=True, parents=True)
    with (args.workspace / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock'
                for p in args.workspace.iterdir()), 'REVISION_CONFLICT')
        config = read(args.inputs)
        evidence = {k: identity(v) for k, v in dict(source_inputs=args.inputs,
                    source_bindings=Path(config['files']['drv_bindings']['path']),
                    census=args.census, tt_cap=args.tt_cap, ss_cap=args.ss_cap,
                    census_script=args.census_script, ss_script=args.ss_script).items()}
        result = prepare(config, args.census, {'tt': args.tt_cap, 'ss': args.ss_cap}, evidence,
                         args.workspace, args.capacitance_only)
        (args.workspace / 'plan.json').write_text(json.dumps(result, indent=2) + '\n')
        (args.workspace / 'HEAD').write_text('rev1\n')
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Bind this run's GRT reports/immutable ODB to a reference ECO input.

Caller provides the owned memory scope/deadline. Controller <256MiB; <=100k
census rows/1M terminal visits/8MiB census, <=16MiB/report, <=1024 raw reported
rows across TT+SS and64MiB combined mappings. One child. Actual ODB connections
project input slew obligations to unique drivers; every raw row is retained.
Reported-only estimates are never timing signoff.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

from reference_ecos import quote, read, require

FILES = {'tt_state', 'ss_state', 'tt_report', 'ss_report', 'odb', 'sdc', 'cppr',
         'tt_std', 'tt_ram', 'ss_std', 'ss_ram'}
MAX_REPORTED_ROWS = 1024
MAX_MAPPING_BYTES = 64 << 20


def identity(path):
    path = path.resolve(strict=True)
    size = path.stat().st_size
    require(path.is_file() and size <= 4 << 30, 'file/tool byte budget')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    require(path.stat().st_size == size, 'file changed while hashing')
    return dict(path=str(path), sha256=digest, bytes=size)


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def external(command, output):
    start = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    used = 0
    try:
        with (output / 'census.log').open('xb') as log, selectors.DefaultSelector() as select:
            os.set_blocking(process.stdout.fileno(), False)
            select.register(process.stdout, selectors.EVENT_READ)
            while select.get_map():
                require(time.monotonic()-start <= 180, 'census external deadline exceeded')
                for key, _ in select.select(.1):
                    data = os.read(key.fileobj.fileno(), 16384)
                    if not data:
                        select.unregister(key.fileobj)
                        continue
                    require(used+len(data) <= 4 << 20, 'census log byte budget')
                    log.write(data)
                    used += len(data)
            code = process.wait(timeout=5)
        require(code == 0, 'census external command failed')
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.kill()
            process.wait(timeout=5)
    return dict(wall_seconds=time.monotonic()-start, exit_code=code, log_bytes=used)


def census_script(files, odb, output, requested=()):
    require(len(requested) <= MAX_REPORTED_ROWS, 'reported pin projection budget')
    lines = ['define_corners tt ss', 'set_thread_count 2']
    for corner in ('tt', 'ss'):
        for kind in ('std', 'ram'):
            lines.append('read_liberty -corner '+corner+' '+quote(files[corner+'_'+kind]['path']))
    lines += ['read_db '+quote(odb), 'read_sdc '+quote(files['sdc']['path']),
              'source '+quote(files['cppr']['path']),
              'set block [[[ord::get_db] getChip] getBlock]',
              'if {[$block getDbUnitsPerMicron] != 1000} {error "unexpected DBU"}',
              'set out [open '+quote(output / 'census.tsv')+' w]',
              'set projection [open '+quote(output / 'projection.tsv')+' w]',
              'set wanted [dict create '+ ' '.join(quote(name)+' 1' for name in sorted(requested)) + ']']
    lines += r'''
set nets 0; set visits 0; set rows 0; set census_bytes 0; set projection_bytes 0
set found {}
proc safe_fields {values} {
 foreach value $values {
  if {[string length $value]>4096 || [regexp {[\t\r\n]} $value]} {error "unsafe census field"}
 }
}
foreach net [$block getNets] {
 if {[$net getSigType] in {POWER GROUND}} {continue}
 incr nets
 if {$nets>100000} {error "net census budget"}
 set drivers {}; set port_drivers 0; set bidirectional 0; set loads 0; set diodes 0; set requested_terms {}
 foreach pin [$net getITerms] {
  incr visits
  if {$visits>1000000} {error "pin visit budget"}
  set type [$pin getIoType]
  if {$type eq "OUTPUT"} {lappend drivers $pin}
  if {$type eq "INOUT"} {set bidirectional 1}
  set reported [[$pin getInst] getName]/[[$pin getMTerm] getName]
  if {[dict exists $wanted $reported]} {lappend requested_terms [list $reported $pin $type]}
  if {$type eq "INPUT"} {
   incr loads
   if {[string match ANTENNA* [[[$pin getInst] getMaster] getName]]} {incr diodes}
  }
 }
 foreach pin [$net getBTerms] {
  incr visits
  if {$visits>1000000} {error "pin visit budget"}
  set type [$pin getIoType]
  if {$type eq "INPUT"} {incr port_drivers}
  if {$type eq "OUTPUT"} {incr loads}
  if {$type eq "INOUT"} {set bidirectional 1}
 }
 if {[llength $requested_terms]>0} {
  if {$bidirectional} {error "reported net has an ambiguous bidirectional terminal"}
  set count [expr {[llength $drivers]+$port_drivers}]
  if {$count==0} {error "reported net has no actual ODB driver"}
  if {$count!=1} {error "reported net has multiple actual ODB drivers"}
  if {$port_drivers} {error "reported net has a top-level port driver; instance ECO policy required"}
 }
 if {$loads<1 || [llength $drivers]!=1} {continue}
 if {$port_drivers || $bidirectional} {continue}
 set driver [lindex $drivers 0]
 set name [[$driver getInst] getName]/[[$driver getMTerm] getName]
 set pin [get_pins -quiet $name]
 if {[llength $pin]!=1} {error "driver has no unique STA binding"}
 set clocks [get_property $pin clocks]
 set clock [expr {[llength $clocks]>0 || [$net getSigType] eq "CLOCK"}]
 set master [[[$driver getInst] getMaster] getName]
 set netname [get_full_name [get_nets -of_objects $pin]]
 safe_fields [list $name $master $netname]
 set row [join [list $name $master $netname [$net getSigType] $clock [expr {$loads-$diodes}] $diodes $loads] "\t"]
 incr census_bytes [expr {[string bytelength $row]+1}]
 if {$census_bytes>8388608 || $census_bytes+$projection_bytes>67108864} {error "census mapping byte budget"}
 puts $out $row
 incr rows
 foreach request $requested_terms {
  lassign $request reported term direction
  if {[dict exists $found $reported]} {error "reported terminal is not unique in ODB"}
  set target [get_pins -quiet $reported]
  if {[llength $target]!=1} {error "reported terminal has no unique STA binding"}
  set target_clock [expr {[llength [get_property $target clocks]]>0}]
  safe_fields [list $reported $direction $name $master $netname]
  set mapped [join [list $reported $direction $name $master $netname [$net getSigType] $clock $loads $target_clock] "\t"]
  incr projection_bytes [expr {[string bytelength $mapped]+1}]
  if {$projection_bytes>67108864 || $census_bytes+$projection_bytes>67108864} {error "projection mapping byte budget"}
  puts $projection $mapped
  dict set found $reported 1
 }
}
foreach reported [dict keys $wanted] {
 if {![dict exists $found $reported]} {error "reported terminal missing or unsupported in actual ODB: $reported"}
}
close $out
close $projection
puts "RUSTEDA_CENSUS $nets $visits $rows"
'''.strip().splitlines()
    lines += ['write_verilog '+quote(output / 'netlist.v'),
              'write_verilog -include_pwr_gnd '+quote(output / 'netlist.pnl.v')]
    return 'if {[catch {\n'+'\n'.join(lines)+'\n} message options]} {puts stderr $message; puts stderr [dict get $options -errorinfo]; exit 2}\nexit\n'


def parse_report(path):
    require(path.stat().st_size <= 16 << 20, 'GRT report byte budget')
    text = path.read_text()
    sections = text.split('report_check_types', 1)
    require(len(sections) == 2, 'GRT check table missing')
    section = sections[1].split('report_parasitic_annotation', 1)[0]
    rows, kind = [], None
    kinds = {'max slew': 'max_slew', 'max capacitance': 'max_capacitance', 'max fanout': 'max_fanout'}
    base = text[:text.index('report_check_types')].count('\n') + 1
    for offset, line in enumerate(section.splitlines()):
        require(len(line) <= 16384, 'GRT report line budget')
        if line.strip() in kinds:
            kind = kinds[line.strip()]
        if '(VIOLATED)' not in line:
            continue
        values = line.split()
        require(kind is not None and len(values) in (4, 5) and values[-1] == '(VIOLATED)', 'GRT violation row format')
        pin = values[0]
        require(isinstance(pin, str) and 0 < len(pin) <= 4096 and '/' in pin, 'instance reported pin required')
        limit, actual = map(float, values[1:3])
        reported_slack = float(values[3]) if len(values) == 5 else None
        slack = reported_slack if reported_slack is not None else float(Decimal(values[1])-Decimal(values[2]))
        require(all(math.isfinite(x) and 0 <= x < 10000 for x in (limit, actual)), 'GRT numeric row budget')
        require(math.isfinite(slack) and abs(slack) < 10000, 'GRT slack numeric budget')
        rows.append(dict(kind=kind, reported_pin=pin, limit=limit, actual=actual, slack=slack,
                         reported_slack=reported_slack,
                         slack_source='original_opensta_report' if reported_slack is not None else 'computed_limit_minus_actual; original_report_omitted_slack',
                         raw_fields=values[1:], raw=line,
                         report_line=base+offset))
        require(len(rows) <= MAX_REPORTED_ROWS, 'GRT raw reported row budget')
    require(len({(r['kind'], r['reported_pin']) for r in rows}) == len(rows), 'duplicate raw GRT obligation')
    for kind, word in (('max_slew', 'slew'), ('max_capacitance', 'cap'), ('max_fanout', 'fanout')):
        counts = re.findall(r'max '+word+r' violation count (\d+)', text)
        require(len(counts) == 1 and int(counts[0]) == sum(r['kind'] == kind for r in rows),
                'GRT counter/report row mismatch')
    return rows


def bind_rows(raw, census, projections, clock_fanout_only=False):
    groups = {}
    for row in raw:
        reported = row['reported_pin']
        require(reported in projections, 'reported pin missing from actual ODB projection: ' + reported)
        mapped = projections[reported]
        pin = mapped['driver_pin']
        require(pin in census and mapped['direction'] in {'INPUT', 'OUTPUT'}, 'projected ODB driver/direction missing')
        item = census[pin]
        require(all(mapped[key] == item[key] for key in ('master', 'net', 'sigtype', 'clock', 'loads')),
                'projection differs from actual ODB driver census')
        clock = bool(item['clock'] or mapped['reported_clock'] or item['sigtype'] == 'CLOCK')
        if clock:
            require(clock_fanout_only and row['kind'] == 'max_fanout',
                    'clock ECO needs a separate fanout-only qualified policy: ' + reported)
        elif clock_fanout_only:
            continue
        require(mapped['direction'] == 'OUTPUT' or row['kind'] == 'max_slew', 'only input slew can project from a load terminal')
        if row['kind'] == 'max_fanout':
            require(row['limit'] == 10 and row['actual'] == item['loads'], 'actual ODB/report fanout differs')
        key = row['kind'], pin
        if key not in groups:
            groups[key] = dict(kind=row['kind'], pin=pin, master=item['master'], net=item['net'],
                               report_values=dict(projection='unique_actual_odb_driver', obligations=[]), engine='opensta')
        groups[key]['report_values']['obligations'].append({**row, 'reported_direction': mapped['direction']})
    expected = sum(bool(projections[row['reported_pin']]['clock'] or projections[row['reported_pin']]['reported_clock']
                        or projections[row['reported_pin']]['sigtype'] == 'CLOCK') == clock_fanout_only
                   for row in raw) if clock_fanout_only else len(raw)
    require(sum(len(row['report_values']['obligations']) for row in groups.values()) == expected, 'selected raw GRT obligation lost during grouping')
    return [groups[key] for key in sorted(groups)]


def prepare(config, output, clock_fanout_only=False):
    require(set(config) == {'schema', 'run_root', 'files', 'tools', 'policy'}
            and config['schema'] == 'rusteda.reference-grt-inputs.v1', 'GRT input schema')
    files, tools = config['files'], config['tools']
    require(set(files) == FILES and set(tools) == {'appimage'}, 'GRT input/tool set')
    for group in (files, tools):
        for record in group.values():
            require(identity(Path(record['path'])) == record, 'GRT input/tool identity changed')
    root = Path(config['run_root']).resolve(strict=True)
    for key in ('tt_state', 'ss_state', 'tt_report', 'ss_report'):
        require(Path(files[key]['path']).resolve().is_relative_to(root), 'report is outside the current run')
    tt, ss = (read(Path(files[key]['path'])) for key in ('tt_state', 'ss_state'))
    odb = Path(tt['odb']).resolve(strict=True)
    require(odb.is_relative_to(root) and odb == Path(ss['odb']).resolve(strict=True), 'GRT corners use different/outside geometry')
    before = identity(odb)
    require(before == files['odb'] and before['bytes'] <= 128 << 20, 'ODB identity/byte budget')
    raw = {corner: parse_report(Path(files[corner+'_report']['path'])) for corner in ('tt', 'ss')}
    require(sum(map(len, raw.values())) <= MAX_REPORTED_ROWS, 'combined GRT raw reported row budget')
    write(output / 'raw-obligations.json', dict(schema='rusteda.reference-grt-obligations.v1',
          source_reports={corner: files[corner+'_report'] for corner in ('tt', 'ss')},
          obligations=raw, raw_count={corner: len(value) for corner, value in raw.items()}, source_odb=before,
          scope='all original reported-pin rows and limits; no clock/signal row is removed from this evidence'))
    wanted = {row['reported_pin'] for rows in raw.values() for row in rows}
    script = output / 'census.tcl'
    script.write_text(census_script(files, odb, output, wanted))
    command = [tools['appimage']['path'], 'openroad', '-exit', str(script)]
    write(output / 'command.json', command)
    resources = external(command, output)
    path = output / 'census.tsv'
    require(path.stat().st_size <= 8 << 20, 'census byte budget')
    projection_path = output / 'projection.tsv'
    require(projection_path.is_file() and path.stat().st_size + projection_path.stat().st_size <= MAX_MAPPING_BYTES,
            'combined driver projection mapping byte budget')
    census = {}
    with path.open() as stream:
        for index, line in enumerate(stream, 1):
            require(index <= 100000 and len(line) <= 16384, 'census row budget')
            pin, master, net, sigtype, clock, logic, diodes, loads = line.rstrip('\n').split('\t')
            require(pin not in census, 'duplicate census driver')
            clock, logic, diodes, loads = map(int, (clock, logic, diodes, loads))
            require(clock in (0, 1) and 0 <= logic <= loads <= 100000 and 0 <= diodes
                    and logic+diodes == loads, 'census counts')
            census[pin] = dict(master=master, net=net, clock=clock, sigtype=sigtype, loads=loads)
    projections = {}
    with projection_path.open() as stream:
        for index, line in enumerate(stream, 1):
            require(index <= MAX_REPORTED_ROWS and len(line) <= 16384, 'projection row budget')
            reported, direction, driver_pin, master, net, sigtype, clock, loads, reported_clock = line.rstrip('\n').split('\t')
            require(reported in wanted and reported not in projections, 'unexpected/duplicate projected reported pin')
            clock, loads, reported_clock = int(clock), int(loads), int(reported_clock)
            require(clock in (0, 1) and reported_clock in (0, 1) and 0 <= loads <= 100000, 'projection source count')
            projections[reported] = dict(direction=direction, driver_pin=driver_pin, master=master,
                                         net=net, sigtype=sigtype, clock=clock, loads=loads, reported_clock=reported_clock)
    require(set(projections) == wanted, 'not every original reported pin has an actual ODB projection')
    rows = {corner: bind_rows(raw[corner], census, projections, clock_fanout_only) for corner in ('tt', 'ss')}
    evidence = {key: files[key] for key in ('tt_state', 'ss_state', 'tt_report', 'ss_report')}
    evidence.update(census=identity(path), census_script=identity(script), projection=identity(projection_path),
                    raw_obligations=identity(output / 'raw-obligations.json'))
    binding = dict(schema='rusteda.reference-grt-bindings.v1', netlist=identity(output / 'netlist.v'),
        sdc=files['sdc'], cppr='off', bindings=rows,
        parasitics=dict(wire_model='global_routing_estimate', signoff=False, coverage='reported_violators_only'),
        planning=dict(mode='headroom', evidence=evidence))
    require(len(json.dumps(binding, allow_nan=False).encode()) <= 2 << 20, 'planner bindings byte budget')
    write(output / 'bindings.json', binding)
    eco_files = {key: files[key] for key in ('sdc', 'cppr', 'tt_std', 'tt_ram', 'ss_std', 'ss_ram')}
    eco_files.update(odb=before, netlist=binding['netlist'], drv_bindings=identity(output / 'bindings.json'))
    policy = dict(config['policy'])
    if clock_fanout_only:
        policy['clock_fanout'] = True
    write(output / 'eco-inputs.json', dict(schema='rusteda.reference-drv-inputs.v1', files=eco_files, policy=policy))
    for record in [*files.values(), *tools.values(), before]:
        require(identity(Path(record['path'])) == record, 'GRT sources changed during collection')
    write(output / 'summary.json', dict(status='prepared_not_qualified', needs_eco=any(rows.values()),
        scope='clock_fanout_only_repair_plan' if clock_fanout_only else 'estimated GRT reported-only DRV; complete extracted signoff still required',
        clock_fanout_only=clock_fanout_only, all_drv_passed=False,
        observed_rows={corner: len(value) for corner, value in raw.items()},
        raw_count={corner: len(value) for corner, value in raw.items()},
        planned_count={corner: len(value) for corner, value in rows.items()},
        input_slew_projections={corner: sum(ob['kind'] == 'max_slew' and projections[ob['reported_pin']]['direction'] == 'INPUT'
                                           for ob in value) for corner, value in raw.items()}, odb=before,
        remaining_signal_raw_count={corner: sum(not (projections[ob['reported_pin']]['clock']
                                         or projections[ob['reported_pin']]['reported_clock']
                                         or projections[ob['reported_pin']]['sigtype'] == 'CLOCK') for ob in value)
                                    for corner, value in raw.items()} if clock_fanout_only else {},
        census_drivers=len(census), resources=resources, inputs=config,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    parser.add_argument('--clock-fanout-only', action='store_true',
                        help='plan only actual CLOCK fanout repairs; retain all original signal obligations as unresolved evidence')
    args = parser.parse_args()
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock' for p in output.iterdir()), 'REVISION_CONFLICT')
        prepare(read(args.inputs), output, args.clock_fanout_only)
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps(dict(status='prepared_not_qualified', output=str(output))))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(status='failed', error=str(error))))
        raise SystemExit(2)

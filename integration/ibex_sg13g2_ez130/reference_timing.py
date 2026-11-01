#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Qualify one reference OpenRCX extraction without inventing native parity.

Input: rusteda.reference-timing-inputs.v1, with the frozen signoff_timing policy,
its ordinary pinned files/tools/budgets, exactly one spef_openrcx, and scope mode
single_reference_openrcx. This adapter retains the original diagnostic outputs.
Absolute checks use their conservative min-slack/max-violation rows; four native
versus reference differences remain diagnostic, even when above 1 ps. Native
two-extraction qualification is a separate, unchanged gate.

If native completeness rejects the input, fresh external TT/SS observations and
public-API pin/master/net bindings are still produced for repair. They cannot
pass this gate. Constant-disabled endpoints require the native enumeration and
individual original OpenSTA witnesses. Original models and inputs are read-only.

Memory estimate: native state <=2 GiB plus <=512 MiB bounded policy/JSON state;
corners and external processes run sequentially, with two workers. No paths or
cones are enumerated here. Use an OS 4 GiB RAM / 1 GiB swap scope. Child commands
retain the frozen policy's address-space, output, row and wall-clock limits.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time
from types import SimpleNamespace


MIB = 1 << 20
GIB = 1 << 30
POLICY_SHA256 = '551298f6ededb81ee9d97bfdcde64383d4245da374b6e90b60462aa66e76bec3'
FILES = {'netlist', 'sdc', 'std_tt', 'ram_tt', 'std_ss', 'ram_ss', 'physical_lef', 'spef_openrcx'}
TOOLS = {'rusteda', 'ruststa', 'opensta', 'gnu_time'}
CORNERS = ('tt', 'ss')
SCOPE = dict(rc_corner='nom', cppr='off', precision='f64', backend='cpu',
             clock_model='propagated', wire_model='spef_rc')
BUDGETS = dict(threads=2, timeout_seconds=180, max_input_bytes=128*MIB,
               max_output_bytes=256*MIB, max_state_gib=2,
               max_limit_rows=100000, max_checks=100000)
KINDS = ('setup', 'hold', 'slew', 'cap', 'fanout')
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


class WallBudgetExceeded(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def record(path):
    return dict(path=str(path.resolve()), sha256=digest(path), bytes=path.stat().st_size)


def read_json(path, cap=64*MIB):
    require(path.is_file() and path.stat().st_size <= cap, 'JSON_BYTE_BUDGET: '+str(path))
    return json.loads(path.read_text())


def write_json(path, value, cap=64*MIB):
    data = json.dumps(value, indent=2, allow_nan=False)+'\n'
    require(len(data.encode()) <= cap, 'REPORT_BYTE_BUDGET: '+str(path))
    path.write_text(data)


def finite(value):
    require(type(value) in (int, float) and math.isfinite(value), 'NONFINITE_METRIC')
    return float(value)


def count(value):
    require(type(value) is int and value >= 0, 'INVALID_COUNT')
    return value


def name(value):
    require(isinstance(value, str) and 0 < len(value.encode()) <= 4096
            and not any(c in value for c in '{}\x00\r\n\t') and not value.endswith('\\'), 'UNSUPPORTED_NAME')
    return value


def word(value):
    return '{'+name(str(value))+'}'


def annotation_rows(text, maximum):
    """Parse the public indentation format, retaining missing-load witnesses."""
    sections = []
    for line in text.splitlines():
        match = re.fullmatch(r'Found (\d+) (partially )?unannotated drivers\.', line)
        if match:
            sections.append(dict(partial=bool(match[2]), expected=int(match[1]), rows=[]))
        elif line.strip():
            require(sections and line.startswith(' '), 'ANNOTATION_FORMAT')
            pin = name(line.strip())
            if line.startswith('  '):
                require(sections[-1]['partial'] and sections[-1]['rows'], 'ANNOTATION_LOAD_OWNER')
                sections[-1]['rows'][-1]['unannotated_loads'].append(pin)
            else:
                sections[-1]['rows'].append(dict(pin=pin, unannotated_loads=[]))
            require(sum(len(s['rows']) for s in sections) <= maximum, 'ANNOTATION_ROW_BUDGET')
    require(len(sections) == 2 and [s['partial'] for s in sections] == [False, True], 'ANNOTATION_SECTIONS')
    require(all(len(s['rows']) == s['expected'] for s in sections), 'ANNOTATION_ROW_COVERAGE')
    rows = [dict(**r, partial=s['partial']) for s in sections for r in s['rows']]
    require(sum(len(r['unannotated_loads']) for r in rows) <= maximum, 'ANNOTATION_LOAD_BUDGET')
    require(len({r['pin'] for r in rows}) == len(rows), 'ANNOTATION_DUPLICATE_DRIVER')
    return dict(unannotated_drivers=sections[0]['expected'],
                partially_unannotated_drivers=sections[1]['expected'], rows=rows)


def drv_rows(path, kind, maximum):
    text = path.read_text()
    result = []
    for line in text.splitlines():
        if '(VIOLATED)' not in line:
            continue
        match = re.fullmatch(r'\s*(\S+)\s+('+NUMBER+r')\s+('+NUMBER+r')(?:\s+('+NUMBER+r'))?\s+\(VIOLATED\)', line)
        require(match is not None, 'DRV_ROW_FORMAT: '+line)
        limit, value = finite(float(match[2])), finite(float(match[3]))
        result.append(dict(pin=name(match[1]), kind=kind, limit=limit, value=value,
                           slack=finite(float(match[4])) if match[4] else limit-value,
                           slack_basis='reported' if match[4] else 'limit_minus_value_missing_report_column',
                           report=record(path), raw=line))
        require(len(result) <= maximum, 'DRV_ROW_BUDGET')
    require(len(result) == text.count('(VIOLATED)'), 'DRV_ROW_COVERAGE')
    return result


class Run:
    def __init__(self, args):
        self.root = args.workspace.resolve()
        self.config_path = args.inputs.resolve(strict=True)
        c = self.config = read_json(self.config_path, 2*MIB)
        require(set(c) == {'schema', 'top', 'files', 'tools', 'policy', 'budgets', 'scope'}, 'INPUT_KEYS')
        require(c['schema'] == 'rusteda.reference-timing-inputs.v1', 'INPUT_SCHEMA')
        require(isinstance(c['top'], str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]{0,255}', c['top']), 'TOP_IDENTIFIER')
        require(set(c['files']) == FILES and set(c['tools']) == TOOLS, 'INPUT_RECORD_KEYS')
        require(set(c['budgets']) == set(BUDGETS), 'BUDGET_KEYS')
        self.b = c['budgets']
        for key, maximum in BUDGETS.items():
            require(type(self.b[key]) is int and 1 <= self.b[key] <= maximum, 'BUDGET_RANGE: '+key)
        require(set(c['scope']) == set(SCOPE) | {'mode', 'tolerance_ns'}, 'SCOPE_KEYS')
        require(all(c['scope'][k] == v for k, v in SCOPE.items()), 'SCOPE_UNSUPPORTED')
        require(c['scope']['mode'] == 'single_reference_openrcx', 'SCOPE_MODE')
        require(0 < finite(c['scope']['tolerance_ns']) <= .001, 'TOLERANCE_RANGE')
        self.initial = {str(self.config_path): digest(self.config_path),
                        str(Path(__file__).resolve()): digest(Path(__file__).resolve())}
        for label, item in [*c['files'].items(), *c['tools'].items(), ('policy', c['policy'])]:
            require(isinstance(item, dict) and set(item) == {'path', 'sha256', 'bytes'}, 'RECORD_KEYS: '+label)
            require(isinstance(item['path'], str), 'RECORD_PATH: '+label)
            path = Path(item['path']).resolve(strict=True)
            cap = 256*MIB if label in TOOLS else self.b['max_input_bytes']
            require(str(path) == item['path'] and path.is_file() and path.stat().st_size <= cap, 'INPUT_PATH_OR_BUDGET: '+label)
            require(record(path) == item, 'STALE_INPUT: '+label)
            if label in TOOLS:
                with path.open('rb') as stream:
                    require(stream.read(4) == b'\x7fELF', 'DIRECT_ELF_REQUIRED: '+label)
            self.initial[str(path)] = item['sha256']
        require(c['policy']['sha256'] == POLICY_SHA256, 'UNSUPPORTED_BASE_POLICY')
        require(sum(v['bytes'] for v in c['files'].values()) <= 256*MIB, 'AGGREGATE_INPUT_BUDGET')
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root/'workspace.lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock' for p in self.root.iterdir()), 'REVISION_CONFLICT')
        (self.root/'logs').mkdir()
        self.started = time.monotonic()
        self.result = dict(schema='rusteda.reference-timing-result.v1', status='running',
                           input_manifest=record(self.config_path), policy=record(Path(__file__).resolve()),
                           base_policy=c['policy'], inputs=c['files'], tools=c['tools'], scope=c['scope'],
                           estimate=dict(peak_bytes=5*GIB//2, native_state_bytes=2*GIB,
                                         policy_bytes=GIB//2, threads=self.b['threads'], corners=2,
                                         execution='sequential; no graph/path enumeration'), resources=[])
        self.checkpoint()
        spec = importlib.util.spec_from_file_location('rusteda_frozen_signoff_timing', c['policy']['path'])
        self.base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.base)
        config = {k: v for k, v in c.items() if k != 'policy'}
        config['schema'] = 'rusteda.signoff-timing-inputs.v1'
        config['scope'] = {**c['scope'], 'mode': 'diagnostic'}
        self.derived_path = self.root/'diagnostic-inputs.json'
        write_json(self.derived_path, config)
        self.base_args = dict(inputs=self.derived_path, base_rev='empty')

    def checkpoint(self):
        write_json(self.root/'result.json', self.result, self.b['max_output_bytes'])

    def base_run(self, directory):
        return self.base.Run(SimpleNamespace(workspace=self.root/directory, **self.base_args))

    def diagnostic(self):
        run = self.base_run('diagnostic')
        try:
            run.execute()
        except (ValueError, OSError, KeyError, TypeError, MemoryError, subprocess.TimeoutExpired) as error:
            run.result.update(status='failed', error=str(error), wall_seconds=time.monotonic()-run.started)
            run.checkpoint()
        self.result['resources'] += [dict(branch='diagnostic', **r) for r in run.resources]
        self.result['diagnostic_result'] = record(run.root/'result.json')
        path = run.root/'logs'/'openrcx-run.stdout'
        if path.is_file():
            document = read_json(path, self.b['max_output_bytes'])
            self.result['native_analysis'] = dict(status='completed' if document.get('ok') else 'rejected',
                diagnostics=document.get('diagnostics', []), error=document.get('error'), report=record(path))
        else:
            self.result['native_analysis'] = dict(status='unverified', error=run.result.get('error'))
        return run

    def raw_reference(self, run, corner, witness_available, validation_error=None):
        """Read only fresh public reports; validation errors remain explicit failures."""
        prefix = 'openrcx-'+corner+'-'
        reports = run.root/'reports'
        log_path = run.root/'logs'/('openrcx-'+corner+'-opensta.stdout')
        text = log_path.read_text()
        require('RUSTEDA_CPPR_OFF 0' in text and 'OpenSTA 3.1.' in text, 'REFERENCE_SCOPE_OR_VERSION')
        require(not re.search(r'^Error[: ]|^Warning[: ]', text, re.M), 'REFERENCE_TOOL_DIAGNOSTIC')
        require(re.search(r'^ time 1ns$', text, re.M) and re.search(r'^ capacitance 1pF$', text, re.M), 'REFERENCE_UNITS')
        row = dict(status='diagnostic', helper_validation_error=validation_error,
                   constant_witness_scope='enumerated_native_endpoints' if witness_available else 'unverified_native_analysis_rejected',
                   setup_ws_ns=finite(float(re.search(r'worst slack max ('+NUMBER+r')', text)[1])),
                   hold_ws_ns=finite(float(re.search(r'worst slack min ('+NUMBER+r')', text)[1])),
                   reported_check_rows={}, drv_rows=[])
        for kind in KINDS:
            path = reports/(prefix+kind+'.rpt')
            require(path.stat().st_size < self.b['max_output_bytes'], 'REFERENCE_REPORT_BUDGET')
            contents = path.read_text()
            checked = contents.count('(VIOLATED)')+contents.count('(MET)')
            require(checked < self.b['max_checks'], 'REFERENCE_CHECK_TRUNCATION')
            row['reported_check_rows'][kind] = checked
            row[kind+'_violations'] = contents.count('(VIOLATED)')
            if kind in ('slew', 'cap', 'fanout'):
                row['drv_rows'] += drv_rows(path, kind, self.b['max_checks'])
        for kind in ('setup', 'hold'):
            require((row[kind+'_ws_ns'] < 0) == (row[kind+'_violations'] > 0), 'REFERENCE_TIMING_COVERAGE')
        row['annotation'] = annotation_rows((reports/(prefix+'annotation.rpt')).read_text(), self.b['max_checks'])
        row['check_setup_findings'] = (reports/(prefix+'scope.rpt')).read_text().strip()
        row['reports'] = {p.name: record(p) for p in reports.iterdir() if p.name.startswith(prefix)}
        row['stdout'] = record(log_path)
        return row

    def observe(self, diagnostic):
        run = self.base_run('observation')
        run.physical_models()
        output = {}
        for co in CORNERS:
            path = diagnostic.root/'logs'/('openrcx-'+co+'-unconstrained.stdout')
            available = path.is_file()
            native = dict(unconstrained=read_json(path)) if available else {
                'unconstrained': {'sections': {'unconstrained': {'rows': []}}}}
            error, oracle = None, None
            try:
                oracle = run.reference('openrcx', co, native)
            except (ValueError, OSError, KeyError, TypeError, MemoryError, subprocess.TimeoutExpired) as exc:
                error = str(exc)
            try:
                row = self.raw_reference(run, co, available, error)
                row['constant_disabled_endpoints'] = oracle['constant_disabled_endpoints'] if oracle and available else None
                output[co] = row
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
                output[co] = dict(status='failed', error=str(exc), helper_validation_error=error,
                                  constant_witness_scope='unverified', reports={})
        run.result.update(status='observation_only', corners=output,
                          unverified_items=['reference_gate', 'native_dual_source_parity'])
        run.checkpoint()
        self.result['resources'] += [dict(branch='observation', **r) for r in run.resources]
        self.result['observation_result'] = record(run.root/'result.json')
        return run, output

    def bindings(self, run, observations, rows):
        require(set(observations) == set(CORNERS) and all(
            all(kind+'_violations' in r for kind in KINDS) and 'annotation' in r
            for r in observations.values()), 'BINDINGS_REQUIRE_COMPLETE_REFERENCE_OBSERVATIONS')
        union = {}
        annotation = {}
        for co, observed in observations.items():
            for violation in observed.get('drv_rows', []):
                pin = violation['pin']
                union.setdefault(pin, []).append(dict(engine='opensta', corner=co, **violation))
            for violation in rows.get(co, {}).get('native_drv_rows', []):
                require(finite(violation['slack']) < 0, 'NATIVE_DRV_SELECTION')
                union.setdefault(name(violation['pin']), []).append(dict(engine='ruststa', **violation))
            for row in observed.get('annotation', {}).get('rows', []):
                annotation.setdefault(row['pin'], row)
        pins = set(union) | set(annotation)
        for row in annotation.values():
            pins.update(row['unannotated_loads'])
        require(len(pins) <= self.b['max_checks'] and sum(len(p.encode()) for p in pins) <= 8*MIB, 'BINDING_WORK_BUDGET')
        ordered = sorted(pins)
        require(sum(len(v) for v in union.values()) <= 2*self.b['max_checks'], 'BINDING_VIOLATION_BUDGET')
        require(len(ordered)*2500+sum(len(p.encode()) for p in ordered)
                <= self.b['max_output_bytes'], 'BINDING_SCRIPT_ESTIMATE_BUDGET')
        # Only public Tcl object/property queries. Load the exact same logical
        # netlist; OpenDB escaping/physical aliases are deliberately not guessed.
        lines = ['if {![info exists ::sta_crpr_enabled]} {error "CPPR control missing"}',
                 'set ::sta_crpr_enabled 0']
        lines += ['read_liberty '+word(self.config['files'][k+'_tt']['path']) for k in ('std', 'ram')]
        lines += ['read_verilog '+word(run.root/'physical-only.v'),
                  'read_verilog '+word(self.config['files']['netlist']['path']),
                  'link_design '+word(self.config['top']), 'puts "RUSTEDA_CPPR_OFF $::sta_crpr_enabled"']
        for index, pin in enumerate(ordered):
            get = 'get_pins' if '/' in pin else 'get_ports'
            lines += ['set p ['+get+' -quiet '+word(pin)+']',
                      'if {[llength $p] != 1} {error "binding pin identity"}',
                      ('set nets [get_nets -quiet -of_objects $p]' if '/' in pin else 'set nets [get_nets -quiet [get_full_name $p]]'),
                      'if {$nets eq "NULL"} {set nets {}}',
                      'if {[llength $nets] > 1} {error "binding multiple nets"}',
                      'set net {}; set np {}; set ports {}; set inst {}; set master {}',
                      'if {[llength $nets]} {set net [get_full_name $nets]; set np [get_pins -quiet -of_objects $nets]; set ports [get_ports -quiet -of_objects $nets]}',
                      'if {[string first / [get_full_name $p]] >= 0} {set i [get_cells -quiet -of_objects $p]; if {[llength $i] != 1} {error "binding instance identity"}; set inst [get_full_name $i]; set master [get_property $i ref_name]}',
                      'puts [join [list RUSTEDA_BIND '+str(index)+' [get_full_name $p] $inst $master $net [get_property $p direction] [llength $np] [llength $ports]] "\\t"]',
                      'set ndrivers 0',
                      'foreach q [concat $np $ports] {',
                      ' set qn [get_full_name $q]; set d [get_property $q direction]',
                      ' if {([string first / $qn] >= 0 && $d eq "output") || ([string first / $qn] < 0 && $d eq "input")} {',
                      '  incr ndrivers; if {$ndrivers > 16} {error "binding driver budget"}',
                      '  set qm {}; if {[string first / $qn] >= 0} {set qi [get_cells -quiet -of_objects $q]; set qm [get_property $qi ref_name]}',
                      '  puts [join [list RUSTEDA_DRIVER '+str(index)+' $qn $qm] "\\t"]',
                      ' }', '}']
        lines += ['exit']
        path = run.root/'reports'/'driver-bindings.tcl'
        require(sum(len(s.encode()) for s in lines) < self.b['max_output_bytes'], 'PROBE_SCRIPT_BUDGET')
        path.write_text('\n'.join(lines)+'\n')
        log = run.command('driver-bindings-opensta', [run.tools['opensta'], '-no_init', '-threads', self.b['threads'], '-exit', path])
        require(not re.search(r'^Error[: ]|^Warning[: ]', log, re.M), 'BINDING_TOOL_DIAGNOSTIC')
        require('RUSTEDA_CPPR_OFF 0' in log and 'OpenSTA 3.1.' in log, 'BINDING_TOOL_SCOPE')
        found = {}
        drivers = {}
        for line in log.splitlines():
            if line.startswith('RUSTEDA_BIND\t'):
                fields = line.split('\t')
                require(len(fields) == 9, 'BINDING_FORMAT')
                index = int(fields[1])
                require(0 <= index < len(ordered) and fields[2] == ordered[index] and index not in found, 'BINDING_IDENTITY')
                found[index] = dict(pin=fields[2], instance=fields[3], master=fields[4], net=fields[5],
                                    direction=fields[6], net_pins=int(fields[7]), net_ports=int(fields[8]))
            elif line.startswith('RUSTEDA_DRIVER\t'):
                fields = line.split('\t')
                require(len(fields) == 4 and 0 <= int(fields[1]) < len(ordered), 'DRIVER_BINDING_FORMAT')
                drivers.setdefault(int(fields[1]), []).append(dict(pin=fields[2], master=fields[3]))
        require(len(found) == len(ordered), 'BINDING_COVERAGE')
        mapped = {ordered[i]: dict(**r, drivers=drivers.get(i, [])) for i, r in found.items()}
        for row in annotation.values():
            binding = mapped[row['pin']]
            row['binding'] = binding
            row['connected_loads'] = max(0, binding['net_pins']+binding['net_ports']-1)
            for load in row['unannotated_loads']:
                require(binding['net'] and binding['net'] == mapped[load]['net'], 'PARTIAL_ANNOTATION_NET_IDENTITY')
        eco = dict(schema='rusteda.reference-drv-bindings.v1', netlist=self.config['files']['netlist'],
                   sdc=self.config['files']['sdc'], spef=self.config['files']['spef_openrcx'], cppr='off',
                   bindings={co: [] for co in CORNERS}, native_extra=[],
                   planning=dict(mode='headroom', evidence={}))
        for co, observed in observations.items():
            for kind in ('slew', 'cap', 'fanout'):
                key = 'openrcx-'+co+'-'+kind+'.rpt'
                if key in observed.get('reports', {}):
                    eco['planning']['evidence'][co+'_'+kind] = observed['reports'][key]
        eco['planning']['evidence']['binding_probe'] = record(run.root/'logs'/'driver-bindings-opensta.stdout')
        kinds = dict(slew='max_slew', cap='max_capacitance', fanout='max_fanout',
                     max_transition='max_slew', max_capacitance='max_capacitance', max_fanout='max_fanout')
        for pin in sorted(union):
            bound = mapped[pin]
            require(bound['net'] and len(bound['drivers']) == 1, 'ECO_SINGLE_DRIVER_REQUIRED: '+pin)
            driver = bound['drivers'][0]
            require('/' in driver['pin'] and driver['master'], 'ECO_INSTANCE_DRIVER_REQUIRED: '+pin)
            for v in union[pin]:
                require(v['kind'] in kinds, 'ECO_DRV_KIND')
                row = dict(kind=kinds[v['kind']], pin=driver['pin'], master=driver['master'], net=bound['net'],
                           report_values=dict(limit=v['limit'], value=v['value'], slack=v['slack'],
                                              reported_pin=pin, report=v.get('report'), raw=v.get('raw')),
                           engine=v['engine'])
                if v['engine'] == 'opensta':
                    eco['bindings'][v['corner']].append(row)
                else:
                    eco['native_extra'].append(dict(corner=v['corner'], **row))
        partial = [r for r in annotation.values() if r['partial']]
        if partial:
            missing = sorted({load for r in partial for load in r['unannotated_loads']})
            require(missing and len(missing) <= 1024, 'MISSING_CONNECTION_REPAIR_BUDGET')
            evidence = dict(schema='rusteda.reference-annotation-evidence.v1', status='incomplete',
                            scope='original OpenSTA partially-unannotated-driver report; exact missing load pins verified on same canonical NLnet by public API',
                            netlist=self.config['files']['netlist'], spef=self.config['files']['spef_openrcx'],
                            partial_drivers=partial, missing_connections=[mapped[p] for p in missing],
                            reports={co: observations[co].get('reports', {}).get('openrcx-'+co+'-annotation.rpt') for co in CORNERS},
                            binding_probe=record(run.root/'logs'/'driver-bindings-opensta.stdout'))
            write_json(self.root/'annotation-evidence.json', evidence, self.b['max_output_bytes'])
            eco['annotation_scope'] = dict(status='incomplete', missing_connections=missing,
                                           evidence=record(self.root/'annotation-evidence.json'))
        require(len(eco['native_extra']) <= 1024, 'ECO_NATIVE_EXTRA_BUDGET')
        write_json(self.root/'eco-bindings.json', eco, self.b['max_output_bytes'])
        self.result['eco_bindings'] = record(self.root/'eco-bindings.json')
        output = dict(schema='rusteda.reference-drv-bindings.v1', status='observed',
                      scope='read-only public OpenSTA pin/master/canonical NLnet binding; repair-only until timing gate passes',
                      inputs=self.config['files'], tools=self.config['tools'],
                      rows=[dict(**mapped[pin], violations=union[pin]) for pin in sorted(union)],
                      partial_annotation=[r for r in annotation.values() if r['partial']],
                      reports=dict(script=record(path), stdout=record(run.root/'logs'/'driver-bindings-opensta.stdout')))
        write_json(self.root/'drv-bindings.json', output, self.b['max_output_bytes'])
        for co, observed in observations.items():
            if 'annotation' in observed:
                for row in observed['annotation']['rows']:
                    row.update(binding=mapped[row['pin']], connected_loads=annotation[row['pin']]['connected_loads'])
                observed['annotation']['active_unannotated_drivers'] = sum(r['connected_loads'] > 0 for r in observed['annotation']['rows'])
        self.result['resources'].append(dict(branch='observation', **run.resources[-1]))
        return record(self.root/'drv-bindings.json')

    def validate_row(self, co, row, observed):
        require(row['spef_sha256'] == self.config['files']['spef_openrcx']['sha256'], 'ROW_SPEF_BINDING')
        require(all(row['ruststa_scope'][k] == v for k, v in SCOPE.items() if k != 'rc_corner'), 'ROW_NATIVE_SCOPE')
        complete = row['native_completeness']
        witnesses = row['constant_disabled_endpoints']
        require(len(witnesses) == count(complete['unconstrained_endpoints']) == row['unconstrained_endpoints'], 'CONSTANT_WITNESS_COVERAGE')
        require(len({w['endpoint'] for w in witnesses}) == len(witnesses), 'DUPLICATE_CONSTANT_WITNESS')
        require(all(w['verified'] is True and w['native_reason'] == 'constant_pin'
                    and type(w['opensta_value']) is int and w['opensta_value'] in (0, 1) for w in witnesses), 'CONSTANT_WITNESS_UNVERIFIED')
        require(observed['helper_validation_error'] is None, 'OBSERVATION_VALIDATION')
        require(observed['constant_disabled_endpoints'] == witnesses, 'CONSTANT_WITNESS_REPEAT')
        for kind in KINDS:
            require(row['opensta'][kind+'_violations'] == observed[kind+'_violations'], 'REFERENCE_COUNT_REPEAT')
        for key in ('setup_ws_ns', 'hold_ws_ns'):
            require(finite(row['opensta'][key]) == finite(observed[key]), 'REFERENCE_SLACK_REPEAT')
            require(row[key] == min(row['ruststa'][key], row['opensta'][key]), 'CONSERVATIVE_SLACK')
        for kind in KINDS:
            key = kind+'_violations'
            require(count(row[key]) == max(count(row['ruststa'][key]), count(row['opensta'][key])), 'CONSERVATIVE_COUNT')
        require(row['checks_truncated'] is False and count(complete['unevaluated_limit_checks']) == 0, 'UNEVALUATED_CHECKS')
        require(count(row['active_unconstrained_endpoints']) == 0 and count(row['unconstrained_inputs']) == 0
                and count(row['unmodeled_arcs']) == 0 and not complete['unanalyzed_arcs'], 'NATIVE_COMPLETENESS')
        annotation = observed['annotation']
        require(annotation['active_unannotated_drivers'] == annotation['partially_unannotated_drivers'] == 0, 'ACTIVE_ANNOTATION_INCOMPLETE')
        require(not observed['check_setup_findings'] and not row['opensta']['check_setup_findings'], 'CONSTRAINT_SCOPE_FINDINGS')
        passed = all(row[k] >= 0 for k in ('setup_ws_ns', 'hold_ws_ns')) and all(row[k+'_violations'] == 0 for k in KINDS)
        require(row['status'] == ('passed' if passed else 'failed'), 'ROW_STATUS')
        return passed

    def execute(self):
        diagnostic = self.diagnostic()
        diagnostic_result = read_json(diagnostic.root/'result.json')
        rows, parity = {}, None
        path = diagnostic.root/'timing.json'
        if path.is_file():
            original = read_json(path)
            require(original['schema'] == 'rusteda.signoff-timing.v1' and set(original['sources']) == {'openrcx'}, 'DIAGNOSTIC_SCOPE')
            rows = original['sources']['openrcx']
            self.result['diagnostic_timing'] = record(path)
        path = diagnostic.root/'parity.json'
        if path.is_file():
            parity = read_json(path)
            self.result['diagnostic_parity'] = record(path)
        observe_run, observations = self.observe(diagnostic)
        failures = []
        if diagnostic_result.get('error'):
            failures.append('native_or_diagnostic_rejected: '+diagnostic_result['error'])
        try:
            bindings = self.bindings(observe_run, observations, rows)
        except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            bindings = None
            failures.append('bindings: '+str(error))
        coverage = set(rows) == set(CORNERS)
        for co in CORNERS:
            if co not in rows:
                failures.append(co+': native_completeness_unverified')
                continue
            try:
                if not self.validate_row(co, rows[co], observations[co]):
                    failures.append(co+': absolute_timing_or_drv_failed')
            except (ValueError, KeyError, TypeError) as error:
                coverage = False
                failures.append(co+': '+str(error))
        unchanged = all(Path(p).is_file() and digest(Path(p)) == sha for p, sha in self.initial.items())
        if not unchanged:
            failures.append('inputs_changed')
        compare = dict(status='unverified', comparison='ruststa_vs_opensta_on_identical_spef',
                       cppr='off', corners=list(CORNERS), spef_sources=['openrcx'], compared_values=0,
                       differences=[], tolerance_ns=self.config['scope']['tolerance_ns'],
                       within_tolerance=None, max_abs_difference_ns=None)
        if parity and parity['compared_values'] == 4:
            require(parity['comparison'] == compare['comparison'] and parity['cppr'] == 'off'
                    and parity['corners'] == list(CORNERS) and parity['spef_sources'] == ['openrcx'], 'NATIVE_COMPARE_SCOPE')
            require(parity['netlist_sha256'] == self.config['files']['netlist']['sha256']
                    and parity['spef_sha256'] == {'openrcx': self.config['files']['spef_openrcx']['sha256']}, 'NATIVE_COMPARE_BINDING')
            compare.update(parity)
            compare.update(status='diagnostic', within_tolerance=parity['max_abs_difference_ns'] <= compare['tolerance_ns'])
        elif coverage:
            failures.append('native_compare_incomplete')
        scope = dict(**SCOPE, mode='single_reference_openrcx', corners=list(CORNERS), spef_sources=['openrcx'],
                     netlist_sha256=self.config['files']['netlist']['sha256'], sdc_sha256=self.config['files']['sdc']['sha256'],
                     spef_sha256=self.config['files']['spef_openrcx']['sha256'])
        unverified = ['complete_rtl2gds_flow', 'native_two_source_extracted_parity',
                      'original_rtl_full_four_state_formal_equivalence', 'physical_drc_lvs_antenna']
        if not coverage:
            unverified += ['native_completeness_or_constant_witnesses']
        timing = dict(schema='rusteda.reference-timing.v1', status='passed' if not failures and coverage else 'failed',
                      coverage_complete=coverage, inputs_unchanged=unchanged, scope=scope,
                      metric_policy='original minimum slack / maximum violation count over native and OpenSTA; native numerical differences diagnostic only',
                      input_manifest=record(self.config_path), inputs=self.config['files'], tools=self.config['tools'],
                      policy=record(Path(__file__).resolve()), base_policy=self.config['policy'],
                      corners=rows, observations=observations, native_compare=compare, drv_bindings=bindings,
                      eco_bindings=self.result.get('eco_bindings'),
                      native_analysis=self.result['native_analysis'],
                      diagnostic_result=self.result['diagnostic_result'],
                      diagnostic_timing=self.result.get('diagnostic_timing'), diagnostic_parity=self.result.get('diagnostic_parity'),
                      failed_items=failures, unverified_items=unverified)
        write_json(self.root/'reference-timing.json', timing, self.b['max_output_bytes'])
        self.result.update(status=timing['status'], timing=record(self.root/'reference-timing.json'),
                           drv_bindings=bindings, inputs_unchanged=unchanged, coverage_complete=coverage,
                           failed_items=failures, unverified_items=unverified, wall_seconds=time.monotonic()-self.started)
        self.checkpoint()
        (self.root/'HEAD').write_text('rev1\n')
        return timing['status'] == 'passed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    run = None
    def timeout(_signal, _frame):
        raise WallBudgetExceeded('REFERENCE_TIMING_TOTAL_WALL_BUDGET')
    try:
        resource.setrlimit(resource.RLIMIT_AS, (4*GIB, 4*GIB))
        run = Run(args)
        signal.signal(signal.SIGALRM, timeout)
        signal.alarm(4*run.b['timeout_seconds'])
        passed = run.execute()
        signal.alarm(0)
        return 0 if passed else 1
    except (ValueError, OSError, KeyError, TypeError, AttributeError, MemoryError, WallBudgetExceeded, subprocess.TimeoutExpired) as error:
        signal.alarm(0)
        if run:
            run.result.update(status='failed', error=str(error), wall_seconds=time.monotonic()-run.started)
            run.checkpoint()
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

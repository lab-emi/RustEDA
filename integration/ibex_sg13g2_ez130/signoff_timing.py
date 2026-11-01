#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Bounded, same-input RustSTA/OpenSTA extracted timing qualification.

This is flow policy and report interpretation, not a timing implementation.
Each extraction source gets a fresh native workspace; each corner gets a fresh
external OpenSTA process. Original Liberty, SDC, Verilog and SPEF are read-only.
The only generated models describe disconnected, PG-only physical LEF masters
absent from Liberty. Functional cells, including SRAMs, never get substitutes.

Input schema: rusteda.signoff-timing-inputs.v1. All files/tools are pinned records
{path, sha256, bytes}. Required files: netlist,sdc,std_tt,ram_tt,std_ss,ram_ss,
physical_lef,spef_native,spef_openrcx. Diagnostic mode may omit a SPEF and cannot
pass qualification. Outputs: timing.json, parity.json, result.json and raw logs.
Run in a <=4 GiB OS scope. Children also have explicit address-space, output and
wall limits. No old reports or precomputed timing numbers are accepted.
RC import bounds: <=128 MiB per input/SPEF, <=128 MiB of RC names,
<=2M nodes and <=4M elements per SPEF (run09b native SPEF:
1.18M C + 0.82M R = 2.005M elements, above the former 2M bound). At ~32 B per
element plus interned names the import state stays well below 1 GiB; analysis
state is still capped by max_state_gib. The child file-size cap
(max_output_bytes) also bounds the RustSTA parasitics side table, two corners
of one SPEF (64 MiB was hit at 2M elements), so its maximum is 256 MiB.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
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


MIB = 1 << 20
GIB = 1 << 30
CORNERS = ('tt', 'ss')
SOURCES = ('native', 'openrcx')
FILES = {'netlist', 'sdc', 'std_tt', 'ram_tt', 'std_ss', 'ram_ss', 'physical_lef'}
TOOLS = {'rusteda', 'ruststa', 'opensta', 'gnu_time'}
SCOPE = dict(rc_corner='nom', cppr='off', precision='f64', backend='cpu',
             clock_model='propagated', wire_model='spef_rc')
BUDGETS = dict(threads=2, timeout_seconds=180, max_input_bytes=128*MIB,
               max_output_bytes=256*MIB, max_state_gib=2,
               max_limit_rows=100000, max_checks=100000)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def record(path):
    return dict(path=str(path.resolve()), sha256=digest(path), bytes=path.stat().st_size)


def read_json(path, cap):
    require(path.is_file() and path.stat().st_size <= cap, 'JSON_BYTE_BUDGET: '+str(path))
    return json.loads(path.read_text())


def write_json(path, value):
    data = json.dumps(value, indent=2, allow_nan=False)+'\n'
    require(len(data.encode()) <= 64*MIB, 'REPORT_BYTE_BUDGET')
    path.write_text(data)


def finite(value):
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value), 'NONFINITE_METRIC')
    return float(value)


def tcl_word(value):
    text = str(value)
    require(not any(c in text for c in '{}\r\n') and not text.endswith('\\'),
            'UNSUPPORTED_TCL_NAME: '+text)
    return '{'+text+'}'


def annotation_drivers(text, maximum):
    """Read OpenSTA's driver rows separately from indented unannotated sinks."""
    headers = ('unannotated', 'partially unannotated')
    counts, rows, sinks, section = {}, [], [], None
    for line in text.splitlines():
        if not line.strip():
            continue
        header = re.fullmatch(r'Found (\d+) (unannotated|partially unannotated) drivers\.', line)
        if header:
            count, section = int(header[1]), header[2]
            require(section not in counts and count <= maximum, 'ANNOTATION_HEADER_COUNT')
            counts[section] = count
            continue
        driver = re.fullmatch(r' (\S+)', line)
        sink = re.fullmatch(r'  (\S+)', line)
        require(section is not None and (driver is not None or sink is not None), 'ANNOTATION_ROW_FORMAT')
        if driver:
            require(len(rows) < maximum and len(driver[1].encode()) <= 4096, 'ANNOTATION_ROW_BUDGET')
            rows.append(dict(pin=driver[1], kind=section))
        else:
            require(section == 'partially unannotated' and rows
                    and rows[-1]['kind'] == section, 'ANNOTATION_ORPHAN_SINK')
            require(len(sinks) < maximum and len(sink[1].encode()) <= 4096, 'ANNOTATION_SINK_BUDGET')
            sinks.append(dict(driver=rows[-1]['pin'], pin=sink[1]))
    require(set(counts) == set(headers), 'ANNOTATION_HEADERS')
    require(all(sum(row['kind'] == kind for row in rows) == counts[kind] for kind in headers)
            and len({row['pin'] for row in rows}) == len(rows), 'ANNOTATION_DRIVER_COVERAGE')
    sink_drivers = {sink['driver'] for sink in sinks}
    require(all(row['pin'] in sink_drivers for row in rows
                if row['kind'] == 'partially unannotated'), 'ANNOTATION_PARTIAL_SINK_COVERAGE')
    return counts, rows, sinks


class Run:
    def __init__(self, args):
        self.root = args.workspace.resolve()
        self.config_path = args.inputs.resolve(strict=True)
        self.config = read_json(self.config_path, 2*MIB)
        c = self.config
        require(set(c) == {'schema', 'top', 'files', 'tools', 'budgets', 'scope'}, 'INPUT_KEYS')
        require(c['schema'] == 'rusteda.signoff-timing-inputs.v1', 'INPUT_SCHEMA')
        require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]{0,255}', c['top']), 'TOP_IDENTIFIER')
        require(set(c['tools']) == TOOLS, 'TOOL_KEYS')
        require(FILES <= set(c['files']) <= FILES | {'spef_'+s for s in SOURCES}, 'FILE_KEYS')
        require(set(c['budgets']) == set(BUDGETS), 'BUDGET_KEYS')
        self.b = c['budgets']
        for key, maximum in BUDGETS.items():
            require(type(self.b[key]) is int and 1 <= self.b[key] <= maximum, 'BUDGET_RANGE: '+key)
        require(set(c['scope']) == set(SCOPE) | {'mode', 'tolerance_ns'}, 'SCOPE_KEYS')
        require(all(c['scope'][k] == v for k, v in SCOPE.items()), 'SCOPE_UNSUPPORTED')
        require(c['scope']['mode'] in ('signoff', 'diagnostic'), 'SCOPE_MODE')
        self.tolerance = finite(c['scope']['tolerance_ns'])
        require(0 < self.tolerance <= .001, 'TOLERANCE_RANGE')
        self.sources = [s for s in SOURCES if 'spef_'+s in c['files']]
        require(self.sources and (self.sources == list(SOURCES) or c['scope']['mode'] == 'diagnostic'),
                'MISSING_SIGNOFF_SPEF')
        if self.sources == list(SOURCES):
            require(c['files']['spef_native']['path'] != c['files']['spef_openrcx']['path'],
                    'SPEF_SOURCES_REQUIRE_DISTINCT_FILES')
        self.initial = {str(self.config_path): digest(self.config_path),
                        str(Path(__file__).resolve()): digest(Path(__file__).resolve())}
        self.files, self.tools = {}, {}
        for name, table, cap in [('files', self.files, self.b['max_input_bytes']),
                                 ('tools', self.tools, 256*MIB)]:
            for key, item in c[name].items():
                require(set(item) == {'path', 'sha256', 'bytes'}, 'RECORD_KEYS: '+key)
                p = Path(item['path']).resolve(strict=True)
                require(str(p) == item['path'] and p.is_file() and p.stat().st_size <= cap,
                        'FILE_BUDGET_OR_PATH: '+key)
                require(record(p) == item, 'STALE_INPUT: '+key)
                self.initial[str(p)] = item['sha256']
                table[key] = p
                if name == 'tools':
                    with p.open('rb') as stream:
                        require(stream.read(4) == b'\x7fELF', 'DIRECT_ELF_REQUIRED: '+key)
        require(sum(v['bytes'] for v in c['files'].values()) <= 256*MIB, 'AGGREGATE_INPUT_BUDGET')
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root/'workspace.lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock' for p in self.root.iterdir()),
                'REVISION_CONFLICT')
        (self.root/'logs').mkdir()
        (self.root/'reports').mkdir()
        self.resources = []
        self.started = time.monotonic()
        self.result = dict(schema='rusteda.signoff-timing-result.v1', status='running',
                           input_manifest=record(self.config_path), policy=record(Path(__file__).resolve()),
                           inputs=c['files'], tools=c['tools'],
                           scope=c['scope'], resources=self.resources,
                           estimate=dict(peak_bytes=2*GIB, objects=500000, state_bytes_per_object=200,
                                         corners=2, threads=self.b['threads'],
                                         note='sequential sources; bounded native 32 paths, no Python graph traversal'))
        self.checkpoint()

    def checkpoint(self):
        write_json(self.root/'result.json', self.result)

    def command(self, label, argv, native=False):
        require(re.fullmatch('[A-Za-z0-9_-]+', label), 'COMMAND_HANDLE')
        stdout = self.root/'logs'/(label+'.stdout')
        stderr = self.root/'logs'/(label+'.stderr')
        timing = self.root/'logs'/(label+'.time')
        cmd = [str(self.tools['gnu_time']), '-v', '-o', str(timing), *map(str, argv)]
        write_json(self.root/'logs'/(label+'-command.json'), cmd)
        def limits():
            resource.setrlimit(resource.RLIMIT_AS, (4*GIB, 4*GIB))
            resource.setrlimit(resource.RLIMIT_FSIZE, (self.b['max_output_bytes'], self.b['max_output_bytes']))
        started = time.monotonic()
        with stdout.open('wb') as out, stderr.open('wb') as err:
            proc = subprocess.Popen(cmd, stdout=out, stderr=err, start_new_session=True,
                                    preexec_fn=limits, env={**os.environ,
                                    'RAYON_NUM_THREADS': str(self.b['threads']),
                                    'OMP_NUM_THREADS': str(self.b['threads'])})
            try:
                code = proc.wait(timeout=self.b['timeout_seconds'])
            except BaseException:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise
        measured = timing.read_text() if timing.is_file() else ''
        rss = re.search(r'Maximum resident set size \(kbytes\):\s*(\d+)', measured)
        self.resources.append(dict(command=label, wall_seconds=time.monotonic()-started,
                                   peak_rss_kib=int(rss[1]) if rss else None, exit_code=code))
        self.checkpoint()
        require(code == 0, 'TOOL_FAILED: '+label+' exit '+str(code))
        require(all(p.stat().st_size < self.b['max_output_bytes'] for p in (stdout, stderr)),
                'COMMAND_OUTPUT_BUDGET: '+label)
        if native:
            doc = read_json(stdout, self.b['max_output_bytes'])
            require(doc.get('schema') == 'rusteda.v1' and doc.get('ok') is True,
                    'NATIVE_REJECTED: '+label)
            return doc
        return stdout.read_text()

    def physical_models(self):
        lef = self.files['physical_lef'].read_text()
        logical = self.files['netlist'].read_text()
        cells = {}
        for co in CORNERS:
            names = set()
            for kind in ('std', 'ram'):
                text = self.files[kind+'_'+co].read_text()
                names.update(re.findall(r'\bcell\s*\(\s*"?([A-Za-z_][A-Za-z0-9_$]*)"?\s*\)', text))
            cells[co] = names
        names = []
        for match in re.finditer(r'(?ms)^MACRO\s+(\S+)\s*$(.*?)^END\s+\1\s*$', lef):
            name, body = match[1], match[2]
            pins = list(re.finditer(r'(?ms)^\s*PIN\s+(\S+)\s*$(.*?)^\s*END\s+\1\s*$', body))
            if not pins or any(not re.search(r'\bUSE\s+(POWER|GROUND)\s*;', p[2]) for p in pins):
                continue
            require((name in cells['tt']) == (name in cells['ss']), 'PHYSICAL_CELL_CORNER_MISMATCH')
            if name in cells['tt']:
                continue
            require(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name), 'PHYSICAL_CELL_IDENTIFIER')
            for inst in re.finditer(r'(?m)^[ \t]*'+re.escape(name)+r'[ \t]+\\?[^\s(]+[ \t]*\(([^;]*)\);', logical):
                require(not inst[1].strip(), 'CONNECTED_PHYSICAL_ONLY_CELL: '+name)
            names.append(name)
            require(len(names) <= 256, 'PHYSICAL_CELL_BUDGET')
        (self.root/'physical-only.v').write_text(''.join('module '+n+'(); endmodule\n' for n in names))
        for co in CORNERS:
            (self.root/(co+'-physical.lib')).write_text(
                'library(physical_'+co+') { time_unit : "1ns"; capacitive_load_unit(1,pf);\n'
                +''.join(' cell('+n+') { area : 0; }\n' for n in names)+'}\n')
        self.result['physical_only_models'] = dict(masters=names,
            basis='original LEF every pin POWER/GROUND; absent from original TT/SS Liberty; all logical instances disconnected',
            functional_models_replaced=False)

    def native(self, source):
        ws = self.root/('ruststa-'+source)
        self.command(source+'-init', [self.tools['rusteda'], 'init', '-w', ws, '--format', 'json'], True)
        argv = [self.tools['rusteda'], 'import', '-w', ws, '--base-rev', 'rev0', '--threads',
                self.b['threads'], '--format', 'json', '--max-spef-mib', '128', '--max-rc-name-mib', '128',
                '--max-rc-nodes', '2000000', '--max-rc-elements', '4000000', '--max-rc-nets', '100000']
        for co in CORNERS:
            for p in [self.files['std_'+co], self.files['ram_'+co], self.root/(co+'-physical.lib')]:
                argv += ['--lib', p, '--corner', co]
            argv += ['--spef', self.files['spef_'+source], '--spef-corner', co]
        argv += ['--netlist', self.files['netlist'], '--sdc', self.files['sdc']]
        imported = self.command(source+'-import', argv, True)
        require(imported['metrics']['top'] == self.config['top'], 'NATIVE_TOP_MISMATCH')
        self.command(source+'-run', [self.tools['ruststa'], 'run', '-w', ws, '--base-rev', 'rev1',
                     '--threads', self.b['threads'], '--backend', 'cpu', '--precision', 'f64',
                     '--max-state-gib', self.b['max_state_gib'], '--max-paths', '32',
                     '--max-limit-rows', self.b['max_limit_rows'], '--require-wire-model', 'spef_rc',
                     '--format', 'json'], True)
        result = {}
        for co in CORNERS:
            reports = {}
            for section in ('summary', 'drv', 'unconstrained', 'notes'):
                args = [self.tools['ruststa'], 'report', '-w', ws, '--rev', 'rev2', '--corner', co,
                        '--threads', self.b['threads'], '--section', section, '--limit', self.b['max_checks'],
                        '--format', 'json']
                if section == 'drv':
                    args += ['--filter', 'violating']
                doc = self.command(source+'-'+co+'-'+section, args, True)
                require(all(doc['scope'][k] == v for k, v in SCOPE.items() if k != 'rc_corner'),
                        'NATIVE_SCOPE_MISMATCH')
                if section != 'summary':
                    rows = doc['sections'][section]
                    require(rows['shown'] == rows['matched'] == len(rows['rows']) <= self.b['max_checks'],
                            'NATIVE_REPORT_TRUNCATED')
                    require(not rows.get('truncated', 0) and not rows.get('unevaluated', 0),
                            'NATIVE_UNEVALUATED_OR_TRUNCATED')
                reports[section] = doc
            result[co] = reports
        return result

    def reference(self, source, corner, native):
        prefix = source+'-'+corner
        out = self.root/'reports'
        path = lambda kind: out/(prefix+'-'+kind)
        n = self.b['max_checks']+1
        script = ['if {![info exists ::sta_crpr_enabled]} {error "CPPR control missing"}',
                  'set ::sta_crpr_enabled 0',
                  'if {$::sta_crpr_enabled != 0} {error "CPPR disable failed"}']
        script += ['read_liberty '+tcl_word(self.files[k+'_'+corner]) for k in ('std', 'ram')]
        script += ['read_verilog '+tcl_word(self.root/'physical-only.v'),
                   'read_verilog '+tcl_word(self.files['netlist']), 'link_design '+tcl_word(self.config['top']),
                   'read_sdc '+tcl_word(self.files['sdc']), 'read_spef '+tcl_word(self.files['spef_'+source]),
                   'if {$::sta_crpr_enabled != 0} {error "SDC changed CPPR policy"}',
                   'puts "RUSTEDA_CPPR_OFF $::sta_crpr_enabled"', 'report_units',
                   'report_worst_slack -max -digits 12', 'report_worst_slack -min -digits 12',
                   'report_checks -path_delay min_max -group_path_count 4 -endpoint_path_count 1 '
                   '-fields {slew capacitance input_pin net} -format full_clock_expanded -digits 12 > '
                   +tcl_word(path('paths.rpt'))]
        for kinds, label in [('max_delay -recovery -clock_gating_setup', 'setup'),
                             ('min_delay -removal -clock_gating_hold', 'hold'),
                             ('max_slew', 'slew'), ('max_capacitance', 'cap'), ('max_fanout', 'fanout')]:
            # OpenSTA's -violators selection can omit a small negative raw slack. Keep the
            # bounded complete table, and count its explicit strict VIOLATED annotations.
            script += ['report_check_types -'+kinds+' -no_line_splits -digits 12 -max_count '
                       +str(n)+' > '+tcl_word(path(label+'.rpt'))]
        script += ['report_parasitic_annotation -report_unannotated > '+tcl_word(path('annotation.rpt')),
                   'check_setup -verbose > '+tcl_word(path('scope.rpt'))]
        raw = native['unconstrained']['sections']['unconstrained']['rows']
        for index, row in enumerate(raw):
            name = row['pin']
            require(len(name.encode()) <= 4096, 'PIN_NAME_BUDGET')
            kind = 'get_pins' if '/' in name else 'get_ports'
            script += ['puts "RUSTEDA_CONSTANT_BEGIN '+str(index)+'"',
                       'set p ['+kind+' -quiet '+tcl_word(name)+']',
                       'if {[llength $p] != 1} {error "constant witness pin identity"}',
                       'report_constant $p', 'puts "RUSTEDA_CONSTANT_END '+str(index)+'"']
        script += ['set f [open '+tcl_word(path('annotation.rpt'))+' r]', 'set a [read $f]', 'close $f',
                   'foreach line [split $a "\\n"] {', r' if {[regexp {^  \S} $line]} {continue}', ' set p [string trim $line]',
                   ' if {$p eq "" || [string match "Found *" $p]} {continue}',
                   ' if {[string first / $p] >= 0} {set obj [get_pins -quiet [list $p]]} else {set obj [get_ports -quiet [list $p]]}',
                   ' if {[llength $obj] != 1} {error "unannotated driver identity"}',
                   # OpenSTA's -of_objects takes pins, not ports; a port's net has its name.
                   ' if {[string first / $p] >= 0} {set nets [get_nets -quiet -of_objects $obj]} else {set nets [get_nets -quiet [list $p]]}',
                   ' if {$nets eq "NULL"} {set nets {}}',
                   ' set pins {}; set ports {}',
                   ' if {[llength $nets] > 0} {',
                   '  set pins [get_pins -quiet -of_objects $nets]',
                   '  set ports [get_ports -quiet -of_objects $nets]', ' }',
                   ' puts "RUSTEDA_UNANNOTATED\\t$p\\t[llength $nets]\\t[llength $pins]\\t[llength $ports]"', '}', 'exit']
        tcl = path('tcl')
        tcl.write_text('\n'.join(script)+'\n')
        log = self.command(prefix+'-opensta', [self.tools['opensta'], '-no_init', '-threads',
                                             self.b['threads'], '-exit', tcl])
        require(not re.search(r'^Error[: ]', log, re.M), 'OPENSTA_ERROR: '+prefix)
        require('RUSTEDA_CPPR_OFF 0' in log, 'OPENSTA_CPPR_CONTROL')
        require('OpenSTA 3.1.' in log, 'OPENSTA_VERSION_REQUIRES_3_1')
        require(not re.search(r'^Warning[: ]', log, re.M), 'OPENSTA_WARNING: '+prefix)
        require(re.search(r'^ time 1ns$', log, re.M) is not None
                and re.search(r'^ capacitance 1pF$', log, re.M) is not None, 'OPENSTA_REPORT_UNITS')
        result = dict(setup_ws_ns=finite(float(re.search(r'worst slack max ([\d.eE+-]+)', log)[1])),
                      hold_ws_ns=finite(float(re.search(r'worst slack min ([\d.eE+-]+)', log)[1])))
        result['reported_check_rows'] = {}
        result['check_selection'] = 'bounded complete DRV table; timing violations cross-checked against global worst slack; no -violators selection'
        for kind in ('setup', 'hold', 'slew', 'cap', 'fanout'):
            p = path(kind+'.rpt')
            require(p.stat().st_size < self.b['max_output_bytes'], 'OPENSTA_REPORT_BYTE_BUDGET')
            text = p.read_text()
            count = text.count('(VIOLATED)')
            checked = count + text.count('(MET)')
            require(checked < self.b['max_checks'], 'OPENSTA_CHECK_TRUNCATION')
            result['reported_check_rows'][kind] = checked
            result[kind+'_violations'] = count
        for kind in ('setup', 'hold'):
            require((result[kind+'_ws_ns'] < 0) == (result[kind+'_violations'] > 0),
                    'OPENSTA_VIOLATION_COVERAGE: '+kind)
        witnesses = []
        for index, row in enumerate(raw):
            block = re.search(r'RUSTEDA_CONSTANT_BEGIN '+str(index)+r'\n(.*?)\nRUSTEDA_CONSTANT_END '+str(index), log, re.S)
            value = re.fullmatch(r'\S+[ \t]+([01])', block[1].strip()) if block else None
            witnesses.append(dict(endpoint=row['pin'], reason='constant_disabled',
                                  native_reason=row['reason'], verified=row['reason'] == 'constant_pin' and value is not None,
                                  opensta_value=int(value[1]) if value else None))
        result['constant_disabled_endpoints'] = witnesses
        annotation = path('annotation.rpt').read_text()
        counts, drivers, sinks = annotation_drivers(annotation, self.b['max_checks'])
        rows = [dict(pin=m[1], connected_nets=int(m[2]), net_pins=int(m[3]), net_ports=int(m[4]),
                     connected_loads=max(0, int(m[3])+int(m[4])-1)) for m in re.finditer(
            r'^RUSTEDA_UNANNOTATED\t([^\t\n]+)\t(\d+)\t(\d+)\t(\d+)$', log, re.M)]
        require([row['pin'] for row in rows] == [row['pin'] for row in drivers], 'ANNOTATION_ROW_COVERAGE')
        for row, driver in zip(rows, drivers):
            row['kind'] = driver['kind']
        result['annotation'] = dict(unannotated_drivers=counts['unannotated'],
                                    partially_unannotated_drivers=counts['partially unannotated'],
                                    active_unannotated_drivers=sum(r['connected_loads'] > 0 for r in rows),
                                    rows=rows, partially_unannotated_sinks=sinks)
        result['check_setup_findings'] = path('scope.rpt').read_text().strip()
        result['reports'] = {p.name: record(p) for p in out.iterdir() if p.name.startswith(prefix+'-')}
        return result

    def execute(self):
        self.physical_models()
        scope = {**SCOPE, 'netlist_sha256': self.config['files']['netlist']['sha256'],
                 'sdc_sha256': self.config['files']['sdc']['sha256'], 'mode': self.config['scope']['mode']}
        timing = dict(schema='rusteda.signoff-timing.v1', status='running', scope=scope,
                      metric_policy='minimum slack and maximum violation count over both engines; individual values retained', sources={})
        parity = dict(schema='rusteda.signoff-timing-parity.v1', status='running',
                      comparison='ruststa_vs_opensta_on_identical_spef', ruststa_vs_opensta_on_identical_spef=True,
                      cppr='off', corners=list(CORNERS), spef_sources=self.sources, compared_values=0,
                      tolerance_ns=self.tolerance, netlist_sha256=scope['netlist_sha256'],
                      spef_sha256={s:self.config['files']['spef_'+s]['sha256'] for s in self.sources}, differences=[])
        all_ok = True
        for source in self.sources:
            native = self.native(source)
            timing['sources'][source] = {}
            for co in CORNERS:
                reports = native[co]
                oracle = self.reference(source, co, reports)
                metrics, complete = reports['summary']['metrics'], reports['summary']['completeness']
                native_values = dict(setup_ws_ns=finite(metrics['worst_setup_slack_ns']),
                                     hold_ws_ns=finite(metrics['worst_hold_slack_ns']))
                for kind, key in [('setup','violating_endpoints'), ('hold','hold_violating_endpoints'),
                                  ('slew','max_transition_violating'), ('cap','max_capacitance_violating'),
                                  ('fanout','max_fanout_violating')]:
                    require(type(metrics[key]) is int and metrics[key] >= 0, 'NATIVE_CHECK_COUNT')
                    native_values[kind+'_violations'] = metrics[key]
                drv_rows = reports['drv']['sections']['drv']['rows']
                for kind, key in [('max_transition','slew'), ('max_capacitance','cap'), ('max_fanout','fanout')]:
                    require(sum(r['kind'] == kind and finite(r['slack']) < 0 for r in drv_rows)
                            == native_values[key+'_violations'], 'NATIVE_DRV_ROW_COVERAGE: '+kind)
                witness = oracle['constant_disabled_endpoints']
                require(complete['unconstrained_endpoints'] == len(witness), 'UNCONSTRAINED_COVERAGE')
                row = {k:(min(native_values[k], oracle[k]) if k.endswith('_ns') else max(native_values[k], oracle[k])) for k in native_values}
                row.update(spef_sha256=parity['spef_sha256'][source],
                    unconstrained_endpoints=complete['unconstrained_endpoints'],
                    active_unconstrained_endpoints=sum(not w['verified'] for w in witness),
                    constant_disabled_endpoints=witness, unconstrained_inputs=complete['unconstrained_inputs'],
                    unmodeled_arcs=sum(complete['unanalyzed_arcs'].values()), checks_truncated=False,
                    ruststa=native_values, opensta=oracle, native_completeness=complete,
                    ruststa_scope=reports['summary']['scope'],
                    native_drv_rows=drv_rows, native_notes=reports['notes']['sections']['notes']['rows'])
                require(complete['unevaluated_limit_checks'] == 0, 'NATIVE_UNEVALUATED_CHECKS')
                passed = all(row[k] >= 0 for k in ('setup_ws_ns','hold_ws_ns')) and all(row[k] == 0 for k in (
                    'setup_violations','hold_violations','slew_violations','cap_violations','fanout_violations',
                    'active_unconstrained_endpoints','unconstrained_inputs','unmodeled_arcs'))
                passed = passed and not oracle['check_setup_findings'] and all(
                    oracle['annotation'][key] == 0 for key in ('active_unannotated_drivers', 'partially_unannotated_drivers'))
                row['status'] = 'passed' if passed else 'failed'
                all_ok &= passed
                timing['sources'][source][co] = row
                for key in ('setup_ws_ns', 'hold_ws_ns'):
                    difference = abs(native_values[key]-oracle[key])
                    parity['differences'].append(dict(source=source, corner=co, field=key,
                        ruststa_ns=native_values[key], opensta_ns=oracle[key], abs_difference_ns=difference))
                    parity['compared_values'] += 1
                write_json(self.root/'timing.json', timing)
                write_json(self.root/'parity.json', parity)
        unchanged = all(Path(p).is_file() and digest(Path(p)) == h for p,h in self.initial.items())
        complete = self.sources == list(SOURCES) and self.config['scope']['mode'] == 'signoff'
        maximum = max(d['abs_difference_ns'] for d in parity['differences'])
        parity.update(max_abs_difference_ns=maximum, inputs_unchanged=unchanged,
                      status='passed' if complete and unchanged and maximum <= self.tolerance else 'failed')
        timing.update(status='passed' if complete and unchanged and all_ok else 'failed', inputs_unchanged=unchanged)
        write_json(self.root/'timing.json', timing)
        write_json(self.root/'parity.json', parity)
        self.result.update(status='passed' if timing['status'] == parity['status'] == 'passed' else 'failed',
            timing=record(self.root/'timing.json'), parity=record(self.root/'parity.json'),
            inputs_unchanged=unchanged, wall_seconds=time.monotonic()-self.started,
            unverified_items=[] if complete else ['complete_two_source_extracted_signoff'],
            failed_items=[name for name,doc in [('timing',timing),('parity',parity)] if doc['status'] != 'passed'])
        self.checkpoint()
        (self.root/'HEAD').write_text('rev1\n')
        return self.result['status'] == 'passed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    run = None
    try:
        resource.setrlimit(resource.RLIMIT_AS, (4*GIB, 4*GIB))
        run = Run(args)
        return 0 if run.execute() else 1
    except (ValueError, OSError, KeyError, TypeError, MemoryError, subprocess.TimeoutExpired) as error:
        if run:
            run.result.update(status='failed', error=str(error), wall_seconds=time.monotonic()-run.started)
            run.checkpoint()
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

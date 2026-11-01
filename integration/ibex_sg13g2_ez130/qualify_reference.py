#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Bind independent LibreLane physical/timing/vendor evidence to one fresh run.

CLI: --inputs FILE --workspace NEW --base-rev empty. Context schema is
rusteda.reference-qualification-inputs.v1, with mode fresh/diagnostic, run_root,
files/tools FileIDs, five run-relative artifacts, eight required report paths,
optional simulation_result/formal_equivalence, and run_binding path/null.
Fresh mode additionally requires the real owned worker and chained producer
receipts. Diagnostic mode cannot produce a benchmark-eligible qualification.

No EDA algorithm is implemented here. Memory estimate<384MiB: <=32MiB/report,
one <=64MiB Yosys JSON for the existing bounded macro contract, streamed XML
<=64MiB/500k findings, <=100k DEF components, <=256 input records. No paths,
cones or flattened device graphs are materialized. Controller512MiB/90s guard;
the caller supplies the whole-worker cgroup and end-to-end timing measurement.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import resource
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import macro_contract
import signoff_lvs
import strict_checks


SCHEMA = 'rusteda.reference-qualification-inputs.v1'
RTL_SHA256 = '8a8fe84b181e6e3b169721034850a1b8fabfd320d988eb89e016598e906911e4'
RC_RULE_KEY = 'ihp/libs.tech/librelane/openrcx/IHP_rcx_patterns.rules'
RC_RULE_SHA256 = 'ffda02d57a6ecb39b02268be9511d89ef072911f17bd2dcd095f3f3cbeb5a905'
ARTIFACTS = {'gds', 'def', 'netlist_final', 'netlist_powered_final', 'spef_openrcx_nom', 'netlist_synth'}
REPORTS = {'drc', 'antenna', 'lvs', 'timing', 'timing_result', 'simulation', 'macro_contract', 'openrcx_extraction', 'synthesis_equivalence', 'final_equivalence'}
FILES = {'contract', 'rtl', 'sdc', 'tb', 'std_tt', 'std_ss', 'ram_tt', 'ram_ss',
         'drc_deck', 'antenna_deck', 'drc_profile', 'sram_qualification',
         'rules_nom', 'tech_lef', 'std_lef', 'ram_lef'}
VIOLATIONS = ('setup_violations', 'hold_violations', 'slew_violations', 'cap_violations', 'fanout_violations')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def component_placement(statement):
    # Public Cadence LEF/DEF5.8 Components grammar. SOURCE records provenance;
    # it cannot justify dropping devices or changing placement/signoff checks.
    match = re.fullmatch(r'\s*-\s+(\S+)\s+(\S+)\s+'
                         r'(?:\+\s+(?i:SOURCE)\s+((?i:NETLIST|DIST|USER|TIMING))\s+)?'
                         r'\+\s+(FIXED|PLACED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)\s*;\s*', statement)
    require(match is not None, 'unsupported DEF component statement; optional single SOURCE and placed/fixed geometry required')
    name, master, source, status, x, y, orientation = match.groups()
    return name, master, source.upper() if source else None, status, int(x), int(y), orientation


def read(path, cap=32 << 20):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= cap, 'JSON byte budget: ' + str(path))
    with path.open('rb') as stream:
        raw = stream.read(cap + 1)
    require(len(raw) <= cap, 'JSON grew past byte budget')
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON: ' + value)))


def identity(path, cap=4 << 30):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= cap, 'file byte budget: ' + str(path))
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'file changed while hashing')
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def verify(record):
    require(isinstance(record, dict) and set(record) == {'path', 'sha256', 'bytes'}
            and isinstance(record['path'], str) and Path(record['path']).is_absolute(), 'complete absolute FileID required')
    require(identity(record['path']) == record, 'source identity changed: ' + record['path'])
    return record


def natural(value):
    return type(value) is int and value >= 0


def finite(value):
    require(type(value) in (int, float) and math.isfinite(value), 'finite numeric metric required')
    return value


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def antenna_categories(path):
    require(path.is_file() and path.stat().st_size <= 64 << 20, 'antenna XML byte budget')
    names, items = set(), 0
    for event, node in ET.iterparse(path, events=('end',)):
        if node.tag == 'category' and node.find('name') is not None:
            names.add(node.findtext('name'))
            require(len(names) <= 128, 'antenna category budget')
        if node.tag == 'item':
            items += 1
            require(items <= 500000, 'antenna finding budget')
            node.clear()
    expected = {'Ant.a', 'Ant.c', 'Ant.g', 'Ant.h', 'Ant.i'}
    expected.update('Ant.' + k + '_' + m for k in ('b', 'e')
                    for m in ('Metal1', 'Metal2', 'Metal3', 'Metal4', 'Metal5', 'TopMetal1', 'TopMetal2'))
    expected.update('Ant.' + k + '_' + v for k in ('d', 'f')
                    for v in ('Via1', 'Via2', 'Via3', 'Via4', 'TopVia1', 'TopVia2'))
    require(names == expected and len(names) == 31, 'complete original31 antenna categories not present')
    return items


def physical_report(name, report, artifacts, files, dynamic_record):
    """Shared by production and actual historical physical-subgate controls."""
    require(report.get('status') == 'passed' and report.get('complete') is True, 'independent physical gate failed/incomplete: ' + name)
    actual = report.get('gds_sha256', report.get('gds', {}).get('sha256'))
    require(actual == artifacts['gds']['sha256'], 'physical report belongs to another GDS: ' + name)
    if name in ('drc', 'antenna'):
        require(natural(report.get('violations')) and report['violations'] == 0, 'foundry violations: ' + name)
        require(report['scope'] == 'complete_foundry_' + ('main' if name == 'drc' else 'antenna'), 'foundry table scope differs')
        require(report['disabled_rules'] == (['recommended'] if name == 'drc' else []), 'foundry rules disabled')
        require(report['deck_sha256'] == files['drc_deck' if name == 'drc' else 'antenna_deck']['sha256'], 'foundry deck differs')
        require(report['profile'] == files['drc_profile'], 'foundry provenance differs')
        raw = dynamic_record(report['raw_report'])
        if name == 'antenna':
            require(antenna_categories(Path(raw['path'])) == 0, 'antenna XML contains findings')
    else:
        require(report['powered_netlist_sha256'] == artifacts['netlist_powered_final']['sha256'], 'LVS belongs to another powered netlist')
        require(report['top'] == 'ibex_sram_bench' and report['top_port_identity'] is True
                and report['macro_instances'] == report['reference_macro_instances'] == 2
                and report['macro_devices_verified'] is True and report['extraction_errors'] == []
                and report['macro_qualification_sha256'] == files['sram_qualification']['sha256'], 'full device/macro/interface LVS missing')
        require(report['input_hashes_unchanged'] is True, 'LVS source inputs changed')
        qualification = read(files['sram_qualification']['path'], 16 << 20)
        decks = [digest for name, digest in qualification['frozen'].items() if name.endswith('.lvs')]
        require(qualification['status'] == 'qualified' and qualification['top_pin_count'] == 193
                and decks == [report['deck_sha256']], 'LVS extraction deck is not the qualified macro profile')
        counts = report['macro_expanded_device_counts']
        require(counts['layout'] == counts['reference'] == qualification['expanded_device_counts_each_side'], 'LVS actual macro device classes/counts differ')
        for record in report['artifacts'].values():
            dynamic_record(record)
    return dict(status='passed', complete=True, gds_sha256=artifacts['gds']['sha256'], scope=report['scope'])


class Qualification:
    def __init__(self, context, output):
        required = {'schema', 'mode', 'run_root', 'files', 'tools', 'artifacts', 'reports', 'run_binding'}
        require(isinstance(context, dict) and set(context) == required and context['schema'] == SCHEMA, 'reference qualification context fields/schema')
        require(context['mode'] in ('fresh', 'diagnostic'), 'explicit fresh/diagnostic mode required')
        self.context, self.output = context, output
        self.root = Path(context['run_root']).resolve(strict=True)
        require(self.root.is_dir(), 'run_root is not a directory')
        self.files, self.tools = context['files'], context['tools']
        require(FILES <= set(self.files) and len(self.files) <= 256 and len(self.tools) <= 32, 'reference input set/budget')
        for group in (self.files, self.tools):
            for name, record in group.items():
                require(re.fullmatch('[A-Za-z0-9_-]{1,128}', name), 'unsafe input handle')
                verify(record)
        require(ARTIFACTS == set(context['artifacts']) and REPORTS <= set(context['reports'])
                and not set(context['reports']) - REPORTS - {'simulation_result', 'formal_equivalence'}, 'reference artifact/report set')
        self.artifacts = {key: identity(self.current(path)) for key, path in context['artifacts'].items()}
        self.report_records = {key: identity(self.current(path), 32 << 20) for key, path in context['reports'].items()}
        self.reports = {key: read(record['path']) for key, record in self.report_records.items()}
        self.contract = read(self.files['contract']['path'], 1 << 20)
        self.contract_sources = {}
        self.gates, self.failures = {}, []

    def current(self, relative):
        require(isinstance(relative, str) and 0 < len(relative) <= 4096 and '\x00' not in relative, 'run-relative artifact required')
        value = Path(relative)
        require(not value.is_absolute() and '..' not in value.parts, 'dynamic artifact must be run-relative')
        path = (self.root / value).resolve(strict=True)
        require(path.is_relative_to(self.root), 'dynamic artifact escapes this run')
        return path

    def dynamic_record(self, record):
        verify(record)
        require(Path(record['path']).is_relative_to(self.root), 'historical/external dynamic evidence forbidden')
        return record

    def tool(self, *aliases):
        values = [self.tools[key] for key in aliases if key in self.tools]
        require(values and all(value == values[0] for value in values), 'missing/conflicting tool role: ' + '/'.join(aliases))
        return values[0]

    def common_configuration(self):
        c = self.contract
        require(c['schema'] == 'rusteda.ibex-sg13g2-ez130.contract.v1' and c['design'] == 'ibex_sram_bench', 'original contract identity')
        require(self.files['contract'] == identity(Path(__file__).parent / 'contract.json'), 'common original contract differs')
        require(self.files['rtl']['sha256'] == RTL_SHA256 and self.files['sdc']['sha256'] == c['common']['sdc_sha256'], 'original RTL/SDC differs')
        hashes = {value['sha256'] for value in self.files.values()}
        if not set(c['files'].values()) <= hashes:
            require(self.context['mode'] == 'fresh' and self.context['run_binding'] is not None, 'original contract PDK source inventory incomplete')
            binding = read(self.current(self.context['run_binding']), 8 << 20)
            manifest_id = verify(binding['manifest'])
            source_roots = read(manifest_id['path'], 8 << 20)['sources']
            for name, wanted in c['files'].items():
                prefix, relative = name.split('/', 1)
                require(prefix in ('ez130', 'ihp') and '..' not in Path(relative).parts, 'unsafe contract PDK source')
                actual = identity(Path(source_roots[prefix]) / relative)
                require(actual['sha256'] == wanted, 'original contract PDK view differs: ' + name)
                self.contract_sources[name] = actual
        require(self.files['antenna_deck']['sha256'] == c['files']['ihp/libs.tech/klayout/tech/drc/rule_decks/antenna.drc'], 'original antenna deck differs')
        profile = read(self.files['drc_profile']['path'], 2 << 20)
        offgrid = profile['files']['tech/drc/rule_decks/geometry/3_1_offgrid.drc']
        require(profile['original_views_modified'] is False and offgrid['prepared_sha256'] == c['signoff_offgrid_file_sha256'], 'official offgrid correction provenance differs')
        for key, name in [('std_tt', 'ez130/lib/ez130_8t_tt_1p20v_25c.lib'), ('std_ss', 'ez130/lib/ez130_8t_ss_1p08v_125c.lib'),
                          ('ram_tt', 'ihp/libs.ref/sg13g2_sram/lib/RM_IHPSG13_1P_1024x32_c2_bm_bist_typ_1p20V_25C.lib'),
                          ('ram_ss', 'ihp/libs.ref/sg13g2_sram/lib/RM_IHPSG13_1P_1024x32_c2_bm_bist_slow_1p08V_125C.lib')]:
            require(self.files[key]['sha256'] == c['files'][name], 'original corner library differs: ' + key)
        require(c['common']['clock_period_ns'] == 100 and c['common']['cppr'] == 'off'
                and c['common']['corners'] == ['tt_1p20v_25c', 'ss_1p08v_125c']
                and c['common']['early_derate'] == 1.0 and c['common']['late_derate'] == 1.0, 'shared timing contract differs')
        require(c['common']['rc_calibration_rules'] == RC_RULE_KEY
                and c['common']['rc_calibration_sha256'] == c['files'][RC_RULE_KEY] == RC_RULE_SHA256
                and self.files['rules_nom']['sha256'] == RC_RULE_SHA256,
                'shared original default RC calibration differs')
        return dict(status='passed', contract=self.files['contract'], rtl=self.files['rtl'], sdc=self.files['sdc'],
                    rc_calibration=self.files['rules_nom'], original_pdk_inventory=self.contract_sources)

    def configuration(self, record, steps):
        self.dynamic_record(record)
        config = read(record['path'], 2 << 20)
        common = self.contract['common']
        require(config['DESIGN_NAME'] == 'ibex_sram_bench' and config['CLOCK_PORT'] == 'clk_i'
                and finite(config['CLOCK_PERIOD']) == common['clock_period_ns'], 'LibreLane clock/top configuration differs')
        require(config['DIE_AREA'] == common['die_um'] and config['CORE_AREA'] == common['core_um'], 'LibreLane die/core differs')
        require([config['RT_MIN_LAYER'], config['RT_MAX_LAYER']] == ['Metal2', 'TopMetal2']
                and [config['RT_CLOCK_MIN_LAYER'], config['RT_CLOCK_MAX_LAYER']] == ['Metal2', 'TopMetal2'], 'matched routing layers differ')
        require(config['FP_MACRO_HORIZONTAL_HALO'] == config['FP_MACRO_VERTICAL_HALO'] == common['macro_halo_um'], 'macro halo differs')
        physical = read(self.files['physical']['path'], 1 << 20)
        expected_blocks = [[v[k]/1000 for k in ('lx','ly','ux','uy')] for v in physical.get('placement_blockages', [])]
        require(config['FP_OBSTRUCTIONS'] == expected_blocks, 'hard placement blockages differ')
        name = 'RM_IHPSG13_1P_1024x32_c2_bm_bist'
        require(set(config['MACROS']) == {name}, 'macro library configuration differs')
        instances = config['MACROS'][name]['instances']
        require(set(instances) == set(common['macros']), 'macro instance set differs')
        for instance, wanted in common['macros'].items():
            require(instances[instance]['location'] == wanted['location_um']
                    and instances[instance]['orientation'] == wanted['orientation'], 'macro configuration placement differs')
        require(identity(config['SIGNOFF_SDC_FILE'])['sha256'] == self.files['sdc']['sha256'], 'configured signoff SDC differs')
        require(identity(config['PNR_SDC_FILE'])['sha256'] == identity(Path(__file__).parent / 'pnr.sdc')['sha256']
                and identity(config['STA_EXTRA_CORNER_TCL_FILE'])['sha256'] == identity(Path(__file__).parent / 'cppr_off.tcl')['sha256'], 'configured common PNR/CPPR wrapper differs')
        macro = config['MACROS'][name]
        for role, suffix in [('gds', 'gds/RM_IHPSG13_1P_1024x32_c2_bm_bist.gds'), ('lef', 'lef/RM_IHPSG13_1P_1024x32_c2_bm_bist.lef')]:
            require(isinstance(macro[role], list) and len(macro[role]) == 1
                    and identity(macro[role][0])['sha256'] == self.contract['files']['ihp/libs.ref/sg13g2_sram/' + suffix], 'configured SRAM physical model differs')
        require(any(record['path'] in step['arguments'] for step in steps), 'configuration not consumed by an actual recorded CLI step')

    def provenance(self):
        if self.context['mode'] == 'diagnostic':
            require(self.context['run_binding'] is None, 'diagnostic mode cannot claim fresh producer binding')
            return dict(status='diagnostic_only', benchmark_eligible=False)
        owner = signoff_lvs.inherited_owner()
        require(owner is not None, 'fresh qualification requires actual owned producer scope')
        ownership = read(owner['ownership_record'], 2 << 20)
        require(ownership.get('flow_implementation') == 'librelane', 'fresh worker is not the reference producer')
        binding_path = self.current(self.context['run_binding'])
        binding = read(binding_path, 8 << 20)
        require(binding['schema'] == 'rusteda.reference-run-binding.v1' and binding['implementation'] == 'librelane'
                and binding['runtime'] == 'native' and binding['request_id'] == owner['request_id']
                and Path(binding['run_root']).resolve() == self.root and binding['initial_state'] is None
                and binding['inputs_unchanged'] is True and binding['status'] == 'implementation_completed', 'fresh run provenance differs')
        require(binding['manifest'] == owner['producer_manifest'], 'reference manifest differs from owned request')
        manifest = read(binding['manifest']['path'], 8 << 20)
        start_record = self.dynamic_record(binding['start_binding'])
        start = read(start_record['path'], 4 << 20)
        require(start['schema'] == 'rusteda.reference-start-binding.v1'
                and start['request_id'] == owner['request_id'] and Path(start['run_root']).resolve() == self.root
                and start['manifest'] == binding['manifest'] and start['initial_state'] is None
                and start['rtl_expected_sha256'] == RTL_SHA256
                and start['files'] == self.files and start['tools'] == self.tools, 'fresh start binding differs')
        require(manifest['files'] == self.files and manifest['tools'] == self.tools, 'start inputs differ from actual producer manifest')
        require(verify(start['producer']) in [*self.files.values(), *self.tools.values()], 'producer source is not a pinned manifest input')
        steps = binding['steps']
        require(isinstance(steps, list) and 1 <= len(steps) <= 128, 'producer step budget')
        outputs, ids, observations = {}, set(), []
        for step in steps:
            require(re.fullmatch('[A-Za-z0-9_-]{1,128}', step['id']) and step['id'] not in ids, 'unsafe/duplicate producer step')
            ids.add(step['id'])
            tool = self.tools.get(step['tool']) if isinstance(step['tool'], str) else step['tool']
            require(tool in self.tools.values() and verify(tool) == tool, 'step tool is not pinned')
            require(type(step['exit_code']) is int and finite(step['wall_seconds']) >= 0, 'producer command exit/wall time')
            arguments = step['arguments']
            require(isinstance(arguments, list) and len(arguments) <= 512
                    and all(isinstance(s, str) and len(s) <= 65536 and '\x00' not in s for s in arguments), 'producer argv budget')
            for key in ('stdout', 'stderr'):
                self.dynamic_record(step[key])
            state = step.get('source_state')
            resumed = any(s in ('--from', '--with-initial-state', '--initial-state') or s.startswith('--with-initial-state=') for s in arguments)
            if state is not None:
                self.dynamic_record(state)
                require(outputs.get(state['path']) == state, 'checkpoint not produced by an earlier step of this run')
            else:
                require(not resumed, 'resumed command lacks current-run state chain')
            produced = step.get('outputs', {})
            require(isinstance(produced, dict) and len(produced) <= 128, 'step output budget')
            purpose = step.get('purpose', 'implementation')
            require(purpose in ('implementation', 'repair_observation'), 'unknown producer step purpose')
            if step['exit_code'] != 0:
                require(type(step['exit_code']) is int and step['exit_code'] == 1
                        and purpose == 'repair_observation', 'producer command failed')
                observations.append(self.repair_observation(step, produced))
            for record in produced.values():
                self.dynamic_record(record)
                require(record['path'] not in outputs or outputs[record['path']] == record, 'producer overwrote a bound artifact')
                outputs[record['path']] = record
            require(len(outputs) <= 4096, 'producer output inventory budget')
        require(binding['artifacts'] == self.artifacts, 'producer final artifact set differs')
        require(all(outputs.get(record['path']) == record for record in self.artifacts.values()), 'final artifact not produced by recorded steps')
        prepared = self.dynamic_record(binding['preparation'])
        require(outputs.get(prepared['path']) == prepared, 'preparation is not a producer output')
        preparation = read(prepared['path'], 2 << 20)
        require(preparation['status'] == 'prepared_not_qualified' and preparation['contract_sha256'] == self.files['contract']['sha256']
                and preparation['rtl_sha256'] == RTL_SHA256, 'fresh RTL preparation differs')
        fresh_rtl = Path(prepared['path']).parent / 'rtl/design.v'
        require(identity(fresh_rtl)['sha256'] == RTL_SHA256 and fresh_rtl.resolve().is_relative_to(self.root), 'fresh original RTL bytes absent')
        configs = binding.get('configurations')
        require(isinstance(configs, list) and 1 <= len(configs) <= 32, 'actual consumed reference configurations required')
        for record in configs:
            self.configuration(record, steps)
        return dict(status='passed', request_id=owner['request_id'], run_binding=identity(binding_path),
                    start_binding=start_record, preparation=prepared, steps=len(steps),
                    expected_failed_repair_observations=observations, benchmark_eligible=True)

    def repair_observation(self, step, produced):
        """Admit only an actual failed reference timing observation before repair."""
        require(step.get('purpose') == 'repair_observation' and type(step.get('exit_code')) is int
                and step['exit_code'] == 1, 'nonzero observation purpose/exit differs')
        require({'reference_timing', 'result'} <= set(produced), 'failed observation lacks paired timing/result outputs')
        timing_id = self.dynamic_record(produced['reference_timing'])
        result_id = self.dynamic_record(produced['result'])
        timing, result = read(timing_id['path']), read(result_id['path'])
        require(timing['schema'] == 'rusteda.reference-timing.v1'
                and result['schema'] == 'rusteda.reference-timing-result.v1'
                and timing['status'] == result['status'] == 'failed'
                and timing['inputs_unchanged'] is result['inputs_unchanged'] is True
                and type(timing['coverage_complete']) is bool and type(result['coverage_complete']) is bool
                and result['timing'] == timing_id, 'nonzero step is not a bound failed reference observation')
        for document in (timing, result):
            require(verify(document['policy']) in self.files.values()
                    and document['policy']['path'] in step['arguments'], 'failed observation policy/argv differs')
            source = self.dynamic_record(document['input_manifest'])
            require(source['path'] in step['arguments'], 'failed observation input manifest not consumed')
            require(document['scope']['mode'] == 'single_reference_openrcx'
                    and document['scope']['cppr'] == 'off'
                    and 'spef_openrcx' in document['inputs'] and 'spef_native' not in document['inputs'], 'failed observation scope differs')
            if document['schema'] == 'rusteda.reference-timing.v1':
                require(document['scope']['spef_sources'] == ['openrcx'], 'failed timing observation source set differs')
            for key in ('sdc', 'std_tt', 'std_ss', 'ram_tt', 'ram_ss'):
                require(verify(document['inputs'][key])['sha256'] == self.files[key]['sha256'], 'failed observation original models/constraints differ')
            self.dynamic_record(document['inputs']['netlist'])
            self.dynamic_record(document['inputs']['spef_openrcx'])
        return dict(step=step['id'], exit_code=1, purpose='repair_observation', timing=timing_id,
                    result=result_id, status='expected_failed_observation_retained_not_final_signoff')

    def physical(self, name):
        return physical_report(name, self.reports[name], self.artifacts, self.files, self.dynamic_record)

    def geometry(self):
        path = Path(self.artifacts['def']['path'])
        require(path.stat().st_size <= 128 << 20, 'DEF byte budget')
        macros, count, statement, inside, die, top, units = {}, 0, '', False, None, None, None
        source_counts = {name: 0 for name in ('absent', 'NETLIST', 'DIST', 'USER', 'TIMING')}
        with path.open() as stream:
            for line in stream:
                require(len(line) <= 65536, 'DEF line budget')
                if line.startswith('DESIGN '):
                    require(top is None, 'duplicate DEF design'); top = line.split()[1]
                if line.startswith('UNITS DISTANCE MICRONS '):
                    require(units is None, 'duplicate DEF units'); units = int(line.split()[3])
                if line.startswith('DIEAREA '):
                    require(die is None, 'duplicate DEF die'); die = list(map(int, re.findall(r'-?\d+', line)))
                if line.startswith('COMPONENTS '):
                    require(not inside and not count, 'duplicate DEF components'); inside = True; continue
                if line.startswith('END COMPONENTS'):
                    require(inside and not statement.strip(), 'unfinished DEF component'); inside = False; continue
                if not inside:
                    continue
                statement += line
                require(len(statement) <= 65536, 'DEF component statement budget')
                if ';' not in line:
                    continue
                count += 1; require(count <= 100000, 'DEF component count budget')
                name, master, source, status, x, y, orientation = component_placement(statement)
                source_counts[source or 'absent'] += 1
                if master == 'RM_IHPSG13_1P_1024x32_c2_bm_bist':
                    require(name not in macros, 'duplicate macro placement')
                    macros[name] = dict(position_dbu=[int(x), int(y)], orientation=orientation, placement_status=status)
                statement = ''
        require(not inside and top == 'ibex_sram_bench' and units == 1000, 'DEF top/units differs')
        expected_die = [int(Decimal(str(v)) * 1000) for v in self.contract['common']['die_um']]
        require(die == expected_die and set(macros) == {'u_imem', 'u_dmem'}, 'DEF die/macro set differs')
        for name, wanted in self.contract['common']['macros'].items():
            expected = [int(Decimal(str(v)) * 1000) for v in wanted['location_um']]
            require(macros[name]['position_dbu'] == expected and macros[name]['orientation'] == wanted['orientation'], 'actual macro placement differs')
        return dict(status='passed', component_count=count, macros=macros, die_dbu=die,
                    component_source_counts=source_counts)

    def extraction(self):
        report = self.reports['openrcx_extraction']
        require(report['status'] == 'extracted_not_qualified' and report['extraction_finished'] is True
                and report['source'] == 'openrcx' and report['rc_corner'] == 'nom' and report['top'] == 'ibex_sram_bench', 'actual nominal OpenRCX extraction missing')
        require(report['spef'] == self.artifacts['spef_openrcx_nom'] and report['files']['def'] == self.artifacts['def'], 'extraction source DEF/SPEF differs')
        for observed, original in [('rules_nom', 'rules_nom'), ('tech_lef', 'tech_lef'),
                                   ('cell_lef', 'std_lef'), ('ram_lef', 'ram_lef')]:
            require(report['files'][observed] == self.files[original],
                    'actual OpenRCX source differs from pinned input: ' + observed)
        require(verify(report['files']['rules_nom'])['sha256'] == RC_RULE_SHA256
                and self.contract['common']['rc_calibration_rules'] == RC_RULE_KEY
                and self.contract['common']['rc_calibration_sha256'] == self.contract['files'][RC_RULE_KEY] == RC_RULE_SHA256,
                'actual OpenRCX did not use the original default calibration')
        require(natural(report['annotated_net_records']) and report['annotated_net_records'] > 0, 'empty extraction')
        require(report['tools']['appimage'] == self.tool('appimage'), 'extraction tool differs')
        return dict(status='passed', source='openrcx', rc_corner='nom', sources=1,
                    def_sha256=self.artifacts['def']['sha256'], spef=self.artifacts['spef_openrcx_nom'],
                    rc_calibration=report['files']['rules_nom'])

    def timing(self):
        report, result = self.reports['timing'], self.reports['timing_result']
        failures = []
        for co, row in report.get('corners', {}).items():
            for key in VIOLATIONS:
                if natural(row.get(key)) and row[key] > 0:
                    failures.append(co + '.' + key + '=' + str(row[key]))
        require(report['schema'] == 'rusteda.reference-timing.v1' and report['status'] == 'passed'
                and report['coverage_complete'] is True and report['inputs_unchanged'] is True, 'reference timing failed/incomplete: ' + ', '.join(failures))
        require(result['schema'] == 'rusteda.reference-timing-result.v1' and result['status'] == 'passed'
                and result['coverage_complete'] is True and result['inputs_unchanged'] is True, 'reference timing result failed/incomplete')
        require(result['timing'] == self.report_records['timing'], 'reference timing/result linkage differs')
        expected_inputs = {key: self.files[key] for key in ('sdc', 'std_tt', 'std_ss', 'ram_tt', 'ram_ss')}
        expected_inputs.update(netlist=self.artifacts['netlist_final'], spef_openrcx=self.artifacts['spef_openrcx_nom'])
        for document in (report, result):
            for key, expected in expected_inputs.items():
                source = verify(document['inputs'][key])
                require((source['sha256'], source['bytes']) == (expected['sha256'], expected['bytes']), 'timing input model differs: ' + key)
                if key in ('netlist', 'spef_openrcx'):
                    self.dynamic_record(source)
            require(document['tools']['rusteda'] == self.tool('rusteda') and document['tools']['ruststa'] == self.tool('ruststa')
                    and document['tools']['opensta'] == self.tool('opensta', 'reference'), 'timing executable differs')
            require(verify(document['base_policy'])['sha256'] == '551298f6ededb81ee9d97bfdcde64383d4245da374b6e90b60462aa66e76bec3', 'timing base policy differs')
            require(verify(document['policy']) in self.files.values(), 'reference timing policy is not a pinned producer input')
            for key in ('diagnostic_result', 'diagnostic_timing', 'diagnostic_parity'):
                self.dynamic_record(document[key])
        scope = report['scope']
        for key, wanted in dict(mode='single_reference_openrcx', corners=['tt', 'ss'], spef_sources=['openrcx'], cppr='off',
                                wire_model='spef_rc', clock_model='propagated', rc_corner='nom').items():
            require(scope[key] == wanted, 'reference timing scope differs: ' + key)
        require(scope['netlist_sha256'] == self.artifacts['netlist_final']['sha256']
                and scope['sdc_sha256'] == self.files['sdc']['sha256']
                and scope['spef_sha256'] == self.artifacts['spef_openrcx_nom']['sha256'], 'stale reference timing inputs')
        require(set(report['corners']) == {'tt', 'ss'}, 'reference timing corner set')
        for co, row in report['corners'].items():
            require(row['status'] == 'passed' and row['spef_sha256'] == self.artifacts['spef_openrcx_nom']['sha256'], 'failed/stale timing corner')
            require(all(finite(row[key]) >= 0 for key in ('setup_ws_ns', 'hold_ws_ns'))
                    and all(type(row[key]) is int and row[key] == 0 for key in VIOLATIONS), 'timing/DRV violation')
            require(row['checks_truncated'] is False and all(type(row[key]) is int and row[key] == 0 for key in
                    ('active_unconstrained_endpoints', 'unconstrained_inputs', 'unmodeled_arcs')), 'incomplete timing scope')
            witnesses = row['constant_disabled_endpoints']
            require(len(witnesses) <= 100000 and row['unconstrained_endpoints'] == len(witnesses)
                    and len({w['endpoint'] for w in witnesses}) == len(witnesses), 'constant witness coverage')
            require(all(w['reason'] == 'constant_disabled' and w['native_reason'] == 'constant_pin'
                        and w['verified'] is True and type(w['opensta_value']) is int and w['opensta_value'] in (0, 1) for w in witnesses), 'unverified constant-disabled endpoint')
            native, oracle, complete = row['ruststa'], row['opensta'], row['native_completeness']
            for key in ('setup_ws_ns', 'hold_ws_ns'):
                require(finite(row[key]) == min(finite(native[key]), finite(oracle[key])), 'nonconservative timing metric')
            for key in VIOLATIONS:
                require(natural(native[key]) and natural(oracle[key]) and row[key] == max(native[key], oracle[key]), 'nonconservative DRV count')
            require(complete['unconstrained_endpoints'] == len(witnesses) and complete['unconstrained_inputs'] == 0
                    and complete['unevaluated_limit_checks'] == 0 and sum(complete['unanalyzed_arcs'].values()) == 0, 'native timing completeness failed')
            require(not oracle['check_setup_findings'], 'OpenSTA constraint/clock coverage findings')
            annotation = oracle['annotation']
            require(annotation['active_unannotated_drivers'] == annotation['partially_unannotated_drivers'] == 0, 'partial/active unannotated parasitics')
            require(natural(annotation['unannotated_drivers']) and len(annotation['rows']) == annotation['unannotated_drivers']
                    and all(type(r['connected_loads']) is int and r['connected_loads'] == 0 for r in annotation['rows']), 'unannotated-driver witnesses incomplete')
            for record in oracle['reports'].values():
                self.dynamic_record(record)
        compare = report['native_compare']
        require(compare['status'] == 'diagnostic' and compare['compared_values'] == 4 and len(compare['differences']) == 4,
                'single-source comparison mislabeled as complete parity')
        seen, maximum = set(), 0.0
        for item in compare['differences']:
            pair = (item['corner'], item['field'])
            require(pair not in seen and pair[0] in ('tt', 'ss') and pair[1] in ('setup_ws_ns', 'hold_ws_ns'), 'comparison coverage/duplicates')
            seen.add(pair)
            row = report['corners'][pair[0]]
            require(item.get('source', 'openrcx') == 'openrcx' and item['ruststa_ns'] == row['ruststa'][pair[1]]
                    and item['opensta_ns'] == row['opensta'][pair[1]], 'comparison values differ from exact corner')
            difference = abs(finite(item['ruststa_ns']) - finite(item['opensta_ns']))
            require(math.isclose(finite(item['abs_difference_ns']), difference, abs_tol=1e-12, rel_tol=1e-12), 'incorrect numerical difference')
            maximum = max(maximum, difference)
        require(math.isclose(compare['max_abs_difference_ns'], maximum, abs_tol=1e-12, rel_tol=1e-12), 'incorrect maximum comparison difference')
        # Absolute timing/DRV and completeness are hard gates. This numerical
        # diagnostic never fabricates the native two-source/eight-value gate.
        return dict(status='passed', complete=True, scope=scope, corners=report['corners'], native_compare=compare,
                    native_two_source_accuracy='unverified')

    def macros(self):
        lvs = self.reports['lvs']
        source = self.dynamic_record(lvs['artifacts']['netlist.json'])
        checked = macro_contract.check(Path(source['path']))
        supplied = self.reports['macro_contract']
        require(checked['status'] == supplied['status'] == 'passed' and checked['macro_count'] == supplied['macro_count'] == 2
                and checked['instances'] == supplied['instances'] == ['u_dmem', 'u_imem'] and checked['errors'] == supplied['errors'] == [], 'actual SRAM interface contract failed')
        return dict(status='passed', powered_netlist_json=source, contract=checked)

    def simulation(self):
        report = self.reports['simulation']
        require(report['status'] == 'passed' and report['complete'] is True
                and report['netlist_sha256'] == self.artifacts['netlist_final']['sha256']
                and report['original_rtl_sha256'] == RTL_SHA256
                and report['testbench'] == self.files['tb']
                and report['simulator'] == self.tool('iverilog') and report['runtime'] == self.tool('vvp'), 'stale/incomplete original vendor simulation')
        log = self.dynamic_record(report['log'])
        require('PASS: real SRAM banks, byte masks, boundary addresses, Ibex program stores 42 and 43' in Path(log['path']).read_text(), 'original supplier test completion absent')
        source_report = self.reports.get('simulation_result', report)
        sources = source_report.get('sources')
        require(isinstance(sources, dict) and source_report.get('input_hashes_unchanged') is True, 'original supplier model source binding missing')
        expected = self.contract['files']
        for key, role in [('vendor_0', 'ez130/verilog/ez130_8t.v'),
                          ('vendor_1', 'ihp/libs.ref/sg13g2_sram/verilog/RM_IHPSG13_1P_1024x32_c2_bm_bist.v'),
                          ('vendor_2', 'ihp/libs.ref/sg13g2_sram/verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v')]:
            require(verify(sources[key])['sha256'] == expected[role], 'supplier truth/model differs')
        require(sources['rtl']['sha256'] == RTL_SHA256 and sources['final_netlist'] == self.artifacts['netlist_final'], 'simulation source netlist/RTL differs')
        return dict(status='passed', complete=True, final_netlist=self.artifacts['netlist_final'], supplier_models='original', log=log)

    def run(self):
        methods = {'common_contract': self.common_configuration, 'fresh_provenance': self.provenance,
                   'geometry': self.geometry, 'drc': lambda: self.physical('drc'), 'antenna': lambda: self.physical('antenna'),
                   'lvs': lambda: self.physical('lvs'), 'macros': self.macros, 'extraction': self.extraction,
                   'timing': self.timing, 'simulation': self.simulation,
                   'final_density': lambda: strict_checks.density(self),
                   'synthesis_equivalence': lambda: strict_checks.equivalence(self, 'synthesis'),
                   'final_equivalence': lambda: strict_checks.equivalence(self, 'final')}
        (self.output / 'gates').mkdir()
        for name, method in methods.items():
            try:
                self.gates[name] = method()
            except (OSError, ValueError, KeyError, TypeError, StopIteration, RuntimeError, MemoryError, subprocess.SubprocessError) as error:
                self.gates[name] = dict(status='failed', complete=False, error=str(error))
                self.failures.append(name + ': ' + str(error))
            save(self.output / 'gates' / (name + '.json'), self.gates[name])
        for group in (self.files, self.tools, self.artifacts, self.report_records):
            for record in group.values():
                verify(record)
        for record in self.contract_sources.values():
            verify(record)
        all_passed = not self.failures
        fresh = self.context['mode'] == 'fresh'
        result = dict(schema='rusteda.reference-qualification.v1', status=('passed' if fresh else 'diagnostic_passed') if all_passed else 'failed',
                      complete=all_passed, mode=self.context['mode'], implementation='librelane', runtime='native',
                      end_to_end_qualified=all_passed and fresh, benchmark_eligible=all_passed and fresh,
                      files=self.files, tools=self.tools, artifacts=self.artifacts, reports=self.report_records,
                      gates={name: identity(self.output / 'gates' / (name + '.json')) for name in self.gates}, failed_items=self.failures,
                      formal_equivalence=dict(status='proven' if not any('equivalence' in x for x in self.failures) else 'unverified', required_for_reference_physical_signoff=True,
                          scope='Same native two-state X/Z-as-zero original-RTL event CEC; all states covered; four-state refinement unverified.'),
                      native_two_source_eight_value_accuracy='unverified',
                      input_hashes_unchanged=True)
        save(self.output / 'qualification.json', result)
        return result


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (512 << 20,) * 2)
    output = args.workspace.resolve(); output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock' for p in output.iterdir()), 'REVISION_CONFLICT')
        try:
            result = Qualification(read(args.inputs.resolve(strict=True), 4 << 20), output).run()
        except (OSError, ValueError, KeyError, TypeError, StopIteration, RuntimeError) as error:
            result = dict(schema='rusteda.reference-qualification.v1', status='unverified', complete=False,
                          end_to_end_qualified=False, benchmark_eligible=False, error=str(error))
        result['wall_seconds'] = time.monotonic() - started
        result['metadata_peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        save(output / 'qualification.json', result)
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps({key: result[key] for key in ('schema', 'status', 'complete', 'end_to_end_qualified', 'benchmark_eligible')}))
        return 0 if result['status'] in ('passed', 'diagnostic_passed') else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(json.dumps(dict(status='unverified', error=str(error))), file=sys.stderr)
        raise SystemExit(2)

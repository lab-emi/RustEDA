#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Fresh native LibreLane RTL2GDS with bounded, same-run adaptive repair.

This foreground integration driver owns one 8GiB/2GiB cgroup and one root scale
lock. It starts with fresh Ibex conversion, never accepts an initial checkpoint,
and records every consumed state and final artifact. Preparation, planning,
incremental ECO, rerouting, extraction, vendor simulation and independent
signoff all belong to the measured interval. Tool installation/build is outside.

Controller estimate <192MiB (128 steps/4096 records/64MiB logs); children are
serial, estimated peak3GiB for DRT/antenna and <1GiB for simulation. No graph,
path or device expansion is performed here. Every external child has a wall
deadline and the owner kills its exact complete cgroup on any incomplete run.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time


MIB = 1 << 20
RTL_SHA256 = '8a8fe84b181e6e3b169721034850a1b8fabfd320d988eb89e016598e906911e4'
TOP = 'ibex_sram_bench'
MACRO = 'RM_IHPSG13_1P_1024x32_c2_bm_bist'
HELPERS = {'prepare', 'grt_collector', 'eco_planner', 'timing_runner', 'extract_runner',
           'simulation_runner', 'drc_runner', 'lvs_runner', 'qualifier', 'producer', 'headroom_planner',
           'clock_planner'}
INPUTS = {'contract', 'rtl', 'sdc', 'tb', 'std_tt', 'std_ss', 'ram_tt', 'ram_ss', 'tech_lef',
          'std_lef', 'ram_lef', 'rules_nom', 'drc_deck', 'antenna_deck', 'drc_profile',
          'sram_qualification', 'lvs_deck', 'std_cdl', 'ram_cdl', 'spacer_cdl', 'interfaces',
          'cppr', 'physical_policy', 'vendor_0', 'vendor_1', 'vendor_2', 'config_template',
          'clock_operation', 'logic_runner', 'macro_bb', 'relation_checker', 'density_checker', 'density_policy', 'density_lef', 'physical'} | HELPERS
TOOLS = {'python', 'librelane', 'appimage', 'gnu_time', 'sv2v', 'rusteda', 'ruststa', 'opensta',
         'yosys', 'iverilog', 'vvp', 'abc', 'checker'}
ALGORITHM_FIELDS = {'RUN_POST_GRT_RESIZER_TIMING', 'RUN_POST_GRT_DESIGN_REPAIR',
    'GRT_RESIZER_SETUP_SLACK_MARGIN', 'GRT_RESIZER_HOLD_SLACK_MARGIN',
    'GRT_RESIZER_SETUP_REPAIR_TNS_PCT', 'GRT_DESIGN_REPAIR_MAX_CAP_PCT',
    'GRT_DESIGN_REPAIR_MAX_SLEW_PCT', 'GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH',
    'HEURISTIC_ANTENNA_THRESHOLD', 'CTS_SINK_CLUSTERING_SIZE'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path, cap=8*MIB):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= cap, 'JSON byte budget: '+str(path))
    return json.loads(path.read_bytes(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def identity(path, cap=4 << 30):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= cap, 'file byte budget: '+str(path))
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'file changed while hashing')
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def save(path, value, replace=False):
    path = Path(path)
    data = json.dumps(value, indent=2, allow_nan=False)+'\n'
    require(len(data.encode()) <= 8*MIB, 'output JSON byte budget')
    if replace:
        temp = path.with_name(path.name+'.tmp')
        with temp.open('x') as stream:
            stream.write(data)
        temp.replace(path)
    else:
        with path.open('x') as stream:
            stream.write(data)


def validate(config):
    require(set(config) == {'schema', 'files', 'tools', 'sources', 'limits', 'config_updates'}
        and config['schema'] == 'rusteda.reference-flow-inputs.v1', 'reference flow schema')
    files, tools = config['files'], config['tools']
    require(INPUTS <= set(files) and len(files) <= 256 and set(tools) == TOOLS, 'complete pinned reference input/tool set')
    for group in (files, tools):
        for name, record in group.items():
            require(re.fullmatch('[A-Za-z0-9_-]{1,128}', name) and set(record) == {'path', 'sha256', 'bytes'}, 'unsafe source record')
            require(identity(record['path']) == record, 'input/tool identity differs: '+name)
    require(files['producer'] == identity(__file__), 'producer source differs')
    require(files['rtl']['sha256'] == RTL_SHA256, 'original converted RTL identity')
    require(set(config['sources']) == {'ibex', 'ihp', 'ez130', 'project'}, 'source root set')
    require(all(Path(p).is_absolute() and Path(p).is_dir() for p in config['sources'].values()), 'absolute existing source roots')
    limits = config['limits']
    require(set(limits) == {'threads', 'cpu_affinity', 'memory_mib', 'swap_mib', 'deadline_seconds',
        'max_grt_rounds', 'max_detailed_rounds', 'max_log_bytes'}, 'reference limits')
    require(limits['memory_mib'] == 8192 and limits['swap_mib'] == 2048
        and limits['threads'] == 16 and limits['max_log_bytes'] == 64*MIB, 'matched resource allocation')
    for key, bounds in {'deadline_seconds': (3600, 43200), 'max_grt_rounds': (1, 8), 'max_detailed_rounds': (1, 8)}.items():
        require(type(limits[key]) is int and bounds[0] <= limits[key] <= bounds[1], 'reference work bound: '+key)
    cpus = limits['cpu_affinity']
    require(isinstance(cpus, list) and len(cpus) == 16 and len(set(cpus)) == 16
        and all(type(cpu) is int and cpu in os.sched_getaffinity(0) for cpu in cpus), 'matched CPU allocation')
    physical = set()
    for cpu in cpus:
        root = Path('/sys/devices/system/cpu/cpu'+str(cpu)+'/topology')
        physical.add(((root/'physical_package_id').read_text().strip(), (root/'core_id').read_text().strip()))
    require(len(physical) == 16, 'sixteen distinct physical cores required')
    require(set(config['config_updates']) <= ALGORITHM_FIELDS, 'hard constraints cannot be overridden')


class Flow:
    def __init__(self, config, manifest, root, request):
        self.c, self.root, self.request = config, root, request
        self.files, self.tools, self.limits = config['files'], config['tools'], config['limits']
        self.manifest = identity(manifest)
        self.steps, self.produced, self.configurations = [], {}, []
        self.log_bytes, self.started = 0, time.monotonic()
        self.env = dict(os.environ, TMPDIR=str(root/'tmp'), OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1',
                        RUST_TEST_THREADS='2', CARGO_BUILD_JOBS='2', RAYON_NUM_THREADS='16')
        for name in ('logs', 'tmp', 'plans'):
            (root/name).mkdir(exist_ok=False)

    def file(self, name):
        return self.files[name]['path']

    def local(self, record):
        require(Path(record['path']).is_relative_to(self.root), 'dynamic state is outside this run')
        require(identity(record['path']) == record, 'dynamic state changed')
        return record

    def command(self, name, tool, arguments, outputs, seconds=180, state=None, observation=False):
        require(len(self.steps) < 126 and re.fullmatch('[A-Za-z0-9_-]{1,128}', name)
            and name not in {s['id'] for s in self.steps}, 'producer step budget/identity')
        if state is not None:
            require(self.produced.get(state['path']) == state, 'checkpoint not produced earlier in this run')
            self.local(state)
        command = [self.tools[tool]['path'], *map(str, arguments)]
        require(len(command) <= 512 and all(len(s) <= 65536 and '\x00' not in s for s in command), 'argv budget')
        prefix = self.root/'logs'/name
        timer = self.tools['gnu_time']['path']
        actual = [timer, '-v', '-o', str(prefix.with_suffix('.time')), *command]
        save(prefix.with_suffix('.command.json'), actual)
        tick = time.monotonic()
        disk = os.statvfs(self.root)
        require(disk.f_bavail*disk.f_frsize >= 4 << 30, 'insufficient remaining disk for next guarded stage')
        process = subprocess.Popen(actual, cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            with prefix.with_suffix('.stdout').open('xb') as out, prefix.with_suffix('.stderr').open('xb') as err, selectors.DefaultSelector() as poll:
                for stream, target in ((process.stdout, out), (process.stderr, err)):
                    os.set_blocking(stream.fileno(), False)
                    poll.register(stream, selectors.EVENT_READ, target)
                while poll.get_map():
                    require(time.monotonic()-tick <= seconds and time.monotonic()-self.started <= self.limits['deadline_seconds'], 'producer command deadline')
                    for key, _ in poll.select(.2):
                        data = os.read(key.fileobj.fileno(), 16384)
                        if not data:
                            poll.unregister(key.fileobj)
                            continue
                        self.log_bytes += len(data)
                        require(self.log_bytes <= self.limits['max_log_bytes'], 'aggregate producer log byte budget')
                        key.data.write(data)
                code = process.wait(timeout=10)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.kill()
                process.wait(timeout=10)
        if not (code == 0 or observation and code == 1):
            save(self.root/'failed-step.json', dict(id=name, tool=tool, arguments=list(map(str, arguments)),
                exit_code=code, wall_seconds=time.monotonic()-tick,
                stdout=identity(prefix.with_suffix('.stdout')), stderr=identity(prefix.with_suffix('.stderr'))))
            raise ValueError('command failed: '+name+' exit '+str(code))
        produced = outputs() if callable(outputs) else outputs
        records = {key: self.local(identity(path)) for key, path in produced.items()}
        for record in records.values():
            require(record['path'] not in self.produced or self.produced[record['path']] == record, 'overwritten producer output')
            self.produced[record['path']] = record
        row = dict(id=name, tool=tool, arguments=list(map(str, arguments)), exit_code=code,
            wall_seconds=time.monotonic()-tick, stdout=identity(prefix.with_suffix('.stdout')),
            stderr=identity(prefix.with_suffix('.stderr')), outputs=records,
            purpose='repair_observation' if observation else 'implementation')
        if state is not None:
            row['source_state'] = state
        self.steps.append(row)
        save(self.root/'steps.json', self.steps, replace=(self.root/'steps.json').exists())
        return records

    def helper(self, name, helper, input_data, destination, outputs, seconds=180, state=None, observation=False,
               extra_arguments=()):
        path = self.root/'plans'/(name+'.json')
        save(path, input_data)
        return self.command(name, 'python', [self.file(helper), '--inputs', path, '--workspace', destination,
            '--base-rev', 'empty', *extra_arguments], outputs, seconds, state=state, observation=observation)

    def latest_state(self, tag):
        folder = self.root/'runs'/tag
        require(folder.is_dir(), 'LibreLane run directory missing')
        candidates = []
        for directory in folder.iterdir():
            if directory.is_dir() and re.fullmatch('[0-9]+-[a-z0-9_-]+', directory.name) and (directory/'state_out.json').is_file():
                candidates.append((int(directory.name.split('-', 1)[0]), directory/'state_out.json'))
                require(len(candidates) <= 128, 'LibreLane step count budget')
        require(candidates, 'LibreLane completed no state')
        return max(candidates)[1]

    def ll(self, tag, config, first=None, last='OpenROAD.STAMidPNR-3', state=None):
        path = self.root/(tag+'-config.json')
        save(path, config)
        self.configurations.append(identity(path))
        arguments = ['--manual-pdk', '--pdk-root', self.root/'prepare/pdk', '--pdk', 'ihp-sg13g2',
            '--scl', 'ez130_8t', '-j', '2', '--condensed', '--run-tag', tag, '--to', last]
        if first is not None:
            require(state is not None, 'resumed stage needs a current-run state')
            arguments += ['--from', first, '--with-initial-state', state['path']]
        arguments.append(path)
        def outputs():
            result = {'state': self.latest_state(tag)}
            data = read(result['state'])
            for key in ('odb', 'nl', 'pnl', 'def'):
                if isinstance(data.get(key), str):
                    result[key] = data[key]
            if tag == 'grt-00':
                states = sorted((self.root/'runs'/tag).glob('*-yosys-synthesis/state_out.json'))
                require(len(states) == 1, 'one actual synthesis state required')
                result['synthesis_netlist'] = Path(read(states[0])['nl'])
            final = self.root/'runs'/tag/'final'
            for key, relative in {'gds': 'gds/'+TOP+'.gds', 'spef': 'spef/nom/'+TOP+'.nom.spef'}.items():
                if (final/relative).is_file():
                    result[key] = final/relative
            return result
        records = self.command(tag, 'librelane', arguments, outputs, 7200, state=state)
        if tag == 'grt-00':
            self.synthesis_netlist = records['synthesis_netlist']
        return records

    def prepare(self):
        destination = self.root/'prepare'
        args = [self.file('prepare'), '--workspace', destination, '--base-rev', 'empty',
            '--ibex-source', self.c['sources']['ibex'], '--ihp-root', self.c['sources']['ihp'],
            '--ez130-root', self.c['sources']['ez130'], '--sv2v', self.tools['sv2v']['path'],
            '--gnu-time', self.tools['gnu_time']['path']]
        self.prepared = self.command('prepare', 'python', args, {
            'preparation': destination/'preparation.json', 'rtl': destination/'rtl/design.v'}, 240)
        require(self.prepared['rtl']['sha256'] == RTL_SHA256, 'fresh RTL differs from common frozen workload')
        config = read(destination/'config.json')
        config.update(self.c['config_updates'])
        config.update(DIODE_CELL='ANTENNA8/A', DRT_THREADS=16, DRT_SAVE_SNAPSHOTS=False,
            KLAYOUT_DRC_RUNSET=self.file('drc_deck'))
        return config

    def policy(self, prefix):
        return dict(prefix=prefix, buffer='BUFX8', upsize={'BUFX1': 'BUFX4'}, max_fanout_sinks=4,
            max_repairs=8192, max_pin_visits=1000000, fanout_placement='cluster',
            preserve_placement=True, incremental_global_route=True)

    def eco(self, name, inputs, state):
        destination = self.root/name
        self.helper(name+'_plan', 'eco_planner', inputs, destination,
            {'plan': destination/'plan.json', 'script': destination/'eco.tcl'}, state=state)
        self.command(name+'_apply', 'appimage', ['openroad', '-exit', destination/'eco.tcl'],
            {key: destination/value for key, value in {'odb': 'eco.odb', 'nl': 'eco.v', 'pnl': 'eco.pnl.v',
                'complete': 'complete.txt', 'guides': 'guides-final.guide'}.items()}, 240, state=state)
        text = (destination/'complete.txt').read_text().split()
        require(len(text) == 3 and text[0] == 'requires_full_revalidation' and 0 < int(text[1]) <= 8192,
            'bounded ECO did not complete')
        self.command(name+'_state', 'python', [self.file('producer'), '--_derive-state', state['path'],
            str(destination), '--sdc', self.file('sdc'), '--base-rev', 'rev1'],
            {'state': destination/'state_out.json'}, state=state)
        return self.produced[str(destination/'state_out.json')]

    def protect_clock_port(self, state):
        """Insert at most one real diode before DRT; final antenna is unchecked."""
        destination = self.root/'primary-clock-protection'
        source = read(self.local(state)['path'])
        files = {key: self.files[value] for key, value in {'sdc': 'sdc', 'cppr': 'cppr',
            'tt_std': 'std_tt', 'tt_ram': 'ram_tt', 'ss_std': 'std_ss', 'ss_ram': 'ram_ss'}.items()}
        files['odb'] = self.local(identity(source['odb']))
        require(self.produced.get(files['odb']['path']) == files['odb'],
            'clock protection database is not an earlier current-run output')
        inputs = dict(schema='rusteda.primary-clock-antenna-inputs.v1', files=files,
            shared_policy=self.files['eco_planner'], policy=dict(clock_port='clk_i',
                prefix='clockport_protect', diode_master='ANTENNA8', signal_pin='A',
                max_instances=100000, max_visits=1000000, max_sinks=4096,
                max_new_diodes=1, max_anchor_distance_dbu=100000))
        self.helper('primary_clock_plan', 'clock_planner', inputs, destination,
            {'plan': destination/'plan.json', 'script': destination/'clock-protect.tcl'}, state=state)
        plan = read(destination/'plan.json')
        require(plan['operation'] == self.files['clock_operation']
            and plan['shared_policy'] == self.files['eco_planner'] and plan['files'] == files,
            'clock protection source binding differs')

        def outputs():
            outcome = destination/'outcome.tsv'
            require(outcome.is_file() and outcome.stat().st_size <= 16384, 'clock outcome byte budget')
            rows = outcome.read_text().splitlines()
            require(1 <= len(rows) <= 32 and all(line.count('\t') == 1 for line in rows),
                'clock outcome row schema')
            fields = dict(line.split('\t') for line in rows)
            require(len(fields) == len(rows) and fields.get('antenna') == 'unverified',
                'clock outcome cannot certify antenna')
            added = fields.get('status') == 'inserted_one_clock_port_diode_requires_signoff'
            require(added or fields.get('status') == 'no_op_existing_clock_diode',
                'clock protection did not complete')
            require(fields.get('new_diodes') == ('1' if added else '0'), 'clock diode count differs')
            result = {'outcome': outcome}
            paths = {key: destination/value for key, value in {'odb': 'clock-protected.odb',
                'nl': 'clock-protected.v', 'pnl': 'clock-protected.pnl.v', 'guides': 'guides-final.guide'}.items()}
            if added:
                result.update(paths)
            else:
                require(not any(path.exists() for path in paths.values()), 'clock no-op wrote implementation outputs')
            return result

        applied = self.command('primary_clock_apply', 'appimage',
            ['openroad', '-exit', destination/'clock-protect.tcl'], outputs, 240, state=state)
        if 'odb' not in applied:
            return state
        derived = self.command('primary_clock_state', 'python', [self.file('producer'),
            '--_derive-state', state['path'], str(destination), '--sdc', self.file('sdc'),
            '--base-rev', 'rev1', '--output-prefix', 'clock-protected'],
            {'state': destination/'state_out.json'}, state=state)
        return derived['state']

    def grt(self, config):
        tt = self.ll('grt-00', config)
        # The initial port net can have no diode even when the LEF antenna
        # estimator reports zero. Protect its witnessed clock sink before
        # detailed pin access, then observe fresh parasitics without another
        # resizer pass. The original foundry antenna table remains mandatory.
        protected = self.protect_clock_port(tt['state'])
        tt = self.ll('grt-clock-protected-tt', dict(config,
            RUN_POST_GRT_RESIZER_TIMING=False, RUN_POST_GRT_DESIGN_REPAIR=False),
            'OpenROAD.STAMidPNR-3', state=protected)
        for index in range(self.limits['max_grt_rounds']):
            ssconfig = dict(config, DEFAULT_CORNER='nom_slow_1p08V_125C')
            ss = self.ll('grt-ss-'+str(index), ssconfig, 'OpenROAD.STAMidPNR-3', state=tt['state'])
            tdata, sdata = read(tt['state']['path']), read(ss['state']['path'])
            require(tdata['odb'] == sdata['odb'], 'TT/SS GRT checks use different geometry')
            common = {key: self.files[value] for key, value in {'sdc': 'sdc', 'cppr': 'cppr',
                'tt_std': 'std_tt', 'tt_ram': 'ram_tt', 'ss_std': 'std_ss', 'ss_ram': 'ram_ss'}.items()}
            common.update(tt_state=tt['state'], ss_state=ss['state'], odb=identity(tdata['odb']),
                tt_report=identity(Path(tt['state']['path']).parent/'checks.rpt'),
                ss_report=identity(Path(ss['state']['path']).parent/'checks.rpt'))
            data = dict(schema='rusteda.reference-grt-inputs.v1', run_root=str(self.root), files=common,
                tools={'appimage': self.tools['appimage']}, policy=self.policy('grt'+str(index)))
            # The public CTS cluster-size knob does not bound internal clock
            # branch fanout. Repair only witnessed CLOCK max-fanout rows here;
            # clock slew/capacitance and ambiguous drivers still fail closed.
            # All raw signal obligations remain in the collector's evidence.
            clock_destination = self.root/('grt-clock-observation-'+str(index))
            self.helper('grt_clock_observe_'+str(index), 'grt_collector', data, clock_destination,
                {'summary': clock_destination/'summary.json', 'inputs': clock_destination/'eco-inputs.json'},
                240, state=tt['state'], extra_arguments=('--clock-fanout-only',))
            clock_summary = read(clock_destination/'summary.json')
            require(clock_summary.get('clock_fanout_only') is True
                and type(clock_summary.get('needs_eco')) is bool, 'explicit clock repair observation required')
            if clock_summary['needs_eco']:
                state = self.eco('grt-clock-eco-'+str(index),
                    read(clock_destination/'eco-inputs.json'), tt['state'])
                # New clock stages change insertion delay and skew. Re-run
                # propagated-clock timing repair before observing BOTH corners
                # on the new geometry; never reuse the pre-ECO reports.
                repair_config = dict(config, RUN_POST_GRT_RESIZER_TIMING=True,
                    RUN_POST_GRT_DESIGN_REPAIR=False)
                tt = self.ll('grt-clock-tt-'+str(index), repair_config,
                    'OpenROAD.ResizerTimingPostGRT', state=state)
                continue
            destination = self.root/('grt-observation-'+str(index))
            self.helper('grt_observe_'+str(index), 'grt_collector', data, destination,
                {'summary': destination/'summary.json', 'inputs': destination/'eco-inputs.json'}, 240, state=tt['state'])
            summary = read(destination/'summary.json')
            require(summary.get('clock_fanout_only') is False
                and type(summary.get('needs_eco')) is bool, 'complete signal repair observation required')
            if summary['needs_eco'] is False:
                return tt['state']
            state = self.eco('grt-eco-'+str(index), read(destination/'eco-inputs.json'), tt['state'])
            config = dict(config, RUN_POST_GRT_RESIZER_TIMING=False, RUN_POST_GRT_DESIGN_REPAIR=False)
            tt = self.ll('grt-tt-'+str(index), config, 'OpenROAD.STAMidPNR-3', state=state)
        raise ValueError('global-route adaptive repair round budget exhausted')

    def timing(self, name, final, spef):
        files = {key: self.files[key] for key in ('sdc', 'std_tt', 'ram_tt', 'std_ss', 'ram_ss')}
        files.update(netlist=final['nl'], spef_openrcx=spef, physical_lef=self.files['std_lef'])
        destination = self.root/name
        data = dict(schema='rusteda.reference-timing-inputs.v1', top=TOP, files=files,
            tools={key: self.tools[key] for key in ('rusteda', 'ruststa', 'opensta', 'gnu_time')},
            policy=self.files['physical_policy'], budgets=dict(threads=2, timeout_seconds=180,
                max_input_bytes=128*MIB, max_output_bytes=256*MIB, max_state_gib=2, max_limit_rows=100000, max_checks=100000),
            scope=dict(mode='single_reference_openrcx', rc_corner='nom', cppr='off', precision='f64', backend='cpu',
                clock_model='propagated', wire_model='spef_rc', tolerance_ns=.001))
        return self.helper(name, 'timing_runner', data, destination,
            {'reference_timing': destination/'reference-timing.json', 'result': destination/'result.json',
             'bindings': destination/'eco-bindings.json'}, 600, state=final['state'], observation=True)

    def headroom(self, name, inputs, final, observation):
        # The current extracted full tables define an optimization target of
        # 80% of each ORIGINAL capacitance limit. Original violations of every
        # kind remain obligations. No global fanout-four expansion is requested.
        import reference_grt_inputs
        require(identity(reference_grt_inputs.__file__) == self.files['grt_collector'], 'census generator source differs')
        census = self.root/(name+'-census')
        census.mkdir()
        script = census/'census.tcl'
        files = {key: inputs['files'][key] for key in ('sdc', 'cppr', 'tt_std', 'tt_ram', 'ss_std', 'ss_ram')}
        script.write_text(reference_grt_inputs.census_script(files, final['odb']['path'], census))
        self.command(name+'_census', 'appimage', ['openroad', '-exit', script],
            {'census': census/'census.tsv', 'script': script}, 240, state=final['state'])
        source = self.root/'plans'/(name+'-source.json')
        save(source, inputs)
        observer = Path(observation['reference_timing']['path']).parent
        destination = self.root/(name+'-plan')
        self.command(name+'_headroom', 'python', [self.file('headroom_planner'), '--inputs', source,
            '--census', census/'census.tsv', '--census-script', script,
            '--tt-cap', observer/'observation/reports/openrcx-tt-cap.rpt',
            '--ss-cap', observer/'observation/reports/openrcx-ss-cap.rpt',
            '--ss-script', observer/'observation/reports/driver-bindings.tcl',
            '--workspace', destination, '--base-rev', 'empty', '--capacitance-only'],
            {'inputs': destination/'inputs.json', 'plan': destination/'plan.json'}, 180, state=final['state'])
        return read(destination/'inputs.json')

    def detailed(self, config, state):
        config = dict(config, RUN_POST_GRT_RESIZER_TIMING=False, RUN_POST_GRT_DESIGN_REPAIR=False,
            DRT_ANTENNA_REPAIR_ITERS=0)
        for index in range(self.limits['max_detailed_rounds']):
            # Existing diode pins participate in initial DRT pin access on every
            # pass. Keep insertion in the explicit GRT stages so DRT sees the
            # sealed diode set. Full foundry antenna signoff still checks every
            # original rule on the actual final GDS.
            final = self.ll('detailed-'+str(index), config, 'OpenROAD.DetailedRouting', 'Checker.XOR', state=state)
            observed = self.timing('routed-timing-'+str(index), final, final['spef'])
            observation = read(observed['reference_timing']['path'])
            if observation['status'] == 'passed':
                extraction = self.root/('independent-rcx-'+str(index))
                data = dict(schema='rusteda.reference-extraction-inputs.v1', top=TOP,
                    files=dict(def_=final['def'], tech_lef=self.files['tech_lef'], cell_lef=self.files['std_lef'],
                        ram_lef=self.files['ram_lef'], rules_nom=self.files['rules_nom']),
                    tools={key: self.tools[key] for key in ('appimage', 'gnu_time')},
                    budgets=dict(threads=2, timeout_seconds=900, max_spef_bytes=128*MIB, max_nets=100000, max_log_bytes=32*MIB))
                data['files']['def'] = data['files'].pop('def_')
                extracted = self.helper('independent_rcx_'+str(index), 'extract_runner', data, extraction,
                    {'result': extraction/'result.json', 'spef': extraction/'nom.spef'}, 960, state=final['state'])
                checked = self.timing('signoff-timing-'+str(index), final, extracted['spef'])
                if read(checked['reference_timing']['path'])['status'] == 'passed':
                    return final, extracted, checked
                observed = checked
            binding = read(observed['bindings']['path'])
            require(any(binding['bindings'].values()) or binding.get('native_extra') or binding.get('annotation_scope'),
                'timing failure has no qualified repair obligation; preserve reports for diagnosis')
            files = {key: self.files[value] for key, value in {'sdc': 'sdc', 'cppr': 'cppr',
                'tt_std': 'std_tt', 'tt_ram': 'ram_tt', 'ss_std': 'std_ss', 'ss_ram': 'ram_ss'}.items()}
            files.update(odb=final['odb'], netlist=final['nl'], drv_bindings=observed['bindings'])
            inputs = dict(schema='rusteda.reference-drv-inputs.v1', files=files, policy=self.policy('post'+str(index)))
            inputs = self.headroom('post-'+str(index), inputs, final, observed)
            state = self.eco('detailed-eco-'+str(index), inputs, final['state'])
        raise ValueError('detailed-route adaptive repair round budget exhausted')

    def signoff(self, final, extraction, timing):
        reports = {'timing': timing['reference_timing'], 'timing_result': timing['result'], 'openrcx_extraction': extraction['result']}
        for name, table in (('drc', 'main'), ('antenna', 'antenna')):
            destination = self.root/('signoff-'+name)
            self.command('signoff_'+name, 'python', [self.file('drc_runner'), '--gds', final['gds']['path'],
                '--profile', self.file('drc_profile'), '--tool', self.tools['appimage']['path'], '--table', table,
                '--workspace', destination, '--base-rev', 'empty'], {'report': destination/'summary.json'},
                1200, state=final['state'])
            reports[name] = identity(destination/'summary.json')
        destination = self.root/'signoff-lvs'
        data = dict(schema='rusteda.device-lvs-inputs.v1', top=TOP, gds=final['gds']['path'],
            powered_netlist=final['pnl']['path'], deck=self.file('lvs_deck'),
            cdl=[self.file(key) for key in ('std_cdl', 'ram_cdl', 'spacer_cdl')], interfaces=self.file('interfaces'),
            macro=MACRO, macro_count=2, macro_qualification=self.file('sram_qualification'),
            tools={key: self.tools[value]['path'] for key, value in {'appimage': 'appimage', 'yosys': 'yosys', 'time': 'gnu_time'}.items()},
            limits=dict(memory_mib=4096, swap_mib=512, deadline_seconds=1200, threads=2))
        self.helper('signoff_lvs', 'lvs_runner', data, destination,
            {'report': destination/'summary.json', 'netlist': destination/'netlist.json'}, 1260, state=final['state'])
        reports['lvs'] = identity(destination/'summary.json')
        sim = self.root/'signoff-simulation'
        simfiles = {key: self.files[key] for key in ('rtl', 'tb', 'std_tt', 'std_ss', 'ram_tt', 'ram_ss',
            'vendor_0', 'vendor_1', 'vendor_2', 'physical_policy')}
        simfiles.update(netlist=final['nl'], physical_lef=self.files['std_lef'])
        self.helper('signoff_simulation', 'simulation_runner', dict(schema='rusteda.reference-simulation-inputs.v1',
            files=simfiles, tools={key: self.tools[key] for key in ('yosys', 'iverilog', 'vvp', 'gnu_time')}), sim,
            {'simulation': sim/'simulation.json', 'summary': sim/'summary.json', 'macros': sim/'macro-contract.json'}, 600, state=final['state'])
        reports.update(simulation=identity(sim/'simulation.json'), simulation_result=identity(sim/'summary.json'),
            macro_contract=identity(sim/'macro-contract.json'))
        logic = self.root/'signoff-logic'
        data = dict(schema='rusteda.logic-signoff-inputs.v1', top=TOP, rtl=self.file('rtl'),
            synthesis_netlist=self.synthesis_netlist['path'], final_netlist=final['nl']['path'],
            library=self.file('std_tt'), macro_bb=self.file('macro_bb'), macro=MACRO,
            macro_instances=['u_imem','u_dmem'], tb=self.file('tb'),
            vendor_verilog=[self.file('vendor_'+str(i)) for i in range(3)],
            vendor_include_dirs=[str(Path(self.file('vendor_1')).parent)], relation_checker=self.file('relation_checker'),
            tools={k:self.tools[v]['path'] for k,v in dict(yosys='yosys',abc='abc',checker='checker',iverilog='iverilog',vvp='vvp',time='gnu_time').items()},
            physical_lef=self.file('std_lef'),
            physical_libraries={k:self.file(k) for k in ('std_tt','ram_tt','std_ss','ram_ss')},
            limits=dict(memory_mib=4096,swap_mib=512,deadline_seconds=1800,threads=2,
                        max_conflicts=2000000,discovery_work=16000000000,discovery_rounds=32),
            expected_hashes=dict(rtl=self.files['rtl']['sha256'],library=self.files['std_tt']['sha256'],
                macro_bb=self.files['macro_bb']['sha256'],tb=self.files['tb']['sha256'],
                synthesis_netlist=self.synthesis_netlist['sha256'],final_netlist=final['nl']['sha256']))
        self.helper('signoff_logic', 'logic_runner', data, logic,
            {k:logic/(k+'.json') for k in ('synthesis_equivalence','final_equivalence','summary')},1920,state=final['state'])
        reports.update({k:identity(logic/(k+'.json')) for k in ('synthesis_equivalence','final_equivalence')})
        return reports

    def run(self, stop_after=None):
        config = self.prepare()
        if stop_after == 'prepare':
            save(self.root/'prefix-result.json', dict(status='unverified', complete=False,
                reason='explicit preparation-only control; no EDA qualification', steps=self.steps))
            return None
        state = self.grt(config)
        final, extraction, timing = self.detailed(config, state)
        reports = self.signoff(final, extraction, timing)
        artifacts = {key: final[value] for key, value in {'gds': 'gds', 'def': 'def', 'netlist_final': 'nl',
            'netlist_powered_final': 'pnl'}.items()}
        artifacts['netlist_synth'] = self.synthesis_netlist
        artifacts['spef_openrcx_nom'] = extraction['spef']
        actual_rcx = read(extraction['result']['path'])
        contract = read(self.file('contract'))
        require(actual_rcx['files']['rules_nom'] == self.files['rules_nom']
            and self.files['rules_nom']['sha256'] == contract['common']['rc_calibration_sha256'],
            'actual final extraction uses another nominal calibration')
        for record in [*self.files.values(), *self.tools.values()]:
            require(identity(record['path']) == record, 'whole-flow input changed')
        binding = dict(schema='rusteda.reference-run-binding.v1', implementation='librelane', runtime='native',
            request_id=self.request, run_root=str(self.root), manifest=self.manifest,
            start_binding=identity(self.root/'start-binding.json'), preparation=self.prepared['preparation'],
            initial_state=None, steps=self.steps, artifacts=artifacts, inputs_unchanged=True,
            configurations=self.configurations, status='implementation_completed')
        save(self.root/'run-binding.json', binding)
        relative = lambda record: str(Path(record['path']).relative_to(self.root))
        data = dict(schema='rusteda.reference-qualification-inputs.v1', mode='fresh', run_root=str(self.root),
            files=self.files, tools=self.tools, artifacts={key: relative(value) for key, value in artifacts.items()},
            reports={key: relative(value) for key, value in reports.items()}, run_binding='run-binding.json')
        self.helper('qualify', 'qualifier', data, self.root/'qualification',
            {'qualification': self.root/'qualification/qualification.json'}, 180)
        return artifacts


def scope_state(unit):
    result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'Description', '-p', 'ActiveState', '-p', 'ControlGroup'],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def launch(args, config):
    root = args.workspace.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root/'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock' for p in root.iterdir()), 'REVISION_CONFLICT')
        project = Path(config['sources']['project'])
        with (project/'.work/scale.lock').open('a') as scale:
            fcntl.flock(scale, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = args.started
            available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
            require(available >= 10 << 30, 'insufficient available memory for guarded reference run')
            disk = os.statvfs(root)
            require(disk.f_bavail*disk.f_frsize >= 12 << 30, 'insufficient disk space for fresh evidence')
            request = hashlib.sha256((identity(args.inputs)['sha256']+'\n'+str(root)+'\n'+str(args.stop_after)).encode()).hexdigest()
            ownership = root/'ownership'
            ownership.mkdir()
            save(ownership/'native.json', dict(implementation='native', flow_implementation='librelane',
                request_id=request, manifest=identity(args.inputs)))
            save(root/'start-binding.json', dict(schema='rusteda.reference-start-binding.v1', request_id=request,
                run_root=str(root), manifest=identity(args.inputs), producer=config['files']['producer'],
                files=config['files'], tools=config['tools'], initial_state=None, rtl_expected_sha256=RTL_SHA256))
            save(root/'estimate.json', dict(controller_mib=192, serial_child_peak_mib=3072,
                memory_mib=8192, swap_mib=2048, allocated_physical_cores=16,
                independent_checker_threads=2, host_mem_available_bytes=available, max_steps=128,
                max_grt_rounds=config['limits']['max_grt_rounds'], max_detailed_rounds=config['limits']['max_detailed_rounds']))
            unit = 'rusteda-worker-'+request[:24]+'.scope'
            description = 'RustEDA worker '+request
            environment = dict(os.environ, RUSTEDA_WORKER_ID=request, RUSTEDA_OWNERSHIP=str(ownership))
            command = ['systemd-run', '--user', '--scope', '-q', '--unit', unit, '--description', description,
                '-p', 'MemoryMax=8G', '-p', 'MemorySwapMax=2G', '-p', 'AllowedCPUs='+','.join(map(str, config['limits']['cpu_affinity'])),
                config['tools']['gnu_time']['path'], '-v', '-o', str(root/'whole.time'),
                config['tools']['python']['path'], config['files']['producer']['path'], '--_worker',
                '--inputs', str(args.inputs.resolve()), '--workspace', str(root), '--request-id', request]
            if args.stop_after:
                command += ['--stop-after', args.stop_after]
            save(root/'whole-command.json', command)
            process = None
            peak = swap = None
            code, stopped, count, detached = None, False, 0, False
            try:
                process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                with (root/'whole.stdout').open('xb') as out, (root/'whole.stderr').open('xb') as err, selectors.DefaultSelector() as poll:
                    for stream, target in ((process.stdout, out), (process.stderr, err)):
                        os.set_blocking(stream.fileno(), False)
                        poll.register(stream, selectors.EVENT_READ, target)
                    while poll.get_map():
                        require(time.monotonic()-started <= config['limits']['deadline_seconds'], 'whole-flow deadline reached')
                        state = scope_state(unit)
                        if state.get('Description') == description and state.get('ControlGroup'):
                            group = Path('/sys/fs/cgroup')/state['ControlGroup'].lstrip('/')
                            for filename, kind in (('memory.peak', 'memory'), ('memory.swap.peak', 'swap')):
                                try:
                                    value = int((group/filename).read_text())
                                    if kind == 'memory':
                                        peak = max(peak or 0, value)
                                    else:
                                        swap = max(swap or 0, value)
                                except FileNotFoundError:
                                    pass
                        for key, _ in poll.select(.5):
                            chunk = os.read(key.fileobj.fileno(), 16384)
                            if not chunk:
                                poll.unregister(key.fileobj)
                                continue
                            count += len(chunk)
                            require(count <= 64*MIB, 'whole controller log budget')
                            key.data.write(chunk)
                    code = process.wait(timeout=10)
            finally:
                state = scope_state(unit)
                if state.get('ActiveState') == 'active':
                    detached = True
                    require(state.get('Description') == description, 'owned scope identity changed; refusing unrelated cleanup')
                    subprocess.run(['systemctl', '--user', 'kill', '--kill-whom=all', '--signal=KILL', unit],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
                for _ in range(50):
                    state = scope_state(unit)
                    if state.get('ActiveState') in ('inactive', 'failed'):
                        stopped = True
                        break
                    time.sleep(.1)
                if process and process.poll() is None:
                    process.wait(timeout=10)
                save(root/'resource-receipt.json', dict(exit_code=code, cleanup_verified=stopped, unit=unit,
                    request_id=request, wall_seconds=time.monotonic()-started, cgroup_memory_peak_bytes=peak,
                    cgroup_swap_peak_bytes=swap, memory_mib=8192, swap_mib=2048,
                    detached_scope=detached, cpu_affinity=config['limits']['cpu_affinity'],
                    scope='preparation-only control' if args.stop_after else 'fresh original RTL through all repair and independent signoff'))
            require(code == 0 and stopped and not detached, 'fresh reference flow failed or cleanup incomplete')
            (root/'HEAD').write_text('rev1\n')
            print(json.dumps(dict(status='unverified' if args.stop_after else 'passed',
                qualification=None if args.stop_after else str(root/'qualification/qualification.json'),
                resource_receipt=str(root/'resource-receipt.json'))))


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--base-rev')
    parser.add_argument('--stop-after', choices=('prepare',), help='Explicit unqualified preparation-only control')
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--request-id', help=argparse.SUPPRESS)
    parser.add_argument('--_derive-state', nargs=2, metavar=('SOURCE', 'ECO'), help=argparse.SUPPRESS)
    parser.add_argument('--sdc', help=argparse.SUPPRESS)
    parser.add_argument('--output-prefix', choices=('eco', 'clock-protected'), default='eco',
        help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.started = started
    if args._derive_state:
        source, destination = map(Path, args._derive_state)
        with (destination/'workspace.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(args.base_rev == 'rev1' and (destination/'HEAD').read_text().strip() == args.base_rev
                and not (destination/'state_out.json').exists(), 'REVISION_CONFLICT')
            state = read(source)
            state = {key: state[key] for key in ('json_h', 'vh') if key in state}
            prefix = args.output_prefix
            state.update(odb=str(destination/(prefix+'.odb')), nl=str(destination/(prefix+'.v')),
                pnl=str(destination/(prefix+'.pnl.v')),
                sdc=str(Path(args.sdc).resolve(strict=True)), metrics={}, def_=None)
            state['def'] = state.pop('def_')
            save(destination/'state_out.json', state)
            (destination/'HEAD').write_text('rev2\n')
        return
    require(args.inputs is not None and args.workspace is not None, '--inputs/--workspace required')
    config = read(args.inputs, 2*MIB)
    validate(config)
    if args._worker:
        require(args.request_id == os.environ.get('RUSTEDA_WORKER_ID'), 'worker ownership differs')
        from signoff_lvs import inherited_owner
        require(inherited_owner() is not None, 'actual owned cgroup required')
        artifacts = Flow(config, args.inputs, args.workspace.resolve(), args.request_id).run(args.stop_after)
        print(json.dumps(dict(status='unverified' if args.stop_after else 'passed', artifacts=artifacts)))
    else:
        require(args.base_rev is not None, '--base-rev required')
        launch(args, config)


if __name__ == '__main__':
    main()

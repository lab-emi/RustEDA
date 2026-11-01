#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Append concrete native physical implementation and independent signoff.

The input frontend fragment is a recipe, never a historical database/report.
All mutable physical inputs below are generated inside the current run. The
fragment remains unqualified until logic proofs and complete gate normalization
are appended. A prefix manifest can only be used with producer --stop-after.

Builder estimate <64 MiB; <=128 steps, <=256 files/32 tools, <=2 MiB config.
Heavy child estimates/guards are recorded by the individual bounded helpers.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import sys


REQUIRED = {'physical_prepare', 'rc_builder', 'reference_extract', 'timing_runner', 'postroute_runner',
            'lvs_runner', 'drc_runner', 'macro_contract', 'std_gds', 'ram_gds',
            'layer_map', 'rules_nom', 'magic_tech', 'magic_extract', 'ram_lef',
            'drc_profile', 'drc_deck', 'antenna_deck', 'lvs_deck', 'std_cdl',
            'ram_cdl', 'filler_cdl', 'sram_qualification', 'lvs_interfaces',
            'antenna_runner', 'antenna_fields', 'contract'}
V2_FILES = {'antenna_group_policy', 'antenna_legacy', 'antenna_verifier', 'antenna_source_manifest'}
GROUP_POLICY = dict(group_names=128, max_groups=1024, max_attempts=64,
    max_total_jumpers=4096, max_commands=4096, max_report_bytes=8 << 30,
    max_selected_report_bytes=128 << 20, max_metadata_bytes=4 << 20,
    max_cumulative_work=200000000000)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    require(path.is_file() and path.stat().st_size <= 2 << 20, 'JSON byte budget')
    return json.loads(path.read_text())


def identity(path):
    path = path.resolve(strict=True)
    require(path.is_file() and path.stat().st_size <= 4 << 30, 'input byte budget')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=str(path), sha256=digest, bytes=path.stat().st_size)


def capture_policy(record):
    """Load the same explicitly pinned producer policy used by the frontend."""
    path = Path(record['path'])
    require(identity(path) == record and record['bytes'] <= 2 << 20, 'producer policy changed')
    spec = importlib.util.spec_from_file_location('native_capture_policy', path)
    require(spec is not None and spec.loader is not None, 'producer policy loader')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(identity(path) == record, 'producer policy changed during import')
    return module


def grouped_antenna(files):
    """An explicit policy opts in; all imported Python siblings are pinned."""
    if 'antenna_group_policy' not in files:
        require(not (V2_FILES & set(files)), 'v2 antenna sources require explicit group policy')
        return None
    require(V2_FILES <= set(files), 'incomplete explicit v2 antenna source pins')
    for role in V2_FILES | {'antenna_runner', 'antenna_fields'}:
        require(identity(Path(files[role]['path'])) == files[role], 'v2 source changed: ' + role)
    def unique(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicate v2 policy field')
            value[key] = item
        return value
    policy_path = Path(files['antenna_group_policy']['path'])
    require(policy_path.stat().st_size <= 65536, 'v2 policy byte budget')
    policy = json.loads(policy_path.read_bytes(), object_pairs_hook=unique)
    require(policy == GROUP_POLICY and all(type(v) is int for v in policy.values()),
            'v2 antenna policy must match qualified integer budgets')
    runner = Path(files['antenna_runner']['path']).resolve(strict=True)
    require(runner.name == 'native_antenna_groups.py'
            and Path(files['antenna_legacy']['path']).resolve(strict=True) == runner.parent / 'native_antenna.py'
            and Path(files['antenna_fields']['path']).resolve(strict=True) == runner.parent / 'report_fields.py',
            'v2 antenna imported legacy/field reader must be explicit same-directory files')
    manifest = read(Path(files['antenna_source_manifest']['path']))
    require(manifest['schema'] == 'rusteda.private-source-review.v1'
            and isinstance(manifest['sources'], list) and len(manifest['sources']) <= 128,
            'v2 antenna source manifest schema/budget')
    recorded = {}
    for item in manifest['sources']:
        record = {k: item[k] for k in ('path', 'sha256', 'bytes')}
        require(identity(Path(record['path'])) == record, 'v2 source snapshot changed')
        name = Path(record['path']).name
        require(name not in recorded, 'duplicate v2 source role')
        recorded[name] = record
    for role, name in [('antenna_runner', 'native_antenna_groups.py'),
                       ('antenna_legacy', 'native_antenna.py'), ('antenna_fields', 'report_fields.py')]:
        require(name in recorded and all(files[role][k] == recorded[name][k] for k in ('sha256', 'bytes')),
                'v2 executable dependency differs from frozen source: ' + role)
    return policy



def single_process_antenna(files):
    """A pinned explicit policy selects the single-process lineage protocol."""
    if 'antenna_session_policy' not in files:
        return False
    record = files['antenna_session_policy']
    path = Path(record['path'])
    require(record['bytes'] <= 65536 and identity(path) == record,
            'antenna session policy changed or exceeds byte budget')
    policy = read(path)
    require(policy == dict(schema='rusteda.antenna-session-policy.v1',
        execution_mode='single_native_process', actual_native_processes=1)
        and type(policy['actual_native_processes']) is int,
        'antenna session execution policy differs')
    return True


def build(config):
    required = {'schema', 'frontend_fragment', 'files', 'tools', 'pdk_root'}
    require(required <= set(config) and not set(config) - required - {'parallel_signoff'}
            and config['schema'] == 'rusteda.native-backend-inputs.v1', 'backend config schema')
    require(type(config.get('parallel_signoff', False)) is bool, 'parallel_signoff must be explicit boolean')
    record = config['frontend_fragment']
    require(identity(Path(record['path'])) == record, 'frontend recipe changed')
    front = read(Path(record['path']))
    require(front['schema'] == 'rusteda.native-frontend-fragment.v1', 'frontend recipe schema')
    require(REQUIRED <= set(config['files']) and len(config['files']) <= 128,
            'backend collateral incomplete')
    require(set(config['tools']) == {'appimage', 'opensta', 'yosys'}, 'reference tool set')
    files, tools = dict(front['files']), dict(front['tools'])
    policy = capture_policy(files['producer'])
    policy.validate_stdout_artifacts(front['steps'], front.get('stdout_artifacts'), front['scope'])
    for source, target in ((config['files'], files), (config['tools'], tools)):
        for key, item in source.items():
            require(key not in target or target[key] == item, 'conflicting input identity: ' + key)
            require(identity(Path(item['path'])) == item, 'backend input changed: ' + key)
            target[key] = item
    require(len(files) <= 256 and len(tools) <= 32, 'aggregate input budget')
    antenna_grouping = grouped_antenna(files)
    antenna_session = single_process_antenna(files)
    require(not antenna_session or antenna_grouping is not None,
            'antenna session requires the explicit grouped policy')
    contract = read(Path(files['contract']['path']))
    require(contract['common']['rc_calibration_rules'] == 'ihp/libs.tech/librelane/openrcx/IHP_rcx_patterns.rules'
            and files['rules_nom']['sha256'] == contract['common']['rc_calibration_sha256'],
            'native backend must use the pinned original PDK default nominal calibration')
    steps = list(front['steps'])
    output = '{output}/backend/'
    road = '{output}/' + front['outputs']['final_workspace']
    final_def = '{output}/logs/be_def.stdout'
    final_netlist = '{output}/logs/be_netlist.stdout'
    powered = '{output}/logs/be_powered.stdout'
    gds = output + 'ibex_sram_bench.gds'
    native_spef = output + 'native.nom.spef'
    rcx_spef = output + 'openrcx/nom.spef'

    def render(name, filename, content):
        steps.append(dict(id='be_' + name, kind='render', destination='backend/' + filename,
                          content=content))

    def command(name, tool, arguments, seconds=180):
        steps.append(dict(id='be_' + name, kind='command', tool=tool,
                          arguments=arguments, timeout_s=seconds))

    def input_id(name):
        return {'$input': name}

    def output_id(path):
        return {'$identity': path}

    def output_sha(path):
        return {'$sha256': path}

    render('physical_inputs', 'physical-inputs.json',
           dict(schema='rusteda.native-physical-inputs.v1', pdk_root=config['pdk_root'],
                files={key: input_id(key) for key in ('std_gds', 'ram_gds', 'layer_map',
                       'tech_lef', 'rules_nom', 'magic_tech', 'magic_extract', 'rc_builder')}))
    command('profiles', 'python', ['{files:physical_prepare}', '--inputs', output + 'physical-inputs.json',
            '--workspace', output + 'profiles', '--base-rev', 'empty'])
    command('route', 'rustroad', ['--workspace', road, '--threads', '{threads}',
            '--max-expansions', '80000000', 'route', '--base-rev', 'rev3', '--scope', 'all',
            '--config', '{output}/frontend/physical/routing.json'], 43200)
    # Post-route electrical repair: equal-width resizes replayed through the
    # frontend's own final import/place/CTS arguments, then an ECO route on the
    # trial route. Every later step reads its final workspace (a file-for-file
    # copy of the trial when no resize is needed).
    by_id = {s['id']: s for s in front['steps']}

    def final_stage(step):
        args = list(by_id[step]['arguments'])
        require(by_id[step]['tool'] == 'rustroad' and args[:2] == ['--workspace', road],
                'frontend final recipe differs: ' + step)
        return args[2:]

    stage_import = final_stage('fe_final_pdk_import')
    require(stage_import.count('--netlist') == 1, 'frontend final import netlist')
    prects = stage_import[stage_import.index('--netlist') + 1]
    stage_import[stage_import.index('--netlist') + 1] = '@NETLIST@'
    postroute = output + 'postroute'
    render('postroute_inputs', 'postroute-inputs.json',
           dict(schema='rusteda.native-postroute-inputs.v1', top='ibex_sram_bench',
                trial=dict(workspace=road, revision='rev4'), final_workspace=postroute + '/road',
                prects_netlist=prects,
                recipe={'import': stage_import, 'place': final_stage('fe_final_place'),
                        'cts': final_stage('fe_final_cts'), 'route': steps[-1]['arguments'][2:],
                        'extract': ['--max-expansions', '8000000', 'extract', '--rc-profile',
                                    output + 'profiles/ihp-rc-nom.json', '--corner', 'nom', '--out', '@SPEF@',
                                    '--max-spef-mib', '256', '--extract-memory-mib', '3584',
                                    '--max-extract-shapes', '4000000', '--max-rc-nodes', '2000000',
                                    '--max-rc-elements', '30000000', '--max-coupling-caps', '10000000',
                                    '--max-extract-work', '100000000000', '--max-extract-candidates', '65536',
                                    '--min-coupling-ff', '0.1']},
                profiles=dict(sizing='{output}/frontend/profiles/sizing.json',
                              delay='{output}/frontend/profiles/delay.json',
                              cell_buses='{output}/frontend/profiles/cell-buses.json', cell_lef='{files:cell_lef}'),
                timing={key: input_id(key) for key in ('std_tt', 'ram_tt', 'std_ss', 'ram_ss', 'sdc')},
                tools={key: {'$tool': key} for key in ('rustroad', 'rusteda', 'ruststa')},
                policy=dict(near_limit_ratio=0.9, target_ratio=0.8, max_rounds=2, max_resizes=256),
                limits=dict(threads=front['scope']['threads'], deadline_seconds=5400)))
    command('postroute', 'python', ['{files:postroute_runner}', '--inputs', output + 'postroute-inputs.json',
            '--workspace', postroute, '--base-rev', 'empty'], 5460)
    road = postroute + '/road'
    antenna_content = dict(schema='rusteda.native-antenna-inputs.v1', design_workspace=road,
                initial_revision='rev4', tool={'$tool': 'rustroad'},
                limits=dict(threads=front['scope']['threads'], max_rounds=3,
                            max_selected_nets=100000, max_work=200000000,
                            max_expansions=8000000, deadline_seconds=1800))
    if antenna_grouping is not None:
        antenna_content.update(schema='rusteda.native-antenna-inputs.v2', grouping=antenna_grouping)
    render('antenna_inputs', 'antenna-inputs.json', antenna_content)
    command('antenna_repair', 'python', ['{files:antenna_runner}', '--inputs',
            output + 'antenna-inputs.json', '--workspace', output + 'antenna-repair',
            '--base-rev', 'empty'], 1860)
    if antenna_grouping is not None:
        render('antenna_verify_inputs', 'antenna-verification-inputs.json',
               dict(schema=('rusteda.antenna-v3-independent-verifier-inputs.v1' if antenna_session
                        else 'rusteda.antenna-v2-independent-verifier-inputs.v1'),
                    lineage=output_id('backend/antenna-repair/lineage.json'),
                    tool={'$tool': 'rustroad'}, source_manifest=input_id('antenna_source_manifest'),
                    design_workspace=road, evidence_kind='actual_native_artifacts',
                    **({'controller_inputs': output_id('backend/antenna-inputs.json')}
                       if antenna_session else {})))
        command('antenna_verify', 'python', ['{files:antenna_verifier}', '--inputs',
                output + 'antenna-verification-inputs.json', '--workspace',
                output + 'antenna-verification', '--base-rev', 'empty'], 180)
    for name, operation in [('inspect', 'inspect'), ('def', 'export-def'),
                            ('netlist', 'export-verilog')]:
        command(name, 'rustroad', ['--workspace', road, operation], 300)
    command('powered', 'rustroad', ['--workspace', road, 'export-verilog', '--include-power'], 300)
    command('gds', 'rustroad', ['--workspace', road, 'export-gds', '--cell-gds',
            output + 'profiles/combined-cells.gds', '--layer-map', output + 'profiles/gds-map.json',
            '--out', gds], 300)
    command('extract', 'rustroad', ['--workspace', road, '--max-expansions', '8000000',
            'extract', '--rc-profile', output + 'profiles/ihp-rc-nom.json', '--corner', 'nom',
            '--out', native_spef, '--max-spef-mib', '256', '--extract-memory-mib', '3584',
            '--max-extract-shapes', '4000000',
            '--max-rc-nodes', '2000000', '--max-rc-elements', '30000000',
            '--max-coupling-caps', '10000000', '--max-extract-work', '100000000000',
            '--max-extract-candidates', '65536', '--min-coupling-ff', '0.1'], 1800)
    render('rcx_inputs', 'openrcx-inputs.json',
           dict(schema='rusteda.reference-extraction-inputs.v1', top='ibex_sram_bench',
                files={'def': output_id('logs/be_def.stdout'),
                       'tech_lef': input_id('tech_lef'), 'cell_lef': input_id('std_lef'),
                       'ram_lef': input_id('ram_lef'), 'rules_nom': input_id('rules_nom')},
                tools={key: {'$tool': key} for key in ('appimage', 'gnu_time')},
                budgets=dict(threads=2, timeout_seconds=900, max_spef_bytes=128 << 20,
                             max_nets=100000, max_log_bytes=32 << 20)))
    command('openrcx', 'python', ['{files:reference_extract}', '--inputs', output + 'openrcx-inputs.json',
            '--workspace', output + 'openrcx', '--base-rev', 'empty'], 1000)
    render('timing_inputs', 'timing-inputs.json',
           dict(schema='rusteda.signoff-timing-inputs.v1', top='ibex_sram_bench',
                files={'netlist': output_id('logs/be_netlist.stdout'),
                       **{key: input_id(key) for key in ('sdc', 'std_tt', 'ram_tt', 'std_ss', 'ram_ss')},
                       'spef_native': output_id('backend/native.nom.spef'),
                       'spef_openrcx': output_id('backend/openrcx/nom.spef'),
                       'physical_lef': input_id('std_lef')},
                tools={key: {'$tool': key} for key in ('rusteda', 'ruststa', 'opensta', 'gnu_time')},
                # run09b: the routed SPEF's parasitics side table exceeds 64 MiB
                # (SIGXFSZ); overlay09/signoff_timing.py allows up to 256 MiB.
                budgets=dict(threads=2, timeout_seconds=180, max_input_bytes=128 << 20,
                             max_output_bytes=256 << 20, max_state_gib=2,
                             max_limit_rows=100000, max_checks=100000),
                scope=dict(mode='signoff', rc_corner='nom', cppr='off', precision='f64',
                           backend='cpu', clock_model='propagated', wire_model='spef_rc',
                           tolerance_ns=.001)))
    command('timing', 'python', ['{files:timing_runner}', '--inputs', output + 'timing-inputs.json',
            '--workspace', output + 'timing', '--base-rev', 'empty'], 1800)
    for table in ('main', 'antenna'):
        command(table, 'python', ['{files:drc_runner}', '--gds', gds, '--profile',
                '{files:drc_profile}', '--tool', '{tools:appimage}', '--table', table,
                '--workspace', output + table, '--base-rev', 'empty'], 1200)
    render('lvs_inputs', 'lvs-inputs.json',
           dict(schema='rusteda.device-lvs-inputs.v1', top='ibex_sram_bench', gds=gds,
                powered_netlist=powered, deck='{files:lvs_deck}',
                cdl=['{files:std_cdl}', '{files:ram_cdl}', '{files:filler_cdl}'],
                interfaces='{files:lvs_interfaces}', macro='RM_IHPSG13_1P_1024x32_c2_bm_bist',
                macro_count=2, macro_qualification='{files:sram_qualification}',
                tools={'appimage': '{tools:appimage}', 'yosys': '{tools:yosys}', 'time': '{tools:gnu_time}'},
                limits=dict(memory_mib=4096, swap_mib=512, deadline_seconds=1200, threads=2),
                expected_hashes={'gds': output_sha('backend/ibex_sram_bench.gds'),
                    'powered_netlist': output_sha('logs/be_powered.stdout'),
                    'deck': files['lvs_deck']['sha256'],
                    'macro_qualification': files['sram_qualification']['sha256'],
                    'interfaces': files['lvs_interfaces']['sha256']}))
    command('lvs', 'python', ['{files:lvs_runner}', '--inputs', output + 'lvs-inputs.json',
            '--workspace', output + 'lvs', '--base-rev', 'empty'], 1500)
    command('macro_contract', 'python', ['{files:macro_contract}', output + 'lvs/netlist.json'], 180)
    require(len(steps) <= 128 and len({step['id'] for step in steps}) == len(steps), 'step budget/identity')
    steps, stdout_artifacts = policy.declare_stdout_artifacts(steps)
    policy.validate_stdout_artifacts(steps, stdout_artifacts, front['scope'])
    artifacts = dict(gds='backend/ibex_sram_bench.gds', **{'def': 'logs/be_def.stdout'},
                     netlist_synth='frontend/mapped.v', netlist_final='logs/be_netlist.stdout',
                     netlist_powered_final='logs/be_powered.stdout', spef_native_nom='backend/native.nom.spef',
                     spef_openrcx_nom='backend/openrcx/nom.spef')
    gates = dict(drc='backend/main/summary.json', antenna='backend/antenna/summary.json',
                 lvs='backend/lvs/summary.json', timing='backend/timing/timing.json',
                 timing_accuracy='backend/timing/parity.json')
    outputs = dict(final_workspace='backend/postroute/road', trial_workspace=front['outputs']['final_workspace'],
                   postroute='backend/postroute/result.json', artifacts=artifacts,
                   available_gates=gates, macro_contract='logs/be_macro_contract.stdout',
                   stop_after='be_macro_contract')
    unverified = ['complete_flow', 'inputs_frozen', 'macros', 'synthesis_equivalence',
                  'final_equivalence', 'simulation', 'placement', 'cts', 'power_connectivity',
                  'routing', 'extraction', 'controls', 'benchmark']
    fragment = dict(schema='rusteda.native-backend-fragment.v1', files=files, tools=tools,
                    steps=steps, scope=front['scope'], outputs=outputs, artifacts=artifacts,
                    gates=gates, unverified=unverified, stdout_artifacts=stdout_artifacts)
    prefix = dict(schema='rusteda.ihp-flow.v1', name='fresh native physical/signoff prefix',
                  pdk='ihp-sg13g2', library='ez130_8t', corners=['tt', 'ss'], files=files, tools=tools,
                  steps=steps, scope=front['scope'], gates={}, artifacts=artifacts, stdout_artifacts=stdout_artifacts)
    if config.get('parallel_signoff', False):
        require(front['scope']['threads'] >= 6 and front['scope'].get('memory_mib') == 8192,
                'parallel signoff requires six physical CPUs and the matched 8192 MiB cgroup')
        groups = [policy.signoff_parallel_group()]
        policy.validate_parallel_groups(steps, groups, front['scope'])
        fragment['parallel_groups'] = groups
        prefix['parallel_groups'] = groups
    return fragment, prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock'
                for p in args.workspace.iterdir()), 'REVISION_CONFLICT')
        fragment, prefix = build(read(args.inputs))
        for name, value in (('fragment.json', fragment), ('prefix-manifest.json', prefix)):
            (args.workspace / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
        (args.workspace / 'HEAD').write_text('rev1\n')
        print(json.dumps(fragment['outputs']))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        raise SystemExit(2)

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

The steps are composed for a native PDK profile (rusteda.native-pdk-profile.v1,
integration/ibex_pdks/NATIVE.md): --profile FILE, the one the frontend fragment
was composed for. Without it the profile is the IHP SG13G2/EZ130 one, whose
prefix is the published rusteda.ihp-flow.v1 recipe, byte for byte. Another
profile gives the cell and macro GDS libraries, the layer map and RC profile
sources of `rustroad physical-inputs` (v3 inputs, RC profile rc-nom.json), the
pinned RC calibration, the corners and Liberty groups of the post-route
repair, and which referee steps follow the final exports (none unless the
profile lists them: its shared gates judge the layout afterwards).
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

# Final-stream representation of `rustroad export-gds` (RustROAD 3a2ef82 or later): each net's
# metal as an exact cover of maximal rectangles, vias written flat with it. The geometry equals
# the default stream's (KLayout XOR empty on every layer); the shared foundry main/antenna DRC
# and LVS run faster on it (RustROAD docs/gds-export.md, "Downstream check cost"). Every export
# whose bytes must equal the final GDS (qualify_native.py's early export, the foundry-first
# initial export copied from be_gds) passes the same options. Both post-route orders write the
# final GDS with it.
GDS_STYLE = ('--merge-shapes', 'rectangles', '--vias', 'flat')


# The backend collateral of the published IHP recipe (profile flow_schema rusteda.ihp-flow.v1).
REQUIRED = {'physical_prepare', 'rc_builder', 'reference_extract', 'timing_runner', 'postroute_runner',
            'lvs_runner', 'drc_runner', 'macro_contract', 'std_gds', 'ram_gds',
            'layer_map', 'rules_nom', 'magic_tech', 'magic_extract', 'ram_lef',
            'drc_profile', 'drc_deck', 'antenna_deck', 'lvs_deck', 'std_cdl',
            'ram_cdl', 'filler_cdl', 'sram_qualification', 'lvs_interfaces',
            'antenna_runner', 'antenna_fields', 'contract'}
# The referee steps after the final exports, in manifest order, and the pinned files each needs.
REFEREE_STEPS = ('openrcx', 'timing', 'main', 'antenna', 'lvs', 'macro_contract')
REFEREE_FILES = dict(openrcx={'reference_extract', 'ram_lef'}, timing={'timing_runner'},
                     main={'drc_runner', 'drc_profile', 'drc_deck'}, antenna={'drc_runner', 'drc_profile', 'antenna_deck'},
                     lvs={'lvs_runner', 'lvs_deck', 'std_cdl', 'ram_cdl', 'filler_cdl', 'sram_qualification',
                          'lvs_interfaces'},
                     macro_contract={'macro_contract'})
RC_BUILDERS = ('ihp', 'sky130', 'gf180')
V2_FILES = {'antenna_group_policy', 'antenna_legacy', 'antenna_verifier', 'antenna_source_manifest'}
# Post-route repair order. screen_after_extract (default): antenna repair and
# the one full native extraction run on the trial route, and be_postroute
# screens DRV on that SPEF, then materializes the final workspace (hard links
# when clean; ECO rebuild, antenna repair and extraction again otherwise).
# extract_before_antenna: the earlier order, in which be_postroute extracts
# the pre-antenna trial itself and be_extract extracts the final layout again
# (kept for the foundry-first capture/reuse protocol, which is bound to it).
SCREEN_ORDER, LEGACY_ORDER = 'screen_after_extract', 'extract_before_antenna'
POSTROUTE_ORDERS = (SCREEN_ORDER, LEGACY_ORDER)
# Who runs the four backend design steps (be_profiles, be_antenna_repair,
# be_antenna_verify, be_postroute). 'python' (default): this recipe's helpers
# native_physical_inputs.py, antenna_session/native_antenna_groups.py and
# verify.py, native_postroute.py; the full qualification composer
# (native_flow.py, qualify_native.py) and the foundry-first protocol bind those
# and nothing else. 'native': RustROAD's own flow-step commands physical-inputs,
# antenna-session, verify-antenna-lineage and postroute-repair (RustROAD
# docs/flow-steps.md), which write the same files; no Python helper is pinned
# or run for these steps. Native steps exist for the screen order with the
# grouped single-process antenna session only.
PYTHON_STEPS, NATIVE_STEPS = 'python', 'native'
BACKEND_STEPS = (PYTHON_STEPS, NATIVE_STEPS)
PYTHON_STEP_FILES = {'physical_prepare', 'rc_builder', 'postroute_runner', 'antenna_runner', 'antenna_fields'}
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


def frontend_module():
    """The frontend composer beside this file: it owns the PDK profile reader."""
    path = Path(__file__).resolve().with_name('native_frontend.py')
    spec = importlib.util.spec_from_file_location('native_frontend_profile', path)
    require(spec is not None and spec.loader is not None, 'frontend composer loader')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackendProfile:
    """What the backend reads of a PDK profile beyond the frontend's view."""

    def __init__(self, profile):
        data = profile.data
        self.profile, self.legacy = profile, profile.legacy
        self.top, self.macros = profile.top, profile.macros
        # GDS libraries to bundle, in order: the cell libraries, then every macro master.
        cells = data['gds']['cells']
        require(isinstance(cells, list) and cells, 'PDK profile gds.cells')
        self.gds_keys = ['std_gds' if k == 0 else 'std%d_gds' % (k + 1) for k in range(len(cells))]
        self.gds_keys += [m['id'] + '_gds' for m in self.macros]
        layer_map = data['gds_layer_map']
        require(isinstance(layer_map, dict) and (set(layer_map) == {'klayout', 'layers'} or set(layer_map) == {'tracked'}),
                'PDK profile gds_layer_map: klayout with layers, or tracked')
        self.projected = 'klayout' in layer_map
        self.layers = layer_map.get('layers', [])
        require(all(isinstance(x, str) and x for x in self.layers) and (not self.projected or self.layers),
                'PDK profile gds_layer_map.layers')
        rc = data['rc']
        self.rc_built = 'profile' not in rc
        if self.rc_built:
            require(set(rc) - {'calibration'} == {'builder', 'resistance_model', 'rules', 'magic_tech', 'magic_extract'}
                    and rc['builder'] in RC_BUILDERS and rc['resistance_model'] in ('magic', 'openrcx-pdk')
                    and isinstance(rc['rules'], dict) and set(rc['rules']) == {'path', 'sha256'},
                    'PDK profile rc: builder, resistance_model, rules with sha256, magic_tech, magic_extract')
            self.rc = dict(builder=rc['builder'], resistance_model=rc['resistance_model'])
            # The pinned RC calibration: the nominal rules file must have these bytes.
            self.rc_calibration_sha256 = rc['rules']['sha256']
            # The name the IHP recipe's own contract gives this calibration.
            self.rc_calibration_name = rc.get('calibration')
            require(self.rc_calibration_name is None or isinstance(self.rc_calibration_name, str),
                    'PDK profile rc.calibration')
            self.magic_extract = rc['magic_extract'] is not None
        else:
            require(set(rc) == {'profile'}, 'PDK profile rc: a tracked profile takes no builder')
            self.rc, self.rc_calibration_sha256, self.rc_calibration_name, self.magic_extract = {}, None, None, False
        steps = data.get('referee_steps', [])
        require(isinstance(steps, list) and tuple(steps) == REFEREE_STEPS[:len(steps)],
                'PDK profile referee_steps: a prefix of ' + ', '.join(REFEREE_STEPS))
        self.referee_steps = tuple(steps)
        require(not self.legacy or (self.referee_steps == REFEREE_STEPS and self.gds_keys == ['std_gds', 'ram_gds']
                                    and self.projected and self.rc_built and self.magic_extract
                                    and self.rc_calibration_name and len(self.macros) == 1),
                'the rusteda.ihp-flow.v1 recipe has one cell and one macro GDS, a projected layer map, '
                'a built RC profile and all six referee steps')
        require(not self.referee_steps or len(self.macros) == 1,
                'the referee steps check exactly one macro master')

    def physical_keys(self):
        """The pinned inputs of `rustroad physical-inputs`, in its record order."""
        keys = [*self.gds_keys, 'layer_map' if self.projected else 'gds_map', 'tech_lef']
        if self.rc_built:
            keys += ['rules_nom', 'magic_tech'] + (['magic_extract'] if self.magic_extract else [])
        else:
            keys.append('rc_profile')
        return keys

    def required(self):
        """The backend files a profile's native steps pin."""
        keys = set(self.physical_keys()) - {'tech_lef'} | {'antenna_group_policy', 'antenna_session_policy'}
        for step in self.referee_steps:
            keys |= REFEREE_FILES[step]
        return keys

    def rc_profile_name(self):
        return 'ihp-rc-nom.json' if self.legacy else 'rc-nom.json'


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


def group_policy(files):
    """The pinned grouped-session policy: exactly the qualified integer budgets."""
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
    return policy


def native_antenna(files):
    """Native steps: the grouped policy and the single-process session policy are
    pinned; the session and its verifier are commands of the pinned rustroad."""
    require('antenna_group_policy' in files and 'antenna_session_policy' in files,
            'native backend steps require the grouped single-process antenna session policies')
    record = files['antenna_group_policy']
    require(identity(Path(record['path'])) == record, 'v2 source changed: antenna_group_policy')
    policy = group_policy(files)
    require(single_process_antenna(files), 'native backend steps require the single-process antenna session')
    return policy


def grouped_antenna(files):
    """An explicit policy opts in; all imported Python siblings are pinned."""
    if 'antenna_group_policy' not in files:
        require(not (V2_FILES & set(files)), 'v2 antenna sources require explicit group policy')
        return None
    require(V2_FILES <= set(files), 'incomplete explicit v2 antenna source pins')
    for role in V2_FILES | {'antenna_runner', 'antenna_fields'}:
        require(identity(Path(files[role]['path'])) == files[role], 'v2 source changed: ' + role)
    policy = group_policy(files)
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


def build(config, profile=None):
    frontend = frontend_module()
    profile = profile if profile is not None else frontend.load_profile()
    pdk = BackendProfile(profile)
    required = {'schema', 'frontend_fragment', 'files', 'tools', 'pdk_root'}
    require(required <= set(config)
            and not set(config) - required - {'parallel_signoff', 'postroute_order', 'backend_steps'}
            and config['schema'] == 'rusteda.native-backend-inputs.v1', 'backend config schema')
    require(type(config.get('parallel_signoff', False)) is bool, 'parallel_signoff must be explicit boolean')
    order = config.get('postroute_order', SCREEN_ORDER)
    require(order in POSTROUTE_ORDERS, 'postroute_order must be one of ' + ', '.join(POSTROUTE_ORDERS))
    backend_steps = config.get('backend_steps', PYTHON_STEPS)
    require(backend_steps in BACKEND_STEPS, 'backend_steps must be one of ' + ', '.join(BACKEND_STEPS))
    native = backend_steps == NATIVE_STEPS
    require(not native or order == SCREEN_ORDER, 'native backend steps require the screen_after_extract order')
    require(native or pdk.legacy, 'the Python step helpers are the IHP recipe\'s: this profile needs backend_steps native')
    record = config['frontend_fragment']
    require(identity(Path(record['path'])) == record, 'frontend recipe changed')
    front = read(Path(record['path']))
    require(front['schema'] == 'rusteda.native-frontend-fragment.v1', 'frontend recipe schema')
    require((REQUIRED - (PYTHON_STEP_FILES if native else set()) if pdk.legacy else pdk.required()) <= set(config['files'])
            and len(config['files']) <= 128, 'backend collateral incomplete')
    require(pdk.legacy or set(config['files']) == pdk.required(), 'explicit backend file set required')
    require(not native or not (PYTHON_STEP_FILES | V2_FILES - {'antenna_group_policy'}) & set(config['files']),
            'native backend steps pin no Python step helper')
    require(set(config['tools']) == ({'appimage', 'opensta', 'yosys'} if pdk.referee_steps else set()),
            'reference tool set')
    files, tools = dict(front['files']), dict(front['tools'])
    require(pdk.legacy or set(front['files']) == profile.frontend_file_keys(),
            'frontend recipe was composed for another PDK profile')
    policy = capture_policy(files['producer'])
    profile.validate_stdout_artifacts(policy, front['steps'], front.get('stdout_artifacts'), front['scope'])
    for source, target in ((config['files'], files), (config['tools'], tools)):
        for key, item in source.items():
            require(key not in target or target[key] == item, 'conflicting input identity: ' + key)
            require(identity(Path(item['path'])) == item, 'backend input changed: ' + key)
            target[key] = item
    require(len(files) <= 256 and len(tools) <= 32, 'aggregate input budget')
    if native:
        antenna_grouping, antenna_session = native_antenna(files), True
    else:
        antenna_grouping = grouped_antenna(files)
        antenna_session = single_process_antenna(files)
    require(not antenna_session or antenna_grouping is not None,
            'antenna session requires the explicit grouped policy')
    # The pinned RC calibration. The IHP recipe's contract (files:contract of the prepared design)
    # names the calibration and its SHA-256; the profile states which name that contract must
    # give (rc.calibration). Any other profile pins the SHA-256 of its rules file itself.
    if pdk.legacy:
        contract = read(Path(files['contract']['path']))
        require(contract['common']['rc_calibration_rules'] == pdk.rc_calibration_name
                and files['rules_nom']['sha256'] == contract['common']['rc_calibration_sha256'],
                'native backend must use the pinned original PDK default nominal calibration')
    else:
        require(not pdk.rc_built or files['rules_nom']['sha256'] == pdk.rc_calibration_sha256,
                'native backend must use the pinned original PDK default nominal calibration')
    steps = list(front['steps'])
    output = '{output}/backend/'
    road = '{output}/' + front['outputs']['final_workspace']
    final_def = '{output}/logs/be_def.stdout'
    final_netlist = '{output}/logs/be_netlist.stdout'
    powered = '{output}/logs/be_powered.stdout'
    gds = output + pdk.top + '.gds'
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

    physical_keys = ('std_gds', 'ram_gds', 'layer_map', 'tech_lef', 'rules_nom', 'magic_tech', 'magic_extract')
    if not pdk.legacy:
        # Any PDK profile: N GDS libraries, a projected or tracked layer map, a built or tracked
        # RC profile; files are identified by their pinned bytes, never by name.
        render('physical_inputs', 'physical-inputs.json',
               dict(schema='rusteda.native-physical-inputs.v3', pdk=profile.pdk, library=profile.library,
                    cell_gds=list(pdk.gds_keys), layers=list(pdk.layers) if pdk.projected else [], rc=dict(pdk.rc),
                    files={key: input_id(key) for key in pdk.physical_keys()}))
        command('profiles', 'rustroad', ['physical-inputs', '--inputs', output + 'physical-inputs.json',
                '--out-dir', output + 'profiles'])
    elif native:
        # The v2 inputs are v1 without the pinned Python profile builder.
        render('physical_inputs', 'physical-inputs.json',
               dict(schema='rusteda.native-physical-inputs.v2', pdk_root=config['pdk_root'],
                    files={key: input_id(key) for key in physical_keys}))
        command('profiles', 'rustroad', ['physical-inputs', '--inputs', output + 'physical-inputs.json',
                '--out-dir', output + 'profiles'])
    else:
        render('physical_inputs', 'physical-inputs.json',
               dict(schema='rusteda.native-physical-inputs.v1', pdk_root=config['pdk_root'],
                    files={key: input_id(key) for key in (*physical_keys, 'rc_builder')}))
        command('profiles', 'python', ['{files:physical_prepare}', '--inputs', output + 'physical-inputs.json',
                '--workspace', output + 'profiles', '--base-rev', 'empty'])
    command('route', 'rustroad', ['--workspace', road, '--threads', '{threads}',
            '--max-expansions', '80000000', 'route', '--base-rev', 'rev3', '--scope', 'all',
            '--config', '{output}/frontend/physical/routing.json'], 43200)
    # Post-route electrical repair: equal-width resizes replayed through the
    # frontend's own final import/place/CTS arguments, then an ECO route on the
    # screened route. Every final export reads the runner's final workspace.
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
    route_args = steps[-1]['arguments'][2:]
    # --threads is a global option: it must precede the subcommand
    # (foundry_first/protocol.py), and it sizes extraction's strip pool like
    # the route step's. One template serves be_extract and every extraction
    # the post-route runner performs, so their arguments cannot drift apart.
    extract_args = ['--threads', '{threads}', '--max-expansions', '8000000', 'extract', '--rc-profile',
                    output + 'profiles/' + pdk.rc_profile_name(), '--corner', 'nom', '--out', '@SPEF@',
                    '--max-spef-mib', '256', '--extract-memory-mib', '3584',
                    '--max-extract-shapes', '4000000', '--max-rc-nodes', '2000000',
                    '--max-rc-elements', '30000000', '--max-coupling-caps', '10000000',
                    '--max-extract-work', '100000000000', '--max-extract-candidates', '65536',
                    '--min-coupling-ff', '0.1']
    def postroute_content(schema, tools, **extra):
        # Key order is the rendered JSON order; the earlier order's document
        # keeps its original layout byte for byte.
        content = dict(schema=schema, top=pdk.top, trial=dict(workspace=road, revision='rev4'),
                       final_workspace=final_road)
        if 'final_spef' in extra:
            content['final_spef'] = extra.pop('final_spef')
        content.update(prects_netlist=prects,
                       recipe={'import': stage_import, 'place': final_stage('fe_final_place'),
                               'cts': final_stage('fe_final_cts'), 'route': route_args,
                               'extract': list(extract_args)})
        content.update(extra)
        content.update(profiles=dict(sizing='{output}/frontend/profiles/sizing.json',
                                     delay='{output}/frontend/profiles/delay.json',
                                     cell_buses='{output}/frontend/profiles/cell-buses.json',
                                     cell_lef='{files:cell_lef}'),
                       timing={key: input_id(key) for key in (*timing_libraries, 'sdc')},
                       tools={key: {'$tool': key} for key in tools},
                       policy=dict(near_limit_ratio=0.9, target_ratio=0.8, max_rounds=2, max_resizes=256),
                       limits=dict(threads=front['scope']['threads'], deadline_seconds=5400))
        if not pdk.legacy:
            # The PDK identity of the repair: sizing label, the two corners, the Liberty groups
            # and the database unit of the workspaces (the floorplan's).
            content.update(profile=dict(pdk=profile.pdk, corners=list(profile.corners), groups=list(profile.groups),
                                        dbu_per_um=units_per_micron))
        return content

    # Liberty files by corner, each corner's groups in order (the IHP recipe: std_tt, ram_tt,
    # std_ss, ram_ss).
    timing_libraries = [g + '_' + c for c in profile.corners for g in profile.groups]
    units_per_micron = None
    if not pdk.legacy:
        require(len(profile.corners) == 2, 'the post-route repair policy needs exactly two corners')
        units_per_micron = read(Path(files['floorplan']['path']))['units_per_micron']
        require(type(units_per_micron) is int and 1 <= units_per_micron <= 100000, 'floorplan units_per_micron')
    final_road = postroute + '/road'
    antenna_limits = dict(threads=front['scope']['threads'], max_rounds=3,
                          max_selected_nets=100000, max_work=200000000,
                          max_expansions=8000000, deadline_seconds=1800)

    def antenna_steps(workspace):
        antenna_content = dict(schema='rusteda.native-antenna-inputs.v1', design_workspace=workspace,
                               initial_revision='rev4', tool={'$tool': 'rustroad'}, limits=antenna_limits)
        if antenna_grouping is not None:
            antenna_content.update(schema='rusteda.native-antenna-inputs.v2', grouping=antenna_grouping)
        render('antenna_inputs', 'antenna-inputs.json', antenna_content)
        if native:
            # The same v2 session inputs; the verifier inputs (v2) are v1 without
            # the manifest of frozen Python sources.
            command('antenna_repair', 'rustroad', ['antenna-session', '--inputs',
                    output + 'antenna-inputs.json', '--out-dir', output + 'antenna-repair'], 1860)
            render('antenna_verify_inputs', 'antenna-verification-inputs.json',
                   dict(schema='rusteda.antenna-v3-independent-verifier-inputs.v2',
                        lineage=output_id('backend/antenna-repair/lineage.json'),
                        tool={'$tool': 'rustroad'}, design_workspace=workspace,
                        evidence_kind='actual_native_artifacts',
                        controller_inputs=output_id('backend/antenna-inputs.json')))
            command('antenna_verify', 'rustroad', ['verify-antenna-lineage', '--inputs',
                    output + 'antenna-verification-inputs.json', '--out-dir',
                    output + 'antenna-verification'], 180)
            return
        command('antenna_repair', 'python', ['{files:antenna_runner}', '--inputs',
                output + 'antenna-inputs.json', '--workspace', output + 'antenna-repair',
                '--base-rev', 'empty'], 1860)
        if antenna_grouping is not None:
            render('antenna_verify_inputs', 'antenna-verification-inputs.json',
                   dict(schema=('rusteda.antenna-v3-independent-verifier-inputs.v1' if antenna_session
                            else 'rusteda.antenna-v2-independent-verifier-inputs.v1'),
                        lineage=output_id('backend/antenna-repair/lineage.json'),
                        tool={'$tool': 'rustroad'}, source_manifest=input_id('antenna_source_manifest'),
                        design_workspace=workspace, evidence_kind='actual_native_artifacts',
                        **({'controller_inputs': output_id('backend/antenna-inputs.json')}
                           if antenna_session else {})))
            command('antenna_verify', 'python', ['{files:antenna_verifier}', '--inputs',
                    output + 'antenna-verification-inputs.json', '--workspace',
                    output + 'antenna-verification', '--base-rev', 'empty'], 180)

    def extract_step(workspace, spef):
        command('extract', 'rustroad', ['--workspace', workspace,
                *[spef if a == '@SPEF@' else a for a in extract_args]], 1800)

    if order == SCREEN_ORDER:
        # be_route -> antenna repair (+ verify) -> be_extract on the trial ->
        # be_postroute screens that SPEF -> final exports. The trial SPEF keeps
        # a distinct name: only be_postroute publishes the final SPEF path.
        trial_spef = output + 'trial.native.nom.spef'
        antenna_steps(road)
        extract_step(road, trial_spef)
        grouped = antenna_grouping is not None
        screen = dict(spef=output_id('backend/trial.native.nom.spef'),
                      extract_report=output_id('logs/be_extract.stdout'),
                      extract_command=output_id('logs/be_extract.command.json'))
        if native:
            # v3 is v2 without the Python tool and helper pins: an ECO rebuild is
            # antenna-repaired and verified by the same pinned rustroad.
            render('postroute_inputs', 'postroute-inputs.json', postroute_content(
                'rusteda.native-postroute-inputs.v3', ('rustroad', 'rusteda', 'ruststa'),
                final_spef=native_spef, screen=screen,
                antenna=dict(inputs=output_id('backend/antenna-inputs.json'),
                             lineage=output_id('backend/antenna-repair/lineage.json'),
                             verifier_inputs=output_id('backend/antenna-verification-inputs.json'),
                             verification=output_id('backend/antenna-verification/result.json'))))
            command('postroute', 'rustroad', ['postroute-repair', '--inputs', output + 'postroute-inputs.json',
                    '--out-dir', postroute], 5460)
        else:
            render('postroute_inputs', 'postroute-inputs.json', postroute_content(
                'rusteda.native-postroute-inputs.v2', ('rustroad', 'rusteda', 'ruststa', 'python'),
                final_spef=native_spef, screen=screen,
                antenna=dict(inputs=output_id('backend/antenna-inputs.json'),
                             lineage=output_id('backend/antenna-repair/lineage.json'),
                             runner=input_id('antenna_runner'),
                             verifier=input_id('antenna_verifier') if grouped else None,
                             verifier_inputs=output_id('backend/antenna-verification-inputs.json') if grouped else None,
                             verification=output_id('backend/antenna-verification/result.json') if grouped else None)))
            command('postroute', 'python', ['{files:postroute_runner}', '--inputs', output + 'postroute-inputs.json',
                    '--workspace', postroute, '--base-rev', 'empty'], 5460)
        road = final_road
        for name, operation in [('inspect', 'inspect'), ('def', 'export-def'),
                                ('netlist', 'export-verilog')]:
            command(name, 'rustroad', ['--workspace', road, operation], 300)
        command('powered', 'rustroad', ['--workspace', road, 'export-verilog', '--include-power'], 300)
        command('gds', 'rustroad', ['--workspace', road, 'export-gds', '--cell-gds',
                output + 'profiles/combined-cells.gds', '--layer-map', output + 'profiles/gds-map.json',
                '--out', gds, *GDS_STYLE], 300)
    else:
        # Earlier order: be_postroute extracts the pre-antenna trial itself and
        # copies it file for file; antenna repair then edits that copy and
        # be_extract extracts the final layout again.
        render('postroute_inputs', 'postroute-inputs.json', postroute_content(
            'rusteda.native-postroute-inputs.v1', ('rustroad', 'rusteda', 'ruststa')))
        command('postroute', 'python', ['{files:postroute_runner}', '--inputs', output + 'postroute-inputs.json',
                '--workspace', postroute, '--base-rev', 'empty'], 5460)
        road = final_road
        antenna_steps(road)
        for name, operation in [('inspect', 'inspect'), ('def', 'export-def'),
                                ('netlist', 'export-verilog')]:
            command(name, 'rustroad', ['--workspace', road, operation], 300)
        command('powered', 'rustroad', ['--workspace', road, 'export-verilog', '--include-power'], 300)
        command('gds', 'rustroad', ['--workspace', road, 'export-gds', '--cell-gds',
                output + 'profiles/combined-cells.gds', '--layer-map', output + 'profiles/gds-map.json',
                '--out', gds, *GDS_STYLE], 300)
        extract_step(road, native_spef)
    referee = set(pdk.referee_steps)
    macro = pdk.macros[0] if pdk.macros else None
    if 'openrcx' in referee:
        render('rcx_inputs', 'openrcx-inputs.json',
               dict(schema='rusteda.reference-extraction-inputs.v1', top=pdk.top,
                    files={'def': output_id('logs/be_def.stdout'),
                           'tech_lef': input_id('tech_lef'), 'cell_lef': input_id('std_lef'),
                           'ram_lef': input_id('ram_lef'), 'rules_nom': input_id('rules_nom')},
                    tools={key: {'$tool': key} for key in ('appimage', 'gnu_time')},
                    budgets=dict(threads=2, timeout_seconds=900, max_spef_bytes=128 << 20,
                                 max_nets=100000, max_log_bytes=32 << 20)))
        command('openrcx', 'python', ['{files:reference_extract}', '--inputs', output + 'openrcx-inputs.json',
                '--workspace', output + 'openrcx', '--base-rev', 'empty'], 1000)
    if 'timing' in referee:
        render('timing_inputs', 'timing-inputs.json',
               dict(schema='rusteda.signoff-timing-inputs.v1', top=pdk.top,
                    files={'netlist': output_id('logs/be_netlist.stdout'),
                           **{key: input_id(key) for key in ('sdc', *timing_libraries)},
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
        if table in referee:
            command(table, 'python', ['{files:drc_runner}', '--gds', gds, '--profile',
                    '{files:drc_profile}', '--tool', '{tools:appimage}', '--table', table,
                    '--workspace', output + table, '--base-rev', 'empty'], 1200)
    if 'lvs' in referee:
        render('lvs_inputs', 'lvs-inputs.json',
               dict(schema='rusteda.device-lvs-inputs.v1', top=pdk.top, gds=gds,
                    powered_netlist=powered, deck='{files:lvs_deck}',
                    cdl=['{files:std_cdl}', '{files:ram_cdl}', '{files:filler_cdl}'],
                    interfaces='{files:lvs_interfaces}', macro=macro['master'],
                    macro_count=len(macro['instances']), macro_qualification='{files:sram_qualification}',
                    tools={'appimage': '{tools:appimage}', 'yosys': '{tools:yosys}', 'time': '{tools:gnu_time}'},
                    limits=dict(memory_mib=4096, swap_mib=512, deadline_seconds=1200, threads=2),
                    expected_hashes={'gds': output_sha('backend/' + pdk.top + '.gds'),
                        'powered_netlist': output_sha('logs/be_powered.stdout'),
                        'deck': files['lvs_deck']['sha256'],
                        'macro_qualification': files['sram_qualification']['sha256'],
                        'interfaces': files['lvs_interfaces']['sha256']}))
        command('lvs', 'python', ['{files:lvs_runner}', '--inputs', output + 'lvs-inputs.json',
                '--workspace', output + 'lvs', '--base-rev', 'empty'], 1500)
    if 'macro_contract' in referee:
        command('macro_contract', 'python', ['{files:macro_contract}', output + 'lvs/netlist.json'], 180)
    require(len(steps) <= 128 and len({step['id'] for step in steps}) == len(steps), 'step budget/identity')
    steps, stdout_artifacts = profile.declare_stdout_artifacts(policy, steps)
    profile.validate_stdout_artifacts(policy, steps, stdout_artifacts, front['scope'])
    artifacts = dict(gds='backend/' + pdk.top + '.gds', **{'def': 'logs/be_def.stdout'},
                     netlist_synth='frontend/mapped.v', netlist_final='logs/be_netlist.stdout',
                     netlist_powered_final='logs/be_powered.stdout', spef_native_nom='backend/native.nom.spef')
    if 'openrcx' in referee:
        artifacts.update(spef_openrcx_nom='backend/openrcx/nom.spef')
    # The gate reports of the referee steps this prefix runs (the IHP recipe: all five).
    gates = {name: path for step, name, path in (
        ('main', 'drc', 'backend/main/summary.json'), ('antenna', 'antenna', 'backend/antenna/summary.json'),
        ('lvs', 'lvs', 'backend/lvs/summary.json'), ('timing', 'timing', 'backend/timing/timing.json'),
        ('timing', 'timing_accuracy', 'backend/timing/parity.json')) if step in referee}
    outputs = dict(final_workspace='backend/postroute/road', trial_workspace=front['outputs']['final_workspace'],
                   postroute='backend/postroute/result.json', artifacts=artifacts,
                   available_gates=gates)
    if 'macro_contract' in referee:
        outputs.update(macro_contract='logs/be_macro_contract.stdout')
    outputs.update(stop_after=steps[-1]['id'])
    unverified = ['complete_flow', 'inputs_frozen', 'macros', 'synthesis_equivalence',
                  'final_equivalence', 'simulation', 'placement', 'cts', 'power_connectivity',
                  'routing', 'extraction', 'controls', 'benchmark']
    fragment = dict(schema='rusteda.native-backend-fragment.v1', files=files, tools=tools,
                    steps=steps, scope=front['scope'], outputs=outputs, artifacts=artifacts,
                    gates=gates, unverified=unverified, stdout_artifacts=stdout_artifacts)
    if native:
        # Marks the fragment for the qualification composer, which binds the Python steps.
        fragment['backend_steps'] = NATIVE_STEPS
    prefix = dict(schema=profile.flow_schema,
                  name='fresh native physical/signoff prefix' if pdk.legacy
                  else 'fresh native physical prefix (' + profile.data['name'] + ')',
                  **profile.manifest_identity(), files=files, tools=tools,
                  steps=steps, scope=front['scope'], gates={}, artifacts=artifacts, stdout_artifacts=stdout_artifacts)
    # The frontend's own groups (native_frontend.frontend_parallel_group on a four-CPU allocation)
    # cover only its steps, which this prefix keeps in order; the foundry-check group follows.
    groups = list(front.get('parallel_groups', []))
    require(all(tag.startswith('fe_') for group in groups for tag in group['steps']),
            'frontend parallel groups must stay inside the frontend prefix')
    if config.get('parallel_signoff', False):
        require({'main', 'antenna', 'lvs'} <= referee, 'parallel signoff needs the main, antenna and LVS referee steps')
        require(front['scope']['threads'] >= 4 and front['scope'].get('memory_mib') == 8192,
                'parallel signoff requires four physical CPUs and the matched 8192 MiB cgroup')
        groups.append(policy.signoff_parallel_group(front['scope']['threads']))
    if groups:
        policy.validate_parallel_groups(steps, groups, front['scope'])
        fragment['parallel_groups'] = groups
        prefix['parallel_groups'] = groups
    return fragment, prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--base-rev', required=True)
    parser.add_argument('--profile', type=Path,
                        help='native PDK profile (profile.json); default: the IHP SG13G2/EZ130 profile')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock'
                for p in args.workspace.iterdir()), 'REVISION_CONFLICT')
        fragment, prefix = build(read(args.inputs), frontend_module().load_profile(args.profile))
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

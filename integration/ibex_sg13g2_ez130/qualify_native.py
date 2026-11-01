#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Normalize existing independent evidence into the native flow's final gates.

This program implements no DRC, LVS, equivalence or timing oracle. Every such
verdict must already exist for these exact current-run artifacts. Geometry,
library, tool and report identities are checked before writing derived gates.

Memory estimate <384 MiB: <=16 MiB/report, <=128 MiB DEF, <=256 input records;
large native report/IR files are stream-hashed and only their <=64 KiB metadata
prefix is decoded. At most3 antenna attempts,100k selected nets and16 commands;
64 KiB line cap, 500k DEF component cap, no graph/path materialization. The
caller supplies the whole-worker cgroup; this controller has a 512 MiB limit.
"""
from __future__ import annotations

import argparse
import codecs
import fcntl
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import resource
import sys
import types
from decimal import Decimal


DERIVED = {'inputs_frozen', 'macros', 'placement', 'cts', 'power_connectivity',
           'routing', 'extraction', 'controls'}
REPORTS = {'drc', 'antenna', 'lvs', 'timing', 'timing_accuracy',
           'synthesis_equivalence', 'final_equivalence', 'simulation'}
CONTRACT_SHA256 = 'a58f53feb169724d987f774c3143083eee373806df251eb4164ca1ec82566ed8'
CONTRACT_BYTES = 5666
TAP_CONTRACT_SHA256 = '96d227ad2aa23b9b41d853c836a3e9c6fbfe39ec07c02fc64f343418ddc64282'
TAP_SCHEMA = 'rusteda.native-foundry-first-tap-qualification-inputs.v2'
TAP_ROLES = {'tap_coverage_worker', 'tap_coverage_runner', 'tap_latchup_runner',
             'tap_latchup_common', 'tap_latchup_deck', 'tap_guard', 'tap_policy'}
TAP_POLICY = dict(schema='rusteda.private-staggered-tap-policy.v1',
    native_max_clear_gap_dbu=30000, nominal_phase_dbu=15000,
    foundry_diffusion_tie_max_um=20, execution='serial_after_original_signoff_group',
    certificate_scope='NAME_excluded_SRAM_targets_full_well_domains',
    original_sixteen_gates='unchanged_required')
# Opt-in schedule: maximal starts on the foundry-first GDS beside main/antenna. It
# only counts when that file is byte-identical to the final GDS; a repaired layout
# fails review and must rerun serially, so no verdict crosses a layout change.
TAP_OVERLAP_EXECUTION = 'overlap_foundry_first_same_immutable_gds'
TAP_OVERLAP_ALL_EXECUTION = 'overlap_all_same_immutable_gds'
TAP_EARLY_EXECUTION = 'early_maximal_same_immutable_gds'
TAP_INITIAL_GDS = 'backend/foundry-first/initial.gds'
TAP_EARLY_GDS = 'backend/tap-early.gds'
TAP_LIMITS = dict(cells=16384, hierarchy_edges=250000, instance_visits=4000000,
    input_shapes=2000000, vertices=8000000, shape_visits=8000000,
    vertex_visits=32000000, domains=4096, anchors=250000, bin_entries=1000000,
    candidates=4096, boxes=16000000, distance_checks=200000000, pending=64, depth=32)
TAP_PATHS = dict(latchup='backend/latchup/summary.json',
    coverage_config='backend/tap-coverage-inputs.json',
    coverage_controller='backend/tap-coverage/controller.json',
    coverage_report='backend/tap-coverage/summary.json',
    routing_audit='frontend/physical/routing-writer-audit.json',
    latchup_execution='backend/latchup-execution/result.json',
    coverage_execution='backend/tap-coverage-execution/result.json')
TAP_WORKER = ('98e1044bad21adc710700da0b53236876949adeecdfd04adb01eba67cbf33b47', 18682)
RC_RULE_KEY = 'ihp/libs.tech/librelane/openrcx/IHP_rcx_patterns.rules'
RC_RULE_SHA256 = 'ffda02d57a6ecb39b02268be9511d89ef072911f17bd2dcd095f3f3cbeb5a905'
V2_FILES = {'antenna_group_policy', 'antenna_legacy', 'antenna_verifier', 'antenna_source_manifest'}
GROUP_POLICY = dict(group_names=128, max_groups=1024, max_attempts=64,
    max_total_jumpers=4096, max_commands=4096, max_report_bytes=8 << 30,
    max_selected_report_bytes=128 << 20, max_metadata_bytes=4 << 20,
    max_cumulative_work=200000000000)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path, cap=16 << 20):
    require(path.is_file() and path.stat().st_size <= cap, 'JSON report byte budget: ' + str(path))
    return json.loads(path.read_text())


def identity(path):
    path = path.resolve(strict=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= 4 << 30, 'file byte budget')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            'file changed while hashing')
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def natural(value):
    return type(value) is int and value >= 0


def metadata_prefix(path, selected, file_cap=512 << 20):
    """Read early canonical JSON metadata without materializing native IR arrays.

    This deliberately checks only selected metadata. The complete file is
    independently hash-bound, and its EDA semantics remain subject to full GDS
    DRC/antenna/device-LVS gates. A changed field order that puts a large array
    before required metadata fails with an explicit prefix-budget diagnostic.
    """
    require(path.is_file() and path.stat().st_size <= file_cap, 'native metadata file byte budget')
    with path.open('rb') as stream:
        prefix = codecs.getincrementaldecoder('utf-8')().decode(stream.read(65536), final=False)
    decoder = json.JSONDecoder()
    index, result, seen = 0, {}, set()

    def whitespace():
        nonlocal index
        while index < len(prefix) and prefix[index] in ' \n\r\t':
            index += 1

    def character(expected):
        nonlocal index
        whitespace()
        require(index < len(prefix) and prefix[index] == expected,
                'native metadata prefix format/budget')
        index += 1

    character('{')
    while not set(selected) <= result.keys():
        whitespace()
        try:
            key, index = decoder.raw_decode(prefix, index)
            require(isinstance(key, str) and key not in seen and len(seen) < 128,
                    'native metadata duplicate key/budget')
            seen.add(key)
            character(':')
            whitespace()
            value, index = decoder.raw_decode(prefix, index)
        except json.JSONDecodeError as error:
            raise ValueError('native metadata prefix field byte budget/order') from error
        if key in selected:
            result[key] = value
        if not set(selected) <= result.keys():
            character(',')
    return result


def rc_profile_sources(profile, files):
    """Bind native profile data to the same original calibration as OpenRCX."""
    require(profile['schema'] == 'rustroad.rc-profile.v1' and profile['pdk'] == 'ihp'
            and profile['corner'] == 'nom'
            and profile['units'] == dict(length='um', capacitance='pF', resistance='ohm')
            and profile['technology_lef_sha256'] == files['tech_lef']['sha256'],
            'native nominal RC profile scope differs')
    roles = dict(capacitance_calibration_table='rules_nom', technology_lef='tech_lef',
                 resistance_and_crosscheck_tech='magic_tech', resistance_extract_tech='magic_extract')
    sources = profile['sources']
    require(isinstance(sources, list) and len(sources) == len(roles)
            and {value['role'] for value in sources} == set(roles), 'native RC source inventory differs')
    for value in sources:
        require(set(value) == {'role', 'path', 'sha256', 'bytes'}, 'complete native RC source identity required')
        record = {key: value[key] for key in ('path', 'sha256', 'bytes')}
        require(record == files[roles[value['role']]] and identity(Path(record['path'])) == record,
                'actual native RC profile source differs: ' + value['role'])
    require(files['rules_nom']['sha256'] == RC_RULE_SHA256, 'native profile calibration is not the original default')
    model = profile['model']['resistance_sources']
    require(model['wire'] == files['rules_nom']['path'] + ': constant RESOVER0 column4 Ohm/nm x1000 xwidth_um'
            and model['cut'] == files['tech_lef']['path'] + ': LAYER CUT RESISTANCE, fixed across RC corners',
            'native RC resistance source model differs')
    return sources


def calibration_evidence(files, root, extracted, external, joint_tap=False):
    contract_id = files['contract']
    require(contract_id == identity(Path(contract_id['path']))
            and contract_id['sha256'] == (TAP_CONTRACT_SHA256 if joint_tap else CONTRACT_SHA256)
            and contract_id['bytes'] == CONTRACT_BYTES, 'common native contract differs')
    contract = read(Path(contract_id['path']), 1 << 20)
    require(contract['schema'] == 'rusteda.ibex-sg13g2-ez130.contract.v1'
            and contract['common']['rc_calibration_rules'] == RC_RULE_KEY
            and contract['common']['rc_calibration_sha256'] == contract['files'][RC_RULE_KEY] == RC_RULE_SHA256
            and files['rules_nom']['sha256'] == RC_RULE_SHA256
            and identity(Path(files['rules_nom']['path'])) == files['rules_nom'],
            'common original default RC calibration differs')
    for observed, original in [('rules_nom', 'rules_nom'), ('tech_lef', 'tech_lef'),
                               ('cell_lef', 'std_lef'), ('ram_lef', 'ram_lef')]:
        require(external['files'][observed] == files[original],
                'actual OpenRCX source differs from pinned input: ' + observed)
    source = extracted['rc_profile']
    path = Path(source['path']).resolve(strict=True)
    require(path.is_relative_to(root) and source == identity(path) and source['bytes'] <= 2 << 20,
            'native RC profile must be this-run hash-bound data')
    profile = read(path, 2 << 20)
    actual_sources = rc_profile_sources(profile, files)
    require(profile['technology_lef_sha256'] == contract['files']['ez130/lef/ez130_sg13g2_tech.lef'],
            'native RC profile technology differs from common contract')
    return dict(contract=contract_id, rc_calibration=files['rules_nom'], native_rc_profile=source,
                native_profile_sources=actual_sources, rc_corner='nom', resistance_model='openrcx-pdk')


def antenna_lineage(lineage, root, route, inspect, cts, files, tools):
    """Verify actual revision ancestry; native antenna results stay diagnostic."""
    require(lineage.get('schema') == 'rusteda.native-antenna-lineage.v1'
            and lineage.get('status') in {'complete', 'unresolved'}
            and lineage.get('scope') == 'supported_standard_cell_subset'
            and lineage.get('route_scope') == 'all_signal_nets'
            and lineage.get('requires_full_revalidation') is True
            and lineage.get('whole_layout_antenna_status') == 'unchecked',
            'native antenna lineage scope/status differs')
    require(lineage.get('tool') == tools['rustroad'], 'antenna lineage used another native tool')
    initial, final = lineage['initial_revision'], lineage['final_revision']
    require(initial == route['revision'] and final == inspect['revision'],
            'route/antenna/final revision binding differs')
    workspace = Path(lineage['workspace']).resolve(strict=True)
    require(workspace.is_dir() and workspace.is_relative_to(root), 'antenna workspace outside current run')

    def revision(value):
        require(isinstance(value, str) and re.fullmatch('[A-Za-z0-9_-]{1,64}', value),
                'unsafe native revision handle')
        return value

    def record(value, cap=256 << 20):
        require(isinstance(value, dict) and set(value) == {'path', 'sha256', 'bytes'}
                and isinstance(value['path'], str) and Path(value['path']).is_absolute()
                and natural(value['bytes']) and value['bytes'] <= cap,
                'native lineage file identity/budget')
        path = Path(value['path']).resolve(strict=True)
        require(path.is_relative_to(root) and identity(path) == value,
                'native lineage file is stale/external')
        return path

    def parent(value, expected, expected_parent=None, check_parent=True):
        require(isinstance(value, dict) and set(value) == {'revision', 'parent_revision', 'design_file'}
                and value['revision'] == revision(expected), 'native revision metadata differs')
        path = record(value['design_file'], 512 << 20)
        require(path == (workspace / 'revisions' / expected / 'design.json').resolve(strict=True),
                'native design metadata belongs to another revision')
        actual = metadata_prefix(path, {'format_version', 'revision', 'parent_revision', 'stage'})
        require(type(actual['format_version']) is int and actual['format_version'] in {4, 5, 6}
                and actual['revision'] == expected and actual['stage'] == 'placed'
                and actual['parent_revision'] == value['parent_revision'], 'native persisted revision header differs')
        if check_parent:
            require(actual['parent_revision'] == expected_parent, 'native child has another actual parent')
        return value

    raw_fields = {'schema', 'status', 'workspace_revision', 'design', 'scope', 'sources', 'safety_margin'}

    def antenna_report(value, header, expected):
        path = record(value)
        raw = metadata_prefix(path, raw_fields, 256 << 20)
        require(raw['schema'] == 'rustroad.antenna-check.v1'
                and raw['workspace_revision'] == expected and raw['design'] == 'ibex_sram_bench'
                and raw['status'] in {'passed', 'partial', 'violated'}
                and raw['scope']['routing_scope'] == 'all_signal_nets',
                'antenna raw report belongs to another revision/scope')
        require(all(header.get(key) == value for key, value in raw.items() if key != 'design'),
                'antenna selected header differs from actual raw prefix')
        sources = raw['sources']
        require(isinstance(sources, list) and len(sources) == 2
                and {source['role'] for source in sources} == {'technology_lef', 'cell_lef'},
                'antenna source scope differs')
        for source in sources:
            source_key = 'tech_lef' if source['role'] == 'technology_lef' else 'cell_lef'
            expected_source = files[source_key]
            require(source['path'] == expected_source['path']
                    and source['sha256'] == expected_source['sha256'], 'antenna used another source LEF')
        rows = header['nets']
        require(isinstance(rows, list) and len(rows) <= 100000
                and natural(header['violations']) and natural(header['lef_violations']),
                'antenna header object/count budget')
        names = set()
        violating = set()
        for row in rows:
            name = row['net']
            require(isinstance(name, str) and 0 < len(name) <= 4096 and name not in names
                    and type(row['violated']) is bool, 'antenna header net identity/budget')
            names.add(name)
            if row['violated']:
                violating.add(name)
        return violating

    commands = lineage['commands']
    require(isinstance(commands, list) and 1 <= len(commands) <= 16, 'antenna command budget')
    by_output = {}
    for command in commands:
        argv = command['command']
        require(isinstance(argv, list) and 8 <= len(argv) <= 64
                and all(isinstance(arg, str) and len(arg) <= 8192 for arg in argv)
                and argv[0] == tools['rustroad']['path'] and argv[1:3] == ['--workspace', str(workspace)]
                and argv[3] == '--threads' and argv[4] in {'2', '16'}
                and argv[5:7] == ['--max-expansions', '8000000'] and command['exit_code'] == 0,
                'antenna command identity/scope differs')
        path = record(command['stdout'])
        record(command['stderr'], 16 << 20)
        require(str(path) not in by_output, 'duplicate antenna command output')
        by_output[str(path)] = argv[7:]

    def produced(value, operation):
        path = record(value)
        require(str(path) in by_output and by_output[str(path)][0] == operation,
                'antenna evidence not produced by this-run native command')
        return by_output[str(path)]

    parent(lineage['initial'], initial, cts['revision'])
    attempts, rounds = lineage['attempts'], lineage['rounds']
    require(isinstance(attempts, list) and len(attempts) <= 3
            and isinstance(rounds, list) and len(rounds) <= 3, 'antenna repair round budget')
    changed, current, seen = [], initial, {initial}
    for attempt in attempts:
        base, child = revision(attempt['base_revision']), revision(attempt['revision'])
        require(base == current and attempt['parent_revision'] == base, 'broken antenna attempt ancestry')
        before = read(record(attempt['before_header'], 16 << 20))
        violating = antenna_report(attempt['before_report'], before, base)
        produced(attempt['before_report'], 'check-antenna')
        produced(attempt['model'], 'export-connectivity')
        require(attempt['model']['bytes'] <= 64 << 20, 'native connectivity model byte budget')
        selected_path = record(attempt['selected'], 8 << 20)
        selected = read(selected_path, 8 << 20)
        require(isinstance(selected, list) and 0 < len(selected) <= 100000
                and all(isinstance(name, str) and 0 < len(name) <= 4096 for name in selected)
                and selected == sorted(set(selected)) and set(selected) <= violating,
                'antenna selection differs from actual violated current nets')
        argv = produced(attempt['repair_report'], 'repair-antenna')
        for flag, value in [('--base-rev', base), ('--antenna-report', attempt['before_report']['path']),
                            ('--selected-nets', str(selected_path))]:
            require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv)
                    and argv[argv.index(flag) + 1] == value, 'antenna repair arguments not bound to evidence')
        repair = read(record(attempt['repair_report'], 16 << 20))
        require(repair['schema'] == 'rustroad.antenna-repair.v1'
                and repair['base_revision'] == base and repair['revision'] == child
                and repair['routing_scope'] == 'all_signal_nets'
                and repair['repair_scope'] == 'selected_supported_nets'
                and repair['selected_nets'] == selected and repair['requires_full_revalidation'] is True
                and repair['antenna_status'] == 'partial_selected_supported_nets'
                and repair['drc_status'] == 'unchecked'
                and repair['status'] in {'subset_clean', 'subset_repaired', 'subset_partial', 'subset_unrepaired'}
                and repair['safety_margin'] == before['safety_margin']['fraction'],
                'native repair report source/scope differs')
        require(isinstance(repair['jumpers'], list) and len(repair['jumpers']) <= 4096,
                'antenna jumper object budget')
        if child != base:
            require(child not in seen and repair['jumpers'], 'antenna child reused or has no geometry change')
            parent(attempt['child'], child, base)
            seen.add(child)
            changed.append(attempt)
        else:
            require(not repair['jumpers'] and repair['before'] == repair['after'],
                    'no-op antenna attempt changed geometry/metrics')
            parent(attempt['child'], base, check_parent=False)
        current = child
    require(rounds == changed and current == final, 'antenna rounds omitted or fabricated a revision')
    parent(lineage['final'], final, check_parent=False)
    snapshot = record(lineage['workspace_head'], 128)
    actual_head = workspace / 'HEAD'
    require(actual_head.is_file() and actual_head.stat().st_size <= 128
            and snapshot.read_text() == final + '\n' and actual_head.read_text().strip() == final,
            'antenna final HEAD snapshot/actual workspace differs')
    header = read(record(lineage['final_header'], 16 << 20))
    remaining = antenna_report(lineage['final_report'], header, final)
    produced(lineage['final_report'], 'check-antenna')
    produced(lineage['final_model'], 'export-connectivity')
    require(lineage['final_model']['bytes'] <= 64 << 20, 'final native model byte budget')
    if attempts:
        require(lineage['final_report'] != attempts[-1]['before_report'], 'final antenna check was not freshly generated')
    excluded, unresolved = lineage['excluded_macro_touch'], lineage['unresolved_supported_nets']
    for names in (excluded, unresolved):
        require(isinstance(names, list) and len(names) <= 100000
                and all(isinstance(name, str) and 0 < len(name) <= 4096 for name in names)
                and names == sorted(set(names)), 'antenna unresolved/excluded name budget')
    require(set(unresolved) <= remaining and remaining <= set(unresolved) | set(excluded)
            and not set(unresolved) & set(excluded)
            and lineage['status'] == ('unresolved' if unresolved else 'complete'),
            'antenna subset status does not preserve unresolved nets')
    return dict(initial_revision=initial, final_revision=final, committed_repairs=len(rounds),
                attempts=len(attempts), workspace_head=lineage['workspace_head'],
                subset_status=lineage['status'], excluded_macro_touch=excluded,
                unresolved_supported_nets=unresolved, whole_layout_antenna_status='unchecked',
                requires_full_revalidation=True,
                scope='native subset repair ancestry only; complete original final-GDS antenna gate remains mandatory')



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


def antenna_lineage_v2(lineage, verification, root, route, inspect, cts, files, tools, lineage_path):
    """Consume the standalone verifier result; never import/run its large IR reader."""
    session = lineage['schema'] == 'rusteda.native-antenna-lineage.v3'
    require(session == single_process_antenna(files), 'antenna declared execution/lineage protocol differs')
    expected_review = ('rusteda.antenna-v3-independent-lineage-review.v1' if session
                       else 'rusteda.antenna-v2-independent-lineage-review.v1')
    require(V2_FILES <= set(files), 'v2 antenna source pins missing')
    require(files['flow_qualifier'] == identity(Path(__file__)), 'v2 qualifier source differs from executed helper')
    def unique(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicate v2 policy field')
            value[key] = item
        return value
    policy_path = Path(files['antenna_group_policy']['path'])
    require(policy_path.stat().st_size <= 65536, 'v2 policy byte budget')
    policy = json.loads(policy_path.read_bytes(), object_pairs_hook=unique)
    require(policy == GROUP_POLICY and all(type(v) is int for v in policy.values()), 'v2 antenna policy differs')
    runner = Path(files['antenna_runner']['path']).resolve(strict=True)
    require(runner.name == 'native_antenna_groups.py'
            and Path(files['antenna_legacy']['path']).resolve(strict=True) == runner.parent / 'native_antenna.py'
            and Path(files['antenna_fields']['path']).resolve(strict=True) == runner.parent / 'report_fields.py',
            'v2 imported sibling identities differ')
    source_manifest = read(Path(files['antenna_source_manifest']['path']), 2 << 20)
    require(source_manifest['schema'] == 'rusteda.private-source-review.v1'
            and len(source_manifest['sources']) <= 128, 'v2 source manifest schema/budget')
    recorded = {}
    for item in source_manifest['sources']:
        record = {k: item[k] for k in ('path', 'sha256', 'bytes')}
        require(identity(Path(record['path'])) == record, 'v2 source snapshot changed')
        name = Path(record['path']).name
        require(name not in recorded, 'duplicate v2 source role')
        recorded[name] = record
    for role, name in [('antenna_runner', 'native_antenna_groups.py'),
                       ('antenna_legacy', 'native_antenna.py'), ('antenna_fields', 'report_fields.py')]:
        require(name in recorded and all(files[role][k] == recorded[name][k] for k in ('sha256', 'bytes')),
                'v2 source differs from reviewed manifest')

    def dynamic(relative, cap=2 << 20):
        path = (root / relative).resolve(strict=True)
        require(path.is_relative_to(root) and path.is_file() and path.stat().st_size <= cap,
                'v2 evidence escapes this run or exceeds byte budget')
        return path

    def record(value, cap=512 << 20):
        require(isinstance(value, dict) and set(value) == {'path', 'sha256', 'bytes'}
                and natural(value['bytes']) and value['bytes'] <= cap, 'v2 dynamic file identity/budget')
        path = Path(value['path']).resolve(strict=True)
        require(path.is_relative_to(root) and identity(path) == value, 'v2 evidence stale/external')
        return path

    require(verification['schema'] == expected_review
            and verification['status'] == 'metadata_lineage_verified_requires_full_signoff'
            and verification['evidence_kind'] == 'actual_native_artifacts'
            and verification['whole_layout_antenna_status'] == 'unchecked'
            and verification['benchmark'] == 'not_run', 'v2 verifier is not actual metadata-only evidence')
    require(verification['verifier_source'] == files['antenna_verifier']
            and verification['tool'] == tools['rustroad']
            and verification['source_manifest'] == files['antenna_source_manifest']
            and verification['policy'] == policy
            and verification['lineage'] == identity(lineage_path), 'v2 verifier/source/lineage binding differs')
    require(verification['native_analysis_status'] in {'clean', 'violated', 'partial'}
            and natural(verification['routed_named_nets']) and 0 < verification['routed_named_nets'] <= 100000
            and natural(verification['final_group_count'])
            and verification['final_group_count'] == (verification['routed_named_nets'] + 127) // 128,
            'v2 final named inventory/group status unverified')
    require(isinstance(verification['source_lefs'], list) and len(verification['source_lefs']) == 2
            and {s['role'] for s in verification['source_lefs']} == {'technology_lef', 'cell_lef'},
            'v2 verifier LEF source inventory differs')
    for source in verification['source_lefs']:
        expected = files['tech_lef' if source['role'] == 'technology_lef' else 'cell_lef']
        require(source['path'] == expected['path'] and source['sha256'] == expected['sha256'],
                'v2 verifier used another source LEF')
    input_path = dynamic('backend/antenna-verification-inputs.json')
    require(verification['input_manifest'] == identity(input_path), 'v2 verifier used another config')
    config = read(input_path, 1 << 20)
    expected_config = dict(schema=('rusteda.antenna-v3-independent-verifier-inputs.v1' if session
        else 'rusteda.antenna-v2-independent-verifier-inputs.v1'), lineage=identity(lineage_path),
        tool=tools['rustroad'], source_manifest=files['antenna_source_manifest'],
        design_workspace=lineage['workspace'], evidence_kind='actual_native_artifacts')
    if session:
        expected_config['controller_inputs'] = identity(dynamic('backend/antenna-inputs.json'))
    require(config == expected_config, 'v2/v3 verifier config differs')
    require(lineage['schema'] in {'rusteda.native-antenna-lineage.v2', 'rusteda.native-antenna-lineage.v3'}
            and lineage['scope'] == 'supported_standard_cell_subset'
            and lineage['route_scope'] == 'all_signal_nets'
            and lineage['status'] in {'complete', 'unresolved'}
            and lineage['tool'] == tools['rustroad'], 'v2 lineage prefix scope differs')
    if session:
        require(verification['execution_mode'] == 'single_native_process'
                and type(verification['actual_native_processes']) is int
                and verification['actual_native_processes'] == 1
                and natural(verification['logical_operations'])
                and 0 < verification['logical_operations'] <= policy['max_commands'],
                'v3 actual process/logical operation scope differs')
    initial, final = lineage['initial_revision'], lineage['final_revision']
    workspace = Path(lineage['workspace']).resolve(strict=True)
    require(workspace.is_relative_to(root) and verification['workspace'] == str(workspace)
            and initial == verification['initial_revision'] == route['revision']
            and final == verification['final_revision'] == inspect['revision'], 'v2 route/final workspace differs')
    for key, expected, parent in [('initial', initial, cts['revision']), ('final', final, None)]:
        value = lineage[key]
        require(verification[key] == value and value['revision'] == expected
                and re.fullmatch('[A-Za-z0-9_-]{1,64}', expected), 'v2 revision identity differs')
        path = record(value['design_file'])
        require(path == workspace / 'revisions' / expected / 'design.json', 'v2 revision path differs')
        actual = metadata_prefix(path, {'format_version', 'revision', 'parent_revision', 'stage'})
        require(type(actual['format_version']) is int and actual['format_version'] in {4, 5, 6}
                and actual['stage'] == 'placed' and actual['revision'] == expected
                and actual['parent_revision'] == value['parent_revision'], 'v2 persisted revision differs')
        if key == 'initial':
            require(value['parent_revision'] == parent, 'v2 route no longer descends from the one CTS')
    snapshot = record(lineage['workspace_head'], 128)
    actual_head = workspace / 'HEAD'
    require(verification['workspace_head'] == lineage['workspace_head']
            and actual_head.stat().st_size <= 128 and snapshot.read_text() == final + '\n'
            and actual_head.read_text().strip() == final, 'v2 actual HEAD changed after verification')

    receipts = read(dynamic('step-receipts.json'), 2 << 20)
    require(isinstance(receipts, list) and len(receipts) <= 129, 'producer receipt budget')
    positions, by_id = {}, {}
    for index, receipt in enumerate(receipts):
        tag = receipt['id']
        require(isinstance(tag, str) and tag not in by_id, 'producer duplicated step receipt')
        positions[tag], by_id[tag] = index, receipt
    needed = {'fe_final_cts', 'be_route', 'be_antenna_repair', 'be_antenna_verify_inputs',
              'be_antenna_verify', 'be_inspect', 'be_def', 'be_netlist', 'be_powered', 'be_gds', 'be_extract'}
    if 'be_postroute' in by_id:
        needed.add('be_postroute')
        require(positions['be_route'] < positions['be_postroute'] < positions['be_antenna_repair'],
                'post-route repair must sit between the trial route and antenna repair')
    require(needed <= set(by_id)
            and positions['fe_final_cts'] < positions['be_route'] < positions['be_antenna_repair']
            < positions['be_antenna_verify_inputs'] < positions['be_antenna_verify']
            and all(positions['be_antenna_verify'] < positions[tag] for tag in
                    ('be_inspect', 'be_def', 'be_netlist', 'be_powered', 'be_gds', 'be_extract')),
            'producer verifier did not precede every final export')
    for tag in needed - {'be_antenna_verify_inputs'}:
        require(by_id[tag]['kind'] == 'command' and type(by_id[tag]['exit_code']) is int
                and by_id[tag]['exit_code'] == 0 and 'capture_error' not in by_id[tag],
                'producer command did not complete successfully: ' + tag)
    require(by_id['be_antenna_verify_inputs']['kind'] == 'render', 'verifier input was not producer-rendered')
    expected_argv = [tools['gnu_time']['path'], '-v', '-o', str(root / 'logs/be_antenna_verify.time'),
        tools['python']['path'], files['antenna_verifier']['path'], '--inputs', str(input_path),
        '--workspace', str(root / 'backend/antenna-verification'), '--base-rev', 'empty']
    command_path = dynamic('logs/be_antenna_verify.command.json')
    require(read(command_path) == dict(argv=expected_argv, cwd=str(root)), 'producer executed another verifier command')
    verification_stdout = dynamic('logs/be_antenna_verify.stdout')
    require(by_id['be_antenna_verify']['stdout'] == identity(verification_stdout)
            and read(verification_stdout) == verification, 'verifier result differs from actual command stdout')
    record(by_id['be_antenna_verify']['stderr'], 16 << 20)
    time_path = dynamic('logs/be_antenna_verify.time', 1 << 20)
    text = time_path.read_text()
    rss = re.findall(r'Maximum resident set size \(kbytes\):\s*(\d+)', text)
    require(len(rss) == 1 and 0 < int(rss[0]) <= 2 * 1024 * 1024
            and re.search(r'Exit status:\s*0\s*$', text), 'verifier RSS/exit status unverified')
    require(verification['resources']['address_space_limit_bytes'] == 2 << 30
            and natural(verification['resources']['peak_rss_kib_self'])
            and verification['resources']['peak_rss_kib_self'] > 0, 'standalone verifier address-space guard unverified')
    return dict(initial_revision=initial, final_revision=final, workspace_head=lineage['workspace_head'],
        subset_status=lineage['status'], native_analysis_status=verification['native_analysis_status'],
        verification=identity(dynamic('backend/antenna-verification/result.json')),
        verifier_inputs=identity(input_path), producer_command=identity(command_path),
        producer_stdout=identity(verification_stdout), producer_receipt=by_id['be_antenna_verify'],
        verifier_resources=dict(gnu_time=identity(time_path), peak_rss_kib=int(rss[0])),
        routed_named_nets=verification['routed_named_nets'], final_group_count=verification['final_group_count'],
        whole_layout_antenna_status='unchecked', requires_full_revalidation=True,
        **(dict(execution_mode='single_native_process',
                actual_native_processes=verification['actual_native_processes'],
                logical_operations=verification['logical_operations']) if session else {}),
        scope='independently verified grouped native metadata; complete original foundry antenna gate remains separate')


def postroute_lineage(result, root, route, cts, tools, foundry_first=False):
    """Bind the post-route runner: trial = the be_route revision of the frontend
    final workspace; final = a file-for-file trial copy (route/CTS records stay
    the trial's) or an ECO rebuild whose own CTS/route records replace them."""
    require(result['schema'] == ('rusteda.native-postroute-reuse-result.v1' if foundry_first
            else 'rusteda.native-postroute-result.v1')
            and result['status'] in {'clean', 'repaired', 'residual_violations', 'round_budget_exhausted'}
            and result['final_revision'] == 'rev4' and result['tools']['rustroad'] == tools['rustroad']
            and result['trial']['revision'] == route['revision'] == 'rev4'
            and Path(result['final_workspace']).resolve() == (root / 'backend/postroute/road').resolve()
            and Path(result['trial']['workspace']).resolve().is_relative_to(root),
            'post-route result does not bind the trial route and final workspace')
    if result['final_source'] == 'trial_copy':
        require(result['resized'] == {} and natural(result['copied_files']) and result['copied_files'] > 0,
                'trial copy without resizes required')
        return route, cts, dict(source='trial_copy', status=result['status'], resized=0)
    require(result['final_source'] == 'eco_rebuild' and result['resized'], 'ECO rebuild without resizes')
    builds = [r['build'] for r in result['rounds'] if 'build' in r]
    require(builds and len(builds) <= 3, 'post-route build count')
    previous = result['trial']['workspace']
    for build in builds:
        require(build['revisions'] == ['rev1', 'rev2', 'rev3', 'rev4']
                and build['eco_prior']['workspace'] == previous and build['eco_prior']['revision'] == 'rev4',
                'post-route rebuild chain differs')
        previous = build['workspace']
    records = {}
    for stage in ('cts', 'route'):
        value = builds[-1]['stage_stdout'][stage]
        path = Path(value['path']).resolve(strict=True)
        require(path.is_relative_to(root / 'backend/postroute') and identity(path) == value,
                'post-route stage record stale: ' + stage)
        records[stage] = read(path, 1 << 20)
    require(records['route']['revision'] == 'rev4' and records['cts']['revision'] == 'rev3'
            and records['route']['routing_status'] == 'signal_geometry_checked',
            'post-route rebuild did not route its own CTS revision')
    return records['route'], records['cts'], dict(source='eco_rebuild', status=result['status'],
                                                  resized=len(result['resized']), builds=len(builds))


def placed_macros(path, macros):
    require(path.is_file() and path.stat().st_size <= 128 << 20, 'DEF byte budget')
    components, statement, in_components = {}, '', False
    count, units, design = 0, None, None
    with path.open() as stream:
        while line := stream.readline(65537):
            require(len(line) <= 65536, 'DEF line byte budget')
            if line.startswith('DESIGN '):
                require(design is None, 'duplicate DEF DESIGN')
                design = line.split()[1]
            if line.startswith('UNITS DISTANCE MICRONS '):
                require(units is None, 'duplicate DEF units')
                units = int(line.split()[3])
            if line.startswith('COMPONENTS '):
                require(not in_components and count == 0, 'duplicate DEF component section')
                in_components = True
                continue
            if line.startswith('END COMPONENTS'):
                require(in_components and not statement.strip(), 'unfinished DEF component')
                in_components = False
                break  # Placement-only check; do not parse later supply-net lines.
            if not in_components:
                continue
            statement += line
            require(len(statement) <= 65536, 'DEF component statement budget')
            if ';' not in line:
                continue
            count += 1
            require(count <= 500000, 'DEF component count budget')
            match = re.fullmatch(r'\s*-\s+(\S+)\s+(\S+)\s+\+\s+(FIXED|PLACED)\s*'
                                 r'\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)\s*;\s*', statement)
            require(match is not None, 'unsupported DEF placement statement')
            name, master, status, x, y, orientation = match.groups()
            if master == 'RM_IHPSG13_1P_1024x32_c2_bm_bist':
                require(name not in components, 'duplicate SRAM instance')
                components[name] = dict(master=master, status=status,
                                        position_dbu=[int(x), int(y)], orientation=orientation)
            statement = ''
    require(not in_components and design == 'ibex_sram_bench' and units == 1000, 'DEF top/units/scope')
    require(set(components) == {'u_imem', 'u_dmem'}, 'actual DEF SRAM instance set')
    # Expected SRAM placement: the hash-pinned common contract (DBU 1000/um).
    require(set(macros) == {'u_imem', 'u_dmem'}, 'contract SRAM instance set')
    for name in ('u_imem', 'u_dmem'):
        expected = [round(v * 1000) for v in macros[name]['location_um']]
        require(macros[name]['orientation'] == 'N' and macros[name]['cell'] == 'RM_IHPSG13_1P_1024x32_c2_bm_bist'
                and len(expected) == 2, 'contract SRAM placement form')
        require(components[name]['position_dbu'] == expected
                and components[name]['orientation'] == 'N' and components[name]['status'] == 'FIXED',
                'actual SRAM placement differs from common configuration')
    return dict(top=design, dbu_per_um=units, component_count=count, macros=components)


def final_density(context, def_path):
    """An optional final-layout policy augments the existing placement gate."""
    files = context['files']
    roles = {'density_checker', 'density_policy'}
    if not roles & files.keys():
        return None
    require(roles <= files.keys(), 'paired final density inputs required')
    policy = read(Path(files['density_policy']['path']), 1 << 20)
    common = read(Path(files['contract']['path']), 1 << 20)['common']
    require(policy['evaluation_stage'] == 'after_final_signal_routing_and_all_cts_hold_drv_antenna_ecos'
            and type(policy['max_percent']) is int
            and policy['max_percent'] == common['final_density_max_percent'],
            'final density policy differs from the physical contract')
    source = files['density_checker']
    require(source == identity(Path(source['path'])) and source['bytes'] <= 1 << 20,
            'final density checker identity/budget')
    spec = importlib.util.spec_from_file_location('qualified_final_density', source['path'])
    require(spec is not None and spec.loader is not None, 'density checker loader')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.utilization(def_path, Path(files['cell_lef']['path']),
                                read(Path(files['physical']['path']), 1 << 20),
                                common, policy['max_percent'])
    require(source == identity(Path(source['path'])), 'density checker changed')
    require(result['status'] == 'passed', 'FINAL_DENSITY_LIMIT: final routed utilization exceeds target')
    result.update(scope='final_routed_layout', inputs={role: files[role] for role in
                  ('density_checker', 'density_policy', 'cell_lef', 'physical', 'contract')})
    return result


def tap_identity(path, cap=4 << 30):
    """Bound growth through EOF as well as before/after source metadata."""
    path = Path(path).resolve(strict=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= cap, 'joint file byte/type budget')
    digest, total = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while chunk := stream.read(min(65536, cap - total + 1)):
            total += len(chunk)
            require(total <= cap, 'joint file grew beyond byte budget')
            digest.update(chunk)
    after = path.stat()
    require(total == before.st_size == after.st_size
            and (before.st_dev, before.st_ino, before.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_mtime_ns), 'joint file changed during identity')
    return dict(path=str(path), sha256=digest.hexdigest(), bytes=total)


def tap_read(path, cap=4 << 20):
    record = tap_identity(path, cap)
    with Path(record['path']).open('rb') as stream:
        raw = stream.read(cap + 1)
    require(len(raw) == record['bytes'] <= cap
            and hashlib.sha256(raw).hexdigest() == record['sha256']
            and tap_identity(path, cap) == record, 'joint JSON changed/grown')
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate joint metadata key')
            result[key] = value
        return result
    def constant(_value):
        raise ValueError('nonfinite joint metadata')
    def finite_float(value):
        result = float(value)
        require(math.isfinite(result), 'nonfinite joint metadata')
        return result
    result = json.loads(raw, object_pairs_hook=unique, parse_float=finite_float, parse_constant=constant)
    pending, nodes = [(result, 0)], 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        require(nodes <= 65536 and depth <= 32, 'joint JSON node/depth budget')
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        for child in children:
            require(nodes + len(pending) < 65536, 'joint JSON pending budget')
            pending.append((child, depth + 1))
    return result


def tap_raw_zero(record, rule, root, helper):
    path = Path(record['path']).resolve(strict=True)
    require(path.is_relative_to(root) and tap_identity(path, 64 << 20) == record,
            'joint raw foundry current-run identity differs')
    # Reuse only the already pinned C7 metadata parser: <=64MiB/250k nodes,
    # depth64/categories4096, DTD/entity rejection; no geometry/deck decoding.
    categories, count = helper.xml_scope(path)
    require(count == 0 and categories and rule in categories
            and all(len(name.encode()) <= 4096 for name in categories)
            and tap_identity(path, 64 << 20) == record,
            'joint raw foundry finding or required category absent')
    return categories


def tap_routing_inventory(value, root, template):
    """Independently inventory every possible path writer, not an audit claim.

    Same T1 frontend admission:16,384 nodes/pending,depth32,one replay writer.
    <=8MiB additional estimated pending state; admit before every append.
    """
    allowed = {('limits', 'replay', 'path'), ('track_router', 'limits', 'replay', 'path')}
    pending, nodes, writers = [((), value, 0)], 0, []
    while pending:
        trail, current_value, depth = pending.pop()
        nodes += 1
        require(nodes <= 16384 and depth <= 32, 'joint routing writer work/depth budget')
        if isinstance(current_value, dict):
            children = current_value.items()
        elif isinstance(current_value, list):
            children = enumerate(current_value)
        else:
            children = ()
            if trail and (trail[-1] == 'path' or trail[-1].endswith(('_path', '_file', '_directory'))):
                require(trail in allowed and isinstance(current_value, str) and current_value,
                        'unknown joint routing writer field')
                if template:
                    require(current_value == '{output}/backend/route-budget-error.json',
                            'foreign joint routing writer template')
                else:
                    target = Path(current_value)
                    require(target.is_absolute() and target.resolve() == root / 'backend/route-budget-error.json'
                            and target.resolve().is_relative_to(root), 'foreign joint routing writer target')
                require(not writers, 'joint routing writer inventory must have exactly one writer')
                writers.append(dict(field='/'.join(trail), path=current_value))
        for key, child in children:
            require(nodes + len(pending) < 16384 and depth < 32,
                    'joint routing writer pending/depth budget')
            pending.append((trail + (str(key),), child, depth + 1))
    require(len(writers) == 1, 'joint routing writer inventory must have exactly one writer')
    return writers


def tap_overlap(context):
    """True when the pinned policy selects a same-immutable-GDS overlap."""
    return tap_read(context['files']['tap_policy']['path'], 65536)['execution'] in (
        TAP_OVERLAP_EXECUTION, TAP_OVERLAP_ALL_EXECUTION, TAP_EARLY_EXECUTION)


def tap_overlap_all(context):
    """True when every step from maximal through logic shares one dependency region."""
    return tap_read(context['files']['tap_policy']['path'], 65536)['execution'] in (TAP_OVERLAP_ALL_EXECUTION, TAP_EARLY_EXECUTION)


def tap_early(context):
    """True when maximal starts on a pre-postroute GDS, before the foundry-first chain."""
    return tap_read(context['files']['tap_policy']['path'], 65536)['execution'] == TAP_EARLY_EXECUTION


def tap_overlap_all_group():
    """Exact region: the serial order survives as dependencies, only maximal and later checks overlap.

    <=24 nodes. Reservations sum to at most 7168 MiB at admission; a real repair or
    extraction runs beside maximal under the same cap, and a repaired layout then
    fails review on the GDS identity above.
    """
    lean = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15]

    def command(dependencies, slots, memory):
        return dict(depends_on=dependencies, cpu_slots=slots, memory_mib=memory)

    return dict(max_parallel=8, max_memory_mib=7168, steps={
        'nf_tap_latchup': command([], [6, 7], 1920),
        'be_ff_main': command([], [0, 1], 2560),
        'be_ff_antenna': command([], [2, 3], 2688),
        'be_antenna_repair': command(['be_ff_main', 'be_ff_antenna'], lean, 5248),
        'be_antenna_verify': command(['be_antenna_repair'], [0, 1], 2304),
        'be_inspect': command(['be_antenna_verify'], [0, 1], 512),
        'be_def': command(['be_inspect'], [0, 1], 512),
        'be_netlist': command(['be_def'], [0, 1], 512),
        'be_powered': command(['be_netlist'], [0, 1], 512),
        'be_gds': command(['be_powered'], [0, 1], 512),
        'be_extract': command(['be_gds'], lean, 3840),
        'be_rcx_inputs': dict(depends_on=['be_extract']),
        'be_openrcx': command(['be_rcx_inputs'], [4, 5], 768),
        'be_timing_inputs': dict(depends_on=['be_openrcx']),
        'be_timing': command(['be_timing_inputs'], [4, 5], 1024),
        'be_main': command(['be_extract'], [0, 1], 2304),
        'be_antenna': command(['be_extract'], [2, 3], 2560),
        'be_lvs_inputs': dict(depends_on=['be_extract']),
        'be_lvs': command(['be_lvs_inputs', 'be_antenna'], [2, 3], 2048),
        'be_macro_contract': command(['be_lvs'], [10, 11], 256),
        'nf_tap_coverage_inputs': dict(depends_on=['be_extract']),
        'nf_tap_coverage': command(['nf_tap_coverage_inputs'], [0, 1], 1280),
        'nf_logic_inputs': dict(depends_on=['be_extract']),
        'nf_logic': command(['nf_logic_inputs'], [8, 9], 768)})


def tap_early_group():
    """Exact region: maximal beside postroute, LVS and OpenRCX beside the main precheck.

    <=27 nodes. Reservations sum to at most 7168 MiB at admission. Every export before the
    decision is read-only; a real repair or a resize changes the final GDS and then fails
    review on the GDS identity, so this schedule can only pass an unrepaired layout.
    """
    lean = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15]

    def command(dependencies, slots, memory):
        return dict(depends_on=dependencies, cpu_slots=slots, memory_mib=memory)

    return dict(max_parallel=8, max_memory_mib=7168, steps={
        'nf_tap_latchup': command([], [6, 7], 1792),
        'be_postroute': command([], lean, 5248),
        'be_antenna_inputs': dict(depends_on=['be_postroute']),
        'be_ff_export': command(['be_antenna_inputs'], [0, 1], 512),
        'be_inspect': command(['be_ff_export'], [4, 5], 512),
        'be_def': command(['be_inspect'], [4, 5], 512),
        'be_netlist': command(['be_def'], [4, 5], 512),
        'be_powered': command(['be_netlist'], [4, 5], 512),
        'be_gds': command(['be_powered'], [4, 5], 512),
        'be_ff_main': command(['be_ff_export'], [0, 1], 2304),
        'be_ff_antenna': command(['be_ff_export'], [2, 3], 2560),
        'be_antenna_repair': command(['be_ff_main', 'be_ff_antenna', 'be_gds'], lean, 5248),
        'be_antenna_verify': command(['be_antenna_repair'], [0, 1], 2304),
        'be_extract': command(['be_antenna_verify'], lean, 3840),
        'be_rcx_inputs': dict(depends_on=['be_def']),
        'be_openrcx': command(['be_rcx_inputs', 'be_ff_antenna'], [4, 5], 768),
        'be_timing_inputs': dict(depends_on=['be_openrcx', 'be_extract']),
        'be_timing': command(['be_timing_inputs'], [4, 5], 1024),
        'be_main': command(['be_extract'], [0, 1], 2304),
        'be_antenna': command(['be_extract'], [2, 3], 2560),
        'be_lvs_inputs': dict(depends_on=['be_gds']),
        'be_lvs': command(['be_lvs_inputs', 'be_ff_antenna'], [2, 3], 2048),
        'be_macro_contract': command(['be_lvs'], [10, 11], 256),
        'nf_tap_coverage_inputs': dict(depends_on=['be_extract']),
        'nf_tap_coverage': command(['nf_tap_coverage_inputs'], [0, 1], 1280),
        'nf_logic_inputs': dict(depends_on=['be_extract']),
        'nf_logic': command(['nf_logic_inputs'], [8, 9], 768)})


def tap_early_export():
    """Exact read-only export of the routed frontend workspace for the early maximal."""
    return dict(id='be_tap_export', kind='command', tool='rustroad', timeout_s=300, arguments=[
        '--workspace', '{output}/frontend/road-final', 'export-gds', '--cell-gds',
        '{output}/backend/profiles/combined-cells.gds', '--layer-map', '{output}/backend/profiles/gds-map.json',
        '--out', '{output}/' + TAP_EARLY_GDS, '--cell-lef', '{files:cell_lef}'])


def tap_maximal_gds(context, artifacts):
    """The GDS maximal actually ran on, proven byte-identical to the final GDS."""
    if not tap_overlap(context):
        return artifacts['gds']
    initial = tap_identity(Path(context['run_root']) / (TAP_EARLY_GDS if tap_early(context) else TAP_INITIAL_GDS), 64 << 20)
    require((initial['sha256'], initial['bytes']) == (artifacts['gds']['sha256'], artifacts['gds']['bytes']),
            'overlap maximal GDS differs from the final GDS; rerun the serial schedule')
    return initial


def tap_execution_proof(context, artifacts, tag, argv, manifest_id, helper):
    """Pin full actual checker output to the actual producer stdout emission."""
    root, files, tools = Path(context['run_root']), context['files'], context['tools']
    latchup = tag == 'nf_tap_latchup'
    capture = root / ('backend/latchup-execution' if latchup else 'backend/tap-coverage-execution')
    work = root / ('backend/latchup' if latchup else 'backend/tap-coverage')
    path = capture / 'result.json'
    result_id = tap_identity(path, 65536)
    result = tap_read(path, 65536)
    expected_fields = {'schema', 'status', 'producer_step', 'input_manifest', 'ownership',
        'source_pins', 'tools', 'inputs', 'outputs', 'child', 'actual_python_children',
        'actual_native_processes', 'native_process_count_scope', 'wall_seconds',
        'command_deadline_seconds', 'cleanup_reserve_seconds'}
    require(set(result) == expected_fields and result['schema'] == 'rusteda.joint-tap-execution.v1'
            and result['status'] == 'captured_passed_requires_joint_qualification'
            and result['producer_step'] == tag and result['input_manifest'] == manifest_id
            and result['ownership'] == tap_identity(root.parent / 'ownership/native.json', 65536),
            'actual joint execution receipt schema/ownership differs')
    source_pins = dict(guard=files['tap_guard'], runner=files[
        'tap_latchup_runner' if latchup else 'tap_coverage_runner'])
    source_pins['common' if latchup else 'worker'] = files[
        'tap_latchup_common' if latchup else 'tap_coverage_worker']
    require(result['source_pins'] == source_pins and result['tools'] == {
            name: tools[name] for name in ('python', 'appimage')}, 'actual joint executable pins differ')
    actual_inputs = dict(gds=tap_maximal_gds(context, artifacts) if latchup else artifacts['gds'])
    if latchup:
        profile = Path(files['drc_profile']['path']) if tap_early(context) else root / 'backend/postroute/effective-inputs/foundry/provenance.json'
        actual_inputs.update(profile=tap_identity(profile, 2 << 20),
            deck=tap_identity(profile.parent / 'klayout/tech/drc/rule_decks/sg13g2_maximal.drc', 256 << 20))
    else:
        actual_inputs['config'] = tap_identity(root / TAP_PATHS['coverage_config'], 1 << 20)
    outputs = dict(summary=tap_identity(work / 'summary.json', 4 << 20), head=tap_identity(work / 'HEAD', 128))
    if latchup:
        outputs.update(raw_report=tap_identity(work / 'drc.lyrdb', 64 << 20),
                       command=tap_identity(work / 'command.json', 65536))
    else:
        outputs['controller'] = tap_identity(work / 'controller.json', 4 << 20)
    require(result['inputs'] == actual_inputs and result['outputs'] == outputs,
            'actual joint complete input/output artifact identity differs')
    for head in (work / 'HEAD', capture / 'HEAD'):
        require(tap_identity(head, 128)['bytes'] == 5 and head.read_bytes() == b'rev1\n',
                'actual joint checker/capture completion HEAD differs')
    child = result['child']
    require(set(child) == {'argv', 'pid', 'exit_code', 'wall_seconds', 'available_seconds', 'stdout', 'stderr'}
            and child['argv'] == [argv[0], *argv[2:]]
            and type(child['pid']) is int and child['pid'] > 0
            and type(child['exit_code']) is int and child['exit_code'] == 0
            and type(result['actual_python_children']) is int and result['actual_python_children'] == 1
            and result['actual_native_processes'] is None
            and result['native_process_count_scope'] ==
                'not inferred; exact existing checker/controller command is separately bound',
            'actual joint child command/exit/count telemetry differs')
    seconds = 900 if latchup else 600
    require(type(result['command_deadline_seconds']) is int and result['command_deadline_seconds'] == seconds
            and type(result['cleanup_reserve_seconds']) is int and result['cleanup_reserve_seconds'] == 10
            and type(child['available_seconds']) in (int, float) and math.isfinite(child['available_seconds'])
            and 0 < child['available_seconds'] <= seconds - 10,
            'actual joint execution deadline differs')
    for value in (result['wall_seconds'], child['wall_seconds']):
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                'actual joint execution nonfinite/negative wall')
    require(child['wall_seconds'] <= result['wall_seconds'] <= seconds,
            'actual joint whole-command/child timing differs')
    for name in ('stdout', 'stderr'):
        require(child[name] == tap_identity(capture / (name + '.log'), 16 << 20),
                'actual joint child capture differs')
    require(sum(child[name]['bytes'] for name in ('stdout', 'stderr')) <= 16 << 20,
            'actual joint child aggregate log budget')
    require(tap_identity(path, 65536) == result_id, 'actual joint typed result changed during review')
    return result, dict(schema='rusteda.joint-tap-emission.v1', status=result['status'],
                        producer_step=tag, execution_result=result_id)


def tap_producer_proof(context, artifacts, c, inputs, helper):
    """Exact serial recipe plus real producer receipts, not matching dicts alone."""
    files, tools = context['files'], context['tools']
    root = Path(context['run_root'])
    manifest, groups = helper.producer_manifest(c)
    overlap_all, early = tap_overlap_all(context), tap_early(context)
    require(len(manifest['steps']) == (129 if early else 128) and len(groups) == (1 if early else 2 if overlap_all else 6)
            and set(manifest['gates']) == DERIVED | REPORTS
            and manifest['artifacts'] == context['artifacts']
            and type(manifest['scope']['threads']) is int and manifest['scope']['threads'] == 16
            and type(manifest['scope']['memory_mib']) is int and manifest['scope']['memory_mib'] == 8192,
            'joint proof requires128 steps/original16 gates/six groups/scope')
    require(all(manifest['files'].get(role) == files[role] for role in
            TAP_ROLES | {'flow_qualifier', 'contract', 'physical', 'routing'})
            and all(manifest['tools'].get(role) == tools[role] for role in ('python', 'appimage', 'gnu_time')),
            'joint context differs from actual producer source/tool pins')
    b4 = dict(max_parallel=3, max_memory_mib=7168, steps={
        'be_rcx_inputs': dict(depends_on=[]),
        'be_openrcx': dict(depends_on=['be_rcx_inputs'], cpu_slots=[4, 5], memory_mib=768),
        'be_timing_inputs': dict(depends_on=['be_openrcx']),
        'be_timing': dict(depends_on=['be_timing_inputs'], cpu_slots=[4, 5], memory_mib=1024),
        'be_main': dict(depends_on=[], cpu_slots=[0, 1], memory_mib=2304),
        'be_antenna': dict(depends_on=[], cpu_slots=[2, 3], memory_mib=2560),
        'be_lvs_inputs': dict(depends_on=[]),
        'be_lvs': dict(depends_on=['be_lvs_inputs', 'be_antenna'], cpu_slots=[2, 3], memory_mib=2048)})
    overlap = tap_overlap(context)
    grouped_tap = {tag for group in groups for tag in group['steps'] if tag.startswith('nf_tap_')}
    if overlap_all:
        require((tap_early_group() if early else tap_overlap_all_group()) in groups
                and grouped_tap == {'nf_tap_latchup', 'nf_tap_coverage_inputs', 'nf_tap_coverage'},
                'overlap-all region differs from its exact dependency proof')
    else:
        require(b4 in groups and grouped_tap == ({'nf_tap_latchup'} if overlap else set()),
                'joint extras must remain serial after the unchanged original B4 group')
    if overlap and not overlap_all:
        lane = next(group for group in groups if 'nf_tap_latchup' in group['steps'])
        require(set(lane['steps']) == {'be_ff_main', 'be_ff_antenna', 'nf_tap_latchup'}
                and lane['steps']['nf_tap_latchup']['depends_on'] == [] and lane['max_parallel'] == 3,
                'overlap maximal must share exactly the foundry-first precheck lane')
    final_gds = '{output}/' + context['artifacts']['gds']
    expected = [dict(id='nf_tap_latchup', kind='command', tool='python', timeout_s=900,
        arguments=['{files:tap_guard}', '{files:tap_latchup_runner}', '--gds', final_gds,
            '--profile', '{output}/backend/postroute/effective-inputs/foundry/provenance.json',
            '--tool', '{tools:appimage}', '--top', 'ibex_sram_bench', '--workspace',
            '{output}/backend/latchup', '--base-rev', 'empty']),
        dict(id='nf_tap_coverage_inputs', kind='render', destination=TAP_PATHS['coverage_config'],
            content=dict(schema='rusteda.tap-coverage.v1', gds={'$identity': context['artifacts']['gds']},
                workspace='{output}/backend/tap-coverage', base_revision='empty', top='ibex_sram_bench',
                excluded_cells={'RM_IHPSG13_1P_1024x32_c2_bm_bist': 2}, limits=TAP_LIMITS)),
        dict(id='nf_tap_coverage', kind='command', tool='python', timeout_s=600,
            arguments=['{files:tap_guard}', '{files:tap_coverage_runner}', '--config',
                '{output}/backend/tap-coverage-inputs.json', '--workspace',
                '{output}/backend/tap-coverage', '--base-rev', 'empty', '--tool',
                '{tools:appimage}', '--tool-sha256', tools['appimage']['sha256'],
                '--tool-bytes', str(tools['appimage']['bytes'])])]
    steps = manifest['steps']
    order = {step['id']: i for i, step in enumerate(steps)}
    begin = order['be_macro_contract'] + 1
    serial = expected
    if overlap:
        maximal, serial = expected[0], expected[1:]
        maximal['arguments'][maximal['arguments'].index(final_gds)] = '{output}/' + (TAP_EARLY_GDS if early else TAP_INITIAL_GDS)
        if early:
            maximal['arguments'][maximal['arguments'].index('--profile') + 1] = '{files:drc_profile}'
        require(steps[order['be_tap_export' if early else 'be_ff_export'] + 1] == maximal
                and (not early or steps[order['be_tap_export']] == tap_early_export()),
                'overlap maximal recipe/order differs')
    require(steps[begin:begin + len(serial)] == serial and order['nf_logic_inputs'] == begin + len(serial)
            and (overlap_all or all(order[tag] < order['be_macro_contract'] for group in groups for tag in group['steps']))
            and {tag for tag in order if tag.startswith('nf_tap_')} == {step['id'] for step in expected},
            'joint actual producer recipe/order differs')
    prefix = [tools['python']['path'], files['tap_guard']['path']]
    commands = {
        'nf_tap_latchup': [*prefix, files['tap_latchup_runner']['path'], '--gds',
            tap_maximal_gds(context, artifacts)['path'],
            '--profile', files['drc_profile']['path'] if early else str(root / 'backend/postroute/effective-inputs/foundry/provenance.json'),
            '--tool', tools['appimage']['path'], '--top', 'ibex_sram_bench', '--workspace',
            str(root / 'backend/latchup'), '--base-rev', 'empty'],
        'nf_tap_coverage': [*prefix, files['tap_coverage_runner']['path'], '--config',
            str(root / TAP_PATHS['coverage_config']), '--workspace', str(root / 'backend/tap-coverage'),
            '--base-rev', 'empty', '--tool', tools['appimage']['path'], '--tool-sha256',
            tools['appimage']['sha256'], '--tool-bytes', str(tools['appimage']['bytes'])]}
    native = helper.p.read(root.parent / 'ownership/native.json')
    emissions = {tag: tap_execution_proof(context, artifacts, tag, argv, native['manifest'], helper)
                 for tag, argv in commands.items()}
    rows = {tag: helper.producer_command(c, inputs, tag, argv, emissions[tag][1]) for tag, argv in commands.items()}
    if early:
        require(helper.runner.decision(c)['mode'] == 'foundry_clean_keep_revision',
                'early schedule exports before the decision; a repaired layout must rerun the serial schedule')
        written = helper.p.read(helper.producer_command(c, inputs, 'be_tap_export', [c['tools']['rustroad']['path'],
            '--workspace', str(root / 'frontend/road-final'), 'export-gds', '--cell-gds',
            str(root / 'backend/profiles/combined-cells.gds'), '--layer-map', str(root / 'backend/profiles/gds-map.json'),
            '--out', str(root / TAP_EARLY_GDS), '--cell-lef', files['cell_lef']['path']])['stdout']['path'])
        require(written['status'] == 'written' and written['revision'] == 'rev4'
                and written['workspace_mutated'] is False
                and written['output'] == tap_maximal_gds(context, artifacts),
                'early maximal export differs from its checked GDS')
    receipts = helper.p.read(root / 'step-receipts.json')
    require(isinstance(receipts, list) and len(receipts) <= 129, 'actual joint render receipt budget')
    render = [row for row in receipts if row['id'] == 'nf_tap_coverage_inputs']
    require(len(render) == 1 and set(render[0]) == {'id', 'kind', 'wall_seconds'}
            and render[0]['kind'] == 'render' and type(render[0]['wall_seconds']) in (int, float)
            and math.isfinite(render[0]['wall_seconds']) and render[0]['wall_seconds'] >= 0,
            'actual joint config render receipt differs')
    actual_order = [row['id'] for row in receipts]
    require(actual_order.index('nf_tap_latchup') < actual_order.index('nf_tap_coverage_inputs')
            < actual_order.index('nf_tap_coverage'), 'actual joint config render order differs')
    return dict(ownership=helper.p.identity(root.parent / 'ownership/native.json'),
                commands={tag: dict(receipt=row,
                    command=helper.p.identity(root / 'logs' / (tag + '.command.json')),
                    execution=emissions[tag][1]['execution_result'])
                    for tag, row in rows.items()}, coverage_render=render[0])


def verify_tap_checks(context, native, artifacts, reports, current, helper):
    """Metadata composition only; all physical verdicts come from pinned tools."""
    files, tools = context['files'], context['tools']
    root = Path(context['run_root']).resolve(strict=True)
    required_roles = TAP_ROLES
    require({name for name in files if name.startswith('tap_')} == required_roles,
            'explicit complete joint helper/dependency/policy pins required')
    for name in required_roles:
        cap = 256 << 20 if name == 'tap_latchup_deck' else 65536 if name == 'tap_policy' else 1 << 20
        require(tap_identity(files[name]['path'], cap) == files[name], 'joint source/policy changed: ' + name)
    require(files['tap_latchup_common'] == files['drc_runner'],
            'joint maximal common wrapper differs from original DRC wrapper')
    require(Path(files['tap_guard']['path']).name == 'tap_capture.py',
            'joint v2 requires the typed capture wrapper')
    require(tap_identity(__file__, 1 << 20) == files['flow_qualifier'], 'running joint qualifier source differs')
    policy = tap_read(files['tap_policy']['path'], 65536)
    require(policy in (TAP_POLICY, {**TAP_POLICY, 'execution': TAP_OVERLAP_EXECUTION},
                       {**TAP_POLICY, 'execution': TAP_OVERLAP_ALL_EXECUTION},
                       {**TAP_POLICY, 'execution': TAP_EARLY_EXECUTION})
            and all(type(policy[k]) is int for k in
            ('native_max_clear_gap_dbu', 'nominal_phase_dbu', 'foundry_diffusion_tie_max_um')),
            'explicit joint tap policy differs')
    worker = files['tap_coverage_worker']
    require((worker['sha256'], worker['bytes']) == TAP_WORKER,
            'joint proof requires the independently frozen hierarchy-fixed checker')
    for runner_role, sibling_role, runner_name, sibling_name in (
            ('tap_coverage_runner', 'tap_coverage_worker', 'tap_coverage_runner.py', 'tap_coverage.py'),
            ('tap_latchup_runner', 'tap_latchup_common', 'signoff_latchup.py', 'signoff_drc.py')):
        runner = Path(files[runner_role]['path']).resolve(strict=True)
        require(runner.name == runner_name and Path(files[sibling_role]['path']).resolve(strict=True)
                == runner.with_name(sibling_name), 'joint imported sibling source differs')
    contract = tap_read(files['contract']['path'])['common']
    require(type(contract['native_tap_max_gap_um']) is int and contract['native_tap_max_gap_um'] == 30
            and type(contract['tap_rule_max_distance_um']) is int and contract['tap_rule_max_distance_um'] == 20,
            'native30um policy/foundry20um rule conflated')
    physical = tap_read(files['physical']['path'])['tap']
    require(type(physical['max_gap']) is int and physical['max_gap'] == 30000
            and physical.get('pattern') == dict(kind='staggered', phase=15000)
            and type(physical['pattern']['phase']) is int, 'explicit native pattern differs')
    expected_paths = TAP_PATHS
    require(context['tap_checks'] == expected_paths, 'private current-run tap evidence paths differ')
    paths = {name: current(path) for name, path in expected_paths.items()}
    ids = {name: tap_identity(path, 4 << 20) for name, path in paths.items()}
    require(sum(record['bytes'] for record in ids.values()) <= 8 << 20,
            'joint simultaneous evidence JSON byte budget')
    data = {name: tap_read(path) for name, path in paths.items()}
    latchup, config, controller, certificate = [data[key] for key in
        ('latchup', 'coverage_config', 'coverage_controller', 'coverage_report')]
    routing_audit = data['routing_audit']
    routing_path = current('frontend/physical/routing.json')
    require(routing_audit.get('schema') == 'rusteda.private-routing-writer-audit.v1'
            and routing_audit.get('status') == 'passed'
            and routing_audit.get('run_root') == str(root)
            and routing_audit.get('template_mode') is False
            and routing_audit.get('config') == tap_identity(routing_path, 1 << 20),
            'rendered routing writer audit missing/stale')
    original_routing = tap_read(files['routing']['path'], 1 << 20)
    rendered_routing = tap_read(routing_path, 1 << 20)
    writers = routing_audit.get('writers')
    tap_routing_inventory(original_routing, root, True)
    require(isinstance(writers, list) and writers == tap_routing_inventory(rendered_routing, root, False),
            'routing writer inventory differs from actual complete JSON')
    for writer in writers:
        require(set(writer) == {'field', 'path'}
                and writer['field'] in {'limits/replay/path', 'track_router/limits/replay/path'},
                'unknown routing writer in audit')
        target = Path(writer['path'])
        require(target.is_absolute() and target.resolve().is_relative_to(root)
                and target.resolve() == root / 'backend/route-budget-error.json',
                'rendered routing writer escapes fresh output')
        current_value, original_value = rendered_routing, original_routing
        parts = writer['field'].split('/')
        for part in parts[:-1]:
            current_value, original_value = current_value[part], original_value[part]
        require(current_value[parts[-1]] == writer['path']
                and original_value[parts[-1]] == '{output}/backend/route-budget-error.json',
                'routing writer materialization is not declared containment-only change')
        current_value[parts[-1]] = original_value[parts[-1]]
    require(rendered_routing == original_routing,
            'rendered routing algorithm knobs changed outside the containment ledger')
    gds, tool = artifacts['gds'], tools['appimage']
    require(tap_identity(gds['path'], 64 << 20) == gds
            and tap_identity(tool['path'], 2 << 30) == tool, 'joint final GDS/tool budget or identity')
    # The full C7 review has already validated the producer, selected postroute
    # round, whole RC/snapshot closure and typed final-GDS table proof. Preserve
    # its raw early report paths. Only the new T1 witnesses must be fresh final
    # GDS executions, using the SAME immutable effective foundry inventory.
    inputs = root / 'backend/foundry-first/inputs.json'
    c, post = helper.runner.configuration(inputs)
    require(post == native['postroute'], 'joint postroute result differs from C7 authority')
    effective = post['effective_foundry']
    profile_path = current('backend/postroute/effective-inputs/foundry/provenance.json')
    require(effective['schema'] == 'rusteda.immutable-foundry-profile.v1'
            and effective['source'] == files['drc_profile']
            and effective['effective'] == tap_identity(profile_path, 2 << 20),
            'joint maximal profile does not bind the C7 immutable profile')
    deck_path = profile_path.parent / 'klayout/tech/drc/rule_decks/sg13g2_maximal.drc'
    deck_id = tap_identity(deck_path, 256 << 20)
    rows = [row for row in effective['files'] if row['effective']['path'] == str(deck_path)]
    require(len(rows) == 1 and rows[0]['source'] == files['tap_latchup_deck']
            and rows[0]['effective'] == deck_id
            and all(deck_id[k] == files['tap_latchup_deck'][k] for k in ('sha256', 'bytes')),
            'joint maximal effective deck is not the exact pinned source copy')
    helper.runner.effective(c, post)
    execution = tap_producer_proof(context, artifacts, c, inputs, helper)

    require(latchup.get('schema') == 'rusteda.foundry-latchup-result.v1'
            and latchup.get('status') == 'passed' and latchup.get('complete') is True
            and type(latchup.get('violations')) is int and latchup['violations'] == 0
            and type(latchup.get('engine_exit_code')) is int and latchup['engine_exit_code'] == 0
            and latchup.get('required_rule_categories') == ['LU.a']
            and latchup.get('scope') == 'complete_foundry_maximal_additional_to_original_main'
            and latchup.get('original_main_status') == 'unchecked_required_separately',
            'extra unchanged maximal LU.a not semantically passed')
    require(latchup['gds'] == tap_maximal_gds(context, artifacts) and latchup['tool'] == tool
            and latchup['profile'] == (files['drc_profile'] if tap_early(context) else effective['effective'])
            and latchup['deck'] == (files['tap_latchup_deck'] if tap_early(context) else deck_id)
            and latchup['runner'] == files['tap_latchup_runner']
            and latchup['common_runner'] == files['tap_latchup_common'],
            'latchup GDS/tool/dependency/profile binding differs')
    require(latchup.get('findings') == {}, 'nonzero/unchecked maximal findings')
    latchup_command_path = paths['latchup'].parent / 'command.json'
    expected_latchup_command = [tool['path'], 'klayout', '-b', '-r', latchup['deck']['path']]
    for value in ('threads=2', 'run_mode=deep', 'topcell=ibex_sram_bench', 'input=' + latchup['gds']['path'],
                  'report=' + str(paths['latchup'].parent / 'drc.lyrdb'),
                  'log=' + str(paths['latchup'].parent / 'deck.log')):
        expected_latchup_command.extend(['-rd', value])
    require(tap_read(latchup_command_path) == expected_latchup_command,
            'maximal command differs from unchanged complete deck execution')
    require(Path(latchup['raw_report']['path']) == paths['latchup'].parent / 'drc.lyrdb',
            'maximal raw report is not the expected fresh output')
    categories = tap_raw_zero(latchup['raw_report'], 'LU.a', root, helper)
    require(latchup['categories'] == categories, 'maximal category inventory differs from raw report')
    main = reports['drc']
    require(main['status'] == 'passed' and main['complete'] is True
            and type(main['violations']) is int and main['violations'] == 0,
            'original complete main deck not passed')
    main_categories = tap_raw_zero(main['raw_report'], 'LU.b', root, helper)
    require(config == dict(schema='rusteda.tap-coverage.v1', gds=gds,
            workspace=str(paths['coverage_report'].parent), base_revision='empty', top='ibex_sram_bench',
            excluded_cells={'RM_IHPSG13_1P_1024x32_c2_bm_bist': 2}, limits=TAP_LIMITS),
            'coverage actual config/GDS/scope differs')
    require(controller.get('schema') == 'rusteda.tap-coverage-controller.v1'
            and controller.get('status') == 'passed' and type(controller.get('exit_code')) is int
            and controller['exit_code'] == 0 and controller['tool'] == tool
            and controller['worker'] == files['tap_coverage_worker']
            and controller['controller'] == files['tap_coverage_runner']
            and controller['config'] == ids['coverage_config']
            and controller['report'] == ids['coverage_report'], 'coverage controller binding/status differs')
    require(controller['command'] == [tool['path'], 'klayout', '-b', '-r',
            files['tap_coverage_worker']['path'], '-rd', 'config=' + ids['coverage_config']['path']],
            'coverage command is not the bound worker/config/tool')
    require(certificate.get('schema') == 'rusteda.tap-coverage-result.v1'
            and certificate.get('status') == 'passed' and certificate['gds'] == gds
            and certificate['checker'] == files['tap_coverage_worker']
            and certificate['config'] == ids['coverage_config']
            and certificate.get('geometry_scope') == 'rectilinear_polygons_unit_orthogonal_transforms_no_paths'
            and certificate.get('criterion') == 'conservative_complete_box_L1_contacted_tie_certificate'
            and certificate.get('scope') == 'nonexcluded_diffusion_contact_targets_full_well_domains'
            and certificate.get('excluded_macro_content_status') == 'unchecked_requires_separate_macro_contract_and_full_lvs'
            and certificate.get('full_foundry_latchup_status') == 'unchecked'
            and certificate['excluded_cells'] == config['excluded_cells']
            and set(certificate.get('rules', {})) == {'LU.a', 'LU.b'},
            'coverage certificate status/exact admission/scope differs')
    require(certificate['limits'] == TAP_LIMITS
            and all(type(v) is int for v in certificate['limits'].values())
            and certificate.get('hierarchy_budget_scope') == dict(
                hierarchy_edges='distinct_stored_parent_child_dependencies',
                hierarchy_instance_records_limit=TAP_LIMITS['instance_visits'],
                instance_visits='expanded_full_hierarchy_occurrences'),
            'coverage must retain original caps and corrected honest hierarchy accounting')
    work_limits = {**TAP_LIMITS, 'hierarchy_instance_records': TAP_LIMITS['instance_visits']}
    require(isinstance(certificate.get('work'), dict) and set(certificate['work']) <= set(work_limits)
            and all(type(value) is int and 0 <= value <= work_limits[name]
                    for name, value in certificate['work'].items()), 'coverage work telemetry differs')
    for rule in ('LU.a', 'LU.b'):
        row = certificate['rules'][rule]
        require(row.get('status') == 'passed' and row.get('uncertified_boxes') == []
                and all(type(row.get(k)) is int and row[k] > 0 for k in
                        ('domains', 'target_polygons', 'tie_points')), 'nonvacuous coverage missing: ' + rule)
    require(type(certificate.get('max_distance_dbu')) is int
            and certificate['max_distance_dbu'] > 0
            and Decimal(certificate['dbu_um']).is_finite()
            and Decimal(certificate['dbu_um']) > 0
            and Decimal(certificate['dbu_um']) * certificate['max_distance_dbu'] == Decimal(20),
            'certificate distance is not the hard20um foundry rule')
    heads = {}
    for name in ('latchup', 'coverage_report'):
        head = paths[name].parent / 'HEAD'
        record = tap_identity(head, 128)
        with head.open('rb') as stream:
            raw = stream.read(129)
        require(raw == b'rev1\n' and tap_identity(head, 128) == record,
                'joint extra workspace HEAD is not the actual completed empty-base child')
        heads[name] = record
    # Original device-level LVS and macro contract remain separately mandatory
    # in the original qualifier before any derived gate can be emitted.
    require(reports['lvs']['macro_devices_verified'] is True,
            'name-excluded macro content obligation still requires complete device LVS')
    return dict(status='passed', policy=files['tap_policy'], final_gds=gds,
                source_pins={name: files[name] for name in sorted(required_roles)}, tool=tool,
                actual_producer=execution, effective_profile=effective['effective'],
                completed_workspaces=heads,
                source_profile=files['drc_profile'], effective_deck=deck_id, source_deck=files['tap_latchup_deck'],
                reports=ids, maximal_raw_report=latchup['raw_report'], original_main_raw_report=main['raw_report'],
                maximal_command=tap_identity(latchup_command_path, 1 << 20),
                routing_template=files['routing'], rendered_routing=tap_identity(routing_path, 1 << 20),
                routing_writer_audit=ids['routing_audit'],
                required_maximal_category='LU.a', required_original_main_category='LU.b',
                maximal_category_count=len(categories), main_category_count=len(main_categories),
                certificate_scope=certificate['scope'], excluded_macro_content_status=certificate['excluded_macro_content_status'],
                macro_obligation='separate original macro contract and complete device LVS required and retained')


def foundry_first_review(context, native, artifacts, route, inspect, cts, reports=None, current=None):
    files = context['files']
    record = files['foundry_first_verifier']
    require(identity(Path(record['path'])) == record, 'foundry-first verifier source changed')
    namespace = 'rusteda_foundry_review_' + files['foundry_first_sources']['sha256']
    require(not any(namespace + suffix in sys.modules for suffix in ('', '.runner', '.protocol', '.verify')),
            'foundry-first review namespace conflict')
    package = types.ModuleType(namespace)
    package.__path__ = [str(Path(record['path']).parent)]
    sys.modules[namespace] = package
    spec = importlib.util.spec_from_file_location(namespace + '.verify', record['path'])
    require(spec is not None and spec.loader is not None, 'foundry verifier loader')
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    try:
        spec.loader.exec_module(helper)
        result = helper.qualification_review(context, native, artifacts, route, inspect, cts)
        if context['schema'] == TAP_SCHEMA:
            # This is reached only after the complete unchanged C7 typed proof.
            # The loaded verifier/protocol/runner are already source-bundle pinned.
            result = (*result, verify_tap_checks(context, native, artifacts, reports, current, helper))
    finally:
        for suffix in ('.verify', '.runner', '.protocol', ''):
            sys.modules.pop(namespace + suffix, None)
    require(identity(Path(record['path'])) == record, 'foundry-first verifier changed during qualification')
    return result


def qualify(context, output):
    joint_tap = context.get('schema') == TAP_SCHEMA
    require(set(context) == {'schema', 'run_root', 'files', 'tools', 'artifacts', 'reports',
                             'native_reports', 'controls'} | ({'tap_checks'} if joint_tap else set()),
            'qualification context fields')
    require(context['schema'] in {'rusteda.native-qualification-inputs.v1', 'rusteda.native-qualification-inputs.v2',
            'rusteda.native-foundry-first-qualification-inputs.v1', TAP_SCHEMA}, 'qualification schema')
    foundry_first = context['schema'] == 'rusteda.native-foundry-first-qualification-inputs.v1' or joint_tap
    require(({name for name in context['files'] if name.startswith('tap_')} == TAP_ROLES if joint_tap
             else not any(name.startswith('tap_') for name in context['files'])), 'joint tap policy/schema mismatch')
    v2 = context['schema'] == 'rusteda.native-qualification-inputs.v2' or foundry_first
    require(foundry_first == ('foundry_first_policy' in context['files']), 'foundry policy/schema mismatch')
    require(v2 == ('antenna_group_policy' in context['files'])
            and (v2 or not V2_FILES & set(context['files'])), 'qualification version/policy mismatch')
    root = Path(context['run_root']).resolve(strict=True)

    def current(relative):
        value = Path(relative)
        require(not value.is_absolute() and value.parts and '..' not in value.parts, 'relative run artifact required')
        path = (root / value).resolve(strict=True)
        require(path.is_relative_to(root), 'historical/external dynamic artifact forbidden')
        return path

    require(len(context['files']) <= 256 and len(context['tools']) <= 32, 'input count budget')
    for group in ('files', 'tools'):
        for key, record in context[group].items():
            require(identity(Path(record['path'])) == record, 'input changed: ' + key)
    require(set(context['artifacts']) == {'gds', 'def', 'netlist_synth', 'netlist_final',
            'netlist_powered_final', 'spef_native_nom', 'spef_openrcx_nom'}, 'complete artifact set')
    artifacts = {key: identity(current(value)) for key, value in context['artifacts'].items()}
    require(set(context['reports']) == REPORTS, 'independent report set')
    reports = {name: read(current(path)) for name, path in context['reports'].items()}
    for name, report in reports.items():
        require(report['status'] in (['proven', 'passed'] if 'equivalence' in name else ['passed']),
                'independent gate not passed: ' + name)
    for name in ('drc', 'antenna', 'lvs'):
        report = reports[name]
        gds_sha = report.get('gds_sha256', report.get('gds', {}).get('sha256'))
        require(gds_sha == artifacts['gds']['sha256'] and report['complete'] is True,
                'physical evidence belongs to another/incomplete GDS: ' + name)
    for name, key in [('drc', 'drc_deck'), ('antenna', 'antenna_deck')]:
        report = reports[name]
        require(natural(report['violations']) and report['violations'] == 0
                and report['deck_sha256'] == context['files'][key]['sha256']
                and report['disabled_rules'] == (['recommended'] if name == 'drc' else []),
                'foundry rule scope/violations differs')
    lvs = reports['lvs']
    require(lvs['powered_netlist_sha256'] == artifacts['netlist_powered_final']['sha256']
            and lvs['top'] == 'ibex_sram_bench' and lvs['top_port_identity'] is True
            and lvs['macro_instances'] == lvs['reference_macro_instances'] == 2
            and lvs['macro_devices_verified'] is True and lvs['extraction_errors'] == []
            and lvs['macro_qualification_sha256'] == context['files']['sram_qualification']['sha256'],
            'complete device-level LVS evidence missing')
    for name, artifact in [('synthesis_equivalence', 'netlist_synth'),
                           ('final_equivalence', 'netlist_final')]:
        proof = reports[name]
        require(proof['status'] == 'proven' and proof['complete'] is True
                and proof['rtl_sha256'] == context['files']['rtl']['sha256']
                and proof['netlist_sha256'] == artifacts[artifact]['sha256'], 'stale/incomplete equivalence verdict')
        manifest = proof['manifest']
        require(manifest['all_rtl_inputs_and_states_quantified'] is True
                and manifest['all_gate_initial_states_covered'] is True
                and manifest['reset_assumptions'] == manifest['case_assumptions'] == manifest['initial_state_choices'] == []
                and manifest['state_pairs'] == manifest['gate_state_elements'] > 0
                and manifest['rtl_unknown_literals'] == 'X/Z-as-zero; distinct from four-state equality',
                'unqualified RTL event/state proof scope')
        cut = proof['fresh_original_cut']
        require(Path(cut['path']).resolve().is_relative_to(root)
                and identity(Path(cut['path'])) == cut, 'fresh original RTL cut missing or changed')
    simulation = reports['simulation']
    require(simulation['complete'] is True
            and simulation['netlist_sha256'] == artifacts['netlist_final']['sha256']
            and simulation['testbench'] == context['files']['tb']
            and simulation['simulator'] == context['tools']['iverilog']
            and simulation['runtime'] == context['tools']['vvp'], 'stale/incomplete vendor simulation')
    timing, parity = reports['timing'], reports['timing_accuracy']
    require(timing['scope']['netlist_sha256'] == parity['netlist_sha256'] == artifacts['netlist_final']['sha256']
            and timing['scope']['sdc_sha256'] == context['files']['sdc']['sha256'], 'stale timing inputs')
    for source, key in [('native', 'spef_native_nom'), ('openrcx', 'spef_openrcx_nom')]:
        require(parity['spef_sha256'][source] == artifacts[key]['sha256'], 'parity SPEF differs')
        require(set(timing['sources'][source]) == {'tt', 'ss'}, 'missing timing corner')
        for row in timing['sources'][source].values():
            require(row['spef_sha256'] == artifacts[key]['sha256'], 'timing SPEF differs')
    native_names = {'route', 'inspect', 'cts', 'macro_contract', 'native_extraction', 'openrcx_extraction',
                    'antenna_lineage'}
    if 'postroute' in context['native_reports']:
        native_names.add('postroute')
        require(context['native_reports']['postroute'] == 'backend/postroute/result.json',
                'post-route evidence must use the producer output path')
    if v2:
        native_names.add('antenna_verification')
        require(context['native_reports']['antenna_lineage'] == ('backend/foundry-first/decision/result.json'
                    if foundry_first else 'backend/antenna-repair/lineage.json')
                and context['native_reports']['antenna_verification'] == ('backend/foundry-verification/result.json'
                    if foundry_first else 'backend/antenna-verification/result.json'),
                'v2/explicit foundry evidence must use the current producer output paths')
    require(set(context['native_reports']) == native_names, 'native evidence set')
    native = {name: (metadata_prefix(current(path), {'schema', 'status', 'scope', 'route_scope',
                'workspace', 'tool', 'initial_revision', 'final_revision', 'initial', 'final', 'workspace_head'}, file_cap=16 << 20)
                if v2 and not foundry_first and name == 'antenna_lineage' else read(current(path), 1 << 20)
                if v2 and name == 'antenna_verification' else read(current(path)))
              for name, path in context['native_reports'].items()}
    route, inspect, cts = native['route'], native['inspect'], native['cts']
    postroute = None
    if 'postroute' in native:
        route, cts, postroute = postroute_lineage(native['postroute'], root, route, cts, context['tools'], foundry_first)
    require(inspect['stage'] == 'placed'
            and route['routing_status'] == inspect['routing_status'] == 'signal_geometry_checked'
            and natural(inspect['routed_nets']) and inspect['routed_nets'] > 0,
            'all-net native route not established')
    extract_execution = None
    tap_checks = None
    if foundry_first:
        reviewed = foundry_first_review(context, native, artifacts, route, inspect, cts, reports, current)
        if joint_tap:
            lineage, actual_extracted, extract_execution, tap_checks = reviewed
        else:
            lineage, actual_extracted, extract_execution = reviewed
    elif v2:
        lineage = antenna_lineage_v2(native['antenna_lineage'], native['antenna_verification'],
            root, route, inspect, cts, context['files'], context['tools'],
            current(context['native_reports']['antenna_lineage']))
    else:
        lineage = antenna_lineage(native['antenna_lineage'], root, route, inspect, cts,
                                  context['files'], context['tools'])
    require(cts['clock_topology_status'] == 'unrouted_topology_checked'
            and inspect['clock_topology_status'] == 'routed_geometry_checked'
            and cts['clock_buffers'] == inspect['clock_buffers'] and inspect['clock_buffers'] > 0,
            'single-CTS topology/routed evidence differs')
    extracted, external = (actual_extracted if foundry_first else native['native_extraction']), native['openrcx_extraction']
    require(extracted['schema'] == 'rustroad.extract.v1' and extracted['status'] == 'ok'
            and extracted['design'] == 'ibex_sram_bench'
            and extracted['workspace_revision'] == inspect['revision']
            and (extracted['output'] == artifacts['spef_native_nom'] or foundry_first
                 and extract_execution['mode'] == 'artifact_reuse'
                 and extracted['output']['sha256'] == artifacts['spef_native_nom']['sha256']
                 and extracted['output']['bytes'] == artifacts['spef_native_nom']['bytes'])
            and extracted['scope']['corner'] == 'nom'
            and extracted['unannotated_signal_nets'] == [],
            'native extraction belongs to another/partially routed design')
    require(external['status'] == 'extracted_not_qualified' and external['extraction_finished'] is True
            and external['top'] == 'ibex_sram_bench' and external['rc_corner'] == 'nom'
            and external['source'] == 'openrcx'
            and external['spef'] == artifacts['spef_openrcx_nom']
            and external['files']['def'] == artifacts['def'], 'OpenRCX source DEF or SPEF differs')
    calibration = calibration_evidence(context['files'], root, extracted, external, joint_tap)
    macro = native['macro_contract']
    require(macro['status'] == 'passed' and macro['macro_count'] == 2
            and macro['instances'] == ['u_dmem', 'u_imem'] and macro['errors'] == [], 'macro interface failed')
    placement = placed_macros(current(context['artifacts']['def']),
                              read(Path(context['files']['contract']['path']), 1 << 20)['common']['macros'])
    density = final_density(context, current(context['artifacts']['def']))
    if density is not None:
        placement['density'] = density
    controls_path = Path(context['controls']).resolve(strict=True)
    require(any(Path(record['path']) == controls_path for record in context['files'].values()),
            'component controls must be a pinned immutable input')
    controls = read(controls_path)
    require(controls['schema'] == 'rusteda.ihp-component-qualification.v1'
            and controls['status'] == 'passed'
            and {'sky130', 'gf180mcu'} <= set(controls['baseline_pdks']), 'baseline controls not qualified')
    for tool in ('rustsys', 'ruststa', 'rustroad', 'rusteda'):
        require(controls['tools'][tool] == context['tools'][tool], 'controls refer to another tool: ' + tool)
    required_cases = {'rtl_mutation', 'macro_byte_mask', 'sram_width', 'sram_layer', 'sram_short',
                      'power_open', 'welltap_missing', 'offgrid_metal', 'antenna_unit',
                      'stale_evidence', 'bounded_work', 'sky130_sta', 'gf180mcu_sta'}
    require(required_cases <= set(controls['cases']), 'missing independent negative/baseline controls')
    for name in required_cases:
        case = controls['cases'][name]
        expected = 'matched' if name in {'sky130_sta', 'gf180mcu_sta'} else 'rejected'
        require(case['status'] == 'passed' and case['observed_outcome'] == expected
                and case['expected_outcome_verified'] is True,
                'control did not demonstrate its expected outcome: ' + name)
        require(identity(Path(case['evidence']['path'])) == case['evidence'], 'control evidence changed')
    dependencies = {name: identity(current(path)) for name, path in context['reports'].items()}
    native_dependencies = {name: identity(current(path)) for name, path in context['native_reports'].items()}
    common = dict(status='passed', artifacts=artifacts, independent_reports=dependencies,
                  native_reports=native_dependencies)
    derived = dict(
        inputs_frozen=dict(**common, files=context['files'], tools=context['tools']),
        macros=dict(**common, interface=macro, physical=placement['macros'],
                    macro_instances=2, macro_devices_verified=True),
        placement=dict(**common, **placement, basis='native placement plus complete foundry DRC/device LVS'),
        cts=dict(**common, clock_buffers=inspect['clock_buffers'],
                 scope='single native CTS topology, routed geometry and actual propagated timing'),
        power_connectivity=dict(**common, scope='complete extracted-device LVS including body ties, SRAM and DECAP',
                                extraction_errors=[]),
        routing=dict(**common, complete=True, scope='all', unrouted_signal_nets=0,
                     unconnected_signal_pins=0, routed_nets=inspect['routed_nets'],
                     antenna_revision_lineage=lineage, postroute_repair=postroute,
                     basis='native all-signal route and verified antenna child revisions plus independent complete LVS and RC annotation'),
        extraction=dict(**common, complete=True, rc_corner='nom', spef_sources=['native', 'openrcx'],
                        resistance_model='openrcx-pdk', netlist_sha256=artifacts['netlist_final']['sha256'],
                        calibration_evidence=calibration,
                        spef_sha256={source: artifacts[key]['sha256'] for source, key in
                        [('native', 'spef_native_nom'), ('openrcx', 'spef_openrcx_nom')]},
                        scope='separate native/OpenRCX extraction; full active annotation checked by both timing engines'),
        controls=dict(**common, qualification=identity(controls_path)))
    if foundry_first:
        derived['routing']['basis'] = ('native all-signal route; independently verified unchanged revision or actual legacy repair; '
            'complete original foundry/LVS and RC annotation obligations retained')
        derived['extraction']['native_execution'] = extract_execution
        derived['extraction']['scope'] = ('actual current-run native extraction or proven identical-whole-input artifact reuse; '
            'independent OpenRCX extraction; full active annotation checked by both timing engines')
    if joint_tap:
        derived['controls']['tap_checks'] = tap_checks
    require(set(derived) == DERIVED, 'internal derived gate set')
    for name, report in derived.items():
        write(output / (name + '.json'), report)
    result = dict(status='passed', scope='current native run, all independent gates and input bindings',
                  logic_scope='two-state X/Z-as-zero original RTL events; four-state refinement unverified',
                  artifacts=artifacts, independent_reports=dependencies,
                  native_reports=native_dependencies,
                  derived_gates={name: identity(output / (name + '.json')) for name in derived},
                  benchmark='unverified')
    if joint_tap:
        result['tap_checks'] = tap_checks
    write(output / 'result.json', result)
    return result


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
        result = qualify(read(args.inputs), args.workspace)
        (args.workspace / 'HEAD').write_text('rev1\n')
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        raise SystemExit(2)

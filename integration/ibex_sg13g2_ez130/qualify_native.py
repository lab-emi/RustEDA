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
from pathlib import Path
import re
import resource
import sys


DERIVED = {'inputs_frozen', 'macros', 'placement', 'cts', 'power_connectivity',
           'routing', 'extraction', 'controls'}
REPORTS = {'drc', 'antenna', 'lvs', 'timing', 'timing_accuracy',
           'synthesis_equivalence', 'final_equivalence', 'simulation'}
CONTRACT_SHA256 = 'a58f53feb169724d987f774c3143083eee373806df251eb4164ca1ec82566ed8'
CONTRACT_BYTES = 5666
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


def calibration_evidence(files, root, extracted, external):
    contract_id = files['contract']
    require(contract_id == identity(Path(contract_id['path']))
            and contract_id['sha256'] == CONTRACT_SHA256
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
    require(isinstance(receipts, list) and len(receipts) <= 128, 'producer receipt budget')
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


def postroute_lineage(result, root, route, cts, tools):
    """Bind the post-route runner: trial = the be_route revision of the frontend
    final workspace; final = a file-for-file trial copy (route/CTS records stay
    the trial's) or an ECO rebuild whose own CTS/route records replace them."""
    require(result['schema'] == 'rusteda.native-postroute-result.v1'
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


def qualify(context, output):
    require(set(context) == {'schema', 'run_root', 'files', 'tools', 'artifacts', 'reports',
                             'native_reports', 'controls'}, 'qualification context fields')
    require(context['schema'] in {'rusteda.native-qualification-inputs.v1', 'rusteda.native-qualification-inputs.v2'}, 'qualification schema')
    v2 = context['schema'] == 'rusteda.native-qualification-inputs.v2'
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
        require(context['native_reports']['antenna_lineage'] == 'backend/antenna-repair/lineage.json'
                and context['native_reports']['antenna_verification'] == 'backend/antenna-verification/result.json',
                'v2 evidence must use the current producer output paths')
    require(set(context['native_reports']) == native_names, 'native evidence set')
    native = {name: (metadata_prefix(current(path), {'schema', 'status', 'scope', 'route_scope',
                'workspace', 'tool', 'initial_revision', 'final_revision', 'initial', 'final', 'workspace_head'}, file_cap=16 << 20)
                if v2 and name == 'antenna_lineage' else read(current(path), 1 << 20)
                if v2 and name == 'antenna_verification' else read(current(path)))
              for name, path in context['native_reports'].items()}
    route, inspect, cts = native['route'], native['inspect'], native['cts']
    postroute = None
    if 'postroute' in native:
        route, cts, postroute = postroute_lineage(native['postroute'], root, route, cts, context['tools'])
    require(inspect['stage'] == 'placed'
            and route['routing_status'] == inspect['routing_status'] == 'signal_geometry_checked'
            and natural(inspect['routed_nets']) and inspect['routed_nets'] > 0,
            'all-net native route not established')
    if v2:
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
    extracted, external = native['native_extraction'], native['openrcx_extraction']
    require(extracted['schema'] == 'rustroad.extract.v1' and extracted['status'] == 'ok'
            and extracted['design'] == 'ibex_sram_bench'
            and extracted['workspace_revision'] == inspect['revision']
            and extracted['output'] == artifacts['spef_native_nom']
            and extracted['scope']['corner'] == 'nom'
            and extracted['unannotated_signal_nets'] == [],
            'native extraction belongs to another/partially routed design')
    require(external['status'] == 'extracted_not_qualified' and external['extraction_finished'] is True
            and external['top'] == 'ibex_sram_bench' and external['rc_corner'] == 'nom'
            and external['source'] == 'openrcx'
            and external['spef'] == artifacts['spef_openrcx_nom']
            and external['files']['def'] == artifacts['def'], 'OpenRCX source DEF or SPEF differs')
    calibration = calibration_evidence(context['files'], root, extracted, external)
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
    require(set(derived) == DERIVED, 'internal derived gate set')
    for name, report in derived.items():
        write(output / (name + '.json'), report)
    result = dict(status='passed', scope='current native run, all independent gates and input bindings',
                  logic_scope='two-state X/Z-as-zero original RTL events; four-state refinement unverified',
                  artifacts=artifacts, independent_reports=dependencies,
                  native_reports=native_dependencies,
                  derived_gates={name: identity(output / (name + '.json')) for name in derived},
                  benchmark='unverified')
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

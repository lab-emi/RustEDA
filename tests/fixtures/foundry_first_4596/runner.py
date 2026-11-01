#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit opt-in orchestration; all EDA verdicts are external actual outputs.

Controller<128MiB,2MiB metadata records,64KiB streaming; no geometry decode.
Prechecks use the producer's bounded DAG/owned process groups, never new scopes.
"""
import argparse
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

try:
    from . import protocol as p
except ImportError:
    import protocol as p

ROLES = {'foundry_first_runner': 'runner.py', 'foundry_first_protocol': 'protocol.py',
         'foundry_first_verifier': 'verify.py', 'foundry_first_policy': 'policy.json',
         'foundry_first_scopes': 'scopes.json'}
SOURCE_ROLE = 'foundry_first_sources'


def bound_sources(files):
    present = set(ROLES) | {SOURCE_ROLE}
    if 'foundry_first_policy' not in files:
        p.need(not present & set(files), 'FOUNDRY_FIRST_REQUIRES_POLICY')
        return False
    p.need(present <= set(files), 'FOUNDRY_FIRST_SOURCES_INCOMPLETE')
    manifest = p.read(p.record(files[SOURCE_ROLE]))
    p.need(manifest['schema'] == 'rusteda.foundry-first-source-bundle.v1'
           and set(manifest) == {'schema', 'sources'}, 'FOUNDRY_FIRST_SOURCE_SCHEMA')
    rows = manifest['sources']
    p.need(isinstance(rows, dict) and set(rows) == set(ROLES), 'FOUNDRY_FIRST_SOURCE_ROLES')
    parent = None
    for role, name in ROLES.items():
        path = p.record(files[role], 1 << 20)
        p.need(rows[role] == files[role] and path.name == name, 'FOUNDRY_FIRST_SOURCE_CHANGED')
        parent = parent or path.parent
        p.need(path.parent == parent, 'FOUNDRY_FIRST_SOURCE_DIRECTORY')
    p.validate_policy(p.read(files['foundry_first_policy']['path']))
    return True


def compose(manifest):
    """Transform only a fully validated legacy manifest, with an explicit pin."""
    files = manifest['files']
    if not bound_sources(files):
        return manifest
    p.need('antenna_session_policy' in files and 'antenna_group_policy' in files,
           'FOUNDRY_FIRST_REQUIRES_QUALIFIED_LEGACY_FALLBACK')
    manifest = dict(manifest)
    steps = copy.deepcopy(manifest['steps'])
    manifest['steps'] = steps
    by = {v['id']: v for v in steps}
    p.need(len(by) == len(steps) and all(k in by for k in ('be_postroute_inputs', 'be_postroute',
        'be_antenna_inputs', 'be_antenna_repair', 'be_antenna_verify_inputs', 'be_antenna_verify',
        'be_gds', 'be_extract', 'be_main', 'be_antenna', 'nf_qualification_inputs')), 'FOUNDRY_FIRST_RECIPE')
    old = by['be_antenna_inputs']['content']
    p.need(old['schema'] == 'rusteda.native-antenna-inputs.v2' and old['initial_revision'] == 'rev4',
           'FOUNDRY_FIRST_INITIAL_ROUTE')
    post = by['be_postroute_inputs']['content']
    p.need(post['schema'] == 'rusteda.native-postroute-inputs.v1', 'FOUNDRY_FIRST_POSTROUTE')
    post['schema'] = 'rusteda.native-postroute-reuse-inputs.v1'
    by['be_postroute']['arguments'].append('--foundry-first')
    post['foundry_first'] = dict(protocol={'$input': 'foundry_first_protocol'},
        policy={'$input': 'foundry_first_policy'}, cell_gds={'$identity': 'backend/profiles/combined-cells.gds'},
        layer_map={'$identity': 'backend/profiles/gds-map.json'}, drc_profile={'$input': 'drc_profile'}, cell_lef={'$input': 'cell_lef'}, run_root='{output}')
    effective = '{output}/backend/postroute/effective-inputs/'
    argv = ['{tools:rustroad}', *by['be_extract']['arguments']]
    p.need(argv.count('--rc-profile') == 1, 'FOUNDRY_FIRST_EXTRACT_PROFILE')
    argv[argv.index('--rc-profile') + 1] = effective + 'rc-profile.json'
    base = '{output}/backend/foundry-first/'
    cfg = base + 'inputs.json'
    final_gds = '{output}/' + manifest['artifacts']['gds']
    configuration = dict(schema='rusteda.foundry-first-inputs.v1', run_root='{output}',
        workspace=old['design_workspace'], initial_revision='rev4', legacy_inputs=old,
        postroute_result={'$identity': 'backend/postroute/result.json'},
        files={role: {'$input': role} for role in [*ROLES, SOURCE_ROLE, 'drc_runner',
            'drc_profile', 'drc_deck', 'antenna_deck', 'antenna_runner', 'antenna_verifier',
            'antenna_source_manifest', 'postroute_runner', 'producer', 'cell_lef']},
        tools={role: {'$tool': role} for role in ('python', 'rustroad', 'appimage', 'gnu_time')},
        initial_gds=base + 'initial.gds', final_gds=final_gds,
        effective_inputs=effective, extract_argv=argv, scope=manifest['scope'])
    by['be_antenna_inputs'].update(destination='backend/foundry-first/inputs.json', content=configuration)
    export = dict(by['be_gds'])
    export['id'] = 'be_ff_export'
    export['arguments'] = list(export['arguments'])
    for flag, value in [('--out', configuration['initial_gds']), ('--cell-gds', effective + 'cells.gds'),
                        ('--layer-map', effective + 'layer-map.json')]:
        p.need(export['arguments'].count(flag) == 1, 'FOUNDRY_FIRST_GDS_FLAGS')
        export['arguments'][export['arguments'].index(flag) + 1] = value
        if flag != '--out':
            by['be_gds']['arguments'][by['be_gds']['arguments'].index(flag) + 1] = value
    if '--cell-lef' in export['arguments']:
        p.need(export['arguments'].count('--cell-lef') == 1, 'FOUNDRY_FIRST_CELL_LEF')
        export['arguments'][export['arguments'].index('--cell-lef') + 1] = effective + 'cells.lef'
        by['be_gds']['arguments'][by['be_gds']['arguments'].index('--cell-lef') + 1] = effective + 'cells.lef'
    else:
        export['arguments'].extend(['--cell-lef', effective + 'cells.lef'])
        by['be_gds']['arguments'].extend(['--cell-lef', effective + 'cells.lef'])
    configuration['export_argv'] = ['{tools:rustroad}', *export['arguments']]
    extra = [export]
    for table in ('main', 'antenna'):
        extra.append(dict(id='be_ff_' + table, kind='command', tool='python', timeout_s=1260,
            arguments=['{files:foundry_first_runner}', 'precheck', '--table', table, '--inputs', cfg,
                       '--workspace', base + 'pre-' + table, '--base-rev', 'empty']))
        by['be_' + table].update(tool='python', arguments=['{files:foundry_first_runner}',
            'final-table', '--table', table, '--inputs', cfg, '--workspace',
            '{output}/backend/' + table, '--base-rev', 'empty'], timeout_s=1260)
    by['be_antenna_repair'].update(arguments=['{files:foundry_first_runner}', 'decide', '--inputs', cfg,
        '--workspace', base + 'decision', '--base-rev', 'empty'], timeout_s=1860)
    by['be_antenna_verify'].update(arguments=['{files:foundry_first_verifier}', '--inputs', cfg,
        '--workspace', '{output}/backend/foundry-verification', '--base-rev', 'empty'])
    by['be_extract'].update(tool='python', arguments=['{files:foundry_first_runner}', 'extract',
        '--inputs', cfg, '--workspace', '{output}/backend/foundry-extraction', '--base-rev', 'empty'])
    context = by['nf_qualification_inputs']['content']
    context['schema'] = 'rusteda.native-foundry-first-qualification-inputs.v1'
    context['native_reports']['antenna_lineage'] = 'backend/foundry-first/decision/result.json'
    context['native_reports']['antenna_verification'] = 'backend/foundry-verification/result.json'
    result = []
    for step in steps:
        if step['id'] == 'be_antenna_verify_inputs':
            continue
        result.append(step)
        if step['id'] == 'be_antenna_inputs':
            result.extend(extra)
    p.need(len(result) <= 128, 'FOUNDRY_FIRST_STEP_BUDGET')
    manifest['steps'] = result
    threads = manifest['scope']['threads']
    p.need(type(threads) is int and threads in (2, 16), 'FOUNDRY_FIRST_CPU_ALLOCATION')
    group = dict(max_parallel=2, max_memory_mib=7168, steps={
        'be_ff_main': dict(depends_on=[], cpu_slots=[0, 1], memory_mib=3456),
        'be_ff_antenna': dict(depends_on=[], cpu_slots=[2, 3] if threads >= 4 else [0, 1], memory_mib=3456)})
    groups = [group, *manifest.get('parallel_groups', [])]
    assigned = {tag for value in groups for tag in value['steps']}
    # Every new controller that can spawn a child runs in an existing producer
    # owned PGID with unconditional descendant cleanup, even for one-node jobs.
    for tag, reservation, slots in [
        ('be_postroute', 7168, list(range(threads))),
        ('be_antenna_repair', 7168, list(range(threads))),
        ('be_antenna_verify', 2304, [0, 1]),
        ('be_extract', 7168, list(range(threads))),
        ('be_main', 3456, [0, 1]),
        ('be_antenna', 3456, [2, 3] if threads >= 4 else [0, 1])]:
        if tag not in assigned:
            groups.append(dict(max_parallel=1, max_memory_mib=7168,
                steps={tag: dict(depends_on=[], cpu_slots=slots, memory_mib=reservation)}))
    p.need(len(groups) <= 8, 'FOUNDRY_FIRST_GROUP_BUDGET')
    manifest['parallel_groups'] = sorted(groups, key=lambda value:
        min(i for i, step in enumerate(result) if step['id'] in value['steps']))
    return manifest


def configuration(path):
    c = p.read(path)
    p.need(set(c) == {'schema', 'run_root', 'workspace', 'initial_revision', 'legacy_inputs',
        'postroute_result', 'files', 'tools', 'initial_gds', 'final_gds', 'effective_inputs',
        'extract_argv', 'export_argv', 'scope'} and c['schema'] == 'rusteda.foundry-first-inputs.v1', 'FOUNDRY_FIRST_INPUT_SCHEMA')
    bound_sources(c['files'])
    root = p.real_path(c['run_root'])
    p.need(Path(path).resolve().is_relative_to(root), 'FOUNDRY_FIRST_INPUT_OUTSIDE_RUN')
    for item in c['files'].values():
        p.record(item)
    for item in c['tools'].values():
        p.record(item)
    p.need(c['initial_revision'] == 'rev4' and type(c['scope']['memory_mib']) is int
           and c['scope']['memory_mib'] == 8192 and type(c['scope']['threads']) is int
           and c['scope']['threads'] in (2, 16), 'FOUNDRY_FIRST_SCOPE')
    post = p.read(p.record(c['postroute_result']))
    p.need(post['schema'] == 'rusteda.native-postroute-reuse-result.v1', 'FOUNDRY_FIRST_POSTROUTE_SCHEMA')
    for key in ('workspace', 'initial_gds', 'final_gds', 'effective_inputs'):
        path_value = Path(c[key])
        p.need(path_value.is_absolute() and '..' not in path_value.parts and path_value.is_relative_to(root),
               'FOUNDRY_FIRST_WRITER_CONTAINMENT')
    p.need(post['final_workspace'] == c['workspace'] and post['final_revision'] == 'rev4',
           'FOUNDRY_FIRST_POSTROUTE_BINDING')
    expected_paths = dict(workspace='backend/postroute/road', initial_gds='backend/foundry-first/initial.gds',
        final_gds='backend/ibex_sram_bench.gds', effective_inputs='backend/postroute/effective-inputs')
    for key, suffix in expected_paths.items():
        p.need(Path(c[key]) == root / suffix, 'FOUNDRY_FIRST_FIXED_PATH')
    p.need(c['postroute_result']['path'] == str(root / 'backend/postroute/result.json')
           and post['input_manifest']['path'] == str(root / 'backend/postroute-inputs.json'), 'POSTROUTE_RECEIPT_PATH')
    for argv, operation in [(c['extract_argv'], 'extract'), (c['export_argv'], 'export-gds')]:
        p.need(isinstance(argv, list) and 1 <= len(argv) <= 128
               and all(isinstance(value, str) for value in argv)
               and argv[:3] == [c['tools']['rustroad']['path'], '--workspace', c['workspace']]
               and argv.count(operation) == 1 and argv.count('--out') == 1, 'ACTUAL_NATIVE_ARGV')
    p.need(c['extract_argv'][c['extract_argv'].index('--out') + 1] == str(root / 'backend/native.nom.spef')
           and c['extract_argv'].count('--rc-profile') == 1
           and c['extract_argv'][c['extract_argv'].index('--rc-profile') + 1] ==
               str(root / 'backend/postroute/effective-inputs/rc-profile.json'), 'EXTRACT_EFFECTIVE_PATH')
    for flag, name in [('--cell-gds', 'cells.gds'), ('--layer-map', 'layer-map.json'), ('--cell-lef', 'cells.lef')]:
        p.need(c['export_argv'].count(flag) == 1 and c['export_argv'][c['export_argv'].index(flag) + 1]
               == str(root / 'backend/postroute/effective-inputs' / name), 'EXPORT_EFFECTIVE_PATH')
    return c, post


def product_guard(c):
    """Require the existing enclosing worker guard; never create a child scope."""
    p.need(re.fullmatch('[0-9a-f]{64}', os.environ.get('RUSTEDA_WORKER_ID', ''))
           and os.environ.get('RUSTEDA_OWNERSHIP'), 'PRODUCT_OWNERSHIP_REQUIRED')
    with Path('/proc/self/cgroup').open() as stream:
        entries = stream.read(4097)
    p.need(len(entries) <= 4096, 'CGROUP_METADATA_BUDGET')
    rows = [row[3:] for row in entries.splitlines() if row.startswith('0::')]
    p.need(len(rows) == 1 and '..' not in Path(rows[0]).parts, 'CGROUP_V2_REQUIRED')
    folder = Path('/sys/fs/cgroup') / rows[0].lstrip('/')
    values = []
    for name, maximum in [('memory.max', 8 << 30), ('memory.swap.max', 4 << 30)]:
        with (folder / name).open() as stream:
            raw = stream.read(33).strip()
        p.need(len(raw) <= 32 and raw.isdecimal() and 0 <= int(raw) <= maximum, 'PRODUCT_MEMORY_GUARD_REQUIRED')
        values.append(int(raw))
    p.need(values[0] > 0 and type(c['scope']['memory_mib']) is int
           and c['scope']['memory_mib'] == 8192, 'PRODUCT_MEMORY_ALLOCATION')
    return values[0]


def command(c, output, label, argv, seconds, cap=2 << 20):
    """Same producer-owned PGID/cgroup; no daemon, session or child scope."""
    p.need(len(argv) <= 256 and type(seconds) is int and 1 <= seconds <= 5400,
           'CHILD_COMMAND_BUDGET')
    product_guard(c)
    argv = [str(a) for a in argv]
    timer = output / (label + '.time')
    actual = [c['tools']['gnu_time']['path'], '-v', '-o', str(timer), *argv]
    stdout, stderr = output / (label + '.stdout'), output / (label + '.stderr')
    tick = time.monotonic()
    error = None
    env = dict(os.environ, RAYON_NUM_THREADS=str(c['scope']['threads']))
    p.need(not env.get('RUSTROAD_RULE_DUMP'), 'EXTERNAL_DIAGNOSTIC_WRITER')
    with stdout.open('xb') as so, stderr.open('xb') as se:
        child = subprocess.Popen(actual, stdin=subprocess.DEVNULL, stdout=so, stderr=se, env=env)
        # Fixed deadline/capture limits. On leader termination the enclosing
        # producer kills remaining members of its owned PGID. Existing legacy
        # session fallback retains its own bounded native-child cleanup contract;
        # every child still inherits this product cgroup and physical memory cap.
        for _ in range(seconds * 10 + 2):
            if stdout.stat().st_size > cap or stderr.stat().st_size > 64 << 20:
                error = 'capture_budget'
            if time.monotonic() - tick >= seconds:
                error = 'deadline'
            if error:
                child.kill()
                child.wait(timeout=10)
                break
            try:
                child.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            child.kill()
            child.wait(timeout=10)
            error = 'poll_work_budget'
    return dict(argv=argv, timed_argv=actual, exit_code=child.returncode,
                wall_seconds=time.monotonic() - tick, stdout=p.identity(stdout), stderr=p.identity(stderr),
                time=p.identity(timer) if timer.exists() else None, capture_error=error)


def effective(c, post):
    owner = Path(c['workspace']).parent
    p.immutable(post['effective_rc']['effective'], owner, 16 << 20)
    for value in post['effective_exports'].values():
        p.immutable(value['effective'], owner, 64 << 20)
    profile = post['effective_foundry']
    p.immutable(profile['effective'], owner, 2 << 20)
    p.need(len(profile['files']) <= 4096, 'FOUNDRY_PROFILE_FILE_COUNT')
    for value in profile['files']:
        p.immutable(value['effective'], owner, 256 << 20)
    return profile['effective']


def precheck(c, post, inputs, output, table):
    p.need(table in ('main', 'antenna'), 'PRECHECK_TABLE')
    profile = effective(c, post)
    gds = p.seal_owned(c['initial_gds'], c['run_root'], p.POLICY['max_gds_bytes'])
    reservation = 48 * gds['bytes'] + (256 << 20) + (64 << 20)
    if gds['bytes'] > p.POLICY['max_gds_bytes'] or reservation > 3456 << 20:
        return dict(schema='rusteda.foundry-precheck.v1', status='unverified', reason='memory_admission',
                    table=table, input_manifest=p.identity(inputs), gds=gds, complete=False,
                    violations=None, actual_foundry_processes=0, checker_command_attempts=0, command=None)
    argv = [c['tools']['python']['path'], c['files']['drc_runner']['path'], '--gds', gds['path'],
            '--profile', profile['path'], '--tool', c['tools']['appimage']['path'], '--table', table,
            '--workspace', str(output / 'raw'), '--base-rev', 'empty']
    row = command(c, output, 'original-checker', argv, 1200)
    summary = output / 'raw/summary.json'
    result = p.read(summary) if summary.is_file() and summary.stat().st_size <= 2 << 20 else None
    complete = (row['capture_error'] is None and type(row['exit_code']) is int
                and row['exit_code'] in (0, 1) and result is not None and result.get('complete') is True
                and result.get('engine_exit_code') == 0)
    reason = None
    if complete:
        try:
            if __package__:
                from .verify import raw_foundry
            else:
                from verify import raw_foundry
            raw_foundry(p.identity(summary), c, post, table, gds)
        except (OSError, ValueError, KeyError, TypeError, SyntaxError) as error:
            complete = False
            reason = 'scope_or_source_binding: ' + str(error)[:4096]
    known_execution = (result is not None and type(result.get('engine_exit_code')) is int
                       and result.get('tool') == c['tools']['appimage']
                       and result.get('gds') == gds and result.get('profile') == profile)
    return dict(schema='rusteda.foundry-precheck.v1', status='observed' if complete else 'unverified',
                table=table, input_manifest=p.identity(inputs), gds=gds, complete=complete,
                violations=result['violations'] if complete else None, actual_foundry_processes=1 if known_execution else None,
                checker_command_attempts=1,
                unverified_reason=reason, command=row, raw_summary=p.identity(summary) if summary.is_file() else None)


def decision(c):
    return p.read(Path(c['initial_gds']).parent / 'decision/result.json')


def decide(c, post, inputs, output, table):
    effective(c, post)
    initial = p.identity(c['initial_gds'])
    checks = {}
    clean = True
    for key in ('main', 'antenna'):
        path = Path(c['initial_gds']).parent / ('pre-' + key) / 'result.json'
        p.need(not path.parent.is_symlink() and not path.is_symlink(), 'PRECHECK_RECEIPT_SYMLINK')
        if path.parent.exists():
            p.real_path(path.parent)
        if not path.exists():
            checks[key] = None
            clean = False
            continue
        item = checks[key] = p.identity(path)
        d = p.read(p.record(item))
        clean &= (d['schema'] == 'rusteda.foundry-precheck.v1' and d['table'] == key
                  and d['gds'] == initial and d['input_manifest'] == p.identity(inputs)
                  and d['complete'] is True and type(d['violations']) is int and d['violations'] == 0)
    try:
        before = p.snapshot(c['workspace'])
    except p.Miss:
        before = None
        clean = False
    result = dict(schema='rusteda.foundry-first-decision.v1', input_manifest=p.identity(inputs),
        initial_gds=initial, checks=checks, initial_revision=c['initial_revision'],
        workspace=c['workspace'], initial_snapshot=before, native_process_scope='antenna_controller_only',
        requires_full_revalidation=True, whole_layout_antenna_status='unchecked')
    if clean:
        p.need(before['revision'] == c['initial_revision'], 'REVISION_CONFLICT')
        result.update(mode='foundry_clean_keep_revision', final_revision=before['revision'],
                      final_snapshot=before, native_analysis_status='unchecked',
                      unresolved_supported_nets=None, actual_native_processes=0)
    else:
        legacy = output / 'legacy-inputs.json'
        p.write_new(legacy, c['legacy_inputs'])
        argv = [c['tools']['python']['path'], c['files']['antenna_runner']['path'], '--inputs', str(legacy),
                '--workspace', str(Path(c['run_root']) / 'backend/antenna-repair'), '--base-rev', 'empty']
        row = command(c, output, 'legacy-repair', argv, 1860)
        p.need(row['capture_error'] is None and type(row['exit_code']) is int and row['exit_code'] == 0,
               'LEGACY_REPAIR_FAILED')
        lineage = p.identity(Path(c['run_root']) / 'backend/antenna-repair/lineage.json')
        raw = p.read(p.record(lineage), 16 << 20)
        try:
            after = p.snapshot(c['workspace'])
        except p.Miss:
            after = None
        result.update(mode='legacy_repair', legacy_inputs=p.identity(legacy), legacy_command=row,
            legacy_lineage=lineage, final_revision=raw['final_revision'], final_snapshot=after,
            native_analysis_status=raw['native_analysis_status'],
            unresolved_supported_nets=raw['unresolved_supported_nets'], actual_native_processes=1)
    return result


def extract(c, post, inputs, output, table):
    d = decision(c)
    effective(c, post)
    argv = c['extract_argv']
    destination = p.private_path(argv[argv.index('--out') + 1], c['run_root'])
    p.need(not destination.exists() and not destination.is_symlink()
           and destination.is_relative_to(Path(c['run_root'])), 'SPEF_DESTINATION')
    with p.shared(c['workspace']) as locked:
        try:
            current = p.snapshot_locked(locked)
        except p.Miss:
            current = None
        p.need(current == d['final_snapshot'], 'FINAL_SNAPSHOT_CHANGED')
        captured = None
        miss = 'repair_requires_full_extraction'
        if d['mode'] == 'foundry_clean_keep_revision':
            p.need(current is not None, 'CLEAN_SNAPSHOT_UNSUPPORTED')
            captures = post.get('extract_inputs', [])
            p.need(isinstance(captures, list) and len(captures) <= 4, 'PRIOR_EXTRACTION_COUNT')
            candidate = None
            try:
                if captures:
                    candidate = p.read(p.record(captures[-1]))
            except FileNotFoundError:
                pass
            miss = 'missing_capture_receipt' if candidate is None else 'unsupported_or_changed_complete_inputs'
            if candidate is not None and candidate['schema'] == 'rusteda.actual-full-extract-inputs.v1':
                source_input = p.read(p.record(post['input_manifest']))
                p.selected_capture_request(candidate, source_input, post, c['tools']['rustroad'])
                p.need(isinstance(post['commands'], list) and len(post['commands']) <= 256
                       and sum(row == candidate['command'] for row in post['commands']) == 1
                       and candidate['command']['stdout'] == candidate['raw_report'], 'PRIOR_ACTUAL_COMMAND_MEMBERSHIP')
                p.need(candidate['status'] == 'captured'
                       and candidate['source_workspace'] == candidate['command']['argv'][2]
                       and Path(candidate['source_workspace']).is_relative_to(Path(c['run_root']))
                       and isinstance(post['rounds'], list) and 1 <= len(post['rounds']) <= 4
                       and post['rounds'][-1]['workspace'] == candidate['source_workspace']
                       and post['rounds'][-1]['check']['spef'] == candidate['output'], 'PRIOR_FINAL_SELECTED_CHECK')
                try:
                    eligible = p.reuse_match(candidate, current, argv, c['tools']['rustroad'],
                        post['effective_rc']['effective'], p.extraction_environment(c['scope']['threads']))
                except p.Miss:
                    eligible = False
                if eligible:
                    captured = candidate
        if captured is not None:
            try:
                raw = p.read(p.record(captured['raw_report']))
                source = p.record(captured['output'], p.POLICY['max_spef_bytes'])
            except FileNotFoundError:
                captured = None
                miss = 'missing_prior_report_or_spef'
        if captured is not None:
            p.need(raw['schema'] == 'rustroad.extract.v1' and raw['status'] == 'ok'
                   and raw['workspace_revision'] == current['revision']
                   and raw['rc_profile'] == post['effective_rc']['effective']
                   and raw['output'] == captured['output'], 'PRIOR_EXTRACT_REPORT')
            copied = p.freeze_file(source, destination, c['run_root'], p.POLICY['max_spef_bytes'])
            p.need(copied['source'] == captured['output'], 'REUSE_SOURCE_CHANGED')
            actual = copied['effective']
            p.need(p.same_bytes(actual, captured['output']), 'REUSED_SPEF_CHANGED')
            result = dict(schema='rusteda.full-extract-result.v1', mode='artifact_reuse',
                input_manifest=p.identity(inputs), snapshot=current, prior_capture=post['extract_inputs'][-1],
                raw_report=captured['raw_report'], source=captured['output'], output=actual,
                actual_native_processes=0, command=None)
        else:
            row = command(c, output, 'actual-extract', argv, 1800)
            p.need(row['capture_error'] is None and type(row['exit_code']) is int and row['exit_code'] == 0,
                   'FULL_EXTRACTION_FAILED')
            raw = p.read(p.record(row['stdout']))
            p.need(raw['schema'] == 'rustroad.extract.v1' and raw['status'] == 'ok'
                   and raw['workspace_revision'] == d['final_revision']
                   and raw['rc_profile'] == post['effective_rc']['effective']
                   and raw['output'] == p.identity(destination), 'FULL_EXTRACT_REPORT')
            destination.chmod(0o444)
            result = dict(schema='rusteda.full-extract-result.v1', mode='actual_native_execution',
                input_manifest=p.identity(inputs), snapshot=current, raw_report=row['stdout'],
                output=p.identity(destination, p.POLICY['max_spef_bytes']), actual_native_processes=1, command=row)
            result['reuse_miss_reason'] = miss
        try:
            after = p.snapshot_locked(locked)
        except p.Miss:
            after = None
        p.need(after == current, 'EXTRACTION_SNAPSHOT_CHANGED')
    return result


def final_table(c, post, inputs, output, table):
    p.need(table in ('main', 'antenna'), 'FINAL_TABLE')
    d = decision(c)
    final = p.seal_owned(c['final_gds'], c['run_root'], p.POLICY['max_gds_bytes'])
    with p.shared(c['workspace']) as locked:
        try:
            current = p.snapshot_locked(locked)
        except p.Miss:
            current = None
        p.need(current == d['final_snapshot'], 'FINAL_SNAPSHOT_CHANGED')
        if d['mode'] == 'foundry_clean_keep_revision':
            p.need(p.same_bytes(final, d['initial_gds']), 'FINAL_GDS_CHANGED')
            early = p.read(p.record(d['checks'][table]))
            raw_id = early['raw_summary']
            mode, row, processes = 'same_immutable_gds', None, 0
        else:
            profile = effective(c, post)
            argv = [c['tools']['python']['path'], c['files']['drc_runner']['path'], '--gds', final['path'],
                '--profile', profile['path'], '--tool', c['tools']['appimage']['path'], '--table', table,
                '--workspace', str(output / 'raw'), '--base-rev', 'empty']
            row = command(c, output, 'actual-final-checker', argv, 1200)
            p.need(row['capture_error'] is None and type(row['exit_code']) is int and row['exit_code'] == 0, 'FINAL_FOUNDRY_FAILED')
            raw_id = p.identity(output / 'raw/summary.json')
            mode, processes = 'actual_foundry_execution', 1
        raw = p.read(p.record(raw_id))
        p.need(raw['complete'] is True and type(raw['violations']) is int and raw['violations'] == 0
               and p.same_bytes(raw['gds'], final), 'FINAL_FOUNDRY_BINDING')
        result = dict(schema='rusteda.foundry-bound-result.v1', mode=mode, input_manifest=p.identity(inputs),
            table=table, final_gds=final, raw_summary=raw_id, actual_foundry_processes=processes,
            command=row, **{k: raw[k] for k in ('status', 'complete', 'violations', 'gds', 'deck_sha256',
                                            'disabled_rules', 'raw_report')})
    # Preserve the raw path/report. This is a typed binding, never a forged raw
    # checker summary claiming it executed on a different final output path.
    p.write_new(output / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('precheck', 'decide', 'extract', 'final-table'))
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    parser.add_argument('--table', choices=('main', 'antenna'))
    args = parser.parse_args()
    c, post = configuration(args.inputs)
    output = p.private_path(args.workspace, c['run_root'], create_parents=True)
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        p.need(args.base_rev == 'empty' and all(v.name == 'workspace.lock' for v in output.iterdir()),
               'REVISION_CONFLICT')
        result = globals()[args.action.replace('-', '_')](c, post, args.inputs, output, args.table)
        p.write_new(output / 'result.json', result)
        (output / 'HEAD').write_text('rev1\n')
        print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RecursionError, SyntaxError) as error:
        print(json.dumps(dict(schema='rusteda.foundry-first-error.v1', status='failed',
                              error=str(error)[:4096]), allow_nan=False), file=sys.stderr)
        raise SystemExit(1)

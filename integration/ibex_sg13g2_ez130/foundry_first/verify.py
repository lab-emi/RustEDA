#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Independent metadata proof for opt-in foundry decisions and whole RC reuse.

No physical/model oracle is implemented. Original checker outputs supply every
EDA verdict. Bounded XML scope/count verification never decodes geometry.
"""
import argparse
import fcntl
import json
from pathlib import Path
import resource
import sys
import xml.etree.ElementTree as ET

try:
    from . import protocol as p
    from . import runner
except ImportError:
    import protocol as p
    import runner


def integer(value, expected=None):
    p.need(type(value) is int and value >= 0 and (expected is None or value == expected),
           'ACTUAL_COUNTER_INTEGER')


def xml_scope(path):
    before = p.identity(path, 64 << 20)
    stack, categories, seen, count, nodes = [], [], set(), 0, 0
    class Bounded:
        def __init__(self, raw):
            self.raw, self.remaining, self.tail = raw, before['bytes'], b''
        def read(self, size):
            if not self.remaining:
                p.need(self.raw.read(1) == b'', 'RAW_XML_GREW')
                return b''
            size = min(65536 if size is None or size < 0 else size, 65536, self.remaining)
            chunk = self.raw.read(size)
            p.need(len(chunk) == size, 'RAW_XML_CHANGED')
            self.remaining -= len(chunk)
            text = self.tail + chunk
            p.need(b'<!DOCTYPE' not in text and b'<!ENTITY' not in text, 'RAW_XML_DECLARATION_UNSUPPORTED')
            self.tail = text[-16:]
            return chunk
    with Path(path).open('rb') as raw:
        for event, node in ET.iterparse(Bounded(raw), events=('start', 'end')):
            if event == 'start':
                nodes += 1
                p.need(nodes <= 250000, 'RAW_XML_NODE_WORK_BUDGET')
                stack.append(node.tag)
                p.need(len(stack) <= 64 and (len(stack) != 1 or node.tag == 'report-database'), 'RAW_XML_SCOPE')
            else:
                if node.tag == 'category' and len(stack) > 1 and stack[-2] == 'categories':
                    name = node.findtext('name')
                    p.need(name and len(name) <= 8192 and name not in seen and len(categories) < 4096,
                           'RAW_CATEGORY_BUDGET')
                    categories.append(name)
                    seen.add(name)
                    node.clear()
                if node.tag == 'item':
                    count += 1
                    p.need(count <= 500000, 'RAW_FINDING_BUDGET')
                    node.clear()
                # Keep only category/name until the category end event.
                # Other text/geometry is never retained by this metadata reader.
                if not (node.tag == 'name' and len(stack) > 1 and stack[-2] == 'category'):
                    node.clear()
                p.need(stack.pop() == node.tag, 'RAW_XML_BALANCE')
    p.need(not stack, 'RAW_XML_TRUNCATED')
    p.need(p.identity(path, 64 << 20) == before, 'RAW_XML_CHANGED')
    return categories, count


def raw_foundry(raw_id, c, post, table, gds):
    raw = p.read(p.record(raw_id))
    integer(raw['engine_exit_code'], 0)
    integer(raw['violations'])
    for key in ('gds', 'tool', 'profile'):
        p.file_id_shape(raw[key])
    p.need(raw['complete'] is True and raw['scope'] == 'complete_foundry_' + table
           and raw['gds'] == gds and raw['tool'] == c['tools']['appimage']
           and raw['profile'] == post['effective_foundry']['effective']
           and raw['disabled_rules'] == (['recommended'] if table == 'main' else []), 'RAW_FOUNDRY_BINDING')
    scopes = p.read(p.record(c['files']['foundry_first_scopes']))
    p.need(scopes['schema'] == 'rusteda.qualified-foundry-scope.v1', 'QUALIFIED_SCOPE_SCHEMA')
    for role, value in [('profile', c['files']['drc_profile']), ('tool', c['tools']['appimage']),
                        ('driver', c['files']['drc_runner'])]:
        p.need(scopes[role] == {key: value[key] for key in ('sha256', 'bytes')}, 'QUALIFIED_SCOPE_SOURCE')
    expected = scopes['tables'][table]
    p.need(raw['deck_sha256'] == expected['deck_sha256'] ==
           c['files']['drc_deck' if table == 'main' else 'antenna_deck']['sha256'], 'RAW_DECK_CHANGED')
    names, count = xml_scope(p.record(raw['raw_report'], 64 << 20))
    p.need(names == expected['categories'] and count == raw['violations'], 'RAW_RULE_SCOPE_CHANGED')
    p.need(raw['status'] == ('passed' if count == 0 else 'failed'), 'RAW_FINDING_STATUS')
    return raw


def producer_command(c, inputs, tag, core_argv, expected_value=None):
    root = Path(c['run_root'])
    receipts = p.read(root / 'step-receipts.json')
    p.need(isinstance(receipts, list) and len(receipts) <= 128, 'PRODUCER_RECEIPT_BUDGET')
    rows = {}
    for value in receipts:
        p.need(isinstance(value['id'], str) and value['id'] not in rows, 'PRODUCER_RECEIPT_DUPLICATE')
        rows[value['id']] = value
    p.need(tag in rows and rows[tag]['kind'] == 'command' and 'capture_error' not in rows[tag],
           'PRODUCER_EXECUTION_MISSING')
    integer(rows[tag]['exit_code'], 0)
    row = rows[tag]
    command_path = root / 'logs' / (tag + '.command.json')
    actual = p.read(command_path)
    expected = [c['tools']['gnu_time']['path'], '-v', '-o', str(root / 'logs' / (tag + '.time')), *core_argv]
    p.need(actual == dict(argv=expected, cwd=str(root)), 'PRODUCER_COMMAND_CHANGED')
    stdout = p.record(row['stdout'], 2 << 20)
    p.record(row['stderr'], 64 << 20)
    p.need(stdout == root / 'logs' / (tag + '.stdout'), 'PRODUCER_STDOUT_PATH')
    if expected_value is not None:
        p.need(p.read(stdout) == expected_value, 'PRODUCER_STDOUT_SUBSTITUTION')
    return row


def precheck(c, post, inputs, table, item):
    value = p.read(p.record(item))
    integer(value['checker_command_attempts'])
    if value['actual_foundry_processes'] is not None:
        integer(value['actual_foundry_processes'])
    p.need(value['schema'] == 'rusteda.foundry-precheck.v1' and value['table'] == table
           and value['gds'] == p.identity(c['initial_gds'])
           and value['input_manifest'] == p.identity(inputs), 'PRECHECK_BINDING')
    work = Path(c['initial_gds']).parent / ('pre-' + table)
    producer_command(c, inputs, 'be_ff_' + table, [c['tools']['python']['path'],
        c['files']['foundry_first_runner']['path'], 'precheck', '--table', table, '--inputs', str(inputs),
        '--workspace', str(work), '--base-rev', 'empty'], value)
    if value['checker_command_attempts'] == 0:
        integer(value['actual_foundry_processes'], 0)
        p.need(value['status'] == 'unverified' and value['reason'] == 'memory_admission'
               and value['complete'] is False and value['violations'] is None and value['command'] is None,
               'PRECHECK_NOT_EXECUTED_STATUS')
        return value
    integer(value['checker_command_attempts'], 1)
    row = value['command']
    expected = [c['tools']['python']['path'], c['files']['drc_runner']['path'], '--gds', c['initial_gds'],
        '--profile', post['effective_foundry']['effective']['path'], '--tool', c['tools']['appimage']['path'],
        '--table', table, '--workspace', str(work / 'raw'), '--base-rev', 'empty']
    p.need(row['argv'] == expected, 'PRECHECK_ACTUAL_COMMAND')
    p.record(row['stdout'], 2 << 20)
    p.record(row['stderr'], 64 << 20)
    if value['complete'] is True:
        integer(value['actual_foundry_processes'], 1)
        integer(row['exit_code'])
        p.need(row['exit_code'] in (0, 1) and row['capture_error'] is None, 'PRECHECK_EXECUTION_INCOMPLETE')
        raw = raw_foundry(value['raw_summary'], c, post, table, value['gds'])
        p.need(p.read(row['stdout']['path']) == raw and value['status'] == 'observed'
               and type(value['violations']) is int and value['violations'] == raw['violations'], 'PRECHECK_RAW_SUBSTITUTION')
    else:
        p.need(value['status'] == 'unverified' and value['violations'] is None
               and value['actual_foundry_processes'] in (None, 1), 'UNVERIFIED_AS_ZERO')
        if value['actual_foundry_processes'] == 1:
            integer(value['actual_foundry_processes'], 1)
            raw = p.read(p.record(value['raw_summary']))
            p.need(type(raw['engine_exit_code']) is int and raw['tool'] == c['tools']['appimage']
                   and raw['gds'] == value['gds'] and raw['profile'] == post['effective_foundry']['effective'],
                   'UNVERIFIED_EXECUTION_COUNT')
    return value


def postroute_authority(c, post):
    root = Path(c['run_root'])
    p.need(p.read(p.record(c['postroute_result'])) == post, 'POSTROUTE_FULL_RESULT_CHANGED')
    rows = p.read(root / 'step-receipts.json')
    p.need(isinstance(rows, list) and len(rows) <= 128
           and sum(row.get('id') == 'be_postroute_inputs' and row.get('kind') == 'render' for row in rows) == 1,
           'POSTROUTE_ACTUAL_RENDER_REQUIRED')
    summary = {key: post[key] for key in ('schema', 'status', 'final_workspace', 'final_revision', 'resized')}
    summary.update(input_manifest=post['input_manifest'], result=c['postroute_result'])
    producer_command(c, None, 'be_postroute', [c['tools']['python']['path'],
        c['files']['postroute_runner']['path'], '--inputs', str(root / 'backend/postroute-inputs.json'),
        '--workspace', str(root / 'backend/postroute'), '--base-rev', 'empty', '--foundry-first'], summary)
    source = p.read(p.record(post['input_manifest']))
    p.need(source['schema'] == 'rusteda.native-postroute-reuse-inputs.v1'
           and post['policy_source'] == c['files']['postroute_runner']
           and post['capture_policy'] == source['foundry_first']
           and type(source['limits']['threads']) is int and source['limits']['threads'] == c['scope']['threads'],
           'POSTROUTE_ACTUAL_CAPTURE_SOURCE')
    return source


def decision_review(c, post, inputs, output=None):
    decision_path = Path(c['initial_gds']).parent / 'decision/result.json'
    path = decision_path
    d = p.read(path)
    for key in ('input_manifest', 'initial_gds'):
        p.file_id_shape(d[key])
    for key in ('initial_snapshot', 'final_snapshot'):
        if d[key] is not None:
            p.validate_snapshot(d[key])
    postroute_authority(c, post)
    p.immutable(d['initial_gds'], c['run_root'], p.POLICY['max_gds_bytes'])
    # The actual pre-export process is authority for the checked immutable GDS,
    # not a caller-chosen same-name file or a report borrowed from a later rev.
    export_row = producer_command(c, inputs, 'be_ff_export', c['export_argv'])
    export = p.read(export_row['stdout']['path'])
    p.need(export['status'] == 'written' and export['revision'] == 'rev4'
           and export['workspace_mutated'] is False and export['output'] == p.identity(c['initial_gds'])
           and export['cell_gds'] == post['effective_exports']['cell_gds']['effective']
           and export['cell_lef'] == post['effective_exports']['cell_lef']['effective']
           and export['layer_map'] == post['effective_exports']['layer_map']['effective'], 'ACTUAL_GDS_EXPORT_BINDING')
    p.need(d['schema'] == 'rusteda.foundry-first-decision.v1' and d['input_manifest'] == p.identity(inputs)
           and d['workspace'] == c['workspace'] and d['initial_revision'] == 'rev4'
           and d['initial_gds'] == p.identity(c['initial_gds'])
           and d['native_process_scope'] == 'antenna_controller_only'
           and d['requires_full_revalidation'] is True and d['whole_layout_antenna_status'] == 'unchecked',
           'DECISION_BINDING')
    producer_command(c, inputs, 'be_antenna_repair', [c['tools']['python']['path'],
        c['files']['foundry_first_runner']['path'], 'decide', '--inputs', str(inputs), '--workspace',
        str(path.parent), '--base-rev', 'empty'], d)
    checks = {}
    for key in ('main', 'antenna'):
        if d['checks'][key] is None:
            path = Path(c['initial_gds']).parent / ('pre-' + key) / 'result.json'
            p.need(d['mode'] == 'legacy_repair' and not path.exists()
                   and not path.is_symlink() and not path.parent.is_symlink(), 'MISSING_PRECHECK_CANNOT_AUTHORIZE_CLEAN')
            checks[key] = None
        else:
            checks[key] = precheck(c, post, inputs, key, d['checks'][key])
    try:
        snapshot = p.snapshot(c['workspace'])
    except p.Miss:
        snapshot = None
    p.need(d['final_snapshot'] == snapshot, 'DECISION_FINAL_SNAPSHOT_CHANGED')
    if d['mode'] == 'foundry_clean_keep_revision':
        integer(d['actual_native_processes'], 0)
        p.need(snapshot is not None and snapshot == d['initial_snapshot']
               and snapshot['revision'] == d['final_revision'] == d['initial_revision']
               and d['native_analysis_status'] == 'unchecked' and d['unresolved_supported_nets'] is None,
               'DECISION_FALSE_NATIVE_OR_REVISION')
        for value in checks.values():
            p.need(value is not None and value['complete'] is True
                   and type(value['violations']) is int and value['violations'] == 0,
                   'DECISION_REQUIRES_COMPLETE_ZERO')
    else:
        p.need(d['mode'] == 'legacy_repair', 'DECISION_MODE')
        integer(d['actual_native_processes'], 1)
        p.need(p.read(p.record(d['legacy_inputs'])) == c['legacy_inputs'], 'LEGACY_CONTROLLER_CHANGED')
        row = d['legacy_command']
        integer(row['exit_code'], 0)
        expected = [c['tools']['python']['path'], c['files']['antenna_runner']['path'], '--inputs',
            d['legacy_inputs']['path'], '--workspace', str(Path(c['run_root']) / 'backend/antenna-repair'), '--base-rev', 'empty']
        p.need(row['argv'] == expected and row['capture_error'] is None, 'LEGACY_EXECUTION_CHANGED')
        p.record(row['stdout'], 2 << 20)
        p.record(row['stderr'], 64 << 20)
        if output is not None:
            old_cfg = dict(schema='rusteda.antenna-v3-independent-verifier-inputs.v1',
                lineage=d['legacy_lineage'], tool=c['tools']['rustroad'],
                source_manifest=c['files']['antenna_source_manifest'], design_workspace=c['workspace'],
                evidence_kind='actual_native_artifacts', controller_inputs=d['legacy_inputs'])
            old_input = output / 'legacy-verifier-inputs.json'
            p.write_new(old_input, old_cfg)
            argv = [c['tools']['python']['path'], c['files']['antenna_verifier']['path'], '--inputs',
                str(old_input), '--workspace', str(output / 'legacy-review'), '--base-rev', 'empty']
            row = runner.command(c, output, 'actual-legacy-verifier', argv, 180)
            integer(row['exit_code'], 0)
            p.need(row['capture_error'] is None, 'LEGACY_REVIEW_FAILED')
            review_id = p.identity(output / 'legacy-review/result.json')
            review = p.read(p.record(review_id))
            p.need(review['schema'] == 'rusteda.antenna-v3-independent-lineage-review.v1'
                   and review['status'] == 'metadata_lineage_verified_requires_full_signoff'
                   and p.read(row['stdout']['path']) == review
                   and review['lineage'] == d['legacy_lineage']
                   and review['controller_inputs'] == d['legacy_inputs'], 'LEGACY_RAW_REVIEW_CHANGED')
            return dict(decision=p.identity(decision_path), mode=d['mode'], final_revision=d['final_revision'],
                native_analysis_status=review['native_analysis_status'], actual_native_processes=1,
                legacy_review=review_id, legacy_verifier_command=row)
    return dict(decision=p.identity(decision_path), mode=d['mode'], final_revision=d['final_revision'],
                native_analysis_status=d['native_analysis_status'], actual_native_processes=d['actual_native_processes'])


def extraction_review(value, c, post, inputs, final_spef):
    p.need(value['schema'] == 'rusteda.full-extract-result.v1' and value['input_manifest'] == p.identity(inputs)
           and value['output'] == final_spef, 'EXTRACT_RESULT_BINDING')
    raw = p.read(p.record(value['raw_report']))
    for key in ('output', 'rc_profile'):
        p.file_id_shape(raw[key])
    if value['snapshot'] is not None:
        p.validate_snapshot(value['snapshot'])
    p.need(value['snapshot'] == runner.decision(c)['final_snapshot'], 'EXTRACT_SNAPSHOT_BINDING')
    p.need(raw['schema'] == 'rustroad.extract.v1' and raw['status'] == 'ok'
           and raw['rc_profile'] == post['effective_rc']['effective']
           and raw['workspace_revision'] == runner.decision(c)['final_revision']
           and raw['scope']['corner'] == c['extract_argv'][c['extract_argv'].index('--corner') + 1], 'EXTRACT_RAW_BINDING')
    if value['mode'] == 'artifact_reuse':
        integer(value['actual_native_processes'], 0)
        p.need(value['command'] is None and value['prior_capture'] == post['extract_inputs'][-1], 'REUSE_PRIOR_SELECTION')
        capture = p.read(p.record(value['prior_capture']))
        p.selected_capture_request(capture, p.read(p.record(post['input_manifest'])), post, c['tools']['rustroad'])
        p.need(isinstance(post['commands'], list) and len(post['commands']) <= 256
               and sum(row == capture['command'] for row in post['commands']) == 1
               and capture['command']['stdout'] == capture['raw_report']
               and capture['raw_report'] == value['raw_report'] and capture['output'] == raw['output'] == value['source'],
               'REUSE_ACTUAL_EXECUTION_AUTHORITY')
        p.need(capture['status'] == 'captured'
               and capture['source_workspace'] == capture['command']['argv'][2]
               and Path(capture['source_workspace']).is_relative_to(Path(c['run_root']))
               and isinstance(post['rounds'], list) and 1 <= len(post['rounds']) <= 4
               and post['rounds'][-1]['workspace'] == capture['source_workspace']
               and post['rounds'][-1]['check']['spef'] == capture['output'], 'REUSE_FINAL_SELECTED_CHECK')
        p.record(capture['command']['stderr'], 64 << 20)
        p.need(p.reuse_match(capture, p.snapshot(c['workspace']), c['extract_argv'], c['tools']['rustroad'],
            post['effective_rc']['effective'], p.extraction_environment(c['scope']['threads']))
            and p.same_bytes(p.identity(p.record(value['source'])), final_spef), 'REUSE_COMPLETE_INPUT_MISMATCH')
    else:
        p.need(value['mode'] == 'actual_native_execution', 'EXTRACT_EXECUTION_MODE')
        integer(value['actual_native_processes'], 1)
        row = value['command']
        integer(row['exit_code'], 0)
        p.need(row['capture_error'] is None and row['argv'] == c['extract_argv']
               and row['stdout'] == value['raw_report'] and raw['output'] == final_spef, 'ACTUAL_EXTRACT_COMMAND')
        p.record(row['stderr'], 64 << 20)
    return raw


def table_review(value, c, post, inputs, table, final_gds):
    p.need(value['schema'] == 'rusteda.foundry-bound-result.v1' and value['table'] == table
           and value['input_manifest'] == p.identity(inputs) and value['final_gds'] == final_gds,
           'FINAL_FOUNDRY_RESULT_BINDING')
    raw = raw_foundry(value['raw_summary'], c, post, table, value['gds'])
    p.need(p.same_bytes(raw['gds'], final_gds), 'FINAL_GDS_NOT_CHECKED')
    for key in ('status', 'complete', 'violations', 'gds', 'deck_sha256', 'disabled_rules', 'raw_report'):
        p.need(value[key] == raw[key], 'FINAL_FOUNDRY_RAW_SUBSTITUTION')
    d = runner.decision(c)
    if value['mode'] == 'same_immutable_gds':
        integer(value['actual_foundry_processes'], 0)
        prior = p.read(p.record(d['checks'][table]))
        p.need(d['mode'] == 'foundry_clean_keep_revision' and value['command'] is None
               and value['raw_summary'] == prior['raw_summary']
               and p.same_bytes(d['initial_gds'], final_gds)
               and p.snapshot(c['workspace']) == d['final_snapshot'], 'FINAL_EARLY_GDS_PROOF')
    else:
        p.need(value['mode'] == 'actual_foundry_execution' and d['mode'] == 'legacy_repair', 'FINAL_FOUNDRY_MODE')
        integer(value['actual_foundry_processes'], 1)
        row = value['command']
        integer(row['exit_code'], 0)
        expected = [c['tools']['python']['path'], c['files']['drc_runner']['path'], '--gds', final_gds['path'],
            '--profile', post['effective_foundry']['effective']['path'], '--tool', c['tools']['appimage']['path'],
            '--table', table, '--workspace', str(Path(c['run_root']) / 'backend' / table / 'raw'), '--base-rev', 'empty']
        p.need(row['capture_error'] is None and raw['gds'] == final_gds and row['argv'] == expected,
               'FINAL_FOUNDRY_EXECUTION')
        p.record(row['stdout'], 2 << 20)
        p.record(row['stderr'], 64 << 20)
        p.need(p.read(row['stdout']['path']) == raw, 'FINAL_RAW_STDOUT_CHANGED')
    return raw


def qualification_review(context, native, artifacts, route, inspect, cts):
    """Typed proof branch; raw reports remain exactly as the tools emitted."""
    root = Path(context['run_root'])
    inputs = root / 'backend/foundry-first/inputs.json'
    c, post = runner.configuration(inputs)
    p.need(c['files']['foundry_first_sources'] == context['files']['foundry_first_sources']
           and c['tools'] == {key: context['tools'][key] for key in c['tools']}, 'QUALIFICATION_INPUT_BINDING')
    for role, value in c['files'].items():
        p.need(value == context['files'][role], 'QUALIFICATION_SOURCE_CHANGED')
    runner.effective(c, post)
    p.immutable(p.identity(c['initial_gds'], p.POLICY['max_gds_bytes']), c['run_root'], p.POLICY['max_gds_bytes'])
    p.immutable(artifacts['gds'], c['run_root'], p.POLICY['max_gds_bytes'])
    p.immutable(artifacts['spef_native_nom'], c['run_root'], p.POLICY['max_spef_bytes'])
    postroute_authority(c, post)
    integer(post['copied_files'])
    decision = runner.decision(c)
    reviewed = decision_review(c, post, inputs)
    p.need(native['antenna_lineage'] == decision, 'DECISION_CONTEXT_CHANGED')
    verification = native['antenna_verification']
    p.need(verification['schema'] == 'rusteda.foundry-first-review.v1'
           and verification['status'] == 'metadata_verified_requires_all_final_gates'
           and verification['input_manifest'] == p.identity(inputs)
           and verification['source_manifest'] == context['files']['foundry_first_sources']
           and verification['whole_layout_signoff'] == 'unchecked_until_final_gds_and_all_gates', 'ACTUAL_REVIEW_BINDING')
    producer_command(c, inputs, 'be_antenna_verify', [c['tools']['python']['path'],
        c['files']['foundry_first_verifier']['path'], '--inputs', str(inputs), '--workspace',
        str(root / 'backend/foundry-verification'), '--base-rev', 'empty'], verification)
    actual_review = verification['decision_review']
    for key, value in reviewed.items():
        p.need(actual_review[key] == value, 'DECISION_REVIEW_CHANGED')
    if decision['mode'] == 'foundry_clean_keep_revision':
        p.need(decision['initial_snapshot']['metadata']['parent_revision'] == cts['revision']
               and decision['initial_revision'] == route['revision'] == decision['final_revision'] == inspect['revision'],
               'REAL_ROUTE_ANCESTRY_REQUIRED')
    else:
        legacy = p.read(p.record(actual_review['legacy_review']))
        row = actual_review['legacy_verifier_command']
        integer(row['exit_code'], 0)
        old_inputs_path = root / 'backend/foundry-verification/legacy-verifier-inputs.json'
        old_inputs = p.read(old_inputs_path)
        p.need(old_inputs == dict(schema='rusteda.antenna-v3-independent-verifier-inputs.v1',
            lineage=decision['legacy_lineage'], tool=c['tools']['rustroad'],
            source_manifest=c['files']['antenna_source_manifest'], design_workspace=c['workspace'],
            evidence_kind='actual_native_artifacts', controller_inputs=decision['legacy_inputs']), 'LEGACY_REVIEW_INPUTS')
        expected = [c['tools']['python']['path'], c['files']['antenna_verifier']['path'], '--inputs',
            str(old_inputs_path), '--workspace', str(root / 'backend/foundry-verification/legacy-review'), '--base-rev', 'empty']
        p.need(row['argv'] == expected and row['capture_error'] is None
               and p.read(p.record(row['stdout'])) == legacy
               and legacy['schema'] == 'rusteda.antenna-v3-independent-lineage-review.v1'
               and legacy['verifier_source'] == c['files']['antenna_verifier']
               and legacy['source_manifest'] == c['files']['antenna_source_manifest']
               and legacy['tool'] == c['tools']['rustroad']
               and legacy['lineage'] == decision['legacy_lineage']
               and legacy['controller_inputs'] == decision['legacy_inputs']
               and legacy['initial']['parent_revision'] == cts['revision']
               and legacy['initial_revision'] == route['revision']
               and legacy['final_revision'] == inspect['revision'], 'ACTUAL_LEGACY_REVIEW_AUTHORITY')
        integer(legacy['actual_native_processes'], 1)
    extracted = native['native_extraction']
    producer_command(c, inputs, 'be_extract', [c['tools']['python']['path'],
        c['files']['foundry_first_runner']['path'], 'extract', '--inputs', str(inputs), '--workspace',
        str(root / 'backend/foundry-extraction'), '--base-rev', 'empty'], extracted)
    raw = extraction_review(extracted, c, post, inputs, artifacts['spef_native_nom'])
    for table, report in [('main', 'drc'), ('antenna', 'antenna')]:
        value = p.read(root / context['reports'][report])
        table_review(value, c, post, inputs, table, artifacts['gds'])
        producer_command(c, inputs, 'be_' + table, [c['tools']['python']['path'],
            c['files']['foundry_first_runner']['path'], 'final-table', '--table', table, '--inputs', str(inputs),
            '--workspace', str(root / 'backend' / table), '--base-rev', 'empty'], value)
    return dict(initial_revision=decision['initial_revision'], final_revision=decision['final_revision'],
        policy=decision['mode'], native_analysis_status=actual_review['native_analysis_status'],
        actual_native_processes=decision['actual_native_processes'],
        whole_layout_antenna_status='unchecked', requires_full_revalidation=True,
        verification=p.identity(root / 'backend/foundry-verification/result.json'),
        scope='explicit conditional execution proof; complete final-GDS foundry gates remain mandatory'), raw, extracted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    c, post = runner.configuration(args.inputs)
    output = p.private_path(args.workspace, c['run_root'], create_parents=True)
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        p.need(args.base_rev == 'empty' and all(v.name == 'workspace.lock' for v in output.iterdir()), 'REVISION_CONFLICT')
        reviewed = decision_review(c, post, args.inputs, output)
        result = dict(schema='rusteda.foundry-first-review.v1', status='metadata_verified_requires_all_final_gates',
            input_manifest=p.identity(args.inputs), source_manifest=c['files'][runner.SOURCE_ROLE],
            decision_review=reviewed, whole_layout_signoff='unchecked_until_final_gds_and_all_gates')
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

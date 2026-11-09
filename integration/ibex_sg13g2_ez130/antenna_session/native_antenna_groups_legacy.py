#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Bounded antenna groups; inventory coverage is distinct from foundry signoff.

Memory estimate and fixed budgets: adjacent plan.json. One <=64MiB connectivity
model is decoded at a time (<768MiB estimated); selected metadata is <=4MiB.
One serial native child is estimated <=2GiB. Caller provides a 4GiB cgroup.
No graph/path enumeration; all name sets, attempts, artifacts and work are capped.
Legacy native_antenna.py and its v1 protocol remain unchanged.

v3 (source03): a spent attempt/jumper/work budget or the time needed for the
final checks stops repair instead of failing the run. The final groups are
always checked, so every net left violating is listed as unresolved, with the
reason in `stop_reason`. A group whose identical eligible set already had a
no-op repair is not retried. The pinned POLICY is unchanged.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import native_antenna as legacy
from report_fields import fields

require, read, identity, save, head, metadata = (
    legacy.require, legacy.read, legacy.identity, legacy.save, legacy.head, legacy.metadata)

POLICY = dict(group_names=128, max_groups=1024, max_attempts=64,
    max_total_jumpers=4096, max_commands=4096, max_report_bytes=8 << 30,
    max_selected_report_bytes=128 << 20, max_metadata_bytes=4 << 20,
    max_cumulative_work=200000000000)
LIMITS = dict(max_shapes_per_net=200000, max_total_shapes=2000000,
    max_pair_checks=200000000, max_union_cells=4000000, max_rows=100000,
    max_work=200000000, report_memory_bytes=128 << 20)
HEADER = legacy.HEADER | {'design', 'selection', 'stats'}
OBSERVATIONS = {'observed', 'no_geometry', 'no_gate_area', 'no_applicable_rule_observation'}


def natural(value):
    return type(value) is int and value >= 0


def sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()


def canonical_names(names, maximum=100000):
    require(isinstance(names, list) and len(names) <= maximum
        and all(isinstance(n, str) and 0 < len(n.encode('utf-8')) <= 4096 for n in names),
        'native group name/count budget')
    require(sum(len(n.encode('utf-8')) for n in names) <= 16 << 20,
        'native group aggregate name byte budget')
    require(names == sorted(set(names)), 'native group names must be canonical/distinct')
    return names


def validate_inventory(value, revision, routing_scope='all_signal_nets'):
    require(value['schema'] == 'rustroad.antenna-inventory.v1'
        and value['status'] == 'inventory_only' and value['workspace_revision'] == revision
        and value['routing_scope'] == routing_scope and sha(value['source_sha256']),
        'native routed inventory revision/scope/source differs')
    for key in ('model_logical_net_count', 'router_declared_net_count',
                'routed_logical_net_count', 'excluded_nonlogical_supply_tie_count'):
        require(natural(value[key]) and value[key] <= 1000000, 'native inventory count budget')
    rows = value['nets']
    require(isinstance(rows, list) and len(rows) <= 100000, 'native inventory row budget')
    names, ids, canonical = [], set(), []
    for row in rows:
        require(set(row) == {'net_id', 'canonical_name'} and natural(row['net_id'])
            and row['net_id'] < value['model_logical_net_count'] and row['net_id'] not in ids,
            'native inventory explicit ID differs or duplicated')
        ids.add(row['net_id'])
        names.append(row['canonical_name'])
        canonical.append(dict(net_id=row['net_id'], canonical_name=row['canonical_name']))
    canonical_names(names)
    require(len(rows) == value['routed_logical_net_count']
        and len(rows) + value['excluded_nonlogical_supply_tie_count'] == value['router_declared_net_count'],
        'native inventory count reconciliation differs')
    require(value['inventory_sha256'] == fingerprint([
        revision, routing_scope, value['source_sha256'], canonical]), 'native inventory digest differs')
    require(isinstance(value['sources'], list) and len(value['sources']) == 2
        and {r['role'] for r in value['sources']} == {'technology_lef', 'cell_lef'}
        and all(sha(r['sha256']) and Path(r['path']).is_absolute() for r in value['sources']),
        'native inventory source LEFs differ')
    return names


def validate_report(report, names, revision, sources, routing_scope, source=None):
    canonical_names(names, POLICY['group_names'])
    require(report['schema'] == 'rustroad.antenna-check.v1'
        and report['workspace_revision'] == revision
        and report['scope']['routing_scope'] == routing_scope
        and report['scope']['extra_shapes'] == 0
        and report['scope']['drc_status'] == 'unchecked'
        and report['sources'] == sources and report['safety_margin']['fraction'] == .1,
        'selected antenna source/revision/scope differs')
    selection = report['selection']
    require(selection['schema'] == 'rustroad.antenna-selected.v1'
        and selection['scope'] == 'explicit_routed_net_subset'
        and selection['canonical_nets'] == names
        and selection['selection_sha256'] == fingerprint(names)
        and sha(selection['source_sha256']) and selection['limits'] == LIMITS,
        'selected antenna names/hash/limits differ')
    require(source is None or selection['source_sha256'] == source, 'final group physical source differs')
    rows = selection['coverage']
    require(isinstance(rows, list) and len(rows) == len(names), 'selected antenna coverage count differs')
    partial, by_name, row_count = False, {}, 0
    for row, name in zip(rows, names, strict=True):
        require(set(row) == {'net', 'shapes', 'rows', 'gate_terminals', 'observation', 'model_partial'}
            and row['net'] == name and type(row['model_partial']) is bool
            and all(natural(row[k]) for k in ('shapes', 'rows', 'gate_terminals'))
            and row['rows'] <= LIMITS['max_rows'] and row['shapes'] <= LIMITS['max_shapes_per_net']
            and row['observation'] in OBSERVATIONS, 'selected antenna observation identity/budget')
        require((row['observation'] != 'no_geometry' or row['shapes'] == 0)
            and (row['observation'] != 'no_gate_area' or row['gate_terminals'] == 0)
            and (row['observation'] != 'observed' or row['rows'] > 0),
            'selected antenna observation counters differ')
        row_count += row['rows']
        partial |= row['model_partial'] or row['observation'] != 'observed'
        by_name[name] = row
    require(row_count <= LIMITS['max_rows'] and natural(report['violations'])
        and natural(report['lef_violations']) and report['lef_violations'] <= report['violations'] <= row_count,
        'selected antenna row/violation budget')
    require(isinstance(report['unsupported'], list) and len(report['unsupported']) <= 100000,
        'selected antenna unsupported budget')
    partial |= bool(report['unsupported']) or report['status'] == 'subset_partial'
    nets, violating, seen = report['nets'], set(), set()
    require(isinstance(nets, list) and len(nets) <= len(names), 'selected worst-net count differs')
    for row in nets:
        require(row['net'] in by_name and row['net'] not in seen and type(row['violated']) is bool,
            'selected antenna worst-net identity differs')
        seen.add(row['net'])
        if row['violated']:
            violating.add(row['net'])
    require(bool(violating) == bool(report['violations']) and report['status'] in
        ({'subset_violated'} if violating else {'subset_partial', 'subset_clean'}),
        'selected antenna status differs')
    require(not partial or report['status'] != 'subset_clean', 'partial selected model mislabeled clean')
    work = report['stats']['work']
    require(natural(work) and work <= LIMITS['max_work'], 'selected antenna model work budget')
    memory = report['stats']['report_memory']
    require(all(natural(memory[k]) for k in ('limit_bytes', 'live_bytes', 'peak_live_bytes',
        'cumulative_reserved_bytes', 'cumulative_row_bytes'))
        and memory['limit_bytes'] == LIMITS['report_memory_bytes']
        and memory['live_bytes'] <= memory['peak_live_bytes'] <= memory['limit_bytes']
        and memory['peak_live_bytes'] <= memory['cumulative_reserved_bytes'],
        'selected antenna live/cumulative report memory differs')
    return dict(violating=violating, coverage=by_name, partial=partial, rows=row_count, work=work)


class Run(legacy.Run):
    def __init__(self, config, output):
        require(set(config) == {'schema', 'design_workspace', 'initial_revision', 'tool', 'limits', 'grouping'}
            and config['schema'] == 'rusteda.native-antenna-inputs.v2'
            and config['grouping'] == POLICY, 'native grouped antenna input policy differs')
        old = {k: v for k, v in config.items() if k != 'grouping'}
        old['schema'] = 'rusteda.native-antenna-inputs.v1'
        super().__init__(old, output)
        self.input = config
        self.output_bytes, self.work_budget_charge, self.total_jumpers = 0, 0, 0
        self.reported_work, self.validation_work_upper_bound = 0, 0

    def command(self, name, args, cap=128 << 20):
        require(len(self.commands) < POLICY['max_commands'], 'native antenna command budget')
        available = POLICY['max_report_bytes'] - self.output_bytes - (16 << 20)
        require(available > 0, 'native antenna aggregate report budget')
        result = super().command(name, args, min(cap, available))
        self.output_bytes += self.commands[-1]['output_bytes']
        require(self.output_bytes <= POLICY['max_report_bytes'], 'native antenna aggregate report budget')
        return result

    def charge(self, work, unreported_validation_upper_bound=0):
        require(natural(work) and natural(unreported_validation_upper_bound)
            and self.work_budget_charge + work + unreported_validation_upper_bound <= POLICY['max_cumulative_work'],
            'native antenna cumulative work budget')
        self.reported_work += work
        self.validation_work_upper_bound += unreported_validation_upper_bound
        self.work_budget_charge += work + unreported_validation_upper_bound

    def inventory(self, name, revision, scope='all_signal_nets'):
        path = self.command(name, ['antenna-inventory'], 64 << 20)
        value = read(path, 64 << 20)
        names = validate_inventory(value, revision, scope)
        require(head(self.design) == revision, 'read-only inventory changed actual HEAD')
        return path, value, names

    def check_group(self, name, names, revision, inventory, source=None):
        require(self.work_budget_charge + LIMITS['max_work'] <= POLICY['max_cumulative_work'],
            'native antenna cumulative work admission budget')
        selection = self.output/(name+'-selection.json')
        save(selection, names)
        path = self.command(name, ['check-antenna', '--selected-nets', selection,
            '--safety-margin', '0.1', '--max-work', '200000000', '--max-rows', '100000'])
        report = fields(path, HEADER, max_bytes=POLICY['max_selected_report_bytes'],
            max_field_bytes=POLICY['max_metadata_bytes'], max_selected_bytes=POLICY['max_metadata_bytes'])
        summary = validate_report(report, names, revision, inventory['sources'], inventory['routing_scope'], source)
        require(head(self.design) == revision, 'selected read-only check changed actual HEAD')
        self.charge(summary['work'])
        header = self.output/(name+'-header.json')
        save(header, report)
        return dict(report=identity(path), header=identity(header), selection=identity(selection)), summary

    def remaining(self):
        return self.c['limits']['deadline_seconds'] - (time.monotonic() - self.started)

    def run(self):
        current = self.c['initial_revision']
        inventory_path, initial_inventory, names = self.inventory('inventory-initial', current)
        inventory_seconds = self.commands[-1]['wall_seconds']
        require(names, 'native grouped antenna inventory is empty')
        model_path = self.command('model-initial', ['export-connectivity'], 64 << 20)
        model_seconds = self.commands[-1]['wall_seconds']
        all_names, touches = legacy.macro_nets(read(model_path))
        require(set(names) <= all_names, 'native inventory absent from actual current connectivity')
        groups = [names[i:i+POLICY['group_names']] for i in range(0, len(names), POLICY['group_names'])]
        require(len(groups) <= POLICY['max_groups'], 'native antenna group budget')
        attempts, rounds, stop, futile, skipped = [], [], None, set(), 0
        check_seconds, attempt_seconds = 0.0, 0.0

        def reserve():
            # Final inventory, model and every final group check, from the slowest
            # measured instance of each, with 50% headroom and 60 s.
            return 1.5*(inventory_seconds + model_seconds + len(groups)*check_seconds) + 60
        for iteration in range(self.c['limits']['max_rounds']):
            changed = False
            for index, group in enumerate(groups):
                if self.remaining() < reserve() + 1.5*check_seconds:
                    stop = 'deadline_reserve'
                    break
                planning, summary = self.check_group('round-'+str(iteration)+'-group-'+str(index),
                    group, current, initial_inventory)
                check_seconds = max(check_seconds, self.commands[-1]['wall_seconds'])
                eligible = sorted(n for n in summary['violating'] - touches
                    if not summary['coverage'][n]['model_partial']
                    and summary['coverage'][n]['observation'] == 'observed')
                if not eligible:
                    continue
                if tuple(eligible) in futile:
                    skipped += 1
                    continue
                if len(attempts) >= POLICY['max_attempts']:
                    stop = 'attempt_budget'
                    break
                if self.total_jumpers >= POLICY['max_total_jumpers']:
                    stop = 'jumper_budget'
                    break
                if self.work_budget_charge + 4*LIMITS['max_work'] > POLICY['max_cumulative_work']:
                    stop = 'work_budget'
                    break
                if self.remaining() < reserve() + max(attempt_seconds, 2*check_seconds):
                    stop = 'deadline_reserve'
                    break
                tick = time.monotonic()
                # Selected reports bind their exact canonical selection, not a
                # superset. Recheck only eligible names immediately before repair.
                before, fresh = self.check_group('repair-'+str(len(attempts))+'-before',
                    eligible, current, initial_inventory)
                require(fresh['violating'] == set(eligible) and all(
                    fresh['coverage'][n]['observation'] == 'observed'
                    and fresh['coverage'][n]['model_partial'] is False for n in eligible),
                    'fresh repair obligations or supported-model coverage changed')
                remaining_jumpers = POLICY['max_total_jumpers'] - self.total_jumpers
                require(remaining_jumpers > 0, 'native antenna total jumper budget')
                # In the pinned engine planner spatial and rule counters have
                # separate W bounds; final route validation has its own W cap.
                # Reserve 4W; validation is not reported and is charged as W,
                # explicitly an upper bound, never a fabricated measured zero.
                require(self.work_budget_charge + 4*LIMITS['max_work'] <= POLICY['max_cumulative_work'],
                    'native antenna repair cumulative work admission budget')
                selected = self.output/('repair-'+str(len(attempts))+'-selection.json')
                save(selected, eligible)
                base = current
                repair_path = self.command('repair-'+str(len(attempts)), ['repair-antenna',
                    '--base-rev', base, '--selected-report', before['report']['path'],
                    '--selected-nets', selected, '--safety-margin', '0.1', '--max-work', '200000000',
                    '--max-jumpers', str(remaining_jumpers), '--max-jumpers-per-net', '16',
                    '--max-positions', '512', '--max-candidates', '32'])
                repair = fields(repair_path, {'schema', 'status', 'base_revision', 'revision',
                    'routing_scope', 'repair_scope', 'selected_nets', 'requires_full_revalidation',
                    'drc_status', 'antenna_status', 'safety_margin', 'jumpers', 'stats',
                    'before', 'after', 'selected_analysis'}, max_bytes=128 << 20,
                    max_field_bytes=4 << 20, max_selected_bytes=8 << 20)
                current = head(self.design)
                require(repair['schema'] == 'rustroad.antenna-repair.v1'
                    and repair['base_revision'] == base and repair['revision'] == current
                    and repair['routing_scope'] == 'all_signal_nets'
                    and repair['repair_scope'] == 'selected_supported_nets'
                    and repair['selected_nets'] == eligible and repair['requires_full_revalidation'] is True
                    and repair['drc_status'] == 'unchecked'
                    and repair['antenna_status'] == 'partial_selected_supported_nets'
                    and repair['status'] in {'subset_clean', 'subset_repaired', 'subset_partial', 'subset_unrepaired'}
                    and repair['selected_analysis']['canonical_nets'] == eligible
                    and repair['selected_analysis']['limits'] == LIMITS
                    and repair['safety_margin'] == .1,
                    'native grouped repair identity/scope differs')
                require(isinstance(repair['jumpers'], list) and len(repair['jumpers']) <= remaining_jumpers,
                    'native grouped repair jumper budget')
                self.total_jumpers += len(repair['jumpers'])
                antenna_work, planner_work = (repair['stats'][k] for k in ('antenna_work', 'planner_work'))
                require(natural(antenna_work) and antenna_work <= LIMITS['max_work']
                    and natural(planner_work) and planner_work <= 2*LIMITS['max_work'],
                    'native repair reported work exceeds admitted bounds')
                self.charge(antenna_work + planner_work, LIMITS['max_work'])
                child = metadata(self.design, current)
                if current != base:
                    require(child['parent_revision'] == base and repair['jumpers'], 'native repair ancestry differs')
                    changed = True
                else:
                    require(not repair['jumpers'] and repair['before'] == repair['after'], 'native no-op repair changed geometry')
                attempt = dict(round=iteration, group=index, base_revision=base, revision=current,
                    planning=planning, before=before, selected=identity(selected),
                    repair_report=identity(repair_path), child=child,
                    work_accounting=dict(reported_antenna_work=antenna_work,
                        reported_planner_work=planner_work,
                        unreported_validation_work_upper_bound=LIMITS['max_work'],
                        budget_charge=antenna_work+planner_work+LIMITS['max_work']))
                attempts.append(attempt)
                if current != base:
                    rounds.append(attempt)
                else:
                    futile.add(tuple(eligible))
                attempt_seconds = max(attempt_seconds, time.monotonic() - tick)
                del repair
            if stop or not changed:
                break
        final_inventory_path, final_inventory, final_names = self.inventory('inventory-final', current)
        require(final_names == names and final_inventory['nets'] == initial_inventory['nets']
            and final_inventory['excluded_nonlogical_supply_tie_count'] == initial_inventory['excluded_nonlogical_supply_tie_count'],
            'antenna repair changed actual routed membership')
        final_model_path = self.command('model-final', ['export-connectivity'], 64 << 20)
        require(identity(final_model_path)['sha256'] == identity(model_path)['sha256'],
            'antenna repair changed actual logical connectivity')
        final_groups, covered, violating, partial, observations = [], set(), set(), False, {}
        for index, group in enumerate(groups):
            record, summary = self.check_group('final-group-'+str(index), group, current,
                final_inventory, final_inventory['source_sha256'])
            require(not covered.intersection(group), 'final antenna groups overlap')
            covered.update(group)
            violating.update(summary['violating'])
            partial |= summary['partial']
            for row in summary['coverage'].values():
                key = row['observation']
                observations[key] = observations.get(key, 0) + 1
            final_groups.append(dict(group=index, revision=current, **record))
        require(covered == set(final_names) and head(self.design) == current,
            'final antenna coverage union or actual HEAD differs')
        require(identity(self.tool['path']) == self.tool, 'native antenna tool changed')
        snapshot = self.output/'final-head.txt'
        snapshot.write_text(current+'\n')
        result = dict(schema='rusteda.native-antenna-lineage.v2',
            status='unresolved' if violating-touches else 'complete',
            scope='supported_standard_cell_subset', route_scope='all_signal_nets',
            workspace=str(self.design), tool=self.tool, initial_revision=self.c['initial_revision'],
            final_revision=current, initial=self.initial, final=metadata(self.design, current),
            workspace_head=identity(snapshot), initial_inventory=identity(inventory_path),
            final_inventory=identity(final_inventory_path), initial_model=identity(model_path),
            final_model=identity(final_model_path), policy=POLICY, rounds=rounds, attempts=attempts,
            final_groups=final_groups, inventory_status='complete',
            native_analysis_status='partial' if partial else ('violated' if violating else 'clean'),
            observations=observations, excluded_macro_touch=sorted(violating & touches),
            unresolved_supported_nets=sorted(violating-touches), requires_full_revalidation=True,
            whole_layout_antenna_status='unchecked', commands=self.commands,
            report_bytes=self.output_bytes, reported_work=self.reported_work,
            unreported_validation_work_upper_bound=self.validation_work_upper_bound,
            work_budget_charge=self.work_budget_charge,
            total_jumpers=self.total_jumpers, stop_reason=stop, skipped_futile_groups=skipped,
            wall_seconds=time.monotonic()-self.started)
        save(self.output/'lineage.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output/'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock' for p in output.iterdir()),
            'REVISION_CONFLICT')
        try:
            result = Run(read(args.inputs, 2 << 20), output).run()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            save(output/'failure.json', dict(status='failed', complete=False, error=str(error)))
            print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
            return 1
        (output/'HEAD').write_text('rev1\n')
        print(json.dumps({k: result[k] for k in ('schema', 'status', 'initial_revision',
            'final_revision', 'inventory_status', 'native_analysis_status', 'requires_full_revalidation')}))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())

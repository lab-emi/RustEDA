#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""One actual native session, independently bound logical operation artifacts.

Estimate: <=256 MiB adapter metadata plus serial native child <1.5 GiB; caller
provides4GiB/1800s scope. One <=4MiB header decoded at a time; raw check rows stream.
All original group/work/jumper/report budgets remain. Native foundry signoff is
separate. No graph/path enumeration or fabricated per-operation process receipt.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
import native_antenna as legacy
import native_antenna_groups_legacy as groups
from report_fields import fields

require, read, identity, save, head, metadata = (
    legacy.require, legacy.read, legacy.identity, legacy.save, legacy.head, legacy.metadata)
POLICY, LIMITS, HEADER = groups.POLICY, groups.LIMITS, groups.HEADER
REPAIR_FIELDS = {'schema','status','base_revision','revision','routing_scope','repair_scope',
 'selected_nets','requires_full_revalidation','drc_status','antenna_status','safety_margin',
 'jumpers','stats','before','after','selected_analysis'}


class Run(groups.Run):
    # command() is the first-party bounded capture below, with the whole session
    # using the original1800s orchestration deadline instead of a per-operation600s.
    def command(self, name, args, cap=256 << 20):
        output = self.output/(name+'.json')
        error = self.output/(name+'.stderr')
        argv = [self.tool['path'], '--workspace', str(self.design), '--threads', str(self.c['limits']['threads']),
                '--max-expansions', '8000000', *map(str, args)]
        save(self.output/(name+'-command.json'), argv)
        tick, count = time.monotonic(), 0
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
        try:
            with output.open('xb') as out, error.open('xb') as err, selectors.DefaultSelector() as poll:
                for pipe, target, maximum in ((process.stdout, out, cap), (process.stderr, err, 16 << 20)):
                    os.set_blocking(pipe.fileno(), False)
                    poll.register(pipe, selectors.EVENT_READ, (target, maximum, 0))
                while poll.get_map():
                    require(time.monotonic()-tick <= 1800 and time.monotonic()-self.started <= 1800, 'native antenna deadline')
                    for key, _ in poll.select(.1):
                        chunk = os.read(key.fileobj.fileno(), 16384)
                        if not chunk:
                            poll.unregister(key.fileobj)
                            continue
                        target, maximum, used = key.data
                        used += len(chunk)
                        require(used <= maximum, 'native report byte budget')
                        count += len(chunk)
                        target.write(chunk)
                        poll.modify(key.fileobj, selectors.EVENT_READ, (target, maximum, used))
                code = process.wait(timeout=10)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.kill()
                process.wait(timeout=10)
        record = dict(command=argv, exit_code=code, stdout=identity(output), stderr=identity(error),
            wall_seconds=time.monotonic()-tick, output_bytes=count)
        self.commands.append(record)
        require(code == 0, 'native antenna command failed: '+name)
        return output

    def run(self):
        current = self.c['initial_revision']
        plan = self.output / 'native-plan.json'
        native = self.output / 'native-reports'
        save(plan, dict(schema='rustroad.antenna-group-repair.v1',output=str(native),
            max_rounds=self.c['limits']['max_rounds'],deadline_seconds=self.c['limits']['deadline_seconds'],
            policy={key: POLICY[key] for key in ('group_names','max_groups','max_attempts',
                'max_total_jumpers','max_commands','max_report_bytes','max_cumulative_work')}))
        stdout = self.command('native-session', ['repair-antenna-groups','--base-rev',current,
            '--plan',plan], 16 << 20)
        summary = read(stdout,16 << 20)
        require(summary['schema']=='rustroad.antenna-group-repair-result.v1'
            and identity(stdout)['sha256']==identity(native/'summary.json')['sha256'],
            'native stdout/summary binding differs')
        require(summary['initial_revision']==current and summary['final_revision']==head(self.design),
            'native session actual revision differs')
        final = summary['final_revision']
        operations, check_records = [], {}
        report_bytes = 0

        def operation(name, verb, revision, selection=None, selected_report=None, base=None):
            nonlocal report_bytes
            record = identity(native/(name+'.json'),128 << 20)
            report_bytes += record['bytes']
            require(len(operations)<POLICY['max_commands'] and report_bytes<=POLICY['max_report_bytes'],
                'native logical operation count/byte budget')
            operations.append(dict(kind='native_in_process_operation',name=name,operation=verb,
                revision=revision,report=record,selection=selection,selected_report=selected_report,
                base_revision=base))
            return record

        def check(name, revision):
            path = native/(name+'.json')
            report = fields(path,HEADER,max_bytes=128 << 20,max_field_bytes=4 << 20,
                            max_selected_bytes=4 << 20)
            names = report['selection']['canonical_nets']
            selection, header = native/(name+'-selection.json'), native/(name+'-header.json')
            save(selection,names);save(header,report)
            record = dict(report=operation(name,'check-antenna',revision,identity(selection)),
                          header=identity(header),selection=identity(selection))
            check_records[name]=record
            return record

        initial_inventory = operation('inventory-initial','antenna-inventory',current)
        initial_model = operation('model-initial','export-connectivity',current)
        attempts_by_group = {(row['round'],row['group']):(number,row)
                             for number,row in enumerate(summary['attempts'])}
        require(len(attempts_by_group)==len(summary['attempts'])<=64,'native attempt identity/count')
        planning = []
        for scanned, path in enumerate(native.iterdir(),1):
            require(scanned<=POLICY['max_commands']+1,'native raw artifact count budget')
            match = re.fullmatch(r'round-(\d+)-group-(\d+)\.json',path.name)
            if match:planning.append(tuple(map(int,match.groups())))
        require(len(planning)<=POLICY['max_groups']*self.c['limits']['max_rounds'],
                'native planning operation budget')
        attempts, rounds = [], []
        for iteration,index in sorted(planning):
            planning_record = check(f'round-{iteration}-group-{index}',current)
            if (iteration,index) not in attempts_by_group:continue
            number,row = attempts_by_group[(iteration,index)]
            require(number==len(attempts) and row['base_revision']==current,'native attempt order/base differs')
            before = check(f'repair-{number}-before',current)
            selection = native/f'repair-{number}-selection.json'
            save(selection,row['selected_nets'])
            path = native/f'repair-{number}.json'
            repair = fields(path,REPAIR_FIELDS,max_bytes=128 << 20,max_field_bytes=4 << 20,
                            max_selected_bytes=8 << 20)
            record = operation(f'repair-{number}','repair-antenna',current,identity(selection),
                               before['report'],current)
            child = row['revision']
            costs = repair['stats']
            attempt = dict(round=iteration,group=index,base_revision=current,revision=child,
                planning=planning_record,before=before,selected=identity(selection),repair_report=record,
                child=metadata(self.design,child),work_accounting=dict(
                    reported_antenna_work=costs['antenna_work'],reported_planner_work=costs['planner_work'],
                    unreported_validation_work_upper_bound=LIMITS['max_work'],
                    budget_charge=costs['antenna_work']+costs['planner_work']+LIMITS['max_work']))
            attempts.append(attempt)
            if child!=current:rounds.append(attempt)
            current=child
        require(current==final and len(attempts)==len(summary['attempts']),'native final attempt chain differs')
        final_inventory = operation('inventory-final','antenna-inventory',final)
        final_model = operation('model-final','export-connectivity',final)
        final_groups=[]
        for index in range(summary['groups']):
            final_groups.append(dict(group=index,revision=final,**check(f'final-group-{index}',final)))
        require(report_bytes==summary['report_bytes'],'native logical report byte ledger differs')
        require(identity(self.tool['path'])==self.tool,'native tool changed')
        snapshot=self.output/'final-head.txt';snapshot.write_text(final+'\n')
        result = dict(schema='rusteda.native-antenna-lineage.v3',status=summary['status'],
            scope=summary['scope'],route_scope=summary['route_scope'],workspace=str(self.design),
            tool=self.tool,initial_revision=self.c['initial_revision'],final_revision=final,
            initial=self.initial,final=metadata(self.design,final),workspace_head=identity(snapshot),
            initial_inventory=initial_inventory,final_inventory=final_inventory,
            initial_model=initial_model,final_model=final_model,policy=POLICY,rounds=rounds,
            attempts=attempts,final_groups=final_groups,inventory_status=summary['inventory_status'],
            native_analysis_status=summary['native_analysis_status'],observations=summary['observations'],
            excluded_macro_touch=summary['excluded_macro_touch'],
            unresolved_supported_nets=summary['unresolved_supported_nets'],requires_full_revalidation=True,
            whole_layout_antenna_status='unchecked',commands=self.commands,operations=operations,
            execution_mode='single_native_process',actual_native_processes=1,
            logical_operations=len(operations),native_plan=identity(plan),native_summary=identity(native/'summary.json'),
            process_report_bytes=sum(row['output_bytes'] for row in self.commands),report_bytes=report_bytes,
            reported_work=summary['reported_work'],
            unreported_validation_work_upper_bound=summary['unreported_validation_work_upper_bound'],
            work_budget_charge=summary['work_budget_charge'],total_jumpers=summary['total_jumpers'],
            stop_reason=summary['stop_reason'],skipped_futile_groups=summary['skipped_futile_groups'],
            wall_seconds=time.monotonic()-self.started)
        # Include generated metadata in the disk admission; no child timings are invented.
        total,count=0,0
        for directory in (self.output,native):
            for path in directory.iterdir():
                if path==native:continue
                require(path.is_file() and not path.is_symlink(),'native evidence file scope')
                count+=1;total+=path.stat().st_size
                require(count<=3*POLICY['max_commands']+256 and total+(16 << 20)<=POLICY['max_report_bytes'],
                        'native aggregate evidence count/byte budget')
        save(self.output/'lineage.json',result)
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

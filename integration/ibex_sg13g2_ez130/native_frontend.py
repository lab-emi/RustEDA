#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Compose a fresh RTL -> dynamic ECO -> estimated-RC repair -> final ONE-CTS prefix.

Overlay09: ideal-wire ECOs (reset/fanout/primary clock) are followed by two
placement-estimated rounds. temporary: place+CTS, RC estimate, wire-aware
electrical ECO. repair: seeded place+CTS, RC estimate, propagated-clock hold
ECO (with the residual electrical pass and port diodes). final: seeded
place+CTS whose RC estimate must close DRV before routing is appended.

build --config inputs.json --workspace NEW --base-rev empty emits fragment.json
and manifest.json for producer.py --stop-after fe_frontend_validate. Config schema
rusteda.native-frontend-inputs.v1 contains hash/byte-pinned files/tools, scope,
and explicit selection policy. Optional hashed post-route capacitance feedback
selects repair loads only; all synthesis, placement and signoff reports are fresh.
All EDA operations use native ELF CLIs; preparation is bounded policy.
Full routing/RC/GDS/signoff gates must be appended by the owning flow runner.
The allocated physical pool may contain 2 or 16 workers; this prefix keeps its
qualified synthesis, import, STA and temporary/final CTS stages at two workers.
On a four-CPU allocation (a host adaptation admits it) the prefix is one
producer parallel group (frontend_parallel_group): each step waits only for
the steps whose files and workspaces it uses, and the two-corner STA runs use
all four CPUs; every command, argument (but those STA thread counts), output
and the step order are the serial prefix's. The planning helpers are native
commands of the pinned rusteda: `frontend-profiles`, `frontend-physical`,
`frontend-pins`, `frontend-validate` and, for each ECO round, `eco-round`
(bundle, plan and apply in one process). They write the files of the pinned
reference helpers (files:prepare native_frontend_inputs.py and files:ecos
native_ecos.py, which stay the definition and are still hash-pinned) byte for
byte. A hold round with post-route capacitance feedback
(selection.electrical.cap_feedback) is planned by the reference helper itself
(bundle --eco-workspace).
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import resource
import sys

FILE_KEYS = {'rtl','sdc','std_tt','ram_tt','std_ss','ram_ss','tech_lef','cell_lef','std_lef',
             'floorplan','physical','routing','interfaces','ecos','frontend','prepare',
             'delay_helper','sizing_helper','liberty_reader','pin_caps_source','producer',
             'estimator','rc_policy'}
TOOL_KEYS = {'rustsys','rusteda','ruststa','rustroad','python','gnu_time','xxhsum','pin_caps'}


def require(ok, text):
    if not ok: raise ValueError(text)


def read(path, cap=2 << 20):
    require(path.is_file() and path.stat().st_size <= cap, 'input JSON budget')
    return json.loads(path.read_text())


def identity(path):
    path = path.resolve(strict=True)
    with path.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=str(path), sha256=digest, bytes=path.stat().st_size)


def capture_policy(record):
    """Import only the explicitly pinned producer's side-effect-free policy."""
    path = Path(record['path'])
    require(identity(path) == record and record['bytes'] <= 2 << 20, 'producer policy changed')
    spec = importlib.util.spec_from_file_location('native_capture_policy', path)
    require(spec is not None and spec.loader is not None, 'producer policy loader')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(identity(path) == record, 'producer policy changed during import')
    return module


def build(config):
    require(config['schema'] == 'rusteda.native-frontend-inputs.v1', 'frontend config schema')
    require(set(config)=={'schema','files','tools','scope','selection'},'unknown frontend config field')
    feedback = config['selection'].get('electrical', {}).get('cap_feedback')
    feedback_keys = set()
    if feedback is not None:
        require(config['files'].get('cap_feedback') == feedback, 'cap feedback must be a frozen input')
        evidence = read(Path(feedback['path']), 1 << 20)
        roles = {'drv', 'spef', 'timing_inputs', 'repair_def', 'final_def', 'generator', 'drv_ss', 'corner_coverage'}
        require(set(evidence['source_files']) == roles, 'cap feedback provenance roles')
        feedback_keys = {'cap_feedback'} | {'cap_feedback_' + role for role in roles}
        require(all(config['files'].get('cap_feedback_' + role) == record
                    for role, record in evidence['source_files'].items()), 'cap feedback sources must be frozen inputs')
    require(set(config['files']) == FILE_KEYS | feedback_keys and set(config['tools']) == TOOL_KEYS, 'explicit frontend file/tool set required')
    require(type(config['scope']['threads']) is int and config['scope']['threads'] in {2,16},
            'physical allocation must be 2 or 16; qualified frontend stages use two workers')
    for group in ['files','tools']:
        for name, record in config[group].items():
            require(identity(Path(record['path'])) == record, 'input changed: '+name)
            require(record['bytes']<= (256<<20 if group=='tools' else 64<<20),'input byte budget: '+name)
            if group == 'tools':
                with Path(record['path']).open('rb') as f: require(f.read(4) == b'\x7fELF', 'direct native ELF required')
    files, tools = config['files'], config['tools']
    four = 4 <= config['scope']['threads'] < 6
    steps, lanes = recipe(config, four)
    require(len(steps)<=128,'producer step budget')
    require(len({s['id']for s in steps})==len(steps),'duplicate producer step identity')
    policy = capture_policy(files['producer'])
    steps, stdout_artifacts = policy.declare_stdout_artifacts(steps)
    policy.validate_stdout_artifacts(steps, stdout_artifacts, config['scope'])
    groups=[frontend_parallel_group(steps, lanes)] if four else []
    if groups:policy.validate_parallel_groups(steps, groups, config['scope'])
    outputs=dict(final_workspace='frontend/road-final',final_netlist='logs/fe_final_export.stdout',
                 prects_netlist='frontend/eco-hold/netlist.v',temporary_seed='logs/fe_temporary_seed.stdout',
                 final_seed='frontend/eco-hold/seed.json',final_estimate='frontend/estimate-final/result.json',
                 temporary_workspace='frontend/road-temporary',final_sta_workspace='frontend/sta-final',
                 receipt='frontend/result/result.json',stop_after='fe_frontend_validate')
    artifacts={k:v for k,v in outputs.items() if k in ['final_netlist','prects_netlist','temporary_seed','receipt']}
    fragment=dict(schema='rusteda.native-frontend-fragment.v1',files=files,tools=tools,steps=steps,outputs=outputs,artifacts=artifacts,
                  stdout_artifacts=stdout_artifacts,scope=config['scope'],unverified=['route','extracted RC','GDS','DRC','antenna','LVS','extracted_timing','CEC','benchmark'])
    manifest=dict(schema='rusteda.ihp-flow.v1',name='fresh Ibex IHP native frontend',pdk='ihp-sg13g2',library='ez130_8t',corners=['tt','ss'],files=files,tools=tools,scope=config['scope'],steps=steps,gates={},artifacts=artifacts,stdout_artifacts=stdout_artifacts)
    if groups:fragment['parallel_groups']=manifest['parallel_groups']=groups
    return fragment,manifest


# The validate step reads every round's outputs and the producer's step receipts; it runs after the
# group, so --stop-after fe_frontend_validate leaves the group concurrent.
FRONTEND_GROUP_EXCLUDES = ('fe_frontend_validate',)
# Deadline of an ECO round's one process (bundle, then the ECO stage): the two former steps'
# 180 s each.
ECO_STEP_SECONDS = 360


def frontend_parallel_group(steps, lanes):
    """The frontend prefix of a four-CPU allocation as one producer parallel group.

    `lanes` holds each step's dependencies (the steps that write the files and workspaces it
    reads), CPU slots and memory reservation, written next to the step in `recipe`. Every step but
    validate is in the group, in manifest order; a step without dependencies starts at once.
    Tools that run two workers take two slots, the two-corner STA runs and the ECO rounds (which
    hash their inputs on several threads) all four, the profiles two, the other helpers and the
    read-only exports and reports one. At most four steps run at once; the reservations of any four
    (512 MiB at most each, measured peaks below 180 MiB) stay under the 7168 MiB scheduling cap.
    """
    members={s['id']:lanes[s['id']] for s in steps if s['id'] not in FRONTEND_GROUP_EXCLUDES}
    require(list(members)==[s['id'] for s in steps][:len(members)],'frontend group must be the manifest prefix')
    return dict(max_parallel=4,max_memory_mib=7168,steps=members)


def recipe(config, four):
    """The prefix's steps and, for frontend_parallel_group, each step's dependencies, CPU slots and
    memory reservation. `four`: a four-CPU allocation (one dependency graph, four-thread STA runs)."""
    files, tools, steps = config['files'], config['tools'], []
    out = '{output}/frontend/'
    # A four-CPU allocation runs the prefix as one dependency graph (frontend_parallel_group): every
    # step keeps its arguments, order and outputs, and waits only for the steps whose files it
    # reads. Its two-corner STA runs then take all four CPUs (--threads 4; the STA tables do not
    # depend on the worker count). Two- and sixteen-CPU allocations keep the serial prefix.
    sta_threads = '4' if four else '2'
    lanes = {}
    def command(name, tool, args, seconds=180, after=(), slots=(0, 1), memory=512):
        if tool=='rusteda' and '--threads' not in args: args += ['--threads','2']
        steps.append(dict(id='fe_'+name, kind='command', tool=tool, arguments=args, timeout_s=seconds))
        lanes['fe_'+name] = dict(depends_on=list(dict.fromkeys(after)), cpu_slots=list(slots), memory_mib=memory)
        return 'fe_'+name
    def render(name, dest, data):
        steps.append(dict(id='fe_'+name, kind='render', destination='frontend/'+dest, content=data))
        lanes['fe_'+name] = dict(depends_on=[])
        return 'fe_'+name
    base_context = dict(schema='rusteda.native-frontend-context.v1', run_root='{output}',
                        files={k:{'$input':k} for k in files}, tools={k:{'$tool':k} for k in tools},
                        selection=config['selection'])
    context=render('context', 'context.json', base_context)
    # A planning helper: the native form (`rusteda frontend-<name>`) of the pinned reference
    # helper's command of that name (files:prepare), writing the same files byte for byte.
    def helper(step, name, workspace, extra=(), **lane):
        return command(step,'rusteda',['frontend-'+name,'--context',out+'context.json','--workspace',out+workspace,'--base-rev','empty',*extra],**lane)
    # The helpers of the first moments share the two CPUs that synthesis leaves free: the
    # profiles (the run's first helper, which hashes every pinned input) take CPU 2 with the
    # two small SRAM pin tables; the two standard-cell pin tables and the timing workspaces'
    # inits take CPU 3, side by side.
    profiles=helper('profiles','profiles','profiles',after=[context],slots=[2])
    caps=[]
    for corner in ['tt','ss']:
        for kind,key in [('std','std_'+corner),('ram','ram_'+corner)]:
            caps.append(command('caps_'+corner+'_'+kind,'pin_caps',['{files:'+key+'}'],slots=[2 if kind=='ram' else 3],memory=256))
    ws=out+'synth'
    init=command('synth_init','rusteda',['init','-w',ws,'--format','json'],slots=[0],memory=128)
    # The run's Liberty compile cache (see the timing imports below): the synthesis import publishes
    # its two TT libraries there, while the timing steps wait for synthesis, so the first timing
    # import compiles only the SS libraries (its workspace files are the same either way).
    lib_cache=out+'lib-cache'
    imp=command('synth_import','rusteda',['import','-w',ws,'--base-rev','rev0','--threads','2','--format','json','--lib-cache',lib_cache,'--lib','{files:std_tt}','--corner','tt','--lib','{files:ram_tt}','--corner','tt','--rtl','{files:rtl}','--top',config['selection']['top']],after=[init])
    synth=command('synth','rustsys',['--workspace',ws,'synth','--base-rev','rev1','--threads','2'],600,after=[imp])
    mapped=command('synth_export','rusteda',['export','-w',ws,'--rev','rev2','--netlist',out+'mapped.v','--cell-buses',out+'profiles/cell-buses.json'],after=[synth,profiles],slots=[0])
    libargs=[]
    for c in ['tt','ss']:
        for k in ['std','ram']:libargs += ['--lib','{files:'+k+'_'+c+'}','--corner',c]
    # The timing imports share one Liberty compile cache in this run's tree: a library compiled
    # by an earlier timing import of the run is copied into the next workspace instead of parsed
    # again (rusteda import --lib-cache; the workspace cache files are the same either way). The
    # synthesis import publishes the TT libraries, so fe_mapped_import compiles the two SS ones.
    # The cache writes each file under a temporary name and renames it, so imports may run beside
    # each other.
    # STA steps by timing tag: the graph step and every report the next ECO bundle reads.
    graph_step,report_steps={},{}
    def timing(tag, source, hold=False, run=True, spef=None, after=()):
        ws=out+'sta-'+tag
        init=command(tag+'_init','rusteda',['init','-w',ws,'--format','json'],slots=[3],memory=128)
        imp=['import','-w',ws,'--base-rev','rev0','--threads','2','--format','json','--lib-cache',lib_cache]
        if spef:imp+=['--max-spef-mib','64','--max-rc-name-mib','64','--max-rc-nodes','2000000','--max-rc-elements','4000000','--max-rc-nets','100000']
        for c in ['tt','ss']:
            for k in ['std','ram']:imp+=['--lib','{files:'+k+'_'+c+'}','--corner',c]
            if spef:imp+=['--spef',spef,'--spef-corner',c]
        last=command(tag+'_import','rusteda',imp+['--netlist',source,'--sdc','{files:sdc}'],after=[init,*after],slots=[2,3])
        # The two corners share the run's workers (--concurrent-corners): same tables, less wall time.
        if run: last=command(tag+'_run','ruststa',['run','-w',ws,'--base-rev','rev1','--threads',sta_threads,'--concurrent-corners','--max-state-gib','2','--max-paths','32','--max-limit-rows','100000','--require-wire-model','spef_rc' if spef else 'ideal','--format','json'],after=[last],slots=[0,1,2,3] if four else [0,1])
        # The graph and the reports only read the committed revision (shared workspace locks).
        # The hold report, the longest, keeps CPU 3 to itself; the others share CPUs 0 to 2.
        lane=iter([0,1,2]*3)
        graph_step[tag]=command(tag+'_graph','rusteda',['export','-w',ws,'--rev','rev2' if run else 'rev1','--kind','connectivity-json','--cell-buses',out+'profiles/cell-buses.json','--netlist',out+tag+'-graph.json','--format','json'],after=[last,profiles],slots=[next(lane)])
        report_steps[tag]=[]
        if run:
            for c in ['tt','ss']:
                # Estimated rounds also list near-limit rows for the ECO's DRV margin.
                report_steps[tag].append(command(tag+'_'+c+'_drv','ruststa',['report','-w',ws,'--corner',c,'--threads','2','--section','drv','--filter','slack<0.2' if spef else 'violating','--limit','100000','--format','json'],after=[last],slots=[next(lane)]))
                if hold:report_steps[tag].append(command(tag+'_'+c+'_endpoints','ruststa',['report','-w',ws,'--corner',c,'--threads','2','--section','endpoints','--limit','0','--format','json'],after=[last],slots=[next(lane)]))
            # Endpoints below 0.5 ns: the hold ECO also charges arcs its own DRV repairs may shorten.
            if hold:report_steps[tag].append(command(tag+'_hold','ruststa',['report','-w',ws,'--threads','2','--section','hold_fix','--target-margin','0.5','--limit','0','--format','json'],after=[last],slots=[3]))
        return ws
    eco_step={}
    feedback=config['selection'].get('electrical',{}).get('cap_feedback') is not None
    def eco(op, tag, source, timed=None, graph_tag=None, after=()):
        # One process bundles, plans and applies: `rusteda eco-round` is the native form of the
        # reference helper's `bundle --eco-workspace` (native_frontend_inputs.py, then the ECO
        # stage of native_ecos.py): the bundle and ECO files are those helpers' byte for byte
        # (but the receipt's run time, peak memory and planner hash). The run's flow digest cache
        # spares hashing the pinned tools and libraries first; the final input check still reads
        # every file in full (the settled ones beside the apply step).
        # The bundle binds the pin tables, this tag's reports (and placement estimate) and the
        # graph of graph_tag; `after` names the producers of its source, timed netlist, placement
        # and estimate, and, after the first round, the reset round's root policy.
        # The step's deadline is the sum of the two steps' it replaces (180 s each): the bundle
        # has 180 s, and the ECO stage then its 180 s planner budget.
        common=['--operation',op,'--timing-tag',tag,'--source',source,'--graph',out+(graph_tag or tag)+'-graph.json']
        if timed: common += ['--timed',timed]
        lane=dict(after=[context,profiles,*caps,graph_step[graph_tag or tag],*report_steps[tag],*after],slots=[0,1,2,3] if four else [0,1])
        if op=='hold' and feedback:
            # Post-route capacitance feedback binds the hold round to an extracted witness: that
            # variant is planned by the reference helper.
            eco_step[op]=command('eco_'+op,'python',['{files:prepare}','bundle','--context',out+'context.json','--workspace',out+'bundle-'+op,'--base-rev','empty',*common,'--eco-workspace',out+'eco-'+op,'--digest-cache',out+'sha256-cache.json'],ECO_STEP_SECONDS,**lane)
        else:
            eco_step[op]=command('eco_'+op,'rusteda',['eco-round','--context',out+'context.json','--bundle',out+'bundle-'+op,'--workspace',out+'eco-'+op,'--base-rev','empty',*common],ECO_STEP_SECONDS,**lane)
        return out+'eco-'+op+'/netlist.v'
    source=out+'mapped.v'; timing('mapped',source,after=[mapped])
    source=eco('reset','mapped',source,after=[mapped])
    timing('reset',source,after=[eco_step['reset']]); source=eco('fanout','reset',source,after=[eco_step['reset']])
    timing('fanout',source,after=[eco_step['fanout']]); source=eco('primary-clock','fanout',source,after=[eco_step['fanout'],eco_step['reset']])
    timing('primary',source,run=False,after=[eco_step['primary-clock']])
    physical_config=helper('physical_config','physical','physical',['--roots',out+'bundle-reset/roots.json'],after=[context,eco_step['reset']],slots=[1])
    export_step,seed_step,def_step,estimate_step={},{},{},{}
    def physical(tag, source, seed=None, weights=None, config='physical', after=()):
        ws=out+'road-'+tag
        args=['--workspace',ws,'--threads','2','--max-expansions','4000000']
        last=command(tag+'_pdk_import','rustroad',args+['import-pdk','--base-rev','rev0','--tech-lef','{files:tech_lef}','--cell-lef','{files:cell_lef}','--netlist',source,'--floorplan','{files:floorplan}','--physical-config',out+config+'/physical.json'],after=after)
        place=args+['place','--base-rev','rev1']
        if seed:place += ['--seed',seed]
        if weights:place += ['--net-weights',weights]
        last=command(tag+'_place','rustroad',place,after=[last])
        last=command(tag+'_cts','rustroad',args+['clock-tree','--base-rev','rev2','--sdc',out+'profiles/cts.sdc'],after=[last,profiles])
        export_step[tag]=command(tag+'_export','rustroad',['--workspace',ws,'export-verilog'],after=[last],slots=[0])
        return ws,'{output}/logs/fe_'+tag+'_export.stdout'
    def estimate(tag, ws, seed=True):
        cts='fe_'+tag+'_cts'
        if seed:seed_step[tag]=command(tag+'_seed','rustroad',['--workspace',ws,'export-placement-seed'],after=[cts],slots=[1])
        def_step[tag]=command(tag+'_def','rustroad',['--workspace',ws,'export-def'],after=[cts],slots=[2])
        # `rusteda estimate-spef` is the native form of the pinned estimator (files:estimator):
        # the same estimate.spef, result.json and summary line, byte for byte, from the same
        # DEF, LEF and policy.
        estimate_step[tag]=command(tag+'_estimate','rusteda',['estimate-spef','--def','{output}/logs/fe_'+tag+'_def.stdout','--lef','{files:cell_lef}','--policy','{files:rc_policy}','--workspace',out+'estimate-'+tag,'--base-rev','empty'],after=[def_step[tag]],slots=[2])
        return out+'estimate-'+tag+'/estimate.spef'
    # overlay16: one pin-planning placement moves every signal pin next to its loads before the
    # temporary round, so the electrical ECO seeds its isolation buffers beside the planned pins.
    plan=['--workspace',out+'road-pins','--threads','2','--max-expansions','4000000']
    last=command('pins_pdk_import','rustroad',plan+['import-pdk','--base-rev','rev0','--tech-lef','{files:tech_lef}','--cell-lef','{files:cell_lef}','--netlist',source,'--floorplan','{files:floorplan}','--physical-config',out+'physical/physical.json'],after=[physical_config,eco_step['primary-clock']])
    last=command('pins_place','rustroad',plan+['place','--base-rev','rev1'],after=[last])
    last=command('pins_def','rustroad',['--workspace',out+'road-pins','export-def'],after=[last],slots=[0])
    pins=helper('pins','pins','physical-pins',['--def','{output}/logs/fe_pins_def.stdout'],after=[context,physical_config,last],slots=[0])
    temp,temp_net=physical('temporary',source,config='physical-pins',after=[pins,eco_step['primary-clock']])
    # overlay14: the electrical round balances ungated launch clocks from this round's hold report.
    timing('temporary',temp_net,hold=True,spef=estimate('temporary',temp),after=[export_step['temporary'],estimate_step['temporary']])
    estimated=lambda tag:[export_step[tag],seed_step[tag],def_step[tag],estimate_step[tag]]
    repair_source=eco('electrical','temporary',source,timed=temp_net,graph_tag='primary',after=[eco_step['primary-clock'],eco_step['reset'],*estimated('temporary')])
    timing('electrical',repair_source,run=False,after=[eco_step['electrical']])
    repair,repair_net=physical('repair',repair_source,'{output}/frontend/eco-electrical/seed.json','{output}/frontend/eco-electrical/weights.json','physical-pins',after=[pins,eco_step['electrical']])
    timing('repair',repair_net,hold=True,spef=estimate('repair',repair),after=[export_step['repair'],estimate_step['repair']])
    final_source=eco('hold','repair',repair_source,timed=repair_net,graph_tag='electrical',after=[eco_step['electrical'],eco_step['reset'],*estimated('repair')])
    final,final_net=physical('final',final_source,'{output}/frontend/eco-hold/seed.json','{output}/frontend/eco-hold/weights.json','physical-pins',after=[pins,eco_step['hold']])
    command('final_inspect','rustroad',['--workspace',final,'inspect'],after=['fe_final_cts'],slots=[1])
    timing('final',final_net,spef=estimate('final',final,seed=False),after=[export_step['final'],estimate_step['final']])
    helper('frontend_validate','validate','result')
    return steps, lanes


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['build']);p.add_argument('--config',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--base-rev',required=True);args=p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS,(512<<20,512<<20));args.workspace.mkdir(parents=True,exist_ok=True)
    with (args.workspace/'workspace.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);require(args.base_rev=='empty' and not any(x.name!='workspace.lock'for x in args.workspace.iterdir()),'REVISION_CONFLICT')
        fragment,manifest=build(read(args.config))
        for name,value in [('fragment.json',fragment),('manifest.json',manifest)]:
            (args.workspace/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
        (args.workspace/'HEAD').write_text('rev1\n');print(json.dumps(fragment['outputs']))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError) as error:
        print(json.dumps(dict(status='failed',error=str(error))),file=sys.stderr);raise SystemExit(2)

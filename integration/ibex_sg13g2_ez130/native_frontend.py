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
    files, tools, steps = config['files'], config['tools'], []
    out = '{output}/frontend/'
    def command(name, tool, args, seconds=180):
        if tool=='rusteda' and '--threads' not in args: args += ['--threads','2']
        steps.append(dict(id='fe_'+name, kind='command', tool=tool, arguments=args, timeout_s=seconds))
    def render(name, dest, data):
        steps.append(dict(id='fe_'+name, kind='render', destination='frontend/'+dest, content=data))
    base_context = dict(schema='rusteda.native-frontend-context.v1', run_root='{output}',
                        files={k:{'$input':k} for k in files}, tools={k:{'$tool':k} for k in tools},
                        selection=config['selection'])
    render('context', 'context.json', base_context)
    command('profiles','python',['{files:prepare}','profiles','--context',out+'context.json','--workspace',out+'profiles','--base-rev','empty'])
    for corner in ['tt','ss']:
        for kind,key in [('std','std_'+corner),('ram','ram_'+corner)]:
            command('caps_'+corner+'_'+kind,'pin_caps',['{files:'+key+'}'])
    ws=out+'synth'
    command('synth_init','rusteda',['init','-w',ws,'--format','json'])
    command('synth_import','rusteda',['import','-w',ws,'--base-rev','rev0','--threads','2','--format','json','--lib','{files:std_tt}','--corner','tt','--lib','{files:ram_tt}','--corner','tt','--rtl','{files:rtl}','--top',config['selection']['top']])
    command('synth','rustsys',['--workspace',ws,'synth','--base-rev','rev1','--threads','2'],600)
    command('synth_export','rusteda',['export','-w',ws,'--rev','rev2','--netlist',out+'mapped.v','--cell-buses',out+'profiles/cell-buses.json'])
    libargs=[]
    for c in ['tt','ss']:
        for k in ['std','ram']:libargs += ['--lib','{files:'+k+'_'+c+'}','--corner',c]
    def timing(tag, source, hold=False, run=True, spef=None):
        ws=out+'sta-'+tag
        command(tag+'_init','rusteda',['init','-w',ws,'--format','json'])
        imp=['import','-w',ws,'--base-rev','rev0','--threads','2','--format','json']
        if spef:imp+=['--max-spef-mib','64','--max-rc-name-mib','64','--max-rc-nodes','2000000','--max-rc-elements','4000000','--max-rc-nets','100000']
        for c in ['tt','ss']:
            for k in ['std','ram']:imp+=['--lib','{files:'+k+'_'+c+'}','--corner',c]
            if spef:imp+=['--spef',spef,'--spef-corner',c]
        command(tag+'_import','rusteda',imp+['--netlist',source,'--sdc','{files:sdc}'])
        if run: command(tag+'_run','ruststa',['run','-w',ws,'--base-rev','rev1','--threads','2','--max-state-gib','2','--max-paths','32','--max-limit-rows','100000','--require-wire-model','spef_rc' if spef else 'ideal','--format','json'])
        command(tag+'_graph','rusteda',['export','-w',ws,'--rev','rev2' if run else 'rev1','--kind','connectivity-json','--cell-buses',out+'profiles/cell-buses.json','--netlist',out+tag+'-graph.json','--format','json'])
        if run:
            for c in ['tt','ss']:
                # Estimated rounds also list near-limit rows for the ECO's DRV margin.
                command(tag+'_'+c+'_drv','ruststa',['report','-w',ws,'--corner',c,'--threads','2','--section','drv','--filter','slack<0.2' if spef else 'violating','--limit','100000','--format','json'])
                if hold:command(tag+'_'+c+'_endpoints','ruststa',['report','-w',ws,'--corner',c,'--threads','2','--section','endpoints','--limit','0','--format','json'])
            # Endpoints below 0.5 ns: the hold ECO also charges arcs its own DRV repairs may shorten.
            if hold:command(tag+'_hold','ruststa',['report','-w',ws,'--threads','2','--section','hold_fix','--target-margin','0.5','--limit','0','--format','json'])
        return ws
    def eco(op, tag, source, timed=None, graph_tag=None):
        args=['{files:prepare}','bundle','--context',out+'context.json','--workspace',out+'bundle-'+op,'--base-rev','empty','--operation',op,'--timing-tag',tag,'--source',source,'--graph',out+(graph_tag or tag)+'-graph.json']
        if timed: args += ['--timed',timed]
        command('bundle_'+op,'python',args)
        command('eco_'+op,'python',['{files:ecos}',op,'--workspace',out+'eco-'+op,'--base-rev','empty','--inputs',out+'bundle-'+op+'/inputs.json','--policy',out+'bundle-'+op+'/policy.json'])
        return out+'eco-'+op+'/netlist.v'
    source=out+'mapped.v'; timing('mapped',source)
    source=eco('reset','mapped',source)
    timing('reset',source); source=eco('fanout','reset',source)
    timing('fanout',source); source=eco('primary-clock','fanout',source)
    timing('primary',source,run=False)
    command('physical_config','python',['{files:prepare}','physical','--context',out+'context.json','--workspace',out+'physical','--base-rev','empty','--roots',out+'bundle-reset/roots.json'])
    def physical(tag, source, seed=None, weights=None, config='physical'):
        ws=out+'road-'+tag
        args=['--workspace',ws,'--threads','2','--max-expansions','4000000']
        command(tag+'_pdk_import','rustroad',args+['import-pdk','--base-rev','rev0','--tech-lef','{files:tech_lef}','--cell-lef','{files:cell_lef}','--netlist',source,'--floorplan','{files:floorplan}','--physical-config',out+config+'/physical.json'])
        place=args+['place','--base-rev','rev1']
        if seed:place += ['--seed',seed]
        if weights:place += ['--net-weights',weights]
        command(tag+'_place','rustroad',place)
        command(tag+'_cts','rustroad',args+['clock-tree','--base-rev','rev2','--sdc',out+'profiles/cts.sdc'])
        command(tag+'_export','rustroad',['--workspace',ws,'export-verilog'])
        return ws,'{output}/logs/fe_'+tag+'_export.stdout'
    def estimate(tag, ws, seed=True):
        if seed:command(tag+'_seed','rustroad',['--workspace',ws,'export-placement-seed'])
        command(tag+'_def','rustroad',['--workspace',ws,'export-def'])
        command(tag+'_estimate','python',['{files:estimator}','--def','{output}/logs/fe_'+tag+'_def.stdout','--lef','{files:cell_lef}','--policy','{files:rc_policy}','--workspace',out+'estimate-'+tag,'--base-rev','empty'])
        return out+'estimate-'+tag+'/estimate.spef'
    # overlay16: one pin-planning placement moves every signal pin next to its loads before the
    # temporary round, so the electrical ECO seeds its isolation buffers beside the planned pins.
    plan=['--workspace',out+'road-pins','--threads','2','--max-expansions','4000000']
    command('pins_pdk_import','rustroad',plan+['import-pdk','--base-rev','rev0','--tech-lef','{files:tech_lef}','--cell-lef','{files:cell_lef}','--netlist',source,'--floorplan','{files:floorplan}','--physical-config',out+'physical/physical.json'])
    command('pins_place','rustroad',plan+['place','--base-rev','rev1'])
    command('pins_def','rustroad',['--workspace',out+'road-pins','export-def'])
    command('pins','python',['{files:prepare}','pins','--context',out+'context.json','--workspace',out+'physical-pins','--base-rev','empty','--def','{output}/logs/fe_pins_def.stdout'])
    temp,temp_net=physical('temporary',source,config='physical-pins')
    # overlay14: the electrical round balances ungated launch clocks from this round's hold report.
    timing('temporary',temp_net,hold=True,spef=estimate('temporary',temp))
    repair_source=eco('electrical','temporary',source,timed=temp_net,graph_tag='primary')
    timing('electrical',repair_source,run=False)
    repair,repair_net=physical('repair',repair_source,'{output}/frontend/eco-electrical/seed.json','{output}/frontend/eco-electrical/weights.json','physical-pins')
    timing('repair',repair_net,hold=True,spef=estimate('repair',repair))
    final_source=eco('hold','repair',repair_source,timed=repair_net,graph_tag='electrical')
    final,final_net=physical('final',final_source,'{output}/frontend/eco-hold/seed.json','{output}/frontend/eco-hold/weights.json','physical-pins')
    command('final_inspect','rustroad',['--workspace',final,'inspect'])
    timing('final',final_net,spef=estimate('final',final,seed=False))
    command('frontend_validate','python',['{files:prepare}','validate','--context',out+'context.json','--workspace',out+'result','--base-rev','empty'])
    require(len(steps)<=128,'producer step budget')
    require(len({s['id']for s in steps})==len(steps),'duplicate producer step identity')
    policy = capture_policy(files['producer'])
    steps, stdout_artifacts = policy.declare_stdout_artifacts(steps)
    policy.validate_stdout_artifacts(steps, stdout_artifacts, config['scope'])
    outputs=dict(final_workspace='frontend/road-final',final_netlist='logs/fe_final_export.stdout',
                 prects_netlist='frontend/eco-hold/netlist.v',temporary_seed='logs/fe_temporary_seed.stdout',
                 final_seed='frontend/eco-hold/seed.json',final_estimate='frontend/estimate-final/result.json',
                 temporary_workspace='frontend/road-temporary',final_sta_workspace='frontend/sta-final',
                 receipt='frontend/result/result.json',stop_after='fe_frontend_validate')
    artifacts={k:v for k,v in outputs.items() if k in ['final_netlist','prects_netlist','temporary_seed','receipt']}
    fragment=dict(schema='rusteda.native-frontend-fragment.v1',files=files,tools=tools,steps=steps,outputs=outputs,artifacts=artifacts,
                  stdout_artifacts=stdout_artifacts,scope=config['scope'],unverified=['route','extracted RC','GDS','DRC','antenna','LVS','extracted_timing','CEC','benchmark'])
    manifest=dict(schema='rusteda.ihp-flow.v1',name='fresh Ibex IHP native frontend',pdk='ihp-sg13g2',library='ez130_8t',corners=['tt','ss'],files=files,tools=tools,scope=config['scope'],steps=steps,gates={},artifacts=artifacts,stdout_artifacts=stdout_artifacts)
    return fragment,manifest


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

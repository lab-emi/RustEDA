#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Bounded preparation/binding for native_frontend.py; never edits Verilog.

Every dynamic source/report/graph/seed must be inside the current producer run.
Profile LUT/LEF operations call the existing first-party Road policy helpers.
Overlay09: electrical and hold bundles bind the estimated-RC timing of their
tag (placed DEF, placement seed, estimate SPEF and policy); validate requires
the final placement's estimated DRV to be closed and records its hold.
Overlay16: `pins` plans every signal pin from a pin-planning placement: the
nearest core side of its load centroid (strongest preference first, as the
Road floorplan derivation does) with the centroid as its along-side target.
The temporary, repair and final rounds all import that plan.
"""
from __future__ import annotations
import argparse,csv,fcntl,hashlib,importlib.util,json,os,re,resource,signal,sys
from pathlib import Path
from types import SimpleNamespace


def require(ok,text):
    if not ok:raise ValueError(text)


def read(p,cap=64<<20):
    require(p.is_file() and p.stat().st_size<=cap,'JSON byte budget: '+str(p));return json.loads(p.read_text())


def rec(p):
    p=p.resolve(strict=True)
    with p.open('rb')as f:h=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(path=str(p),sha256=h,bytes=p.stat().st_size)


def write(p,data):
    p.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def context(path):
    c=read(path,2<<20);require(c['schema']=='rusteda.native-frontend-context.v1','context schema')
    for group in ['files','tools']:
        for name,item in c[group].items():
            cap=256<<20 if group=='tools'else (2<<20 if name in ['sdc','interfaces','floorplan','physical','routing','ecos','frontend','prepare','producer','delay_helper','sizing_helper','liberty_reader','pin_caps_source','estimator','rc_policy']else 64<<20)
            require(item['bytes']<=cap,'input byte budget: '+name)
            require(rec(Path(item['path']))==item,'pinned input changed: '+name)
    require(sum(c['files'][k]['bytes']for k in ['std_tt','std_ss','ram_tt','ram_ss'])<=64<<20,'aggregate Liberty budget')
    c['root']=Path(c['run_root']).resolve();return c


def fresh(path,base):
    path.mkdir(parents=True,exist_ok=True);lock=(path/'workspace.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    require(base=='empty' and not any(p.name!='workspace.lock'for p in path.iterdir()),'REVISION_CONFLICT');return lock


def dynamic(c,path):
    p=Path(path).resolve(strict=True);require(p.is_relative_to(c['root']),'historical/external dynamic input forbidden');return p


def paths(c):return {k:Path(v['path'])for k,v in c['files'].items()}


def io_and_clock(sdc,selection):
    drivers,loads={},{};clock={}
    for line in sdc.read_text().splitlines():
        line=line.strip()
        if line.startswith('set_units'):raise ValueError('explicit IO projection requires ns/pF source units')
        if line.startswith('set_driving_cell'):
            m=re.fullmatch(r'set_driving_cell -lib_cell (\S+) -pin \{([^{}]+)\} -input_transition_rise ([0-9.eE+-]+) -input_transition_fall ([0-9.eE+-]+) \[get_ports \{([^{}]+)\}\]',line)
            require(m is not None,'unsupported literal driving-cell projection');drivers[m[5]]=dict(cell=m[1],pin=m[2])
        if line.startswith('set_load'):
            m=re.fullmatch(r'set_load -pin_load ([0-9.eE+-]+) \[get_ports \{([^{}]+)\}\]',line)
            require(m is not None,'unsupported literal load projection');loads[m[2]]=float(m[1])
        if line.startswith('create_clock'):
            m=re.fullmatch(r'create_clock -name (\S+) -period ([0-9.eE+-]+) \[get_ports \{([^{}]+)\}\]',line)
            require(m is not None and not clock,'one literal primary clock required');clock=dict(name=m[1],period=float(m[2]),port=m[3])
        if line.startswith('set_clock_transition'):clock['transition']=float(line.split()[1])
        if line.startswith('set_clock_uncertainty'):clock['uncertainty']=float(line.split()[1])
        if line.startswith('set_max_fanout'):clock['fanout']=float(line.split()[1])
    require(clock.get('name')==clock.get('port')==selection['primary_clock'] and set(clock)=={'name','port','period','transition','uncertainty','fanout'},'incomplete original clock projection')
    n=clock['name'];projection=f'# CTS-only literal projection; full original SDC remains mandatory for STA.\ncreate_clock -name {n} -period {clock["period"]} [get_ports {{{n}}}]\nset_clock_transition {clock["transition"]} [get_clocks {{{n}}}]\nset_clock_uncertainty {clock["uncertainty"]} [get_clocks {{{n}}}]\nset_propagated_clock [get_clocks {{{n}}}]\nset_max_fanout {clock["fanout"]} [current_design]\n'
    return dict(sdc_sha256=rec(sdc)['sha256'],drivers=drivers,loads_pf=loads),projection,clock


def profiles(c,out):
    f=paths(c);selection=c['selection'];sys.path.insert(0,str(f['liberty_reader'].parent))
    delay=module('frontend_delay',f['delay_helper']);sizing=module('frontend_sizing',f['sizing_helper'])
    names=[x['cell']for x in selection['buffers']]+selection['delay_cells'];require(len(names)<=32 and len(set(names))==len(names),'explicit cell selection budget')
    diode=selection.get('port_diode');require(diode is None or (set(diode)=={'cell','pin'} and diode['cell']not in names),'port diode selection')
    libs={corner:delay.lib.Library(f['std_'+corner],wanted=set(names)|({diode['cell']}if diode else set()))for corner in ['tt','ss']}
    macros=delay.lef_macros(f['std_lef']);widths=sizing.lef_widths(f['std_lef'],1000)
    antenna=[]
    if diode:
        # Same admission as build_delay_cells.py: LEF ANTENNACELL, one INPUT signal pin
        # with ANTENNADIFFAREA, and a function-free single-pin Liberty cell.
        n,pin=diode['cell'],diode['pin'];m=macros.get(n)or{};signal={k:v for k,v in m.get('pins',{}).items()if v['use']=='SIGNAL'}
        require(m.get('cls')=='CORE ANTENNACELL' and list(signal)==[pin] and signal[pin]['direction']=='INPUT' and signal[pin]['diff_area_um2'],'port diode is not a single-input LEF antenna cell')
        for co,lib in libs.items():
            cell=lib.cells.get(n);pins=cell.sub('pin')if cell else[]
            require(cell is not None and [g.name for g in pins]==[pin] and not any(g.attr('function')for g in pins),f'{co}/{n}: port diode Liberty pin set')
        antenna.append(dict(name=n,pin=pin,area_um2=float(libs['tt'].cells[n].attr('area')),lef_size_um=m['size_um'],lef_diff_area_um2=signal[pin]['diff_area_um2'],lef_gate_area_um2=signal[pin]['gate_area_um2']or 0.0,policy_class='physical_only'))
    sources=[dict(role='liberty_'+co,**rec(f['std_'+co]))for co in ['tt','ss']]+[dict(role='cell_lef',**rec(f['cell_lef'])),dict(role='original_cell_lef',**rec(f['std_lef']))]
    policy=dict(allowed=names,corners=['tt','ss'],purpose='identity buffer/delay flow policy from original PDK')
    write(out/'cell-policy.json',policy);representative=dict(input_slew_ns=.2,load_pf=.01);buffers=[];members=[]
    for name in names:
        per={}
        for co,lib in libs.items():
            d,error=delay.buffer_data(lib,name,representative);require(not error,f'{co}/{name}: {error}');per[co]=d
        ref=per['tt'];buffers.append(dict(name=name,input_pin=ref['input_pin'],output_pin=ref['output_pin'],function=ref['function'],function_check='identity in original TT/SS Liberty',timing_sense='positive_unate',area_um2=ref['area_um2'],lef_size_um=macros[name]['size_um'],policy_class='cts_hold',input_cap_pf={co:d['input_cap_pf']for co,d in per.items()},delay_ns={co:d['delay_ns']for co,d in per.items()},output_slew_ns={co:d['output_slew_ns']for co,d in per.items()}))
        if name not in selection['delay_cells']:
            cell=libs['tt'].cells[name];cap=next(p for p in cell.sub('pin')if p.attr('direction')=='output').attr('max_capacitance')
            members.append(dict(name=name,width_dbu=widths[name],area_um2=float(cell.attr('area')),max_capacitance_pf=float(cap)if cap else None,drive={co:sizing.drive_data(lib.cells[name],lib)for co,lib in libs.items()}))
    write(out/'delay.json',dict(schema='rustroad.delay-cells.v1',pdk='ihp-sg13g2',library='ez130_8t',generator=str(Path(__file__).resolve()),sources=sources,cell_policy={k:v for k,v in rec(out/'cell-policy.json').items()if k!='bytes'},corners=[dict(name=co,liberty=f['std_'+co].stem,rc='nom')for co in ['tt','ss']],representative={**representative,'reason':'diagnostic estimate; re-time every ECO at both corners'},selection=policy,buffers=buffers,antenna_cells=antenna,excluded=[]))
    signature,error=sizing.describe(libs['tt'].cells[selection['reset']['leaf']]);require(not error,'sizing signature')
    members.sort(key=lambda d:(d['max_capacitance_pf']or 0,d['area_um2'],d['name']))
    write(out/'sizing.json',dict(schema='rustroad.sizing-profile.v1',pdk='ihp-sg13g2',library='ez130_8t',generator=str(Path(__file__).resolve()),sources=sources,dbu_per_um=1000,rule='original TT/SS identity functions and original LEF geometry',drive_model=dict(input_slew_ns=sizing.DRIVE_INPUT_SLEW_NS,loads_pf=list(sizing.DRIVE_LOADS_PF),corners=['tt','ss']),families=[dict(id='BUFX',order='max_capacitance_pf then area_um2',**signature,cells=members)],excluded=[]))
    buses=read(f['interfaces'],1<<20)['cell_buses'];require(buses==read(f['floorplan'],1<<20)['pin_buses'],'original interface and floorplan bus ranges differ');write(out/'cell-buses.json',buses)
    io,cts,clock=io_and_clock(f['sdc'],selection);write(out/'io.json',io);(out/'cts.sdc').write_text(cts)
    write(out/'receipt.json',dict(status='profiles_prepared',inputs=c['files'],clock_projection=clock,full_sdc=rec(f['sdc']),unverified=['all physical/timing signoff']))


def bundle(c,args,out):
    f=paths(c);root=c['root'];tag=args.timing_tag;require(re.fullmatch('[a-zA-Z0-9_-]+',tag),'unsafe timing tag')
    source=dynamic(c,args.source);timed=dynamic(c,args.timed or args.source);graph=dynamic(c,args.graph);profile=root/'frontend/profiles'
    inputs=dict(schema='rusteda.native-eco-inputs.v1',netlist=rec(source),netlist_json={**rec(graph),'source_sha256':rec(source)['sha256']},sdc=rec(f['sdc']),io=rec(profile/'io.json'),profiles=dict(delay=rec(profile/'delay.json'),lef=rec(f['cell_lef']),sizing=rec(profile/'sizing.json'),cell_buses=rec(profile/'cell-buses.json')),tools=dict(rustroad=c['tools']['rustroad'],xxhsum=c['tools']['xxhsum'],liberty_reader=c['files']['liberty_reader']),corners={})
    if timed!=source:inputs['timed_netlist']=rec(timed)
    estimated=args.operation in ('electrical','hold')
    if estimated:
        inputs['placement']=dict(seed=rec(dynamic(c,root/f'logs/fe_{tag}_seed.stdout')),**{'def':rec(dynamic(c,root/f'logs/fe_{tag}_def.stdout'))})
        if args.operation=='hold':inputs['placement']['weights']=rec(dynamic(c,root/'frontend/eco-electrical/weights.json'))
        inputs['rc_policy']=rec(f['rc_policy']);inputs['tools']['estimator']=c['files']['estimator']
        estimate=read(dynamic(c,root/f'frontend/estimate-{tag}/result.json'),1<<20)
        require(estimate['inputs']['def']['sha256']==inputs['placement']['def']['sha256'] and estimate['inputs']['policy']['sha256']==c['files']['rc_policy']['sha256'],'estimate belongs to another placement or policy')
    for co in ['tt','ss']:
        drv=dynamic(c,root/f'logs/fe_{tag}_{co}_drv.stdout')
        inputs['corners'][co]=dict(pin_tables=[{**rec(dynamic(c,root/f'logs/fe_caps_{co}_{kind}.stdout')),'library':rec(f[key+'_'+co])}for kind,key in [('std','std'),('ram','ram')]],drv=rec(drv))
        if estimated:inputs['corners'][co]['spef']=rec(dynamic(c,root/f'frontend/estimate-{tag}/estimate.spef'))
        if args.operation in ('electrical','hold'):
            inputs['corners'][co].update(endpoints=rec(dynamic(c,root/f'logs/fe_{tag}_{co}_endpoints.stdout')),hold_fix=rec(dynamic(c,root/f'logs/fe_{tag}_hold.stdout')))
    write(out/'inputs.json',inputs)
    ecos=module('frontend_ecos',f['ecos']);a=SimpleNamespace(max_work=10000000,max_cells=100000,max_connections=500000,max_rows=200000)
    parsed=ecos.Inputs(out/'inputs.json',a);corners=['tt','ss'];io=ecos.io_projection(parsed,f['sdc']);pins=ecos.pin_views(parsed,corners);g=ecos.Graph(parsed,parsed.document(inputs['netlist_json']),c['selection']['top'],pins,io)
    reports=[parsed.report(co,'drv',timed,f['sdc'])for co in corners]
    initial=root/'frontend/bundle-reset/roots.json'
    if args.operation=='reset':
        def choose(pin):
            choices=[(sum(x['pin']==pin for x in rows),bit)for bit,rows in g.loads.items()];count,bit=max(choices)
            require(count>=1000 and sum(n==count for n,b in choices)==1,'ambiguous large reset/clock root')
            d=g.drivers[bit];driver=d['instance']+'/'+d['pin'];names={r['net']for env in reports for r in env['sections']['drv']['rows']if r['pin']==driver and r['kind']=='max_fanout' and g.bit(r['net'])==bit}
            require(names,'compiled DRV row does not bind root');return sorted(names)[0],d,count
        clock,driver,ck=choose(c['selection']['clock_sink_pin']);reset,rd,rb=choose(c['selection']['reset']['pin'])
        require(driver['cell']in c['selection']['gating_cells'],'largest CK root is not the explicitly selected gate class')
        primary=g.bit(c['selection']['primary_clock']);cell=g.cells[driver['instance']]
        require(any(primary in values and cell['port_directions'][port]=='input'for port,values in cell['connections'].items()),'gated root does not derive directly from the original primary clock')
        keys=['instance','cell','pin']
        roots=dict(clock_root=clock,reset_root=reset,clock_driver={k:driver[k]for k in keys},reset_driver={k:rd[k]for k in keys},clock_sinks=ck,reset_sinks=rb,sdc=rec(f['sdc']),source=rec(source),graph=rec(graph),reports=[rec(Path(inputs['corners'][co]['drv']['path']))for co in corners])
    else:roots=read(dynamic(c,initial),1<<20)
    require(roots['sdc']==rec(f['sdc']),'root policy belongs to another SDC')
    for name,driver in [(roots['clock_root'],roots['clock_driver']),(roots['reset_root'],roots['reset_driver'])]:
        actual=g.drivers[g.bit(name)];require(all(actual[k]==driver[k]for k in ['instance','cell','pin']),'current root driver differs from bound first-run policy')
    write(out/'roots.json',roots);selection=c['selection'];policy={k:selection[k]for k in ['top','primary_clock','buffers','primary_buffer','delay_cells','target_fanout','cap_fraction','hold_margin_ns','setup_margin_ns']}
    if 'port_diode' in selection:policy['port_diode']=selection['port_diode']
    policy.update(schema='rusteda.native-eco-policy.v1',clock_roots=[selection['primary_clock'],roots['clock_root']],reset={**selection['reset'],'net':roots['reset_root']},scope='estimated_rc' if estimated else 'ideal_diagnostic',prefix='native_'+args.operation.replace('-','_'))
    if estimated:policy.update(electrical=selection['electrical'],launch_chain_max=selection['launch_chain_max'])
    write(out/'policy.json',policy)


def routing_capture(routing,root):
    """Materialize the one explicit run-local, bounded failure-capture policy."""
    replay=routing.get('limits',{}).get('replay')
    template='{output}/backend/route-budget-error.json'
    if not isinstance(replay,dict) or replay.get('path')!=template:
        require(not(isinstance(replay,dict) and isinstance(replay.get('path'),str) and '{output}' in replay['path']),'unsupported routing capture template')
        return None
    require(set(replay)=={'path','net','round','max_bytes','phase'},'routing capture template keys differ')
    require(replay['net'] is None and replay['round'] is None and type(replay['max_bytes']) is int and replay['max_bytes']==536870912 and replay['phase']=='post_budget_error','routing capture template policy differs')
    root=root.resolve(strict=True);literal=root/'backend/route-budget-error.json'
    require(not literal.exists() and not literal.is_symlink(),'routing capture output already exists')
    capture=literal.resolve()
    require(capture.is_relative_to(root) and capture!=root,'routing capture escapes this producer run')
    require(not capture.exists() and not capture.is_symlink(),'routing capture output already exists')
    replay['path']=str(capture)
    return dict(template=template,snapshot_path=str(capture),max_bytes=replay['max_bytes'],phase=replay['phase'],scope='failure diagnostic only; no route or signoff pass')


def physical(c,args,out):
    f=paths(c);roots=read(dynamic(c,args.roots),1<<20);data=read(f['physical'],1<<20);data['clock']['root_net']=roots['clock_root']
    require(len(data['filler_cells'])==9 and data['tap']['max_gap']==15000 and data['tap']['body_view']=='gds','matched decap/tap template differs')
    write(out/'physical.json',data);routing=read(f['routing'],1<<20);layers=routing['track_router'];require(layers['min_routing_layer']=='Metal2' and layers['max_routing_layer']=='TopMetal2','matched routing layer policy differs');capture=routing_capture(routing,c['root']);write(out/'routing.json',routing)
    receipt=dict(inputs={k:rec(f[k])for k in ['physical','floorplan','routing']},roots=rec(Path(args.roots)),scope='route policy prepared, route not executed')
    if capture is not None:receipt['routing_capture']={**capture,'materialized_routing':rec(out/'routing.json')}
    write(out/'receipt.json',receipt)


def macro_sizes(lef):
    sizes,name={},None
    for line in lef.read_text().splitlines():
        t=line.split()
        if len(t)>=2 and t[0]=='MACRO':name=t[1]
        elif name and len(t)>=4 and t[0]=='SIZE' and t[2]=='BY':sizes[name]=(round(float(t[1])*1000),round(float(t[3])*1000));name=None
    require(0<len(sizes)<=4096,'cell LEF macro budget');return sizes


def pins(c,args,out):
    """Load-aware I/O plan from this run's pin-planning placement (DBU, die origin 0,0)."""
    f=paths(c);root=c['root'];placed=dynamic(c,args.def_);require(placed.stat().st_size<=128<<20,'placed DEF byte budget')
    data=read(dynamic(c,root/'frontend/physical/physical.json'),1<<20);require('io_plan' not in data,'physical config already plans its pins')
    sizes=macro_sizes(f['cell_lef']);diode=c['selection'].get('port_diode',{}).get('cell');floor=read(f['floorplan'],1<<20)
    die,core=floor['die_area'],floor['core_area'];require(die['lx']==0 and die['ly']==0,'die origin')
    text=placed.read_text();cells={}
    m=re.search(r'\nCOMPONENTS (\d+) ;(.*?)\nEND COMPONENTS',text,re.S);require(m and int(m[1])<=1_000_000,'placed component budget')
    for item in m[2].split(';'):
        t=item.split()
        if len(t)<3 or t[0]!='-':continue
        g=re.search(r'\+ (?:PLACED|FIXED) \( (-?\d+) (-?\d+) \) (\S+)',item);require(g is not None and t[2] in sizes,'unplaced or unknown component: '+t[1])
        w,h=sizes[t[2]];w,h=(h,w)if g[3]in('E','W','FE','FW')else(w,h);cells[t[1]]=(t[2],int(g[1])+w//2,int(g[2])+h//2)
    require(len(cells)==int(m[1]),'placed component count')
    m=re.search(r'\nPINS (\d+) ;(.*?)\nEND PINS',text,re.S);require(m and int(m[1])<=100_000,'placed pin budget')
    names=[t[1]for t in(item.split()for item in m[2].split(';')if '+ USE SIGNAL' in item)if len(t)>=2 and t[0]=='-']
    require(len(names)==len(set(names))>0,'signal pin names');loads={n:[]for n in names}
    m=re.search(r'\nNETS (\d+) ;(.*?)\nEND NETS',text,re.S);require(m and int(m[1])<=2_000_000,'placed net budget')
    for net in m[2].split('\n- ')[1:]:
        ends=re.findall(r'\( (\S+) (\S+) \)',net.split('+ ROUTED')[0].split(';')[0])
        ports=[p for i,p in ends if i=='PIN']
        for p in ports:
            require(p in loads,'net names an unplanned pin: '+p)
            loads[p]+=[cells[i]for i,_ in ends if i!='PIN' and cells[i][0]!=diode]
    mid=((core['lx']+core['ux'])//2,(core['ly']+core['uy'])//2)
    centroid={n:(sum(x for _,x,_ in v)//len(v),sum(y for _,_,y in v)//len(v))if v else mid for n,v in loads.items()}
    dist=lambda p:[p[0]-core['lx'],core['ux']-p[0],p[1]-core['ly'],core['uy']-p[1]]
    # Conservative side capacity: a quarter of the slots at width plus spacing.
    pitch=4*(max(data['io_width'],1)+data['io_spacing']);cap=[(die['uy']-die['ly'])//pitch]*2+[(die['ux']-die['lx'])//pitch]*2
    sides=[[]for _ in range(4)]
    for n in sorted(names,key=lambda n:(-(sorted(dist(centroid[n]))[1]-sorted(dist(centroid[n]))[0]),n)):
        d=dist(centroid[n]);s=next((s for s in sorted(range(4),key=lambda s:(d[s],s))if len(sides[s])<cap[s]),None)
        require(s is not None,'I/O pins exceed the conservative side capacity');sides[s].append(n)
    for s,side in enumerate(sides):side.sort(key=lambda n:(centroid[n][1 if s<2 else 0],n))
    data['io_plan']=dict(west=sides[0],east=sides[1],south=sides[2],north=sides[3],targets={n:centroid[n][1 if s<2 else 0]for s,side in enumerate(sides)for n in side})
    write(out/'physical.json',data)
    plan=[dict(pin=n,side=['west','east','south','north'][s],target_dbu=centroid[n][1 if s<2 else 0],loads=len(loads[n]))for s,side in enumerate(sides)for n in side]
    write(out/'receipt.json',dict(rule='nearest core side of the load centroid, strongest preference first; along-side target is the centroid; port diodes excluded',
          inputs=dict(placed_def=rec(placed),physical=rec(root/'frontend/physical/physical.json'),cell_lef=rec(f['cell_lef']),floorplan=rec(f['floorplan'])),
          side_counts=[len(x)for x in sides],pins=plan,scope='pin plan from the pin-planning placement; the temporary, repair and final rounds import it'))


def validate(c,out):
    root=c['root'];receipts=read(root/'step-receipts.json',2<<20)
    for tag in ['temporary','repair','final']:
        ids=[r['id']for r in receipts if r['kind']=='command' and r['id']==f'fe_{tag}_cts'];require(ids==[f'fe_{tag}_cts'],'exactly one CTS per fresh workspace required')
        ws=root/f'frontend/road-{tag}';require((ws/'HEAD').read_text().strip()=='rev3','unexpected final physical revision')
        report=read(root/f'logs/fe_{tag}_cts.stdout',2<<20);require(report['revision']=='rev3' and report['clock_buffers']>0 and report['clock_topology_status']=='unrouted_topology_checked','native CTS topology not verified')
    pin_plan=read(root/'frontend/physical-pins/receipt.json',1<<20);require(pin_plan['inputs']['placed_def']==rec(root/'logs/fe_pins_def.stdout'),'pin plan belongs to another placement')
    seed=read(dynamic(c,root/'logs/fe_temporary_seed.stdout'),64<<20);require(seed['schema']=='rustroad.placement-seed.v1' and seed['source']['revision']=='rev3' and seed['source']['imported_netlist_sha256']==rec(root/'frontend/eco-primary-clock/netlist.v')['sha256'],'seed not from this-run temporary imported netlist')
    plans={op:read(root/f'frontend/eco-{op}/receipt.json',16<<20)for op in ['reset','fanout','primary-clock','electrical','hold']}
    require(all(p['scope']==('estimated_rc' if op in ('electrical','hold') else 'ideal_diagnostic')for op,p in plans.items()),'unexpected frontend timing scope')
    estimate=read(root/'frontend/estimate-final/result.json',1<<20);require(estimate['inputs']['def']['sha256']==rec(root/'logs/fe_final_def.stdout')['sha256'],'final estimate belongs to another placement')
    final={}
    for co in ['tt','ss']:
        env=read(root/f'logs/fe_final_{co}_drv.stdout',16<<20);require(env['scope']['wire_model']=='spef_rc' and env['scope']['clock_model']=='propagated' and env['scope']['cppr']=='off','final scope differs')
        m=env['metrics'];final[co]={k:m[k]for k in ['max_transition_violating','max_capacitance_violating','max_fanout_violating','worst_setup_slack_ns','worst_hold_slack_ns','hold_violating_endpoints']}
        require(all(m[k]==0 for k in ['max_transition_violating','max_capacitance_violating','max_fanout_violating']),'final estimated-RC DRV not closed: '+co)
    result=dict(schema='rusteda.native-frontend-result.v1',status='frontend_completed',scope='original RTL, dynamic native ECO, temporary CTS, two placement-estimated RC repair rounds, fresh final one CTS; placement RC estimate, not extraction',tools=c['tools'],original_inputs=c['files'],final_workspace=str(root/'frontend/road-final'),final_netlist=rec(root/'logs/fe_final_export.stdout'),prects_netlist=rec(root/'frontend/eco-hold/netlist.v'),same_run_seed=rec(root/'logs/fe_temporary_seed.stdout'),pin_plan=dict(physical=rec(root/'frontend/physical-pins/physical.json'),receipt=rec(root/'frontend/physical-pins/receipt.json'),side_counts=pin_plan['side_counts']),seed_source=seed['source'],final_estimate=dict(result=rec(root/'frontend/estimate-final/result.json'),timing=final),plans={op:dict(operations=p['operations'],receipt=rec(root/f'frontend/eco-{op}/receipt.json'))for op,p in plans.items()},unverified=['route','extracted RC','GDS','DRC','antenna','LVS','extracted_timing','CEC','benchmark'])
    write(out/'result.json',result)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['profiles','bundle','physical','pins','validate']);p.add_argument('--context',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--base-rev',required=True);p.add_argument('--operation',choices=['reset','fanout','primary-clock','electrical','hold']);p.add_argument('--timing-tag');p.add_argument('--source');p.add_argument('--timed');p.add_argument('--graph');p.add_argument('--roots');p.add_argument('--def',dest='def_');a=p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS,(2<<30,2<<30));signal.alarm(180);c=context(a.context)
    with fresh(a.workspace,a.base_rev):
        if a.command=='profiles':profiles(c,a.workspace)
        elif a.command=='bundle':bundle(c,a,a.workspace)
        elif a.command=='physical':physical(c,a,a.workspace)
        elif a.command=='pins':pins(c,a,a.workspace)
        else:validate(c,a.workspace)
        (a.workspace/'HEAD').write_text('rev1\n')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError,MemoryError) as error:
        print(json.dumps(dict(status='failed',error=str(error))),file=sys.stderr);raise SystemExit(2)

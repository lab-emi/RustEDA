#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Private bounded primary-clock-port ANTENNA8 plan, public OpenROAD API only.

This writes a Tcl plan; it is not antenna/signoff. Controller <64MiB; external
estimate1.3GiB, <=100k instances/1M visits/4096 clock sinks/one new diode.
Use a2GiB/512MiB/180s/two-physical-CPU scope for execution. The original
reference_ecos source supplies its unchanged quote/identity primitives; locking,
padding, route-layer and PG guards follow that qualified first-party policy.
"""
import argparse,fcntl,hashlib,importlib.util,json,resource,re,sys
from pathlib import Path

FIELDS={'odb','sdc','cppr','tt_std','tt_ram','ss_std','ss_ram'}
def require(value,message):
 if not value:raise ValueError(message)
def identity(path):
 path=Path(path).resolve(strict=True);stat=path.stat();require(path.is_file()and stat.st_size<=256<<20,'file budget')
 with path.open('rb')as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
 require((stat.st_size,stat.st_mtime_ns)==(path.stat().st_size,path.stat().st_mtime_ns),'file changed')
 return dict(path=str(path),sha256=sha,bytes=stat.st_size)
def read(path):
 require(path.is_file()and path.stat().st_size<=2<<20,'JSON budget')
 return json.loads(path.read_bytes())
def build(c,out):
 require(set(c)=={'schema','files','policy','shared_policy'}and c['schema']=='rusteda.primary-clock-antenna-inputs.v1','schema')
 require(set(c['files'])==FIELDS,'explicit ODB/4Lib/originalSDC set required')
 for r in [*c['files'].values(),c['shared_policy']]:require(identity(r['path'])==r,'stale input')
 require(Path(c['shared_policy']['path']).name=='reference_ecos.py','shared first-party policy required')
 sys.dont_write_bytecode=True
 spec=importlib.util.spec_from_file_location('qualified_reference_ecos',c['shared_policy']['path'])
 shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)
 q=shared.quote
 for record in c['files'].values():require(shared.identity(Path(record['path']))==record,'shared policy input identity differs')
 p=c['policy'];require(set(p)=={'clock_port','prefix','diode_master','signal_pin','max_instances','max_visits','max_sinks','max_new_diodes','max_anchor_distance_dbu'},'policy fields')
 require(isinstance(p['clock_port'],str)and re.fullmatch('[A-Za-z0-9_-]{1,128}',p['clock_port']),'safe port')
 require(isinstance(p['prefix'],str)and re.fullmatch('[A-Za-z0-9_]{1,48}',p['prefix']),'safe namespace')
 require(p['diode_master']=='ANTENNA8'and p['signal_pin']=='A','original diode interface required')
 bounds={'max_instances':100000,'max_visits':1000000,'max_sinks':4096,'max_new_diodes':1,'max_anchor_distance_dbu':100000}
 require(all(type(p[k])is int and p[k]==v for k,v in bounds.items()),'fixed qualified work/placement budget')
 lines=['define_corners tt ss','set_thread_count 2']
 for corner in ['tt','ss']:
  for kind in ['std','ram']:lines.append('read_liberty -corner '+corner+' '+q(c['files'][corner+'_'+kind]['path']))
 lines+=['read_db '+q(c['files']['odb']['path']),'read_sdc '+q(c['files']['sdc']['path']),'source '+q(c['files']['cppr']['path']),
  'set out '+q(out),'set clock_port '+q(p['clock_port']),'set diode_name '+q(p['prefix']+'_diode'),
  'set max_instances 100000','set max_visits 1000000','set max_sinks 4096','set max_anchor_distance 100000',
  'source '+q(Path(__file__).parent/'operation.tcl')]
 script=out/'clock-protect.tcl'
 script.write_text('if {[catch {\n'+'\n'.join(lines)+'\n} message options]} {\n puts stderr $message\n puts stderr [dict get $options -errorinfo]\n exit 2\n}\nexit 0\n')
 plan=dict(schema='rusteda.primary-clock-antenna-plan.v1',status='requires_execution_and_complete_signoff',
  files=c['files'],shared_policy=c['shared_policy'],policy=p,script=identity(script),operation=identity(Path(__file__).parent/'operation.tcl'),
  expected_outputs=dict(outcome='outcome.tsv',odb='clock-protected.odb',netlist='clock-protected.v',powered_netlist='clock-protected.pnl.v',guides='guides-final.guide'),
  scope='at most one same-net clock-port diode; no logic/clock-tree resize; existing diode is logical no-op only',
  antenna='unverified',unverified=['detailed routing','actual31rule antenna','RC','originalTTSS timing','DRC','deviceLVS','wholeflow','benchmark'])
 (out/'plan.json').write_text(json.dumps(plan,indent=2)+'\n');return plan
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',type=Path,required=True)
 parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--base-rev',required=True);a=parser.parse_args()
 resource.setrlimit(resource.RLIMIT_AS,(512<<20,)*2)
 a.workspace.mkdir(parents=True,exist_ok=True)
 with(a.workspace/'workspace.lock').open('a')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);require(a.base_rev=='empty'and not any(p.name!='workspace.lock'for p in a.workspace.iterdir()),'REVISION_CONFLICT')
  value=build(read(a.inputs),a.workspace.resolve());(a.workspace/'HEAD').write_text('rev1\n')
  print(json.dumps(dict(status=value['status'],script=value['script'],antenna='unverified')))
if __name__=='__main__':
 try:main()
 except(OSError,ValueError,KeyError,TypeError)as error:
  print(json.dumps(dict(status='failed',error=str(error))),file=sys.stderr);raise SystemExit(2)

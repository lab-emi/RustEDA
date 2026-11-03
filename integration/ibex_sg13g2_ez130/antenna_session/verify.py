#!/usr/bin/env python3
"""Independent bounded lineage-v3 verifier; never executes an EDA command.

No imports from the controller, legacy helper or its field reader. Peak estimate
<1.5GiB:64MiB model x12 +16MiB lineage x8 +16MiB summary x8 +2MiB inputs x8
+128MiB names/IDs +64MiB decoder and bounded file/operation maps.
Only one connectivity model is decoded at a time; raw check rows are streamed.
Caller:2GiB/512MiB/180s/2CPU. Metadata fixtures cannot qualify an EDA engine.
Lineage v4 (native summary v2): only the initial and the committed final
revision are materialized; attempt children are logical revisions, chained by
name and bound to distinct physical-source fingerprints by their check reports.
Check reports may hold only their violated rows (check_rows='violated'); the
coverage row counts, worst-net summary and verdict counts are then checked
against the complete evaluated inventory they report.
"""
from pathlib import Path
import argparse,collections,fcntl,hashlib,json,math,re,resource,sys,time

POLICY=dict(group_names=128,max_groups=1024,max_attempts=64,max_total_jumpers=4096,
 max_commands=4096,max_report_bytes=8<<30,max_selected_report_bytes=128<<20,
 max_metadata_bytes=4<<20,max_cumulative_work=200000000000)
LIMITS=dict(max_shapes_per_net=200000,max_total_shapes=2000000,max_pair_checks=200000000,
 max_union_cells=4000000,max_rows=100000,max_work=200000000,report_memory_bytes=128<<20)
HEADER={'schema','status','workspace_revision','scope','safety_margin','nets','violations',
 'lef_violations','sources','unsupported','design','selection','stats'}
REV=re.compile(r'[A-Za-z0-9_-]{1,64}')

def need(ok,message):
 if not ok:raise ValueError(message)
def integer(n):return type(n)is int and n>=0
def digest(n):return isinstance(n,str)and re.fullmatch('[a-f0-9]{64}',n)is not None
def pairs(items):
 value={}
 for key,item in items:
  need(key not in value,'DUPLICATE_JSON_KEY');value[key]=item
 return value
def nonfinite(_):raise ValueError('NONFINITE_JSON_NUMBER')
DEC=json.JSONDecoder(object_pairs_hook=pairs,parse_constant=nonfinite)
def fingerprint(value):
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def identity(path,cap=4<<30):
 path=Path(path).resolve(strict=True);before=path.stat()
 need(path.is_file()and before.st_size<=cap,'ARTIFACT_BYTE_BUDGET')
 with path.open('rb')as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
 after=path.stat();need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
  (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'ARTIFACT_CHANGED')
 return dict(path=str(path),sha256=sha,bytes=after.st_size)
def read(path,cap):
 path=Path(path);need(path.stat().st_size<=cap,'JSON_BYTE_BUDGET')
 return DEC.decode(path.read_text(encoding='utf-8'))
def names(value,cap=100000):
 need(isinstance(value,list)and len(value)<=cap,'NAME_COUNT_BUDGET')
 need(all(isinstance(n,str)and 0<len(n.encode())<=4096 for n in value),'NAME_BYTE_BUDGET')
 need(sum(len(n.encode())for n in value)<=16<<20,'TOTAL_NAME_BYTE_BUDGET')
 need(value==sorted(set(value)),'NAMES_NOT_CANONICAL');return value

class Reader:
 """Strict incremental top-level reader, streaming the checks array itemwise."""
 def __init__(self,path,raw_cap=128<<20,field_cap=4<<20,row_cap=1<<20,max_rows=100000,metadata_cap=None):
  self.path=Path(path);need(self.path.stat().st_size<=raw_cap,'RAW_REPORT_BYTE_BUDGET')
  self.raw_cap=raw_cap;self.field_cap=field_cap;self.row_cap=row_cap;self.max_rows=max_rows
  self.metadata_cap=metadata_cap or field_cap
  self.stream=None;self.buffer='';self.offset=0;self.eof=False;self.total=0
 def more(self):
  if self.eof:return False
  self.buffer=self.buffer[self.offset:];self.offset=0
  chunk=self.stream.read(65536)
  if not chunk:self.eof=True;return False
  self.total+=len(chunk.encode());need(self.total<=self.raw_cap,'RAW_REPORT_BYTE_BUDGET')
  self.buffer+=chunk;return True
 def space(self):
  while True:
   while self.offset<len(self.buffer)and self.buffer[self.offset]in' \r\n\t':self.offset+=1
   if self.offset<len(self.buffer)or not self.more():return
 def char(self):
  self.space();need(self.offset<len(self.buffer),'TRUNCATED_JSON')
  ch=self.buffer[self.offset];self.offset+=1;return ch
 def peek(self):
  self.space();return self.buffer[self.offset]if self.offset<len(self.buffer)else None
 def value(self,cap):
  self.space()
  while True:
   need(len(self.buffer[self.offset:].encode())<=cap+65536,'JSON_VALUE_BYTE_BUDGET')
   try:value,end=DEC.raw_decode(self.buffer,self.offset)
   except json.JSONDecodeError:
    need(self.more(),'MALFORMED_OR_TRUNCATED_JSON');continue
   except RecursionError as error:raise ValueError('JSON_DEPTH_BUDGET')from error
   # A number split at the chunk boundary must not be accepted as its prefix.
   if end==len(self.buffer)and not self.eof:
    self.more();continue
   need(len(self.buffer[self.offset:end].encode())<=cap,'JSON_VALUE_BYTE_BUDGET')
   self.offset=end;return value
 def scan(self,on_check,required=None):
  with self.path.open(encoding='utf-8',errors='strict')as stream:
   self.stream=stream;need(self.char()=='{','REPORT_OBJECT_REQUIRED')
   metadata={};keys=set();first=True;count=0;selected_bytes=0
   while self.peek()!='}':
    if not first:need(self.char()==',','TOP_SEPARATOR')
    first=False;key=self.value(8192)
    need(isinstance(key,str)and key not in keys and len(keys)<128,'TOP_KEY_BUDGET_OR_DUPLICATE')
    keys.add(key);need(self.char()==':','KEY_SEPARATOR')
    if key=='checks':
     need(self.char()=='[','CHECKS_ARRAY_REQUIRED');item_first=True
     while self.peek()!=']':
      if not item_first:need(self.char()==',','CHECK_SEPARATOR')
      item_first=False;count+=1;need(count<=self.max_rows,'CHECK_ROW_BUDGET')
      on_check(self.value(self.row_cap))
     need(self.char()==']','CHECK_ARRAY_END')
    else:
     value=self.value(self.field_cap);selected_bytes+=len(json.dumps(value,ensure_ascii=False).encode())
     need(selected_bytes<=self.metadata_cap,'TOTAL_METADATA_BYTE_BUDGET');metadata[key]=value
   need(self.char()=='}'and self.peek()is None,'TRAILING_JSON_DATA')
   need((HEADER|{'checks'}if required is None else required)<=keys,'REPORT_FIELDS_MISSING')
   return metadata,count

class Verifier:
 def __init__(self,cfg):
  need(set(cfg)=={'schema','lineage','tool','source_manifest','design_workspace','evidence_kind','controller_inputs'},'INPUT_SCHEMA')
  need(cfg['schema']=='rusteda.antenna-v3-independent-verifier-inputs.v1','INPUT_SCHEMA')
  need(cfg['evidence_kind']in['protocol_fixture','actual_native_artifacts'],'EVIDENCE_SCOPE')
  self.cfg=cfg;self.start=time.monotonic();self.records={};self.report_summaries={};self.repair_costs={}
  self.group_reports=0;self.reported_work=0;self.validation_upper_bound=0;self.work_budget_charge=0
  self.workspace=Path(cfg['design_workspace']).resolve(strict=True);self.operation_map={}
  self.logical=False;self.check_rows='all'
 def check_time(self):need(time.monotonic()-self.start<=170,'VERIFIER_DEADLINE')
 def file(self,record,cap=4<<30):
  self.check_time();need(isinstance(record,dict)and set(record)=={'path','sha256','bytes'},'FILE_ID_SCHEMA')
  need(Path(record['path']).is_absolute()and digest(record['sha256'])and integer(record['bytes']),'FILE_ID_SCHEMA')
  need(record['bytes']<=cap,'FILE_ID_BYTE_BUDGET');path=record['path']
  if path in self.records:need(self.records[path]==record,'CONFLICTING_FILE_ID')
  else:
   # <=4 records/command plus <=200 source/revision records;4096 commands.
   need(len(self.records)<20000,'FILE_COUNT_BUDGET');need(identity(path,cap)==record,'STALE_FILE_ID');self.records[path]=record
  return Path(path)
 def data(self,record,cap):return read(self.file(record,cap),cap)
 def revision(self,row,expected,parent=None):
  need(REV.fullmatch(expected)is not None and row['revision']==expected,'REVISION_IDENTITY')
  path=self.file(row['design_file'],512<<20)
  need(path==self.workspace/'revisions'/expected/'design.json','FOREIGN_REVISION_PATH')
  # Native format header precedes immutable IR; no graph is decoded.
  with path.open()as stream:text=stream.read(65536)
  dec=DEC;i=0;values={}
  def space(pos):
   while pos<len(text)and text[pos]in' \t\r\n':pos+=1
   return pos
  i=space(i);need(i<len(text)and text[i]=='{','REVISION_OBJECT');i+=1
  for index in range(128):
   i=space(i)
   if index:need(text[i]==',','REVISION_HEADER_SEPARATOR');i=space(i+1)
   key,i=dec.raw_decode(text,i);need(key not in values,'REVISION_DUPLICATE_KEY');i=space(i)
   need(text[i]==':','REVISION_KEY_SEPARATOR');i=space(i+1);value,i=dec.raw_decode(text,i);values[key]=value
   if{'format_version','revision','parent_revision'}<=values.keys():break
  need(type(values['format_version'])is int and values['format_version']in[4,5,6],'REVISION_FORMAT')
  need(values['revision']==expected and values['parent_revision']==row['parent_revision'],'REVISION_HEADER_MISMATCH')
  if parent is not None:need(row['parent_revision']==parent,'BROKEN_PARENT_CHAIN')
 def inventory(self,record,revision,scope):
  d=self.data(record,64<<20)
  need(d['schema']=='rustroad.antenna-inventory.v1'and d['status']=='inventory_only','INVENTORY_SCHEMA')
  need(d['workspace_revision']==revision and d['routing_scope']==scope and digest(d['source_sha256']),'INVENTORY_BINDING')
  for key in['model_logical_net_count','router_declared_net_count','routed_logical_net_count','excluded_nonlogical_supply_tie_count']:
   need(integer(d[key])and d[key]<=1000000,'INVENTORY_COUNT_BUDGET')
  rows=d['nets'];need(isinstance(rows,list)and len(rows)<=100000,'INVENTORY_ROW_BUDGET')
  ns=names([r['canonical_name']for r in rows]);ids=[]
  for row in rows:
   need(set(row)=={'canonical_name','net_id'}and integer(row['net_id'])and row['net_id']<d['model_logical_net_count'],'INVENTORY_NET_ID');ids.append(row['net_id'])
  need(len(set(ids))==len(ids),'DUPLICATE_NET_ID')
  need(len(ns)==d['routed_logical_net_count']and len(ns)+d['excluded_nonlogical_supply_tie_count']==d['router_declared_net_count'],'INVENTORY_COUNT_RECONCILIATION')
  canonical=[dict(net_id=row['net_id'],canonical_name=row['canonical_name'])for row in rows]
  need(d['inventory_sha256']==fingerprint([revision,scope,d['source_sha256'],canonical]),'INVENTORY_DIGEST')
  need(len(d['sources'])==2 and {r['role']for r in d['sources']}=={'technology_lef','cell_lef'},'INVENTORY_LEF_ROLES')
  for source in d['sources']:
   need(digest(source['sha256'])and Path(source['path']).is_absolute(),'INVENTORY_LEF_BINDING')
   need(identity(source['path'])['sha256']==source['sha256'],'STALE_ORIGINAL_LEF')
  return dict(names=ns,ids=ids,source=d['source_sha256'],sources=d['sources'],counts={k:d[k]for k in['model_logical_net_count','router_declared_net_count','excluded_nonlogical_supply_tie_count']})
 def model(self,record):
  d=self.data(record,64<<20);need(set(d)=={'layers','masters','instance_masters','ports','nets','pins'},'MODEL_SCHEMA')
  ms,im,ns,ps=[d[k]for k in['masters','instance_masters','nets','pins']]
  need(len(ms)<=4096 and len(im)<=100000 and len(ns)<=100000 and len(ps)<=1000000,'MODEL_OBJECT_BUDGET')
  net_names=[r['name']for r in ns];names(sorted(net_names));need(len(net_names)==len(set(net_names)),'MODEL_DUPLICATE_NAMES')
  blocks=set()
  for n,m in enumerate(im):
   need(integer(m)and m<len(ms),'MODEL_MASTER_INDEX')
   if ms[m]['class']=='BLOCK':blocks.add(n)
  touches=set()
  for pin in ps:
   net=pin['net'];owner=pin['owner'];need(net is None or integer(net)and net<len(ns),'MODEL_NET_INDEX')
   if owner['kind']=='instance':
    inst=owner['instance'];need(integer(inst)and inst<len(im),'MODEL_INSTANCE_INDEX')
    if inst in blocks and net is not None:touches.add(net_names[net])
   else:need(owner['kind']=='port','MODEL_OWNER_KIND')
  return set(net_names),touches
 def operation(self,record,verb,selection=None,revision=None):
  path=record['path'];need(path in self.operation_map,'ARTIFACT_HAS_NO_NATIVE_OPERATION')
  operation=self.operation_map[path]
  need(operation['report']==record and operation['operation']==verb,'NATIVE_OPERATION_BINDING')
  need(operation['selection']==selection,'NATIVE_OPERATION_SELECTION_BINDING')
  if revision is not None:need(operation['revision']==revision,'NATIVE_OPERATION_REVISION')
  return operation
 def report(self,record,selection,revision,inv,source=None):
  self.check_time();need(len(self.report_summaries)<=4096,'GROUP_REPORT_COUNT')
  ns=names(self.data(selection,1<<20),128);ns_set=set(ns);self.operation(record,'check-antenna',selection,revision)
  counts=collections.Counter();bad=collections.Counter();lef=collections.Counter()
  def row(r):
   self.check_time();need(isinstance(r,dict)and r.get('net')in ns_set,'CHECK_ROW_NET_SCOPE')
   need(type(r.get('violated'))is bool and type(r.get('exceeds_lef_limit'))is bool,'CHECK_ROW_VERDICT')
   need(r['violated']or not violated_only,'NONVIOLATED_ROW_IN_VIOLATED_ONLY_REPORT')
   need(r['violated']or not r['exceeds_lef_limit'],'LEF_EXCESS_WITHOUT_VIOLATION')
   counts[r['net']]+=1;bad[r['net']]+=r['violated'];lef[r['net']]+=r['exceeds_lef_limit']
   for key in['gate_area_um2','diff_area_um2','metal_um2','denominator_um2','ratio','limit','effective_limit']:
    value=r.get(key);need(type(value)in[int,float]and math.isfinite(value)and value>=0,'CHECK_ROW_NUMERICS')
  # Rows stream before the metadata is read: they are checked against the
  # session's declared row scope, which the report must then repeat.
  violated_only=self.check_rows=='violated'
  raw=Reader(self.file(record,128<<20));d,nrows=raw.scan(row)
  need(d.get('check_rows')==('violated'if violated_only else None),'CHECK_ROW_SCOPE')
  need(d['schema']=='rustroad.antenna-check.v1'and d['workspace_revision']==revision,'REPORT_REVISION')
  need(d['sources']==inv['sources']and d['scope']['routing_scope']==self.route_scope,'REPORT_SOURCE_SCOPE')
  need(d['scope']['extra_shapes']==0 and d['scope']['drc_status']=='unchecked'and d['safety_margin']['fraction']==.1,'REPORT_MODEL_SCOPE')
  s=d['selection'];need(s['schema']=='rustroad.antenna-selected.v1'and s['scope']=='explicit_routed_net_subset','REPORT_SELECTION_SCOPE')
  need(s['canonical_nets']==ns and s['selection_sha256']==fingerprint(ns)and s['limits']==LIMITS and digest(s['source_sha256']),'REPORT_SELECTION_BINDING')
  if source is not None:need(s['source_sha256']==source,'REPORT_PHYSICAL_SOURCE')
  coverage=s['coverage'];need(isinstance(coverage,list)and len(coverage)==len(ns),'REPORT_COVERAGE_COUNT')
  partial=False;rows={}
  for expected,c in zip(ns,coverage,strict=True):
   need(set(c)=={'net','shapes','rows','gate_terminals','observation','model_partial'}and c['net']==expected,'REPORT_COVERAGE_IDENTITY')
   need(type(c['model_partial'])is bool and all(integer(c[k])for k in['shapes','rows','gate_terminals']),'REPORT_COVERAGE_TYPES')
   need(c['shapes']<=LIMITS['max_shapes_per_net']and(c['rows']>=counts[expected]if violated_only else c['rows']==counts[expected]),'REPORT_ROW_COUNT_MISMATCH')
   observation=c['observation'];need(observation in['observed','no_geometry','no_gate_area','no_applicable_rule_observation'],'OBSERVATION_KIND')
   if observation=='observed':need(c['rows']>0,'OBSERVED_WITH_NO_ROWS')
   else:need(c['rows']==0,'UNOBSERVED_WITH_ROWS')
   if observation=='no_geometry':need(c['shapes']==0,'NO_GEOMETRY_HAS_SHAPES')
   if observation=='no_gate_area':need(c['gate_terminals']==0,'NO_GATE_HAS_TERMINALS')
   partial|=c['model_partial']or observation!='observed';rows[expected]=c
  evaluated=sum(c['rows']for c in coverage)
  need(sum(counts.values())==nrows<=evaluated<=LIMITS['max_rows'],'REPORT_TOTAL_ROWS')
  need(integer(d['violations'])and integer(d['lef_violations'])and d['violations']==sum(bad.values())and d['lef_violations']==sum(lef.values()),'REPORT_VERDICT_COUNT_MISMATCH')
  need(isinstance(d['unsupported'],list)and len(d['unsupported'])<=100000,'UNSUPPORTED_BUDGET');partial|=bool(d['unsupported'])
  worst=d['nets'];need(isinstance(worst,list)and len(worst)<=len(ns),'WORST_NET_BUDGET');seen=set()
  for r in worst:
   name=r['net'];need(name in rows and name not in seen and type(r['violated'])is bool,'WORST_NET_IDENTITY')
   seen.add(name);need(r['violated']==bool(bad[name]),'WORST_NET_HIDES_ROW_VIOLATION')
  need(set(counts)<=seen and{n for n,c in rows.items()if c['rows']}==seen,'WORST_NET_OMITS_OBSERVED_NET')
  violated={n for n in ns if bad[n]}
  partial|=d['status']=='subset_partial'
  expected_status='subset_violated'if violated else'subset_partial'if partial else'subset_clean'
  need(d['status']==expected_status,'REPORT_STATUS')
  stats=d['stats'];work=stats['work'];need(integer(work)and work<=LIMITS['max_work'],'REPORT_WORK')
  if'partial_nets_checked'in stats:need(stats['partial_nets_checked']==evaluated,'STATS_CHECK_ROW_COUNT')
  memory=stats['report_memory'];need(all(integer(memory[k])for k in['limit_bytes','live_bytes','peak_live_bytes','cumulative_reserved_bytes','cumulative_row_bytes']),'REPORT_MEMORY_TYPE')
  need(memory['limit_bytes']==128<<20 and memory['live_bytes']<=memory['peak_live_bytes']<=memory['limit_bytes']and memory['peak_live_bytes']<=memory['cumulative_reserved_bytes'],'REPORT_MEMORY_LEDGER')
  compact=dict(revision=revision,source=s['source_sha256'],work=work,selection=selection,
   coverage_sha256=fingerprint(coverage),row_count=nrows)
  if record['path']in self.report_summaries:need(self.report_summaries[record['path']]==compact,'REPORT_REPARSE_DIFFERS')
  else:self.report_summaries[record['path']]=compact
  return dict(names=ns,coverage=rows,partial=partial,violating=violated,source=s['source_sha256'],header={k:d[k]for k in HEADER})
 def group_record(self,record,revision,inv,source=None):
  summary=self.report(record['report'],record['selection'],revision,inv,source)
  need(self.data(record['header'],4<<20)==summary['header'],'HEADER_DIFFERS_FROM_RAW_REPORT')
  return summary
 def run(self):
  cfg=self.cfg;lineage=self.data(cfg['lineage'],16<<20);self.route_scope=lineage['route_scope']
  need(self.route_scope=='all_signal_nets','FULL_ROUTED_SCOPE_REQUIRED')
  need(lineage['schema']in('rusteda.native-antenna-lineage.v3','rusteda.native-antenna-lineage.v4')and lineage['scope']=='supported_standard_cell_subset','LINEAGE_SCHEMA')
  self.logical=lineage['schema'].endswith('.v4')
  need(lineage['workspace']==str(self.workspace)and lineage['tool']==cfg['tool']and lineage['policy']==POLICY,'LINEAGE_IDENTITY_POLICY')
  self.file(cfg['tool']);source_manifest=self.data(cfg['source_manifest'],4<<20)
  need(isinstance(source_manifest['sources'],list)and len(source_manifest['sources'])<=128,'SOURCE_COUNT_BUDGET')
  for row in source_manifest['sources']:self.file({k:row[k]for k in['path','sha256','bytes']},4<<20)
  controller_id=cfg['controller_inputs']
  controller=self.data(controller_id,2<<20)
  need(set(controller)=={'schema','design_workspace','initial_revision','tool','limits','grouping'}and controller['schema']=='rusteda.native-antenna-inputs.v2','CONTROLLER_INPUT_SCHEMA')
  need(controller['design_workspace']==str(self.workspace)and controller['tool']==cfg['tool']and controller['initial_revision']==lineage['initial_revision']and controller['grouping']==POLICY,'CONTROLLER_INPUT_BINDING')
  limits=controller['limits']
  need(set(limits)=={'threads','max_rounds','max_selected_nets','max_work','max_expansions','deadline_seconds'},'CONTROLLER_LIMIT_SCHEMA')
  need(all(integer(v)for v in limits.values())and limits['threads']in[2,4,16]and 1<=limits['max_rounds']<=3 and limits['max_selected_nets']==100000 and limits['max_work']==200000000 and limits['max_expansions']==8000000 and limits['deadline_seconds']==1800,'CONTROLLER_LIMIT_BINDING')
  if 'controller_inputs'in lineage:need(lineage['controller_inputs']==controller_id,'LINEAGE_CONTROLLER_INPUT_BINDING')
  need(lineage['execution_mode']=='single_native_process'and type(lineage['actual_native_processes'])is int and lineage['actual_native_processes']==1,'NATIVE_EXECUTION_MODE')
  commands=lineage['commands'];need(isinstance(commands,list)and len(commands)==1,'COMMAND_COUNT_EXACTLY_ONE')
  command=commands[0]
  need(set(command)=={'command','exit_code','stdout','stderr','wall_seconds','output_bytes'},'NATIVE_PROCESS_RECEIPT_SCHEMA')
  need(type(command['exit_code'])is int and command['exit_code']==0,'FAILED_COMMAND_IN_SUCCESS_LINEAGE')
  out=command['stdout'];err=command['stderr'];self.file(out,16<<20);self.file(err,16<<20)
  count=out['bytes']+err['bytes'];need(integer(command['output_bytes'])and command['output_bytes']==count,'COMMAND_BYTE_COUNT')
  need(integer(lineage['process_report_bytes'])and lineage['process_report_bytes']==count<=POLICY['max_report_bytes'],'PROCESS_REPORT_BYTE_BUDGET')
  need(type(command['wall_seconds'])in[int,float]and math.isfinite(command['wall_seconds'])and 0<=command['wall_seconds']<=1800,'NATIVE_PROCESS_WALL_BUDGET')
  plan_path=self.file(lineage['native_plan'],64<<10);plan=self.data(lineage['native_plan'],64<<10)
  need(set(plan)=={'schema','output','max_rounds','deadline_seconds','policy'},'NATIVE_PLAN_SCHEMA')
  need(plan['schema']=='rustroad.antenna-group-repair.v1'and type(plan['max_rounds'])is int and plan['max_rounds']==limits['max_rounds']and type(plan['deadline_seconds'])is int and plan['deadline_seconds']==limits['deadline_seconds'],'NATIVE_PLAN_DEFAULTS')
  need(plan['policy']=={key:POLICY[key]for key in['group_names','max_groups','max_attempts','max_total_jumpers','max_commands','max_report_bytes','max_cumulative_work']},'NATIVE_PLAN_POLICY')
  native=plan_path.parent/'native-reports'
  need(plan_path.name=='native-plan.json'and plan['output']==str(native),'NATIVE_PLAN_OUTPUT')
  need(Path(out['path'])==plan_path.parent/'native-session.json'and Path(err['path'])==plan_path.parent/'native-session.stderr','NATIVE_PROCESS_CAPTURE_PATHS')
  argv=command['command'];need(isinstance(argv,list)and len(argv)==12 and all(isinstance(x,str)for x in argv),'NATIVE_COMMAND_SCHEMA')
  need(argv[:5]==[cfg['tool']['path'],'--workspace',str(self.workspace),'--threads',str(limits['threads'])]and argv[5:]==['--max-expansions','8000000','repair-antenna-groups','--base-rev',lineage['initial_revision'],'--plan',str(plan_path)],'NATIVE_COMMAND_EXACT_BINDING')
  captured_command=identity(plan_path.parent/'native-session-command.json',64<<10)
  need(self.data(captured_command,64<<10)==argv,'NATIVE_CAPTURED_COMMAND_DIFFERS')
  native_summary_path=self.file(lineage['native_summary'],16<<20)
  need(native_summary_path==native/'summary.json'and out['sha256']==lineage['native_summary']['sha256']and out['bytes']==lineage['native_summary']['bytes'],'NATIVE_STDOUT_SUMMARY_BYTES')
  with Path(out['path']).open('rb')as captured,native_summary_path.open('rb')as stored:
   while True:
    self.check_time();left,right=captured.read(65536),stored.read(65536)
    need(left==right,'NATIVE_STDOUT_SUMMARY_RAW_BYTES')
    if not left:break
  native_summary=self.data(lineage['native_summary'],16<<20)
  need(native_summary['schema']==('rustroad.antenna-group-repair-result.v2'if self.logical else'rustroad.antenna-group-repair-result.v1'),'NATIVE_SUMMARY_SCHEMA')
  shared=['status','initial_revision','final_revision','scope','route_scope','inventory_status','native_analysis_status','observations','excluded_macro_touch','unresolved_supported_nets','requires_full_revalidation','whole_layout_antenna_status','total_jumpers','stop_reason','skipped_futile_groups','reported_work','unreported_validation_work_upper_bound','work_budget_charge','report_bytes']
  if self.logical:
   shared+=['logical_revisions','committed_revision','check_rows']
   need(native_summary['check_rows']in['violated','all']and integer(native_summary['logical_revisions']),'NATIVE_LOGICAL_SUMMARY')
   self.check_rows=native_summary['check_rows']
  need(all(native_summary[k]==lineage[k]for k in shared),'NATIVE_SUMMARY_LINEAGE_ACCOUNTING')
  need(native_summary['whole_layout_antenna_status']=='unchecked'and native_summary['drc_status']=='unchecked'and native_summary['corner']=='not_applicable'and native_summary['wire_model']=='none'and native_summary['unconstrained_endpoints']is None and native_summary['requires_full_revalidation']is True,'NATIVE_SUMMARY_TRUTHFULNESS')
  need(type(native_summary['wall_seconds'])in[int,float]and math.isfinite(native_summary['wall_seconds'])and 0<=native_summary['wall_seconds']<=command['wall_seconds']<=1800,'NATIVE_SUMMARY_DEADLINE')
  need(integer(native_summary['groups'])and native_summary['groups']==len(lineage['final_groups'])and integer(native_summary['skipped_futile_groups']),'NATIVE_SUMMARY_GROUP_COUNT')
  operations=lineage['operations'];need(isinstance(operations,list)and 4<=len(operations)<=POLICY['max_commands']and type(lineage['logical_operations'])is int and lineage['logical_operations']==len(operations),'LOGICAL_OPERATION_COUNT')
  operation_names=set();report_bytes=0
  for operation in operations:
   need(isinstance(operation,dict)and set(operation)=={'kind','name','operation','revision','report','selection','selected_report','base_revision'}and operation['kind']=='native_in_process_operation','NATIVE_OPERATION_SCHEMA')
   name=operation['name'];verb=operation['operation'];revision=operation['revision']
   need(isinstance(name,str)and re.fullmatch('[A-Za-z0-9_-]{1,128}',name)and name not in operation_names,'NATIVE_OPERATION_NAME')
   operation_names.add(name);need(isinstance(revision,str)and REV.fullmatch(revision),'NATIVE_OPERATION_REVISION')
   record=operation['report'];path=self.file(record,128<<20)
   need(path==native/(name+'.json')and record['path']not in self.operation_map,'NATIVE_OPERATION_REPORT_PATH')
   self.operation_map[record['path']]=operation;report_bytes+=record['bytes']
   if verb=='check-antenna':
    need(re.fullmatch(r'(?:round-\d+-group-\d+|repair-\d+-before|final-group-\d+)',name),'NATIVE_CHECK_NAME')
    need(self.file(operation['selection'],1<<20)==native/(name+'-selection.json')and operation['selected_report']is None and operation['base_revision']is None,'NATIVE_CHECK_INPUTS')
   elif verb=='repair-antenna':
    need(re.fullmatch(r'repair-\d+',name)and operation['base_revision']==revision,'NATIVE_REPAIR_NAME_BASE')
    need(self.file(operation['selection'],1<<20)==native/(name+'-selection.json')and self.file(operation['selected_report'],128<<20)==native/(name+'-before.json'),'NATIVE_REPAIR_INPUTS')
   else:
    need((verb,name)in[('antenna-inventory','inventory-initial'),('antenna-inventory','inventory-final'),('export-connectivity','model-initial'),('export-connectivity','model-final')],'UNKNOWN_NATIVE_OPERATION')
    need(operation['selection']is None and operation['selected_report']is None and operation['base_revision']is None,'NATIVE_READONLY_INPUTS')
  need(integer(lineage['report_bytes'])and report_bytes==lineage['report_bytes']<=POLICY['max_report_bytes'],'GLOBAL_REPORT_BYTE_BUDGET')
  need(count+report_bytes<=POLICY['max_report_bytes'],'TOTAL_PROCESS_AND_REPORT_BYTE_BUDGET')
  for key,name in[('initial_inventory','inventory-initial'),('final_inventory','inventory-final'),('initial_model','model-initial'),('final_model','model-final')]:
   need(lineage[key]['path']==str(native/(name+'.json')),'NATIVE_INITIAL_FINAL_ROLE_BINDING')
  initial=lineage['initial_revision'];current=initial;self.revision(lineage['initial'],initial)
  inv=self.inventory(lineage['initial_inventory'],initial,self.route_scope);self.operation(lineage['initial_inventory'],'antenna-inventory',revision=initial)
  model_names,touches=self.model(lineage['initial_model']);self.operation(lineage['initial_model'],'export-connectivity',revision=initial)
  need(set(inv['names'])<=model_names and inv['counts']['model_logical_net_count']==len(model_names),'INVENTORY_MODEL_MEMBERSHIP')
  attempts=lineage['attempts'];need(isinstance(attempts,list)and len(attempts)<=64,'ATTEMPT_COUNT');jumpers=0;mutating=[]
  states={};pending_child=None
  for number,attempt in enumerate(attempts):
   need(integer(attempt['round'])and attempt['round']<limits['max_rounds']and integer(attempt['group'])and attempt['group']<native_summary['groups'],'ATTEMPT_ROUND_GROUP_BUDGET')
   base=attempt['base_revision'];need(base==current,'BROKEN_ATTEMPT_CHAIN')
   plan=self.group_record(attempt['planning'],base,inv)
   if pending_child is not None:
    # The first check of a new logical revision: a different physical state.
    need(pending_child[0]==base and plan['source']!=pending_child[1],'LOGICAL_REVISION_UNCHANGED_SOURCE');pending_child=None
   eligible=sorted(n for n in plan['violating']-touches if not plan['coverage'][n]['model_partial']and plan['coverage'][n]['observation']=='observed')
   before=self.group_record(attempt['before'],base,inv)
   selected=names(self.data(attempt['selected'],1<<20),128)
   need(selected==eligible==before['names']and before['violating']==set(selected),'REPAIR_EXACT_CANONICAL_SELECTION')
   need(plan['source']==before['source'],'FRESH_CHECK_PHYSICAL_SOURCE_DIFFERS')
   need(all(not before['coverage'][n]['model_partial']and before['coverage'][n]['observation']=='observed'for n in selected),'FRESH_REPAIR_ELIGIBILITY_CHANGED')
   repair_fields={'schema','status','base_revision','revision','routing_scope','repair_scope','selected_nets',
    'requires_full_revalidation','drc_status','antenna_status','safety_margin','jumpers','stats','before','after','selected_analysis'}
   repair,_=Reader(self.file(attempt['repair_report'],128<<20),metadata_cap=8<<20).scan(
    lambda _:need(False,'UNEXPECTED_REPAIR_CHECK_ARRAY'),required=repair_fields)
   operation=self.operation(attempt['repair_report'],'repair-antenna',attempt['selected'],base)
   need(operation['selected_report']==attempt['before']['report'],'REPAIR_CONSUMED_FOREIGN_REPORT')
   need(operation['base_revision']==base,'REPAIR_BASE_OPERATION')
   need(repair['schema']=='rustroad.antenna-repair.v1'and repair['base_revision']==base and repair['revision']==attempt['revision'],'REPAIR_REVISION_BINDING')
   need(repair['routing_scope']==self.route_scope and repair['repair_scope']=='selected_supported_nets'and repair['selected_nets']==selected,'REPAIR_SCOPE')
   need(repair['requires_full_revalidation']is True and repair['drc_status']=='unchecked'and repair['antenna_status']=='partial_selected_supported_nets'and repair['safety_margin']==.1,'REPAIR_TRUTHFULNESS')
   js=repair['jumpers'];need(isinstance(js,list)and jumpers+len(js)<=4096,'TOTAL_JUMPER_BUDGET');jumpers+=len(js)
   need(repair['status']in['subset_clean','subset_repaired','subset_partial','subset_unrepaired']
    and repair['selected_analysis']['canonical_nets']==selected and repair['selected_analysis']['limits']==LIMITS,'REPAIR_SELECTED_ANALYSIS')
   costs=[repair['stats']['antenna_work'],repair['stats']['planner_work']]
   need(all(integer(v)for v in costs)and costs[0]<=LIMITS['max_work']and costs[1]<=2*LIMITS['max_work'],'REPAIR_NEGATIVE_OR_EXCESS_WORK')
   accounting=dict(reported_antenna_work=costs[0],reported_planner_work=costs[1],
    unreported_validation_work_upper_bound=LIMITS['max_work'],budget_charge=sum(costs)+LIMITS['max_work'])
   need(attempt['work_accounting']==accounting and all(integer(x)for x in attempt['work_accounting'].values()),'UNREPORTED_VALIDATION_MISLABELED')
   self.repair_costs[attempt['repair_report']['path']]=dict(accounting=accounting,base=base,child=attempt['revision'])
   child=attempt['revision']
   if self.logical and not attempt['child'].get('materialized'):
    row=attempt['child'];need(set(row)=={'revision','parent_revision','materialized'}and row['materialized']is False,'LOGICAL_REVISION_SCHEMA')
    need(REV.fullmatch(child)is not None and row['revision']==child and child!=lineage['final_revision']and child!=initial,'LOGICAL_REVISION_IDENTITY')
    need(not(self.workspace/'revisions'/child).exists(),'LOGICAL_REVISION_MATERIALIZED')
    if child!=base:need(row['parent_revision']==base,'BROKEN_PARENT_CHAIN')
   elif self.logical:
    need(attempt['child']['materialized']is True and child in(initial,lineage['final_revision']),'MATERIALIZED_REVISION_SCOPE')
    self.revision({k:attempt['child'][k]for k in('revision','parent_revision','design_file')},child,initial if child!=initial else None)
    if child!=base:need(attempt['child']['logical_parent_revision']==base,'BROKEN_PARENT_CHAIN')
   else:self.revision(attempt['child'],child,base if child!=base else None)
   if self.logical:
    # A logical revision is one physical state: each check names its source.
    known=states.setdefault(base,plan['source'])
    need(known==plan['source']==before['source'],'LOGICAL_REVISION_SOURCE')
    if child!=base:need(child not in states,'LOGICAL_REVISION_REUSED');pending_child=(child,known)
    else:pending_child=None
   if child==base:need(not js and repair['before']==repair['after'],'NOOP_REPAIR_CHANGED')
   else:need(js,'MUTATING_REPAIR_HAS_NO_JUMPER');mutating.append(attempt)
   current=child
  need(lineage['rounds']==mutating and lineage['total_jumpers']==jumpers,'ROUND_OR_JUMPER_LEDGER')
  need(current==lineage['final_revision'],'FINAL_REVISION_CHAIN')
  final=self.inventory(lineage['final_inventory'],current,self.route_scope);self.operation(lineage['final_inventory'],'antenna-inventory',revision=current)
  if self.logical:
   if pending_child is not None:need(pending_child[0]==current and final['source']!=pending_child[1],'LOGICAL_REVISION_UNCHANGED_SOURCE')
   need(states.get(current,final['source'])==final['source'],'LOGICAL_REVISION_SOURCE')
   committed=lineage['committed_revision'];need(lineage['logical_revisions']==len(mutating)and committed==(current if mutating else None),'COMMITTED_REVISION_LEDGER')
   need(lineage['materialized_revisions']==sorted({initial,current})and lineage['check_rows']==self.check_rows,'MATERIALIZED_REVISION_LEDGER')
   need(lineage['final']['materialized']is True and lineage['final']['parent_revision']==(initial if mutating else lineage['initial']['parent_revision']),'FINAL_REVISION_PARENT')
   need(lineage['final']['logical_parent_revision']==(mutating[-1]['base_revision']if mutating else None),'FINAL_LOGICAL_PARENT')
  need(final['names']==inv['names']and final['ids']==inv['ids']and final['counts']==inv['counts']and final['sources']==inv['sources'],'ROUTED_INVENTORY_CHANGED')
  self.operation(lineage['final_model'],'export-connectivity',revision=current);self.file(lineage['final_model'],64<<20)
  need(lineage['initial_model']['sha256']==lineage['final_model']['sha256'],'LOGICAL_CONNECTIVITY_CHANGED')
  covered=set();violating=set();partial=False;obs=collections.Counter();groups=lineage['final_groups']
  expected=[final['names'][i:i+128]for i in range(0,len(final['names']),128)]
  need(groups and len(groups)==len(expected)<=1024,'FINAL_GROUP_COUNT')
  for index,(group,canonical)in enumerate(zip(groups,expected,strict=True)):
   need(group['group']==index and group['revision']==current,'MIXED_FINAL_GROUP_REVISION')
   summary=self.group_record(group,current,final,final['source'])
   need(summary['names']==canonical and not covered.intersection(canonical),'FINAL_GROUP_OVERLAP_OR_PARTITION')
   covered.update(canonical);violating.update(summary['violating']);partial|=summary['partial']
   for row in summary['coverage'].values():obs[row['observation']]+=1
  need(covered==set(final['names']),'FINAL_EXACT_UNION')
  # BLOCK contacts have no independently proved complete native macro model.
  # Do not permit a raw clean label to turn this standard-cell scope into one.
  if covered&touches:need(partial,'MACRO_TOUCH_WITHOUT_PARTIAL_WITNESS')
  need(lineage['native_analysis_status']==('partial'if partial else'violated'if violating else'clean'),'FALSE_NATIVE_CLEAN_LABEL')
  need(lineage['status']==('unresolved'if violating-touches else'complete')and lineage['inventory_status']=='complete','LINEAGE_COMPLETION_LABEL')
  need(lineage['observations']==dict(obs)and lineage['excluded_macro_touch']==sorted(violating&touches)and lineage['unresolved_supported_nets']==sorted(violating-touches),'FINAL_OBSERVATION_LEDGER')
  need(lineage['whole_layout_antenna_status']=='unchecked'and lineage['requires_full_revalidation']is True,'FALSE_WHOLE_LAYOUT_PASS')
  # Replay every typed in-process operation, including planning groups which
  # scheduled no repair. No argv or subprocess receipt is invented per report.
  command_revision=initial;source_by_revision={initial:inv['source'],current:final['source']}
  for operation in operations:
   path=operation['report'];verb=operation['operation']
   self.operation(path,verb,operation['selection'],command_revision)
   if verb=='check-antenna':
    need(self.work_budget_charge+LIMITS['max_work']<=POLICY['max_cumulative_work'],'CHECK_WORK_ADMISSION')
    selection=operation['selection']
    summary=self.report(path,selection,command_revision,inv,source_by_revision.get(command_revision))
    source_by_revision.setdefault(command_revision,summary['source'])
    header=identity(Path(path['path']).with_name(Path(path['path']).stem+'-header.json'),4<<20)
    need(self.data(header,4<<20)==summary['header'],'UNREFERENCED_CHECK_HEADER_DIFFERS')
    self.reported_work+=self.report_summaries[path['path']]['work']
    self.work_budget_charge+=self.report_summaries[path['path']]['work']
    del summary
   elif verb=='repair-antenna':
    need(path['path']in self.repair_costs,'REPAIR_NOT_IN_ATTEMPT_CHAIN')
    cost=self.repair_costs[path['path']];need(cost['base']==command_revision,'COMMAND_REPAIR_CHAIN')
    need(self.work_budget_charge+4*LIMITS['max_work']<=POLICY['max_cumulative_work'],'REPAIR_4W_ADMISSION')
    a=cost['accounting'];self.reported_work+=a['reported_antenna_work']+a['reported_planner_work']
    self.validation_upper_bound+=a['unreported_validation_work_upper_bound'];self.work_budget_charge+=a['budget_charge'];command_revision=cost['child']
   else:need(verb in['antenna-inventory','export-connectivity'],'UNKNOWN_LINEAGE_COMMAND')
  need(command_revision==current,'OPERATION_REVISION_SCOPE')
  need(isinstance(native_summary['attempts'],list)and len(native_summary['attempts'])==len(attempts),'NATIVE_SUMMARY_ATTEMPT_COUNT')
  for attempt,native_attempt in zip(attempts,native_summary['attempts'],strict=True):
   need(set(native_attempt)=={'round','group','base_revision','revision','selected_nets','jumpers'},'NATIVE_SUMMARY_ATTEMPT_SCHEMA')
   need(all(native_attempt[k]==attempt[k]for k in['round','group','base_revision','revision'])and native_attempt['selected_nets']==self.data(attempt['selected'],1<<20),'NATIVE_SUMMARY_ATTEMPT_BINDING')
   repair,_=Reader(self.file(attempt['repair_report'],128<<20),metadata_cap=8<<20).scan(lambda _:need(False,'UNEXPECTED_REPAIR_CHECK_ARRAY'),required=repair_fields)
   need(type(native_attempt['jumpers'])is int and native_attempt['jumpers']==len(repair['jumpers']),'NATIVE_SUMMARY_ATTEMPT_JUMPERS')
  need(all(integer(lineage[k])for k in['reported_work','unreported_validation_work_upper_bound','work_budget_charge'])
   and lineage['reported_work']==self.reported_work and lineage['unreported_validation_work_upper_bound']==self.validation_upper_bound
   and lineage['work_budget_charge']==self.work_budget_charge<=POLICY['max_cumulative_work'],'WORK_BUDGET_LEDGER')
  self.revision({k:lineage['final'][k]for k in('revision','parent_revision','design_file')}if self.logical else lineage['final'],current)
  snapshot=self.file(lineage['workspace_head'],128);actual_head=self.workspace/'HEAD'
  need(actual_head.stat().st_size<=128,'WORKSPACE_HEAD_BYTE_BUDGET')
  need(snapshot.read_text()==current+'\n'and actual_head.read_text().strip()==current,'STALE_FINAL_HEAD')
  need(type(lineage['wall_seconds'])in[int,float]and 0<=lineage['wall_seconds']<=1800,'CONTROLLER_WALL_BUDGET')
  for record in list(self.records.values()):
   self.check_time();need(identity(record['path'])==record,'ARTIFACT_CHANGED_DURING_VERIFICATION')
  self.check_time()
  return dict(schema='rusteda.antenna-v3-independent-lineage-review.v1',status='metadata_lineage_verified_requires_full_signoff',
   evidence_kind=cfg['evidence_kind'],lineage=cfg['lineage'],source_manifest=cfg['source_manifest'],tool=cfg['tool'],
   workspace=str(self.workspace),initial_revision=initial,final_revision=current,initial=lineage['initial'],final=lineage['final'],
   execution_mode='single_native_process',actual_native_processes=1,logical_operations=len(operations),native_plan=lineage['native_plan'],native_summary=lineage['native_summary'],controller_inputs=controller_id,process_report_bytes=count,report_bytes=report_bytes,
   workspace_head=lineage['workspace_head'],initial_inventory=lineage['initial_inventory'],final_inventory=lineage['final_inventory'],
   policy=POLICY,source_lefs=final['sources'],routed_named_nets=len(covered),final_group_count=len(groups),
   native_analysis_status=lineage['native_analysis_status'],observations=dict(obs),macro_touch_nets=len(covered&touches),
   parsed_group_reports=len(self.report_summaries),checked_file_ids=len(self.records),reported_work=self.reported_work,
   unreported_validation_work_upper_bound=self.validation_upper_bound,work_budget_charge=self.work_budget_charge,
   whole_layout_antenna_status='unchecked',engine_qualification='unverified_by_this_metadata_verifier',
   manufacturing_DRC_device_LVS_TTSS_dual_source_parity='unverified_by_this_metadata_verifier',benchmark='not_run')

def main():
 resource.setrlimit(resource.RLIMIT_AS,(2<<30,2<<30))
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',type=Path,required=True)
 parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--base-rev',choices=['empty'],required=True)
 args=parser.parse_args();out=args.workspace.resolve();out.mkdir(parents=True,exist_ok=True)
 with(out/'workspace.lock').open('a')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);need(not any(p.name!='workspace.lock'for p in out.iterdir()),'REVISION_CONFLICT')
  try:result=Verifier(read(args.inputs,1<<20)).run()
  except(OSError,ValueError,TypeError,KeyError,IndexError,RecursionError)as error:
   (out/'result.json').write_text(json.dumps(dict(status='rejected_or_unverified',error=str(error),whole_layout_antenna_status='unchecked',benchmark='not_run'),indent=2)+'\n')
   print(json.dumps(dict(status='rejected_or_unverified',error=str(error))),file=sys.stderr);return 1
  result['verifier_source']=identity(__file__,1<<20)
  result['input_manifest']=identity(args.inputs,1<<20)
  result['resources']=dict(address_space_limit_bytes=2<<30,peak_rss_kib_self=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
  (out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');(out/'HEAD').write_text('rev1\n')
  print(json.dumps(result));return 0
if __name__=='__main__':raise SystemExit(main())

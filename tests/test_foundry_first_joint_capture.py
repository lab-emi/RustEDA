"""Real stdlib typed-capture authority controls, never an EDA oracle.

Estimate192MiB total, one test worker and <=2 Python children, <=512KiB tiny
fixtures/source. Caller1GiB/512MiB/120s. The actual twoCPU producer uses dummy
semantic helpers and Python as tool; fixed coverage worker bytes are only
hashed, never imported/executed. Full physical joint qualification is absent.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import native_flow as flow, producer, qualify_native as q, tap_capture as capture
from integration.ibex_sg13g2_ez130.foundry_first import protocol as p, verify
from tests import test_foundry_first_producer as actual

DUMMY = '''import argparse,hashlib,json,os,resource,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path);p.add_argument('--base-rev');p.add_argument('--tool');p.add_argument('--gds');p.add_argument('--profile');p.add_argument('--top');p.add_argument('--config');p.add_argument('--tool-sha256');p.add_argument('--tool-bytes');a=p.parse_args()
def fid(path):
 path=Path(path).resolve();raw=path.read_bytes();return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
a.workspace.mkdir();Path('control.json').write_text('{"status":"passed","metadata_only":true}');(a.workspace/'HEAD').write_text('rev1\\n')
if a.gds:
 profile=Path(a.profile);deck=profile.parent/'klayout/tech/drc/rule_decks/sg13g2_maximal.drc'
 result=dict(schema='metadata_only_not_physical',status='passed',complete=False,violations=None,wall_seconds=0.0,gds=fid(a.gds),profile=fid(profile),deck=fid(deck),tool=fid(a.tool),as_limits=list(resource.getrlimit(resource.RLIMIT_AS)))
 (a.workspace/'drc.lyrdb').write_text('<metadata-only/>');(a.workspace/'command.json').write_text('["not-an-EDA-command"]')
 (a.workspace/'summary.json').write_text(json.dumps(result));print(json.dumps({k:result[k] for k in ('schema','status','complete','violations','wall_seconds')}))
else:
 cfg=json.loads(Path(a.config).read_text())
 if os.environ.get('RUSTEDA_CAPTURE_MUTATE_CONFIG')=='1':
  cfg['changed_by_child']=True;Path(a.config).write_text(json.dumps(cfg))
 result=dict(schema='metadata_only_not_physical',status='passed',gds=cfg['gds'],config=fid(a.config),as_limits=list(resource.getrlimit(resource.RLIMIT_AS)))
 (a.workspace/'summary.json').write_text(json.dumps(result));owner=Path(os.environ['RUSTEDA_OWNERSHIP'])/'native.json';native=json.loads(owner.read_text());m=json.loads(Path(native['manifest']['path']).read_text())
 controller=dict(schema='metadata_only_not_physical',status='passed',exit_code=0,report=fid(a.workspace/'summary.json'),config=fid(a.config),worker=m['files']['tap_coverage_worker'],tool=fid(a.tool))
 (a.workspace/'controller.json').write_text(json.dumps(controller));print('metadata-only helper returned')
'''


class ActualTypedCapture(unittest.TestCase):
    def setUp(self):
        self.f = actual.ActualProducerMetadata(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        folder = self.f.root / 'helpers'; folder.mkdir()
        files = dict(producer=p.identity(self.f.source), tap_guard=p.identity(Path(capture.__file__)))
        policy = folder / 'tap-policy.json'
        policy.write_text(json.dumps(q.TAP_POLICY))
        files['tap_policy'] = p.identity(policy)
        for role, name in [('tap_latchup_runner','signoff_latchup.py'),('tap_coverage_runner','tap_coverage_runner.py')]:
            path=folder/name;path.write_text(DUMMY);files[role]=p.identity(path)
        common=folder/'signoff_drc.py';common.write_text('# metadata fixture, never imported\n');files['tap_latchup_common']=p.identity(common)
        source=Path(os.environ.get('RUSTEDA_TEST_TAP_SOURCE_DIR',str(Path(flow.__file__).parent)))/'tap_coverage.py'
        shutil.copyfile(source,folder/'tap_coverage.py');files['tap_coverage_worker']=p.identity(folder/'tap_coverage.py')
        self.assertEqual((files['tap_coverage_worker']['sha256'],files['tap_coverage_worker']['bytes']),capture.WORKER)
        self.files=files;self.tools=dict(self.f.tools,appimage=self.f.tools['python'])
        extra=flow.joint_tap_steps(files,self.tools,dict(gds='backend/final.gds'))
        prefix=[dict(id='be_macro_contract',kind='render',destination='backend/final.gds',content=dict(metadata_only=True)),
            dict(id='dummy_profile',kind='render',destination='backend/postroute/effective-inputs/foundry/provenance.json',content=dict(metadata_only=True)),
            dict(id='dummy_deck',kind='render',destination='backend/postroute/effective-inputs/foundry/klayout/tech/drc/rule_decks/sg13g2_maximal.drc',content=dict(metadata_only=True))]
        self.data=dict(schema='rusteda.ihp-flow.v1',name='joint_typed_metadata_only',pdk='ihp-sg13g2',library='ez130_8t',corners=['tt','ss'],files=files,tools=self.tools,scope=self.f.scope,artifacts={},gates={name:'control.json' for name in producer.GATES},steps=prefix+extra)
        self.f.manifest.write_text(json.dumps(self.data))
        result=subprocess.run([self.f.python,str(self.f.source),'--project',str(self.f.root),'--manifest',str(self.f.manifest),'--workspace',str(self.f.output),'--base-rev','empty','--pdk','ihp','--rustroad',self.f.python,'--gnu-time',str(self.f.timer)],env=self.f.env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.c=dict(run_root=str(self.f.output),files=files,tools=self.tools,scope=self.f.scope)
        self.context=dict(run_root=str(self.f.output),files=files,tools=self.tools)
        self.artifacts=dict(gds=p.identity(self.f.output/'backend/final.gds'))
        self.argv={}
        for step in extra:
            if step['kind']!='command':continue
            values=[self.tools['python']['path'],*step['arguments']]
            for i,v in enumerate(values):
                v=v.replace('{output}',str(self.f.output))
                for ns,records in [('files',files),('tools',self.tools)]:
                    for role,record in records.items():v=v.replace('{'+ns+':'+role+'}',record['path'])
                values[i]=v
            self.argv[step['id']]=values
        if os.environ.get('RUSTEDA_JOINT_CONTROL_OUTPUT'):
            dest=Path(os.environ['RUSTEDA_JOINT_CONTROL_OUTPUT'])/self.id().split('.')[-1];dest.mkdir()
            sources=[self.f.source,self.f.manifest,self.f.owner/'native.json',*self.f.output.rglob('*')];rows=[]
            for i,path in enumerate(p for p in sources if p.is_file()):
                self.assertLess(i,96);self.assertLessEqual(path.stat().st_size,1<<20)
                dst=dest/(str(i)+'-'+path.name);shutil.copyfile(path,dst);rows.append(dict(original=p.identity(path),frozen=p.identity(dst)))
            (dest/'ACTUAL_TYPED.json').write_text(json.dumps(dict(scope='actual twoCPU stdlib typed capture; no EDA; dummy reports must fail full physical qualifier',files=rows),indent=2)+'\n')

    def result_path(self,tag):
        return self.f.output/q.TAP_PATHS['latchup_execution' if tag=='nf_tap_latchup' else 'coverage_execution']

    def consume(self,tag):
        result,emission=q.tap_execution_proof(self.context,self.artifacts,tag,self.argv[tag],p.identity(self.f.manifest),verify)
        with mock.patch.dict(os.environ,self.f.env,clear=True):
            row=verify.producer_command(self.c,None,tag,self.argv[tag],emission)
        return result,row

    def test_actual_child_emission_pins_full_inputs_outputs_and_original_child_as(self):
        for tag in self.argv:
            value,row=self.consume(tag);self.assertEqual(row['exit_code'],0)
            self.assertEqual(value['actual_python_children'],1);self.assertIsNone(value['actual_native_processes'])
            summary=p.read(value['outputs']['summary']['path']);self.assertEqual(summary['as_limits'],[4096<<20,4096<<20])
            self.assertEqual(summary['schema'],'metadata_only_not_physical')

    def test_resigned_summary_controller_and_typed_result_cannot_change_actual_stdout(self):
        tag='nf_tap_coverage';path=self.result_path(tag);emission=p.read(path)
        summary=Path(emission['outputs']['summary']['path']);data=p.read(summary);data['resigned']=True;summary.write_text(json.dumps(data))
        controller=Path(emission['outputs']['controller']['path']);value=p.read(controller);value['report']=p.identity(summary);controller.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.consume(tag)
        emission['outputs']['summary']=p.identity(summary);emission['outputs']['controller']=p.identity(controller);path.write_text(json.dumps(emission))
        with self.assertRaisesRegex(ValueError,'STDOUT_SUBSTITUTION'):self.consume(tag)

    def test_resigned_config_and_all_later_records_still_require_actual_preinput_emission(self):
        tag='nf_tap_coverage';path=self.result_path(tag);emission=p.read(path)
        config=Path(emission['inputs']['config']['path']);value=p.read(config);value['top']='resigned';config.write_text(json.dumps(value));new=p.identity(config)
        for name in ['summary','controller']:
            dst=Path(emission['outputs'][name]['path']);data=p.read(dst);data['config']=new;dst.write_text(json.dumps(data))
        controller=Path(emission['outputs']['controller']['path']);data=p.read(controller);data['report']=p.identity(emission['outputs']['summary']['path']);controller.write_text(json.dumps(data))
        emission['inputs']['config']=new
        for name in ['summary','controller']:emission['outputs'][name]=p.identity(emission['outputs'][name]['path'])
        path.write_text(json.dumps(emission))
        with self.assertRaisesRegex(ValueError,'STDOUT_SUBSTITUTION'):self.consume(tag)

    def test_path_alias_stale_emission_wrong_source_and_actual_stdout_reject(self):
        tag='nf_tap_latchup';path=self.result_path(tag);original=path.read_text();emission=json.loads(original)
        alternate=self.f.root/'same-summary.json';shutil.copyfile(emission['outputs']['summary']['path'],alternate)
        emission['outputs']['summary']=p.identity(alternate);path.write_text(json.dumps(emission))
        with self.assertRaises(ValueError):self.consume(tag)
        emission=json.loads(original);emission['source_pins']['guard']=self.files['producer'];path.write_text(json.dumps(emission))
        with self.assertRaises(ValueError):self.consume(tag)
        path.write_text(original)
        stdout=self.f.output/'logs'/(tag+'.stdout');stdout.write_text('{"stale":true}')
        receipts=self.f.output/'step-receipts.json';rows=p.read(receipts);next(r for r in rows if r['id']==tag)['stdout']=p.identity(stdout);receipts.write_text(json.dumps(rows))
        with self.assertRaisesRegex(ValueError,'STDOUT_SUBSTITUTION'):self.consume(tag)

    def test_wrapper_result_rawbytes_are_pinned_in_actual_stdout(self):
        tag='nf_tap_latchup';path=self.result_path(tag)
        path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'STDOUT_SUBSTITUTION'):
            self.consume(tag)

    def test_actual_child_changed_config_cannot_emit_a_successful_typed_result(self):
        parent=self.f.root/'mutation-run';parent.mkdir();manifest=parent/'manifest.json'
        manifest.write_text(json.dumps(self.data));output=parent/'output'
        env=dict(self.f.env,RUSTEDA_OWNERSHIP=str(parent/'ownership'),RUSTEDA_CAPTURE_MUTATE_CONFIG='1')
        result=subprocess.run([self.f.python,str(self.f.source),'--project',str(self.f.root),
            '--manifest',str(manifest),'--workspace',str(output),'--base-rev','empty','--pdk','ihp',
            '--rustroad',self.f.python,'--gnu-time',str(self.f.timer)],env=env,stdin=subprocess.DEVNULL,
            capture_output=True,text=True,timeout=25)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((output/q.TAP_PATHS['coverage_execution']).exists())
        self.assertIn('JOINT_CAPTURE_COVERAGE_BINDING',(output/'logs/nf_tap_coverage.stderr').read_text())
        receipts=p.read(output/'step-receipts.json')
        self.assertNotEqual(next(r for r in receipts if r['id']=='nf_tap_coverage')['exit_code'],0)
        if os.environ.get('RUSTEDA_JOINT_CONTROL_OUTPUT'):
            dest=Path(os.environ['RUSTEDA_JOINT_CONTROL_OUTPUT'])/'failed-child-mutation';dest.mkdir()
            rows=[]
            for i,path in enumerate(p for p in [manifest,*output.rglob('*')] if p.is_file()):
                self.assertLess(i,96);self.assertLessEqual(path.stat().st_size,1<<20)
                dst=dest/(str(i)+'-'+path.name);shutil.copyfile(path,dst)
                rows.append(dict(original=p.identity(path),frozen=p.identity(dst)))
            (dest/'EXPECTED_FAILURE.json').write_text(json.dumps(dict(scope='actual mutation during stdlib child rejected; no EDA',files=rows),indent=2)+'\n')


class TypedReaderBudgets(unittest.TestCase):
    def test_weaker_v1_joint_context_is_not_an_admitted_fallback(self):
        context=dict(schema='rusteda.native-foundry-first-tap-qualification-inputs.v1',
            run_root='/never-read',files={},tools={},artifacts={},reports={},native_reports={},controls='/never-read')
        with self.assertRaisesRegex(ValueError,'qualification schema'):
            q.qualify(context,Path('/never-written'))

    def test_exponent_overflow_duplicate_and_nonfinite_are_rejected_by_both_readers(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.json'
            for value in ['{"x":1e999}','{"x":-1e999}','{"x":NaN}','{"x":0,"x":1}']:
                path.write_text(value)
                for reader in (q.tap_read,capture.read):
                    with self.subTest(value=value,reader=reader),self.assertRaises(ValueError):reader(path)

    def test_exhausted_absolute_child_budget_refuses_spawn(self):
        with tempfile.TemporaryDirectory() as folder,mock.patch.object(capture.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CHILD_TIME'):
                capture.capture(['never-executed'],Path(folder),time.monotonic()-1)
            spawn.assert_not_called()

    def test_actual_child_log_budget_is_enforced_during_streaming(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'CHILD_LOG_BUDGET'):
                capture.capture([str(Path(os.sys.executable).resolve()),'-c',
                    "import sys;sys.stdout.write('x'*(17<<20))"],Path(folder),time.monotonic()+10)
            self.assertLessEqual((Path(folder)/'stdout.log').stat().st_size,16<<20)

    def test_actual_deadline_kills_and_reaps_sleeping_stdlib_child(self):
        children=[];real=capture.subprocess.Popen
        def spawn(*args,**kwargs):
            child=real(*args,**kwargs);children.append(child);return child
        with tempfile.TemporaryDirectory() as folder,mock.patch.object(capture.subprocess,'Popen',side_effect=spawn):
            with self.assertRaisesRegex(ValueError,'CHILD_DEADLINE'):
                capture.capture([str(Path(os.sys.executable).resolve()),'-c','import time;time.sleep(30)'],
                                Path(folder),time.monotonic()+.2)
        self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())


if __name__=='__main__':unittest.main()

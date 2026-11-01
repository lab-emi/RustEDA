"""Actual producer/metadata consumer controls; no EDA/tool correctness claim.

Estimate<160MiB: one producer and <=2 stdlib Python children, tiny receipts and
manifests; sequential cases. Caller1GiB/60s/CPU24–29, pinned GNUtime required.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from integration.ibex_sg13g2_ez130 import producer
from integration.ibex_sg13g2_ez130.foundry_first import protocol as p, verify


class ActualProducerMetadata(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output, self.owner = self.root / 'output', self.root / 'ownership'
        self.source = self.root / 'producer.py'
        shutil.copyfile(producer.__file__, self.source)
        self.manifest = self.root / 'manifest.json'
        self.timer = Path(os.environ.get('RUSTEDA_TEST_GNU_TIME', '/usr/bin/time'))
        if not self.timer.is_file():
            self.fail('qualified GNUtime required; actual producer controls cannot be skipped')
        cpus, physical = [], set()
        for cpu in sorted(os.sched_getaffinity(0)):
            folder = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
            key = tuple((folder / n).read_text() for n in ('physical_package_id', 'core_id'))
            if key not in physical:
                physical.add(key); cpus.append(cpu)
            if len(cpus) == 2:
                break
        self.assertEqual(len(cpus), 2, 'two physical test CPUs required')
        self.scope = dict(threads=2, cpu_affinity=cpus, memory_mib=256, max_log_bytes=8192)
        self.python = str(Path(sys.executable).resolve(strict=True))
        self.tools = dict(python=p.identity(self.python), rustroad=p.identity(self.python),
                          gnu_time=p.identity(self.timer))
        self.code = "import json;from pathlib import Path;Path('control.json').write_text('{}');print(json.dumps({'protocol_only':True}))"
        self.env = dict(os.environ, RUSTEDA_WORKER_ID='a' * 64, RUSTEDA_OWNERSHIP=str(self.owner),
                        PYTHONDONTWRITEBYTECODE='1')

    def tearDown(self):
        self.temp.cleanup()

    def run_producer(self, mode):
        tags = (['be_ff_export','be_postroute','be_ff_main','be_ff_antenna','be_antenna_repair',
                 'be_antenna_verify','be_extract','be_main','be_antenna'] if mode == 'all_paths'
                else ['serial'] if mode == 'serial' else ['first'] if mode == 'singleton' else ['first','second'])
        steps = [dict(id=tag, kind='command', tool='python', timeout_s=10, arguments=['-c', self.code]) for tag in tags]
        data = dict(schema='rusteda.ihp-flow.v1', name='C7_metadata_only', pdk='ihp-sg13g2',
            library='ez130_8t', corners=['tt','ss'], files=dict(producer=p.identity(self.source)),
            tools=self.tools, scope=self.scope, steps=steps,
            gates={name:'control.json' for name in producer.GATES}, artifacts={})
        if mode == 'all_paths':
            data['parallel_groups'] = [dict(max_parallel=len(names),max_memory_mib=128,
                steps={tag:dict(depends_on=[],cpu_slots=[i],memory_mib=32) for i,tag in enumerate(names)})
                for names in [['be_postroute'],['be_ff_main','be_ff_antenna'],['be_antenna_repair'],
                              ['be_antenna_verify'],['be_extract'],['be_main','be_antenna']]]
        elif mode != 'serial':
            data['parallel_groups'] = [dict(max_parallel=len(tags), max_memory_mib=128,
                steps={tag:dict(depends_on=[],cpu_slots=[i],memory_mib=32) for i,tag in enumerate(tags)})]
        self.data = data
        self.manifest.write_text(json.dumps(data))
        result = subprocess.run([self.python,str(self.source),'--project',str(self.root),
            '--manifest',str(self.manifest),'--workspace',str(self.output),'--base-rev','empty',
            '--pdk','ihp','--rustroad',self.python,'--gnu-time',str(self.timer)],
            env=self.env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.c = dict(run_root=str(self.output),files=data['files'],tools=self.tools,scope=self.scope)
        return tags

    def consume(self, tag):
        with mock.patch.dict(os.environ,self.env,clear=True):
            return verify.producer_command(self.c,None,tag,[self.tools['python']['path'],'-c',self.code],
                                           dict(protocol_only=True))

    def edit_command(self, tag, change):
        path=self.output/'logs'/(tag+'.command.json')
        original=p.read(path);value=copy.deepcopy(original);change(value)
        path.write_text(json.dumps(value))
        try:
            with self.assertRaises((ValueError,KeyError,TypeError)):self.consume(tag)
        finally:path.write_text(json.dumps(original))

    def test_real_serial_receipt_passes_without_scheduler_fields(self):
        tags=self.run_producer('serial')
        self.assertEqual(set(p.read(self.output/'logs/serial.command.json')),{'argv','cwd'})
        self.assertEqual(self.consume(tags[0])['exit_code'],0)

    def test_real_singleton_and_parallel_receipts_pass_with_exact_scheduler_fields(self):
        self.run_producer('singleton')
        row=self.consume('first')
        self.assertEqual(row['cpu_affinity'],self.scope['cpu_affinity'][:1])
        self.assertEqual(row['memory_reservation_mib'],32)
        self.assertEqual(set(p.read(self.output/'logs/first.command.json')),
            {'argv','cwd','cpu_affinity','memory_reservation_mib'})

    def test_real_two_lane_parallel_receipts_pass_for_both_commands(self):
        for tag in self.run_producer('parallel'):self.consume(tag)

    def test_every_foundry_producer_command_path_uses_real_scheduler_receipts(self):
        for tag in self.run_producer('all_paths'):
            with self.subTest(tag=tag):self.consume(tag)

    def test_recipe_source_is_hashed_without_import_or_execution(self):
        self.run_producer('singleton')
        sentinel=self.root/'must-not-be-written'
        self.source.write_text("from pathlib import Path\nPath("+repr(str(sentinel))+").write_text('unexpected')\nraise RuntimeError('source must not execute')\n")
        # This tests the reader's non-execution property, not authenticating a
        # substituted source as the earlier process. External selected-source
        # bindings remain a separate mandatory authority boundary.
        self.c['files']['producer']=p.identity(self.source)
        self.data['files']['producer']=self.c['files']['producer']
        self.manifest.write_text(json.dumps(self.data));owner=self.owner/'native.json'
        native=p.read(owner);native['manifest']=p.identity(self.manifest);owner.write_text(json.dumps(native))
        with mock.patch.dict(os.environ,self.env,clear=True):verify.producer_manifest(self.c)
        self.assertFalse(sentinel.exists())

    def test_malformed_group_budgets_and_dependencies_reject(self):
        self.run_producer('parallel');original=copy.deepcopy(self.data)
        for field,value in [('cpu_slots',[True]),('memory_mib',True),('depends_on',['second'])]:
            data=copy.deepcopy(original);data['parallel_groups'][0]['steps']['first'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):verify.producer_groups(data)
        data=copy.deepcopy(original);data['parallel_groups']=[]
        with self.assertRaises(ValueError):verify.producer_groups(data)

    def test_wrong_lane_reservation_bool_missing_or_unknown_command_fields_reject(self):
        self.run_producer('singleton')
        changes=[lambda v:v.update(cpu_affinity=self.scope['cpu_affinity'][1:]),
            lambda v:v.update(memory_reservation_mib=33),lambda v:v.update(memory_reservation_mib=True),
            lambda v:v.pop('cpu_affinity'),lambda v:v.pop('memory_reservation_mib'),lambda v:v.update(unrecognized=True)]
        for change in changes:
            with self.subTest(change=change):self.edit_command('first',change)

    def test_changed_ordered_argv_cwd_and_serial_extra_telemetry_reject(self):
        self.run_producer('serial')
        for change in [lambda v:v['argv'].append('changed'),lambda v:v.update(cwd=str(self.root)),
                       lambda v:v.update(cpu_affinity=self.scope['cpu_affinity'])]:
            with self.subTest(change=change):self.edit_command('serial',change)

    def test_actual_receipt_scheduler_fields_are_independently_required(self):
        self.run_producer('singleton');path=self.output/'step-receipts.json';original=p.read(path)
        for field,value in [('cpu_affinity',self.scope['cpu_affinity'][1:]),('memory_reservation_mib',33),
                            ('depends_on',['unexpected']),('started_seconds',True),
                            ('wall_seconds',True),('wall_seconds',-1),('unknown',1)]:
            value_rows=copy.deepcopy(original);value_rows[0][field]=value;path.write_text(json.dumps(value_rows))
            with self.subTest(field=field),self.assertRaises(ValueError):self.consume('first')
        value_rows=copy.deepcopy(original);value_rows[0].pop('cpu_affinity');path.write_text(json.dumps(value_rows))
        with self.assertRaises(ValueError):self.consume('first')

    def test_selected_group_reservation_or_argv_change_reject_even_with_updated_manifest_id(self):
        self.run_producer('singleton');owner=self.owner/'native.json';original=copy.deepcopy(self.data)
        for kind in ('reservation','argv'):
            data=copy.deepcopy(original)
            if kind=='reservation':data['parallel_groups'][0]['steps']['first']['memory_mib']=33
            else:data['steps'][0]['arguments'].append('changed')
            self.manifest.write_text(json.dumps(data));native=p.read(owner);native['manifest']=p.identity(self.manifest)
            owner.write_text(json.dumps(native))
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.consume('first')

    def test_changed_pinned_producer_source_or_ownership_reject(self):
        self.run_producer('singleton')
        raw=self.source.read_bytes();self.source.write_bytes(raw+b'\n# changed\n')
        with self.assertRaises(ValueError):self.consume('first')
        self.source.write_bytes(raw)
        native=p.read(self.owner/'native.json');native['request_id']='b'*64
        (self.owner/'native.json').write_text(json.dumps(native))
        with self.assertRaises(ValueError):self.consume('first')

    def test_repeated_placeholder_expansion_rejects_before_large_rendered_state(self):
        self.run_producer('singleton')
        self.data['files']['oversized']=dict(path='x'*65536)
        self.data['steps'][0]['arguments']=['{files:oversized}'*64]
        self.manifest.write_text(json.dumps(self.data));owner=self.owner/'native.json'
        native=p.read(owner);native['manifest']=p.identity(self.manifest);owner.write_text(json.dumps(native))
        with self.assertRaisesRegex(ValueError,'PRODUCER_ARGUMENT_TOTAL'):self.consume('first')


if __name__=='__main__':unittest.main()

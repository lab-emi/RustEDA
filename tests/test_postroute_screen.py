"""Screen-order post-route runner controls with protocol-only stand-in tools, never EDA.

The stand-ins (tiny Python scripts) only move fixture bytes the way the real
tools' interfaces do: a workspace of revisions with an atomically replaced
HEAD, an extraction report naming its output, a DRV report. Nothing here
times, extracts, routes or repairs anything; it exercises the runner's binding,
round, lineage and final-link logic and the qualifier's review of its result.

Estimate <64 MiB, a few hundred small files, about 60 short child processes;
guard 1 GiB/120 s.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from integration.ibex_sg13g2_ez130 import native_postroute, qualify_native

COMMON = r'''
import hashlib, json, os, sys
from pathlib import Path

def identity(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))

def head(ws):
    return (Path(ws) / 'HEAD').read_text().strip()

def commit(ws, revision, parent, routes, extra=None):
    folder = Path(ws) / 'revisions' / revision
    folder.mkdir(parents=True)
    design = dict(format_version=6, revision=revision, parent_revision=parent, stage='placed', fixture=True,
                  **(extra or {}))
    (folder / 'design.json').write_text(json.dumps(design))
    (folder / 'routes.rbin').write_bytes(routes)
    pending = Path(ws) / ('HEAD-' + revision + '.pending')
    pending.write_text(revision + '\n')
    os.replace(pending, Path(ws) / 'HEAD')

def metadata(ws, revision):
    design = json.loads((Path(ws) / 'revisions' / revision / 'design.json').read_text())
    return dict(revision=revision, parent_revision=design['parent_revision'],
                design_file=identity(Path(ws) / 'revisions' / revision / 'design.json'))

def option(argv, name):
    return argv[argv.index(name) + 1]
'''

ROAD = r'''
NETLIST = """module ibex_sram_bench (a, z);
  input a;
  output z;
  wire n1;
  {cell} g1 (.A(a), .Y(n1));
  BUFX1 g2 (.A(n1), .Y(z));
endmodule
"""
argv = sys.argv[1:]
ws = Path(option(argv, '--workspace')) if '--workspace' in argv else None
if 'export-verilog' in argv:
    resized = json.loads((ws / 'revisions' / head(ws) / 'design.json').read_text()).get('resized', {})
    print(NETLIST.format(cell=resized.get('g1', 'BUFX1')), end='')
elif 'export-def' in argv:
    print('DEF fixture ' + hashlib.sha256((ws / 'revisions' / head(ws) / 'routes.rbin').read_bytes()).hexdigest())
elif 'extract' in argv:
    out, profile = Path(option(argv, '--out')), option(argv, '--rc-profile')
    routes = (ws / 'revisions' / head(ws) / 'routes.rbin').read_bytes()
    out.write_bytes(b'*SPEF fixture\n' + routes + b'\n')
    print(json.dumps(dict(schema='rustroad.extract.v1', status='ok', workspace_revision=head(ws),
                          design='ibex_sram_bench', output=identity(out), rc_profile=identity(profile),
                          unannotated_signal_nets=[])))
elif 'apply-eco' in argv:
    plan = json.loads(Path(option(argv, '--plan')).read_text())
    resized = {op['instance']: op['cell'] for op in plan['operations']}
    Path(option(argv, '--out')).write_text(json.dumps(dict(resized=resized)))
elif 'import-pdk' in argv:
    ws.mkdir(parents=True)
    (ws / 'workspace.lock').touch()
    resized = json.loads(Path(option(argv, '--netlist')).read_text())['resized']
    commit(ws, 'rev1', 'rev0', b'', dict(resized=resized))
    print(json.dumps(dict(revision='rev1', stage='placed', routing_status='not_run')))
elif 'place' in argv or 'clock-tree' in argv:
    base = option(argv, '--base-rev')
    revision = 'rev' + str(int(base[3:]) + 1)
    design = json.loads((ws / 'revisions' / base / 'design.json').read_text())
    commit(ws, revision, base, b'', dict(resized=design['resized']))
    print(json.dumps(dict(revision=revision, stage='placed', routing_status='not_run', clock_buffers=1,
                          clock_topology_status='unrouted_topology_checked')))
elif 'route' in argv:
    prior = (option(argv, '--prior-workspace'), option(argv, '--prior-rev'))
    design = json.loads((ws / 'revisions' / 'rev3' / 'design.json').read_text())
    # A STICKY prior keeps violating (the round-budget control); otherwise the rebuild is clean.
    sticky = b'STICKY' in (Path(prior[0]) / 'revisions' / prior[1] / 'routes.rbin').read_bytes()
    commit(ws, 'rev4', 'rev3', b'routes rebuilt ' + json.dumps(design['resized']).encode()
           + (b' VIOLATE STICKY' if sticky else b''), dict(resized=design['resized']))
    print(json.dumps(dict(revision='rev4', stage='placed', routing_status='signal_geometry_checked',
                          eco_prior=dict(workspace=prior[0], revision=prior[1]))))
else:
    sys.exit('unsupported fixture command: ' + ' '.join(argv))
'''

ANTENNA = r'''
argv = sys.argv[1:]
config = json.loads(Path(option(argv, '--inputs')).read_text())
out = Path(option(argv, '--workspace'))
out.mkdir(parents=True)
ws = Path(config['design_workspace']).resolve()
assert head(ws) == config['initial_revision'] == 'rev4'
routes = (ws / 'revisions' / 'rev4' / 'routes.rbin').read_bytes()
design = json.loads((ws / 'revisions' / 'rev4' / 'design.json').read_text())
commit(ws, 'rev5', 'rev4', routes + b' +jumpers', {k: v for k, v in design.items()
       if k not in ('format_version', 'revision', 'parent_revision', 'stage', 'fixture')})
lineage = dict(schema='rusteda.native-antenna-lineage.v4', status='complete', scope='supported_standard_cell_subset',
               route_scope='all_signal_nets', workspace=str(ws), tool=config['tool'], initial_revision='rev4',
               final_revision='rev5', initial=metadata(ws, 'rev4'), final=metadata(ws, 'rev5'))
(out / 'lineage.json').write_text(json.dumps(lineage))
print(json.dumps(dict(status='complete', final_revision='rev5')))
'''

VERIFY = r'''
argv = sys.argv[1:]
config = json.loads(Path(option(argv, '--inputs')).read_text())
out = Path(option(argv, '--workspace'))
out.mkdir(parents=True)
lineage = json.loads(Path(config['lineage']['path']).read_text())
assert identity(config['lineage']['path']) == config['lineage']
assert lineage['workspace'] == str(Path(config['design_workspace']).resolve())
review = dict(schema='rusteda.antenna-v3-independent-lineage-review.v1',
              status='metadata_lineage_verified_requires_full_signoff', workspace=lineage['workspace'],
              initial_revision=lineage['initial_revision'], final_revision=lineage['final_revision'],
              lineage=config['lineage'])
(out / 'result.json').write_text(json.dumps(review))
print(json.dumps(dict(status=review['status'])))
'''

EDA = r'''
argv = sys.argv[1:]
sta = Path(option(argv, '-w'))
if argv[0] == 'init':
    sta.mkdir()
    print('{}')
elif argv[0] == 'import':
    (sta / 'spef.txt').write_text(option(argv, '--spef'))
    print('{}')
else:
    sys.exit('unsupported fixture command')
'''

STA = r'''
argv = sys.argv[1:]
sta = Path(option(argv, '-w'))
if argv[0] == 'run':
    print('{}')
elif argv[0] == 'report':
    corner = option(argv, '--corner')
    spef = Path((sta / 'spef.txt').read_text()).read_bytes()
    rows = []
    if b'VIOLATE' in spef and corner == 'tt':
        rows = [dict(kind='max_capacitance', net='n1', pin='g1/Y', value=0.12, limit=0.1, slack=-0.02)]
    metrics = dict(worst_setup_slack_ns=1.0, worst_hold_slack_ns=0.1, setup_violating_endpoints=0,
                   hold_violating_endpoints=0, max_transition_violating=0,
                   max_capacitance_violating=len(rows), max_fanout_violating=0)
    if option(argv, '--section') == 'summary':
        print(json.dumps(dict(metrics=metrics)))
    else:
        print(json.dumps(dict(scope=dict(wire_model='spef_rc', clock_model='propagated', corner=corner),
                              sections=dict(drv=dict(shown=len(rows), matched=len(rows), rows=rows)),
                              metrics=metrics)))
else:
    sys.exit('unsupported fixture command')
'''

LIBERTY = '''library(fixture_{corner}) {{
  cell(BUFX1) {{
    area : 1;
    pin(A) {{
      direction : input;
    }}
    pin(Y) {{
      direction : output;
      function : "A";
      max_capacitance : 0.1;
    }}
  }}
  cell(BUFX2) {{
    area : 1;
    pin(A) {{
      direction : input;
    }}
    pin(Y) {{
      direction : output;
      function : "A";
      max_capacitance : 0.2;
    }}
  }}
  cell(BUFX4) {{
    area : 1;
    pin(A) {{
      direction : input;
    }}
    pin(Y) {{
      direction : output;
      function : "A";
      max_capacitance : 0.4;
    }}
  }}
}}
'''
LEF = '''MACRO BUFX1
  SIZE 1.0 BY 2.0 ;
END BUFX1
MACRO BUFX2
  SIZE 1.0 BY 2.0 ;
END BUFX2
MACRO BUFX4
  SIZE 1.0 BY 2.0 ;
END BUFX4
'''


def identity(path):
    path = Path(path).resolve(strict=True)
    data = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


class ScreenFixture:
    """A producer-shaped run root after be_extract, with stand-in tools."""

    def __init__(self, violate, sticky=False):
        self.temp = tempfile.TemporaryDirectory(prefix='postroute-screen-fixture-')
        self.root = Path(self.temp.name).resolve() / 'output'
        root = self.root
        (root / 'logs').mkdir(parents=True)
        (root / 'backend/profiles').mkdir(parents=True)
        tools = root.parent / 'tools'
        tools.mkdir()
        self.tools = {}
        for name, body in [('rustroad', ROAD), ('rusteda', EDA), ('ruststa', STA)]:
            path = tools / name
            path.write_text('#!' + sys.executable + '\n' + COMMON + body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            self.tools[name] = identity(path)
        self.tools['python'] = identity(sys.executable)
        for name, body in [('native_antenna_groups.py', ANTENNA), ('verify.py', VERIFY)]:
            (tools / name).write_text(COMMON + body)
        self.files = dict(antenna_runner=identity(tools / 'native_antenna_groups.py'),
                          antenna_verifier=identity(tools / 'verify.py'))
        timing = root.parent / 'timing'
        timing.mkdir()
        for corner in ('tt', 'ss'):
            (timing / f'std_{corner}.lib').write_text(LIBERTY.format(corner=corner))
            (timing / f'ram_{corner}.lib').write_text(f'library(ram_{corner}) {{ }}\n')
        (timing / 'constraints.sdc').write_text('create_clock -period 10 [get_ports a]\n')
        (timing / 'cells.lef').write_text(LEF)
        prects = timing / 'prects.v'
        prects.write_text('  BUFX1 g1 (.A(a), .Y(n1));\n  BUFX1 g2 (.A(n1), .Y(z));\n')
        rc = root / 'backend/profiles/ihp-rc-nom.json'
        rc.write_text('{"protocol_only": true}')
        # Trial: be_route's rev4, then the producer's antenna repair (rev5) and verifier.
        trial = self.trial = root / 'frontend/road-final'
        trial.mkdir(parents=True)
        (trial / 'workspace.lock').touch()
        (trial / 'receipts').mkdir()
        (trial / 'receipts/rev5.json').write_text('{"fixture_receipt": true}')
        self.commit_trial(b'trial routes' + (b' VIOLATE' if violate else b'') + (b' STICKY' if sticky else b''))
        self.extract_template = ['--threads', '2', '--max-expansions', '8000000', 'extract', '--rc-profile',
                                 str(rc), '--corner', 'nom', '--out', '@SPEF@', '--min-coupling-ff', '0.1']
        antenna_inputs = root / 'backend/antenna-inputs.json'
        antenna_inputs.write_text(json.dumps(dict(
            schema='rusteda.native-antenna-inputs.v2', design_workspace=str(trial), initial_revision='rev4',
            tool=self.tools['rustroad'], limits=dict(threads=2), grouping=dict(protocol_only=True))))
        self.python([self.files['antenna_runner']['path'], '--inputs', antenna_inputs, '--workspace',
                     root / 'backend/antenna-repair', '--base-rev', 'empty'])
        verifier_inputs = root / 'backend/antenna-verification-inputs.json'
        verifier_inputs.write_text(json.dumps(dict(
            schema='rusteda.antenna-v3-independent-verifier-inputs.v1',
            lineage=identity(root / 'backend/antenna-repair/lineage.json'), tool=self.tools['rustroad'],
            source_manifest=self.files['antenna_runner'], design_workspace=str(trial),
            evidence_kind='actual_native_artifacts', controller_inputs=identity(antenna_inputs))))
        self.python([self.files['antenna_verifier']['path'], '--inputs', verifier_inputs, '--workspace',
                     root / 'backend/antenna-verification', '--base-rev', 'empty'])
        # be_extract, exactly as the producer records a command.
        spef = root / 'backend/trial.native.nom.spef'
        argv = [self.tools['rustroad']['path'], '--workspace', str(trial),
                *[str(spef) if a == '@SPEF@' else a for a in self.extract_template]]
        timed = ['/protocol-only/gnu-time', '-v', '-o', str(root / 'logs/be_extract.time'), *argv]
        (root / 'logs/be_extract.command.json').write_text(json.dumps(dict(argv=timed, cwd=str(root))))
        with (root / 'logs/be_extract.stdout').open('wb') as out:
            import subprocess
            subprocess.run(argv, stdout=out, check=True, timeout=30)
        self.timing = {key: identity(timing / name) for key, name in [
            ('std_tt', 'std_tt.lib'), ('std_ss', 'std_ss.lib'), ('ram_tt', 'ram_tt.lib'),
            ('ram_ss', 'ram_ss.lib'), ('sdc', 'constraints.sdc')]}
        self.cell_lef, self.prects = timing / 'cells.lef', prects

    def commit_trial(self, routes):
        trial = self.trial
        for revision, parent in [('rev3', 'rev2'), ('rev4', 'rev3')]:
            folder = trial / 'revisions' / revision
            folder.mkdir(parents=True)
            (folder / 'design.json').write_text(json.dumps(dict(format_version=6, revision=revision,
                                                parent_revision=parent, stage='placed', fixture=True)))
            (folder / 'routes.rbin').write_bytes(routes if revision == 'rev4' else b'')
        (trial / 'HEAD').write_text('rev4\n')

    def python(self, argv):
        import subprocess
        subprocess.run([sys.executable, *map(str, argv)], check=True, timeout=30, capture_output=True)

    def inputs(self, **changes):
        root = self.root
        grouped = dict(verifier=self.files['antenna_verifier'],
                       verifier_inputs=identity(root / 'backend/antenna-verification-inputs.json'),
                       verification=identity(root / 'backend/antenna-verification/result.json'))
        value = dict(
            schema='rusteda.native-postroute-inputs.v2', top='ibex_sram_bench',
            trial=dict(workspace=str(self.trial), revision='rev4'),
            final_workspace=str(root / 'backend/postroute/road'),
            final_spef=str(root / 'backend/native.nom.spef'), prects_netlist=str(self.prects),
            recipe={'import': ['import-pdk', '--base-rev', 'rev0', '--netlist', '@NETLIST@'],
                    'place': ['place', '--base-rev', 'rev1'], 'cts': ['clock-tree', '--base-rev', 'rev2'],
                    'route': ['--threads', '2', 'route', '--base-rev', 'rev3', '--scope', 'all'],
                    'extract': list(self.extract_template)},
            screen=dict(spef=identity(root / 'backend/trial.native.nom.spef'),
                        extract_report=identity(root / 'logs/be_extract.stdout'),
                        extract_command=identity(root / 'logs/be_extract.command.json')),
            antenna=dict(inputs=identity(root / 'backend/antenna-inputs.json'),
                         lineage=identity(root / 'backend/antenna-repair/lineage.json'),
                         runner=self.files['antenna_runner'], **grouped),
            profiles=dict(sizing=str(root / 'unused-sizing.json'), delay=str(root / 'unused-delay.json'),
                          cell_buses=str(root / 'unused-buses.json'), cell_lef=str(self.cell_lef)),
            timing=self.timing, tools=self.tools,
            policy=dict(near_limit_ratio=0.9, target_ratio=0.8, max_rounds=2, max_resizes=256),
            limits=dict(threads=2, deadline_seconds=600))
        value.update(changes)
        path = root / 'backend/postroute-inputs.json'
        path.write_text(json.dumps(value))
        return path

    def run(self, **changes):
        inputs = self.inputs(**changes)
        out = self.root / 'backend/postroute'
        out.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            native_postroute.Runner(inputs, out.resolve()).execute()
        return json.loads((out / 'result.json').read_text())

    def review(self, result):
        root = self.root

        def current(relative):
            return (root / relative).resolve(strict=True)
        route = dict(revision='rev4', routing_status='signal_geometry_checked')
        cts = dict(revision='rev3')
        artifacts = dict(spef_native_nom=identity(root / 'backend/native.nom.spef'))
        return qualify_native.postroute_screen_lineage(result, root, route, cts, dict(rustroad=self.tools['rustroad']),
                                                       artifacts, current)


class ScreenOrderRunner(unittest.TestCase):
    def fixture(self, violate=False, sticky=False):
        f = ScreenFixture(violate, sticky)
        self.addCleanup(f.temp.cleanup)
        return f

    def test_clean_screen_binds_be_extract_and_links_without_extracting(self):
        f = self.fixture()
        result = f.run()
        self.assertEqual((result['status'], result['final_source'], result['final_revision']),
                         ('clean', 'trial_link', 'rev5'))
        names = [row['name'] for row in result['commands']]
        self.assertFalse(any('extract' in name for name in names))
        self.assertEqual(names, ['check0-netlist', 'check0-sta-init', 'check0-sta-import', 'check0-sta-run',
                                 'check0-tt-summary', 'check0-tt-drv', 'check0-ss-summary', 'check0-ss-drv'])
        final, trial = f.root / 'backend/postroute/road', f.trial
        for relative in ['revisions/rev5/routes.rbin', 'revisions/rev4/design.json', 'receipts/rev5.json']:
            self.assertEqual(os.stat(final / relative).st_ino, os.stat(trial / relative).st_ino)
        for fresh in ['HEAD', 'workspace.lock']:
            self.assertNotEqual(os.stat(final / fresh).st_ino, os.stat(trial / fresh).st_ino)
        self.assertEqual((final / 'HEAD').read_text(), 'rev5\n')
        spef = f.root / 'backend/native.nom.spef'
        self.assertEqual(os.stat(spef).st_ino, os.stat(f.root / 'backend/trial.native.nom.spef').st_ino)
        screen = result['screens'][0]
        self.assertEqual(screen['revision_files']['routes.rbin'], identity(trial / 'revisions/rev5/routes.rbin'))
        argv = json.loads((f.root / 'logs/be_extract.command.json').read_text())['argv'][4:]
        self.assertEqual(screen['extraction']['argv_sha256'], hashlib.sha256(json.dumps(argv).encode()).hexdigest())
        route, cts, summary, report = f.review(result)
        self.assertEqual(summary, dict(source='trial_link', status='clean', resized=0, screens=1,
                                       trial_revision='rev5'))
        self.assertEqual(route['revision'], 'rev4')
        self.assertEqual(report['output'], screen['extraction']['spef'])

    def test_forced_resize_rebuilds_repairs_verifies_extracts_and_rescreens(self):
        f = self.fixture(violate=True)
        result = f.run()
        self.assertEqual((result['status'], result['final_source'], result['resized']),
                         ('repaired', 'eco_rebuild', {'g1': 'BUFX2'}))
        self.assertEqual([s['source'] for s in result['screens']], ['trial', 'eco_rebuild'])
        first, second = result['rounds']
        self.assertEqual([op['cell'] for op in first['resizes']], ['BUFX2'])
        self.assertEqual(first['build']['eco_prior'], dict(workspace=str(f.trial), revision='rev5'))
        self.assertEqual(second['resizes'], [])
        build = f.root / 'backend/postroute/build0'
        rebuilt = result['screens'][1]
        self.assertEqual((rebuilt['workspace'], rebuilt['revision']), (str(build), 'rev5'))
        names = [row['name'] for row in result['commands']]
        for name in ['apply0', 'build0-route', 'antenna1', 'antenna1-verify', 'check1-extract', 'check1-sta-run']:
            self.assertIn(name, names)
        antenna = json.loads((f.root / 'backend/postroute/antenna1-inputs.json').read_text())
        template = json.loads((f.root / 'backend/antenna-inputs.json').read_text())
        self.assertEqual(antenna, dict(template, design_workspace=str(build)))
        extract = next(row for row in result['commands'] if row['name'] == 'check1-extract')['argv']
        self.assertEqual(extract[3:], [str(f.root / 'backend/postroute/check1/native.nom.spef')
                                       if a == '@SPEF@' else a for a in f.extract_template])
        final = f.root / 'backend/postroute/road'
        self.assertEqual(os.stat(final / 'revisions/rev5/routes.rbin').st_ino,
                         os.stat(build / 'revisions/rev5/routes.rbin').st_ino)
        self.assertEqual(os.stat(f.root / 'backend/native.nom.spef').st_ino,
                         os.stat(f.root / 'backend/postroute/check1/native.nom.spef').st_ino)
        self.assertNotIn(b'VIOLATE', (f.root / 'backend/native.nom.spef').read_bytes())
        route, cts, summary, report = f.review(result)
        self.assertEqual(summary, dict(source='eco_rebuild', status='repaired', resized=1, screens=2,
                                       trial_revision='rev5'))
        self.assertEqual(route['eco_prior'], dict(workspace=str(f.trial), revision='rev5'))
        self.assertEqual(report['workspace_revision'], 'rev5')

    def test_round_budget_exhausted_keeps_the_last_screened_rebuild(self):
        f = self.fixture(violate=True, sticky=True)
        result = f.run(policy=dict(near_limit_ratio=0.9, target_ratio=0.8, max_rounds=1, max_resizes=256))
        self.assertEqual(result['status'], 'round_budget_exhausted')
        self.assertEqual([[op['cell'] for op in r['resizes']] for r in result['rounds']], [['BUFX2'], ['BUFX4']])
        self.assertEqual(result['resized'], {'g1': 'BUFX2'})
        self.assertEqual(len(result['screens']), 2)
        self.assertEqual(result['final_link']['source'], str(f.root / 'backend/postroute/build0'))
        self.assertIn(b'VIOLATE', (f.root / 'backend/native.nom.spef').read_bytes())
        self.assertEqual(f.review(result)[2]['status'], 'round_budget_exhausted')

    def test_extraction_argv_report_or_lineage_drift_is_rejected(self):
        cases = []
        f = self.fixture()
        command = f.root / 'logs/be_extract.command.json'
        value = json.loads(command.read_text())
        value['argv'].insert(-2, '--extract-query-workers')
        command.write_text(json.dumps(value))
        cases.append((f, 'argv differs'))
        f = self.fixture()
        (f.trial / 'HEAD').write_text('rev4\n')  # the screened HEAD is not where the lineage ends
        cases.append((f, 'does not (bind|end at) the (trial|screened) HEAD'))
        f = self.fixture()
        report = f.root / 'logs/be_extract.stdout'
        value = json.loads(report.read_text())
        value['workspace_revision'] = 'rev4'
        report.write_text(json.dumps(value))
        cases.append((f, 'does not bind its SPEF'))
        for fixture, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(native_postroute.Failure, message):
                fixture.run()

    def test_spef_or_lineage_changed_after_render_is_rejected(self):
        f = self.fixture()
        inputs = f.inputs()
        with (f.root / 'backend/trial.native.nom.spef').open('ab') as stream:
            stream.write(b'late edit')
        out = f.root / 'backend/postroute'
        out.mkdir()
        with self.assertRaisesRegex(native_postroute.Failure, 'input changed'):
            native_postroute.Runner(inputs, out.resolve())

    def test_final_spef_must_be_fresh_beside_the_runner(self):
        f = self.fixture()
        for path in [f.root / 'native.nom.spef', f.root / 'backend/trial.native.nom.spef']:
            out = f.root / 'backend/postroute'
            with self.subTest(path=path), self.assertRaisesRegex(native_postroute.Failure, 'final SPEF'):
                out.mkdir()
                try:
                    native_postroute.Runner(f.inputs(final_spef=str(path)), out.resolve())
                finally:
                    out.rmdir()

    def test_qualifier_rejects_changed_final_bytes_spef_or_selection(self):
        f = self.fixture(violate=True)
        result = f.run()
        mutations = []
        changed = json.loads(json.dumps(result))
        changed['final_source'] = 'trial_link'
        mutations.append((changed, 'final selection'))
        changed = json.loads(json.dumps(result))
        changed['screens'][1]['revision_files']['routes.rbin']['sha256'] = '0' * 64
        mutations.append((changed, 'stale/external'))
        changed = json.loads(json.dumps(result))
        changed['rounds'][0]['build']['eco_prior']['revision'] = 'rev4'
        mutations.append((changed, 'rebuild chain'))
        changed = json.loads(json.dumps(result))
        changed['screens'][1]['antenna']['verification'] = None
        mutations.append((changed, 'verified exactly when'))
        changed = json.loads(json.dumps(result))
        changed['screens'][0]['extraction']['argv_sha256'] = '0' * 64
        mutations.append((changed, 'argv differs'))
        for value, message in mutations:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                f.review(value)
        # An edit of the final workspace's revision table (shared inode) is caught by bytes.
        with (f.root / 'backend/postroute/road/revisions/rev5/routes.rbin').open('ab') as stream:
            stream.write(b'!')
        with self.assertRaisesRegex(ValueError, 'differs from the screened revision|stale'):
            f.review(result)


class LineageMetadata(unittest.TestCase):
    def lineage(self, schema, ledger=True):
        temp = tempfile.TemporaryDirectory(prefix='lineage-metadata-fixture-')
        self.addCleanup(temp.cleanup)
        value = dict(schema=schema, status='complete', scope='supported_standard_cell_subset',
                     route_scope='all_signal_nets', workspace='/protocol-only', tool={}, initial_revision='rev4',
                     final_revision='rev9', initial={}, final={}, workspace_head={},
                     operations=['x' * 100] * 2000)  # attempt/operation arrays well past 64 KiB
        if ledger:
            value.update(logical_revisions=5, committed_revision='rev9', check_rows='violated',
                         materialized_revisions=['rev4', 'rev9'])
        path = Path(temp.name) / 'lineage.json'
        path.write_text(json.dumps(value))
        return path

    def test_v4_ledger_after_the_arrays_is_read_and_v3_stays_prefix_only(self):
        value = qualify_native.antenna_lineage_metadata(self.lineage('rusteda.native-antenna-lineage.v4'))
        self.assertEqual((value['logical_revisions'], value['committed_revision'], value['check_rows'],
                          value['materialized_revisions']), (5, 'rev9', 'violated', ['rev4', 'rev9']))
        self.assertNotIn('operations', value)
        value = qualify_native.antenna_lineage_metadata(self.lineage('rusteda.native-antenna-lineage.v3', False))
        self.assertEqual(set(value), qualify_native.LINEAGE_METADATA)
        with self.assertRaisesRegex(ValueError, 'ledger missing'):
            qualify_native.antenna_lineage_metadata(self.lineage('rusteda.native-antenna-lineage.v4', False))


if __name__ == '__main__':
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""The native composers driven by a PDK profile (integration/ibex_pdks/NATIVE.md).

A synthetic profile with other corner names, two cell libraries, one macro master with three
instances and no referee steps is composed by the host driver; the IHP profile must keep
describing the published recipe. Files are small text stand-ins: composing reads identities
and the JSON policies only, never the PDK data.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'integration/ibex_sg13g2_ez130'
COMPOSE = ROOT / 'integration/ibex_host_benchmark/compose_native.py'


def load(path):
    spec = importlib.util.spec_from_file_location('profile_test_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fe = load(RECIPE / 'native_frontend.py')
be = load(RECIPE / 'native_backend.py')

SELECTION = dict(
    top='ibex_sram_bench', primary_clock='clk_i', clock_sink_pin='CLK', gating_cells=['and2_1'],
    buffers=[dict(cell='buf_1', input='A', output='X')], primary_buffer='buf_1',
    reset=dict(pin='RESET_B', leaf='buf_1', trunk='buf_1'), delay_cells=['dly_1'], target_fanout=9,
    cap_fraction=0.8, hold_margin_ns=0.075, setup_margin_ns=0.05,
    electrical=dict(driver_cells=['buf_1'], sram_buffer='buf_1', sram_clock_buffer='buf_1',
                    sram_masters=['sram512'], load_fraction=0.6, max_wire_um=1200, antenna_cells=[],
                    drv_margin=0.15, hold_drv_margin=0.1,
                    clock_balance=dict(cell='dly_1', max_stages=32, margin_ns=0.1, wire_pf=0.002)),
    launch_chain_max=32)
GROUP_POLICY = dict(group_names=128, max_groups=1024, max_attempts=64, max_total_jumpers=4096, max_commands=4096,
                    max_report_bytes=8 << 30, max_selected_report_bytes=128 << 20, max_metadata_bytes=4 << 20,
                    max_cumulative_work=200000000000)
SESSION_POLICY = dict(schema='rusteda.antenna-session-policy.v1', execution_mode='single_native_process',
                      actual_native_processes=1)


class Tree:
    """A PDK tree, a profile directory, a prepared design and a host map under one root."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.pdk, self.native, self.prep = self.root / 'pdk', self.root / 'profile/native', self.root / 'prep'
        files = {
            'lib/cells_typ.lib': 'library(typ) {}\n', 'lib/cells_slow.lib': 'library(slow) {}\n',
            'lib/sram_typ.lib': 'library(sram) {}\n',
            'lef/tech.lef': 'VERSION 5.7 ;\nEND LIBRARY\n',
            'lef/cells.lef': 'VERSION 5.7 ;\nMACRO buf_1\n  SIZE 1 BY 2 ;\nEND buf_1\nEND LIBRARY\n',
            'lef/extra.lef': 'VERSION 5.7 ;\nMACRO fill_12\n  SIZE 3 BY 2 ;\nEND fill_12\n\nEND LIBRARY\n',
            'lef/sram.lef': 'VERSION 5.4 ;\nMACRO sram512\n  CLASS BLOCK ;\n  SIZE 400 BY 300 ;\nEND sram512\nEND LIBRARY\n',
            'gds/cells.gds': 'cells', 'gds/extra.gds': 'extra', 'gds/sram.gds': 'sram',
            'tech/layers.map': 'DIEAREA ALL 0 0\n', 'tech/rcx.rules': 'Extraction Rules for OpenRCX\n',
            'tech/magic.tech': 'tech\nend\n',
        }
        for name, text in files.items():
            self.write(self.pdk / name, text)
        self.write(self.prep / 'rtl/design.v', 'module ibex_sram_bench; endmodule\n')
        self.write(self.prep / 'constraints.sdc', 'create_clock -name clk_i -period 100 [get_ports {clk_i}]\n')
        policies = {
            'floorplan.json': dict(name='ibex_sram_bench', units_per_micron=2000, pin_buses={}),
            'physical.json': dict(filler_cells=[], tap={}, clock={}),
            'routing.json': dict(track_router=dict(min_routing_layer='Metal2', max_routing_layer='Metal5')),
            'selection.json': SELECTION, 'interfaces.json': dict(cell_buses={}),
            'rc-estimate-policy.json': dict(schema='rusteda.rc-estimate-policy.v1'),
            'contract.json': dict(schema='rusteda.native-pdk-contract.v1'),
            'antenna-group-policy.json': GROUP_POLICY, 'antenna-session-policy.json': SESSION_POLICY,
        }
        for name, value in policies.items():
            self.write(self.native / name, json.dumps(value, indent=1) + '\n')
        self.profile = {
            'schema': 'rusteda.native-pdk-profile.v1', 'name': 'synthetic', 'pdk': 'synthPDK-1',
            'library': 'synth_sc_7t', 'top': 'ibex_sram_bench',
            'corners': [
                {'name': 'typ', 'rc': 'nom', 'liberty': {'std': 'pdk:lib/cells_typ.lib', 'mem': 'pdk:lib/sram_typ.lib'}},
                {'name': 'slow', 'rc': 'nom', 'liberty': {'mem': 'pdk:lib/sram_typ.lib', 'std': 'pdk:lib/cells_slow.lib'}}],
            'lef': {'technology': 'pdk:lef/tech.lef', 'cells': ['pdk:lef/cells.lef', 'pdk:lef/extra.lef'],
                    'combined': None},
            'gds': {'cells': ['pdk:gds/cells.gds', 'pdk:gds/extra.gds']},
            'macros': [{'id': 'mem', 'master': 'sram512', 'instances': ['u_a', 'u_b', 'u_c'],
                        'lef': 'pdk:lef/sram.lef', 'gds': 'pdk:gds/sram.gds'}],
            'gds_layer_map': {'klayout': 'pdk:tech/layers.map', 'layers': ['Metal1', 'Via1', 'Metal2']},
            'rc': {'builder': 'gf180', 'resistance_model': 'magic',
                   'rules': {'path': 'pdk:tech/rcx.rules',
                             'sha256': fe.identity(self.pdk / 'tech/rcx.rules')['sha256']},
                   'magic_tech': 'pdk:tech/magic.tech', 'magic_extract': None},
            'synthesis': {'dont_use': ['*__dly*', 'cell_dbl_?']},
            'allocations': [2, 4],
        }
        tool = str(Path(sys.executable).resolve())
        self.host = {'prepared': str(self.prep), 'roots': {'pdk': str(self.pdk)}, 'threads': 4,
                     'cpu_affinity': [0, 1, 2, 3],
                     'tools': {name: tool for name in ('rustsys', 'rusteda', 'ruststa', 'rustroad', 'python',
                                                       'gnu_time', 'xxhsum', 'pin_caps')}}
        self.count = 0

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def compose(self, profile=None, host=None):
        self.count += 1
        self.write(self.native / 'profile.json', json.dumps(profile or self.profile, indent=1) + '\n')
        self.write(self.root / 'host.json', json.dumps(host or self.host))
        work = self.root / f'compose-{self.count}'
        # The reference sources of the modules are pinned from the superproject checkout; this
        # checkout may be a worktree without them, so stand-ins are given.
        sources = self.root / 'sources'
        for name in ('modules/RustROAD/scripts/build_delay_cells.py', 'modules/RustROAD/scripts/build_sizing_profile.py',
                     'modules/RustROAD/scripts/liberty_lite.py',
                     'modules/RustEDA-Common/crates/formats/rusteda-formats/examples/export_pin_caps.rs'):
            self.write(sources / name, '# stand-in\n')
        done = subprocess.run([sys.executable, str(COMPOSE), '--host', str(self.root / 'host.json'), '--workspace',
                               str(work), '--base-rev', 'empty', '--profile', str(self.native),
                               '--source-root', str(sources)], capture_output=True, text=True)
        return done, work


class ProfileComposition(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tree = Tree(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self, **kwargs):
        done, work = self.tree.compose(**kwargs)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads((work / 'be/prefix-manifest.json').read_text()), work

    def test_a_profile_composes_a_native_flow_manifest_for_its_pdk(self):
        manifest, work = self.manifest()
        self.assertEqual((manifest['schema'], manifest['pdk'], manifest['library'], manifest['corners']),
                         ('rusteda.native-flow.v1', 'synthPDK-1', 'synth_sc_7t', ['typ', 'slow']))
        files = manifest['files']
        # The profile and its contract are pinned; the Liberty files are <group>_<corner>.
        self.assertEqual(files['profile']['path'], str(self.tree.native / 'profile.json'))
        self.assertEqual(files['contract']['path'], str(self.tree.native / 'contract.json'))
        self.assertEqual([k for k in files if k.endswith(('_typ', '_slow'))],
                         ['std_typ', 'std_slow', 'mem_typ', 'mem_slow'])
        self.assertEqual(files['mem_typ'], files['mem_slow'])   # one macro Liberty serves both corners
        self.assertEqual([k for k in files if k.endswith('_gds')], ['std_gds', 'std2_gds', 'mem_gds'])
        self.assertNotIn('ram_lef', files)
        self.assertNotIn('mem_lef', files)                      # macro LEFs are referee inputs only
        steps = {s['id']: s for s in manifest['steps']}
        order = [s['id'] for s in manifest['steps']]
        self.assertEqual(order[-1], 'be_gds')
        self.assertFalse({'be_openrcx', 'be_timing', 'be_main', 'be_antenna', 'be_lvs', 'be_macro_contract'} & set(order))
        self.assertEqual([i for i in order if i.startswith('fe_caps_')],
                         ['fe_caps_typ_std', 'fe_caps_typ_mem', 'fe_caps_slow_std', 'fe_caps_slow_mem'])
        self.assertEqual(steps['fe_caps_slow_mem']['arguments'], ['{files:mem_slow}'])
        self.assertEqual(steps['fe_caps_slow_mem']['stdout_artifact'], 'fe_caps_slow_mem')
        # Synthesis maps with the first corner's libraries and excludes the profile's cells.
        imp = steps['fe_synth_import']['arguments']
        self.assertEqual(imp[imp.index('--lib'):imp.index('--rtl')],
                         ['--lib', '{files:std_typ}', '--corner', 'typ', '--lib', '{files:mem_typ}', '--corner', 'typ'])
        self.assertEqual(steps['fe_synth']['arguments'][-4:], ['--dont-use', '*__dly*', '--dont-use', 'cell_dbl_?'])
        # Timing imports and reports follow the corners.
        imp = steps['fe_final_import']['arguments']
        self.assertEqual([imp[k + 1] for k, a in enumerate(imp) if a == '--lib'],
                         ['{files:std_typ}', '{files:mem_typ}', '{files:std_slow}', '{files:mem_slow}'])
        self.assertEqual([imp[k + 1] for k, a in enumerate(imp) if a == '--spef-corner'], ['typ', 'slow'])
        self.assertIn('fe_final_slow_drv', steps)
        self.assertEqual(steps['fe_final_slow_drv']['stdout_artifact'], 'fe_final_slow_drv')
        # The context hands the profile and the contract to the planning helpers.
        context = steps['fe_context']['content']
        self.assertEqual(context['files']['profile'], {'$input': 'profile'})
        self.assertEqual(context['files']['contract'], {'$input': 'contract'})
        self.assertEqual(context['selection'], SELECTION)
        # The physical inputs name every GDS library and no PDK directory.
        physical = steps['be_physical_inputs']['content']
        self.assertEqual(physical['schema'], 'rusteda.native-physical-inputs.v3')
        self.assertEqual((physical['pdk'], physical['library']), ('synthPDK-1', 'synth_sc_7t'))
        self.assertEqual(physical['cell_gds'], ['std_gds', 'std2_gds', 'mem_gds'])
        self.assertEqual(physical['layers'], ['Metal1', 'Via1', 'Metal2'])
        self.assertEqual(physical['rc'], {'builder': 'gf180', 'resistance_model': 'magic'})
        self.assertEqual(list(physical['files']),
                         ['std_gds', 'std2_gds', 'mem_gds', 'layer_map', 'tech_lef', 'rules_nom', 'magic_tech'])
        self.assertNotIn('pdk_root', physical)
        extract = steps['be_extract']['arguments']
        self.assertEqual(extract[extract.index('--rc-profile') + 1], '{output}/backend/profiles/rc-nom.json')
        postroute = steps['be_postroute_inputs']['content']
        self.assertEqual(postroute['profile'], dict(pdk='synthPDK-1', corners=['typ', 'slow'],
                                                    groups=['std', 'mem'], dbu_per_um=2000))
        self.assertEqual(list(postroute['timing']), ['std_typ', 'mem_typ', 'std_slow', 'mem_slow', 'sdc'])
        gds = steps['be_gds']['arguments']
        self.assertEqual(gds[gds.index('--out') + 1], '{output}/backend/ibex_sram_bench.gds')
        self.assertEqual(manifest['artifacts']['gds'], 'backend/ibex_sram_bench.gds')
        self.assertNotIn('spef_openrcx_nom', manifest['artifacts'])
        # No referee tool and no foundry-check group; the frontend is one four-CPU group.
        self.assertFalse({'appimage', 'opensta', 'yosys'} & set(manifest['tools']))
        self.assertEqual(len(manifest['parallel_groups']), 1)
        # The combined LEF is derived: the first cell LEF, then the MACRO blocks of the others.
        combined = Path(files['cell_lef']['path'])
        self.assertEqual(combined, work / 'collateral/combined-cells.lef')
        self.assertEqual(combined.read_text(),
                         'VERSION 5.7 ;\nMACRO buf_1\n  SIZE 1 BY 2 ;\nEND buf_1\n\n'
                         'MACRO fill_12\n  SIZE 3 BY 2 ;\nEND fill_12\n\n'
                         'MACRO sram512\n  CLASS BLOCK ;\n  SIZE 400 BY 300 ;\nEND sram512\n\nEND LIBRARY\n')
        # The pinned reference executor accepts the manifest's own stream declarations.
        producer = load(RECIPE / 'producer.py')
        bound, corners, groups = producer.flow_identity(manifest)
        self.assertEqual((bound, corners, groups), (True, ('typ', 'slow'), ('mem', 'std')))
        producer.validate_stdout_artifacts(manifest['steps'], manifest['stdout_artifacts'], manifest['scope'],
                                           corners, groups)
        producer.check_profile(manifest, files['profile'])
        with self.assertRaisesRegex(ValueError, 'non-export command'):
            producer.validate_stdout_artifacts(manifest['steps'], manifest['stdout_artifacts'], manifest['scope'])
        other = dict(manifest, library='another')
        with self.assertRaisesRegex(ValueError, 'differs from its pinned PDK profile'):
            producer.check_profile(other, files['profile'])

    def test_tracked_layer_map_and_rc_profile_replace_the_pdk_sources(self):
        self.tree.write(self.tree.native / 'gds-map.json', '{"schema": "rustroad.gds-layer-map.v1"}\n')
        self.tree.write(self.tree.native / 'rc-nom.json', '{"schema": "rustroad.rc-profile.v1"}\n')
        profile = dict(self.tree.profile, gds_layer_map={'tracked': 'profile:gds-map.json'},
                       rc={'profile': 'profile:rc-nom.json'})
        manifest, _ = self.manifest(profile=profile)
        physical = {s['id']: s for s in manifest['steps']}['be_physical_inputs']['content']
        self.assertEqual((physical['layers'], physical['rc']), ([], {}))
        self.assertEqual(list(physical['files']), ['std_gds', 'std2_gds', 'mem_gds', 'gds_map', 'tech_lef', 'rc_profile'])
        self.assertFalse({'layer_map', 'rules_nom', 'magic_tech'} & set(manifest['files']))

    def test_what_a_profile_cannot_state_is_refused(self):
        def refused(message, profile=None, host=None):
            done, work = self.tree.compose(profile=profile, host=host)
            self.assertNotEqual(done.returncode, 0, message)
            self.assertIn(message, done.stderr)
            self.assertFalse((work / 'be/prefix-manifest.json').exists())

        base = self.tree.profile
        rules = dict(base['rc'], rules={'path': 'pdk:tech/rcx.rules', 'sha256': '0' * 64})
        refused('differs from the SHA-256 the profile pins', dict(base, rc=rules))
        refused("names no root 'ciel'", dict(base, lef=dict(base['lef'], technology='ciel:lef/tech.lef')))
        refused('must stay below its root', dict(base, lef=dict(base['lef'], technology='pdk:../prep/constraints.sdc')))
        refused('physical allocation must be one of 2, 4', host=dict(self.tree.host, threads=16,
                                                                  cpu_affinity=list(range(16))))
        refused('unknown PDK profile field: tracks', dict(base, tracks=1))
        refused('PDK profile corner names', dict(base, corners=[dict(base['corners'][0], name='TT'), base['corners'][1]]))
        refused('liberty needs std and every macro identifier',
                dict(base, corners=[base['corners'][0], dict(base['corners'][1], liberty={'std': 'pdk:lib/cells_slow.lib'})]))
        refused('PDK profile referee_steps', dict(base, referee_steps=['timing']))
        refused('the rusteda.ihp-flow.v1 recipe is the IHP SG13G2/EZ130 identity',
                dict(base, flow_schema='rusteda.ihp-flow.v1'))
        three = base['corners'] + [dict(base['corners'][1], name='fast')]
        refused('the post-route repair policy needs exactly two corners', dict(base, corners=three))
        refused('backend_steps native', host=dict(self.tree.host, backend_steps='python'))
        self.tree.write(self.tree.native / 'antenna-group-policy.json', json.dumps(dict(GROUP_POLICY, max_groups=1)))
        refused('v2 antenna policy must match qualified integer budgets')


class IhpProfile(unittest.TestCase):
    """The tracked IHP profile is the published recipe: the composers' default."""

    def test_the_default_profile_is_the_published_ihp_recipe(self):
        profile = fe.load_profile()
        self.assertTrue(profile.legacy)
        self.assertEqual(profile.manifest_identity(), dict(pdk='ihp-sg13g2', library='ez130_8t', corners=['tt', 'ss']))
        self.assertEqual(profile.frontend_file_keys(), {
            'rtl', 'sdc', 'std_tt', 'ram_tt', 'std_ss', 'ram_ss', 'tech_lef', 'cell_lef', 'std_lef', 'floorplan',
            'physical', 'routing', 'interfaces', 'ecos', 'frontend', 'prepare', 'delay_helper', 'sizing_helper',
            'liberty_reader', 'pin_caps_source', 'producer', 'estimator', 'rc_policy'})
        self.assertEqual(profile.liberty_keys(), ['std_tt', 'std_ss', 'ram_tt', 'ram_ss'])
        self.assertEqual((profile.allocations, profile.dont_use, profile.top), ([2, 4, 16], [], 'ibex_sram_bench'))
        backend = be.BackendProfile(profile)
        self.assertEqual(backend.physical_keys(),
                         ['std_gds', 'ram_gds', 'layer_map', 'tech_lef', 'rules_nom', 'magic_tech', 'magic_extract'])
        self.assertEqual(backend.referee_steps, be.REFEREE_STEPS)
        self.assertEqual(backend.rc_profile_name(), 'ihp-rc-nom.json')
        self.assertEqual(backend.rc_calibration_name, 'ihp/libs.tech/librelane/openrcx/IHP_rcx_patterns.rules')
        self.assertEqual(profile.data['macros'][0]['instances'], ['u_imem', 'u_dmem'])

    def test_the_ihp_selection_and_contract_are_data(self):
        native = ROOT / 'integration/ibex_pdks/ihp_sg13g2_ez130/native'
        selection = json.loads((native / 'selection.json').read_text())
        self.assertEqual(selection['top'], 'ibex_sram_bench')
        self.assertEqual([b['cell'] for b in selection['buffers']],
                         ['BUFX%d' % n for n in (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 44, 48, 64)])
        self.assertEqual(selection['electrical']['sram_masters'], [fe.load_profile().macros[0]['master']])
        contract = json.loads((native / 'contract.json').read_text())
        physical = json.loads((RECIPE / 'native-physical.json').read_text())
        routing = json.loads((RECIPE / 'native-routing.json').read_text())
        # The recipe's own policies satisfy the contract the planning helpers enforce for it.
        self.assertEqual(len(physical['filler_cells']), contract['physical']['filler_cells'])
        self.assertEqual(physical['tap']['body_view'], contract['physical']['tap']['body_view'])
        self.assertIn(dict(max_gap=physical['tap']['max_gap'], pattern=physical['tap'].get('pattern')),
                      contract['physical']['tap']['lattices'])
        self.assertEqual({k: routing['track_router'][k] for k in ('min_routing_layer', 'max_routing_layer')},
                         contract['routing'])


if __name__ == '__main__':
    unittest.main()

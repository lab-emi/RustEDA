"""Final-layout area acceptance controls; arithmetic fixtures are not layouts."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration/ibex_sg13g2_ez130'))
import final_density as density
import qualify_native


class FinalDensityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lef = self.root / 'cells.lef'
        self.lef.write_text('\n'.join(
            f'MACRO {name}\n SIZE {width} BY {height} ;\nEND {name}'
            for name, width, height in [('SRAM', 10, 10), ('STD', 10, 10),
                                        ('TAP', 10, 10), ('FILL', 2, 10)]))
        self.common = dict(core_um=[0, 0, 100, 100], macro_halo_um=2, macros={
            'ram_a': dict(cell='SRAM', location_um=[10, 10], orientation='N'),
            'ram_b': dict(cell='SRAM', location_um=[70, 70], orientation='N')})

    def write_def(self, extra=0, fillers=1):
        components = ['- ram_a SRAM + FIXED ( 10000 10000 ) N ;',
                      '- ram_b SRAM + FIXED ( 70000 70000 ) N ;',
                      '- tap TAP + PLACED ( 0 0 ) N ;']
        components += [f'- cell{i} STD + PLACED ( 0 0 ) N ;' for i in range(66 + extra)]
        components += [f'- fill{i} FILL + PLACED ( 0 0 ) N ;' for i in range(fillers)]
        path = self.root / 'design.def'
        path.write_text('DESIGN ibex_sram_bench ;\nUNITS DISTANCE MICRONS 1000 ;\n'
                        f'COMPONENTS {len(components)} ;\n' + '\n'.join(components) +
                        '\nEND COMPONENTS\nEND DESIGN\n')
        return path

    def analyze(self, path):
        return density.utilization(path, self.lef, dict(filler_cells=['FILL']), self.common, 70)

    def test_postroute_eco_growth_crosses_limit(self):
        before = self.analyze(self.write_def())
        after = self.analyze(self.write_def(extra=1))
        # 100*100 minus two 14*14 macro/halo reservations = 9608 um^2.
        self.assertEqual(before['area_dbu2']['available'], 9_608_000_000)
        self.assertEqual(before['area_dbu2']['final_nonfiller_standard_cells'], 6_700_000_000)
        self.assertEqual((before['status'], after['status']), ('passed', 'failed'))

    def test_gap_fill_does_not_hide_or_inflate_logic(self):
        normal = self.analyze(self.write_def())
        filled = self.analyze(self.write_def(fillers=20))
        self.assertEqual(normal['final_nonfiller_utilization_percent'],
                         filled['final_nonfiller_utilization_percent'])
        self.assertGreater(filled['area_dbu2']['reported_separately_filler_decap'],
                           normal['area_dbu2']['reported_separately_filler_decap'])

    def test_later_long_supply_net_is_not_a_component(self):
        path = self.write_def()
        with path.open('a') as stream:
            stream.write('NETS 1 ;\n- VDD ' + '( tap VDD ) ' * 10000 + ';\nEND NETS\n')
        self.assertEqual(self.analyze(path)['status'], 'passed')

    def test_missing_or_unfixed_macro_rejected(self):
        path = self.write_def()
        path.write_text(path.read_text().replace('ram_b SRAM + FIXED', 'ram_b SRAM + PLACED'))
        with self.assertRaisesRegex(ValueError, 'SRAM instance set'):
            self.analyze(path)

    def test_geometry_magnitude_and_halo_are_bounded(self):
        for value in ['1e100000', '1e10000000', '1e++']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                density.dbu(value, 1000)
        self.common['macro_halo_um'] = -1
        with self.assertRaisesRegex(ValueError, 'negative macro halo'):
            self.analyze(self.write_def())

    def test_unknown_master_and_duplicate_component_rejected(self):
        for old, new in [('cell0 STD', 'cell0 UNKNOWN'), ('cell1 STD', 'cell0 STD')]:
            with self.subTest(new=new):
                path = self.write_def()
                path.write_text(path.read_text().replace(old, new))
                with self.assertRaises(ValueError):
                    self.analyze(path)

    def test_qualification_enforces_the_final_policy(self):
        policy = self.root / 'policy.json'
        policy.write_text(json.dumps(dict(max_percent=70,
            evaluation_stage='after_final_signal_routing_and_all_cts_hold_drv_antenna_ecos')))
        contract = self.root / 'contract.json'
        contract.write_text(json.dumps(dict(common=dict(self.common, final_density_max_percent=70))))
        physical = self.root / 'physical.json'
        physical.write_text(json.dumps(dict(filler_cells=['FILL'])))
        files = {key: density.identity(path) for key, path in {
            'density_checker': Path(density.__file__), 'density_policy': policy,
            'contract': contract, 'physical': physical, 'cell_lef': self.lef}.items()}
        result = qualify_native.final_density(dict(files=files), self.write_def())
        self.assertEqual(result['status'], 'passed')
        with self.assertRaisesRegex(ValueError, 'FINAL_DENSITY_LIMIT'):
            qualify_native.final_density(dict(files=files), self.write_def(extra=1))
        files.pop('density_checker')
        with self.assertRaisesRegex(ValueError, 'paired final density'):
            qualify_native.final_density(dict(files=files), self.write_def())


if __name__ == '__main__':
    unittest.main()

"""R20 recipe import/verify: only bytes matching inputs_frozen are accepted."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'integration/ibex_sg13g2_ez130/campaigns/early-20261101/r20_recipe.py'
spec = importlib.util.spec_from_file_location('r20_recipe', TOOL)
r20 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r20)
RECIPE_DIR = ROOT / 'integration/ibex_sg13g2_ez130'
CONTRACT = RECIPE_DIR / 'contract-r20.json'


class R20Recipe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recipe = self.root / 'recipe'
        self.recipe.mkdir()
        self.pin = r20.pins()['contract']

    def host(self, data):
        source = self.root / 'host'
        path = source / Path(self.pin[2][0]).relative_to(r20.RECORDED_ROOT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return source

    def test_fresh_checkout_holds_every_pinned_recipe_file(self):
        result = r20.verify(r20.RECIPE_DIR)
        self.assertEqual(result['recipe'], {role: 'match' for role in r20.RECIPE})
        self.assertTrue(result['ok'])

    def test_committed_contract_is_the_pinned_tap_contract(self):
        self.assertEqual(r20.check('contract', CONTRACT, self.pin), 'match')
        self.assertEqual(self.pin[:2], ('96d227ad2aa23b9b41d853c836a3e9c6fbfe39ec07c02fc64f343418ddc64282', 5666))

    def test_import_accepts_only_pinned_bytes(self):
        data = CONTRACT.read_bytes()
        bad = self.host(data.replace(b'"native_tap_max_gap_um": 30', b'"native_tap_max_gap_um": 15'))
        report = r20.import_files(bad, self.recipe, ['contract'])
        self.assertTrue(report['contract'].startswith('not imported'))
        self.assertFalse((self.recipe / 'contract-r20.json').exists())
        good = self.host(data)
        self.assertTrue(r20.import_files(good, self.recipe, ['contract'])['contract'].startswith('imported'))
        self.assertEqual(r20.import_files(good, self.recipe, ['contract'])['contract'], 'already present')

    def test_import_never_overwrites_other_bytes(self):
        (self.recipe / 'contract-r20.json').write_bytes(b'{}')
        with self.assertRaises(SystemExit):
            r20.import_files(self.host(CONTRACT.read_bytes()), self.recipe, ['contract'])

    def test_manifest_requires_r20_physical_routing_prepare(self):
        shutil.copy(CONTRACT, self.recipe / 'contract-r20.json')
        run16 = ROOT / 'integration/ibex_sg13g2_ez130/native-physical.json'
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps(dict(files=dict(
            contract=r20.identity(self.recipe / 'contract-r20.json'), physical=r20.identity(run16)))))
        result = r20.verify(self.recipe, manifest)
        self.assertEqual(result['manifest'], dict(contract='match', physical='mismatch',
                                                  routing='absent', prepare='absent'))
        self.assertFalse(result['ok'])

    def test_overlay_selects_verified_files_only(self):
        shutil.copy(CONTRACT, self.recipe / 'contract-r20.json')
        config = r20.overlay(dict(files=dict(contract=dict(path='x', sha256='0', bytes=0))), self.recipe)
        self.assertEqual(config['files']['contract']['sha256'], self.pin[0])
        with self.assertRaises(SystemExit):
            r20.overlay(dict(files=dict(physical=dict(path='x', sha256='0', bytes=0))), self.recipe)


class PrepareCompatibility(unittest.TestCase):
    """The R20 and run16 recipes are not interchangeable, so they stay side by side."""

    def prepare(self, helper, physical, routing):
        """Run the helper's real `physical` stage; returns the output directory."""
        spec = importlib.util.spec_from_file_location('prepare_' + helper.stem, helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        out = root / 'frontend/physical'
        out.mkdir(parents=True)
        (root / 'roots.json').write_text(json.dumps(dict(clock_root='n1')))
        records = dict(physical=physical, routing=routing, floorplan=RECIPE_DIR / 'native-floorplan.json')
        context = dict(root=root, files={role: dict(path=str(path)) for role, path in records.items()})
        module.physical(context, SimpleNamespace(roots=str(root / 'roots.json')), out)
        return out

    def test_r20_prepare_accepts_the_r20_recipe_and_writes_the_routing_audit(self):
        out = self.prepare(RECIPE_DIR / 'native_frontend_inputs_r20.py',
                           RECIPE_DIR / 'native-physical-r20.json', RECIPE_DIR / 'native-routing-r20.json')
        self.assertEqual(json.loads((out / 'routing-writer-audit.json').read_text())['status'], 'passed')

    def test_r20_prepare_rejects_the_run16_physical(self):
        with self.assertRaisesRegex(ValueError, 'matched decap/tap template differs'):
            self.prepare(RECIPE_DIR / 'native_frontend_inputs_r20.py',
                         RECIPE_DIR / 'native-physical.json', RECIPE_DIR / 'native-routing.json')

    def test_run16_prepare_rejects_the_r20_physical(self):
        with self.assertRaisesRegex(ValueError, 'matched decap/tap template differs'):
            self.prepare(RECIPE_DIR / 'native_frontend_inputs.py',
                         RECIPE_DIR / 'native-physical-r20.json', RECIPE_DIR / 'native-routing-r20.json')


if __name__ == '__main__':
    unittest.main()

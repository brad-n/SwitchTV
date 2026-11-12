"""switchtv.py from the command line: catalog from each input, build staging (Xcode replaced by a stand-in), the options for the team, and install (devicectl replaced by a recorder)."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import switchtv  # noqa: E402
from switchtv_cli import build, catalog  # noqa: E402

FIXTURES = ROOT / 'tests/fixtures'
KART, WONDER = '0100152000022000', '010015100B514000'
TITLEDB = {'1': {'id': KART, 'name': 'Mario Kart 8 Deluxe', 'publisher': 'Nintendo', 'releaseDate': 20170428, 'category': ['Racing'], 'bannerUrl': 'https://img-eshop.cdn.nintendo.net/i/kart.jpg'},
           '2': {'id': WONDER, 'name': 'Wonder', 'publisher': 'Nintendo', 'releaseDate': 20231020, 'category': ['Platformer'], 'bannerUrl': 'https://img-eshop.cdn.nintendo.net/i/w.jpg'}}


def fake_scale(source, destination, longest):
    shutil.copy(source, destination)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = switchtv.main(list(argv))
        except SystemExit as stop:
            code = stop.code if isinstance(stop.code, int) else 1
            err.write(str(stop.code))
    return code, out.getvalue(), err.getvalue()


class CliCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache, self.out = self.root / 'cache', self.root / 'Catalog'
        self.cache.mkdir()
        (self.cache / 'US.en.json').write_text(json.dumps(TITLEDB))
        (self.cache / 'banners').mkdir()
        for title in (KART, WONDER):
            (self.cache / 'banners' / f'{title}.jpg').write_bytes(b'banner')
        self.patches = [patch.object(catalog, 'sips_scale', fake_scale), patch.dict(os.environ, {}, clear=False)]
        for p in self.patches:
            p.start()
        os.environ.pop('SWITCHTV_TEAM', None)
        os.environ.pop('SWITCHTV_BUNDLE_PREFIX', None)

    def tearDown(self):
        patch.stopall()
        self.temp.cleanup()

    def base(self, *extra):
        return [*extra, '--out', str(self.out), '--cache', str(self.cache), '--offline']


class CatalogCommandTests(CliCase):
    def test_games_file(self):
        code, stdout, _ = run_cli('catalog', '--games', str(FIXTURES / 'games.json'), *self.base('--json'))
        self.assertEqual(code, 0, stdout)
        summary = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(summary['games'], 2)
        self.assertEqual(len(summary['fingerprint']), 16)
        data = json.loads((self.out / 'catalog.json').read_text())
        self.assertEqual([g['titleID'] for g in data['games']], [WONDER, KART])
        self.assertEqual(data['games'][1]['name'], 'Mario Kart 8 Deluxe')
        self.assertTrue((self.out / 'images' / f'{KART}-card.jpg').is_file())   # the banner came from the cache, no network

    def test_apps_folder(self):
        import plistlib
        apps = self.root / 'apps'
        (apps / 'Kart.app').mkdir(parents=True)
        (apps / 'Kart.app/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'x.y', 'CFBundleName': 'Kart', 'CFBundleURLTypes': [{'CFBundleURLSchemes': [f'switchtv-{KART.lower()}']}]}))
        (apps / 'Wonder.app').mkdir()
        (apps / 'Wonder.app/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': f'dev.tvport.t.g{WONDER.lower()}', 'CFBundleName': 'Wonder'}))
        code, stdout, _ = run_cli('catalog', '--apps-dir', str(apps), *self.base())
        self.assertEqual(code, 0, stdout)
        self.assertIn('1 game(s) do not declare the switchtv-<title ID> URL scheme', stdout)   # Wonder is listed but cannot be opened
        self.assertEqual(len(json.loads((self.out / 'catalog.json').read_text())['games']), 2)

    def test_recorded_devicectl_output(self):
        code, stdout, _ = run_cli('catalog', '--tv-apps-json', str(FIXTURES / 'devicectl-apps.json'), *self.base())
        self.assertEqual(code, 0, stdout)
        self.assertEqual(sorted(g['name'] for g in json.loads((self.out / 'catalog.json').read_text())['games']), ['Mario Kart 8 Deluxe', 'Wonder'])

    def test_inputs_combine(self):
        code, _, _ = run_cli('catalog', '--games', str(FIXTURES / 'games.json'), '--tv-apps-json', str(FIXTURES / 'devicectl-apps.json'), *self.base())
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads((self.out / 'catalog.json').read_text())['games']), 2)   # the same two games, listed once

    def test_no_games_is_a_clear_error(self):
        empty = self.root / 'empty.json'
        empty.write_text('{"games": []}')
        code, _, err = run_cli('catalog', '--games', str(empty), *self.base())
        self.assertNotEqual(code, 0)
        self.assertIn('No games found', err)

    def test_a_bad_input_file_is_a_clear_error(self):
        code, _, err = run_cli('catalog', '--games', str(self.root / 'nope.json'), *self.base())
        self.assertEqual(code, 1)
        self.assertIn('could not be read', err)


class BuildCommandTests(CliCase):
    def fake_build(self, captured):
        def compile_app(work, derived, **kwargs):
            captured.update(work=work, derived=derived, **kwargs)
            captured['spec'] = (work / 'project.yml').read_text()
            captured['catalog_files'] = sorted(p.name for p in (work / 'Catalog').iterdir())
            app = derived / 'Build/Products/Release-appletvos/SwitchTV.app'
            app.mkdir(parents=True)
            return app

        def icons(root, work, log):
            (work / 'Generated/Assets.xcassets').mkdir(parents=True)
            return work / 'Generated/Assets.xcassets'

        return patch.object(build, 'compile_app', compile_app), patch.object(build, 'make_icons', icons), patch.object(build, 'stage_project', wraps=build.stage_project)

    def run_build(self, *argv, captured=None):
        captured = {} if captured is None else captured
        patches = self.fake_build(captured)
        with patches[0], patches[1]:
            return run_cli('build', '--games', str(FIXTURES / 'games.json'), *self.base(), *argv), captured

    def test_a_device_build_needs_a_team(self):
        (code, _, err), _ = self.run_build()
        self.assertNotEqual(code, 0)
        self.assertIn('--team', err)

    def test_team_and_prefix_fill_the_project(self):
        (code, stdout, _), got = self.run_build('--team', 'abcde12345'.upper(), '--bundle-prefix', 'com.example', '--json')
        self.assertEqual(code, 0, stdout)
        self.assertIn('PRODUCT_BUNDLE_IDENTIFIER: com.example.abcde12345.switchtv\n', got['spec'])
        self.assertIn('keychain-access-groups: [ABCDE12345.dev.tvport.switchtv]', got['spec'])
        self.assertIn(f'LSApplicationQueriesSchemes: [switchtv-{WONDER.lower()}, switchtv-{KART.lower()}]', got['spec'])
        self.assertIn('catalog.json', got['catalog_files'])
        self.assertFalse(got['simulator'])
        self.assertTrue(json.loads(stdout.strip().splitlines()[-1])['app'].endswith('SwitchTV.app'))

    def test_team_and_prefix_can_come_from_the_environment(self):
        with patch.dict(os.environ, {'SWITCHTV_TEAM': 'ZZZZZ99999', 'SWITCHTV_BUNDLE_PREFIX': 'org.me'}):
            (code, _, _), got = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn('PRODUCT_BUNDLE_IDENTIFIER: org.me.zzzzz99999.switchtv\n', got['spec'])

    def test_the_checkout_holds_no_team_id_of_anyones(self):
        for path in list(ROOT.glob('*.py')) + list((ROOT / 'switchtv_cli').glob('*.py')) + [ROOT / 'project.yml']:
            self.assertNotRegex(path.read_text(), r'L9492K5865', path.name)

    def test_simulator_build_needs_no_team_and_is_separate(self):
        (code, _, _), got = self.run_build('--simulator')
        self.assertEqual(code, 0)
        self.assertTrue(got['simulator'])
        self.assertIn(build.SIMULATOR_TEAM, got['spec'])
        self.assertEqual(got['derived'].name, 'derived-simulator')

    def test_build_without_games_uses_the_catalogue_already_made(self):
        run_cli('catalog', '--games', str(FIXTURES / 'games.json'), *self.base())
        captured = {}
        patches = self.fake_build(captured)
        with patches[0], patches[1]:
            code, _, _ = run_cli('build', *self.base('--simulator'))
        self.assertEqual(code, 0)
        self.assertEqual(len(captured['catalog_files']), 2)   # catalog.json and images

    def test_build_without_games_or_catalogue_says_so(self):
        code, _, err = run_cli('build', *self.base('--simulator'))
        self.assertNotEqual(code, 0)
        self.assertIn('No games given', err)

    def test_the_checkout_is_not_written_to(self):
        before = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if '.git' not in p.parts and '__pycache__' not in p.parts)
        self.run_build('--simulator')
        after = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if '.git' not in p.parts and '__pycache__' not in p.parts)
        self.assertEqual(before, after)


class InstallCommandTests(CliCase):
    def test_install_command_uses_devicectl(self):
        self.assertEqual(build.install_command(Path('/x/SwitchTV.app'), 'Living Room'), ['xcrun', 'devicectl', 'device', 'install', 'app', '--device', 'Living Room', '/x/SwitchTV.app'])

    def test_install_runs_devicectl_once_and_reports_its_failure(self):
        calls = []
        ok = lambda argv, **kw: calls.append(argv) or CompletedProcess(argv, 0, '', '')
        build.install_device(Path('/x/SwitchTV.app'), 'TV', run=ok)
        self.assertEqual(len(calls), 1)
        with self.assertRaisesRegex(build.BuildError, 'locked'):
            build.install_device(Path('/x/SwitchTV.app'), 'TV', run=lambda argv, **kw: CompletedProcess(argv, 1, '', 'the device is locked'))

    def test_cli_install_needs_a_built_app_and_a_device(self):
        code, _, err = run_cli('install', '--device', 'TV', '--cache', str(self.cache))
        self.assertNotEqual(code, 0)
        self.assertIn('No built app', err)
        app = self.cache / 'derived/Build/Products/Release-appletvos/SwitchTV.app'
        app.mkdir(parents=True)
        code, _, err = run_cli('install', '--cache', str(self.cache))
        self.assertIn('Say which Apple TV', err)

    def test_cli_install_passes_the_device_to_devicectl(self):
        app = self.cache / 'derived/Build/Products/Release-appletvos/SwitchTV.app'
        app.mkdir(parents=True)
        calls = []
        with patch.object(build, 'install_device', lambda a, d, **kw: calls.append((a, d))):
            code, _, _ = run_cli('install', '--device', 'Living Room', '--cache', str(self.cache))
        self.assertEqual((code, calls), (0, [(app, 'Living Room')]))


if __name__ == '__main__':
    unittest.main()

"""The three places the list of games comes from: a games file, a folder of built apps (.app and .ipa), and the apps on an Apple TV (devicectl).
The devicectl part runs against tests/fixtures/devicectl-apps.json, a hand-written file shaped like the output of `xcrun devicectl device info apps --json-output`; no Apple TV is asked."""
import json
import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from subprocess import CompletedProcess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from switchtv_cli import inputs  # noqa: E402

FIXTURES = ROOT / 'tests/fixtures'
KART, WONDER = '0100152000022000', '010015100B514000'


def info(bundle, name, schemes=(), **extra):
    plist = {'CFBundleIdentifier': bundle, 'CFBundleName': name, **extra}
    if schemes:
        plist['CFBundleURLTypes'] = [{'CFBundleURLName': bundle, 'CFBundleURLSchemes': list(schemes)}]
    return plist


class GamesFileTests(unittest.TestCase):
    def test_reads_the_fixture_and_skips_a_bad_entry(self):
        notes = []
        games = inputs.games_from_json(FIXTURES / 'games.json', notes.append)
        self.assertEqual([g['titleID'] for g in games], [KART, WONDER])
        self.assertEqual(games[0]['artwork'], str(FIXTURES / 'kart.jpg'))   # relative to the file
        self.assertEqual(games[0]['bundleID'], 'dev.tvport.abcde12345.g0100152000022000')
        self.assertEqual(games[1]['artworkSource'], 'https://img-eshop.cdn.nintendo.net/i/game.jpg')
        self.assertEqual(len(notes), 1)
        self.assertIn('Broken entry', notes[0])

    def test_a_bare_list_works_too(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'g.json'
            path.write_text(json.dumps([{'titleID': KART, 'name': 'Kart'}]))
            self.assertEqual(inputs.games_from_json(path), [{'titleID': KART, 'name': 'Kart'}])

    def test_unreadable_or_wrong_files_are_a_clear_error(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / 'bad.json'
            bad.write_text('{nope')
            with self.assertRaisesRegex(inputs.InputError, 'not valid JSON'):
                inputs.games_from_json(bad)
            bad.write_text('{"games": 3}')
            with self.assertRaisesRegex(inputs.InputError, 'must be a list'):
                inputs.games_from_json(bad)
            with self.assertRaisesRegex(inputs.InputError, 'could not be read'):
                inputs.games_from_json(Path(folder) / 'missing.json')


class AppIdentityTests(unittest.TestCase):
    def test_the_url_scheme_names_the_game_and_says_it_opens(self):
        game = inputs.game_from_info(info('any.bundle.id', 'Kart', [f'switchtv-{KART.lower()}']))
        self.assertEqual((game['titleID'], game['opens'], game['bundleID'], game['name']), (KART, True, 'any.bundle.id', 'Kart'))

    def test_tvport_bundle_ids_name_the_game_but_do_not_open(self):
        game = inputs.game_from_info(info('dev.tvport.abcde12345.g0100152000022000', 'Kart'))
        self.assertEqual((game['titleID'], game['opens']), (KART, False))

    def test_an_explicit_key_names_the_game(self):
        self.assertEqual(inputs.game_from_info(info('x.y', 'Wonder', SwitchTitleID=WONDER.lower()))['titleID'], WONDER)

    def test_other_schemes_and_ids_are_not_games(self):
        self.assertIsNone(inputs.game_from_info(info('com.example.app', 'App', ['myapp', 'switchtv-zzz', 'switchtv'])))
        self.assertIsNone(inputs.game_from_info(info('com.example.01234567890123456789', 'Long digits')))   # 20 digits: not a 16-digit ID

    def test_display_name_wins_over_bundle_name(self):
        self.assertEqual(inputs.game_from_info(info('dev.g0100152000022000', 'Short', CFBundleDisplayName='Long Name'))['name'], 'Long Name')


class AppsFolderTests(unittest.TestCase):
    def make_folder(self, root):
        kart = root / 'Kart.app'
        kart.mkdir()
        (kart / 'Info.plist').write_bytes(plistlib.dumps(info('dev.tvport.t.gKART', 'Kart', [f'switchtv-{KART.lower()}'])))
        nested = root / 'more/Wonder.app'
        nested.mkdir(parents=True)
        (nested / 'Info.plist').write_bytes(plistlib.dumps(info(f'dev.tvport.t.g{WONDER.lower()}', 'Wonder'), fmt=plistlib.FMT_BINARY))
        ipa = root / 'Packed.ipa'
        with zipfile.ZipFile(ipa, 'w') as package:
            package.writestr('Payload/Packed.app/Info.plist', plistlib.dumps(info('x', 'Packed', ['switchtv-0100000000000009'])))
            package.writestr('Payload/Packed.app/Frameworks/Inner.framework/Info.plist', plistlib.dumps(info('x.fw', 'Framework', ['switchtv-0100000000000010'])))
        other = root / 'Other.app'
        other.mkdir()
        (other / 'Info.plist').write_bytes(plistlib.dumps(info('com.example.other', 'Other')))
        (root / 'Broken.ipa').write_text('not a zip')

    def test_finds_apps_and_ipas_and_reports_what_it_skipped(self):
        notes = []
        with tempfile.TemporaryDirectory() as folder:
            self.make_folder(Path(folder))
            games = inputs.games_from_apps(Path(folder), notes.append)
        by_id = {g['titleID']: g for g in games}
        self.assertEqual(sorted(by_id), sorted([KART, WONDER, '0100000000000009']))
        self.assertTrue(by_id[KART]['opens'] and by_id['0100000000000009']['opens'] and not by_id[WONDER]['opens'])
        self.assertEqual(by_id['0100000000000009']['name'], 'Packed')   # the app's own Info.plist, not the framework's inside it
        self.assertTrue(any('Other.app' in n and 'no Switch title ID' in n for n in notes))
        self.assertTrue(any('Broken.ipa' in n for n in notes))

    def test_a_missing_folder_is_a_clear_error(self):
        with self.assertRaisesRegex(inputs.InputError, 'does not exist'):
            inputs.games_from_apps(Path('/nonexistent/switchtv-folder'))


class DevicectlTests(unittest.TestCase):
    def test_reads_the_recorded_output(self):
        games = inputs.games_from_devicectl_file(FIXTURES / 'devicectl-apps.json')
        self.assertEqual([(g['titleID'], g['name'], g['opens']) for g in games], [(KART, 'Mario Kart 8 Deluxe', None), (WONDER, 'Wonder', None)])
        self.assertEqual(games[0]['bundleID'], 'dev.tvport.abcde12345.g0100152000022000')

    def test_asks_devicectl_for_the_apps_with_a_json_file(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            Path(argv[argv.index('--json-output') + 1]).write_text((FIXTURES / 'devicectl-apps.json').read_text())
            return CompletedProcess(argv, 0, '', '')

        games = inputs.games_from_devicectl('Living Room', run=run)
        self.assertEqual(len(games), 2)
        self.assertEqual(calls[0][:6], ['xcrun', 'devicectl', 'device', 'info', 'apps', '--device'])
        self.assertEqual(calls[0][6], 'Living Room')

    def test_a_devicectl_failure_says_why(self):
        run = lambda argv, **kw: CompletedProcess(argv, 1, '', 'ERROR: device is not paired')
        with self.assertRaisesRegex(inputs.InputError, 'not paired'):
            inputs.games_from_devicectl('TV', run=run)

    def test_output_without_apps_is_an_error(self):
        with self.assertRaises(inputs.InputError):
            inputs.games_from_devicectl_output({'result': {}})


class MergeTests(unittest.TestCase):
    def test_one_game_per_title_id_first_input_wins_and_later_ones_fill_gaps(self):
        merged = inputs.merge([{'titleID': KART, 'name': 'Mine', 'opens': True}], [{'titleID': KART, 'name': 'Other', 'bundleID': 'b', 'opens': None}, {'titleID': WONDER, 'name': 'W'}])
        by_id = {g['titleID']: g for g in merged}
        self.assertEqual(by_id[KART], {'titleID': KART, 'name': 'Mine', 'opens': True, 'bundleID': 'b'})
        self.assertEqual(len(merged), 2)


if __name__ == '__main__':
    unittest.main()

"""The catalogue, made from a games list and a titledb snippet, with no network and no Xcode; the project spec; the fingerprint."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from switchtv_cli import build, catalog as sc  # noqa: E402

KART, WONDER = '0100152000022000', '010015100B514000'
GAME_BANNER = 'https://img-eshop.cdn.nintendo.net/i/game.jpg'
TITLEDB = {
    '1': {'id': KART, 'name': 'Mario Kart™ 8 Deluxe', 'publisher': 'Nintendo', 'releaseDate': 20170428, 'numberOfPlayers': 4, 'category': ['Racing', 'Multiplayer', 'Other'],
          'description': 'Race.', 'intro': 'Race anywhere', 'bannerUrl': 'https://img-eshop.cdn.nintendo.net/i/kart.jpg', 'screenshots': ['https://img-eshop.cdn.nintendo.net/i/s1.jpg', 'ftp://bad']},
    '2': {'id': WONDER, 'name': 'Wonder', 'publisher': 'Nintendo', 'releaseDate': 20231020, 'numberOfPlayers': 4, 'category': ['Platformer', 'Action'], 'bannerUrl': GAME_BANNER, 'screenshots': []},
    '3': {'id': WONDER, 'name': 'Wonder Switch 2 Edition', 'publisher': 'Nintendo', 'releaseDate': 20260326, 'bannerUrl': 'https://img-eshop.cdn.nintendo.net/i/s2.jpg'},
}


def fake_scale(source, destination, longest):
    shutil.copy(source, destination)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache, self.out, self.art = self.root / 'cache', self.root / 'out', self.root / 'art'
        self.art.mkdir()
        for title in (KART, WONDER):
            (self.art / f'{title}.jpg').write_bytes(b'art-' + title.encode())
        self.cache.mkdir()
        (self.cache / 'US.en.json').write_text(json.dumps(TITLEDB))
        self.fetches = []
        self.games = [
            {'name': 'Mario Kart 8 Deluxe', 'titleID': KART, 'bundleID': f'bundle.{KART}', 'artwork': str(self.art / f'{KART}.jpg')},
            {'name': 'Wonder', 'titleID': WONDER, 'bundleID': f'bundle.{WONDER}', 'artwork': str(self.art / f'{WONDER}.jpg'), 'artworkSource': GAME_BANNER},
        ]

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, url, destination):
        self.fetches.append(url)
        raise OSError('offline')

    def make(self, games=None):
        return sc.build_catalog(self.games if games is None else games, self.cache, self.out, fetch=self.fetch, scale=fake_scale)


class CatalogTests(Fixture):
    def test_games_are_sorted_by_title_id(self):
        self.assertEqual([g['titleID'] for g in self.make()['games']], [WONDER, KART])

    def test_fields_come_from_titledb_and_the_input(self):
        game = next(g for g in self.make()['games'] if g['titleID'] == KART)
        self.assertEqual((game['publisher'], game['releaseDate'], game['numberOfPlayers']), ('Nintendo', '2017-04-28', 4))
        self.assertEqual(game['genres'], ['Racing'])   # Multiplayer and Other are not genres
        self.assertEqual(game['scheme'], 'switchtv-0100152000022000')
        self.assertEqual(game['bundleID'], f'bundle.{KART}')
        self.assertEqual(game['screenshots'], ['https://img-eshop.cdn.nintendo.net/i/s1.jpg'])
        self.assertEqual(game['name'], 'Mario Kart 8 Deluxe')   # the input's name, not the shop's with its trademark sign

    def test_the_product_the_artwork_came_from_wins(self):
        game = next(g for g in self.make()['games'] if g['titleID'] == WONDER)
        self.assertEqual(game['releaseDate'], '2023-10-20')   # not the Switch 2 Edition's

    def test_images_are_written_beside_the_catalogue(self):
        catalog = self.make()
        game = catalog['games'][0]
        self.assertTrue((self.out / game['card']).is_file() and (self.out / game['banner']).is_file())
        self.assertEqual(json.loads((self.out / 'catalog.json').read_text()), catalog)

    def test_missing_fields_are_reported(self):
        report = sc.field_report(self.make())
        self.assertEqual(report['Mario Kart 8 Deluxe'], [])
        self.assertEqual(report['Wonder'], ['description', 'screenshots'])

    def test_a_title_database_that_cannot_be_fetched_still_lists_the_games(self):
        (self.cache / 'US.en.json').unlink()
        catalog = self.make()
        self.assertEqual(len(catalog['games']), 2)
        game = catalog['games'][0]
        self.assertIsNone(game['publisher'])
        self.assertTrue(game['banner'])   # the game's own artwork still gives the picture

    def test_a_game_without_artwork_gets_the_titledb_banner(self):
        bare = [{'name': 'Mario Kart 8 Deluxe', 'titleID': KART}]
        banners = []

        def fetch(url, destination):
            banners.append(url)
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(b'banner')

        catalog = sc.build_catalog(bare, self.cache, self.out, fetch=fetch, scale=fake_scale)
        self.assertEqual(banners, ['https://img-eshop.cdn.nintendo.net/i/kart.jpg'])
        self.assertTrue(catalog['games'][0]['card'])
        self.assertTrue((self.cache / 'banners' / f'{KART}.jpg').is_file())   # kept for next time

    def test_a_switch_2_game_asks_the_store_only_when_titledb_has_no_banner(self):
        asked = []
        store = lambda title_id: asked.append(title_id) or {'name': 'Store name', 'publisher': 'Pub', 'genres': ['Adventure'], 'wideUrl': 'https://assets.nintendo.com/x', 'description': 'd', 'releaseDate': 20250605}
        s2 = '01000000000AA000'
        catalog = sc.build_catalog([{'name': 'S2', 'titleID': s2}, self.games[0]], self.cache, self.out, fetch=self.fetch, scale=fake_scale, store=store)
        self.assertEqual(asked, [s2])
        self.assertEqual(next(g for g in catalog['games'] if g['titleID'] == s2)['publisher'], 'Pub')

    def test_a_complete_recent_title_database_is_not_fetched_again(self):
        self.make()
        self.assertEqual(self.fetches, [])

    def test_a_game_missing_from_an_old_database_fetches_it_again(self):
        old = {k: v for k, v in TITLEDB.items() if v['id'] != KART}
        (self.cache / 'US.en.json').write_text(json.dumps(old))
        os.utime(self.cache / 'US.en.json', (1, 1))
        self.make()
        self.assertEqual(self.fetches, [sc.TITLEDB_URL])


class ProjectSpecTests(unittest.TestCase):
    SPEC = (ROOT / 'project.yml').read_text()

    def test_team_values_fill_both_targets(self):
        spec = sc.render_spec(self.SPEC, 'L9492K5865', '77', ['switchtv-0100000000000001', 'switchtv-0100000000000002'])
        self.assertNotRegex(spec, r'\$\{SWITCHTV_\w+\}')
        self.assertEqual(spec.count('L9492K5865.dev.tvport.switchtv'), 4)   # two entitlements and two Info.plists
        self.assertIn('PRODUCT_BUNDLE_IDENTIFIER: dev.tvport.l9492k5865.switchtv.topshelf', spec)   # the extension's ID is the app's plus a suffix
        self.assertIn('LSApplicationQueriesSchemes: [switchtv-0100000000000001, switchtv-0100000000000002]', spec)

    def test_prefix_and_group_name_are_options(self):
        spec = sc.render_spec(self.SPEC, 'ABCDE12345', '1', [], prefix='com.example', group_name='com.example.games')
        self.assertIn('PRODUCT_BUNDLE_IDENTIFIER: com.example.abcde12345.switchtv\n', spec)
        self.assertEqual(spec.count('ABCDE12345.com.example.games'), 4)
        self.assertNotIn('dev.tvport', spec)

    def test_extension_entry_point_and_string_versions(self):
        spec = sc.render_spec(self.SPEC, 'L9492K5865', '77', [])
        # TVServices exports _TVExtensionMain as a bare "ret" (the tv-app-extension default entry): the extension process returned at once, exit status 3, and never ran ContentProvider
        self.assertIn('LD_ENTRY_POINT: _NSExtensionMain', spec)
        # a number where Info.plist wants a string crashes CFNetwork (its user agent string) on the first request
        self.assertEqual(spec.count('CFBundleVersion: "77"'), 2)
        self.assertEqual(spec.count('CFBundleShortVersionString: "1.0"'), 2)

    def test_group_name_is_the_one_game_builds_must_use(self):
        self.assertEqual(sc.group_id('L9492K5865'), 'L9492K5865.dev.tvport.switchtv')
        self.assertEqual(sc.KEYCHAIN_SERVICE, 'dev.tvport.switchtv')
        self.assertIn('static let service = "dev.tvport.switchtv"', (ROOT / 'Shared/SharedStore.swift').read_text())   # the Swift side agrees with the Python side


class FingerprintTests(Fixture):
    def test_same_games_same_fingerprint_whenever_it_is_made(self):
        self.make()
        first = sc.fingerprint(self.out, 'app-1')
        with patch('time.time', return_value=1e9):
            self.make()
        self.assertEqual(sc.fingerprint(self.out, 'app-1'), first)

    def test_changes_with_games_images_and_the_app(self):
        self.make()
        base = sc.fingerprint(self.out, 'app-1')
        self.assertNotEqual(sc.fingerprint(self.out, 'app-2'), base)
        (self.art / f'{KART}.jpg').write_bytes(b'new art')
        self.make()
        self.assertNotEqual(sc.fingerprint(self.out, 'app-1'), base)
        self.make(self.games[:1])
        self.assertNotEqual(sc.fingerprint(self.out, 'app-1'), base)

    def test_the_app_digest_counts_the_apps_files_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'Sources').mkdir()
            (root / 'Sources/A.swift').write_text('a')
            (root / 'project.yml').write_text('p')
            (root / 'README.md').write_text('docs')
            first = sc.sources_digest(root, build.APP_PARTS)
            (root / 'README.md').write_text('other docs')
            self.assertEqual(sc.sources_digest(root, build.APP_PARTS), first)
            (root / 'Sources/A.swift').write_text('b')
            self.assertNotEqual(sc.sources_digest(root, build.APP_PARTS), first)


if __name__ == '__main__':
    unittest.main()

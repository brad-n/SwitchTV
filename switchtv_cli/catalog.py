"""The SwitchTV catalogue: what the launcher app lists, made from a list of games (switchtv_cli/inputs.py), a cache of artwork, blawar/titledb's US list and the Nintendo store.

Pure functions where it can be (a games list and a titledb snippet are enough to test them); the network and `sips` are passed in, so a test needs neither.

A "game" here is a dict: titleID (16 hex digits) and name are required; bundleID (the game app's bundle ID), artwork (a local image file) and artworkSource (the shop banner URL the
artwork came from, so the matching product is picked) are optional.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import nintendo_store

TITLEDB_URL = 'https://raw.githubusercontent.com/blawar/titledb/master/US.en.json'
TITLEDB_MAX_AGE_S = 24 * 3600   # a list that lacks a wanted game is fetched again after this
NOT_GENRES = {'Other', 'Multiplayer'}   # titledb's `category` mixes genres with these two
CARD_PX, BANNER_PX = 800, 1920
SCHEME_PREFIX = 'switchtv-'
TITLE_ID = re.compile(r'^[0-9A-Fa-f]{16}$')

# The names SwitchTV and the games it opens agree on. A game build that wants SwitchTV to open it and to share "recently played" must use exactly these (README, "What a game build needs").
KEYCHAIN_SERVICE = 'dev.tvport.switchtv'   # the service of the keychain items that carry last-played times (Shared/SharedStore.swift)
GROUP_NAME = 'dev.tvport.switchtv'         # the keychain access group is <team ID>.<GROUP_NAME>


def scheme(title_id: str) -> str:
    """The URL scheme a game's Apple TV app answers."""
    return SCHEME_PREFIX + title_id.lower()


def group_id(team: str, group_name: str = GROUP_NAME) -> str:
    """The keychain access group SwitchTV, its Top Shelf extension and the game apps share."""
    return f'{team}.{group_name}'


def bundle_id(team: str, prefix: str = 'dev.tvport') -> str:
    return f'{prefix}.{team.lower()}.switchtv'


def render_spec(spec: str, team: str, build: str, schemes: list, *, prefix: str = 'dev.tvport', group_name: str = GROUP_NAME) -> str:
    """project.yml of the SwitchTV app with the signing team's values in: its bundle ID and keychain access group, the build number, and the URL schemes of the catalogue's games."""
    spec = spec.replace('LSApplicationQueriesSchemes: []', f'LSApplicationQueriesSchemes: [{", ".join(schemes)}]')
    for key, value in (('SWITCHTV_BUNDLE_ID', bundle_id(team, prefix)), ('SWITCHTV_GROUP', group_id(team, group_name)), ('SWITCHTV_TEAM', team), ('SWITCHTV_BUILD', build), ('SWITCHTV_PREFIX', prefix)):
        spec = spec.replace('${' + key + '}', value)
    return spec


def release_date(value) -> Optional[str]:
    text = str(value or '')
    return f'{text[:4]}-{text[4:6]}-{text[6:8]}' if re.fullmatch(r'\d{8}', text) else None


def pick_entry(titledb: dict, title_id: str, banner_url: str = '') -> dict:
    """The shop product that is the game itself. The list can hold several under one title ID (the game, its Switch 2 Edition, a bundle): the one whose banner the artwork came from
    (`artworkSource`) wins, then a non-demo product with a banner, then any."""
    candidates = [entry for entry in titledb.values() if isinstance(entry, dict) and (entry.get('id') or '').upper() == title_id.upper()]
    for entry in candidates:
        if banner_url and entry.get('bannerUrl') == banner_url:
            return entry
    return next((e for e in candidates if e.get('bannerUrl') and not e.get('isDemo')), candidates[0] if candidates else {})


def game_record(item: dict, entry: dict, bundle: str, card: str, banner: str) -> dict:
    """One game of catalog.json. Fields the list lacks are null / empty, and the app leaves them out."""
    title_id = item['titleID'].upper()
    players = entry.get('numberOfPlayers')
    return {
        'titleID': title_id,
        'name': item.get('name') or entry.get('name') or title_id,
        'publisher': entry.get('publisher') or None,
        'releaseDate': release_date(entry.get('releaseDate')),
        'numberOfPlayers': players if isinstance(players, int) else None,
        'genres': [g for g in entry.get('category') or [] if g not in NOT_GENRES],
        'description': entry.get('description') or None,
        'intro': entry.get('intro') or None,
        'bundleID': bundle,
        'scheme': scheme(title_id),
        'card': card,
        'banner': banner,
        'screenshots': [s for s in entry.get('screenshots') or [] if isinstance(s, str) and s.startswith('https://')],
    }


def sips_scale(source: Path, destination: Path, longest: int) -> None:
    """A JPEG no larger than `longest` pixels on its long side (the app bundles these, so they are kept small)."""
    result = subprocess.run(['sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', '80', '-Z', str(longest), str(source), '--out', str(destination)], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'sips failed')


def download(url: str, destination: Path, timeout: float = 60) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + '.part')
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'SwitchTV'}), timeout=timeout) as response, partial.open('wb') as out:
        shutil.copyfileobj(response, out)
    partial.replace(destination)


def load_titledb(cache: Path, wanted: set, fetch: Callable[[str, Path], None] = download, now: Callable[[], float] = time.time, log: Callable[[str], None] = lambda _: None) -> dict:
    """titledb's US list from the cache folder, fetched once (about 90 MB) and again only when a wanted game is missing from it and it is a day old. A failed fetch keeps the old list, or none:
    the games are then listed with their own name and artwork only."""
    path = cache / 'US.en.json'
    titledb: dict = {}
    if path.is_file():
        try:
            titledb = json.loads(path.read_text())
        except ValueError:
            titledb = {}
    have = {(e.get('id') or '').upper() for e in titledb.values() if isinstance(e, dict)}
    stale = not path.is_file() or (wanted - have and now() - path.stat().st_mtime > TITLEDB_MAX_AGE_S)
    if stale:
        try:
            log('Fetching the title database (about 90 MB, once)')
            cache.mkdir(parents=True, exist_ok=True)
            fetch(TITLEDB_URL, path)
            titledb = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            log(f'The title database could not be fetched ({error}); games are listed with what the input has')
    return titledb


def build_catalog(games: list, cache: Path, out: Path, *, fetch: Callable[[str, Path], None] = download, scale: Optional[Callable[[Path, Path, int], None]] = None,
                  now: Callable[[], float] = time.time, log: Callable[[str], None] = lambda _: None, store: Optional[Callable[[str], Optional[dict]]] = None) -> dict:
    """Write out/catalog.json and out/images/ for `games`; return the catalogue.
    store: title ID -> Nintendo store product (nintendo_store.load_product), asked only for a game titledb has no banner for (a Switch 2 game); None leaves the store out."""
    scale = scale or sips_scale
    ids = {g['titleID'].upper() for g in games}
    titledb = load_titledb(cache, ids, fetch, now, log)
    shutil.rmtree(out, ignore_errors=True)
    (out / 'images').mkdir(parents=True)
    records = []
    for item in sorted(games, key=lambda g: g['titleID'].upper()):
        title_id = item['titleID'].upper()
        entry = pick_entry(titledb, title_id, item.get('artworkSource') or '')
        if store and not entry.get('bannerUrl'):
            entry = nintendo_store.merge_entry(entry, store(title_id))
        art = Path(item['artwork']) if item.get('artwork') else None
        if not (art and art.is_file()) and entry.get('bannerUrl'):
            art = cache / 'banners' / f'{title_id}.jpg'
            try:
                if not art.is_file():
                    fetch(entry['bannerUrl'], art)
            except OSError as error:
                log(f'{title_id}: the banner could not be fetched ({error})')
        card = banner = ''
        if art and art.is_file():
            try:
                scale(art, out / 'images' / f'{title_id}-banner.jpg', BANNER_PX)
                scale(art, out / 'images' / f'{title_id}-card.jpg', CARD_PX)
                card, banner = f'images/{title_id}-card.jpg', f'images/{title_id}-banner.jpg'
            except (OSError, RuntimeError) as error:
                log(f'{title_id}: the artwork could not be read ({error})')
        records.append(game_record(item, entry, item.get('bundleID') or '', card, banner))
    catalog = {'generated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now())), 'games': records}
    (out / 'catalog.json').write_text(json.dumps(catalog, indent=1, ensure_ascii=False))
    return catalog


FIELDS = ('publisher', 'releaseDate', 'numberOfPlayers', 'genres', 'description', 'screenshots', 'banner')


def field_report(catalog: dict) -> dict:
    """Per game, the fields that came out empty."""
    return {g['name']: [f for f in FIELDS if not g.get(f)] for g in catalog['games']}


def fingerprint(out: Path, extra: str = '') -> str:
    """What an installed SwitchTV was made from: the catalogue (without its timestamp), its images, and `extra` (the app's sources and branding). Same fingerprint, same app."""
    catalog = json.loads((out / 'catalog.json').read_text())
    catalog.pop('generated', None)
    h = hashlib.sha256(json.dumps(catalog, sort_keys=True).encode())
    for path in sorted((out / 'images').glob('*')):
        h.update(path.name.encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    h.update(extra.encode())
    return h.hexdigest()[:16]


def sources_digest(root: Path, parts: tuple = ()) -> str:
    """A digest of the SwitchTV app's own files (sources, project, branding source), so a new release of the app is installed even when the catalogue did not change.
    `parts`: the names under root that count (all files when empty)."""
    h = hashlib.sha256()
    candidates = [root / part for part in parts] if parts else [root]
    files = [f for c in candidates for f in ([c] if c.is_file() else c.rglob('*') if c.is_dir() else []) if f.is_file() and 'Generated' not in f.parts and f.name != '.DS_Store']
    for path in sorted(files):
        h.update(str(path.relative_to(root)).encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()[:16]

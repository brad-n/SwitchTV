"""The SwitchTV catalogue: what the launcher app lists, made from a list of games (switchtv_cli/inputs.py), a cache of artwork, blawar/titledb's US list and the Nintendo store.

Pure functions where it can be (a games list and a titledb snippet are enough to test them); the network and `sips` are passed in, so a test needs neither.

A "game" here is a dict: titleID (16 hex digits) and name are required; bundleID (the game app's bundle ID), artwork (a local image file), artworkSource (the shop banner URL the
artwork came from, so the matching product is picked) and settings (the rows of the game app's own settings list, inputs.py) are optional.
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
OPTIONS_SERVICE = 'dev.tvport.switchtv.options'   # the service of the keychain items that carry a game's options, one per game (Shared/GameOptions.swift)
GROUP_NAME = 'dev.tvport.switchtv'         # the keychain access group is <team ID>.<GROUP_NAME>

# A game's Options page (Sources/OptionsView.swift): the rows of the game app's own settings list (a TVPort app's dm/launcher.json, made from TVPort's dm/games/settings.tsv; its docs/frontends.md describes
# them), less those an Apple TV has no use for, each with the group the page shows it under and a line of help. The game applies the choices when it starts (README, "Game options").
NOT_ON_TV = {'DM_TOUCH_HAPTICS', 'DM_PICTURE_FIT', 'DM_SCREEN_ASPECT', 'DM_FULLSCREEN', 'DM_TV_OUTPUT', 'DM_SW2'}   # a touch screen; a 16:9 picture on a 16:9 TV; the Mac's window, 4K output and controllers
GROUPS = ('Picture', 'Performance', 'Game options', 'Controls', 'Language')   # in this order; a section SwitchTV does not know comes after them, under its own name
GROUP_OF = {**dict.fromkeys(('DM_OPMODE', 'DM_RENDER_SCALE', 'DM_RES_SCALE', 'DM_DYNAMIC_RESOLUTION', 'DM_ANISO', 'DM_PRESENT_UPSCALE', 'DM_UPSCALER', 'DM_MOVIE_NETWORK'), 'Picture'),
            **dict.fromkeys(('DM_FPS_TARGET', 'DM_FPS_HOLD', 'DM_PRESENT_SNAP', 'DM_PSO_MTL4', 'DM_PSO_PREBUILD', 'DM_NVN_ENC_THREAD', 'DM_SHOW_FPS', 'DM_METAL_HUD'), 'Performance'),
            'DM_LANG': 'Language'}
SECTION_GROUP = {'Game options': 'Game options', 'Performance': 'Performance', 'Controls': 'Controls', 'Game': 'Picture'}   # a row GROUP_OF does not name goes by its section
# A line of help for the rows whose list leaves the note empty (the list writes one only for a setting with a cost)
HELP = {
    'DM_FPS_TARGET': 'The frame rate the game aims for. 30 leaves each frame twice the time; the game runs at its real speed either way.',
    'DM_OPMODE': 'Docked draws the game at its TV size; Handheld at its smaller handheld size, which is lighter on the GPU.',
    'DM_RENDER_SCALE': 'The size of the game\'s 3D picture. Lower is lighter on the GPU but softer; text and the HUD stay sharp.',
    'DM_LANG': 'The language of the game\'s text and voices.',
    'DM_SHOW_FPS': 'Shows the frames per second at the top left while you play.',
    'DM_RUMBLE': 'The game\'s vibration on your controller.',
    'DM_RUMBLE_GAIN': 'How strong the game\'s vibration is.',
    'DM_RUMBLE_MIN': 'Vibration weaker than this stays off. Raise it if a controller hums all the time.',
    'DM_PAD_PRESS_TO_JOIN': 'A controller becomes a player only when you press one of its buttons, so the players choose their order.',
    'DM_NVN_ENC_THREAD': 'Prepares the GPU\'s work on a thread of its own, so the game can go on sooner. Leave it on unless a game misbehaves.',
    'DM_FPS_HOLD': 'When frames come late, hold an even 30 fps for a while instead of a mix of 30 and 60.',
    'DM_METAL_HUD': 'Apple\'s performance overlay over the game: frame rate, frame and GPU times, memory.',
}
SETTING_FIELDS = ('env', 'kind', 'label', 'default', 'options', 'min', 'max', 'step', 'on', 'off', 'needs', 'base')


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


def well_formed(row: dict) -> bool:
    """A row the Options page can draw: a choice with [value, label] options, a range with its numbers, or a toggle; its default one of its values."""
    if not (isinstance(row.get('label'), str) and isinstance(row.get('default'), str)):
        return False
    kind = row.get('kind')
    if kind == 'choice':
        options = row.get('options')
        return isinstance(options, list) and bool(options) and all(isinstance(o, list) and len(o) == 2 and all(isinstance(x, str) for x in o) for o in options)
    if kind == 'range':
        numbers = [row.get(k) for k in ('min', 'max', 'step')]
        return all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in numbers) and numbers[0] < numbers[1] and numbers[2] > 0
    return kind == 'toggle' and all(isinstance(row.get(k, ''), str) for k in ('on', 'off'))


def tv_settings(rows, build_env: Optional[dict] = None) -> list:
    """The rows of a game's Options page, from its settings list: those an Apple TV can use, grouped (GROUPS first, in their order; rows keep their order within a group), each with its help line.
    build_env: what the app's dm/env.txt sets (TVPort's build choices). Without a choice the game starts with those values, so a row it sets takes the build's value as its default (`setByBuild`):
    the page shows it as the default and Reset goes back to it; a choice made on the page wins over it (the game applies SwitchTV's choices before its dm/env.txt)."""
    out, first = [], {}
    for row in rows or []:
        if not isinstance(row, dict) or row.get('env') in NOT_ON_TV or not well_formed(row):
            continue
        setting = {key: row[key] for key in SETTING_FIELDS if key in row}
        setting['group'] = GROUP_OF.get(row['env']) or SECTION_GROUP.get(row.get('section')) or str(row.get('section') or 'Game')
        setting['help'] = row.get('note') or HELP.get(row['env'], '')
        if build_env and isinstance(build_env.get(row['env']), str):
            setting['default'], setting['setByBuild'] = build_env[row['env']], True
        first.setdefault(setting['group'], len(first))
        out.append(setting)
    rank = {group: GROUPS.index(group) if group in GROUPS else len(GROUPS) + first[group] for group in first}
    return sorted(out, key=lambda s: rank[s['group']])


def accepts(row: dict, value: str) -> bool:
    """Whether a row of the Options page takes `value` (the same rules as GameSetting.accepts in Shared/Catalog.swift)."""
    kind = row.get('kind')
    if kind == 'choice':
        return any(o[0] == value for o in row['options'])
    if kind == 'toggle':
        return value in (row.get('on', '1'), row.get('off', '0'))
    try:
        return 'x' not in value.lower() and row['min'] - 1e-9 <= float(value) <= row['max'] + 1e-9
    except ValueError:
        return False


def tv_presets(presets, rows: list) -> list:
    """A game's presets for its Options page: each preset's values for the rows the page has (a setting the TV leaves out, or a value its row does not take, is dropped). A preset left with no
    value is dropped too, except Balanced, which may name nothing (it is then the defaults). The presets come from the settings list (TVPort's dm/games/presets.tsv); [] when it has none."""
    by_env = {row['env']: row for row in rows}
    out = []
    for preset in presets or []:
        values = {env: v for env, v in preset['values'].items() if env in by_env and accepts(by_env[env], v)}
        if values or preset['name'] == 'Balanced':
            out.append({'name': preset['name'], 'values': values})
    return out if any(p['values'] for p in out) else []


def game_record(item: dict, entry: dict, bundle: str, card: str, banner: str) -> dict:
    """One game of catalog.json. Fields the list lacks are null / empty, and the app leaves them out."""
    title_id = item['titleID'].upper()
    players = entry.get('numberOfPlayers')
    settings = tv_settings(item.get('settings'), item.get('buildEnv'))
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
        'settings': settings,
        'presets': tv_presets(item.get('presets'), settings),
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

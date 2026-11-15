"""Where the list of games comes from. Three inputs, all giving the same shape (a list of dicts: titleID, name, and when known bundleID, artwork, artworkSource, opens, settings):

  games_from_json(path)         a games file: {"games": [{"titleID": "0100152000022000", "name": "Mario Kart 8 Deluxe", ...}]} (or the bare list). See README, "The games file".
  games_from_apps(folder)       a folder of built game apps (*.app) and packages (*.ipa), read through their Info.plist.
  games_from_devicectl(...)     the apps installed on an Apple TV, from `xcrun devicectl device info apps`.

A game is told apart by its 16-digit Switch title ID. An app carries it as its URL scheme switchtv-<title ID> (what SwitchTV opens it by), or failing that inside its bundle ID
(TVPort's are dev.tvport.<team>.g<title ID>), or in an Info.plist key SwitchTitleID. An app without any of these is not a game SwitchTV can name and is skipped (with a note).
`opens` says whether the app declares the switchtv-<title ID> URL scheme (True/False), or is None when that cannot be known (devicectl does not report URL schemes).
`settings` is the game app's own settings list, the rows of its Options page (catalog.tv_settings): a TVPort app carries it as dm/launcher.json (TVPort's dm/games/settings.py writes it; docs/frontends.md
there describes the rows), and a games file names that file (or gives the rows). devicectl cannot read an app's files, so games found only on the Apple TV have none.
`buildEnv` is what the app's own dm/env.txt sets (KEY: value; TVPort writes the build's choices there, Console mode among them): the game takes those over a choice made in SwitchTV, so its Options
page shows them as set by the build.
"""
from __future__ import annotations

import json
import plistlib
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

SCHEME_PREFIX = 'switchtv-'
TITLE_ID = re.compile(r'^[0-9A-Fa-f]{16}$')
ID_IN_TEXT = re.compile(r"(?<![0-9A-Fa-f])(01[0-9A-Fa-f]{14})(?![0-9A-Fa-f])")
TITLE_ID_KEY = 'SwitchTitleID'
SETTINGS_FILES = ('dm/launcher.json', 'dm/p0/launcher.json')   # in a game app: its settings list; an app of several programs (one game's launcher and the games it starts) has one per program, the first's is used
ENV_NAME = re.compile(r'^DM_[A-Z0-9_]+$')
BUILD_ENV = 'dm/env.txt'   # in a game app: the switches its build set, read at boot before the saved choices (TVPort's dm/ios/Sources/main.m)


def env_file(text: str) -> dict:
    """An env.txt as the game reads it (main.m): KEY=VALUE lines, # starts a comment, the first line of a key wins."""
    found: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        key, equals, value = line.partition('=')
        if line and not line.startswith('#') and equals and key:
            found.setdefault(key, value)
    return found


def build_env_beside(settings: Path) -> Optional[dict]:
    """The app's dm/env.txt next to its settings list (dm/launcher.json, or dm/p<N>/launcher.json one folder down); None when there is none."""
    folder = settings.parent
    for candidate in (folder / 'env.txt', folder.parent / 'env.txt' if re.fullmatch(r'p\d+', folder.name) else None):
        if candidate and candidate.is_file():
            try:
                return env_file(candidate.read_text(errors='replace'))
            except OSError:
                return None
    return None


class InputError(Exception):
    pass


def settings_rows(data) -> Optional[list]:
    """The rows of a settings list (launcher.json: {"settings": [...]}, or the bare list), each a dict naming a DM_ variable; None when `data` is no such list."""
    rows = data.get('settings') if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get('env'), str) and ENV_NAME.match(row['env'])]


def read_settings_file(path: Path, log: Callable[[str], None] = lambda _: None) -> Optional[list]:
    try:
        rows = settings_rows(json.loads(Path(path).read_text()))
    except (OSError, ValueError) as error:
        log(f'The settings list {path} could not be read ({error}); the game is listed without options')
        return None
    if rows is None:
        log(f'The settings list {path} has no "settings" list; the game is listed without options')
    return rows


def normalize(item: dict, base: Optional[Path] = None, log: Callable[[str], None] = lambda _: None) -> Optional[dict]:
    """One game of a games file: validated, with the artwork path made absolute and its settings list read. None when it has no usable title ID."""
    if not isinstance(item, dict):
        return None
    title_id = str(item.get('titleID') or '')
    if not TITLE_ID.match(title_id):
        return None
    game = {'titleID': title_id.upper(), 'name': str(item.get('name') or '').strip()}
    for key in ('bundleID', 'artworkSource'):
        if isinstance(item.get(key), str) and item[key]:
            game[key] = item[key]
    if isinstance(item.get('artwork'), str) and item['artwork']:
        art = Path(item['artwork']).expanduser()
        game['artwork'] = str(art if art.is_absolute() or base is None else base / art)
    if isinstance(item.get('opens'), bool):
        game['opens'] = item['opens']
    settings = item.get('settings')
    if isinstance(settings, str) and settings:   # a launcher.json file, and the app's env.txt beside it
        path = Path(settings).expanduser()
        path = path if path.is_absolute() or base is None else base / path
        rows = read_settings_file(path, log)
        env = build_env_beside(path) if rows is not None else None
        if env is not None:
            game['buildEnv'] = env
    else:
        rows = settings_rows(settings) if isinstance(settings, (list, dict)) else None
    if rows is not None:
        game['settings'] = rows
    return game


def games_from_json(path: Path, log: Callable[[str], None] = lambda _: None) -> list:
    try:
        data = json.loads(Path(path).read_text())
    except OSError as error:
        raise InputError(f'The games file {path} could not be read: {error}') from error
    except ValueError as error:
        raise InputError(f'The games file {path} is not valid JSON: {error}') from error
    items = data.get('games') if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise InputError(f'The games file {path} must be a list of games, or {{"games": [...]}}')
    games = []
    for item in items:
        game = normalize(item, Path(path).resolve().parent, log)
        if game:
            games.append(game)
        else:
            log(f'Skipped an entry of {Path(path).name} without a valid titleID: {json.dumps(item)[:80]}')
    return games


def app_identity(info: dict) -> tuple:
    """(title ID or None, declares the SwitchTV scheme)."""
    for url_type in info.get('CFBundleURLTypes') or []:
        for scheme in (url_type.get('CFBundleURLSchemes') or []) if isinstance(url_type, dict) else []:
            if isinstance(scheme, str) and scheme.lower().startswith(SCHEME_PREFIX) and TITLE_ID.match(scheme[len(SCHEME_PREFIX):]):
                return scheme[len(SCHEME_PREFIX):].upper(), True
    explicit = info.get(TITLE_ID_KEY)
    if isinstance(explicit, str) and TITLE_ID.match(explicit):
        return explicit.upper(), False
    found = ID_IN_TEXT.search(str(info.get('CFBundleIdentifier') or ''))
    if found:
        return found.group(1).upper(), False
    return None, False


def game_from_info(info: dict) -> Optional[dict]:
    title_id, opens = app_identity(info)
    if not title_id:
        return None
    name = info.get('CFBundleDisplayName') or info.get('CFBundleName') or ''
    game = {'titleID': title_id, 'name': str(name).strip(), 'opens': opens}
    if info.get('CFBundleIdentifier'):
        game['bundleID'] = str(info['CFBundleIdentifier'])
    return game


def find_apps(folder: Path) -> list:
    """The .app folders and .ipa files under `folder` (not looking inside an app)."""
    found = []
    stack = [Path(folder)]
    while stack:
        here = stack.pop()
        try:
            children = sorted(here.iterdir())
        except OSError:
            continue
        for child in children:
            if child.suffix == '.app' and child.is_dir():
                found.append(child)
            elif child.suffix.lower() == '.ipa' and child.is_file():
                found.append(child)
            elif child.is_dir() and not child.name.startswith('.'):
                stack.append(child)
    return found


def read_info(app: Path) -> dict:
    """The Info.plist of an .app folder or of the app inside an .ipa (Payload/<name>.app/Info.plist)."""
    if app.is_dir():
        return plistlib.loads((app / 'Info.plist').read_bytes())
    with zipfile.ZipFile(app) as package:
        for name in package.namelist():
            parts = name.split('/')
            if len(parts) == 3 and parts[0] == 'Payload' and parts[1].endswith('.app') and parts[2] == 'Info.plist':
                return plistlib.loads(package.read(name))
    raise InputError('no Payload/*.app/Info.plist')


def read_app_file(app: Path, name: str) -> Optional[bytes]:
    """A file inside an .app folder, or inside the app of an .ipa (Payload/<name>.app/<name>); None when it has none."""
    try:
        if app.is_dir():
            return (app / name).read_bytes() if (app / name).is_file() else None
        with zipfile.ZipFile(app) as package:
            found = next((n for n in package.namelist() if n.startswith('Payload/') and n.count('/') == name.count('/') + 2 and n.split('/')[1].endswith('.app') and n.split('/', 2)[2] == name), None)
            return package.read(found) if found else None
    except (OSError, zipfile.BadZipFile):
        return None


def read_app_settings(app: Path) -> tuple:
    """(the settings list inside an .app or .ipa (SETTINGS_FILES), what its dm/env.txt sets); each None when it has none or it cannot be read."""
    rows = None
    for name in SETTINGS_FILES:
        data = read_app_file(app, name)
        if data is not None:
            try:
                rows = settings_rows(json.loads(data))
            except ValueError:
                rows = None
            break
    env = read_app_file(app, BUILD_ENV) if rows is not None else None
    return rows, env_file(env.decode('utf-8', 'replace')) if env is not None else None


def games_from_apps(folder: Path, log: Callable[[str], None] = lambda _: None) -> list:
    folder = Path(folder)
    if not folder.is_dir():
        raise InputError(f'The folder {folder} does not exist')
    games = []
    for app in find_apps(folder):
        try:
            game = game_from_info(read_info(app))
        except (OSError, ValueError, zipfile.BadZipFile, InputError, plistlib.InvalidFileException) as error:
            log(f'{app.name}: not read ({error})')
            continue
        if game:
            settings, env = read_app_settings(app)
            if settings is not None:
                game['settings'] = settings
            if env is not None:
                game['buildEnv'] = env
            games.append(game)
        else:
            log(f'{app.name}: no Switch title ID in its URL schemes or bundle ID, so it is not listed')
    return games


def games_from_devicectl_output(data: dict, log: Callable[[str], None] = lambda _: None) -> list:
    """The games among the apps `devicectl device info apps --json-output` reports. The report has no URL schemes, so `opens` is None."""
    apps = ((data.get('result') or {}).get('apps')) if isinstance(data, dict) else None
    if not isinstance(apps, list):
        raise InputError('The devicectl output has no result.apps list')
    games = []
    for app in apps:
        if not isinstance(app, dict) or str(app.get('bundleIdentifier') or '').startswith('com.apple.'):
            continue
        title_id, _ = app_identity({'CFBundleIdentifier': app.get('bundleIdentifier'), 'CFBundleURLTypes': []})
        if not title_id:
            continue
        games.append({'titleID': title_id, 'name': str(app.get('name') or '').strip(), 'bundleID': app['bundleIdentifier'], 'opens': None})
    return games


def games_from_devicectl_file(path: Path, log: Callable[[str], None] = lambda _: None) -> list:
    try:
        return games_from_devicectl_output(json.loads(Path(path).read_text()), log)
    except (OSError, ValueError) as error:
        raise InputError(f'The devicectl output {path} could not be read: {error}') from error


def games_from_devicectl(device: str, log: Callable[[str], None] = lambda _: None, run: Callable = subprocess.run) -> list:
    """Asks the Apple TV (a device name, identifier or address devicectl knows) which apps it has. Needs Xcode 15+ and the TV paired."""
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / 'apps.json'
        result = run(['xcrun', 'devicectl', 'device', 'info', 'apps', '--device', device, '--json-output', str(out)], text=True, capture_output=True)
        if result.returncode:
            raise InputError(f'devicectl could not list the apps on {device}: {(result.stderr or result.stdout).strip()[-400:]}')
        return games_from_devicectl_file(out, log)


def merge(*lists: list) -> list:
    """Games from several inputs as one list, one per title ID: the first input to name a field wins, later ones fill what is missing."""
    merged: dict = {}
    for games in lists:
        for game in games:
            have = merged.setdefault(game['titleID'], {})
            for key, value in game.items():
                if key not in have or (have[key] in (None, '') and value not in (None, '')):
                    have[key] = value
    return [merged[key] for key in sorted(merged)]

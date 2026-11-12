"""Where the list of games comes from. Three inputs, all giving the same shape (a list of dicts: titleID, name, and when known bundleID, artwork, artworkSource, opens):

  games_from_json(path)         a games file: {"games": [{"titleID": "0100152000022000", "name": "Mario Kart 8 Deluxe", ...}]} (or the bare list). See README, "The games file".
  games_from_apps(folder)       a folder of built game apps (*.app) and packages (*.ipa), read through their Info.plist.
  games_from_devicectl(...)     the apps installed on an Apple TV, from `xcrun devicectl device info apps`.

A game is told apart by its 16-digit Switch title ID. An app carries it as its URL scheme switchtv-<title ID> (what SwitchTV opens it by), or failing that inside its bundle ID
(TVPort's are dev.tvport.<team>.g<title ID>), or in an Info.plist key SwitchTitleID. An app without any of these is not a game SwitchTV can name and is skipped (with a note).
`opens` says whether the app declares the switchtv-<title ID> URL scheme (True/False), or is None when that cannot be known (devicectl does not report URL schemes).
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


class InputError(Exception):
    pass


def normalize(item: dict, base: Optional[Path] = None) -> Optional[dict]:
    """One game of a games file: validated and with the artwork path made absolute. None when it has no usable title ID."""
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
        game = normalize(item, Path(path).resolve().parent)
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

#!/usr/bin/env python3
"""SwitchTV: an Apple TV launcher for the Switch games you built. This is its build tool (Python 3 standard library, plus Xcode and xcodegen).

  switchtv.py catalog   make Catalog/catalog.json and its images from your games
  switchtv.py build     catalog + project + icons + xcodegen + xcodebuild, for an Apple TV or the tvOS Simulator
  switchtv.py install   put a built SwitchTV on an Apple TV (devicectl) or in the Simulator (simctl)

Where the games come from (catalog and build; give any of these, together if you like):
  --games FILE          a games file (README, "The games file")
  --apps-dir FOLDER     a folder of built game .app / .ipa files, read through their Info.plist
  --tv-apps DEVICE      the apps installed on that Apple TV (xcrun devicectl device info apps)
  --tv-apps-json FILE   the saved output of `xcrun devicectl device info apps --json-output FILE`

Signing (build for a device): --team (or SWITCHTV_TEAM), the 10-character Apple team ID; --bundle-prefix (or SWITCHTV_BUNDLE_PREFIX, default dev.tvport).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from switchtv_cli import build, catalog, inputs, nintendo_store

DEFAULT_PREFIX = 'dev.tvport'


def default_cache() -> Path:
    return Path(os.environ.get('SWITCHTV_CACHE') or Path.home() / 'Library/Caches/SwitchTV')


def log(line: str) -> None:
    print(line, flush=True)


def add_inputs(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group('games')
    group.add_argument('--games', action='append', default=[], type=Path, metavar='FILE', help='a games file (JSON)')
    group.add_argument('--apps-dir', action='append', default=[], type=Path, metavar='FOLDER', help='a folder of built game .app / .ipa files')
    group.add_argument('--tv-apps', metavar='DEVICE', help='read the apps installed on this Apple TV with devicectl')
    group.add_argument('--tv-apps-json', action='append', default=[], type=Path, metavar='FILE', help='the saved JSON output of devicectl device info apps')
    parser.add_argument('--out', type=Path, default=Path('Catalog'), help='the catalogue folder (default ./Catalog)')
    parser.add_argument('--cache', type=Path, default=default_cache(), help='where the title database, banners and store answers are kept (default $SWITCHTV_CACHE or ~/Library/Caches/SwitchTV)')
    parser.add_argument('--offline', action='store_true', help='fetch nothing: use only what the cache and the games already have')
    parser.add_argument('--json', action='store_true', help='print a JSON summary as the last line')


def has_inputs(args) -> bool:
    return bool(args.games or args.apps_dir or args.tv_apps or args.tv_apps_json)


def read_games(args) -> list:
    found = [inputs.games_from_json(p, log) for p in args.games]
    found += [inputs.games_from_apps(p, log) for p in args.apps_dir]
    found += [inputs.games_from_devicectl_file(p, log) for p in args.tv_apps_json]
    if args.tv_apps:
        found.append(inputs.games_from_devicectl(args.tv_apps, log))
    games = inputs.merge(*found)
    unopenable = [g['name'] or g['titleID'] for g in games if g.get('opens') is False]
    if unopenable:
        log(f'{len(unopenable)} game(s) do not declare the switchtv-<title ID> URL scheme and can be listed but not opened: {", ".join(unopenable[:6])}{"..." if len(unopenable) > 6 else ""}')
    return games


def make_catalog(args) -> dict:
    games = read_games(args)
    if not games:
        raise SystemExit('No games found in the inputs given.')
    fetch = (lambda url, path: (_ for _ in ()).throw(OSError('offline'))) if args.offline else catalog.download
    store = None if args.offline else (lambda title_id: nintendo_store.load_product(args.cache, title_id, log=log))
    log('Making the SwitchTV catalogue')
    made = catalog.build_catalog(games, args.cache, args.out, fetch=fetch, log=log, store=store)
    log(f'SwitchTV lists {len(made["games"])} games.')
    for name, missing in catalog.field_report(made).items():
        log(f'{name}: {"no " + ", ".join(missing) if missing else "all fields found"}')
    return made


def summary(args, made: dict, **extra) -> dict:
    return {'switchtv': True, 'games': len(made['games']), 'fingerprint': catalog.fingerprint(args.out, build.app_digest()), 'catalog': str(args.out.resolve()), **extra}


def cmd_catalog(args) -> int:
    made = make_catalog(args)
    if args.json:
        print(json.dumps(summary(args, made)), flush=True)
    return 0


def cmd_build(args) -> int:
    simulator = args.simulator
    if not simulator and not (args.team or os.environ.get('SWITCHTV_TEAM')):
        raise SystemExit('Building for an Apple TV needs your Apple team ID: --team XXXXXXXXXX or SWITCHTV_TEAM. (Add --simulator to build for the tvOS Simulator without signing.)')
    team = build.SIMULATOR_TEAM if simulator else (args.team or os.environ['SWITCHTV_TEAM']).strip()
    if has_inputs(args):
        made = make_catalog(args)
    else:
        try:
            made = json.loads((args.out / 'catalog.json').read_text())
        except (OSError, ValueError):
            raise SystemExit(f'No games given and no catalogue at {args.out}. Pass --games, --apps-dir, --tv-apps or --tv-apps-json, or run `catalog` first.')
    prefix = args.bundle_prefix or os.environ.get('SWITCHTV_BUNDLE_PREFIX') or DEFAULT_PREFIX
    args.cache.mkdir(parents=True, exist_ok=True)
    work = build.stage_project(build.ROOT, args.cache / ('project-simulator' if simulator else 'project'), args.out, [g['scheme'] for g in made['games']], team,
                               prefix=prefix, group_name=args.group_name, log=log)
    log('Building SwitchTV')
    app = build.compile_app(work, args.cache / ('derived-simulator' if simulator else 'derived'), device=args.device, simulator=simulator, log=log)
    log(f'Built {app}')
    if args.json:
        print(json.dumps(summary(args, made, app=str(app))), flush=True)
    return 0


def cmd_install(args) -> int:
    app = args.app
    if not app:
        sub = 'derived-simulator/Build/Products/Release-appletvsimulator' if args.simulator else 'derived/Build/Products/Release-appletvos'
        app = args.cache / sub / 'SwitchTV.app'
    if not app.is_dir():
        raise SystemExit(f'No built app at {app}. Run `build` first (or pass --app).')
    if args.simulator:
        build.install_simulator(app, args.simulator_id)
        log(f'Installed SwitchTV in the Simulator ({args.simulator_id}).')
    else:
        if not args.device:
            raise SystemExit('Say which Apple TV: --device NAME_OR_ID (see `xcrun devicectl list devices`).')
        log('Installing SwitchTV on the Apple TV')
        build.install_device(app, args.device)
        log('SwitchTV installed.')
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = p.add_subparsers(dest='command', required=True)
    c = commands.add_parser('catalog', help='make the catalogue and its images')
    add_inputs(c)
    c.set_defaults(run=cmd_catalog)
    b = commands.add_parser('build', help='build the app for an Apple TV or the Simulator')
    add_inputs(b)
    b.add_argument('--simulator', action='store_true', help='build for the tvOS Simulator (unsigned; no team needed)')
    b.add_argument('--device', help='build for this Apple TV only (identifier); default: any Apple TV')
    b.add_argument('--team', help='your Apple team ID (or SWITCHTV_TEAM)')
    b.add_argument('--bundle-prefix', help=f'bundle ID prefix; the app is <prefix>.<team>.switchtv (or SWITCHTV_BUNDLE_PREFIX, default {DEFAULT_PREFIX})')
    b.add_argument('--group-name', default=catalog.GROUP_NAME, help=f'keychain access group name after the team ID; game builds must use the same (default {catalog.GROUP_NAME})')
    b.set_defaults(run=cmd_build)
    i = commands.add_parser('install', help='install the built app on an Apple TV (devicectl) or in the Simulator (simctl)')
    i.add_argument('--device', help='the Apple TV: name, identifier or address')
    i.add_argument('--app', type=Path, help='the .app to install (default: the one `build` made)')
    i.add_argument('--cache', type=Path, default=default_cache())
    i.add_argument('--simulator', action='store_true', help='install in the tvOS Simulator instead')
    i.add_argument('--simulator-id', default='booted', help='which Simulator (default: the booted one)')
    i.set_defaults(run=cmd_install)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.run(args)
    except (inputs.InputError, build.BuildError) as error:
        print(f'SwitchTV: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

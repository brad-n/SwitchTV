"""Building and installing the SwitchTV app: the project is copied into the cache folder, given the catalogue, the icons and the signing team's values, then xcodegen and xcodebuild run on the copy.
The checkout itself is never written to."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from . import catalog as cat

ROOT = Path(__file__).resolve().parent.parent
APP_PARTS = ('Sources', 'Shared', 'TopShelf', 'Branding', 'project.yml')   # what the app is made of; also what the fingerprint covers
TOOL_PATHS = ['/opt/homebrew/bin', '/usr/local/bin']
SIMULATOR_TEAM = 'SIMULATOR00'   # stands in for a team ID in a Simulator build, which is not signed
REASON = re.compile(r' error: |\[RED\]|FAIL: ')


class BuildError(Exception):
    pass


def tool_env() -> dict:
    return {**os.environ, 'PATH': os.pathsep.join(TOOL_PATHS + [os.environ.get('PATH', '')])}


def app_digest(root: Path = ROOT) -> str:
    """The fingerprint part for the app's own files, so a new release of SwitchTV is installed even when the catalogue is the same."""
    return cat.sources_digest(root, APP_PARTS)


def make_icons(root: Path, work: Path, log: Callable[[str], None]) -> Path:
    """The App Icon and Top Shelf images from the one logo, Branding/switchtv-icon-source.png: Branding/make_branding.swift cuts the mark (icon) and centres the whole logo (Top Shelf)
    on its own white, tools/tv_icon.swift lays them out as the layered tvOS assets with a flat backdrop of that white."""
    source = root / 'Branding/switchtv-icon-source.png'
    brand = work / 'branding'
    shutil.rmtree(brand, ignore_errors=True)
    result = subprocess.run(['xcrun', 'swift', str(root / 'Branding/make_branding.swift'), str(source), str(brand)], text=True, capture_output=True)
    if result.returncode:
        raise BuildError(f'The SwitchTV logo could not be read: {result.stderr.strip()[-400:]}')
    colors = dict(line.split() for line in (brand / 'colors.txt').read_text().splitlines())
    assets = work / 'Generated/Assets.xcassets'
    shutil.rmtree(assets, ignore_errors=True)
    assets.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['xcrun', 'swift', str(root / 'tools/tv_icon.swift'), str(brand / 'icon-art.png'), str(assets)], text=True, capture_output=True,
                            env={**os.environ, 'DM_ICON_BACKDROP': colors['paper'], 'DM_TOP_SHELF_ART': str(brand / 'shelf-art.png')})
    if result.returncode:
        raise BuildError(f'The SwitchTV icon could not be made: {result.stderr.strip()[-400:]}')
    return assets


def stage_project(root: Path, work: Path, catalog_dir: Path, schemes: list, team: str, *, prefix: str, group_name: str, build: Optional[str] = None,
                  log: Callable[[str], None] = lambda _: None, icons: Callable = make_icons) -> Path:
    """work/ = the app's files + Catalog/ + generated icons + project.yml with the team's values filled in. Returns work."""
    if not (root / 'project.yml').is_file():
        raise BuildError(f'{root} has no project.yml: is this a SwitchTV checkout?')
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for name in APP_PARTS:
        if name == 'Branding':
            continue   # the logo is only read for the icons
        source = root / name
        if source.is_dir():
            shutil.copytree(source, work / name, ignore=shutil.ignore_patterns('.DS_Store', 'Generated', 'Catalog'))
        elif source.is_file():
            shutil.copy2(source, work / name)
    shutil.copytree(catalog_dir, work / 'Catalog')
    icons(root, work, log)
    spec = cat.render_spec((work / 'project.yml').read_text(), team, build or str(int(time.time())), schemes, prefix=prefix, group_name=group_name)
    (work / 'project.yml').write_text(spec)
    return work


def stream(argv: list, log: Callable[[str], None], keep: Callable[[str], bool], cwd: Optional[Path] = None) -> None:
    """Runs a command, logging the lines `keep` accepts; on failure raises with the lines that say why (or the last few)."""
    process = subprocess.Popen([str(a) for a in argv], env=tool_env(), cwd=cwd, text=True, errors='replace', stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail: list = []
    reasons: list = []
    for raw in process.stdout:
        line = raw.rstrip('\n')
        tail = (tail + [line])[-25:]
        if REASON.search(line):
            reasons = (reasons + [line.strip()])[-12:]
        if keep(line):
            log(line[:400])
    if process.wait():
        raise BuildError('\n'.join(reasons or tail[-6:])[-1500:])


def compile_app(work: Path, derived: Path, *, device: Optional[str] = None, simulator: bool = False, log: Callable[[str], None] = lambda _: None) -> Path:
    """xcodegen, then xcodebuild. device: an Apple TV's identifier (builds for exactly it) or None for any Apple TV; simulator: the tvOS Simulator, unsigned. Returns the .app."""
    result = subprocess.run(['xcodegen', 'generate', '-q', '--spec', str(work / 'project.yml'), '--project', str(work)], text=True, capture_output=True, env=tool_env())
    if result.returncode:
        raise BuildError(f'xcodegen failed: {(result.stderr or result.stdout).strip()[-500:]}')
    command = ['xcodebuild', '-project', work / 'SwitchTV.xcodeproj', '-scheme', 'SwitchTV', '-configuration', 'Release', '-derivedDataPath', derived]
    if simulator:
        command += ['-destination', 'generic/platform=tvOS Simulator', 'CODE_SIGNING_ALLOWED=NO', 'CODE_SIGNING_REQUIRED=NO', 'build']
        products = 'Release-appletvsimulator'
    else:
        command += ['-destination', f'platform=tvOS,id={device}' if device else 'generic/platform=tvOS', '-allowProvisioningUpdates', 'build']
        products = 'Release-appletvos'
    stream(command, log, lambda x: bool(REASON.search(x)) or x.startswith(('CodeSign', '** ')))
    app = derived / 'Build/Products' / products / 'SwitchTV.app'
    if not app.is_dir():
        raise BuildError('SwitchTV was built but its app is missing')
    return app


def install_command(app: Path, device: str) -> list:
    return ['xcrun', 'devicectl', 'device', 'install', 'app', '--device', device, str(app)]


def install_device(app: Path, device: str, run: Callable = subprocess.run) -> None:
    """Installs the built app on an Apple TV (name, identifier or address devicectl knows)."""
    result = run(install_command(app, device), text=True, capture_output=True)
    if result.returncode:
        raise BuildError((result.stderr.strip() or result.stdout.strip())[-900:] or 'The install failed')


def install_simulator(app: Path, target: str = 'booted', run: Callable = subprocess.run) -> None:
    result = run(['xcrun', 'simctl', 'install', target, str(app)], text=True, capture_output=True)
    if result.returncode:
        raise BuildError((result.stderr.strip() or result.stdout.strip())[-900:] or 'The Simulator install failed')

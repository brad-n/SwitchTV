# Changelog: SwitchTV

What changes in each release, in plain terms. Releases are tagged `v<version>` and published under [Releases](https://github.com/brad-n/SwitchTV/releases).

## Unreleased

**Game options**
- An Options button beside Play on a game's details page: the game's settings (picture, performance, the game's own options such as Ocarina of Time's grass, ground mist, reflections, model detail and sun shadows, controls, language), changed with the remote. Each choice is kept at once and the game takes it the next time it starts; Reset to Defaults forgets them.
- Settings the game's build fixes show with a lock: the game takes the build's value.
- The settings come from the game app's own list (`dm/launcher.json`), read from built apps or named in the games file. Games built by the TVPort fork pick the choices up; the README describes the format for other builds.

## 0.1.0 (2026-11-13)

The first release as a project of its own. SwitchTV was written inside [brad-n/TVPort](https://github.com/brad-n/TVPort) and moved here so it can be built without TVPort.

**The app**
- An Apple TV launcher for the Switch games you built: a banner that follows the game you're on, Recently Played, All Games, rows by genre, and a details page for each game.
- Select plays the game; hold Select (or press Play/Pause) for its details. A game that's no longer installed says to build it again.
- A Top Shelf with your recently played games and all your games.
- Recently played is shared with the game apps through a keychain group, for games built with TVPort.

**Building it without TVPort** (`switchtv.py`)
- `catalog` makes the game list from a games file, a folder of built game apps (`--apps-dir`), or the apps already installed on an Apple TV (`--tv-apps`). Box art and details come from the public title list, and from Nintendo's store for Switch 2 games.
- `build` builds it for an Apple TV (`--team`) or the tvOS Simulator (`--simulator`); `install` puts it on the Apple TV.
- A game opens from SwitchTV when its app registers the link `switchtv-<title ID>`. Games built by TVPort do; the README lists what other build tools need to add.

**TVPort**
- TVPort includes SwitchTV as a submodule and keeps it up to date after every Build & Install, as before.

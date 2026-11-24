# nx2mac

Build Nintendo Switch and Switch 2 games into native macOS `.app` bundles, by driving the
[nxnative](https://github.com/seedds/nxnative) build script against a fresh engine checkout from
GitHub (upstream `seedds/nxnative` or your own fork), pinned to a reported branch + commit.

- **CLI** (`nx2mac`): prerequisites, setup, engine pinning, builds — every command has a
  machine-readable `--json` form.
- **GUI** (`nx2mac.app`): a small SwiftUI wrapper around the CLI with a Build and a Setup tab.

Agent-oriented documentation (dependency checklist, engine policy, JSON contract) lives in
[AGENT.md](AGENT.md).

## Install

```bash
# the app: self-contained, carries its own frozen CLI (Contents/Resources/cli)
./gui/build.sh release ~/Applications
open ~/Applications/nx2mac.app

# optional: the CLI alone, for terminal use
uv tool install .
```

The GUI does **not** need a separately installed CLI — `gui/build.sh` freezes the Python CLI
with PyInstaller into the bundle (≈31 MB) and the app prefers that copy. Installing the CLI is
only useful if you also want `nx2mac …` in a terminal. From a checkout, `uv run nx2mac …` works
without installing anything.

## Quick start

```bash
nx2mac doctor                  # what this Mac is missing, with a fix per item
nx2mac setup                   # asks "Install zstd? (brew install zstd) [y/N]" per missing item
nx2mac engine --source fork fork-customizations   # pin an engine ref (saved to config)
nx2mac build "/path/to/Game [010016...]" \
  --keys ~/switch/prod.keys \
  --out-dir ~/Downloads/MyGame
```

`build` accepts a dump folder, a single package file, or a `.nup`, plus any number of
`--update` / `--dlc` packages picked from anywhere (they are staged into one folder for the
engine). Defaults: the output folder is the game's own directory, and `prod.keys` is looked for
next to/inside the game (explicit `--keys` always wins and is never copied).

## CLI

```
nx2mac version                          # version (also --json)
nx2mac doctor                           # per-check status + fix; exit 1 if anything fails
nx2mac setup [--install NAME…|--all]    # install missing pieces (prompts, or non-interactive)
nx2mac engine [REF] [--source upstream|fork] [--show]      # the two supported engines
nx2mac probe PATH                      # what a folder holds + which package is the game
nx2mac library [--set DIR|--clear]     # the game library (no default; must exist)
nx2mac player [--create NAME|--select NAME|--delete NAME] [--game GAME]
nx2mac build GAME [-k KEYS] [-o APP] [--out-dir DIR]
                [--update F…] [--dlc F…] [--source ID] [--ref REF] [--json]
```

`--json` prints one JSON document for `version|doctor|setup|engine`, and NDJSON events for
`build` / `setup --install` (`engine`, `log`, `result`, `install-start`, `install-done`, `error`).
Exit codes: `0` success, non-zero failure; `build` propagates the engine's code.

## GUI (macOS)

```bash
./gui/build.sh release ~/Applications   # swift build + .app bundle + ad-hoc signature
open ~/Applications/nx2mac.app
```

- **Setup tab** (opens first) — every prerequisite with its state. Fixable items get an **Install**
  button (runs `nx2mac setup --install <name>` and streams the output); items we must not automate
  (installing Xcode, updating macOS, Homebrew itself) show a command to copy and a link.
- **Build tab** — pick the **engine** (source + ref, Save & Pin) first, then `prod.keys` (optional), a **Game dir** (the package it holds is picked for
  you and shown; use **Game → Choose…** to build a different file) and/or the **Game** file itself,
  an update and any number of DLC packages (auto-sized vertical list, five rows then scrolls),
  and an **output folder** (defaults to the game library, so builds are filed into
`<library>/<Game>/`; pick a folder to override).
  The engine's progress streams into the log; when it finishes you can reveal or open the app.

The app reads its icon from `gui/Resources/AppIcon.icns`, regenerated with
`swift gui/tools/make_icon.swift gui/Resources/AppIcon.icns`. Settings shows the app and CLI
versions side by side, and where the CLI came from. Tests: `swift test --package-path gui`
(add `NX2MAC_LIVE_TESTS=1` to drive the real CLI).

`build.sh` resolves the CLI in this order: the path set in Settings → `NX2MAC_BIN` → the copy
frozen into the bundle → `nx2mac` on `PATH` → a development checkout's `.venv`.

The app runs the CLI with a PATH that includes `/opt/homebrew/bin`, `/opt/homebrew/sbin`,
`/usr/local/bin`, `/usr/local/sbin` and `~/.local/bin` ahead of whatever the app inherited: a
Finder-launched app only gets `/usr/bin:/bin:/usr/sbin:/sbin`, which would otherwise hide Homebrew
and make the Setup tab report `brew`/`zstd`/`glslangValidator` as missing. While working on
the SwiftUI side you can skip the (≈7 s) freeze step with `NX2MAC_SKIP_CLI_BUNDLE=1 ./gui/build.sh`.

## One folder per game

`--out-dir DIR` (GUI: *Output folder*, defaulting to the game's own directory) keeps a build
self-contained:

```bash
nx2mac build "/path/to/Game [0100...]" --out-dir ~/Develop/MyGame \
  --update update.nsp --dlc "DLC folder" --keys ~/switch/prod.keys
```

| path | what |
| --- | --- |
| `DIR/<Game>.app` | the finished app (the engine names it) |
| `DIR/<game>_build/` | the engine's build folder — extracted files + shader caches, reused on rebuild |

### The game library (CLI + GUI)

`nx2mac library --set DIR` (GUI: *Setup → Game library*) picks a folder that already exists —
there is no default and nx2mac never creates it. A folder holding game packages is refused: that
is a dump, and builds must not be scattered into it. Choosing a game folder in the Build tab never
changes this setting. Building with the library set files each game into
its own folder, and everything the game writes stays there:

```
<library>/<Game>/
├── <Game>.app            what you double-click
├── logs/                 the engine's run log        (DM_LOG_DIR)
├── saves/                the default save store      (DM_SAVE)
├── players/<name>/saves/ one store per player — each is user 1, so each keeps their own name
├── cheats/               <build id>.txt cheat files   (DM_CHEATS)
├── screenshots/          the in-game album            (DM_ALBUM_DIR)
├── mods/                 loose mod files
├── support/              the engine's own state: env.txt, temp/, its user registry
├── <game>_build/         extracted files + shader caches, reused on rebuild
└── nx2mac.json           what was built, from which engine commit, when
```

Nothing is written to `~/Library/Application Support/nxnative` or `~/Library/Logs/nxnative`
anymore: `build.sh` hands the engine an `--env-file` baked into the app (`support/build-env.txt`)
that sets `DM_SUPPORT_DIR`/`DM_LOG_DIR`/`DM_SAVE`/`DM_CHEATS`/`DM_ALBUM_DIR` to the game's folder
(engine patch `patches/0001-launcher-state-dirs.patch`). `nx2mac library --json` lists what is in
the library — only *built* games (a game folder with a manifest or an `.app`, plus `.app`s sitting
directly in the library root), so pointing the library at a folder that also holds your dumps, keys
and tool checkouts shows no phantom entries; the GUI's **Games** tab lists only built games, one row each: the app's icon, its
name and version, with Play (records "recently played"), a Players menu and a Data menu for
saves/logs/mods/cheats/screenshots.

### Players (library-wide, like NX2Apple)

Players are one list for the whole library, so the picker sits at the top of the **Games** tab and
applies to every game — each game keeps a save store per player:

```bash
nx2mac player --create "Ada"          # add a player with your preferred name (max 8)
nx2mac player --select Ada            # choose who plays (updates every built game's env)
nx2mac player                         # list them; * marks the active one
```

`<library>/players.json` holds the list; each player's store is
`<library>/save/<TITLEID>/profiles/<player>`, and selecting a player rewrites every game's
`support/<TITLEID>/env.txt` (`DM_SAVE`, `DM_NICK`). Each player gets their own save store and their
own `DM_NICK`, which is how every player keeps a custom name (the engine hard-codes user 2..8, and only reads `DM_NICK` for user 1).

## Engine sources

There are exactly two: upstream `seedds/nxnative` and the fork `bf39l/nxnative`. A free-form
clone URL is intentionally not offered — `patches/` is written against these repositories, so an
arbitrary engine could not be built. Checkouts live in the cache as
`<owner>-<repo>_<ref>` (e.g. `bf39l-nxnative_fork-customizations`).

## Engine patches

`patches/NNNN-*.patch` are applied to the pinned engine checkout after every reset (git-format,
filename order), so local fixes for the engine or anything vendored in it survive reboots of the
cache. Keep them minimal and drop them once upstream lands the fix — see
[patches/README.md](patches/README.md).

## Continuous integration and releases

| event | what runs |
| --- | --- |
| pull request | lint + tests for the CLI and the GUI, plus a full app assembly (nothing is published) |
| push to `main` | the same, and the release build (CLI zip, app zip, checksums) is uploaded as a workflow artifact |
| tag `vX.Y.Z` | the release build is published as a GitHub Release for that tag |

The release itself is driven by the tag: pushing `vX.Y.Z` publishes exactly that version — there is
no manual version input and no release from a branch push.

```bash
git tag -a v0.0.1 -m "nx2mac v0.0.1" && git push origin v0.0.1
```

| asset | what |
| --- | --- |
| `nx2mac-cli-<version>-macos-arm64.zip` | standalone CLI (PyInstaller onedir) |
| `nx2mac-app-<version>-macos-arm64.zip` | `nx2mac.app` with that CLI bundled inside |
| `SHA256SUMS.txt` | checksums for both |

A tag can never publish something other than it says: the job fails unless the tag,
`VERSION` (`src/nx2mac/__init__.py`) and `CFBundleShortVersionString` (`gui/Info.plist`) all agree —
bump those two files, commit, then tag.

## Development

```bash
uv sync                     # Python deps (CLI)
uv run pytest               # 106 tests
uv run ruff check src/ tests/
swift test --package-path gui   # 27 tests
```

Layout: `src/nx2mac/` (CLI: `cli`, `doctor`, `remedies`, `installer`, `engines`, `builder`,
`staging`, `pyenv`, `config`, `runner`, `emit`), `gui/` (SwiftUI app + icon tool), `packaging/`
(PyInstaller entry point for the bundled CLI), `patches/` (engine fixes).

## License

MIT — see [LICENSE](LICENSE). nx2mac is an independent launcher that drives the
[nxnative](https://github.com/seedds/nxnative) engine; the MIT copyright lines of nxnative and
NX2Apple are kept alongside ours because this project builds on their work.

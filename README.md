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
nx2mac library [--add DIR|--remove DIR|--clear]   # the game libraries (none by default)
nx2mac mods                            # list each game's mods and apply them
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
  one **Update** file (the newest beside the game is pre-selected — badge *newest* — and listed under
  the row like the game package: name, role, version, size; choose or clear to change it) and any number of DLC packages (auto-sized vertical list, five rows then scrolls),
  and an **output folder** (defaults to the game library, so builds are filed into
`<library>/nx2mac_game_lib/<TID>/`; pick a folder to override).
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
| `<library>/build/<TID>_build/` | the engine's build folder — extracted files + shader caches, reused on rebuild |

### The game library (CLI + GUI)

`nx2mac library --add DIR` (**Games** tab → *Libraries → Add…*; the list shows every folder with a
remove button, and Refresh reloads all of them) adds a folder that already
exists — there is no default, nx2mac never creates it, and **more than one library is fine** (every
library's games appear in the tab, `--remove DIR` forgets one). A folder holding game packages is refused: that
is a dump, and builds must not be scattered into it. Choosing a game folder in the Build tab never
changes this setting. Building with the library set files each game into
its own folder, and everything the game writes stays there:

```
<library>/nx2mac_game_lib/<TID>/
├── <Game>.app            what you double-click
├── logs/                 the engine's run log        (DM_LOG_DIR)
├── saves/                the default save store      (DM_SAVE)
├── players/<name>/saves/ one store per player — each is user 1, so each keeps their own name
├── mods/<TID>/           the game's mods (each mod's romfs overlay)
│   └── cheats/           DM_CHEATS — cheat files live in the mods tree
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

### Where a game's data lives

Everything one game saves sits under its title ID:

```
<library>/save/<TITLEID>/
├── save/                  DM_SAVE — the base store (`save.users`, `save.work` land here too)
├── profiles/<player>/     one store per player
└── pso_user.tsv           DM_PSO_USER — the game's shared pipeline cache
```

### Installing mods and cheats

Both live in the game's folder inside the library — `<chosen>/nx2mac_game_lib/<TITLEID>/mods/`.
Nothing else needs a rebuild: the game reads them at launch.

**Mods (RomFS overlays)**

```
nx2mac_game_lib/<TITLEID>/mods/
└── Cool Mod/
    └── romfs/            the mod's files, replacing the game's own (e.g. romfs/system/...)
```

```bash
nx2mac mods                # list them; `on` = the game will load it
```

`nx2mac mods` (and any `build`/`player` change) writes the mod list into the game's
`support/env.txt` as `DM_ROMFS_OVERLAY`, which the engine overlays over the game's RomFS. Accepted
shapes per mod: `romfs/`, `atmosphere/contents/<TITLEID>/romfs/` or `contents/<TITLEID>/romfs/`.
Pressing "Refresh" in the GUI re-applies them too.

- Mods are loaded in folder-name order, so an earlier name wins a file both replace.
- **RomFS mods only.** A mod that ships code (`exefs/`) cannot be loaded: the engine would have to
  rewrite its plugins first (`scripts/prepare_mod.py` in the engine). Such a mod shows up in
  `nx2mac mods` as `no romfs` and is ignored.
- A mod that changes the *save* layout (a "cheat-like" mod) is not recommended: saves are per
  player under `save/` or `players/<id>/save/`.

**Cheats (Atmosphère format)**

```
nx2mac_game_lib/<TITLEID>/mods/cheats/
└── <build id>.txt          e.g. 0100646009FBE000's build id, 16 hex digits
```

- The file name must be the game's **build ID** — cheat sites publish it as
  `atmosphere/contents/<TITLEID>/cheats/<BUILDID>.txt`; copy it in as-is. Any `.txt` whose name is
  not 20 characters ending in `.txt` is ignored.
- In game, open the window's **Cheats** menu (or panel) and switch entries on; the engine remembers
  the toggles per build in `cheats/<build id>.enabled`.
- From nx2mac: `nx2mac cheats --game "Dead Cells"` lists them, `--enable NAME` / `--disable NAME`
  switches one (same file the game writes), and the GUI's **Games** tab has a cheats button per game
  (⌘-clickable list with switches).
- `DM_CHEATS` already points there (it is written into the app's baked env at build time), and
  `cheats/` sits beside the mods it belongs to.
- Cheats run once per presented frame, so an "infinite health" style cheat can still let you die for
  a frame; that is the engine's semantics, documented in its `docs/reference/cheats.md`.

**If the game crashes or quits after switching cheats on**

That is the cheat code writing into the game, not nx2mac (the engine logs `the host exited with 139`
and a `cheat_tick_thread` backtrace). Two safety nets, both exposed by nx2mac:

```bash
nx2mac cheats --game "Dead Cells"              # shows "master switch off" / "safe mode" banners
nx2mac cheats --game "Dead Cells" --off        # master switch: no cheat runs at all
nx2mac cheats --game "Dead Cells" --on         # remove it again
nx2mac cheats --game "Dead Cells" --restore-safe
```

After a run that ends uncleanly with cheats on, the engine writes
`.cheats-running-<title id>` in the cheats folder and starts the **next** launch with every cheat
off (**safe mode**); `--restore-safe` drops that marker so the saved switches apply again. Turning
cheats on one at a time tells you which one the game cannot survive — cheats that write to code the
rewriter moved are refused (log: `code writes into them are refused`) and simply do nothing.

**A cheat file arms its masters as soon as it sits in the folder.** `{Name}` entries are always
on (the engine's `docs/frontends.md` "Cheats" says so, and they never appear in `.enabled`), so a
file in `mods/cheats/` changes the game even with every switch off — that is not something nx2mac
adds, it is the contract every front end (NX2Apple included) works with. Hence the master switch:
while `.cheats-off-<title id>` exists, no cheat runs at all — that is the safe state to play in.

Observed with Dead Cells' `6E35EE0A24029143.txt`: its *key-press* cheats (`08010000 0813AA10 …`,
e.g. `激活修改`, `無限跳`) write into the game's rewritten code and make the host exit with
`139`. A plain value cheat (`0X…` writes to data, e.g. gold, items) is the safer kind to try first.

### Cloud saves (optional)

Everything nx2mac writes lives in `<chosen folder>/nx2mac_game_lib/` — no dot-files in your folder:

```
<chosen>/nx2mac_game_lib/
├── <Game>.app, <Game>.nx2mac.json
├── players.json
└── <TITLEID>/{save,players,mods,logs,screenshots,support,build}
```

Nothing else is written there.

### Cleaning up

```bash
nx2mac clean              # drop the extracted files + shader caches of every game
nx2mac clean --game "Dead Cells"           # one game
nx2mac clean --manifests                   # also the <Game>.nx2mac.json sidecars
```

It only ever removes nx2mac's own data — `nx2mac_game_lib/<TITLEID>/build` and `temp` folders,
`--manifests` adds the sidecar, `--cache` adds nx2mac's staging folder and managed venv. **The app,
its saves, the engine checkout and your game dumps are never touched**; the next build extracts
again (a few minutes). The GUI offers the same
as **Clean build files** right after a build finishes (it also removes the
`<Game>.nx2mac.json` — the next build writes a fresh one).

```bash
nx2mac cloud --set "~/Library/Mobile Documents/com~apple~CloudDocs/nx2mac"
nx2mac cloud --clear          # back to local only
```

Saves always stay in the library (the game plays from them); the engine additionally copies each
game's save into that synced folder as `<folder>/<TITLEID>/save.dmsync` (per player:
`<folder>/players/<player>/<TITLEID>/save.dmsync`), and pulls it back when it is newer. The engine
keeps 10 backups per side and refuses to overwrite a save that changed on both sides — it plays the
newer one and keeps the other in the backups. GUI: the **Games** tab, a *Cloud saves* section under the libraries — one folder at a time
(*Choose folder…*, `⊖` to stop syncing).

### Players (library-wide, like NX2Apple)

Players are one list for the whole library, so the picker sits at the top of the **Games** tab and
applies to every game — each game keeps a save store per player:

```bash
nx2mac player --create "Ada"          # add a player with your preferred name (max 8)
nx2mac player --select Ada            # choose who plays (updates every built game's env)
nx2mac player                         # list them; * marks the active one
```

In a game, the engine may still show its own account picker on a first launch of a new save store:
pick the user listed there (it carries your player's name) and press **Select**. There is nothing to
create — each player already has their own user 1 — and a cancelled picker makes most games quit, so
press Select rather than Cancel.

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

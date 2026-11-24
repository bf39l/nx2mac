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
uv tool install .              # the CLI on PATH as `nx2mac`
./gui/build.sh release ~/Applications   # the GUI as ~/Applications/nx2mac.app
```

From a checkout, `uv run nx2mac …` works without installing anything.

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
nx2mac engine [REF] [--source upstream|fork] [--show]
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
- **Build tab** — pick `prod.keys` (optional), the game (dump folder, package file or `.nup`),
  an update and any number of DLC packages (auto-sized vertical list, five rows then scrolls),
  an **output folder** (defaults to the game's directory), and the engine + ref.
  The engine's progress streams into the log; when it finishes you can reveal or open the app.

The app reads its icon from `gui/Resources/AppIcon.icns`, regenerated with
`swift gui/tools/make_icon.swift gui/Resources/AppIcon.icns`. Settings shows the app and CLI
versions side by side. Tests: `swift test --package-path gui` (add `NX2MAC_LIVE_TESTS=1` to
drive the real CLI).

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
| `DIR/prod.keys` | only if you keep it there — nx2mac reads your key set in place and never copies it |

## Engine patches

`patches/NNNN-*.patch` are applied to the pinned engine checkout after every reset (git-format,
filename order), so local fixes for the engine or anything vendored in it survive reboots of the
cache. Keep them minimal and drop them once upstream lands the fix — see
[patches/README.md](patches/README.md).

## Development

```bash
uv sync                     # Python deps (CLI)
uv run pytest               # 106 tests
uv run ruff check src/ tests/
swift test --package-path gui   # 27 tests
```

Layout: `src/nx2mac/` (CLI: `cli`, `doctor`, `remedies`, `installer`, `engines`, `builder`,
`staging`, `pyenv`, `config`, `runner`, `emit`), `gui/` (SwiftUI app), `patches/` (engine fixes).

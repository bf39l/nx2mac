# AGENT.md - notes for agents working in this repository

## What this project is

**nx2mac** builds Nintendo Switch (and Switch 2) games into native macOS `.app`
bundles by driving the [nxnative](https://github.com/seedds/nxnative) build script.
v1 is **CLI only - no UI**. The output platform is **macOS only** (`--platform mac-app`).

The reference implementation to learn from is
[NX2Apple](https://github.com/LNPANDY/NX2Apple) (Swift launcher + Python tools). We do not
maintain that project anymore because it tracks the nxnative engine too slowly; nx2mac's
value is staying on the **latest engine commit** with minimal glue.

## Engine source policy (important)

- The engine is **always fetched from GitHub at build time**. Never use a local checkout as a
  build input, and never assume one exists.
- Two selectable sources; if the user hasn't chosen one, **ask**:
  | id         | repository                       | ref    |
  |------------|----------------------------------|--------|
  | `upstream` | https://github.com/seedds/nxnative | `main` |
  | `fork`     | https://github.com/bf39l/nxnative | default branch (user's fork) |
- Always **report the resolved branch name and commit hash** when showing, syncing or building.
- Clone/fetch into a cache dir keyed by source + ref so re-syncs are cheap; keep checkouts clean
  (no local engine edits). Engine fixes go to nxnative as upstream PRs per its CONTRIBUTING.md.
- The root-level `nxnative` and `NX2Apple` symlinks are **personal reference copies only**
  (gitignored). Mine them for behaviour, never depend on their state or pin.

## Dependency checklist (what `doctor` must verify)

Authoritative source: `scripts/build_ipa_from_nsp.py -> check_prereqs()` in the pinned engine
checkout. Mirror it; do not invent a different set.

| Check | Detail / fix when missing |
|---|---|
| macOS, Apple silicon | `sys.platform == 'darwin'`, arm64 host (the script refuses x86_64/Rosetta Python via `check_native_python`) |
| Full Xcode (not CLT) | `xcode-select -p` must point at `/Applications/Xcode.app/...`; README pins **Xcode 27.2**, macOS **26.6+**. Fix: install Xcode from developer.apple.com, then `sudo xcode-select -s /Applications/Xcode.app` |
| Metal toolchain component | `xcrun -sdk macosx metal --version` must run; fix: `xcodebuild -downloadComponent MetalToolchain` (separate download since Xcode 26) |
| Homebrew present | needed to install the tools below |
| brew: `zstd` | `brew install zstd` |
| brew: `glslang` (`glslangValidator`) | `brew install glslang` - OpenGL game shaders, checked before their capture step |
| arm64 Python >= 3.12 (not Rosetta) | the interpreter that runs the engine script; Homebrew python or `/usr/bin/python3`. Note: this is separate from whatever runs nx2mac itself (>=3.11) |
| pip modules for that Python | `lz4`, `capstone>=5,<6` (Capstone 5 names required), `fonttools`, `zbic`, plus `backports.zstd` when Python < 3.14 (`compression.zstd` is built-in from 3.14). Use a venv/uv - PEP 668 blocks global pip on Homebrew python |
| In-repo native binaries (after clone) | `dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate` must run (`--source-sha`) and its fingerprint match `dmsh_chain.lock`; `dm/tools/nx_extract_bin/nx_extract` present; `dm/gfx/tools/spirv_cross_bin/spirv-cross` executable. These are committed universal binaries - **no .NET SDK, no system spirv-cross needed**. Don't rebuild them (`dm/tools/native_tools.sh`) or the fingerprint check fails |
| xcodegen / iOS SDK | **not required** for mac-app (mac path skips both); only needed later if we add IPA/iOS output |

User-provided inputs (validated at build time, with friendly errors):

- Switch 1 game: a folder of `.nsp/.nsz/.xci/.xcz` (+ updates/DLC) **or a single package file**
  (staged into a folder, since the engine wants a folder), and a `prod.keys` file
  (`name = hex value` lines; one file decrypts all games for its key generation). NX2Apple's
  BUILDING.md notes dumps can contain malformed-length lines that make extractors reject the
  file - detect and report those.
- Switch 2 game: a `.nup` container or dump folder (`base/exefs|romfs`, optional
  `updates/<ver>/...`, `dlc/<id>/...`) - works **without** prod.keys.

## Build flow (what the CLI orchestrates)

1. Resolve engine source + ref (ask user if unset); show branch name and commit hash.
2. Clone/fetch into cache dir at that exact commit; verify the in-repo binaries (above).
3. Ensure a suitable Python env (arm64 >= 3.12 + pip modules) - create/repair it in `setup`.
4. Validate game input folder and keys file.
5. Run inside the engine checkout:
   `python scripts/build_ipa_from_nsp.py <game dir> -k <prod.keys> --platform mac-app [-o out.app]`
6. Report the produced `.app` path (default `<game name>.app` in the game folder).

Expectations to surface: first build of a game takes ~30-60 minutes and needs roughly twice
the game size + 10 GB free disk; rebuilds are fast. Optional engine flags worth passing
through later: `--mac-sign` (codesign identity, default ad-hoc), `--seen-shaders`,
`--prebake`, `-o`.

## v1 CLI shape

```
nx2mac doctor      # check the whole table above; show what's missing and how to fix it
nx2mac setup       # per missing item: "Install zstd? (brew install zstd) [y/N]" and run it;
                   # manual-only items (full Xcode, macOS, Homebrew itself) get a note + link
                   #   --install NAME (repeatable) / --all / --yes / --no-prompt
nx2mac probe PATH  # what a folder holds, which package is the base game (the GUI pre-picks it)
nx2mac library     # game library setting + the games built into it (--set/--clear/--json)
nx2mac player      # a game's players: --create/--select/--delete (each keeps own saves + name)
nx2mac engine      # select + pin a source; reports branch name + commit hash (--show: no network)
nx2mac build DIR   # full pipeline; -k <prod.keys>, -o <out.app>, --update F, --dlc F (repeatable),
                   # --out-dir DIR (app + build folder + prod.keys together)
```

`--out-dir DIR` is the recommended workflow: the engine gets `--work-dir DIR/<game>_build`, which
makes it write the `.app` into DIR as well, so the folder holds the app, the build folder and
`prod.keys` only if the user keeps it there (nx2mac never copies keys). `-o` still overrides the
app path.

**Default**: the output folder is the game's own directory (the folder itself for a dump, its
parent for a package file). Without `--out-dir` the engine already writes app + `_build` beside the
game; when nx2mac must stage inputs (lone package file, update/DLC elsewhere) it points
`--work-dir` at that same directory so the result never lands inside the cache. The key set is
always read where it is (explicit `--keys`, game folder/parent, then the output folder) and is
never copied.

`build --update`/`--dlc` **stage** a folder of symlinks (game + chosen extras) because the engine
takes exactly one input folder; the staging dir lives in the cache keyed by the selection, so the
engine's `_build` folder and its caches are reused. Extras are rejected for `.nup` inputs (a NUP
already carries its updates/DLC).

All commands accept `--json` (before or after the subcommand) — see the interface contract below.

Library layout (`src/nx2mac/`): `cli.py` (typer app, thin renderer), `runner.py` (`Env`
protocol + `RealEnv`), `doctor.py` (checks), `remedies.py` (how each check is fixed, and whether
we may run it), `installer.py` (runs those fixes), `engines.py` (GitHub resolve/clone/pin
+ patch series), `builder.py` (validate inputs, run the nxnative script, stream output),
`staging.py` (one engine-ready folder from game + update/DLC), `pyenv.py` (arm64 CPython discovery
+ managed venv), `config.py` (persistent choices), `emit.py` (JSON/NDJSON output), `inputs.py`
(shared extensions). Config: `~/.config/nx2mac/config.toml` (XDG-aware); caches under
`~/Library/Caches/nx2mac` or `$NX2MAC_CACHE_ROOT` (`engines/<source-id>-<ref-slug>`, `python/`,
`staging/<game>-<hash>/`).

## Game library and players

`[library] dirs` is a **list** (unset by default): each entry must exist and must not hold game
packages (a dump is refused; nx2mac never creates folders). `library --add/--remove/--clear` manage
it; `build` files into the first one unless `--library` says otherwise, and the Games tab, the
player/cloud env rewrites and `library --json` cover every library. Only
`library --set`/`--clear` (Setup tab) change it — picking a game dir or output folder in the Build
tab must never touch it. With it set, `build` writes into `<library>/<Game>/` and bakes
`support/build-env.txt` into the app via the engine's `--env-file`, so the launcher keeps its
support dir, logs, saves, cheats and screenshots inside that folder instead of `~/Library`:

| variable | points at |
| --- | --- |
| `DM_SUPPORT_DIR` | `<game>/support` (its env.txt, temp, user registry) — added by our patch |
| `DM_LOG_DIR` | `<game>/logs` — added by our patch |
| `DM_SAVE` / `DM_CHEATS` / `DM_ALBUM_DIR` | `<TID>/save` (players: `<TID>/players/<player>/save`), `<TID>/mods/cheats`, `<TID>/screenshots` |
| `DM_PSO_USER` | `save/<TID>/pso_user.tsv` — the shared pipeline cache, one per game |
| `DM_SAVE_SYNC` | the synced folder from `nx2mac cloud --set` (off by default) |

Players are **library-wide** (`<library>/players.json`, one list for every game, mirroring
NX2Apple's launcher-wide `PlayerProfileStore`): the GUI picks the active player at the top of the
Games tab, and `nx2mac player` rewrites *every* built game's env. Cloud saves use the engine's own `save_sync.c`: the save stays local and `<cloud>/<TITLEID>/save.dmsync`
is the copy (per player: `<cloud>/players/<player>/…`). Only `DM_SAVE_SYNC` is ours to set — the
download-before-mount, upload-after-commit and the backup/conflict rules are the engine's.

`nx2mac player --create NAME` adds a player; each player is **user 1 in their own save store**
(`<game>/players/<id>/saves`) with `DM_NICK=<name>` in `<game>/support/env.txt`, which the launcher
reads after the baked file and lets win. That is the only way to give every player a custom name:
the engine reads `DM_NICK` for user 1 only and hard-codes "Player 2"..8.

## In-game account picker

The engine's Mac front end shows a native "Choose a user" alert (`dm/host/user_select_mac.h`): a
drop-down of the local users plus **Select** / **Cancel**. Pressing Cancel returns
`RES_ACC_CANCELED` and most games **quit** on it; that is engine behaviour, not a crash. Since every
nx2mac player is user 1 in their own store, there is never a user to add in-game — pick the listed
user and press Select. `patches/0003-account-add-user-button.patch` turns "Add user" into its own
button so the intent cannot be confused with Cancel.

## Engine patches (`patches/`)

`patches/NNNN-*.patch` (git format, applied in filename order) are applied with
`git apply --whitespace=nowarn` to the engine checkout after every `git checkout -f <commit>`,
so the tree is deterministic: same pinned commit + same series = same tree. They may fix the
engine or anything vendored in it. A failing patch aborts with an error naming the file, and
applied names are reported on `ResolvedEngine.patches` / in `engine|build --json`. Keep them
minimal and drop them when upstream lands the fix. See `patches/README.md`.

## Machine interface for the future Swift GUI

The GUI will shell out to this CLI and never import Python, so the CLI is the contract. Keep it
stable and renderer-agnostic:

- `doctor|setup|engine|version|build --json`: single-document commands print **one** JSON object;
  `build` prints **NDJSON events**, one object per line:
  `{"event":"engine","engine":{...}}`, `{"event":"log","line":"..."}` (per engine output line),
  `{"event":"result","ok":true,"returncode":0,"app_path":"...","argv":[...]}`,
  or `{"event":"error","message":"..."}` on failure.
- Check names are stable machine keys: `macos`, `apple-silicon`, `macos-version`, `xcode`,
  `metal-toolchain`, `git`, `brew`, `zstd`, `glslangValidator`, `make`, `engine-python`,
  `engine-venv`; each carries `detail`, an actionable `fix` string, plus `installable`, `install`
  (argv), `kind` (`brew|xcode|internal|manual`), `note` and `link`.
- `setup --json` (no `--install`) prints one report document; `setup --install N…|--all --json`
  prints NDJSON (`install-start`, `log`, `install-done`, final `setup` with the refreshed doctor).
- Exit codes: `0` success, non-zero failure; `build` propagates the engine's exit code. The
  reason is always in the JSON payload, so the GUI never parses human text.
- Engine payload always includes `source.id|label|url`, `ref`, `branch`, `commit`,
  `short_commit`, `path`, `patches`.
- Saved state is readable directly from the config file (`engine.id`, `engine.ref`).

Add functionality to the core modules + JSON payloads first; the rich/human output is just a
renderer of the same results.

## English Swift GUI (`gui/`)

A native SwiftUI app that only shells out to `nx2mac … --json` (no Python imports):

- **Setup tab** (first tab, opened by default): every prerequisite with an Install button for the
  fixable ones (`setup --install <name> --json`), Copy command / Open page for manual ones, and
  "Install all missing" (`setup --all`).
- **Build tab** order: engine selection (source + ref + Save & Pin) first, then `prod.keys`, then
  the **Game dir** / **Game** rows — choosing the folder runs `nx2mac probe`
  and auto-selects the base package, which is the only package listed (role + size); a different
  file is chosen by hand with **Game → Choose…**. The output folder defaults to the **game
  library** and the GUI then passes `--library` (not `--out-dir`), so the build is filed into
  `<library>/<Game>/`; choosing a folder yourself sends `--out-dir` instead, which wins., optional update(s) and any
  number of DLC packages/folders (both shown as vertical lists with per-item remove), one optional
  **Output folder** (`--out-dir`: app + build folder + keys), engine + ref ("Save & Pin"), then
  Build with a live log and the engine's stage tags (`[8/8] Mac app`); Done reveals/opens the
  `.app` the CLI reported.

Build it with `./gui/build.sh release [dest]` (SwiftPM + a script that wraps the binary into
`nx2mac.app`; no Xcode project). The script also **freezes the CLI into the bundle**:
`uv run --with pyinstaller pyinstaller … packaging/cli_entry.py` produces a standalone
`Contents/Resources/cli/nx2mac` (≈31 MB, `patches/` included as `nx2mac/patches`), so users never
have to install the CLI; `NX2MAC_SKIP_CLI_BUNDLE=1` skips that step while iterating on Swift.
CLI resolution order in `CLILocator`: Settings override → `NX2MAC_BIN` → bundled copy → `PATH` →
well-known dirs → dev checkout (`.venv/bin/python -m nx2mac`).

**PATH gotcha:** a Finder-launched app inherits launchd's `/usr/bin:/bin:/usr/sbin:/sbin`, so
Homebrew's `brew`/`zstd`/`glslangValidator` look missing and builds fail. `ToolPath.augmented()` +
`CLIRunner.childEnvironment()` prepend the Homebrew/`~/.local/bin` directories for every CLI
subprocess — keep using that helper for new launches, and test the real case with
`env -i /usr/bin/open ~/Applications/nx2mac.app`. The icon is committed at `gui/Resources/AppIcon.icns` and
regenerated with `swift gui/tools/make_icon.swift gui/Resources/AppIcon.icns`; `build.sh` copies
it into the bundle and ad-hoc signs it. Settings shows the app version next to
`nx2mac version --json`, so a GUI/CLI mismatch is visible. It finds the CLI via `NX2MAC_BIN`, the `cliPath` setting, PATH,
well-known install dirs, then a dev checkout's `.venv/bin/python -m nx2mac`. Tests:
`swift test --package-path gui` (add `NX2MAC_LIVE_TESTS=1` to also drive the real CLI).

## Current state

All v1 slices are implemented with unit tests (run: `uv run pytest`, lint: `uv run ruff check`):

- `runner.py` — `Env` protocol (`which`, `run`, `run_stream` incl. per-line callback) + `RealEnv`
- `config.py` — TOML `engine.id` / `engine.ref`, XDG config path, `NX2MAC_CACHE_ROOT`
- `pyenv.py` — arm64 CPython >= 3.12 discovery, managed venv at `<cache>/python`, pip specs
- `doctor.py` — 12 individually-addressable checks with fix commands
- `engines.py` — the closed source catalog (upstream `seedds/nxnative`, fork `bf39l/nxnative`;
  no free-form URL, because `patches/` targets those repos), clone/fetch into
  `<cache>/engines/<owner>-<repo>_<ref>`, ref->commit pinning, patch series
- `builder.py` — game classification (.nsp/.nsz/.xci/.xcz vs .nup), prod.keys lookup, streaming build
- `cli.py` — `version|doctor|setup|engine|build` with human and `--json` rendering
- `remedies.py` / `installer.py` — per-check fixes the CLI can run (`setup --install/--all`)
- `staging.py` — update/DLC staging for the engine's single-folder input model
- `library.py` — the game library: per-game folders, `nx2mac.json` manifest, `players.json`,
  the `--env-file` that keeps the engine's state in the game folder, and the scan for
  `library --json`. The scan lists only **built** games (a folder with a manifest or a `.app`, plus
  `.app`s directly in the library root); dumps, key files and tool checkouts in the same folder are
  ignored, because users do point the library at a folder that already holds their dumps.
- `gui/` — SwiftUI app (Setup, Games, Build tabs, app icon), `swift test` covers payload parsing,
  arg assembly and the output-folder default

Version lives in `src/nx2mac/__init__.py` (`VERSION`); pyproject reads it via
`[tool.hatch.version]`, and `gui/Info.plist` must be bumped alongside it (v0.0.1 at the time of
writing).
- `patches/` — engine patch series + README

**Acceptance test passed** (real build, human + `--json` modes):

```
nx2mac build "/Users/bo/switch/1佣兵之翼_伪之不死鸟_Mercenaries_Wings_The_False_Phoenix[010094500C216000]" \
  --keys /Users/bo/switch/prod.keys -o "/Users/bo/switch/Mercenaries Wings.app" \
  --source fork --ref main
```

Engine pinned at `69513b66`; ~44 s end to end (434 shaders, 211 pipeline
pairs) producing a 241 MB ad-hoc signed `.app` that boots the game's front end. Host prep needed
was `brew install glslang` + `nx2mac setup`. The engine prints a RED "provisional game profile"
warning at the end - that is engine-side bring-up (`docs/agents` in the engine), not nx2mac.

Next: the Swift GUI (shells out to `nx2mac ... --json`, see the contract above).

## Open questions

- Whether `build` should accept arbitrary extra nxnative flags verbatim (`-- ...`). `run_build`
  already has `extra_args`; no CLI flag exposes it yet (useful ones: `--mac-sign`, `--seen-shaders`,
  `--prebake`, `-p/--package`, `--aoc-dir`).
- Optional engine flags worth surfacing: `--mac-sign <identity>`, `--seen-shaders`, `--prebake`.
- iOS/IPA output stays out of scope; keep the design open to it.

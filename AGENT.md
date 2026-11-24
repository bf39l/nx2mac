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

- Switch 1 game: a folder of `.nsp/.nsz/.xci/.xcz` (+ updates/DLC) and a `prod.keys` file
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
nx2mac setup       # install/repair what can be automated (managed Python venv + pip);
                   # print manual steps for the rest (full Xcode, Metal toolchain, brew tools)
nx2mac engine      # select + pin a source; reports branch name + commit hash (--show: no network)
nx2mac build DIR   # full pipeline above; -k <prod.keys>, -o <out.app>; asks which engine if unset
```

All commands accept `--json` (before or after the subcommand) — see the interface contract below.

Library layout (`src/nx2mac/`): `cli.py` (typer app, thin renderer), `runner.py` (`Env`
protocol + `RealEnv`), `doctor.py` (checks + remediation), `engines.py` (GitHub resolve/clone/pin
+ patch series), `builder.py` (validate inputs, run the nxnative script, stream output),
`pyenv.py` (arm64 CPython discovery + managed venv), `config.py` (persistent choices),
`emit.py` (JSON/NDJSON output). Config: `~/.config/nx2mac/config.toml` (XDG-aware); caches under
`~/Library/Caches/nx2mac` or `$NX2MAC_CACHE_ROOT` (`engines/<source-id>-<ref-slug>`, `python/`).

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
  `engine-venv`; each carries `detail` and an actionable `fix` command string.
- Exit codes: `0` success, non-zero failure; `build` propagates the engine's exit code. The
  reason is always in the JSON payload, so the GUI never parses human text.
- Engine payload always includes `source.id|label|url`, `ref`, `branch`, `commit`,
  `short_commit`, `path`, `patches`.
- Saved state is readable directly from the config file (`engine.id`, `engine.ref`).

Add functionality to the core modules + JSON payloads first; the rich/human output is just a
renderer of the same results.

## Current state

All v1 slices are implemented with unit tests (run: `uv run pytest`, lint: `uv run ruff check`):

- `runner.py` — `Env` protocol (`which`, `run`, `run_stream` incl. per-line callback) + `RealEnv`
- `config.py` — TOML `engine.id` / `engine.ref`, XDG config path, `NX2MAC_CACHE_ROOT`
- `pyenv.py` — arm64 CPython >= 3.12 discovery, managed venv at `<cache>/python`, pip specs
- `doctor.py` — 12 individually-addressable checks with fix commands
- `engines.py` — upstream/fork sources, clone/fetch into cache, ref->commit pinning, patch series
- `builder.py` — game classification (.nsp/.nsz/.xci/.xcz vs .nup), prod.keys lookup, streaming build
- `cli.py` — `version|doctor|setup|engine|build` with human and `--json` rendering
- `patches/` — engine patch series + README

**Acceptance test passed** (real build, human + `--json` modes):

```
nx2mac build "/Users/bo/switch/1佣兵之翼_伪之不死鸟_Mercenaries_Wings_The_False_Phoenix[010094500C216000]" \
  --keys /Users/bo/switch/prod.keys -o "/Users/bo/switch/Mercenaries Wings.app" \
  --source fork --ref fork-customizations
```

Engine pinned at `69513b66` (`fork-customizations`); ~44 s end to end (434 shaders, 211 pipeline
pairs) producing a 241 MB ad-hoc signed `.app` that boots the game's front end. Host prep needed
was `brew install glslang` + `nx2mac setup`. The engine prints a RED "provisional game profile"
warning at the end - that is engine-side bring-up (`docs/agents` in the engine), not nx2mac.

Next: the Swift GUI (shells out to `nx2mac ... --json`, see the contract above).

## Open questions

- Whether `build` should accept arbitrary extra nxnative flags verbatim (`-- ...`). `run_build`
  already has `extra_args`; no CLI flag exposes it yet.
- Optional engine flags worth surfacing: `--mac-sign <identity>`, `--seen-shaders`, `--prebake`.
- iOS/IPA output stays out of scope; keep the design open to it.

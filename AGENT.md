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
nx2mac setup       # install/repair what can be automated (brew installs, Python venv + pip,
                   # Metal toolchain download); print manual steps for the rest (full Xcode)
nx2mac engine      # list sources; select upstream|fork with --ref/--pin; sync reports branch+hash
nx2mac build DIR   # full pipeline above; -k <prod.keys>, -o <out.app>; ask which engine if unset
```

Library layout (`src/nx2mac/`): `cli.py` (typer app), `doctor.py` (checks + remediation text),
`engines.py` (GitHub resolve/clone/pin), `builder.py` (run the nxnative script, stream output),
`config.py` (persistent choices: engine id/ref, cache dir). Proposed config location:
`~/.config/nx2mac/config.toml`; caches under `~/Library/Caches/nx2mac/engine/<id>/<commit>`.

## Current state (first kick-off)

- Repo has no commits yet. Python project via uv + hatchling; deps typer, rich, pytest.
  Console script: `nx2mac` (`src/nx2mac/cli.py`). Run with `uv run nx2mac ...`, tests with
  `uv run pytest`.
- Done: package renamed from the earlier `nxmac` stub (we are macOS-only); working `version`,
  `engines` and a first-pass `doctor`; reference symlinks gitignored.
- TODO in order: real dependency checks in `doctor.py` (+ remediation in `setup`), engine
  resolve/clone in `engines.py` with branch+hash reporting, `builder.py` driving the pinned
  script, config persistence, pytest coverage (fake PATH / fake checkouts).

## Open questions

- Config format/location (TOML at `~/.config/nx2mac/config.toml` is the proposal above).
- Whether `build` should accept arbitrary extra nxnative flags verbatim (`-- ...`).
- iOS/IPA output and a GUI are explicitly out of scope for v1; keep the design open to them.

# nx2mac

Build Nintendo Switch and Switch 2 games into native macOS apps by driving the
[nxnative](https://github.com/seedds/nxnative) build script, always against a fresh
engine checkout from GitHub (upstream main or your fork).

v1 is CLI only. Agent-oriented documentation lives in [AGENT.md](AGENT.md); it carries
the dependency checklist, engine source policy and the v1 CLI design.

```bash
uv sync
uv run nx2mac doctor     # check Xcode / Homebrew / Python prerequisites
uv run nx2mac engines    # show engine sources
```

## GUI (macOS)

A native SwiftUI app that wraps the CLI (it never imports the Python code, it runs
`nx2mac … --json` and renders the events):

```bash
./gui/build.sh release          # -> gui/.build/NX2Mac.app
open gui/.build/NX2Mac.app
```

- **Build tab** — pick the dump folder (or a `.nup`), optionally `prod.keys`, an update and any
  number of DLC packages, where to save the `.app`, and which engine/ref to pin. The engine's
  progress streams into the log; when it finishes you can reveal or open the app.
- **Setup tab** — every prerequisite with its state. Fixable items get an **Install** button
  (it runs `nx2mac setup --install <name>` and streams the output); items we must not automate
  (installing Xcode, updating macOS, Homebrew itself) show the command to copy and a link.

`swift test --package-path gui` runs the GUI tests; add `NX2MAC_LIVE_TESTS=1` to also drive the
real CLI.

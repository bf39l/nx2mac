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

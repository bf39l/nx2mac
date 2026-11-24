"""PyInstaller entry point for the CLI bundled inside nx2mac.app."""

from nx2mac.cli import app

if __name__ == "__main__":
    app(prog_name="nx2mac")

"""Assemble one input folder for the engine from a base game plus chosen extras.

The engine's script takes a *single* folder and classifies what it finds there by
reading each package's content meta: base game, update and DLC are told apart by
title ID (`...000` base, `...800` update, `base + 0x1000 + index` DLC). There is
no flag for "here is the update" or "here are the DLCs".

So when the user picks an update or DLC files that live somewhere else, nx2mac
stages a folder of symlinks: the game's own entries plus the chosen files. The
engine then sees a normal one-game folder, and its expensive build folder
(`<title id>_build`, holding extracted files and shader caches) lands inside the
staging directory — outside the user's game folder and reusable next time,
because the staging path is stable for a given selection.
"""

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path

from .inputs import GAME_EXTENSIONS

#: Records the symlinks we created so a refresh only replaces our own entries
#: and leaves the engine's `<title id>_build` directory (and its caches) alone.
MANIFEST_NAME: str = ".nx2mac-links.json"

#: Folders the engine writes next to a game (its build folder and the survey it runs when a game has
#: no profile). They are not game input: staging them would copy a multi-GB cache, and staging one
#: that already sits in the staging folder crashed the refresh (unlink on a directory, EPERM).
ENGINE_FOLDERS: tuple[str, ...] = ("_build", "_survey")


def _slug(text: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in text)
    return cleaned.strip("-._")[:40] or "game"


def staging_dir(cache_root: Path, game_path: Path, extras: Sequence[Path]) -> Path:
    """Stable directory for one (game, update/DLC selection) combination."""
    key = hashlib.sha256("\n".join([str(game_path), *map(str, extras)]).encode()).hexdigest()[:16]
    label = game_path.stem if game_path.is_file() else game_path.name
    return cache_root / "staging" / f"{_slug(label)}-{key}"


def _link(source: Path, directory: Path, taken: set[str]) -> str:
    """Symlink *source* into *directory*, uniquifying the name if needed."""
    name = source.name
    if name in taken:
        index = 2
        while f"{source.stem}-{index}{source.suffix}" in taken:
            index += 1
        name = f"{source.stem}-{index}{source.suffix}"
    destination = directory / name
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        destination.unlink()
    elif destination.is_dir():
        # A real directory we did not create (the engine's own, or a previous run's): never delete
        # it, just take a free name for our link.
        index = 2
        while (directory / f"{source.stem}-{index}{source.suffix}").exists():
            index += 1
        name = f"{source.stem}-{index}{source.suffix}"
        destination = directory / name
    destination.symlink_to(source.resolve())
    taken.add(name)
    return name


def _game_entries(game: Path) -> list[Path]:
    """The entries of a game folder worth staging (a single package file stages itself).

    Files with a game extension are linked individually; dump layout folders
    (`base/`, `updates/<version>/`, `dlc/`) are linked whole so their structure
    survives.
    """
    if game.is_file():
        return [game]
    entries: list[Path] = []
    for entry in sorted(game.iterdir()):
        if entry.name.startswith(".") or entry.name.endswith(ENGINE_FOLDERS):
            continue
        if entry.is_dir() or entry.suffix.lower() in GAME_EXTENSIONS:
            entries.append(entry)
    return entries


def _extra_entries(path: Path) -> list[Path]:
    """A chosen update/DLC path as linkable entries (a file, or a folder's files)."""
    if path.is_file():
        return [path]
    entries: list[Path] = []
    for entry in sorted(path.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir() or entry.suffix.lower() in GAME_EXTENSIONS:
            entries.append(entry)
    return entries


def prepare_staging(cache_root: Path, game_path: Path, updates: Sequence[Path] = (), dlcs: Sequence[Path] = ()) -> Path:
    """Create/refresh the staging folder for one game and its chosen extras.

    `game_path` may be a dump folder or a single package file (`.nsp/.nsz/.xci/.xcz`);
    the engine wants a folder, so a lone file is staged as a folder holding it.

    Symlinks are cheap and transparent to the engine, keep the user's game folder
    untouched, and let several selections coexist (different update = different
    staging dir, each with its own build cache).
    """
    extras = [*updates, *dlcs]
    directory = staging_dir(cache_root, game_path, extras)
    directory.mkdir(parents=True, exist_ok=True)

    manifest = directory / MANIFEST_NAME
    if manifest.is_file():
        for name in json.loads(manifest.read_text(encoding="utf-8")):
            stale = directory / name
            if stale.is_symlink():
                stale.unlink()

    taken: set[str] = set()
    created: list[str] = []
    sources = _game_entries(game_path)
    for extra in extras:
        sources.extend(_extra_entries(extra))

    for source in sources:
        created.append(_link(source, directory, taken))

    manifest.write_text(json.dumps(created, indent=0), encoding="utf-8")
    return directory


def describe_staging(directory: Path) -> str:
    """One-line summary of what the staging folder exposes."""
    links = [entry.name for entry in sorted(directory.iterdir()) if entry.is_symlink()]
    return f"{len(links)} entries in {directory}"


def cleanup_staging(directory: Path) -> None:
    """Remove our symlinks (and the manifest) but keep the engine's build folder."""
    manifest = directory / MANIFEST_NAME
    if manifest.is_file():
        for name in json.loads(manifest.read_text(encoding="utf-8")):
            stale = directory / name
            if stale.is_symlink():
                stale.unlink()
        manifest.unlink()
    if directory.is_dir() and not any(os.scandir(directory)):
        directory.rmdir()

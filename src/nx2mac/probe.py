"""Work out what a game folder holds, so a UI can pre-pick "the game file".

A dump folder usually holds more than one package: the base game, an update
(`...800`), DLC (`...F0xx`), sometimes a merged "B+U" file. The engine reads them
all from the folder and groups them by title ID, but a UI that lets the user pick
one file needs a sensible default — that is what :func:`probe` answers.

The guess is deliberately conservative: a package whose *filename* carries a
16-hex title ID ending in `000` is the base game; otherwise update/DLC-looking
names are dropped and the largest remaining package wins (the base game is
normally the biggest file). The user can always override the choice.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .inputs import GAME_EXTENSIONS, NSO_EXTENSION

#: A 16-hex-digit title ID anywhere in a package name.
TITLE_ID = re.compile(r"[0-9A-Fa-f]{16}")

#: A version in a package name ("…_Update_v1.25.0.nsp", "…[v65536]").
VERSION = re.compile(r"[vV](\d+)(?:\.(\d+))?(?:\.(\d+))?")

#: Name fragments that mean "not the base game".
UPDATE_OR_DLC = re.compile(
    r"(?i)(^|[^a-z])(update|patch|dlc|aoc|addon|add-on|cheat|nsp_update)([^a-z]|$)"
)


@dataclass(frozen=True)
class Package:
    """One game package inside a folder (or the single file that was passed)."""

    path: Path
    extension: str
    title_id: str | None
    role: str  # "base" | "update" | "dlc" | "unknown"
    size: int
    version: tuple[int, int, int] | None = None  # parsed from the name, when it has one

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "extension": self.extension,
            "title_id": self.title_id,
            "role": self.role,
            "size": self.size,
            "version": ".".join(str(part) for part in self.version) if self.version else None,
        }


@dataclass(frozen=True)
class Scan:
    """What :func:`probe` found for one path."""

    path: Path
    kind: str  # "switch1" | "nso" | "empty"
    game_file: Path | None  # the package a UI should pre-select
    needs_prod_keys: bool
    packages: tuple[Package, ...]
    update: Path | None = None  # the newest update package beside the game, if any

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "game_file": str(self.game_file) if self.game_file is not None else None,
            "update": str(self.update) if self.update is not None else None,
            "needs_prod_keys": self.needs_prod_keys,
            "packages": [package.to_dict() for package in self.packages],
        }


def _role_for(name: str, title_id: str | None) -> str:
    """Guess a package's role from its filename (the engine uses content metas)."""
    if title_id is not None:
        low = int(title_id[-3:], 16)
        if low == 0x000:
            return "base"
        if low == 0x800:
            return "update"
        if 0xF000 <= low <= 0xFFFE:
            return "dlc"
    if UPDATE_OR_DLC.search(name):
        return "update" if re.search(r"(?i)update|patch", name) else "dlc"
    return "unknown"


def _packages(path: Path) -> list[Package]:
    """Every game package at *path*, or one level down (dump layouts)."""
    found: list[Package] = []

    def visit(candidate: Path, depth: int) -> None:
        if candidate.suffix.lower() in GAME_EXTENSIONS and candidate.is_file():
            match = TITLE_ID.search(candidate.name)
            title_id = match.group(0).upper() if match else None
            try:
                size = candidate.stat().st_size
            except OSError:
                size = 0
            version = None
            match_version = VERSION.search(candidate.name)
            if match_version:
                version = (
                    int(match_version.group(1)),
                    int(match_version.group(2) or 0),
                    int(match_version.group(3) or 0),
                )
            found.append(
                Package(
                    path=candidate,
                    extension=candidate.suffix.lower(),
                    title_id=title_id,
                    role=_role_for(candidate.name, title_id),
                    size=size,
                    version=version,
                )
            )
            return
        if depth <= 0 or not candidate.is_dir():
            return
        for entry in sorted(candidate.iterdir()):
            if entry.name.startswith(".") or entry.name.endswith("_build"):
                continue
            visit(entry, depth - 1)

    visit(path, 2)  # dump layouts nest one level: dlc/<index>.nsp, updates/<version>/...
    return found


def choose_game_file(packages: list[Package]) -> Path | None:
    """The package a UI should pre-select: the base game, else the biggest one."""
    if not packages:
        return None
    bases = [package for package in packages if package.role == "base"]
    if bases:
        return max(bases, key=lambda package: package.size).path
    candidates = [package for package in packages if package.role != "update"] or packages
    return max(candidates, key=lambda package: package.size).path


def newest_update(packages: list[Package]) -> Path | None:
    """The update to play: the highest version in the folder, else the biggest update."""
    updates = [package for package in packages if package.role == "update"]
    if not updates:
        return None
    numbered = [package for package in updates if package.version is not None]
    if numbered:
        return max(numbered, key=lambda package: (package.version or (0, 0, 0), package.size)).path
    return max(updates, key=lambda package: package.size).path


def probe(path: Path) -> Scan:
    """Describe a game folder or file: what it holds and which package is the game."""
    resolved = path.expanduser()
    resolved = resolved.resolve() if resolved.exists() else resolved

    if resolved.is_file():
        # A file is its own answer (.nup included: the engine takes it directly).
        if resolved.suffix.lower() == NSO_EXTENSION:
            package = Package(resolved, resolved.suffix.lower(), None, "base", _size(resolved))
            return Scan(resolved, "nso", resolved, False, (package,))
        packages = _packages(resolved)
        return Scan(
            resolved,
            "switch1" if packages else "empty",
            choose_game_file(packages),
            True,
            tuple(packages),
            newest_update(packages),
        )

    packages = _packages(resolved)
    if not packages:
        return Scan(resolved, "empty", None, True, ())
    if any(package.extension == NSO_EXTENSION for package in packages):
        nup = next(package for package in packages if package.extension == NSO_EXTENSION)
        return Scan(resolved, "nso", nup.path, False, tuple(packages))
    return Scan(resolved, "switch1", choose_game_file(packages), True, tuple(packages), newest_update(packages))


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0

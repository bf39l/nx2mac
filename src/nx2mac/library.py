"""The game library: one folder per built game.

A library is a directory the user picks (nx2mac never creates it, and there is no
default). Building into it gives every game a self-contained folder:

    <library>/<Game>/
    ├── <Game>.app          the built macOS app
    ├── saves/              DM_SAVE      - the game's save store
    ├── logs/               DM_LOG_DIR   - the engine's run log
    ├── mods/               reserved for loose mod files
    ├── cheats/             DM_CHEATS    - <build id>.txt cheat files
    ├── screenshots/        DM_ALBUM_DIR - the in-game album
    └── nx2mac.json         what was built, from which engine commit

The engine's launcher reads a per-game `env.txt` and only fills in what is *not*
already set, so writing the DM_* variables into that file points saves, cheats,
screenshots and logs at the library without touching the signed app.
"""

import json
import plistlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Manifest written into every library game folder.
MANIFEST_NAME: str = "nx2mac.json"

#: Sub-directories every library game gets. `support` is the engine's own state dir
#: (DM_SUPPORT_DIR): its env.txt, its temp and its per-user registry live there.
GAME_SUBDIRS: tuple[str, ...] = ("saves", "logs", "mods", "cheats", "screenshots", "support")

#: The file the build bakes into the app (`--env-file`) to point the engine here.
BUILD_ENV_NAME: str = "build-env.txt"

#: The players of one game (each gets its own save store, like NX2Apple's library).
PLAYERS_FILE: str = "players.json"
#: Nickname the engine uses for its local user 1 (see hle_account.c: DM_NICK).
PLAYER_NICK_ENV: str = "DM_NICK"
#: How many players one game may have (the engine allows 8 local users; we keep it tidy).
MAX_PLAYERS: int = 8

#: DM_* variables nx2mac manages in the game's env.txt, mapped to library subdirs.
MANAGED_ENV: dict[str, str] = {
    "DM_SAVE": "saves",
    "DM_TEMP": "temp",
    "DM_ALBUM_DIR": "screenshots",
    "DM_CHEATS": "cheats",
    "DM_LOG_DIR": "logs",
}

ENV_HEADER: str = "# written by nx2mac: everything this game writes stays in its own folder\n"


class LibraryError(Exception):
    """Raised when a library folder is missing, unset, or otherwise unusable."""


@dataclass(frozen=True)
class GameEntry:
    """One built game in the library (the folder is the record, nothing else)."""

    name: str  # display name
    path: Path  # the game's folder inside the library
    app_path: Path | None  # the .app, when it is still there
    title_id: str | None = None
    version: str | None = None
    engine_commit: str | None = None
    engine_ref: str | None = None
    built_at: str | None = None
    last_played: str | None = None
    patches: tuple[str, ...] = ()

    @property
    def has_app(self) -> bool:
        """Whether the built app is still where the manifest left it."""
        return self.app_path is not None and self.app_path.is_dir()

    @property
    def icon_path(self) -> Path | None:
        """The app's own icon, for the GUI's library list."""
        if self.app_path is None:
            return None
        for name in ("AppIcon.icns", "app.icns", "icon.icns"):
            candidate = self.app_path / "Contents" / "Resources" / name
            if candidate.is_file():
                return candidate
        return None

    @property
    def size_bytes(self) -> int:
        """Size of the built app (not of the saves/logs beside it)."""
        if not self.has_app or self.app_path is None:
            return 0
        total = 0
        for path in self.app_path.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "app_path": str(self.app_path) if self.app_path is not None else None,
            "has_app": self.has_app,
            "icon_path": str(self.icon_path) if self.icon_path is not None else None,
            "title_id": self.title_id,
            "version": self.version,
            "engine_commit": self.engine_commit,
            "engine_ref": self.engine_ref,
            "built_at": self.built_at,
            "last_played": self.last_played,
            "size_bytes": self.size_bytes,
            "patches": list(self.patches),
            "saves": str(self.path / "saves"),
            "logs": str(self.path / "logs"),
            "mods": str(self.path / "mods"),
            "cheats": str(self.path / "cheats"),
            "screenshots": str(self.path / "screenshots"),
        }


@dataclass(frozen=True)
class Player:
    """One player of a game: a name and their own save store."""

    id: str  # folder name, stable
    name: str  # what the game shows and what we write as DM_NICK
    created: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name, "created": self.created}


def _slug(text: str, taken: set[str]) -> str:
    base = "".join(char if char.isalnum() or char in "-_" else "-" for char in text.strip().lower())
    base = base.strip("-") or "player"
    slug, index = base, 2
    while slug in taken:
        slug = f"{base}-{index}"
        index += 1
    return slug


def load_players(game_directory: Path) -> tuple[list[Player], str | None]:
    """The game's players and the active one's id (empty list when none exist yet)."""
    path = game_directory / PLAYERS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    players = [
        Player(id=str(entry.get("id")), name=str(entry.get("name")), created=entry.get("created"))
        for entry in data.get("players", [])
        if isinstance(entry, dict) and entry.get("id") and entry.get("name")
    ]
    active = data.get("active")
    return players, active if any(player.id == active for player in players) else (players[0].id if players else None)


def save_players(game_directory: Path, players: list[Player], active: str | None) -> Path:
    """Write the player list (and which one plays) into the game folder."""
    path = game_directory / PLAYERS_FILE
    payload = {"active": active, "players": [player.to_dict() for player in players]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def player_save_dir(game_directory: Path, player_id: str) -> Path:
    """A player's own save store (what DM_SAVE points at)."""
    return game_directory / "players" / player_id / "saves"


def active_player(game_directory: Path) -> Player | None:
    players, active = load_players(game_directory)
    return next((player for player in players if player.id == active), None)


def create_player(game_directory: Path, name: str) -> Player:
    """Add a player with a preferred name and make them the active one."""
    wanted = name.strip()
    if not wanted:
        raise LibraryError("a player needs a name")
    if len(wanted) > 32:
        raise LibraryError("a player name can be at most 32 characters (the engine's limit)")
    players, _ = load_players(game_directory)
    if len(players) >= MAX_PLAYERS:
        raise LibraryError(f"this game already has {MAX_PLAYERS} players")
    if any(player.name.casefold() == wanted.casefold() for player in players):
        raise LibraryError(f"a player called {wanted!r} already exists")
    player = Player(id=_slug(wanted, {entry.id for entry in players}), name=wanted, created=_now())
    player_save_dir(game_directory, player.id).mkdir(parents=True, exist_ok=True)
    players.append(player)
    save_players(game_directory, players, player.id)
    return player


def select_player(game_directory: Path, name: str) -> Player:
    """Make an existing player (by name or id) the one who plays."""
    players, _ = load_players(game_directory)
    wanted = name.strip().casefold()
    player = next((entry for entry in players if entry.id.casefold() == wanted or entry.name.casefold() == wanted), None)
    if player is None:
        raise LibraryError(f"no player called {name!r} in this game")
    player_save_dir(game_directory, player.id).mkdir(parents=True, exist_ok=True)
    save_players(game_directory, players, player.id)
    return player


def delete_player(game_directory: Path, name: str) -> Player:
    """Forget a player (their save folder is left on disk, so nothing is lost by accident)."""
    players, active = load_players(game_directory)
    wanted = name.strip().casefold()
    player = next((entry for entry in players if entry.id.casefold() == wanted or entry.name.casefold() == wanted), None)
    if player is None:
        raise LibraryError(f"no player called {name!r} in this game")
    remaining = [entry for entry in players if entry.id != player.id]
    save_players(game_directory, remaining, None if active == player.id else active)
    return player


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def safe_name(text: str) -> str:
    """Filesystem-friendly folder name (keeps letters/digits in any script)."""
    cleaned = "".join(char if char.isalnum() or char in " ._-" else "-" for char in text)
    return " ".join(cleaned.split()).strip(" .-_")[:80] or "Game"


def require_library(library_dir: Path | None) -> Path:
    """Resolve a configured library, insisting it exists (we never create it)."""
    if library_dir is None:
        raise LibraryError(
            "no game library set. Choose one in the app's Setup tab, or run: nx2mac library --set <existing folder>"
        )
    path = library_dir.expanduser().resolve()
    if not path.is_dir():
        raise LibraryError(f"game library does not exist: {path} (create it first; nx2mac does not create it)")
    return path


def game_dir(library: Path, name: str) -> Path:
    """The folder for one game inside a library."""
    return library / safe_name(name)


def prepare_game_dir(library: Path, name: str) -> Path:
    """Create (or reuse) the game's folder plus its saves/logs/mods/cheats dirs."""
    directory = game_dir(library, name)
    for sub in ("", *GAME_SUBDIRS):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    return directory


def app_metadata(app: Path) -> dict[str, str | None]:
    """Read the identity of a built .app (name, title id, version) from its Info.plist."""
    info_path = app / "Contents" / "Info.plist"
    try:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException):
        return {"name": app.stem, "title_id": None, "version": None}
    bundle_id = str(info.get("CFBundleIdentifier") or "")
    title_id = bundle_id.split(".")[-1].upper() if bundle_id.startswith("org.nxnative.") else None
    name = str(info.get("CFBundleName") or info.get("CFBundleDisplayName") or app.stem)
    return {"name": name, "title_id": title_id, "version": str(info.get("CFBundleShortVersionString") or "") or None}


def support_dir(game_directory: Path) -> Path:
    """The engine's state folder for this game: kept inside the game's library folder."""
    return game_directory / "support"


def support_env_path(game_directory: Path) -> Path:
    """The env.txt the engine's launcher reads for this game (after the baked one)."""
    return support_dir(game_directory) / "env.txt"


def build_env_path(game_directory: Path) -> Path:
    """Where the env file handed to the engine at build time is written."""
    return support_dir(game_directory) / BUILD_ENV_NAME


def write_build_env(game_directory: Path) -> Path:
    """The env file baked into the app: it points the engine's state at this folder.

    The launcher reads this file before it decides where its support dir and log go,
    so `DM_SUPPORT_DIR` and `DM_LOG_DIR` here keep saves, logs, cheats, screenshots
    and its own state inside the game's library folder instead of ~/Library.
    """
    directory = support_dir(game_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for sub in GAME_SUBDIRS:
        (game_directory / sub).mkdir(parents=True, exist_ok=True)

    managed = {
        "DM_SUPPORT_DIR": str(directory),
        "DM_LOG_DIR": str(game_directory / "logs"),
        "DM_SAVE": str(game_directory / "saves"),
        "DM_TEMP": str(game_directory / "temp"),
        "DM_ALBUM_DIR": str(game_directory / "screenshots"),
        "DM_CHEATS": str(game_directory / "cheats"),
    }
    path = build_env_path(game_directory)
    lines = [ENV_HEADER.rstrip(), *[f"{key}={value}" for key, value in managed.items()]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_player_env(game_directory: Path) -> Path:
    """Point the engine's saves at the *active player's* store, and name them.

    Each player is user 1 in their own store, which is the only way to give every
    player their own name (the engine hard-codes "Player 2".. and reads DM_NICK for
    user 1 only). This file is read after the baked one, so it wins. Keys nx2mac
    does not manage are preserved, so hand-written settings survive.
    """
    path = support_env_path(game_directory)
    path.parent.mkdir(parents=True, exist_ok=True)

    managed: dict[str, str] = {}
    for variable, sub in MANAGED_ENV.items():
        managed[variable] = str(game_directory / sub)
        (game_directory / sub).mkdir(parents=True, exist_ok=True)

    player = active_player(game_directory)
    if player is not None:
        store = player_save_dir(game_directory, player.id)
        store.mkdir(parents=True, exist_ok=True)
        managed["DM_SAVE"] = str(store)
        managed[PLAYER_NICK_ENV] = player.name

    kept: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key = stripped.split("=", 1)[0].strip()
            if key not in managed:
                kept.append(stripped)

    lines = [ENV_HEADER.rstrip(), *kept]
    lines += [f"{variable}={value}" for variable, value in managed.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_manifest(
    game_directory: Path,
    *,
    name: str,
    app_path: Path,
    title_id: str | None,
    version: str | None,
    engine_source: str,
    engine_ref: str,
    engine_commit: str,
    patches: tuple[str, ...],
    inputs: dict[str, object],
) -> Path:
    """Record what was built (and from which engine) next to the app."""
    payload: dict[str, object] = {
        "name": name,
        "app": str(app_path),
        "title_id": title_id,
        "version": version,
        "engine": {
            "source": engine_source,
            "ref": engine_ref,
            "commit": engine_commit,
            "patches": list(patches),
        },
        "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "inputs": inputs,
        "paths": {sub: str(game_directory / sub) for sub in GAME_SUBDIRS},
    }
    path = game_directory / MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_manifest(game_directory: Path) -> dict[str, object] | None:
    """The manifest of one game folder, or None when there is none/invalid."""
    path = game_directory / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _entry(directory: Path, app: Path | None) -> GameEntry:
    """One library entry, from its manifest when present, else from the app itself."""
    manifest = read_manifest(directory) or {}
    app_str = manifest.get("app")
    if isinstance(app_str, str) and Path(app_str).is_dir():
        app = Path(app_str)
    engine = manifest.get("engine") if isinstance(manifest.get("engine"), dict) else {}
    patches = engine.get("patches") if isinstance(engine, dict) else None

    # A game built before the manifest existed (or moved in by hand) still shows
    # its real name/version: they are in the bundle's Info.plist.
    metadata: dict[str, str | None] = {}
    if app is not None and (not manifest or not manifest.get("version") or not manifest.get("name")):
        metadata = app_metadata(app)

    return GameEntry(
        name=str(manifest.get("name") or metadata.get("name") or (app.stem if app else directory.name)),
        path=directory,
        app_path=app,
        title_id=manifest.get("title_id") or metadata.get("title_id"),  # type: ignore[arg-type]
        version=manifest.get("version") or metadata.get("version"),  # type: ignore[arg-type]
        engine_commit=engine.get("commit") if isinstance(engine, dict) else None,
        engine_ref=engine.get("ref") if isinstance(engine, dict) else None,
        built_at=manifest.get("built_at"),  # type: ignore[arg-type]
        last_played=manifest.get("last_played"),  # type: ignore[arg-type]
        patches=tuple(str(p) for p in patches) if isinstance(patches, list) else (),
    )


def _is_game_folder(directory: Path) -> bool:
    """A built game: nx2mac's manifest, or an app bundle somebody built/put here."""
    if (directory / MANIFEST_NAME).is_file():
        return True
    return any(directory.glob("*.app"))


def list_games(library: Path) -> list[GameEntry]:
    """Every *built* game in the library, newest build first.

    Only folders that hold a build (an `nx2mac.json` manifest or a `.app`) count —
    a library folder routinely also holds dumps, key files and tool checkouts, and
    those must not show up as games. Apps sitting directly in the library root are
    listed too, since `-o` can put them there.
    """
    entries: list[GameEntry] = []
    if not library.is_dir():
        return entries

    for directory in sorted(library.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        if not _is_game_folder(directory):
            continue
        entries.append(_entry(directory, next(iter(sorted(directory.glob("*.app"))), None)))

    for app in sorted(library.glob("*.app")):
        entries.append(_entry(library, app))

    # "Recently played" first (like NX2Apple), then newest build, then name.
    entries.sort(key=lambda entry: (entry.last_played or "", entry.built_at or "", entry.name), reverse=True)
    return entries


def find_game(library: Path, needle: str) -> GameEntry:
    """Look a game up by its folder path, folder name, display name or title id."""
    raw = needle.strip()
    wanted = raw.lower()
    wanted_path = Path(raw).expanduser()
    try:
        wanted_path = wanted_path.resolve()
    except OSError:
        wanted_path = Path(raw)
    for entry in list_games(library):
        if entry.path.resolve() == wanted_path:
            return entry
        if wanted in {entry.path.name.lower(), entry.name.lower(), (entry.title_id or "").lower()}:
            return entry
    raise LibraryError(f"no game called {needle!r} in {library}")


def touch_last_played(game_directory: Path) -> str:
    """Record that the game was launched (keeps "recently played" ordering honest)."""
    path = game_directory / MANIFEST_NAME
    manifest = read_manifest(game_directory) or {}
    stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    manifest["last_played"] = stamp
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stamp


def describe_library(library: Path) -> dict[str, object]:
    """JSON payload for `nx2mac library --json` (the GUI's Games tab)."""
    games = list_games(library)
    return {
        "dir": str(library),
        "exists": library.is_dir(),
        "games": [entry.to_dict() for entry in games],
    }

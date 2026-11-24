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
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .inputs import GAME_EXTENSIONS

#: Manifest written into every library game folder.
MANIFEST_NAME: str = "nx2mac.json"

#: The built apps sit in the folder the user chose; nx2mac's own data sits in `nx2mac_game_lib/`:
#:   <chosen folder>/<Game>.app                       the built app
#:   <chosen folder>/<Game>.nx2mac.json               what was built, from which engine
#:   <chosen folder>/nx2mac_game_lib/players.json            the library's players (every game)
#:   <chosen folder>/nx2mac_game_lib/<TITLEID>/
#:   ├── save/                         DM_SAVE - the base store (save.users/.work land here)
#:   ├── players/<player>/save         one store per player
#:   ├── mods/<mod>/romfs              mods (atmosphere/contents/<TID>/romfs also accepted)
#:   ├── mods/cheats/                  DM_CHEATS
#:   ├── logs/                         DM_LOG_DIR - the launcher writes <TITLEID>.log here
#:   ├── screenshots/                  DM_ALBUM_DIR
#:   ├── support/                      DM_SUPPORT_DIR - env.txt, temp
#:   ├── build/                        the engine's work folder
#:   └── pso_user.tsv                  DM_PSO_USER - the shared pipeline cache
LIBRARY_DIRS: tuple[str, ...] = ("save", "logs", "mods", "screenshots", "support", "build")

#: nx2mac keeps everything in this visible sub-folder of the folder the user chose —
#: no dot-files, and the user's own folder stays as they left it.
LIBRARY_SUBDIR: str = "nx2mac_game_lib"

#: Sub-directories a per-game folder keeps (older layout, still read).
GAME_SUBDIRS: tuple[str, ...] = ("saves", "logs", "mods", "cheats", "screenshots", "support")

#: The file the build bakes into the app (`--env-file`) to point the engine here.
BUILD_ENV_NAME: str = "build-env.txt"

#: The players of one game (each gets its own save store, like NX2Apple's library).
PLAYERS_FILE: str = "players.json"
#: Nickname the engine uses for its local user 1 (see hle_account.c: DM_NICK).
PLAYER_NICK_ENV: str = "DM_NICK"
#: The engine's cloud-saves folder (save_sync.c): it keeps <folder>/<TITLEID>/save.dmsync.
CLOUD_ENV: str = "DM_SAVE_SYNC"
#: Colon-separated RomFS folders of the mods to switch on (hle_fs.c / hle_exl.c).
MODS_ENV: str = "DM_ROMFS_OVERLAY"
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

    #: The library this entry was found in (the scan fills it in).
    library: Path | None = None

    def data_dir(self, kind: str) -> Path:
        """Where this game's saves/logs/mods/cheats/screenshots/support live."""
        if self.library is not None and self.title_id:
            tid = self.title_id
            folders = {
                "saves": game_save_root(self.library, tid),
                "logs": logs_dir(self.library, tid),
                "mods": mods_dir(self.library, tid),
                "cheats": cheats_dir(self.library, tid),
                "screenshots": screenshots_dir(self.library, tid),
                "support": support_dir(self.library, tid),
            }
            if kind in folders:
                return folders[kind]
        # older per-game folder layout
        legacy = {"saves": "saves", "logs": "logs", "mods": "mods", "cheats": "cheats",
                  "screenshots": "screenshots", "support": "support"}
        return self.path / legacy.get(kind, kind)

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
            "saves": str(self.data_dir("saves")),
            "logs": str(self.data_dir("logs")),
            "mods": str(self.data_dir("mods")),
            "cheats": str(self.data_dir("cheats")),
            "screenshots": str(self.data_dir("screenshots")),
            "support": str(self.data_dir("support")),
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


def players_file(library: Path) -> Path:
    """The library's player list (one list for every game, as NX2Apple keeps it)."""
    return data_root(library) / PLAYERS_FILE


def load_players(library: Path) -> tuple[list[Player], str | None]:
    """The library's players and the active one's id (empty list when none exist yet)."""
    path = players_file(library)
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


def save_players(library: Path, players: list[Player], active: str | None) -> Path:
    """Write the library's player list (and which one plays)."""
    mark_library(library)
    path = players_file(library)
    payload = {"active": active, "players": [player.to_dict() for player in players]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def player_save_dir(library: Path, title_id: str, player_id: str) -> Path:
    """A player's own save store (what DM_SAVE points at), like NX2Apple's profiles."""
    return profiles_dir(library, title_id) / player_id / "save"


def active_player(library: Path) -> Player | None:
    players, active = load_players(library)
    return next((player for player in players if player.id == active), None)


def create_player(library: Path, name: str) -> Player:
    """Add a player with a preferred name and make them the active one."""
    wanted = name.strip()
    if not wanted:
        raise LibraryError("a player needs a name")
    if len(wanted) > 32:
        raise LibraryError("a player name can be at most 32 characters (the engine's limit)")
    players, _ = load_players(library)
    if len(players) >= MAX_PLAYERS:
        raise LibraryError(f"this game already has {MAX_PLAYERS} players")
    if any(player.name.casefold() == wanted.casefold() for player in players):
        raise LibraryError(f"a player called {wanted!r} already exists")
    player = Player(id=_slug(wanted, {entry.id for entry in players}), name=wanted, created=_now())
    players.append(player)
    save_players(library, players, player.id)
    for entry in list_games(library):
        if entry.title_id:
            player_save_dir(library, entry.title_id, player.id).mkdir(parents=True, exist_ok=True)
    return player


def select_player(library: Path, name: str) -> Player:
    """Make an existing player (by name or id) the one who plays."""
    players, _ = load_players(library)
    wanted = name.strip().casefold()
    player = next((entry for entry in players if entry.id.casefold() == wanted or entry.name.casefold() == wanted), None)
    if player is None:
        raise LibraryError(f"no player called {name!r}")
    for entry in list_games(library):
        if entry.title_id:
            player_save_dir(library, entry.title_id, player.id).mkdir(parents=True, exist_ok=True)
    save_players(library, players, player.id)
    return player


def delete_player(library: Path, name: str) -> Player:
    """Forget a player (their save folder is left on disk, so nothing is lost by accident)."""
    players, active = load_players(library)
    wanted = name.strip().casefold()
    player = next((entry for entry in players if entry.id.casefold() == wanted or entry.name.casefold() == wanted), None)
    if player is None:
        raise LibraryError(f"no player called {name!r} in this game")
    remaining = [entry for entry in players if entry.id != player.id]
    save_players(library, remaining, None if active == player.id else active)
    return player


def write_all_player_envs(library: Path, cloud_dir: Path | None = None) -> list[Path]:
    """Point every built game at the active player (and the cloud folder, when set)."""
    written: list[Path] = []
    for entry in list_games(library):
        if entry.title_id:
            written.append(write_player_env(library, entry.title_id, cloud_dir))
    return written


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def safe_name(text: str) -> str:
    """Filesystem-friendly folder name (keeps letters/digits in any script)."""
    cleaned = "".join(char if char.isalnum() or char in " ._-" else "-" for char in text)
    return " ".join(cleaned.split()).strip(" .-_")[:80] or "Game"


def check_library_choice(folder: Path) -> Path:
    """Validate a folder chosen as the library: it must exist and must not be a dump."""
    path = require_library(folder)
    if looks_like_game_dump(path):
        raise LibraryError(
            f"{path} holds game packages, so it looks like a game dump. Choose a folder for the "
            "built games instead (nx2mac files each game's app, saves, logs, mods and cheats there)."
        )
    return path


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


def looks_like_game_dump(folder: Path) -> bool:
    """Whether a folder holds game packages directly (a dump, not a place for builds)."""
    for entry in folder.iterdir() if folder.is_dir() else ():
        if entry.is_file() and entry.suffix.lower() in GAME_EXTENSIONS:
            return True
    return False


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


def app_root(folder: Path) -> Path:
    """Where the built apps go: the folder the user chose."""
    resolved = folder.expanduser()
    return resolved.parent if resolved.name == LIBRARY_SUBDIR else resolved


def data_root(folder: Path) -> Path:
    """nx2mac's data folder inside the user's choice (`<chosen>/nx2mac_game_lib`)."""
    return app_root(folder) / LIBRARY_SUBDIR


def root_of(folder: Path) -> Path:
    """The data folder (`<chosen>/nx2mac_game_lib`) — kept as the old name for callers."""
    return data_root(folder)


def is_library(folder: Path) -> bool:
    """Whether this folder holds built games or nx2mac's `nx2mac_game_lib` data folder."""
    base = app_root(folder)
    return (base / LIBRARY_SUBDIR).is_dir() or any(base.glob("*.app"))


def mark_library(folder: Path) -> Path:
    """Create nx2mac's data folder inside the user's choice (nothing hidden is written)."""
    root = data_root(folder)
    root.mkdir(parents=True, exist_ok=True)
    return root


def game_root(library: Path, title_id: str) -> Path:
    """Everything one game writes: `<library>/nx2mac_game_lib/<TITLEID>/`."""
    return data_root(library) / title_id


def game_save_root(library: Path, title_id: str) -> Path:
    """This game's save folder (the store and its players live inside it)."""
    return game_root(library, title_id) / "save"


def save_dir(library: Path, title_id: str) -> Path:
    """The base save store (DM_SAVE when no player is chosen).

    It is a `save` sub-folder so the engine's siblings of DM_SAVE (`<store>.users`,
    `<store>.work`, `<store>_device`) stay inside the game's `<TITLEID>/` folder
    instead of scattering over `save/`.
    """
    return game_save_root(library, title_id)


def pso_user_path(library: Path, title_id: str) -> Path:
    """The game's shared pipeline cache (DM_PSO_USER), one per game."""
    return game_root(library, title_id) / "pso_user.tsv"


def profiles_dir(library: Path, title_id: str) -> Path:
    """Where the players of one game keep their own stores."""
    return game_root(library, title_id) / "players"


def logs_dir(library: Path, title_id: str) -> Path:
    """This game's log folder: the launcher writes <TITLEID>/<TITLEID>.log inside it.

    One folder per title keeps the shape of every other kind (`<kind>/<TITLEID>/…`),
    and the engine names the file itself.
    """
    return game_root(library, title_id) / "logs"


def mods_dir(library: Path, title_id: str) -> Path:
    return game_root(library, title_id) / "mods"


def cheats_dir(library: Path, title_id: str) -> Path:
    """Cheat files live inside the game's mods folder (NX2Apple keeps them there too)."""
    return mods_dir(library, title_id) / "cheats"


def screenshots_dir(library: Path, title_id: str) -> Path:
    return game_root(library, title_id) / "screenshots"


def support_dir(library: Path, title_id: str) -> Path:
    """The engine's state folder for this game (DM_SUPPORT_DIR)."""
    return game_root(library, title_id) / "support"


def build_dir(library: Path, title_id: str) -> Path:
    """The engine's work folder for this game."""
    return game_root(library, title_id) / "build"


def support_env_path(support: Path) -> Path:
    """The env.txt the engine's launcher reads for this game (after the baked one)."""
    return support / "env.txt"


def build_env_path(support: Path) -> Path:
    """Where the env file handed to the engine at build time is written."""
    return support / BUILD_ENV_NAME


def ensure_layout(library: Path, title_id: str) -> Path:
    """Create every folder this game uses in the library, and mark the library."""
    mark_library(library)
    for directory in (
        game_root(library, title_id),
        save_dir(library, title_id),
        logs_dir(library, title_id),
        mods_dir(library, title_id),
        cheats_dir(library, title_id),
        screenshots_dir(library, title_id),
        support_dir(library, title_id),
        build_dir(library, title_id),
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return support_dir(library, title_id)


def write_build_env(library: Path, title_id: str) -> Path:
    """The env file baked into the app: it points the engine's data at the library.

    The launcher reads this file before it decides where its support dir and log go,
    so `DM_SUPPORT_DIR` and `DM_LOG_DIR` here keep saves, logs, cheats, screenshots
    and its own state inside the library (one folder per title ID) instead of ~/Library.
    """
    support = ensure_layout(library, title_id)
    managed = {
        "DM_SUPPORT_DIR": str(support),
        "DM_LOG_DIR": str(logs_dir(library, title_id)),
        "DM_SAVE": str(save_dir(library, title_id)),
        "DM_PSO_USER": str(pso_user_path(library, title_id)),
        "DM_TEMP": str(support / "temp"),
        "DM_ALBUM_DIR": str(screenshots_dir(library, title_id)),
        "DM_CHEATS": str(cheats_dir(library, title_id)),
    }
    path = build_env_path(support)
    lines = [ENV_HEADER.rstrip(), *[f"{key}={value}" for key, value in managed.items()]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def baked_env_path(app: Path) -> Path:
    """The env file inside a built app (the engine copies our --env-file there)."""
    return app / "Contents" / "Resources" / "dm" / "env.txt"


def bake_env(app: Path, env_file: Path) -> Path | None:
    """Refresh the app's baked env (after the game's folder moved), or None."""
    target = baked_env_path(app)
    if not target.parent.is_dir():
        return None
    shutil.copyfile(env_file, target)
    return target


def cloud_value(cloud_dir: Path | None, player_id: str | None) -> str | None:
    """The DM_SAVE_SYNC value for one game (and player), or None to leave it off.

    Each player gets a folder of its own under the cloud root, so two players never
    fight over one `<cloud>/<TITLEID>/save.dmsync`.
    """
    if cloud_dir is None:
        return None
    root = cloud_dir.expanduser()
    return str(root / "players" / player_id) if player_id else str(root)


def mod_romfs(library: Path, title_id: str) -> list[Path]:
    """Every RomFS folder of this game's mods, in the order the engine should overlay them.

    A mod is `<mods>/<name>/` holding `romfs` (or an Atmosphère layout,
    `atmosphere/contents/<TID>/romfs`, or `contents/<TID>/romfs`), like NX2Apple's mods.
    """
    root = mods_dir(library, title_id)
    found: list[Path] = []
    if not root.is_dir():
        return found
    for mod in sorted(root.iterdir()):
        if not mod.is_dir() or mod.name == "cheats" or mod.name.startswith("."):
            continue
        for candidate in (
            mod / "romfs",
            mod / "atmosphere" / "contents" / title_id / "romfs",
            mod / "contents" / title_id / "romfs",
        ):
            if candidate.is_dir():
                found.append(candidate)
                break
    return found


def write_player_env(library: Path, title_id: str, cloud_dir: Path | None = None) -> Path:
    """Point the engine's saves at the *active player's* store, and name them.

    Each player is user 1 in their own store under `save/<TID>/profiles/`, which is
    the only way to give every player their own name (the engine hard-codes
    "Player 2".. and reads DM_NICK for user 1 only). This file is read after the
    baked one, so it wins. Keys nx2mac does not manage are preserved.
    """
    support = ensure_layout(library, title_id)
    path = support_env_path(support)

    managed: dict[str, str] = {
        "DM_SAVE": str(save_dir(library, title_id)),
    }
    player = active_player(library)
    if player is not None:
        store = player_save_dir(library, title_id, player.id)
        store.mkdir(parents=True, exist_ok=True)
        managed["DM_SAVE"] = str(store)
        managed[PLAYER_NICK_ENV] = player.name
    overlays = mod_romfs(library, title_id)
    if overlays:
        managed[MODS_ENV] = ":".join(str(path) for path in overlays)
    cloud = cloud_value(cloud_dir, player.id if player is not None else None)
    if cloud is not None:
        managed[CLOUD_ENV] = cloud
        (cloud_dir.expanduser() if cloud_dir else Path(cloud)).mkdir(parents=True, exist_ok=True)

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
    manifest_file: Path,
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
    """Record what was built (and from which engine) next to the app.

    `manifest_file` is either an app's sidecar (`<Game>.nx2mac.json`) or a game
    folder (where it becomes `nx2mac.json`).
    """
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
    }
    path = manifest_file if manifest_file.suffix == ".json" else manifest_file / MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_manifest(path: Path) -> dict[str, object] | None:
    """A manifest (a game folder's `nx2mac.json` or an app's sidecar), or None."""
    if path.suffix == ".json":
        manifest_path = path
    else:
        manifest_path = path / MANIFEST_NAME
    path = manifest_path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def manifest_path_for(app: Path) -> Path:
    """The manifest that belongs to an app filed directly in the library root."""
    return app.with_suffix(".nx2mac.json")


def _entry(directory: Path, app: Path | None, manifest_path: Path | None = None) -> GameEntry:
    """One library entry, from its manifest when present, else from the app itself."""
    manifest = read_manifest(manifest_path or directory) or {}
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
    """An older per-game folder: a manifest or an app bundle inside it."""
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
    base = app_root(library)
    if not base.is_dir():
        return entries

    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        if not _is_game_folder(directory):
            continue
        entry = _entry(directory, next(iter(sorted(directory.glob("*.app"))), None))
        entries.append(replace(entry, library=library))


    for app in sorted(base.glob("*.app")):
        entry = _entry(base, app, manifest_path=app.with_suffix(".nx2mac.json"))
        entries.append(replace(entry, library=library))

    # "Recently played" first (like NX2Apple), then newest build, then name.
    entries.sort(key=lambda entry: (entry.last_played or "", entry.built_at or "", entry.name), reverse=True)
    return entries


def find_entry_for_input(library: Path, game_path: Path) -> Path | None:
    """The library folder a previous build of this same input was filed into."""
    wanted = str(game_path)
    for entry in list_games(library):
        manifest = read_manifest(entry.path) or {}
        inputs = manifest.get("inputs")
        if isinstance(inputs, dict) and str(inputs.get("game") or "") == wanted:
            return entry.path
    return None


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
        candidates = {entry.path.resolve(), (entry.app_path or entry.path).resolve()}
        if wanted_path in candidates or wanted_path in {app_root(library), data_root(library)}:
            return entry
        if wanted in {entry.path.name.lower(), entry.name.lower(), (entry.title_id or "").lower()}:
            return entry
    raise LibraryError(f"no game called {needle!r} in {library}")


def touch_last_played(manifest_file: Path) -> str:
    """Record that the game was launched (keeps "recently played" ordering honest)."""
    path = manifest_file if manifest_file.suffix == ".json" else manifest_file / MANIFEST_NAME
    manifest = read_manifest(manifest_file) or {}
    stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    manifest["last_played"] = stamp
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stamp


def describe_library(library: Path) -> dict[str, object]:
    """JSON payload for one library."""
    games = list_games(library)
    return {
        "dir": str(library),
        "exists": library.is_dir(),
        "games": [entry.to_dict() for entry in games],
    }


def cheat_builds(library: Path, title_id: str) -> list[dict[str, object]]:
    """The cheat files of one game, with their switches and which are on.

    A cheat file is `<mods>/cheats/<build id>.txt`; the engine remembers the switches
    that are on in `<build id>.enabled`, one cheat name per line (hle_cheats.c).
    """
    folder = cheats_dir(library, title_id)
    builds: list[dict[str, object]] = []
    if not folder.is_dir():
        return builds
    for txt in sorted(folder.glob("*.txt")):
        build = txt.stem
        enabled_file = folder / f"{build}.enabled"
        enabled: set[str] = set()
        if enabled_file.is_file():
            enabled = {line.strip() for line in enabled_file.read_text(encoding="utf-8").splitlines() if line.strip()}
        names: list[str] = []
        for line in txt.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]") and "--" not in stripped:
                names.append(stripped[1:-1])
        builds.append(
            {
                "build": build,
                "file": str(txt),
                "enabled_file": str(enabled_file),
                "cheats": [{"name": name, "enabled": name in enabled} for name in names],
            }
        )
    return builds


def set_cheat(library: Path, title_id: str, build: str, name: str, enabled: bool) -> Path:
    """Switch one cheat on/off in the build's `.enabled` file (what the in-game menu writes)."""
    folder = cheats_dir(library, title_id)
    path = folder / f"{build}.enabled"
    current: list[str] = []
    if path.is_file():
        current = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    names = [line for line in current if line.casefold() != name.casefold()]
    if enabled:
        names.append(name)
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in names), encoding="utf-8")
    return path


def describe_mods(library: Path) -> list[dict[str, object]]:
    """Every mod found in the library, for `nx2mac mods --json`."""
    entries: list[dict[str, object]] = []
    for game in list_games(library):
        if not game.title_id:
            continue
        overlays = {path.parent for path in mod_romfs(library, game.title_id)}
        root = mods_dir(library, game.title_id)
        if not root.is_dir():
            continue
        for mod in sorted(root.iterdir()):
            if not mod.is_dir() or mod.name == "cheats" or mod.name.startswith("."):
                continue
            entries.append(
                {
                    "game": game.name,
                    "title_id": game.title_id,
                    "name": mod.name,
                    "path": str(mod),
                    "active": any(mod == overlay or mod in overlay.parents for overlay in overlays),
                }
            )
    return entries


def describe_libraries(libraries: Iterable[Path]) -> dict[str, object]:
    """JSON payload for `nx2mac library --json`: every library and the games in them."""
    dirs = [path.expanduser().resolve() for path in libraries]
    games: list[dict[str, object]] = []
    for directory in dirs:
        for entry in list_games(directory):
            payload = entry.to_dict()
            payload["library"] = str(directory)
            games.append(payload)
    games.sort(key=lambda game: (str(game.get("last_played") or ""), str(game.get("built_at") or "")), reverse=True)
    return {
        "dirs": [str(directory) for directory in dirs],
        "missing": [str(directory) for directory in dirs if not directory.is_dir()],
        "games": games,
    }


def write_envs_for(libraries: Iterable[Path], cloud_dir: Path | None = None) -> list[Path]:
    """Rewrite the player/cloud env of every game in every library."""
    written: list[Path] = []
    for directory in libraries:
        if directory.is_dir():
            written.extend(write_all_player_envs(directory, cloud_dir))
    return written

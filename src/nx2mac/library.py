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
from collections.abc import Iterable, Sequence
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


def mod_overlay(mod: Path, title_id: str) -> Path | None:
    """The RomFS folder a mod overrides, or None when it has none (a code-only mod).

    The shapes NX2Apple accepts (GameMod.list): `romfs/`, an Atmosphère layout
    (`atmosphere/contents/<TID>/romfs`, `contents/<TID>/romfs`), or the mod folder itself when its
    files sit directly in it. A folder holding only `exefs/` (and the Atmosphère wrapper) changes
    the game's code, which the rewriter cannot take — it has no overlay.
    """
    for candidate in (
        mod / "romfs",
        mod / "atmosphere" / "contents" / title_id / "romfs",
        mod / "contents" / title_id / "romfs",
    ):
        if candidate.is_dir():
            return candidate
    top = [entry.name.lower() for entry in mod.iterdir() if not entry.name.startswith(".")]
    if not top or all(name in {"exefs", "atmosphere", "contents"} for name in top):
        return None  # code only, or empty
    return mod


def mod_romfs(library: Path, title_id: str) -> list[Path]:
    """Every RomFS folder of this game's mods, in the order the engine should overlay them."""
    root = mods_dir(library, title_id)
    found: list[Path] = []
    if not root.is_dir():
        return found
    for mod in sorted(root.iterdir()):
        if not mod.is_dir() or mod.name == "cheats" or mod.name.startswith("."):
            continue
        overlay = mod_overlay(mod, title_id)
        if overlay is not None:
            found.append(overlay)
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


def pack_key_for(app: Path) -> Path:
    """The engine's packing fingerprint beside a built app (`<App>.app.key`)."""
    return app.with_name(app.name + ".key")


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


#: One cheat file's entries, parsed the way the host does (hle_cheats.c parse_file, mirrored by NX2Apple's CheatFiles.parse).
CHEAT_NAME_MAX = 127  # bytes of a [Name] header the host keeps (NAME_MAX 128 with its terminator)
CHEAT_MAX = 256  # entries in one file; the host skips the rest
_HEX = set("0123456789abcdefABCDEF")
_WS = " \t\r"


def _section_marker(name: str) -> tuple[bool, str] | None:
    """`--SectionStart:Name--` / `--SectionEnd:Name--`, as EdiZon and Atmosphere packs write them."""
    if not name.startswith("--Section"):
        return None
    body = name.strip("-")
    for prefix, start in (("SectionStart:", True), ("SectionEnd:", False)):
        if body.startswith(prefix):
            return start, body[len(prefix):]
    return None


def _meta_entries(meta: str) -> dict[str, dict[str, str]]:
    """The rows of a `<build id>.meta.tsv` keyed by cheat name (cheat_anchor.py writes it)."""
    rows: dict[str, dict[str, str]] = {}
    header: list[str] | None = None
    for line in meta.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if header is None:
            header = fields
            if "name" not in header:
                return {}
            continue
        row = dict(zip(header, fields))
        if row.get("kind") == "unresolved":
            continue
        rows[row.get("name", "")] = row
    return rows


def parse_cheat_file(text: str, meta: str | None = None) -> list[dict[str, object]]:
    """Parse an Atmosphère cheat file into entries with the keys the host uses in `.enabled`.

    Mirrors the host (hle_cheats.c parse_file, as NX2Apple's CheatFiles does): `[Name]` is a cheat,
    `{Name}` a master (always on, never listed), a header with no code words is a label, a duplicate
    name's later entries get the key `Name #2`, and an entry mixing code with prose is broken (kept
    off). A `.meta.tsv` beside the file marks one-pass cheats and on/off pairs.
    """
    glued: list[str] = []
    for index, char in enumerate(text):
        if (
            char in "[{"
            and index >= 8
            and all(c in _HEX for c in text[index - 8:index])
            and (index == 8 or text[index - 9] in _WS + "\n")
        ):
            glued.append("\x0b")  # a header glued to a word ("00000008[Next]") starts on its own
        glued.append(char)

    entries: list[dict[str, object]] = []
    section = ""
    for piece in "".join(glued).split("\n"):
        for part in piece.split("\x0b"):
            line = part.strip(_WS)
            entry: dict[str, object] | None = None
            if line[:1] in ("[", "{"):
                closing = "]" if line[0] == "[" else "}"
                end_of_name = line.find(closing, 1)
                if end_of_name <= 1 or len(entries) >= CHEAT_MAX:
                    continue  # an empty/unclosed header (and its lines) belongs to nothing
                raw_name = line[1:end_of_name][:CHEAT_NAME_MAX]
                marker = _section_marker(raw_name)
                if marker is not None:
                    if marker[0]:
                        section = marker[1]
                    entry = {"name": raw_name, "key": raw_name, "kind": "label", "section": section,
                             "broken": False, "once": False, "pair_off": False}
                    if not marker[0]:
                        section = ""
                else:
                    kind = "master" if line[0] == "{" else "cheat"
                    earlier = sum(1 for e in entries if e.get("name") == raw_name)
                    entry = {"name": raw_name, "key": raw_name if earlier == 0 else f"{raw_name} #{earlier + 1}",
                             "kind": kind, "section": section, "broken": False, "once": False, "pair_off": False}
                line = line[end_of_name + 1:].strip(_WS)
            else:
                entry = entries[-1] if entries else None
                if entry is None:
                    continue

            words = 0
            prose = False
            odd = False
            for token in line.split():
                if not all(c in _HEX for c in token):
                    prose = True
                elif len(token) % 8 != 0:
                    odd = True
                else:
                    words += len(token) // 8

            if words:
                entry["broken"] = bool(prose or odd)
                entry["words"] = int(entry.get("words", 0)) + words
                entries.append(entry) if entry not in entries else None
            elif prose or odd:
                entry["broken"] = True
                entries.append(entry) if entry not in entries else None
            elif line:
                continue
            elif entry not in entries:
                entries.append(entry)  # a header with no code words: a label (or the master's name)

    for entry in entries:
        if entry["kind"] != "master" and not entry["broken"] and not entry.get("words"):
            entry["kind"] = "label"

    if meta:
        rows = _meta_entries(meta)
        for entry in entries:
            row = rows.get(str(entry["name"]))
            if not row:
                continue
            entry["resolved"] = row.get("resolved", "")
            entry["meta_kind"] = row.get("kind", "")
            if row.get("once", "").lower() in ("1", "true"):
                entry["once"] = True
            if row.get("pair", ""):
                entry["pair_off"] = row.get("kind") == "off"

    for entry in entries:
        entry.setdefault("resolved", "")
        entry.setdefault("meta_kind", "")
        entry["switchable"] = (
            entry["kind"] == "cheat" and not entry["broken"] and not entry["once"] and not entry["pair_off"]
        )
        entry.pop("words", None)
    return entries


def cheat_blocked_reason(cheat: dict[str, object], *, force: bool = False) -> str | None:
    """Why this entry must not be switched on here, or None when it is allowed.

    Evidence blocks an entry:
    * the file has not been compiled yet — nothing knows what its addresses mean (build once);
    * the compile could not anchor it (`no anchors`) — its addresses belong to another build, so a
      write lands on unrelated code (that is what crashed Dead Cells);
    * `cheat_anchor.py` marked it an instruction patch (`kind=patch`: new code in a cave plus the
      patched word). The Mac port runs the code its *rewriter* produced, so the original
      instruction is not there to patch: the write either does nothing or takes the game down
      (Dead Cells ran a single patch cheat and exited 132, then started in safe mode with every
      cheat off);
    * it is not a switchable cheat at all (a label, a one-pass entry, the off half of a pair).

    `kind=code` (a cheat whose own lines are code, like Metal Slug Tactics' ZR cheats) stays
    allowed: those anchored `exact` and work. `cheat_warning` still mentions the risk, and `--force`
    overrides any block for a one-off experiment.
    """
    if cheat.get("meta_kind") in (None, "") and cheat.get("resolved") in (None, ""):
        return "not checked yet — build the game once so the cheats can be anchored"
    if cheat.get("resolved") == "no anchors":
        return "could not be anchored to this build"
    if cheat.get("meta_kind") == "patch" and not force:
        return "an instruction patch — this port runs the code its rewriter produced, so it cannot apply"
    if not cheat.get("switchable", True):
        return "not a switchable cheat"
    return None


def cheat_warning(cheat: dict[str, object]) -> str:
    """A caution for an allowed entry that rewrites the game's code, or ""."""
    if cheat.get("meta_kind") in ("patch", "code"):
        return "writes code — anchored for this build, but the port may have rewritten it"
    return ""


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
        meta_file = folder / f"{build}.meta.tsv"
        meta = meta_file.read_text(encoding="utf-8", errors="replace") if meta_file.is_file() else None
        cheats = parse_cheat_file(txt.read_text(encoding="utf-8", errors="replace"), meta)
        rows = [
            {
                "name": cheat["name"],
                "key": cheat["key"],
                "kind": cheat["kind"],
                "section": cheat["section"],
                "broken": cheat["broken"],
                "switchable": cheat["switchable"],
                "enabled": cheat["key"] in enabled,
                "resolved": cheat["resolved"],
                "unanchored": cheat["resolved"] == "no anchors",
                "code_patch": cheat["meta_kind"] == "patch",
                "meta_kind": cheat["meta_kind"],
            }
            for cheat in cheats
        ]
        for row in rows:
            row["blocked"] = cheat_blocked_reason(row) if row["switchable"] else None
            row["warning"] = cheat_warning(row) if row["blocked"] is None and row["switchable"] else ""
        builds.append(
            {
                "build": build,
                "file": str(txt),
                "enabled_file": str(enabled_file),
                "cheats": rows,
            }
        )
    return builds


def safe_mode_marker(library: Path, title_id: str) -> Path:
    """Left behind by a run that ended uncleanly with cheats on (the engine starts them off)."""
    return cheats_dir(library, title_id) / f".cheats-running-{title_id}"


def master_off_marker(library: Path, title_id: str) -> Path:
    """The engine's master switch: while it exists, no cheat runs at all."""
    return cheats_dir(library, title_id) / f".cheats-off-{title_id}"


def cheats_state(library: Path, title_id: str) -> dict[str, object]:
    """Whether the engine is in safe mode and whether the master switch is off."""
    return {
        "safe_mode": safe_mode_marker(library, title_id).exists(),
        "master_off": master_off_marker(library, title_id).exists(),
    }


def set_cheats_master(library: Path, title_id: str, off: bool) -> Path:
    """Turn every cheat off (the engine's master switch file) or back on."""
    marker = master_off_marker(library, title_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if off:
        marker.write_text("", encoding="utf-8")
    else:
        marker.unlink(missing_ok=True)
    return marker


def clear_safe_mode(library: Path, title_id: str) -> bool:
    """Forget the unclean-exit marker so the saved switches apply again."""
    marker = safe_mode_marker(library, title_id)
    existed = marker.exists()
    marker.unlink(missing_ok=True)
    return existed


def extracted_main(library: Path, title_id: str) -> Path | None:
    """The game's `ExeFS/main` from the last build (what cheat_anchor.py anchors addresses in)."""
    build = game_root(library, title_id) / "build" / "extract"
    if not build.is_dir():
        return None
    candidates = sorted(build.glob("*/ExeFS/main"), reverse=True)
    return candidates[0] if candidates else None


def cheat_anchor_script(engine_root: Path) -> Path | None:
    """The engine's tool that re-anchors a cheat file to a build (`scripts/cheat_anchor.py`)."""
    script = engine_root / "scripts" / "cheat_anchor.py"
    return script if script.is_file() else None


def build_id_of_app(app: Path | None) -> str | None:
    """The game build ID a packed Mac app records (`# BUILD_ID <hex>` in dm/main.fixups.tsv)."""
    if app is None:
        return None
    fixups = app / "Contents" / "Resources" / "dm" / "main.fixups.tsv"
    try:
        for line in fixups.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# BUILD_ID"):
                candidate = line.split()[-1].strip().upper()
                return candidate if _is_build_id(candidate) else None
    except OSError:
        return None
    return None


def import_cheats(library: Path, title_id: str, sources: Sequence[Path], enable: bool = False) -> list[Path]:
    """Copy a cheat file in, replacing whatever was there (one cheat file per game)."""
    """Copy cheat files (`<build id>.txt`, and a sibling `.meta.tsv`) into the game's folder.

    Exactly what NX2Apple's "Import" does: the host reads them at the next launch. A file whose
    name is not a build ID, or that belongs to another game's build, is refused.
    """
    folder = cheats_dir(library, title_id)
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in sources:
        path = source.expanduser()
        if not path.is_file():
            raise LibraryError(f"no such cheat file: {path}")
        stem = path.stem
        if path.suffix.lower() == ".tsv" and stem.endswith(".meta"):
            stem = stem[: -len(".meta")]
        if not _is_build_id(stem):
            # A pasted/renamed file: name it after the built game's build ID, which is what the host
            # matches the file against (the app records it in dm/main.fixups.tsv).
            detected = build_id_of_app(next(iter(sorted(app_root(library).glob("*.app"))), None))
            if path.suffix.lower() in (".txt", ".tsv") and detected:
                stem = detected
            else:
                raise LibraryError(
                    f"{path.name} is not named after a build ID (16 hex digits, e.g. {detected or '6E35EE0A24029143'}.txt); "
                    "cheat sites name their files that way"
                )
        suffix = ".tsv" if path.name.endswith(".meta.tsv") else path.suffix
        renamed = path.name if path.name.startswith(stem) else f"{stem}{'.meta.tsv' if suffix == '.tsv' else suffix}"
        target = folder / renamed
        data = path.read_text(encoding="utf-8", errors="replace")
        rebased, changed = rebase_cheats(data)
        if changed:
            # log where the caller can see it: the CLI reports this line
            target.write_text(rebased, encoding="utf-8")
        elif target != path:
            target.write_bytes(path.read_bytes())
        written.append(target)
        _keep_single_cheat_file(folder, keep=target.stem)
        if enable:
            enabled_file = folder / f"{stem}.enabled"
            if not enabled_file.exists():
                enabled_file.write_text("", encoding="utf-8")
    return written


def _keep_single_cheat_file(folder: Path, keep: str) -> None:
    """nx2mac keeps one cheat file per game: drop the others (and their side files)."""
    for entry in folder.iterdir():
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.name.split(".")[0] != keep:
            entry.unlink(missing_ok=True)


def clear_cheats(library: Path, title_id: str) -> list[Path]:
    """Remove the game's cheat file, its switches and its meta file (one file is the rule)."""
    folder = cheats_dir(library, title_id)
    removed: list[Path] = []
    if not folder.is_dir():
        return removed
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and not entry.name.startswith(".cheats-"):
            entry.unlink(missing_ok=True)
            removed.append(entry)
    return removed


#: Where a Switch maps the game's `main` module; cheat files written as absolute addresses assume it.
SWITCH_MAIN_BASE = 0x08000000
#: A sane upper bound for an offset inside main (the largest main images are ~120 MB).
MAIN_OFFSET_LIMIT = 0x0C000000


def rebase_cheats(text: str, base: int = SWITCH_MAIN_BASE) -> tuple[str, int]:
    """Turn absolute addresses into main offsets, as the engine's tools expect.

    A cheat file written for a Switch often carries absolute addresses (`0813AA10` = main at
    `0x08000000` + `0x13AA10`). `cheat_anchor.py` and the Mac app work in offsets, so those
    addresses look "outside the main NSO image". Subtracting the base gives `13AA10`, which
    resolves in any build.
    """
    out: list[str] = []
    changed = 0
    for line in text.splitlines():
        words = line.split()
        if words and all(len(word) == 8 and all(c in _HEX for c in word) for word in words):
            fixed: list[str] = []
            for index, word in enumerate(words):
                value = int(word, 16)
                if index > 0 and base <= value < base + MAIN_OFFSET_LIMIT:
                    fixed.append(f"{value - base:08X}")
                    changed += 1
                else:
                    fixed.append(word)
            out.append(" ".join(fixed))
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), changed


def neutralise_unanchored(text: str, meta: str | None) -> tuple[str, list[str]]:
    """Drop the code of entries a compile could not anchor, keeping their names.

    A master (`{Name}`) is applied on every launch, so an unanchored master writes to addresses that
    mean something else on this port — that is a crash, not a cheat. The entry keeps its header, so
    it still shows in the game's Cheats menu as a label, but it has no code words left to run.
    """
    if not meta:
        return text, []
    rows = _meta_entries(meta)
    bound = {name for name, row in rows.items() if row.get("resolved") == "no anchors"}
    if not bound:
        return text, []

    kept: list[str] = []
    dropped: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped[:1] in ("[", "{"):
            closing = "]" if stripped[0] == "[" else "}"
            end = stripped.find(closing, 1)
            name = stripped[1:end] if end > 1 else ""
            skipping = name in bound
            if skipping:
                dropped.append(name)
                kept.append(line)  # the header stays: the menu lists it, nothing runs
            else:
                kept.append(line)
            continue
        if skipping:
            continue  # its code words go
        kept.append(line)
    return "\n".join(kept) + ("\n" if text.endswith("\n") else ""), dropped


def _is_build_id(text: str) -> bool:
    return len(text) == 16 and all(char in "0123456789abcdefABCDEF" for char in text)


def set_cheat(library: Path, title_id: str, build: str, name: str, enabled: bool, force: bool = False) -> Path:
    """Switch one cheat on/off in the build's `.enabled` file (what the in-game menu writes).

    `name` may be the cheat's name or its key (`Name #2` for a duplicate); the file is rewritten in
    the cheat file's own order, as the host does, and only switchable entries are listed.
    """
    folder = cheats_dir(library, title_id)
    path = folder / f"{build}.enabled"
    txt = folder / f"{build}.txt"
    meta_file = folder / f"{build}.meta.tsv"
    meta = meta_file.read_text(encoding="utf-8", errors="replace") if meta_file.is_file() else None
    cheats = parse_cheat_file(txt.read_text(encoding="utf-8", errors="replace"), meta) if txt.is_file() else []

    wanted = name.strip()
    matched = [
        cheat
        for cheat in cheats
        if str(cheat["key"]).casefold() == wanted.casefold() or str(cheat["name"]).casefold() == wanted.casefold()
    ]
    # Never switch on something the pipeline knows cannot run (it crashes the game): code patches,
    # entries a compile could not anchor, or a file that has not been checked yet.
    if enabled and not force:
        for cheat in matched:
            reason = cheat_blocked_reason({**cheat, "switchable": cheat.get("switchable", True)}, force=force)
            if reason is not None:
                raise LibraryError(f"{cheat['name']}: {reason}. Pass --force to switch it on anyway.")
    keys = {str(cheat["key"]) for cheat in matched}
    if not keys:
        keys = {wanted}  # no file to read (a hand-written toggle): keep the name as given

    on: set[str] = set()
    if path.is_file():
        on = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    if enabled:
        on |= keys
    else:
        on -= keys

    if cheats:
        ordered = [str(cheat["key"]) for cheat in cheats if cheat["switchable"] and str(cheat["key"]) in on]
        text = "".join(f"{key}\n" for key in ordered)
    else:
        text = "".join(f"{key}\n" for key in sorted(on))
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
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
            overlay = mod_overlay(mod, game.title_id)
            entries.append(
                {
                    "game": game.name,
                    "title_id": game.title_id,
                    "name": mod.name,
                    "path": str(mod),
                    "overlay": str(overlay) if overlay is not None else None,
                    "usable": overlay is not None,
                    "note": "" if overlay is not None else "no romfs (code-only mods cannot be loaded)",
                    "active": overlay is not None and any(
                        mod == candidate or mod in candidate.parents for candidate in overlays
                    ),
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

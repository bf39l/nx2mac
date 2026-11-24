"""Tests for nx2mac.library (per-game folders, manifests, env.txt wiring, scanning)."""

import json
import plistlib
from pathlib import Path

import pytest

from nx2mac.library import (
    GAME_SUBDIRS,
    MANIFEST_NAME,
    MAX_PLAYERS,
    PLAYERS_FILE,
    LibraryError,
    active_player,
    app_metadata,
    create_player,
    delete_player,
    describe_library,
    find_game,
    list_games,
    load_players,
    player_save_dir,
    prepare_game_dir,
    read_manifest,
    require_library,
    safe_name,
    select_player,
    support_env_path,
    touch_last_played,
    write_build_env,
    write_manifest,
    write_player_env,
)


def _library(tmp_path: Path) -> Path:
    library = tmp_path / "Games"
    library.mkdir()
    return library


def _app(directory: Path, name: str = "Mercenaries Wings", title_id: str = "010094500C216000") -> Path:
    app = directory / f"{name}.app"
    (app / "Contents" / "Resources").mkdir(parents=True)
    info = {
        "CFBundleName": name,
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleIdentifier": f"org.nxnative.{title_id.lower()}",
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    (app / "Contents" / "Resources" / "AppIcon.icns").write_bytes(b"icon")
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "dmhost").write_bytes(b"\x00" * 10)
    return app


def test_require_library_insists_it_exists(tmp_path: Path) -> None:
    with pytest.raises(LibraryError, match="no game library set"):
        require_library(None)

    with pytest.raises(LibraryError, match="does not exist"):
        require_library(tmp_path / "nope")
    assert not (tmp_path / "nope").exists()  # never created for the user

    library = _library(tmp_path)
    assert require_library(library) == library.resolve()


def test_safe_name_keeps_letters_of_any_script(tmp_path: Path) -> None:
    # CJK letters survive; only path-hostile characters become dashes
    assert safe_name("1佣兵之翼_伪之不死鸟 [010094500C216000]") == "1佣兵之翼_伪之不死鸟 -010094500C216000"
    assert "佣兵之翼" in safe_name("佣兵之翼")
    assert "/" not in safe_name("a/b:c*d")
    assert safe_name("   ") == "Game"


def test_prepare_game_dir_makes_the_shared_layout(tmp_path: Path) -> None:
    library = _library(tmp_path)

    directory = prepare_game_dir(library, "Dead Cells")

    assert directory == library / "Dead Cells"
    assert all((directory / sub).is_dir() for sub in GAME_SUBDIRS)
    assert directory.parent == library  # created *inside* the chosen library only


def test_app_metadata_reads_the_bundle(tmp_path: Path) -> None:
    app = _app(tmp_path)

    meta = app_metadata(app)

    assert meta == {"name": "Mercenaries Wings", "title_id": "010094500C216000", "version": "1.0.0"}
    assert app_metadata(tmp_path / "missing.app")["title_id"] is None


def test_write_player_env_points_the_engine_at_the_game_folder(tmp_path: Path) -> None:
    library = _library(tmp_path)
    directory = prepare_game_dir(library, "Dead Cells")
    env_path = support_env_path(directory)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("# mine\nDM_FPS_TARGET=60\nDM_SAVE=/somewhere/else\n", encoding="utf-8")

    written = write_player_env(directory)

    lines = written.read_text(encoding="utf-8").splitlines()
    assert written == env_path and directory in env_path.parents
    assert "DM_FPS_TARGET=60" in lines  # user settings survive
    assert f"DM_SAVE={directory / 'saves'}" in lines  # ours wins
    assert f"DM_CHEATS={directory / 'cheats'}" in lines
    assert f"DM_LOG_DIR={directory / 'logs'}" in lines
    assert f"DM_ALBUM_DIR={directory / 'screenshots'}" in lines
    assert [line for line in lines if line.startswith("DM_SAVE=")] == [f"DM_SAVE={directory / 'saves'}"]


def test_manifest_round_trip_and_scan(tmp_path: Path) -> None:
    library = _library(tmp_path)
    older = prepare_game_dir(library, "Older Game")
    newer = prepare_game_dir(library, "Newer Game")
    app = _app(newer, name="Newer Game", title_id="0100646009FBE000")
    write_manifest(
        newer,
        name="Newer Game",
        app_path=app,
        title_id="0100646009FBE000",
        version="1.25.0",
        engine_source="fork",
        engine_ref="fork-customizations",
        engine_commit="69513b66656d",
        patches=("0001-launcher-state-dirs.patch",),
        inputs={"game": "/dumps/Dead Cells", "kind": "switch1"},
    )
    write_manifest(
        older,
        name="Older Game",
        app_path=older / "gone.app",
        title_id=None,
        version=None,
        engine_source="upstream",
        engine_ref="main",
        engine_commit="deadbeef",
        patches=(),
        inputs={},
    )

    # give the entries explicit build times so the ordering test is deterministic
    for directory, stamp in ((older, "2026-01-01T00:00:00+00:00"), (newer, "2026-02-02T00:00:00+00:00")):
        manifest = read_manifest(directory) or {}
        manifest["built_at"] = stamp
        (directory / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    games = list_games(library)

    assert [game.name for game in games] == ["Newer Game", "Older Game"]  # newest build first
    newest = games[0]
    assert newest.version == "1.25.0" and newest.title_id == "0100646009FBE000"
    assert newest.engine_commit == "69513b66656d" and newest.patches == ("0001-launcher-state-dirs.patch",)
    assert newest.has_app and newest.icon_path == app / "Contents" / "Resources" / "AppIcon.icns"
    assert newest.size_bytes > 0
    assert not games[1].has_app  # the app is gone, the entry stays visible


def test_touch_last_played_moves_a_game_to_the_top(tmp_path: Path) -> None:
    library = _library(tmp_path)
    first = prepare_game_dir(library, "First")
    second = prepare_game_dir(library, "Second")
    for directory, name in ((first, "First"), (second, "Second")):
        write_manifest(
            directory,
            name=name,
            app_path=directory / f"{name}.app",
            title_id=None,
            version=None,
            engine_source="upstream",
            engine_ref="main",
            engine_commit="c" * 40,
            patches=(),
            inputs={},
        )

    stamp = touch_last_played(first)

    assert read_manifest(first)["last_played"] == stamp  # type: ignore[index]
    assert list_games(library)[0].name == "First"


def test_find_game_by_name_folder_or_title_id(tmp_path: Path) -> None:
    library = _library(tmp_path)
    directory = prepare_game_dir(library, "Dead Cells")
    write_manifest(
        directory,
        name="Dead Cells",
        app_path=directory / "Dead Cells.app",
        title_id="0100646009FBE000",
        version="1.25.0",
        engine_source="fork",
        engine_ref="fork-customizations",
        engine_commit="c" * 40,
        patches=(),
        inputs={},
    )

    assert find_game(library, "Dead Cells").path == directory
    assert find_game(library, str(directory)).path == directory  # the GUI passes a path
    assert find_game(library, "0100646009fbe000").title_id == "0100646009FBE000"
    with pytest.raises(LibraryError, match="no game called"):
        find_game(library, "Zelda")


def test_describe_library_payload(tmp_path: Path) -> None:
    library = _library(tmp_path)

    payload = describe_library(library)

    assert payload["dir"] == str(library) and payload["exists"] is True and payload["games"] == []
    entry = prepare_game_dir(library, "One")
    (entry / MANIFEST_NAME).write_text(json.dumps({"name": "One"}), encoding="utf-8")
    assert describe_library(library)["games"][0]["name"] == "One"  # type: ignore[index]


def test_create_player_gives_their_own_store_and_name(tmp_path: Path) -> None:
    directory = prepare_game_dir(_library(tmp_path), "Dead Cells")

    player = create_player(directory, "Ada")

    players, active = load_players(directory)
    assert [entry.name for entry in players] == ["Ada"] and active == player.id
    assert player_save_dir(directory, player.id).is_dir()
    assert (directory / PLAYERS_FILE).is_file()


def test_second_player_gets_a_separate_store_and_can_be_selected(tmp_path: Path) -> None:
    directory = prepare_game_dir(_library(tmp_path), "Dead Cells")
    ada = create_player(directory, "Ada")
    grace = create_player(directory, "Grace")

    assert active_player(directory).name == "Grace"  # the newest player plays
    assert player_save_dir(directory, ada.id) != player_save_dir(directory, grace.id)

    select_player(directory, "ada")

    assert active_player(directory).name == "Ada"


def test_player_names_are_validated(tmp_path: Path) -> None:
    directory = prepare_game_dir(_library(tmp_path), "Dead Cells")
    create_player(directory, "Ada")

    with pytest.raises(LibraryError, match="already exists"):
        create_player(directory, "ada")
    with pytest.raises(LibraryError, match="needs a name"):
        create_player(directory, "  ")
    with pytest.raises(LibraryError, match="32 characters"):
        create_player(directory, "x" * 40)
    with pytest.raises(LibraryError, match="no player called"):
        select_player(directory, "Nobody")


def test_player_limit(tmp_path: Path) -> None:
    directory = prepare_game_dir(_library(tmp_path), "Dead Cells")
    for index in range(MAX_PLAYERS):
        create_player(directory, f"P{index}")

    with pytest.raises(LibraryError, match="already has 8 players"):
        create_player(directory, "One Too Many")


def test_delete_player_keeps_the_saves(tmp_path: Path) -> None:
    directory = prepare_game_dir(_library(tmp_path), "Dead Cells")
    ada = create_player(directory, "Ada")
    store = player_save_dir(directory, ada.id)
    (store / "save.dat").write_bytes(b"\x00")

    delete_player(directory, "Ada")

    assert load_players(directory) == ([], None)
    assert (store / "save.dat").is_file()  # nothing is destroyed


def test_env_follows_the_active_player(tmp_path: Path) -> None:
    directory = prepare_game_dir(_library(tmp_path), "Dead Cells")
    create_player(directory, "Ada")
    grace = create_player(directory, "Grace")

    write_player_env(directory)
    text = support_env_path(directory).read_text(encoding="utf-8")

    assert f"DM_SAVE={player_save_dir(directory, grace.id)}" in text
    assert "DM_NICK=Grace" in text

    select_player(directory, "Ada")
    write_player_env(directory)
    text = support_env_path(directory).read_text(encoding="utf-8")
    assert "DM_NICK=Ada" in text
    assert [line for line in text.splitlines() if line.startswith("DM_NICK=")] == ["DM_NICK=Ada"]


def test_env_without_players_uses_the_shared_saves_folder(tmp_path: Path) -> None:
    directory = prepare_game_dir(_library(tmp_path), "Dead Cells")

    write_player_env(directory)

    text = support_env_path(directory).read_text(encoding="utf-8")
    assert f"DM_SAVE={directory / 'saves'}" in text and "DM_NICK=" not in text


def test_build_env_keeps_everything_inside_the_game_folder(tmp_path: Path) -> None:
    directory = prepare_game_dir(_library(tmp_path), "Dead Cells")

    path = write_build_env(directory)
    text = path.read_text(encoding="utf-8")

    assert path.parent == directory / "support"
    assert f"DM_SUPPORT_DIR={directory / 'support'}" in text
    assert f"DM_LOG_DIR={directory / 'logs'}" in text
    assert f"DM_SAVE={directory / 'saves'}" in text
    assert "~/Library" not in text and "/Users/" not in text

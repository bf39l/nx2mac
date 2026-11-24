"""Tests for nx2mac.library (the book-keeping modules; the library layout).

Layout (NX2Apple-style, keyed by title ID):
    <library>/<Game>.app            built app
    <library>/<Game>.nx2mac.json    its manifest
    <library>/save/<TID>/           DM_SAVE (profiles/<player> below it)
    <library>/logs/<TID>.log        DM_LOG_DIR=<library>/logs
    <library>/mods/<TID>/           mods
    <library>/cheats/<TID>/         DM_CHEATS
    <library>/screenshots/<TID>/    DM_ALBUM_DIR
    <library>/support/<TID>/        DM_SUPPORT_DIR (env.txt, temp)
    <library>/build/<TID>_build/    the engine's work folder
"""

import json
import plistlib
from pathlib import Path

import pytest

from nx2mac.library import (
    LIBRARY_SUBDIR,
    MAX_PLAYERS,
    PLAYERS_FILE,
    LibraryError,
    active_player,
    app_metadata,
    build_dir,
    build_env_path,
    cheats_dir,
    check_library_choice,
    cloud_value,
    create_player,
    delete_player,
    describe_library,
    ensure_layout,
    find_game,
    is_library,
    list_games,
    load_players,
    logs_dir,
    mods_dir,
    player_save_dir,
    profiles_dir,
    read_manifest,
    require_library,
    safe_name,
    save_dir,
    screenshots_dir,
    select_player,
    support_dir,
    support_env_path,
    touch_last_played,
    write_build_env,
    write_manifest,
    write_player_env,
)

TID = "0100646009FBE000"


def _library(tmp_path: Path) -> Path:
    """The folder the user picked; nx2mac keeps its files in `<it>/game_lib`."""
    library = tmp_path / "Games"
    library.mkdir()
    return library


def _app(library: Path, name: str = "Dead Cells", title_id: str = TID, version: str = "1.25.0") -> Path:
    app = library / f"{name}.app"
    (app / "Contents" / "Resources" / "dm").mkdir(parents=True)
    info = {
        "CFBundleName": name,
        "CFBundleShortVersionString": version,
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


def test_a_game_dump_cannot_be_the_library(tmp_path: Path) -> None:
    """Choosing the dump you build from would scatter the build into it."""
    dump = tmp_path / "Dumps" / "Dead Cells [0100646009FBE000]"
    dump.mkdir(parents=True)
    (dump / "base.nsp").write_bytes(b"\x00")

    with pytest.raises(LibraryError, match="looks like a game dump"):
        check_library_choice(dump)

    # an empty folder, or one that only holds built apps, is fine
    library = _library(tmp_path)
    assert check_library_choice(library) == library.resolve()
    _app(library / LIBRARY_SUBDIR)
    assert check_library_choice(library) == library.resolve()


def test_safe_name_keeps_letters_of_any_script() -> None:
    assert "佣兵之翼" in safe_name("1佣兵之翼_伪之不死鸟 [010094500C216000]")
    assert "/" not in safe_name("a/b:c*d")
    assert safe_name("   ") == "Game"


def test_ensure_layout_creates_one_folder_per_kind_and_marks_the_library(tmp_path: Path) -> None:
    library = _library(tmp_path)

    support = ensure_layout(library, TID)

    assert support == library / LIBRARY_SUBDIR / TID / "support"
    for directory in (
        save_dir(library, TID),
        logs_dir(library, TID),
        mods_dir(library, TID),
        cheats_dir(library, TID),
        screenshots_dir(library, TID),
        build_dir(library, TID),
    ):
        assert directory.is_dir()
    assert (library / LIBRARY_SUBDIR).is_dir() and is_library(library)
    assert build_dir(library, TID) == library / LIBRARY_SUBDIR / TID / "build"


def test_build_env_points_every_kind_at_the_library(tmp_path: Path) -> None:
    library = _library(tmp_path)

    path = write_build_env(library, TID)
    text = path.read_text(encoding="utf-8")

    assert path == build_env_path(support_dir(library, TID))
    assert f"DM_SUPPORT_DIR={support_dir(library, TID)}" in text
    assert f"DM_LOG_DIR={logs_dir(library, TID)}" in text
    assert f"DM_SAVE={library / LIBRARY_SUBDIR / TID / 'save'}" in text
    assert f"DM_PSO_USER={library / LIBRARY_SUBDIR / TID / 'pso_user.tsv'}" in text
    assert f"DM_CHEATS={cheats_dir(library, TID)}" in text
    assert f"DM_ALBUM_DIR={screenshots_dir(library, TID)}" in text
    assert "~/Library" not in text


def test_players_are_library_wide_and_get_a_store_per_game(tmp_path: Path) -> None:
    """Players belong to the library (NX2Apple does the same), one store per game."""
    library = _library(tmp_path)
    other = _app(library, name="Metal Slug", title_id="01003D901D24A000")
    first = _app(library, name="Dead Cells", title_id=TID)
    for app, tid in ((first, TID), (other, "01003D901D24A000")):
        write_manifest(app.with_suffix(".nx2mac.json"), name=app.stem, app_path=app, title_id=tid,
                       version="1.0", engine_source="fork", engine_ref="main",
                       engine_commit="c" * 40, patches=(), inputs={})

    ada = create_player(library, "Ada")
    grace = create_player(library, "Grace")

    assert load_players(library)[1] == grace.id  # the newest player plays
    assert (library / LIBRARY_SUBDIR / PLAYERS_FILE).is_file()  # one list for the whole library
    for tid in (TID, "01003D901D24A000"):
        assert player_save_dir(library, tid, ada.id) == profiles_dir(library, tid) / "ada" / "save"
        assert player_save_dir(library, tid, ada.id).is_dir()

    select_player(library, "ada")
    assert active_player(library).name == "Ada"


def test_player_names_are_validated(tmp_path: Path) -> None:
    library = _library(tmp_path)
    create_player(library, "Ada")

    with pytest.raises(LibraryError, match="already exists"):
        create_player(library, "ada")
    with pytest.raises(LibraryError, match="needs a name"):
        create_player(library, "  ")
    with pytest.raises(LibraryError, match="32 characters"):
        create_player(library, "x" * 40)
    with pytest.raises(LibraryError, match="no player called"):
        select_player(library, "Nobody")
    for index in range(MAX_PLAYERS - 1):
        create_player(library, f"P{index}")
    with pytest.raises(LibraryError, match="already has 8 players"):
        create_player(library, "One Too Many")


def test_delete_player_keeps_the_saves(tmp_path: Path) -> None:
    library = _library(tmp_path)
    app = _app(library / LIBRARY_SUBDIR)  # a built game, so the player gets a store there
    write_manifest(app.with_suffix(".nx2mac.json"), name="Dead Cells", app_path=app, title_id=TID,
                   version="1.0", engine_source="fork", engine_ref="main",
                   engine_commit="c" * 40, patches=(), inputs={})
    ada = create_player(library, "Ada")
    store = player_save_dir(library, TID, ada.id)
    (store / "save.dat").write_bytes(b"\x00")

    delete_player(library, "Ada")

    assert load_players(library) == ([], None)
    assert (store / "save.dat").is_file()


def test_player_env_follows_the_active_player(tmp_path: Path) -> None:
    library = _library(tmp_path)
    grace = create_player(library, "Grace")

    text = write_player_env(library, TID).read_text(encoding="utf-8")
    assert support_env_path(support_dir(library, TID)).is_file()
    assert f"DM_SAVE={player_save_dir(library, TID, grace.id)}" in text
    assert "DM_NICK=Grace" in text

    ada = create_player(library, "Ada")
    text = write_player_env(library, TID).read_text(encoding="utf-8")
    assert "DM_NICK=Ada" in text and "DM_NICK=Grace" not in text
    assert f"DM_SAVE={player_save_dir(library, TID, ada.id)}" in text


def test_player_env_without_players_uses_the_base_store(tmp_path: Path) -> None:
    library = _library(tmp_path)

    text = write_player_env(library, TID).read_text(encoding="utf-8")

    assert f"DM_SAVE={library / LIBRARY_SUBDIR / TID / 'save'}" in text and "DM_NICK=" not in text


def test_app_metadata_reads_the_bundle(tmp_path: Path) -> None:
    app = _app(tmp_path)

    meta = app_metadata(app)

    assert meta == {"name": "Dead Cells", "title_id": TID, "version": "1.25.0"}
    assert app_metadata(tmp_path / "missing.app")["title_id"] is None


def test_root_app_with_sidecar_manifest_is_a_game(tmp_path: Path) -> None:
    library = _library(tmp_path)
    app = _app(library)
    write_manifest(
        app.with_suffix(".nx2mac.json"),
        name="Dead Cells",
        app_path=app,
        title_id=TID,
        version="1.25.0",
        engine_source="fork",
        engine_ref="fork-customizations",
        engine_commit="69513b66656d",
        patches=("0001-launcher-state-dirs.patch",),
        inputs={"game": "/dumps/Dead Cells"},
    )

    games = list_games(library)

    assert len(games) == 1
    game = games[0]
    assert (game.name, game.version, game.title_id) == ("Dead Cells", "1.25.0", TID)
    assert game.has_app and game.icon_path == app / "Contents" / "Resources" / "AppIcon.icns"
    assert game.engine_commit == "69513b66656d" and game.patches == ("0001-launcher-state-dirs.patch",)
    # the data folders come from the library layout, not from beside the app
    assert Path(str(game.to_dict()["saves"])) == library / LIBRARY_SUBDIR / TID / "save"
    assert Path(str(game.to_dict()["logs"])) == logs_dir(library, TID)  # logs/<TID>/<TID>.log
    assert Path(str(game.to_dict()["cheats"])) == cheats_dir(library, TID)


def test_app_without_manifest_still_shows_name_and_version(tmp_path: Path) -> None:
    library = _library(tmp_path)
    _app(library, name="Hand Built", title_id="01003D901D24A000", version="1.0.1")

    game = list_games(library)[0]

    assert (game.name, game.version, game.title_id) == ("Hand Built", "1.0.1", "01003D901D24A000")


def test_only_built_games_are_listed(tmp_path: Path) -> None:
    """A library folder also holds dumps, key files and tool checkouts: none are games."""
    library = _library(tmp_path)
    (library / "0Dead_Cells[0100646009FBE000]").mkdir()
    (library / "0Dead_Cells[0100646009FBE000]" / "base.nsp").write_bytes(b"\x00")
    (library / "nxnative-dev").mkdir()
    (library / "prod.keys").write_text("k\n", encoding="utf-8")
    _app(library, name="Dead Cells")

    names = [game.name for game in list_games(library)]

    assert names == ["Dead Cells"]
    assert not ({"0Dead_Cells[0100646009FBE000]", "nxnative-dev"} & set(names))


def test_legacy_per_game_folder_is_still_listed(tmp_path: Path) -> None:
    library = _library(tmp_path)
    legacy = library / "Old Entry"
    (legacy / "Old.app" / "Contents").mkdir(parents=True)
    (legacy / "Old.app" / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleName": "Old", "CFBundleShortVersionString": "0.9"})
    )

    game = list_games(library)[0]

    assert game.name == "Old" and game.version == "0.9"
    assert str(game.to_dict()["saves"]).startswith(str(legacy))  # its own folder layout


def test_touch_last_played_moves_a_game_to_the_top(tmp_path: Path) -> None:
    library = _library(tmp_path)
    first = _app(library, name="First", title_id="0100000000001000")
    second = _app(library, name="Second", title_id="0100000000002000")
    for app, name in ((first, "First"), (second, "Second")):
        write_manifest(app.with_suffix(".nx2mac.json"), name=name, app_path=app, title_id=None,
                       version=None, engine_source="upstream", engine_ref="main",
                       engine_commit="c" * 40, patches=(), inputs={})

    stamp = touch_last_played(first.with_suffix(".nx2mac.json"))

    assert read_manifest(first.with_suffix(".nx2mac.json"))["last_played"] == stamp  # type: ignore[index]
    assert list_games(library)[0].name == "First"


def test_find_game_by_name_path_or_title_id(tmp_path: Path) -> None:
    library = _library(tmp_path)
    app = _app(library)
    write_manifest(app.with_suffix(".nx2mac.json"), name="Dead Cells", app_path=app, title_id=TID,
                   version="1.25.0", engine_source="fork", engine_ref="main",
                   engine_commit="c" * 40, patches=(), inputs={})

    assert find_game(library, "Dead Cells").app_path == app
    assert find_game(library, TID.lower()).version == "1.25.0"
    assert find_game(library, str(app)).name == "Dead Cells"  # the GUI passes the app path
    with pytest.raises(LibraryError, match="no game called"):
        find_game(library, "Zelda")


def test_describe_library_payload(tmp_path: Path) -> None:
    library = _library(tmp_path)

    payload = describe_library(library)

    assert payload["dir"] == str(library) and payload["exists"] is True and payload["games"] == []
    _app(library, name="One")
    assert describe_library(library)["games"][0]["name"] == "One"  # type: ignore[index]


def test_manifest_round_trip(tmp_path: Path) -> None:
    library = _library(tmp_path)
    app = _app(library)
    path = write_manifest(app.with_suffix(".nx2mac.json"), name="Dead Cells", app_path=app, title_id=TID,
                          version="1.25.0", engine_source="fork", engine_ref="fork-customizations",
                          engine_commit="69513b66656d", patches=(), inputs={"game": "/dumps"})

    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["name"] == "Dead Cells" and manifest["title_id"] == TID
    assert manifest["engine"]["commit"] == "69513b66656d" and manifest["built_at"]


def test_saves_live_in_one_folder_per_title_id(tmp_path: Path) -> None:
    """`save/<TID>/` holds the store, the players and the engine's siblings."""
    library = _library(tmp_path)

    text = write_build_env(library, TID).read_text(encoding="utf-8")
    root = library / LIBRARY_SUBDIR / TID

    assert f"DM_SAVE={root / 'save'}" in text  # save.users/.work stay inside a folder of the game's
    assert f"DM_PSO_USER={root / 'pso_user.tsv'}" in text
    write_player_env(library, TID)
    assert (root / "save").is_dir()


def test_cloud_saves_are_off_until_a_folder_is_chosen(tmp_path: Path) -> None:
    library = _library(tmp_path)
    cloud = tmp_path / "iCloud Drive"

    text = write_player_env(library, TID).read_text(encoding="utf-8")
    assert "DM_SAVE_SYNC=" not in text  # local only

    cloud.mkdir()
    text = write_player_env(library, TID, cloud).read_text(encoding="utf-8")
    assert f"DM_SAVE_SYNC={cloud}" in text  # the engine writes <cloud>/<TID>/save.dmsync


def test_each_player_syncs_to_its_own_cloud_folder(tmp_path: Path) -> None:
    library = _library(tmp_path)
    cloud = tmp_path / "Dropbox"
    cloud.mkdir()
    ada = create_player(library, "Ada")

    text = write_player_env(library, TID, cloud).read_text(encoding="utf-8")

    assert f"DM_SAVE_SYNC={cloud / 'players' / ada.id}" in text
    assert cloud_value(cloud, ada.id) == str(cloud / "players" / ada.id)

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
import shutil
from pathlib import Path

import pytest

from nx2mac.library import (
    LIBRARY_SUBDIR,
    MAX_PLAYERS,
    PLAYERS_FILE,
    LibraryError,
    active_player,
    app_metadata,
    app_supports_relative_paths,
    build_dir,
    build_env_path,
    cheat_builds,
    cheats_dir,
    check_library_choice,
    clear_cheats,
    cloud_value,
    create_player,
    delete_player,
    describe_library,
    ensure_layout,
    find_game,
    GameEntry,
    import_cheats,
    is_library,
    list_games,
    load_players,
    logs_dir,
    mods_dir,
    neutralise_unanchored,
    parse_cheat_file,
    player_save_dir,
    profiles_dir,
    read_manifest,
    require_library,
    safe_name,
    save_dir,
    screenshots_dir,
    select_player,
    set_cheat,
    support_dir,
    support_env_path,
    touch_last_played,
    write_build_env,
    write_manifest,
    write_player_env,
)

TID = "0100646009FBE000"


def _library(tmp_path: Path) -> Path:
    """The folder the user picked; nx2mac keeps its files in `<it>/nx2mac_game_lib`."""
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
    assert f"DM_SUPPORT_DIR={support_dir(library, TID).relative_to(library)}" in text
    assert f"DM_LOG_DIR={logs_dir(library, TID).relative_to(library)}" in text
    assert f"DM_SAVE={(library / LIBRARY_SUBDIR / TID / 'save').relative_to(library)}" in text
    assert f"DM_PSO_USER={(library / LIBRARY_SUBDIR / TID / 'pso_user.tsv').relative_to(library)}" in text
    assert f"DM_CHEATS={cheats_dir(library, TID).relative_to(library)}" in text
    assert f"DM_ALBUM_DIR={screenshots_dir(library, TID).relative_to(library)}" in text
    assert "~/Library" not in text


def test_build_env_absolute_rebake(tmp_path: Path) -> None:
    """relative=False writes absolute paths, for re-baking apps whose launcher predates the relocatable patch."""
    library = _library(tmp_path)

    text = write_build_env(library, TID, relative=False).read_text(encoding="utf-8")

    assert f"DM_SUPPORT_DIR={support_dir(library, TID)}" in text
    assert f"DM_SAVE={(library / LIBRARY_SUBDIR / TID / 'save')}" in text
    assert f"DM_CHEATS={cheats_dir(library, TID)}" in text
    assert "DM_SUPPORT_DIR=nx2mac_game_lib" not in text  # nothing relative


def test_player_env_drops_removed_mods(tmp_path: Path) -> None:
    """Removing a mod removes its DM_ROMFS_OVERLAY entry instead of leaving it stale."""
    library = _library(tmp_path)
    mod = mods_dir(library, TID) / "TestOverlay"
    overlay = mod / "romfs"
    overlay.mkdir(parents=True)

    assert f"DM_ROMFS_OVERLAY={overlay.relative_to(library)}" in write_player_env(library, TID).read_text(encoding="utf-8")

    shutil.rmtree(mod)
    text = write_player_env(library, TID).read_text(encoding="utf-8")
    assert "DM_ROMFS_OVERLAY=" not in text


def test_app_supports_relative_paths_detects_relocatable_launcher() -> None:
    """Only apps built with the relocatable patch may take relative env paths."""
    from dataclasses import replace
    base = GameEntry(name="Game", path=Path("/x"), app_path=None)
    assert app_supports_relative_paths(replace(base, patches=())) is False
    assert app_supports_relative_paths(replace(base, patches=("0003-account-user-picker.patch",))) is False
    assert app_supports_relative_paths(replace(base, patches=("0001-launcher-relocatable-state-dirs.patch",))) is True


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
    assert f"DM_SAVE={player_save_dir(library, TID, grace.id).relative_to(library)}" in text
    assert "DM_NICK=Grace" in text

    ada = create_player(library, "Ada")
    text = write_player_env(library, TID).read_text(encoding="utf-8")
    assert "DM_NICK=Ada" in text and "DM_NICK=Grace" not in text
    assert f"DM_SAVE={player_save_dir(library, TID, ada.id).relative_to(library)}" in text


def test_player_env_without_players_uses_the_base_store(tmp_path: Path) -> None:
    library = _library(tmp_path)

    text = write_player_env(library, TID).read_text(encoding="utf-8")

    assert f"DM_SAVE={(library / LIBRARY_SUBDIR / TID / 'save').relative_to(library)}" in text and "DM_NICK=" not in text


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
        patches=("0001-launcher-relocatable-state-dirs.patch",),
        inputs={"game": "/dumps/Dead Cells"},
    )

    games = list_games(library)

    assert len(games) == 1
    game = games[0]
    assert (game.name, game.version, game.title_id) == ("Dead Cells", "1.25.0", TID)
    assert game.has_app and game.icon_path == app / "Contents" / "Resources" / "AppIcon.icns"
    assert game.engine_commit == "69513b66656d" and game.patches == ("0001-launcher-relocatable-state-dirs.patch",)
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

    assert f"DM_SAVE={(root / 'save').relative_to(library)}" in text  # save.users/.work stay inside a folder of the game's
    assert f"DM_PSO_USER={(root / 'pso_user.tsv').relative_to(library)}" in text
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


def test_clean_never_touches_the_app_its_saves_the_engine_or_a_dump(tmp_path: Path, monkeypatch) -> None:
    """`nx2mac clean` only removes nx2mac's own build/temp folders and the sidecar."""
    from typer.testing import CliRunner

    from nx2mac.cli import app

    library = _library(tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    dump = tmp_path / "Dump" / "Game [0100646009FBE000]"
    dump.mkdir(parents=True)
    (dump / "base.nsp").write_bytes(b"save me")
    engine = cache / "engines" / "bf39l-nxnative_main"
    (engine / "scripts").mkdir(parents=True)
    (engine / "scripts" / "build_ipa_from_nsp.py").write_text("x", encoding="utf-8")

    app_path = _app(library)
    write_manifest(app_path.with_suffix(".nx2mac.json"), name="Dead Cells", app_path=app_path, title_id=TID,
                   version="1.25.0", engine_source="fork", engine_ref="main",
                   engine_commit="c" * 40, patches=(), inputs={"game": str(dump)})
    build = build_dir(library, TID)
    (build / "extract").mkdir(parents=True)
    (build / "extract" / "big.bin").write_bytes(b"\x00" * 4096)
    temp = support_dir(library, TID) / "temp"
    temp.mkdir(parents=True)
    store = save_dir(library, TID)
    store.mkdir(parents=True)
    (store / "save.dat").write_bytes(b"progress")

    runner = CliRunner()
    result = runner.invoke(app, ["clean", "--library", str(library), "--manifests"])

    assert result.exit_code == 0
    assert not build.exists() and not temp.exists()  # nx2mac's own folders go
    assert not app_path.with_suffix(".nx2mac.json").exists()  # asked for with --manifests
    assert app_path.is_dir()  # the game stays
    assert (store / "save.dat").is_file()  # the save stays
    assert (dump / "base.nsp").is_file()  # the dump stays
    assert (engine / "scripts" / "build_ipa_from_nsp.py").is_file()  # the engine stays


def test_cheat_files_and_switches_round_trip(tmp_path: Path) -> None:
    """`nx2mac cheats` reads the file's [Name] headers and the engine's <build>.enabled."""
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text(
        "[Unlimited health]\n01000000 00000000\n[Max gold]\n02000000 00000000\n",
        encoding="utf-8",
    )
    # Both are anchored data writes: the pipeline only allows switching on what it has checked.
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "Unlimited health\tdata\t\t\t\t\t\t\t\t\t0x1000\t\t\n"
        "Max gold\tdata\t\t\t\t\t\t\t\t\t0x2000\t\t\n",
        encoding="utf-8",
    )

    builds = cheat_builds(library, TID)

    assert [b["build"] for b in builds] == ["0123456789ABCDEF"]
    cheats = builds[0]["cheats"]
    assert [c["name"] for c in cheats] == ["Unlimited health", "Max gold"]  # type: ignore[index]
    assert not any(c["enabled"] for c in cheats)  # type: ignore[index]

    path = set_cheat(library, TID, "0123456789ABCDEF", "Max gold", True)
    assert path.read_text(encoding="utf-8") == "Max gold\n"
    assert [c["enabled"] for b in cheat_builds(library, TID) for c in b["cheats"]] == [False, True]  # type: ignore[index]

    set_cheat(library, TID, "0123456789ABCDEF", "Max gold", False)
    assert path.read_text(encoding="utf-8") == ""


def test_cheat_parser_matches_the_host_keys(tmp_path: Path) -> None:
    """Masters, labels, duplicate names and broken entries — the keys `.enabled` must use."""
    text = """{Master code}
01000000 00000000
[Unlimited health]
01000000 00000000
[Max HP]
02000000 00000000
[Max HP]
02000000 00000000
[--SectionStart:Items--]
[Gold x100]
03000000 00000000
[Not a cheat, just a note]
[Broken here]
0G000000 00000000
"""
    entries = parse_cheat_file(text)

    assert [e["kind"] for e in entries] == ["master", "cheat", "cheat", "cheat", "label", "cheat", "label", "cheat"]
    assert [e["key"] for e in entries if e["switchable"]] == ["Unlimited health", "Max HP", "Max HP #2", "Gold x100"]
    assert entries[7]["broken"] is True and entries[7]["switchable"] is False
    assert entries[5]["section"] == "Items"  # the section marker applies to what follows


def test_enabling_a_duplicate_uses_its_hash_key(tmp_path: Path) -> None:
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text(
        "[Max HP]\n02000000 00000000\n[Max HP]\n02000000 00000000\n", encoding="utf-8"
    )
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "Max HP\tdata\t\t\t\t\t\t\t\t\t0x1000\t\t\n"
        "Max HP\tdata\t\t\t\t\t\t\t\t\t0x1004\t\t\n",
        encoding="utf-8",
    )

    set_cheat(library, TID, "0123456789ABCDEF", "Max HP #2", True)

    assert (folder / "0123456789ABCDEF.enabled").read_text(encoding="utf-8") == "Max HP #2\n"


def test_only_one_cheat_file_is_kept(tmp_path: Path) -> None:
    """Importing a cheat file replaces the game's previous one (nx2mac keeps exactly one)."""
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    old = folder / "AAAAAAAAAAAAAAAA.txt"
    old.write_text("[Old]\n01000000 00000000\n", encoding="utf-8")
    (folder / "AAAAAAAAAAAAAAAA.enabled").write_text("Old\n", encoding="utf-8")
    new = tmp_path / "BBBBBBBBBBBBBBBB.txt"
    new.write_text("[New]\n01000000 00000000\n", encoding="utf-8")

    import_cheats(library, TID, [new])

    assert sorted(entry.name for entry in folder.iterdir()) == ["BBBBBBBBBBBBBBBB.txt"]


def test_clear_cheats_removes_the_file_and_its_switches(tmp_path: Path) -> None:
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "AAAAAAAAAAAAAAAA.txt").write_text("[A]\n01000000 00000000\n", encoding="utf-8")
    (folder / "AAAAAAAAAAAAAAAA.enabled").write_text("A\n", encoding="utf-8")
    (folder / f".cheats-off-{TID}").write_text("", encoding="utf-8")

    removed = clear_cheats(library, TID)

    assert len(removed) == 2 and not (folder / "AAAAAAAAAAAAAAAA.txt").exists()
    assert (folder / f".cheats-off-{TID}").exists()  # the master switch is not a cheat file


def test_compiled_cheats_land_in_the_mods_folder(tmp_path: Path) -> None:
    """The cheat file the game reads lives in <TID>/mods/cheats, the re-anchor source in <TID>/mods."""
    library = _library(tmp_path)
    mods = mods_dir(library, TID)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    compiled = folder / "6E35EE0A24029143.txt"
    compiled.write_text("[Max HP]\n02000000 00000000\n", encoding="utf-8")
    toml = mods / f"{TID}.cheats.toml"
    toml.write_text("# addresses by meaning\n", encoding="utf-8")

    import_cheats(library, TID, [compiled])

    assert compiled.is_file()  # exactly where DM_CHEATS points
    assert toml.parent == mods and toml.is_file()  # the source stays in mods/, untouched
    assert folder.parent.name == "mods"


def test_unanchored_cheats_are_marked(tmp_path: Path) -> None:
    """A compile that could not anchor an address marks the entry: not working on this port."""
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text(
        "[Max gold]\n02000000 00000000\n[Key cheat]\n08010000 00000000 00000000 00000000\n",
        encoding="utf-8",
    )
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "Max gold\tdata\t\t\t\t\t\t\t\t\t0x1234\t\t\n"
        "Key cheat\tcode\t\t\t\t\t\t\t\t\tno anchors\t\t\n",
        encoding="utf-8",
    )

    build = cheat_builds(library, TID)[0]
    by_name = {cheat["name"]: cheat for cheat in build["cheats"]}  # type: ignore[index]

    assert by_name["Max gold"]["unanchored"] is False
    assert by_name["Key cheat"]["unanchored"] is True


def test_clean_drops_the_packing_key_and_the_manifest(tmp_path: Path, monkeypatch) -> None:
    """Neither the .app.key nor the nx2mac.json is needed to play: clean removes both."""
    from typer.testing import CliRunner

    import nx2mac.cli as cli_module

    library = _library(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    app_path = _app(library)
    write_manifest(app_path.with_suffix(".nx2mac.json"), name="Dead Cells", app_path=app_path, title_id=TID,
                   version="1.25.0", engine_source="fork", engine_ref="main",
                   engine_commit="c" * 40, patches=(), inputs={})
    key = library / f"{TID}.app.key"
    key.write_text("", encoding="utf-8")
    (build_dir(library, TID) / "extract").mkdir(parents=True)
    CliRunner().invoke(cli_module.app, ["library", "--add", str(library)])

    result = CliRunner().invoke(cli_module.app, ["clean", "--library", str(library), "--manifests"])

    assert result.exit_code == 0
    assert not key.exists()
    assert not app_path.with_suffix(".nx2mac.json").exists()
    assert app_path.is_dir()  # the game stays


def test_unanchored_entries_lose_their_code(tmp_path: Path) -> None:
    """A master that could not be anchored runs on every launch: its code is removed, the name stays."""
    text = (
        "{Credits}\n08010000 00000000 11111111 22222222\n"
        "[Max gold]\n04010000 0013AAA4 00005431\n"
    )
    meta = (
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "Credits\tcode\t\t\t\t\t\t\t\t\tno anchors\t\t\n"
        "Max gold\tdata\t\t\t\t\t\t\t\t\t0x13AAA4\t\t\n"
    )

    cleaned, dropped = neutralise_unanchored(text, meta)

    assert dropped == ["Credits"]
    assert "{Credits}" in cleaned and "11111111" not in cleaned  # header kept, code gone
    assert "04010000 0013AAA4 00005431" in cleaned  # the anchored one keeps its code


def test_import_names_a_pasted_file_after_the_build_id(tmp_path: Path) -> None:
    """A pasted cheat file is stored as <build id>.txt, which is what the host matches."""
    library = _library(tmp_path)
    app = _app(library)
    (app / "Contents" / "Resources" / "dm").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Resources" / "dm" / "main.fixups.tsv").write_text(
        "# BUILD_ID 6E35EE0A24029143\n", encoding="utf-8"
    )
    pasted = tmp_path / "zr-cheats.txt"
    pasted.write_text("[按住ZR 生命值]\n04000000 04800158 4B140108\n", encoding="utf-8")

    written = import_cheats(library, TID, [pasted])

    assert [path.name for path in written] == ["6E35EE0A24029143.txt"]


def test_an_entry_the_compile_could_not_anchor_cannot_be_enabled(tmp_path: Path) -> None:
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text("[Old pack]\n04000000 013AA10 4B140108\n", encoding="utf-8")
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "Old pack\tcode\t\t\t\t\t\t\t\t\tno anchors\t\t\n",
        encoding="utf-8",
    )

    with pytest.raises(LibraryError, match="could not be anchored"):
        set_cheat(library, TID, "0123456789ABCDEF", "Old pack", True)


def test_only_a_switch_that_could_not_be_anchored_is_turned_off(tmp_path: Path) -> None:
    """A switch stays on when it anchored — only `no anchors` is dropped.

    The version that also dropped every `kind=patch` switch is what made a working Dead Cells toggle
    silently disappear after a build.
    """
    from nx2mac.cheats import neutralise

    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text(
        "[HP]\n04000000 04800158 4B140108\n[Old pack]\n02000000 00000000\n[Max gold]\n02000000 00000000\n",
        encoding="utf-8",
    )
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "HP\tpatch\t\t\t\t\t\t\t\t\texact\t\t\n"
        "Old pack\tpatch\t\t\t\t\t\t\t\t\tno anchors\t\t\n"
        "Max gold\tdata\t\t\t\t\t\t\t\t\t0x1234\t\t\n",
        encoding="utf-8",
    )
    (folder / "0123456789ABCDEF.enabled").write_text("HP\nOld pack\nMax gold\n", encoding="utf-8")

    reports = neutralise(folder)

    # the anchored patch and the data cheat stay on; only the entry that could not be anchored goes
    assert (folder / "0123456789ABCDEF.enabled").read_text(encoding="utf-8") == "HP\nMax gold\n"
    assert any("Old pack" in report for report in reports)




def test_a_patch_that_writes_udf_is_blocked(tmp_path: Path) -> None:
    """`kind=patch` + `exact`, but the patched word is `udf #0x5431`.

    That sentinel only works while the master redirects execution around it, and the redirector
    cannot anchor on the Mac, so the `udf` executes and kills the host (Dead Cells' `無限血量`
    exited 132). Anchoring `exact` only proves the tool found the code — not that the write is
    survivable.
    """
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text(
        "[無限血量]\n14050000 0013AA2C B9401109\n04000000 0013AA2C 00005431\n20000000\n",
        encoding="utf-8",
    )
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "無限血量\tpatch\t\t\t\t\t\t\t\t\texact\t\t\n",
        encoding="utf-8",
    )

    listed = cheat_builds(library, TID)[0]["cheats"][0]  # type: ignore[index]
    assert "undefined instruction" in str(listed["blocked"])

    with pytest.raises(LibraryError, match="undefined instruction"):
        set_cheat(library, TID, "0123456789ABCDEF", "無限血量", True)
    set_cheat(library, TID, "0123456789ABCDEF", "無限血量", True, force=True)
    assert (folder / "0123456789ABCDEF.enabled").read_text(encoding="utf-8") == "無限血量\n"


def test_a_code_patch_that_anchored_may_be_enabled_with_a_warning(tmp_path: Path) -> None:
    """Metal Slug's ZR cheats anchor exact: the tool verified the words, so they are allowed."""
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text("[ZR health]\n04000000 04800158 4B140108\n", encoding="utf-8")
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "ZR health\tcode\t\t\t\t\t\t\t\t\texact\t\t\n",
        encoding="utf-8",
    )

    listed = cheat_builds(library, TID)[0]["cheats"][0]  # type: ignore[index]
    assert listed["blocked"] is None and "writes code" in listed["warning"]

    set_cheat(library, TID, "0123456789ABCDEF", "ZR health", True)
    assert (folder / "0123456789ABCDEF.enabled").read_text(encoding="utf-8") == "ZR health\n"


def test_a_pointer_chain_data_cheat_is_not_mistaken_for_code(tmp_path: Path) -> None:
    """The opcode walker must skip the operand words of 5/6/7, else a value word gets read as an
    opcode-0 write and a working HP data cheat is blocked as an `udf` sentinel.

    Dead Cells' EdiZon HP cheat (`580F0000 … 640F0000`) is exactly this: a pointer chain that
    writes a value through main+0x268B4A8, never an instruction.
    """
    from nx2mac.library import entry_code_writes

    compiled = (
        "[HP]\n"
        "580F0000 026F8300\n"
        "580F1000 00000030\n"
        "580F1000 00000098\n"
        "780F0000 0000018C\n"
        "640F0000 00000000 05F5E0FF\n"
    )
    assert entry_code_writes(compiled)["HP"] == []  # no main code write

    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text(compiled, encoding="utf-8")
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "HP\tcode\t\t\t\t\t\t\t\t\t4/4 refs (4 exact) through the slot 0x268B4A8\t\t\n",
        encoding="utf-8",
    )

    listed = cheat_builds(library, TID)[0]["cheats"][0]  # type: ignore[index]
    assert listed["blocked"] is None
    assert listed["warning"] == ""  # a data cheat has nothing to warn about

    set_cheat(library, TID, "0123456789ABCDEF", "HP", True)
    assert (folder / "0123456789ABCDEF.enabled").read_text(encoding="utf-8") == "HP\n"


def test_a_code_write_into_a_changed_word_is_blocked(tmp_path: Path) -> None:
    """`kind=code` anchored `exact`, but the write lands in `main.changed.tsv`: the host refuses it.

    The rewriter lists the text words it changed or moved there; a patch into one would undo a site
    or never run (hle_cheats.c `dm_changed_hit`), so it is blocked before launch.
    """
    library = _library(tmp_path)
    folder = cheats_dir(library, TID)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text("[HP]\n04000000 0013AA2C 4B140108\n", encoding="utf-8")
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "HP\tcode\t\t\t\t\t\t\t\t\texact\t\t\n",
        encoding="utf-8",
    )
    patched = build_dir(library, TID) / "patched_v1"
    patched.mkdir(parents=True)
    (patched / "main.changed.tsv").write_text(
        "# BUILD_ID\t0123456789abcdef\n0x13aa2c\t0x4\n", encoding="utf-8"
    )

    listed = cheat_builds(library, TID)[0]["cheats"][0]  # type: ignore[index]
    assert "changed or moved" in str(listed["blocked"])

    with pytest.raises(LibraryError, match="changed or moved"):
        set_cheat(library, TID, "0123456789ABCDEF", "HP", True)
    set_cheat(library, TID, "0123456789ABCDEF", "HP", True, force=True)
    assert (folder / "0123456789ABCDEF.enabled").read_text(encoding="utf-8") == "HP\n"

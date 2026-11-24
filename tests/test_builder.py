"""Tests for nx2mac.builder (input validation + build orchestration)."""

import json
import os
from pathlib import Path

import pytest
from test_doctor import _good_env

from nx2mac.builder import (
    BuildError,
    build_argv,
    classify_game,
    discover_app,
    find_prod_keys,
    run_build,
    validate_extras,
    work_dir_for,
)
from nx2mac.engines import UPSTREAM, ResolvedEngine

SHA = "c" * 40


def _engine(tmp_path: Path) -> ResolvedEngine:
    return ResolvedEngine(source=UPSTREAM, ref="main", commit=SHA, branch="main", path=tmp_path / "eng")


def _game_dir(root: Path, names=("base.nsp",), name="MyGame") -> Path:
    game = root / name
    game.mkdir()
    for member in names:
        (game / member).write_bytes(b"\x00")
    return game


def test_classify_switch1_nsp(tmp_path: Path) -> None:
    game = _game_dir(tmp_path, ("base.nsp", "update.nsz"))

    result = classify_game(game)
    assert (result.kind, result.needs_prod_keys) == ("switch1", True) and str(result.path).startswith(str(tmp_path))


def test_classify_switch1_xci_and_nso(tmp_path: Path) -> None:
    xci = _game_dir(tmp_path, ("dump.xci", "a.bin"), name="XciDump")
    assert classify_game(xci).kind == "switch1"

    nup_file = tmp_path / "port.nup"
    nup_file.write_bytes(b"\x00")
    result = classify_game(nup_file)
    assert (result.kind, result.needs_prod_keys, result.path) == ("nso", False, nup_file.resolve())

    # a dump folder containing NUPs is also an NSO game
    mixed = _game_dir(tmp_path, ("port.nup", "base.nsp"), name="MixedNup")
    assert classify_game(mixed).kind == "nso"


def test_classify_errors(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="does not exist"):
        classify_game(tmp_path / "nope")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BuildError, match=r"no game files found"):
        classify_game(empty)


def test_find_prod_keys_explicit_and_auto(tmp_path: Path) -> None:
    game = _game_dir(tmp_path)

    assert find_prod_keys(None, game) is None  # nothing anywhere yet

    beside = game.parent / "prod.keys"
    beside.write_text("keys\n", encoding="utf-8")
    assert find_prod_keys(None, game) == beside.resolve()

    inside = game / "prod.keys"
    inside.write_text("keys\n", encoding="utf-8")
    assert find_prod_keys(None, game) == inside.resolve()  # the game folder wins over its parent

    other = tmp_path / "elsewhere.txt"
    with pytest.raises(BuildError, match="not found at"):
        find_prod_keys(other, game)


def test_build_argv_shape(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    game_dir = _game_dir(tmp_path)
    keys = tmp_path / "prod.keys"

    argv = build_argv(tmp_path / "py", engine, game_dir.resolve(), keys, tmp_path / "Out.app", ["--gpu", "g18p"])

    assert argv == [
        str(tmp_path / "py"),
        str(engine.path / "scripts" / "build_ipa_from_nsp.py"),
        str(game_dir.resolve()),
        "-k",
        str(keys),
        "--platform",
        "mac-app",
        "-o",
        str(tmp_path / "Out.app"),
        # extra engine flags pass through verbatim, last
        "--gpu",
        "g18p",
    ]


def test_run_build_happy_path(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)

    game = _game_dir(tmp_path)
    (game / "prod.keys").write_text("keys\n", encoding="utf-8")
    out = tmp_path / "Out.app"

    result = run_build(env, cache, _engine(tmp_path), game, output=out)

    assert result.returncode == 0 and result.app_path == out.resolve()
    assert len(env.stream_calls) == 1
    argv, cwd = env.stream_calls[0]
    assert argv[0].endswith("python3") and str(game) in argv
    assert "--platform" in argv and "mac-app" in argv and "-k" in argv and cwd == _engine(tmp_path).path


def test_run_build_blocked_by_preflight(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    env.which_map.pop("zstd")

    with pytest.raises(BuildError, match=r"failing checks.*zstd"):
        run_build(env, cache, _engine(tmp_path), _game_dir(tmp_path))


def test_run_build_missing_keys_for_switch1(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)

    with pytest.raises(BuildError, match="prod.keys"):
        run_build(env, cache, _engine(tmp_path), _game_dir(tmp_path))


def test_validate_extras_rejects_bad_inputs(tmp_path: Path) -> None:
    good = tmp_path / "update.nsp"
    good.write_bytes(b"\x00")
    assert validate_extras([good], "--update") == [good.resolve()]

    with pytest.raises(BuildError, match="does not exist"):
        validate_extras([tmp_path / "nope.nsp"], "--dlc")

    text = tmp_path / "notes.txt"
    text.write_text("x", encoding="utf-8")
    with pytest.raises(BuildError, match="not a game package"):
        validate_extras([text], "--dlc")

    empty = tmp_path / "EmptyDLC"
    empty.mkdir()
    with pytest.raises(BuildError, match="holds no game packages"):
        validate_extras([empty], "--dlc")


def test_run_build_stages_update_and_multiple_dlcs(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)

    game = _game_dir(tmp_path)
    (game / "prod.keys").write_text("keys\n", encoding="utf-8")
    update = tmp_path / "update.nsp"
    update.write_bytes(b"\x00")
    dlc_folder = tmp_path / "DLC"
    dlc_folder.mkdir()
    for name in ("dlc-a.nsp", "dlc-b.nsp"):
        (dlc_folder / name).write_bytes(b"\x00")

    result = run_build(env, cache, _engine(tmp_path), game, output=tmp_path / "O.app", updates=[update], dlcs=[dlc_folder])

    argv, _cwd = env.stream_calls[0]
    staged = Path(argv[2])
    assert staged != game and staged.is_dir()
    assert staged == result.input_path
    names = {entry.name for entry in staged.iterdir()}
    assert {"base.nsp", "update.nsp", "dlc-a.nsp", "dlc-b.nsp"} <= names
    assert (staged / "010094500C216000_build").parent.exists()  # staging lives in the cache
    assert cache in staged.parents


def test_run_build_passes_game_folder_unchanged_without_extras(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    game = _game_dir(tmp_path)
    (game / "prod.keys").write_text("keys\n", encoding="utf-8")

    result = run_build(env, cache, _engine(tmp_path), game)

    assert result.input_path == game.resolve()
    assert env.stream_calls[0][0][2] == str(game.resolve())


def test_run_build_rejects_extras_for_nup(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    nup = tmp_path / "game.nup"
    nup.write_bytes(b"\x00")
    update = tmp_path / "update.nsp"
    update.write_bytes(b"\x00")

    with pytest.raises(BuildError, match="already carries its own updates and DLC"):
        run_build(env, cache, _engine(tmp_path), nup, updates=[update])


def test_work_dir_for_uses_the_game_name(tmp_path: Path) -> None:
    game = _game_dir(tmp_path, ("base.nsp",), name="Mercenaries Wings [010094500C216000]")
    out = tmp_path / "out"

    work = work_dir_for(out, classify_game(game))

    assert work.parent == out and work.name.endswith("_build")
    assert "Mercenaries-Wings" in work.name and "010094500C216000" in work.name  # CJK/alnum kept, brackets made safe


def test_work_dir_for_keeps_cjk_names(tmp_path: Path) -> None:
    game = _game_dir(tmp_path, ("base.nsp",), name="1佣兵之翼[Mercenaries]")
    work = work_dir_for(tmp_path / "out", classify_game(game))

    assert "佣兵之翼" in work.name and "/" not in work.name


def test_find_prod_keys_prefers_game_folder_then_out_dir(tmp_path: Path) -> None:
    game = _game_dir(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "prod.keys").write_text("out-keys\n", encoding="utf-8")

    assert find_prod_keys(None, game, out) == out / "prod.keys"

    (game / "prod.keys").write_text("game-keys\n", encoding="utf-8")
    assert find_prod_keys(None, game, out) == game / "prod.keys"


def test_run_build_out_dir_collects_app_build_and_keys(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    game = _game_dir(tmp_path)
    keys = tmp_path / "prod.keys"
    keys.write_text("keys\n", encoding="utf-8")
    out = tmp_path / "out"

    def fake_build(argv: list[str]) -> int:  # the engine writes the app next to its work dir
        work = Path(argv[argv.index("--work-dir") + 1])
        (work.parent / "Mercenaries Wings.app").mkdir(parents=True)
        return 0

    env.stream_effects.append((r"build_ipa_from_nsp\.py", fake_build))
    result = run_build(env, cache, _engine(tmp_path), game, prod_keys=keys, out_dir=out)

    argv, _cwd = env.stream_calls[0]
    work_dir = out / f"{game.name}_build"
    assert argv[argv.index("--work-dir") + 1] == str(work_dir)
    assert result.work_dir == work_dir and result.out_dir == out.resolve()
    # the key set is used where it lives - nothing is copied into the output folder
    assert not (out / "prod.keys").exists()
    assert argv[argv.index("-k") + 1] == str(keys.resolve())
    assert result.keys_path == keys.resolve()
    # no -o given: the app is discovered in the output folder
    assert "-o" not in argv and result.app_path == out / "Mercenaries Wings.app"


def test_run_build_explicit_output_still_wins(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    game = _game_dir(tmp_path)
    (game / "prod.keys").write_text("keys\n", encoding="utf-8")
    explicit = tmp_path / "elsewhere" / "Custom.app"

    result = run_build(env, cache, _engine(tmp_path), game, output=explicit, out_dir=tmp_path / "out")

    assert result.app_path == explicit.resolve()
    assert env.stream_calls[0][0][env.stream_calls[0][0].index("-o") + 1] == str(explicit)


def test_discover_app_picks_newest(tmp_path: Path) -> None:
    old = tmp_path / "Old.app"
    new = tmp_path / "New.app"
    old.mkdir()
    new.mkdir()
    (old / "stamp").write_text("x", encoding="utf-8")
    os.utime(old, (1_000_000, 1_000_000))

    assert discover_app(tmp_path) == new
    assert discover_app(tmp_path / "nope") is None


def test_run_build_stages_a_lone_switch1_package_file(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    package = tmp_path / "Game_0100.nsp"
    package.write_bytes(b"\x00")
    keys = tmp_path / "prod.keys"
    keys.write_text("keys\n", encoding="utf-8")

    result = run_build(env, cache, _engine(tmp_path), package, prod_keys=keys)

    argv, _cwd = env.stream_calls[0]
    staged = Path(argv[2])
    assert classify_game(package).kind == "switch1"
    assert staged.is_dir() and staged != package  # the engine needs a folder
    assert (staged / package.name).is_symlink()
    assert result.input_path == staged and staged.parent.parent == cache


def test_run_build_passes_a_nup_file_through(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    nup = tmp_path / "Game.nup"
    nup.write_bytes(b"\x00")

    result = run_build(env, cache, _engine(tmp_path), nup)

    assert result.input_path == nup.resolve()  # the engine accepts a .nup file itself
    assert env.stream_calls[0][0][2] == str(nup.resolve())


def test_staged_build_defaults_output_folder_to_the_game_directory(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    package = tmp_path / "Game_0100.nsp"
    package.write_bytes(b"\x00")
    keys = tmp_path / "prod.keys"
    keys.write_text("keys\n", encoding="utf-8")

    # No out_dir given: the app must NOT end up in the cache staging folder.
    result = run_build(env, cache, _engine(tmp_path), package, prod_keys=keys)

    assert result.out_dir == tmp_path.resolve() and result.work_dir == tmp_path.resolve() / "Game_0100_build"
    # keys are used where they are (no copy into the user's folder)
    assert not (tmp_path / "prod.keys").read_text(encoding="utf-8").startswith("out")
    assert result.keys_path == keys.resolve()


def test_out_dir_never_copies_the_key_set(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    package = tmp_path / "Game_0100.nsp"
    package.write_bytes(b"\x00")
    keys = tmp_path / "prod.keys"
    keys.write_text("keys\n", encoding="utf-8")
    out = tmp_path / "Project"

    result = run_build(env, cache, _engine(tmp_path), package, prod_keys=keys, out_dir=out)

    assert result.keys_path == keys.resolve() and not (out / "prod.keys").exists()


def test_run_build_files_the_game_into_the_library(tmp_path: Path, monkeypatch) -> None:
    """One build fills the library layout, keyed by the title ID."""
    import plistlib

    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    package = tmp_path / "Dead_Cells_0100646009FBE000_v1.0.0.nsp"
    package.write_bytes(b"\x00")
    keys = tmp_path / "prod.keys"
    keys.write_text("keys\n", encoding="utf-8")
    library = tmp_path / "Games"
    library.mkdir()

    def fake_build(argv: list[str]) -> int:  # the engine writes the app where -o says
        app = Path(argv[argv.index("-o") + 1])
        (app / "Contents" / "Resources" / "dm").mkdir(parents=True)
        info = {
            "CFBundleName": "Dead Cells",
            "CFBundleShortVersionString": "1.25.0",
            "CFBundleIdentifier": "org.nxnative.0100646009fbe000",
        }
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
        return 0

    env.stream_effects.append((r"build_ipa_from_nsp\.py", fake_build))
    result = run_build(env, cache, _engine(tmp_path), package, prod_keys=keys, library_dir=library)

    tid = "0100646009FBE000"
    assert result.library_dir == library.resolve()      # the folder the user chose
    assert result.out_dir == library.resolve()           # the app is written there
    # the app is renamed to the game's own name and carries a sidecar manifest
    assert result.app_path == library / "Dead Cells.app"
    manifest = json.loads((library / "Dead Cells.nx2mac.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "Dead Cells" and manifest["title_id"] == tid
    assert manifest["engine"]["commit"] == SHA
    # everything the game writes is in the library, keyed by the title ID
    assert result.work_dir == library / "nx2mac_game_lib" / tid / "build"
    argv = env.stream_calls[0][0]
    assert argv[argv.index("--work-dir") + 1] == str(library / "nx2mac_game_lib" / tid / "build")
    build_env = (library / "nx2mac_game_lib" / tid / "support" / "build-env.txt").read_text(encoding="utf-8")
    assert f"DM_SUPPORT_DIR={library / 'nx2mac_game_lib' / tid / 'support'}" in build_env
    assert f"DM_LOG_DIR={library / 'nx2mac_game_lib' / tid / 'logs'}" in build_env
    assert f"DM_SAVE={library / 'nx2mac_game_lib' / tid / 'save'}" in build_env
    for directory in ("logs", "screenshots", "mods", "save", "support", "build"):
        assert (library / "nx2mac_game_lib" / tid / directory).is_dir()
    # cheats live inside the game's mods folder, as NX2Apple keeps them
    assert (library / "nx2mac_game_lib" / tid / "mods" / "cheats").is_dir()
    assert not (library / "cheats").exists()
    # and the scan sees it
    from nx2mac.library import list_games

    entries = list_games(library)
    assert [entry.name for entry in entries] == ["Dead Cells"] and entries[0].version == "1.25.0"
    assert str(entries[0].to_dict()["saves"]) == str(library / "nx2mac_game_lib" / tid / "save")


def test_rebuilding_replaces_the_app_in_place(tmp_path: Path, monkeypatch) -> None:
    import plistlib

    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    package = tmp_path / "Dead_Cells_0100646009FBE000_v1.0.0.nsp"
    package.write_bytes(b"\x00")
    keys = tmp_path / "prod.keys"
    keys.write_text("keys\n", encoding="utf-8")
    library = tmp_path / "Games"
    library.mkdir()
    (library / "keep.me").write_text("x", encoding="utf-8")

    def fake_build(argv: list[str]) -> int:
        app = Path(argv[argv.index("-o") + 1])
        (app / "Contents" / "Resources" / "dm").mkdir(parents=True)
        info = {"CFBundleName": "Dead Cells", "CFBundleShortVersionString": "1.0.0",
                "CFBundleIdentifier": "org.nxnative.0100646009fbe000"}
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
        return 0

    env.stream_effects.append((r"build_ipa_from_nsp\.py", fake_build))
    run_build(env, cache, _engine(tmp_path), package, prod_keys=keys, library_dir=library)
    second = run_build(env, cache, _engine(tmp_path), package, prod_keys=keys, library_dir=library)

    assert second.app_path == library / "Dead Cells.app"
    assert (library / "keep.me").is_file()  # nothing else clobbered
    assert [p.name for p in library.glob("*.app")] == ["Dead Cells.app"]  # no duplicate


def test_run_build_rejects_a_missing_library(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    env = _good_env(tmp_path)
    game = _game_dir(tmp_path)
    (game / "prod.keys").write_text("keys\n", encoding="utf-8")

    with pytest.raises(Exception, match="does not exist"):
        run_build(env, cache, _engine(tmp_path), game, library_dir=tmp_path / "nope")



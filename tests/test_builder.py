"""Tests for nx2mac.builder (input validation + build orchestration)."""

from pathlib import Path

import pytest
from test_doctor import _good_env

from nx2mac.builder import (
    BuildError,
    build_argv,
    classify_game,
    find_prod_keys,
    run_build,
    validate_extras,
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

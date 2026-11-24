"""Tests for nx2mac.builder (input validation + build orchestration)."""

from pathlib import Path

import pytest
from test_doctor import _good_env

from nx2mac.builder import BuildError, build_argv, classify_game, find_prod_keys, run_build
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

    argv = build_argv(tmp_path / "py", engine, classify_game(game_dir), keys, tmp_path / "Out.app", ["--gpu", "g18p"])

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

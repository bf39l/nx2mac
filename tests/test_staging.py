"""Tests for nx2mac.staging (one engine-ready folder from base game + extras)."""

import json
from pathlib import Path

from nx2mac.staging import (
    MANIFEST_NAME,
    cleanup_staging,
    describe_staging,
    prepare_staging,
    staging_dir,
)


def _game(root: Path) -> Path:
    game = root / "MyGame"
    game.mkdir()
    (game / "base.nsp").write_bytes(b"\x00")
    (game / "extra.nsz").write_bytes(b"\x00")
    (game / "notes.txt").write_text("ignored\n", encoding="utf-8")
    (game / "010094500C216000_build").mkdir()  # engine's build folder must be skipped
    return game


def _file(root: Path, name: str) -> Path:
    path = root / name
    path.write_bytes(b"\x00")
    return path


def test_staging_path_is_stable_and_selection_specific(tmp_path: Path) -> None:
    game = _game(tmp_path)
    update = _file(tmp_path, "update.nsp")
    other = _file(tmp_path, "dlc-b.nsp")

    first = staging_dir(tmp_path / "cache", game, [update])
    again = staging_dir(tmp_path / "cache", game, [update])
    different = staging_dir(tmp_path / "cache", game, [other])

    assert first == again and first != different
    assert first.parent == tmp_path / "cache" / "staging" and "MyGame" in first.name


def test_prepare_staging_links_game_files_and_extras(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    game = _game(tmp_path)
    update = _file(tmp_path, "update.nsp")
    dlc_a = _file(tmp_path, "dlc-a.nsp")
    dlc_b = _file(tmp_path, "dlc-b.nsp")

    directory = prepare_staging(cache, game, [update], [dlc_a, dlc_b])

    names = sorted(entry.name for entry in directory.iterdir() if entry.is_symlink())
    assert names == ["base.nsp", "dlc-a.nsp", "dlc-b.nsp", "extra.nsz", "update.nsp"]
    assert (directory / "update.nsp").resolve() == update.resolve()
    # non-game files and stale build folders are not staged
    assert not (directory / "notes.txt").exists() and not (directory / "010094500C216000_build").exists()


def test_prepare_staging_keeps_engine_build_folder(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    game = _game(tmp_path)
    directory = prepare_staging(cache, game, [_file(tmp_path, "update.nsp")])
    build = directory / "010094500C216000_build"
    build.mkdir()
    (build / "keep.txt").write_text("cached\n", encoding="utf-8")

    prepare_staging(cache, game, [_file(tmp_path, "update.nsp")])  # refresh

    assert (build / "keep.txt").read_text(encoding="utf-8") == "cached\n"


def test_prepare_staging_links_folder_extras(tmp_path: Path) -> None:
    game = _game(tmp_path)
    dlc_folder = tmp_path / "DLC"
    dlc_folder.mkdir()
    (dlc_folder / "one.nsp").write_bytes(b"\x00")
    (dlc_folder / "two.nsp").write_bytes(b"\x00")

    directory = prepare_staging(tmp_path / "cache", game, [], [dlc_folder])

    assert {"one.nsp", "two.nsp"} <= {entry.name for entry in directory.iterdir()}


def test_name_collisions_are_uniquified(tmp_path: Path) -> None:
    game = _game(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "base.nsp").write_bytes(b"\x00")  # same name as the game's own file

    directory = prepare_staging(tmp_path / "cache", game, [], [elsewhere / "base.nsp"])

    names = sorted(entry.name for entry in directory.iterdir() if entry.is_symlink())
    assert names == ["base-2.nsp", "base.nsp", "extra.nsz"]


def test_manifest_records_links(tmp_path: Path) -> None:
    directory = prepare_staging(tmp_path / "cache", _game(tmp_path), [_file(tmp_path, "u.nsp")])

    manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))

    assert sorted(manifest) == ["base.nsp", "extra.nsz", "u.nsp"]
    assert "3 entries" in describe_staging(directory)


def test_cleanup_removes_links_but_keeps_build_folder(tmp_path: Path) -> None:
    directory = prepare_staging(tmp_path / "cache", _game(tmp_path), [_file(tmp_path, "u.nsp")])
    build = directory / "010094500C216000_build"
    build.mkdir()

    cleanup_staging(directory)

    assert not any(entry.is_symlink() for entry in directory.iterdir())
    assert build.is_dir() and not (directory / MANIFEST_NAME).exists()


def test_prepare_staging_accepts_a_single_package_file(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    package = _file(tmp_path, "Game [0100].nsp")
    update = _file(tmp_path, "update.nsp")

    directory = prepare_staging(cache, package, [update])

    assert (directory / package.name).is_symlink()
    assert (directory / "update.nsp").is_symlink()
    assert "Game" in directory.name and directory.parent == cache / "staging"


def test_the_engines_own_folders_are_not_staged(tmp_path: Path) -> None:
    """`<title id>_build` and `<title id>_survey` sit next to a dump after a build: never input."""
    from nx2mac.staging import prepare_staging

    game = tmp_path / "Game [0100000000000000]"
    game.mkdir()
    (game / "base.nsp").write_bytes(b"nsp")
    (game / "0100000000000000").mkdir()
    (game / "0100000000000000" / "content.nca").write_bytes(b"nca")
    (game / "0100000000000000_build").mkdir()
    (game / "0100000000000000_build" / "huge.bin").write_bytes(b"x")
    (game / "0100000000000000_survey").mkdir()

    staging = prepare_staging(tmp_path / "cache", game, ())

    names = {entry.name for entry in staging.iterdir() if not entry.name.startswith(".")}
    assert names == {"base.nsp", "0100000000000000"}


def test_a_directory_in_the_way_gets_a_free_name_instead_of_being_deleted(tmp_path: Path) -> None:
    """The refresh must never delete a directory it did not create (that was an EPERM crash)."""
    from nx2mac.staging import prepare_staging

    game = tmp_path / "Game [0100000000000000]"
    game.mkdir()
    (game / "base.nsp").write_bytes(b"nsp")
    (game / "0100000000000000").mkdir()

    staging = prepare_staging(tmp_path / "cache", game, ())
    # a previous run left a real directory where our link wants to go
    blocking = staging / "0100000000000000"
    blocking.unlink()
    blocking.mkdir()
    (blocking / "keep.bin").write_bytes(b"keep")

    prepare_staging(tmp_path / "cache", game, ())

    assert (blocking / "keep.bin").read_bytes() == b"keep"          # untouched
    assert (staging / "0100000000000000-2").is_symlink()            # our link took another name

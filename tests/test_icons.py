"""Tests for nx2mac.icons (downloading known game icons and center-cropping them)."""

import urllib.request
from pathlib import Path

from conftest import FakeEnv

from nx2mac import icons
from nx2mac.runner import RunResult


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_user_dropped_icon_wins_and_is_never_downloaded(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()
    (game / "0400486024DEC000.png").write_bytes(b"user")

    assert icons.ensure_game_icon(game, "0400486024DEC000", FakeEnv()) == game / "0400486024DEC000.png"
    assert (game / "0400486024DEC000.png").read_bytes() == b"user"


def test_ensure_game_icon_downloads_and_center_crops(tmp_path: Path, monkeypatch) -> None:
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _FakeResponse(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64))

    def fake_sips(argv: list[str]) -> RunResult:
        if "-g" in argv:
            return RunResult(stdout="pixelWidth: 920\npixelHeight: 430\n", stderr="")
        # the crop: write the destination
        out = Path(argv[argv.index("--out") + 1])
        out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        return RunResult(stdout="", stderr="")

    env = FakeEnv(run_rules=[(r"^sips", fake_sips)])

    result = icons.ensure_game_icon(game, "0400486024DEC000", env)

    assert result == game / "0400486024DEC000.png" and result.is_file()
    # the raw download is cleaned up, only the cropped square remains
    assert list(game.glob("*.download")) == []


def test_ensure_game_icon_creates_the_game_folder(tmp_path: Path, monkeypatch) -> None:
    """The game's library folder may not exist yet (fetch happens before the engine lays it out)."""
    game = tmp_path / "game"  # deliberately not created
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _FakeResponse(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64))

    def fake_sips(argv: list[str]) -> RunResult:
        if "-g" in argv:
            return RunResult(stdout="pixelWidth: 920\npixelHeight: 430\n", stderr="")
        out = Path(argv[argv.index("--out") + 1])
        out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        return RunResult(stdout="", stderr="")

    env = FakeEnv(run_rules=[(r"^sips", fake_sips)])
    result = icons.ensure_game_icon(game, "0400486024DEC000", env)

    assert result == game / "0400486024DEC000.png" and result.is_file()


def test_unknown_title_is_left_alone(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()

    assert icons.ensure_game_icon(game, "0100000000000000", FakeEnv()) is None
    assert list(game.iterdir()) == []

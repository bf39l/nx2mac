"""Tests for nx2mac.probe (which package inside a folder is "the game")."""

from pathlib import Path

import pytest

from nx2mac.probe import probe


def _folder(tmp_path: Path, names: tuple[str, ...], folder: str = "Dead Cells") -> Path:
    directory = tmp_path / folder
    directory.mkdir()
    for index, name in enumerate(names):
        (directory / name).write_bytes(b"\x00" * (1000 * (index + 1)))  # later files are bigger
    return directory


def test_prefers_the_base_game_by_title_id(tmp_path: Path) -> None:
    game = _folder(
        tmp_path,
        (
            "Dead_Cells_0100646009FBE000_v1.0.0.nsp",
            "Dead_Cells_0100646009FBE800_Update_v1.25.0.nsp",  # bigger, but an update
        ),
    )

    scan = probe(game)

    assert scan.kind == "switch1" and scan.needs_prod_keys
    assert scan.game_file == game / "Dead_Cells_0100646009FBE000_v1.0.0.nsp"
    roles = {package.path.name: package.role for package in scan.packages}
    assert roles["Dead_Cells_0100646009FBE800_Update_v1.25.0.nsp"] == "update"


def test_merged_base_plus_update_file_counts_as_the_game(tmp_path: Path) -> None:
    game = _folder(tmp_path, ("Metal_Slug_Tactics_01003D901D24A000_B+U[v1.0.1].nsp",))

    assert probe(game).game_file == game / "Metal_Slug_Tactics_01003D901D24A000_B+U[v1.0.1].nsp"


def test_falls_back_to_the_biggest_package_without_a_title_id(tmp_path: Path) -> None:
    game = tmp_path / "Some Dump"
    game.mkdir()
    (game / "small.nsp").write_bytes(b"\x00" * 10)
    (game / "big.xci").write_bytes(b"\x00" * 5000)
    (game / "update.nsp").write_bytes(b"\x00" * 9000)  # named as an update: never the pick

    assert probe(game).game_file == game / "big.xci"


def test_reads_one_level_down_for_dump_layouts(tmp_path: Path) -> None:
    game = tmp_path / "Dump"
    (game / "dlc").mkdir(parents=True)
    (game / "base").mkdir()
    (game / "base" / "Game_0100646009FBE000.nsp").write_bytes(b"\x00")
    (game / "dlc" / "0100646009FBF001_DLC_001.nsp").write_bytes(b"\x00" * 20)

    scan = probe(game)

    assert scan.game_file == game / "base" / "Game_0100646009FBE000.nsp"
    assert {package.role for package in scan.packages} == {"base", "dlc"}


def test_nup_folder_and_nup_file_need_no_keys(tmp_path: Path) -> None:
    game = tmp_path / "Switch 2"
    game.mkdir()
    nup = game / "Mario Kart World.nup"
    nup.write_bytes(b"\x00")

    assert probe(game).kind == "nso" and probe(game).needs_prod_keys is False
    single = probe(nup)
    assert single.kind == "nso" and single.game_file == nup.resolve() and single.needs_prod_keys is False


def test_single_package_file_is_its_own_answer(tmp_path: Path) -> None:
    package = tmp_path / "Game_0100646009FBE000.nsp"
    package.write_bytes(b"\x00" * 10)

    scan = probe(package)

    assert scan.kind == "switch1" and scan.game_file == package
    assert scan.packages[0].role == "base"


def test_empty_folder_reports_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "Empty"
    empty.mkdir()

    scan = probe(empty)

    assert (scan.kind, scan.game_file, scan.packages) == ("empty", None, ())


def test_ignores_build_folders_and_hidden_files(tmp_path: Path) -> None:
    game = tmp_path / "Game"
    (game / "Game_0100646009FBE000_build").mkdir(parents=True)
    (game / "Game_0100646009FBE000_build" / "extracted.nsp").write_bytes(b"\x00" * 9000)
    (game / ".hidden.nsp").write_bytes(b"\x00")
    (game / "Game_0100646009FBE000.nsp").write_bytes(b"\x00" * 10)

    assert probe(game).game_file == game / "Game_0100646009FBE000.nsp"


def test_probe_picks_the_newest_update_by_version(tmp_path: Path) -> None:
    game = _folder(
        tmp_path,
        (
            "Game_0100646009FBE000.nsp",
            "Game_0100646009FBE800_Update_v1.0.1.nsp",
            "Game_0100646009FBE800_Update_v1.25.0.nsp",  # newer
            "Game_0100646009FBE800_Update_v1.9.9.nsp",  # "1.9.9" < "1.25.0"
        ),
    )

    scan = probe(game)

    assert scan.update is not None and scan.update.name.endswith("Update_v1.25.0.nsp")
    assert [p.version for p in scan.packages if p.role == "update"] == [(1, 0, 1), (1, 25, 0), (1, 9, 9)]


def test_probe_without_an_update_reports_none(tmp_path: Path) -> None:
    game = _folder(tmp_path, ("Game_0100646009FBE000.nsp",))

    assert probe(game).update is None


# The engine's --progress json stream, as build_ipa_from_nsp.py prints it.
STAGE = '{"ev": "stage", "k": 5, "n": 7, "name": "Mac shaders", "step": "compiling", "t": 1.0}'
STEP = '{"ev": "step", "k": 5, "name": "Mac shaders", "step": "cache check"}'
PROGRESS = ('{"ev": "progress", "k": 5, "name": "Mac shaders", "step": "compiling", "done": 1203, '
            '"total": 19592, "unit": "shaders", "frac": 0.06, "eta": 42.0}')
END = '{"ev": "stage_end", "k": 5, "name": "Mac shaders", "ok": true, "summary": "19,592 of 19,592 shaders ready", "elapsed": 300}'
SAY = '{"ev": "say", "msg": "[RED] something worth reading"}'


def test_translator_turns_engine_progress_into_lines_and_events() -> None:
    from nx2mac.progress import Translator

    lines: list[str] = []
    events: list[dict[str, object]] = []
    translator = Translator(lines.append, lambda kind, fields: events.append(fields), interval=0)

    translator.feed(STAGE)
    translator.feed(PROGRESS)
    translator.feed(END)

    assert lines[0] == "[5/7 Mac shaders] · compiling …"
    assert "1,203/19,592 shaders" in lines[1]  # compiled out of total, in the log
    assert lines[2].startswith("✓ [5/7 Mac shaders] (5:00): 19,592 of 19,592 shaders ready")
    assert [event["kind"] for event in events] == ["stage", "progress", "stage_end"]
    assert events[1]["done"] == 1203 and events[1]["total"] == 19592 and events[1]["index"] == 5


def test_translator_throttles_the_log_but_not_the_events() -> None:
    from nx2mac.progress import Translator

    lines: list[str] = []
    events: list[dict[str, object]] = []
    translator = Translator(lines.append, lambda kind, fields: events.append(fields), interval=60)

    translator.feed(STAGE)
    for done in (10, 20, 30):
        translator.feed(PROGRESS.replace("1203", str(done)))

    # the stage line, then the first counter; the rest of the burst is throttled
    assert len(lines) == 2 and "10/19,592" in lines[1]
    assert len([event for event in events if event["kind"] == "progress"]) == 3


def test_first_counter_is_logged_right_after_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """time.monotonic() is seconds since boot, so the interval must not be measured from 0.

    A machine up for less than the interval (a CI runner, a Mac that just restarted) used to lose
    the first shader counter line entirely.
    """
    from nx2mac.progress import Translator

    monkeypatch.setattr("nx2mac.progress.time.monotonic", lambda: 3.0)
    lines: list[str] = []
    translator = Translator(lines.append, None, interval=60)

    translator.feed(STAGE)
    translator.feed(PROGRESS)

    assert lines == ["[5/7 Mac shaders] · compiling …", "[5/7 Mac shaders] compiling: 1,203/19,592 shaders"]


def test_translator_shows_say_lines_and_plain_text() -> None:
    from nx2mac.progress import Translator

    lines: list[str] = []
    translator = Translator(lines.append, None)

    translator.feed(SAY)
    translator.feed("plain engine output")

    assert lines == ["[RED] something worth reading", "plain engine output"]

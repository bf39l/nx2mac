"""Tests for nx2mac.probe (which package inside a folder is "the game")."""

from pathlib import Path

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

"""Tests for the nx2mac CLI wiring (FakeEnv injected via ``nx2mac.cli._get_env``)."""

import json
import tomllib
from pathlib import Path

import pytest
from conftest import FakeEnv
from test_doctor import _good_env
from typer.testing import CliRunner

import nx2mac.cli as cli_module
from nx2mac import VERSION
from nx2mac.cli import app
from nx2mac.runner import RunResult

runner = CliRunner()
SHA = "c" * 40


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point config and cache into tmp so no real state is touched."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    return tmp_path


def _make_env(tmp: Path) -> FakeEnv:
    """A fully-passing system (tools + complete venv under tmp/cache/python)."""
    env = _good_env(tmp)

    def rev_parse(argv: list[str]) -> RunResult:  # every ref resolves to SHA
        return RunResult(stdout=f"{SHA}\n")

    env.run_rules.append((r"rev-parse --quiet --verify", rev_parse))
    return env


def _prepare_checkout(tmp: Path) -> Path:
    """An already-fetched engine checkout at its canonical cache location."""
    checkout = tmp / "cache" / "engines" / "seedds-nxnative_main"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "scripts").mkdir()
    (checkout / "scripts" / "build_ipa_from_nsp.py").write_text("print(1)\n", encoding="utf-8")
    return checkout


def _game(tmp: Path) -> Path:
    game = tmp / "MyGame"
    game.mkdir()
    (game / "base.nsp").write_bytes(b"\x00")
    (game / "prod.keys").write_text("k\n", encoding="utf-8")
    return game


def test_version(isolated_paths: Path) -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0 and VERSION in result.output


def test_help_lists_commands(isolated_paths: Path) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for name in ("doctor", "setup", "engine", "build"):
        assert name in result.output


def test_doctor_exit_codes_and_table(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    ok = runner.invoke(app, ["doctor"])
    assert ok.exit_code == 0 and "engine-venv" in ok.output and "FAIL" not in ok.output

    env.which_map.pop("zstd")
    bad = runner.invoke(app, ["doctor"])
    assert bad.exit_code == 1 and "zstd" in bad.output and "brew install zstd" in bad.output


def test_engine_persists_selection_and_reports_commit(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    _prepare_checkout(tmp)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["engine", "--source", "upstream"])

    assert result.exit_code == 0 and SHA[:12] in result.output and "branch=main" in result.output
    data = tomllib.loads((tmp / "cfg" / "nx2mac" / "config.toml").read_text(encoding="utf-8"))
    assert data["engine"] == {"id": "upstream", "ref": "main"}


def test_engine_rejects_unknown_source(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["engine", "--source", "bogus"])

    assert result.exit_code == 1 and "Unknown engine source" in result.output


def test_build_runs_engine_script_streams(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    _prepare_checkout(tmp)
    game = _game(tmp)
    out = tmp / "Out.app"
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["build", str(game), "-o", str(out)])

    assert result.exit_code == 0 and "Done." in result.output
    argv, cwd = env.stream_calls[0]
    script = tmp / "cache" / "engines" / "seedds-nxnative_main" / "scripts" / "build_ipa_from_nsp.py"
    assert argv[:2] == [str(tmp / "cache" / "python" / "bin" / "python3"), str(script)]
    assert "--platform" in argv and "mac-app" in argv and "-k" in argv and cwd is not None


def test_build_reports_missing_prereq(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    _prepare_checkout(tmp)
    env.which_map.pop("zstd")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["build", str(_game(tmp)), "-o", str(tmp / "O.app")])

    assert result.exit_code == 1 and "zstd" in result.output and "doctor" in result.output


def test_build_nonzero_engine_exit_propagates(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    env.stream_returncode = 3
    _prepare_checkout(tmp)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["build", str(_game(tmp)), "-o", str(tmp / "O.app")])

    assert result.exit_code == 3 and "exit code" in result.output


def test_setup_reports_ready(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0 and "All prerequisites met" in result.output


def _json(result) -> dict:  # one JSON document
    return json.loads(result.output)


def _ndjson(result) -> list[dict]:  # one event object per line
    return [json.loads(line) for line in result.output.splitlines() if line.strip()]


def test_version_json(isolated_paths: Path) -> None:
    result = runner.invoke(app, ["version", "--json"])

    assert result.exit_code == 0 and _json(result) == {"name": "nx2mac", "version": VERSION}


def test_global_json_flag_also_applies(isolated_paths: Path) -> None:
    result = runner.invoke(app, ["--json", "version"])

    assert result.exit_code == 0 and _json(result)["name"] == "nx2mac"


def test_doctor_json_has_machine_names_and_fixes(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    env.which_map.pop("zstd")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["doctor", "--json"])
    payload = _json(result)

    assert result.exit_code == 1 and payload["ok"] is False
    assert payload["failures"] == ["zstd"]
    assert "brew install zstd" in payload["fixes"]
    by_name = {check["name"]: check for check in payload["checks"]}
    assert by_name["zstd"]["ok"] is False and by_name["macos"]["ok"] is True


def test_engine_json_reports_source_ref_and_commit(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    _prepare_checkout(tmp)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["engine", "--json"])
    payload = _json(result)

    assert result.exit_code == 0
    engine = payload["engine"]
    assert engine["source"]["id"] == "upstream" and engine["ref"] == "main"
    assert engine["commit"] == SHA and engine["branch"] == "main"
    # the repo ships a patch series; it is applied to every checkout
    assert "0001-launcher-state-dirs.patch" in engine["patches"]
    assert payload["saved"] == {"engine_id": "upstream", "engine_ref": "main"}


def test_build_json_emits_ndjson_events(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    env.stream_lines = ["[stage] extracting", "[stage] done"]
    _prepare_checkout(tmp)
    out = tmp / "Out.app"
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["build", str(_game(tmp)), "-o", str(out), "--json"])
    events = _ndjson(result)

    assert result.exit_code == 0
    assert events[0]["event"] == "engine" and events[0]["engine"]["commit"] == SHA
    assert [e["line"] for e in events if e["event"] == "log"] == ["[stage] extracting", "[stage] done"]
    final = events[-1]
    assert final["event"] == "result" and final["ok"] is True
    assert final["app_path"] == str(out.resolve()) and "argv" in final


def test_build_json_reports_failure_as_error_event(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    _prepare_checkout(isolated_paths)
    env.which_map.pop("zstd")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["build", str(_game(isolated_paths)), "--json"])
    events = _ndjson(result)

    assert result.exit_code == 1
    assert events[-1]["event"] == "error" and "zstd" in events[-1]["message"]


def test_build_json_propagates_engine_exit_code(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    env.stream_returncode = 4
    _prepare_checkout(tmp)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["build", str(_game(tmp)), "-o", str(tmp / "O.app"), "--json"])
    final = _ndjson(result)[-1]

    assert result.exit_code == 4 and final["returncode"] == 4 and final["ok"] is False


def test_setup_json_payload_lists_installable_remedies(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    env.which_map.pop("zstd")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    env.which_map.pop("xcodebuild")  # make the full-Xcode check fail (manual remedy)
    result = runner.invoke(app, ["setup", "--json"])
    payload = _json(result)
    by_name = {check["name"]: check for check in payload["checks"]}

    assert result.exit_code == 1 and payload["ok"] is False
    assert by_name["zstd"]["installable"] is True
    assert by_name["zstd"]["install"] == ["brew", "install", "zstd"]
    assert by_name["xcode"]["installable"] is False  # manual: install Xcode yourself
    assert by_name["xcode"]["link"].startswith("https://apps.apple.com")
    assert by_name["macos"]["ok"] is True and by_name["macos"]["installable"] is False


def test_setup_installs_named_check_then_reports(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    env.which_map.pop("zstd")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    def brew_installs_zstd(_argv: list[str]) -> int:  # brew puts the tool on PATH
        env.which_map["zstd"] = tmp / "opt" / "zstd"
        return 0

    env.stream_effects.append((r"^brew install zstd$", brew_installs_zstd))
    result = runner.invoke(app, ["setup", "--install", "zstd"])

    installs = [call for call in env.stream_calls if call[0][:2] == ["brew", "install"]]
    assert result.exit_code == 0 and installs and installs[0][0] == ["brew", "install", "zstd"]
    assert "zstd installed" in result.output.replace("\n", " ")


def test_setup_install_rejects_unknown_check(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["setup", "--install", "warp-drive"])

    assert result.exit_code != 0 and "installable" in result.output


def test_setup_json_install_emits_install_events(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    env.which_map.pop("glslangValidator")
    env.stream_lines = ["==> Pouring glslang", "==> Summary"]
    env.stream_effects.append((r"^brew install glslang$", lambda _argv: env.which_map.__setitem__(
        "glslangValidator", tmp / "opt" / "glslangValidator") or 0))
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["setup", "--json", "--install", "glslangValidator"])
    events = _ndjson(result)

    assert result.exit_code == 0
    assert events[0] == {"event": "install-start", "check": "glslangValidator", "command": ["brew", "install", "glslang"]}
    # the installer echoes the command it runs, then the tool's own output
    assert [e["line"] for e in events if e["event"] == "log"] == [
        "$ brew install glslang",
        "==> Pouring glslang",
        "==> Summary",
    ]
    assert events[-2]["event"] == "install-done" and events[-2]["check"] == "glslangValidator"
    assert events[-1]["event"] == "setup"  # final report with the refreshed doctor


def test_setup_prompts_yes_no_per_missing_component(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    env.which_map.pop("zstd")
    env.which_map.pop("glslangValidator")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)

    env.stream_effects.append((r"^brew install zstd$", lambda _argv: env.which_map.__setitem__(
        "zstd", tmp / "opt" / "zstd") or 0))
    # prompts come in doctor order: zstd first, then glslangValidator
    result = runner.invoke(app, ["setup"], input="y\nn\n")

    asked = [line for line in result.output.splitlines() if "Install " in line and "?" in line]
    assert len(asked) == 2 and "zstd" in asked[0] and "glslangValidator" in asked[1]
    accepted = [call for call in env.stream_calls if call[0] == ["brew", "install", "zstd"]]
    declined = [call for call in env.stream_calls if call[0] == ["brew", "install", "glslang"]]
    assert len(accepted) == 1 and not declined  # said yes, then no


def test_setup_no_prompt_only_reports(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    env.which_map.pop("zstd")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["setup", "--no-prompt"])

    assert result.exit_code == 1 and "Still to do manually" not in result.output
    assert not env.stream_calls


def test_build_forwards_update_and_dlc_flags(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    _prepare_checkout(tmp)
    game = _game(tmp)
    update = tmp / "update.nsp"
    update.write_bytes(b"\x00")
    dlc_folder = tmp / "DLC"
    dlc_folder.mkdir()
    (dlc_folder / "dlc-a.nsp").write_bytes(b"\x00")
    (dlc_folder / "dlc-b.nsp").write_bytes(b"\x00")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(
        app,
        ["build", str(game), "-o", str(tmp / "O.app"), "--update", str(update), "--dlc", str(dlc_folder)],
    )

    assert result.exit_code == 0 and "1 update(s), 1 DLC package(s)" in result.output
    argv, _cwd = env.stream_calls[0]
    staged = Path(argv[2])
    assert staged != game.resolve()
    assert {"base.nsp", "update.nsp", "dlc-a.nsp", "dlc-b.nsp"} <= {entry.name for entry in staged.iterdir()}


def test_build_json_reports_input_path(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    _prepare_checkout(tmp)
    game = _game(tmp)
    update = tmp / "update.nsp"
    update.write_bytes(b"\x00")
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["build", str(game), "--update", str(update), "--json"])

    final = _ndjson(result)[-1]
    assert result.exit_code == 0 and final["event"] == "result"
    assert final["input_path"] != str(game.resolve()) and "/staging/" in final["input_path"]


def test_build_out_dir_keeps_everything_together(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = isolated_paths
    env = _make_env(tmp)
    _prepare_checkout(tmp)
    game = _game(tmp)  # already has prod.keys next to base.nsp
    out = tmp / "MyGameProject"
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    def fake_build(argv: list[str]) -> int:
        work = Path(argv[argv.index("--work-dir") + 1])
        (work.parent / "My Game.app").mkdir(parents=True)
        return 0

    env.stream_effects.append((r"build_ipa_from_nsp\.py", fake_build))
    result = runner.invoke(app, ["build", str(game), "--out-dir", str(out), "--json"])

    final = _ndjson(result)[-1]
    argv = env.stream_calls[0][0]
    assert result.exit_code == 0 and final["event"] == "result"
    assert final["out_dir"] == str(out.resolve()) and final["work_dir"].endswith("_build")
    assert final["keys_path"] == str((game / "prod.keys").resolve())  # used in place, not copied
    assert not (out / "prod.keys").exists()
    assert final["app_path"] == str(out / "My Game.app")
    assert argv[argv.index("--work-dir") + 1] == final["work_dir"]


def test_every_command_is_registered(isolated_paths: Path) -> None:
    """A command silently disappearing is a regression: the GUI calls all of these."""
    from nx2mac.cli import app as cli_app

    names = {(command.name or command.callback.__name__) for command in cli_app.registered_commands}
    for expected in ("version", "doctor", "setup", "engine", "probe", "library", "player", "cloud", "clean", "build"):
        assert expected in names, f"missing command: {expected}"


def test_probe_picks_the_game_package(isolated_paths: Path) -> None:
    tmp = isolated_paths
    game = tmp / "Game [0100646009FBE000]"
    game.mkdir()
    (game / "Dead_Cells_0100646009FBE000_v1.0.0.nsp").write_bytes(b"\x00" * 20)
    (game / "Dead_Cells_0100646009FBE800_Update_v1.25.0.nsp").write_bytes(b"\x00" * 40)

    result = runner.invoke(app, ["probe", str(game), "--json"])
    payload = _json(result)

    assert result.exit_code == 0
    assert payload["kind"] == "switch1" and payload["needs_prod_keys"] is True
    assert payload["game_file"].endswith("Dead_Cells_0100646009FBE000_v1.0.0.nsp")
    assert {package["role"] for package in payload["packages"]} == {"base", "update"}


def test_cheats_enable_reports_an_unrunnable_cheat_instead_of_a_traceback(tmp_path, monkeypatch) -> None:
    """An entry the compile could not anchor is a user mistake: exit 1 with the reason, no traceback."""
    from nx2mac import config as config_mod
    from nx2mac import library as library_mod

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / ".nx2mac-library").touch()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    config_mod.save(config_mod.Config(library_dirs=(str(lib),)))
    tid = "0100646009FBE000"
    folder = library_mod.cheats_dir(lib, tid)
    folder.mkdir(parents=True)
    (folder / "0123456789ABCDEF.txt").write_text("[HP]\n04000000 0013AA2C 00005431\n", encoding="utf-8")
    (folder / "0123456789ABCDEF.meta.tsv").write_text(
        "name\tkind\tsection\ttype\taction\tvalue\task\tvalue_words\tonce\tpair\tresolved\tnote\taddress\n"
        "HP\tpatch\t\t\t\t\t\t\t\t\tno anchors\t\t\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["cheats", "--library", str(lib), "--game", tid, "--enable", "HP"])

    assert result.exit_code == 1
    assert "could not be anchored" in result.output
    assert "Traceback" not in result.output


def test_cheats_resolve_the_game_by_title_id(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The GUI addresses a game by title ID (the dump folder's name is not an entry)."""
    import plistlib

    tmp = isolated_paths
    env = _make_env(tmp)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)
    library = tmp / "Games"
    library.mkdir()
    app = library / "Dead Cells.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "Dead Cells",
        "CFBundleShortVersionString": "1.25.0",
        "CFBundleIdentifier": "org.nxnative.0100646009fbe000",
    }))
    cheats = library / "nx2mac_game_lib" / "0100646009FBE000" / "mods" / "cheats"
    cheats.mkdir(parents=True)
    (cheats / "0123456789ABCDEF.txt").write_text("[Max gold]\n02000000 00000000\n", encoding="utf-8")
    runner.invoke(app_module := cli_module.app, ["library", "--add", str(library)])

    by_title = runner.invoke(app_module, ["cheats", "--game", "0100646009FBE000", "--json"])
    by_folder_name = runner.invoke(app_module, ["cheats", "--game", "0Dead_Cells[0100646009FBE000]", "--json"])

    assert by_title.exit_code == 0 and _json(by_title)["games"][0]["builds"][0]["cheats"][0]["name"] == "Max gold"
    assert by_folder_name.exit_code == 1  # a dump folder name is not a game

    imported = runner.invoke(app_module, ["cheats", "--game", "0100646009FBE000",
                                          "--import", str(cheats / "0123456789ABCDEF.txt"), "--json"])
    assert imported.exit_code == 0


def test_cheats_work_before_the_game_is_built(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cheats belong to a title ID: import/list works with only a game folder chosen."""
    tmp = isolated_paths
    env = _make_env(tmp)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)
    library = tmp / "Games"
    library.mkdir()
    source = tmp / "0123456789ABCDEF.txt"
    source.write_text("[Max gold]\n02000000 00000000\n", encoding="utf-8")
    runner.invoke(app, ["library", "--add", str(library)])

    imported = runner.invoke(app, ["cheats", "--game", "0100646009FBE000",
                                   "--library", str(library), "--import", str(source), "--json"])
    listed = runner.invoke(app, ["cheats", "--game", "0100646009fbe000",
                                 "--library", str(library), "--json"])

    assert imported.exit_code == 0 and listed.exit_code == 0
    build = _json(listed)["games"][0]["builds"][0]
    assert build["file"].endswith("0123456789ABCDEF.txt")
    assert [cheat["name"] for cheat in build["cheats"]] == ["Max gold"]
    assert (library / "nx2mac_game_lib" / "0100646009FBE000" / "mods" / "cheats").is_dir()


def test_build_json_emits_progress_events(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine's --progress json is translated: a log line and a progress event for the UI."""
    tmp = isolated_paths
    env = _make_env(tmp)
    counted = (
        '{"ev": "progress", "k": 6, "name": "Mac shaders", "step": "compiling", "done": 721, '
        '"total": 1262, "unit": "shaders"}'
    )
    env.stream_lines = ['{"ev": "stage", "k": 6, "n": 8, "name": "Mac shaders", "step": "compiling"}', counted]
    _prepare_checkout(tmp)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["build", str(_game(tmp)), "--json"])
    events = _ndjson(result)

    progress = [event for event in events if event["event"] == "progress"]
    assert progress and progress[0]["index"] == 6 and progress[0]["stages"] == 8
    assert progress[-1]["done"] == 721 and progress[-1]["total"] == 1262 and progress[-1]["unit"] == "shaders"
    assert any("721/1,262 shaders" in event["line"] for event in events if event["event"] == "log")

"""Tests for the nx2mac CLI wiring (FakeEnv injected via ``nx2mac.cli._get_env``)."""

import tomllib
from pathlib import Path

import pytest
from conftest import FakeEnv
from test_doctor import _good_env
from typer.testing import CliRunner

import nx2mac.cli as cli_module
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
    checkout = tmp / "cache" / "engines" / "upstream-main"
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

    assert result.exit_code == 0 and "0.1" in result.output


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
    script = tmp / "cache" / "engines" / "upstream-main" / "scripts" / "build_ipa_from_nsp.py"
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

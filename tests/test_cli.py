"""Tests for the nx2mac CLI wiring (FakeEnv injected via ``nx2mac.cli._get_env``)."""

import json
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


def _json(result) -> dict:  # one JSON document
    return json.loads(result.output)


def _ndjson(result) -> list[dict]:  # one event object per line
    return [json.loads(line) for line in result.output.splitlines() if line.strip()]


def test_version_json(isolated_paths: Path) -> None:
    result = runner.invoke(app, ["version", "--json"])

    assert result.exit_code == 0 and _json(result) == {"name": "nx2mac", "version": "0.1.0"}


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
    assert engine["commit"] == SHA and engine["branch"] == "main" and engine["patches"] == []
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


def test_setup_json_payload(isolated_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(isolated_paths)
    monkeypatch.setattr(cli_module, "_get_env", lambda: env)

    result = runner.invoke(app, ["setup", "--json"])
    payload = _json(result)

    assert result.exit_code == 0 and payload["ok"] is True and payload["doctor"]["ok"] is True
    assert payload["python"]["error"] is None and payload["python"]["path"].endswith("python3")

"""Tests for nx2mac.config (TOML load/save round-trips)."""

from pathlib import Path

import pytest

from conftest import FakeEnv  # noqa: F401  (kept so fixtures stay uniform across tests)
from nx2mac.config import Config, ConfigError, config_path, load, save


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.toml") == Config()


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    config = Config(engine_id="fork", engine_ref='game/"xcom2"')  # quotes must survive TOML

    save(config, path)
    assert load(path) == config


def test_save_partial_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    save(Config(engine_id="upstream"), path)
    text = path.read_text(encoding="utf-8")
    assert "[engine]" in text and 'id = "upstream"' in text and "ref =" not in text


def test_save_empty_config_writes_header_only(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    save(Config(), path)

    text = path.read_text(encoding="utf-8")
    assert "[engine]" not in text and load(path) == Config()


def test_load_malformed_toml_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[engine\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="cannot read"):
        load(path)


def test_load_non_table_engine_section_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('engine = "fork"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="must be a table"):
        load(path)


def test_load_ignores_unknown_or_empty_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[engine]\nid = ""\nref = 42\nother = true\n', encoding="utf-8")

    assert load(path) == Config()


def test_config_path_honours_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config_path() == tmp_path / "nx2mac" / "config.toml"

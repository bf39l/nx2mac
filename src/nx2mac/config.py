"""Persistent user configuration for nx2mac (TOML at ``~/.config/nx2mac/config.toml``)."""

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when the config file exists but cannot be read or is malformed."""


@dataclass(frozen=True)
class Config:
    """Persisted user choices. Every field is optional (None = not set)."""

    engine_id: str | None = None  # 'upstream' or 'fork'
    engine_ref: str | None = None  # branch, tag, or commit hash; None = source default


def config_path() -> Path:
    """Path of the TOML config file (honours XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "nx2mac" / "config.toml"


def cache_root() -> Path:
    """Root for engine checkouts and the managed Python venv."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "nx2mac"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "nx2mac"


def _format_str(value: str) -> str:
    """Render a string as a TOML basic (double-quoted) literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def load(path: Path | None = None) -> Config:
    """Read the config file, returning an empty Config when it does not exist yet."""
    path = path or config_path()
    if not path.is_file():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    engine = data.get("engine", {})
    if not isinstance(engine, dict):
        raise ConfigError(f"[engine] table in {path} must be a table")

    def opt(key: str) -> str | None:
        value = engine.get(key)
        return value if isinstance(value, str) and value else None

    return Config(engine_id=opt("id"), engine_ref=opt("ref"))


def save(config: Config, path: Path | None = None) -> None:
    """Write the config as TOML, creating parent directories as needed."""
    path = path or config_path()
    lines: list[str] = ["# nx2mac configuration (written by `nx2mac engine select`)"]
    if config.engine_id is not None or config.engine_ref is not None:
        lines += ["", "[engine]"]
        if config.engine_id is not None:
            lines.append(f"id = {_format_str(config.engine_id)}")
        if config.engine_ref is not None:
            lines.append(f"ref = {_format_str(config.engine_ref)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

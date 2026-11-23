"""Tests for nx2mac.pyenv (interpreter discovery and the managed venv)."""

import re
from pathlib import Path

import pytest
from conftest import FakeEnv
from nx2mac.runner import RunResult
from nx2mac.pyenv import (
    InterpreterInfo,
    PyEnvError,
    VenvStatus,
    backports_zstd_spec,
    best_engine_interpreter,
    ensure_venv,
    find_engine_interpreters,
    probe_venv,
    required_pip_specs,
)

FAKE_PY = "/fake/py3"
INTERP_312_ARM64 = InterpreterInfo(path=Path(FAKE_PY), major=3, minor=12, arch="arm64")


def _make_venv_python(root: Path) -> Path:
    """Create just the venv python file so probes run without a real venv."""
    python = root / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    return python


def test_backports_zstd_spec_by_version() -> None:
    assert backports_zstd_spec((3, 12)) == "backports.zstd"
    assert backports_zstd_spec((3, 13)) == "backports.zstd"
    assert backports_zstd_spec((3, 14)) is None


def test_required_pip_specs() -> None:
    old = required_pip_specs((3, 12))
    new = required_pip_specs((3, 15))
    assert old == ("lz4", "capstone>=5,<6", "fonttools", "zbic", "backports.zstd")
    assert len(new) == 4 and all("backports" not in spec for spec in new)


def test_find_engine_interpreters_filters_by_arch_and_version() -> None:
    env = FakeEnv(
        which_map={"python3": Path(FAKE_PY)},
        run_rules=[
            (rf"^{re.escape(FAKE_PY)} -c", RunResult(stdout="3.12 arm64\n")),
            # PATH python3 is first in candidate order, so it wins the list position.
            (r"^/opt/homebrew/bin/python3 -c", RunResult(stdout="3.13 arm64\n")),  # accepted later
            (r"^/usr/local/bin/python3 -c", RunResult(stdout="3.12 x86_64\n")),  # wrong arch
            (r"^/usr/bin/python3 -c", RunResult(stdout="3.9 arm64\n")),  # too old
        ],
    )

    found = find_engine_interpreters(env)

    assert [(str(i.path), i.major, i.minor) for i in found] == [
        (FAKE_PY, 3, 12),
        ("/opt/homebrew/bin/python3", 3, 13),
    ]


def test_find_engine_interpreters_skips_unusable_candidates() -> None:
    env = FakeEnv(
        which_map={"python3": Path(FAKE_PY)},
        run_rules=[
            (rf"^{re.escape(FAKE_PY)} -c", RunResult(stderr="boom", returncode=1)),  # broken
            (r"^/usr/bin/python3 -c", RunResult(stdout="garbage\n")),  # unparseable
        ],
    )

    assert find_engine_interpreters(env) == []


def test_best_engine_interpreter_none_when_nothing_qualifies() -> None:
    assert best_engine_interpreter(FakeEnv()) is None


def test_probe_venv_reports_missing_modules(tmp_path: Path) -> None:
    python = _make_venv_python(tmp_path)
    env = FakeEnv(run_rules=[(rf"^{python} -c import importlib", RunResult(stdout="capstone>=5,<6 zbic\n"))])

    status = probe_venv(env, tmp_path)

    assert status is not None
    assert (status.python, status.missing_modules, status.ok) == (python, ("capstone>=5,<6", "zbic"), False)


def test_probe_venv_complete_and_absent(tmp_path: Path) -> None:
    python = _make_venv_python(tmp_path / "full")
    env_full = FakeEnv(run_rules=[(rf"^{python} -c import importlib", RunResult(stdout=""))])

    assert probe_venv(env_full, tmp_path / "full") == VenvStatus(python=python)
    assert probe_venv(FakeEnv(), tmp_path / "missing") is None


def test_probe_venv_broken_interpreter_returns_none(tmp_path: Path) -> None:
    python = _make_venv_python(tmp_path)
    env = FakeEnv(run_rules=[(rf"^{python} -c import importlib", RunResult(stderr="bad", returncode=1))])

    assert probe_venv(env, tmp_path) is None


def test_ensure_venv_installs_missing_then_verifies(tmp_path: Path) -> None:
    python = _make_venv_python(tmp_path)  # venv already exists -> no creation call expected
    probes: list[bool] = [False, True]  # first probe reports a missing module, second is clean

    def probe_response(args: list[str]) -> RunResult:
        return RunResult(stdout="lz4\n") if not probes.pop(0) else RunResult(stdout="")

    env = FakeEnv(
        run_rules=[
            (rf"^{python} -c import importlib", probe_response),  # type: ignore[arg-type]
            (rf"^{python} -m pip install", RunResult(stdout="")),
        ],
    )

    status = ensure_venv(tmp_path, env=env, interpreter=INTERP_312_ARM64)

    assert status.ok and status.python == python
    pip_calls = [c for c in env.calls if len(c) > 2 and c[2] == "pip"]
    assert pip_calls == [[str(python), "-m", "pip", "install", "--quiet", *required_pip_specs((3, 12))]]


def test_ensure_venv_complete_env_skips_pip(tmp_path: Path) -> None:
    python = _make_venv_python(tmp_path)
    env = FakeEnv(run_rules=[(rf"^{python} -c import importlib", RunResult(stdout=""))])

    status = ensure_venv(tmp_path, env=env, interpreter=INTERP_312_ARM64)

    assert status.ok and not any("pip" in c for c in env.calls)


def test_ensure_venv_creates_missing_venv(tmp_path: Path) -> None:
    # No venv python file yet; creation succeeds but leaves no file behind in the
    # fake world, so ensure_venv must report failure (file check after creation).
    env = FakeEnv(run_rules=[(r"^/fake/py3 -m venv", RunResult(stdout=""))])

    with pytest.raises(PyEnvError, match="could not create venv"):
        ensure_venv(tmp_path / "cache", env=env, interpreter=INTERP_312_ARM64)


def test_ensure_venv_requires_suitable_interpreter(tmp_path: Path) -> None:
    with pytest.raises(PyEnvError, match="no arm64 CPython"):
        ensure_venv(tmp_path, env=FakeEnv())


def test_ensure_venv_reports_still_missing_modules(tmp_path: Path) -> None:
    python = _make_venv_python(tmp_path)
    # Probe always reports the same missing module, even after pip install.
    env = FakeEnv(
        run_rules=[
            (rf"^{python} -c import importlib", RunResult(stdout="zbic\n")),
            (rf"^{python} -m pip install", RunResult(stdout="done")),
        ]
    )

    with pytest.raises(PyEnvError, match="still missing modules.*zbic"):
        ensure_venv(tmp_path, env=env, interpreter=INTERP_312_ARM64)


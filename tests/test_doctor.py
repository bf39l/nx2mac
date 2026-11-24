"""Tests for nx2mac.doctor (prerequisite checks with an injected Env)."""

import re
from pathlib import Path

import pytest
from conftest import FakeEnv

from nx2mac.doctor import (
    BUILD_PREFLIGHT_EXCLUDE,
    Check,
    DoctorContext,
    DoctorReport,
    run_checks,
)
from nx2mac.runner import RunResult

EXPECTED_NAMES: tuple[str, ...] = (
    "macos",
    "apple-silicon",
    "macos-version",
    "xcode",
    "metal-toolchain",
    "git",
    "brew",
    "zstd",
    "glslangValidator",
    "make",
    "engine-python",
    "engine-venv",
)


def _good_env(tmp_path: Path, *, with_venv: bool = True) -> FakeEnv:
    """A fully-passing fake system rooted in tmp_path."""
    cache = tmp_path / "cache"
    py3 = tmp_path / "bin" / "python3"
    venv_py = cache / "python" / "bin" / "python3"
    if with_venv:
        venv_py.parent.mkdir(parents=True, exist_ok=True)
        venv_py.touch()

    paths = {
        tool: tmp_path / "opt" / tool for tool in ("brew", "zstd", "glslangValidator", "make", "git", "xcodebuild")
    }
    return FakeEnv(
        which_map={**paths, "python3": py3},
        run_rules=[
            (r"^sw_vers -productVersion", RunResult(stdout="26.7\n")),
            (r"^xcode-select -p", RunResult(stdout="/Applications/Xcode.app/Contents/Developer\n")),
            (r"^xcrun -sdk macosx metal --version", RunResult(stdout="Apple Metal version 1.3\n")),
            (rf"^{re.escape(str(py3))} -c import platform, sys", RunResult(stdout="3.12 arm64\n")),
            (rf"^{re.escape(str(venv_py))} -c import importlib", RunResult(stdout="")),
        ],
    )


def _ctx(
    tmp_path: Path | None = None, env: FakeEnv | None = None, *, system: str = "darwin", machine: str = "arm64"
) -> DoctorContext:
    if env is None and tmp_path is not None:
        env = _good_env(tmp_path)
    return DoctorContext(env=env or FakeEnv(), system=system, machine=machine)


def test_all_checks_pass_on_good_macos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    report = run_checks(_ctx(tmp_path))

    assert report.ok
    assert tuple(c.name for c in report.checks) == EXPECTED_NAMES


def test_missing_zstd_reports_brew_fix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    env = _good_env(tmp_path)
    del env.which_map["zstd"]
    report = run_checks(_ctx(tmp_path, env))

    check = next(c for c in report.checks if c.name == "zstd")
    assert not check.ok and "'zstd' not found on PATH" in check.detail
    assert check.fix == "brew install zstd"


def test_command_line_tools_only_fails_xcode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    env = _good_env(tmp_path)
    env.run_rules.insert(0, (r"^xcode-select -p", RunResult(stdout="/Library/Developer/CommandLineTools\n")))

    check = next(c for c in run_checks(_ctx(tmp_path, env)).checks if c.name == "xcode")
    assert not check.ok and "Command Line Tools" in check.detail
    assert "sudo xcode-select -s /Applications/Xcode.app" in check.fix


def test_missing_metal_toolchain_suggests_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    env = _good_env(tmp_path)
    env.run_rules.insert(
        0, (r"^xcrun -sdk macosx metal --version", RunResult(stderr="No Metal toolchain found", returncode=1))
    )

    check = next(c for c in run_checks(_ctx(tmp_path, env)).checks if c.name == "metal-toolchain")
    assert not check.ok and "-downloadComponent MetalToolchain" in check.fix


@pytest.mark.parametrize("version,passes", [("26.7", True), ("26.5", False), ("27.0", True)])
def test_macos_version_floor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: str, passes: bool) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    env = _good_env(tmp_path)
    env.run_rules.insert(0, (r"^sw_vers -productVersion", RunResult(stdout=version + "\n")))

    check = next(c for c in run_checks(_ctx(tmp_path, env)).checks if c.name == "macos-version")
    assert check.ok is passes


def test_non_darwin_system_fails_mac_specific_checks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    report = run_checks(_ctx(tmp_path, system="linux"))

    failed = {c.name for c in report.failures()}
    assert {"macos", "macos-version", "xcode", "metal-toolchain"} <= failed and not report.ok


def test_intel_machine_fails_apple_silicon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    env = _good_env(tmp_path, with_venv=True)

    check = next(c for c in run_checks(_ctx(tmp_path, env, machine="x86_64")).checks if c.name == "apple-silicon")
    assert not check.ok and "arm64" in check.fix


def test_missing_interpreter_fails_engine_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache"))
    env = _good_env(tmp_path)
    del env.which_map["python3"]  # and the fixed candidate paths probe to empty output

    check = next(c for c in run_checks(_ctx(tmp_path, env)).checks if c.name == "engine-python")
    assert not check.ok and "brew install python@" in check.fix


def test_venv_absent_fails_only_engine_venv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(tmp_path / "cache-empty"))
    env = _good_env(tmp_path, with_venv=False)
    report = run_checks(_ctx(tmp_path, env))

    assert not report.ok and [c.name for c in report.failures()] == ["engine-venv"]
    check = next(c for c in report.checks if c.name == "engine-venv")
    assert not check.ok and "nx2mac setup" in check.fix
    # The build pre-flight must ignore the venv check (setup creates it).
    assert report.failure_names(exclude=BUILD_PREFLIGHT_EXCLUDE) == ()


def test_fix_commands_are_unique_and_ordered() -> None:
    report = DoctorReport(
        checks=(
            Check(name="a", ok=False, detail="", fix="brew install zstd"),
            Check(name="b", ok=True, detail=""),
            Check(name="c", ok=False, detail="", fix="xcodebuild -downloadComponent MetalToolchain"),
            Check(name="d", ok=False, detail="", fix="brew install zstd"),  # duplicate
        )
    )

    assert report.fix_commands() == ["brew install zstd", "xcodebuild -downloadComponent MetalToolchain"]
    assert report.failure_names() == ("a", "c", "d")

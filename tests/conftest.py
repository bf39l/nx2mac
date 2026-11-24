"""Shared test doubles for nx2mac unit tests."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nx2mac.runner import RunResult

RunRule = tuple[str, "RunResult | Callable[[list[str]], RunResult]"]


@pytest.fixture(autouse=True)
def isolated_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test off the developer's machine.

    The cache root (engine checkouts, the managed venv) and the config file move into the test's
    temporary directory, and the platform is described as the target — a Mac on Apple silicon — so
    the same suite runs on macOS and on the Linux CI runner. Tests that need other facts inject
    their own `DoctorContext`/`FakeEnv` as before.
    """
    cache = tmp_path / "host-cache"
    monkeypatch.setenv("NX2MAC_CACHE_ROOT", str(cache))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "host-config"))
    monkeypatch.setenv("NX2MAC_TEST_PLATFORM", "darwin")
    monkeypatch.setenv("NX2MAC_TEST_MACHINE", "arm64")
    return cache


@dataclass
class FakeEnv:
    """In-memory implementation of the Env protocol.

    `run` rules are (regex, result) pairs matched in order against the joined
    argv; the first match wins. A rule's result may be a fixed RunResult or a
    callable of the argv for stateful behaviour. Calls with no matching rule
    succeed silently (returncode 0, empty output). Every call is recorded.
    """

    which_map: dict[str, Path] = field(default_factory=dict)
    run_rules: list[RunRule] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)
    stream_calls: list[tuple[list[str], Path | None]] = field(default_factory=list)
    stream_returncode: int = 0
    stream_lines: list[str] = field(default_factory=list)  # fed to on_line when provided
    #: (regex, effect) pairs matched in order against a streamed command; the
    #: effect returns the exit code and may mutate the fake (e.g. add a tool).
    stream_effects: list[tuple[str, Callable[[list[str]], int]]] = field(default_factory=list)

    def which(self, name: str) -> Path | None:
        return self.which_map.get(name)

    def run(self, args: Sequence[str], *, cwd: Path | None = None) -> RunResult:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        joined = " ".join(argv)
        for pattern, result in self.run_rules:
            if re.search(pattern, joined):
                return result(argv) if callable(result) else result
        return RunResult()

    def run_stream(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> int:
        """Record a streaming command and return the configured exit code."""
        argv = [str(a) for a in args]
        self.calls.append(argv)
        self.stream_calls.append((argv, cwd))
        if on_line is not None:
            for line in self.stream_lines:
                on_line(line)
        joined = " ".join(argv)
        for pattern, effect in self.stream_effects:
            if re.search(pattern, joined):
                return effect(argv)
        return self.stream_returncode

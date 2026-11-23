"""Shared test doubles for nx2mac unit tests."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from nx2mac.runner import RunResult

RunRule = tuple[str, "RunResult | Callable[[list[str]], RunResult]"]


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

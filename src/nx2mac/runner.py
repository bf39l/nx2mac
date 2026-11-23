"""Process execution primitives shared by every module that shells out.

Keeping the `Env` protocol in its own module means all logic (checks, git
operations, environment setup) is unit-testable: production uses `RealEnv`,
tests pass a fake.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class RunResult:
    """Outcome of one external command, with captured stdout/stderr text."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class Env(Protocol):
    """Minimal view of the system that a check needs. Tests substitute a fake."""

    def which(self, name: str) -> Path | None:
        """Path to an executable on PATH, or None when it is not installed."""
        ...

    def run(self, args: Sequence[str], *, cwd: Path | None = None) -> RunResult:
        """Run one command (not a shell), capturing stdout/stderr as text."""
        ...


class RealEnv:
    """Production Env backed by shutil.which and subprocess.run."""

    def which(self, name: str) -> Path | None:
        found = shutil.which(name)
        return Path(found) if found is not None else None

    def run(self, args: Sequence[str], *, cwd: Path | None = None) -> RunResult:
        proc = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd or None)
        return RunResult(stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)

"""Running the fixes that `nx2mac setup` offers.

Two kinds of work: commands we run for the user (Homebrew installs, the Xcode
Metal component) and the managed Python venv we create ourselves. Everything is
streamed line by line so both the terminal and the GUI can show progress.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .pyenv import PyEnvError, ensure_venv
from .remedies import Remedy
from .runner import Env


@dataclass(frozen=True)
class InstallResult:
    """Outcome of one attempted fix."""

    name: str  # the check it addressed
    command: str  # what ran (or "nx2mac setup" for the venv)
    returncode: int
    message: str = ""  # failure detail from the venv path

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "check": self.name,
            "command": self.command,
            "returncode": self.returncode,
            "ok": self.ok,
            "message": self.message,
        }


def install(
    env: Env,
    remedy: Remedy,
    cache_root: Path,
    on_line: Callable[[str], None] | None = None,
) -> InstallResult:
    """Run one remedy and report how it went.

    `engine-venv` is created in-process (no command line to stream); everything
    else runs `remedy.argv` through `env.run_stream`, so output goes live.
    """
    if remedy.argv is None:
        return InstallResult(remedy.name, remedy.summary, 1, "this fix has to be done manually")

    if remedy.kind == "internal":
        try:
            status = ensure_venv(cache_root, env=env)
        except PyEnvError as error:
            return InstallResult(remedy.name, remedy.summary, 1, str(error))
        if on_line is not None:
            on_line(f"python env ready: {status.python}")
        return InstallResult(remedy.name, remedy.summary, 0)

    command = " ".join(remedy.argv)
    if on_line is not None:
        on_line(f"$ {command}")
    returncode = env.run_stream(list(remedy.argv), on_line=on_line)
    return InstallResult(remedy.name, command, returncode)


def install_many(
    env: Env,
    remedies: Sequence[Remedy],
    cache_root: Path,
    on_line: Callable[[str], None] | None = None,
    on_result: Callable[[InstallResult], None] | None = None,
) -> list[InstallResult]:
    """Install several remedies in order, reporting each result as it happens."""
    results: list[InstallResult] = []
    for remedy in remedies:
        result = install(env, remedy, cache_root, on_line=on_line)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results

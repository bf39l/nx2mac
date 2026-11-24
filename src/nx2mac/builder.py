"""Game input validation and running the pinned engine's build script.

The heavy lifting is nxnative's own ``scripts/build_ipa_from_nsp.py``; this
module validates what we pass it (game folder / NUP + prod.keys), makes sure
the machine is ready, prepares the managed Python env, and runs the script with
output streaming straight to the console.
"""

import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from .doctor import BUILD_PREFLIGHT_EXCLUDE, DoctorContext, run_checks
from .engines import ResolvedEngine
from .pyenv import ensure_venv
from .runner import Env

#: Switch 1 container extensions the engine script understands.
SWITCH1_EXTENSIONS: frozenset[str] = frozenset({".nsp", ".nsz", ".xci", ".xcz"})
#: A Nintendo Update Package file (Switch 2 / NSO game).
NSO_EXTENSION: str = ".nup"
#: Filenames we recognise as a console's key set, in lookup order.
KEYS_FILENAMES: tuple[str, ...] = ("prod.keys", "prod_keys.txt")


class BuildError(Exception):
    """Raised when inputs are invalid or the build cannot start/run."""


@dataclass(frozen=True)
class GameInput:
    """A validated game location ready to hand to the engine script."""

    path: Path  # absolute folder (Switch 1 / NSO dump) or .nup file
    kind: str  # "switch1" | "nso"
    needs_prod_keys: bool


def _iter_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        yield path
        return
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            yield Path(dirpath) / name


def classify_game(path: Path) -> GameInput:
    """Work out what *path* holds (Switch 1 containers vs an NSO/NUP).

    Raises BuildError when the path does not exist or contains no game files.
    """
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise BuildError(f"game path does not exist: {resolved}")

    extensions = {p.suffix.lower() for p in _iter_files(resolved)}
    if NSO_EXTENSION in extensions:
        return GameInput(path=resolved, kind="nso", needs_prod_keys=False)
    switch1 = extensions & SWITCH1_EXTENSIONS
    if switch1:
        return GameInput(path=resolved, kind="switch1", needs_prod_keys=True)

    raise BuildError(
        f"no game files found in {resolved}: expected .{NSO_EXTENSION.lstrip('.')}, "
        + "/".join(sorted(e.lstrip(".") for e in SWITCH1_EXTENSIONS))
        + " (the folder may hold updates/DLC alongside the base game)"
    )


def find_prod_keys(explicit: Path | None, game_dir: Path) -> Path | None:
    """Locate prod.keys: an explicit path wins, else look beside/inside *game_dir*."""
    if explicit is not None:
        resolved = explicit.expanduser().resolve()
        if not resolved.is_file():
            raise BuildError(f"prod.keys file not found at {resolved}")
        return resolved

    for directory in (game_dir, game_dir.parent):
        for name in KEYS_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def build_argv(
    venv_python: Path,
    engine: ResolvedEngine,
    game: GameInput,
    keys: Path | None,
    output: Path | None,
    extra_args: Sequence[str],
) -> list[str]:
    """Assemble the argv for the engine's build script (macOS app target)."""
    args = [str(venv_python), str(engine.path / "scripts" / "build_ipa_from_nsp.py"), str(game.path)]
    if keys is not None:
        args.extend(["-k", str(keys)])
    args.extend(["--platform", "mac-app"])
    if output is not None:
        args.extend(["-o", str(output)])
    args.extend(extra_args)
    return args


@dataclass(frozen=True)
class BuildResult:
    """Outcome of one build attempt."""

    argv: tuple[str, ...]  # full command line that ran (interpreter included)
    app_path: Path | None  # the .app produced when given an explicit -o and exit was 0
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form for the final `build --json` result event."""
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "app_path": str(self.app_path) if self.app_path is not None else None,
            "argv": list(self.argv),
        }


def run_build(
    env: Env,
    cache_root: Path,
    engine: ResolvedEngine,
    game_path: Path,
    prod_keys: Path | None = None,
    output: Path | None = None,
    extra_args: Sequence[str] = (),
    on_line: Callable[[str], None] | None = None,
) -> BuildResult:
    """Preflight-check, prepare the Python env and stream the engine build.

    Raises BuildError when prerequisites still fail (with a pointer to
    `nx2mac doctor`), inputs are invalid, or prod.keys is missing for a
    Switch 1 game. PyEnvError from venv setup propagates as-is.
    """
    report = run_checks(DoctorContext(env=env))
    blocking = report.failure_names(BUILD_PREFLIGHT_EXCLUDE)
    if blocking:
        raise BuildError(
            "prerequisites are not met (failing checks: "
            + ", ".join(blocking)
            + "). Run `nx2mac doctor` for details or `nx2mac setup` to fix what can be fixed automatically."
        )

    game = classify_game(game_path)
    keys_dir = game.path if game.path.is_dir() else game.path.parent
    keys = find_prod_keys(prod_keys, keys_dir)
    if game.needs_prod_keys and keys is None:
        raise BuildError(
            f"no prod.keys found for this Switch 1 game. Pass --keys <file>, or put `prod.keys` "
            f"in (or next to) {game.path} — one console's key set decrypts that user's games."
        )

    venv = ensure_venv(cache_root, env=env)
    argv = build_argv(
        venv.python, engine, game, keys if game.needs_prod_keys or prod_keys is not None else None, output, extra_args
    )
    returncode = env.run_stream(argv, cwd=engine.path, on_line=on_line)

    app_path: Path | None = None
    if output is not None and returncode == 0:
        app_path = output.expanduser().resolve()

    return BuildResult(argv=tuple(argv), app_path=app_path, returncode=returncode)

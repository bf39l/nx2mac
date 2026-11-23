"""Managed Python environment for running the nxnative build script.

The engine's ``scripts/build_ipa_from_nsp.py`` needs an arm64 CPython >= 3.12
with a small set of packages (lz4, capstone 5.x, fonttools, zbic, zstd). To keep
builds independent from whatever site-packages the host happens to have, nx2mac
keeps its own venv under the cache root and installs exactly those modules.

Package pins mirror ``check_prereqs()`` in the nxnative build script:

* capstone is pinned to the 5.x series (the NSO rewriter uses Capstone-5 names)
* zstd comes from stdlib ``compression.zstd`` on Python >= 3.14, otherwise the
  ``backports.zstd`` package provides the same API
"""

from dataclasses import dataclass
from pathlib import Path

from .runner import Env, RealEnv, RunResult

MIN_VERSION: tuple[int, int] = (3, 12)

#: pip specs installed into the managed venv.
PIP_SPECS: tuple[str, ...] = ("lz4", "capstone>=5,<6", "fonttools", "zbic")

#: Ordered candidate interpreters; first match that is arm64 and new enough wins.
_CANDIDATE_PATHS: tuple[Path, ...] = (
    Path("/opt/homebrew/bin/python3"),  # Apple-silicon Homebrew Python
    Path("/usr/local/bin/python3"),     # Intel Homebrew Python (arch filter rejects it)
    Path("/usr/bin/python3"),           # Xcode CLT bundled CPython
)

#: Prints "MAJOR.MINOR MACHINE" using the interpreter's own view of itself.
_INTERPRETER_PROBE = (
    "import platform, sys\n"
    'print(f"{sys.version_info.major}.{sys.version_info.minor} {platform.machine()}")'
)

#: Imports every engine module in a fresh process and prints the missing names.
_MODULE_PROBE = (
    "import importlib.util\n"
    "missing = []\n"
    "def find(name):\n"
    "    try:\n"
    "        return importlib.util.find_spec(name) is not None\n"
    "    except ImportError:\n"
    "        return False\n"
    'for name in ("lz4", "fontTools", "zbic"):\n'
    "    if not find(name):\n"
    "        missing.append(name)\n"
    'if not find("capstone"):\n'
    '    missing.append("capstone>=5,<6")\n'
    "else:\n"
    "    import capstone\n"
    "    if capstone.cs_version()[0] != 5:\n"
    '        missing.append("capstone>=5,<6")\n'
    'if not (find("compression.zstd") or find("backports.zstd")):\n'
    '    missing.append("zstd")\n'
    "print(\" \".join(missing))\n"
)


class PyEnvError(Exception):
    """Raised when a suitable interpreter cannot be found or the venv cannot be built."""


def backports_zstd_spec(version_info: tuple[int, int]) -> str | None:
    """pip spec providing zstd compression for an interpreter version.

    Python 3.14+ has ``compression.zstd`` in the standard library; earlier
    versions need the API-compatible ``backports.zstd`` package.
    """
    return "backports.zstd" if version_info < (3, 14) else None


def required_pip_specs(version_info: tuple[int, int]) -> tuple[str, ...]:
    """The full list of pip specs the managed venv must contain."""
    spec = backports_zstd_spec(version_info)
    return PIP_SPECS + (spec,) if spec is not None else PIP_SPECS


@dataclass(frozen=True)
class InterpreterInfo:
    """An interpreter discovered on this machine, as it reports itself."""

    path: Path
    major: int
    minor: int
    arch: str  # platform.machine() as seen by that interpreter


def _probe_interpreter(env: Env, candidate: Path) -> InterpreterInfo | None:
    """Run one candidate and parse its self-reported version/architecture."""
    result = env.run([str(candidate), "-c", _INTERPRETER_PROBE])
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) != 2 or not parts[1]:
        return None
    try:
        major, minor = (int(part) for part in parts[0].split(".")[:2])
    except ValueError:
        return None
    return InterpreterInfo(path=candidate, major=major, minor=minor, arch=parts[1])


def find_engine_interpreters(env: Env) -> list[InterpreterInfo]:
    """All candidate interpreters that are arm64 and >= MIN_VERSION (best first)."""
    candidates: list[Path] = []
    found_python3 = env.which("python3")
    if found_python3 is not None:
        candidates.append(found_python3)
    for path in _CANDIDATE_PATHS:
        if path.is_file() and all(path != c for c in candidates):
            candidates.append(path)

    result: list[InterpreterInfo] = []
    for candidate in candidates:
        info = _probe_interpreter(env, candidate)
        if info is not None and info.arch == "arm64" and (info.major, info.minor) >= MIN_VERSION:
            result.append(info)
    return result


def best_engine_interpreter(env: Env) -> InterpreterInfo | None:
    """The first suitable interpreter, or None when no candidate qualifies."""
    interpreters = find_engine_interpreters(env)
    return interpreters[0] if interpreters else None


@dataclass(frozen=True)
class VenvStatus:
    """Result of probing the managed venv with its own python."""

    python: Path  # the venv's bin/python3 executable
    missing_modules: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_modules


def venv_dir(root: Path) -> Path:
    """Directory of the managed venv under a cache root."""
    return root / "python"


def probe_venv(env: Env, root: Path) -> VenvStatus | None:
    """Check which engine modules are importable in the managed venv.

    Returns None when no (working) python exists there yet.
    """
    python = venv_dir(root) / "bin" / "python3"
    if not python.is_file():
        return None
    result: RunResult = env.run([str(python), "-c", _MODULE_PROBE])
    if result.returncode != 0:
        return None
    missing = tuple(name for name in result.stdout.split() if name)
    return VenvStatus(python=python, missing_modules=missing)


def ensure_venv(
    cache_root: Path,
    env: Env | None = None,
    interpreter: InterpreterInfo | None = None,
) -> VenvStatus:
    """Create (if needed) the managed venv and install any missing engine modules.

    Raises PyEnvError with an actionable message when no suitable interpreter
    exists or creating/installing into the venv fails.
    """
    env = env if env is not None else RealEnv()

    interpreter = interpreter or best_engine_interpreter(env)
    if interpreter is None:
        raise PyEnvError(
            "no arm64 CPython >= 3.12 found (looked for 'python3' on PATH plus the "
            f"standard locations). Install one with: brew install python@{MIN_VERSION[1]}"
        )

    venv_path = venv_dir(cache_root)
    python = venv_path / "bin" / "python3"

    if not python.is_file():
        result: RunResult = env.run([str(interpreter.path), "-m", "venv", str(venv_path)])
        if result.returncode != 0 or not python.is_file():
            detail = (result.stderr or result.stdout).strip()
            raise PyEnvError(f"could not create venv at {venv_path}: {detail}")

    status = probe_venv(env, cache_root)
    if status is None:
        raise PyEnvError(
            f"managed python env at {venv_path} looks broken; delete that folder and "
            "re-run `nx2mac setup`"
        )

    if status.missing_modules:
        specs = required_pip_specs((interpreter.major, interpreter.minor))
        result = env.run([str(python), "-m", "pip", "install", "--quiet", *specs])
        detail = (result.stderr or result.stdout).strip()
        if result.returncode != 0:
            raise PyEnvError(
                f"pip install into the managed venv failed ({detail}). Retry manually:\n"
                f'  {python} -m pip install ' + " ".join(specs)
            )
        status = probe_venv(env, cache_root)
        if status is None or not status.ok:
            still_missing = status.missing_modules if status else ("unknown",)
            raise PyEnvError(
                f"managed venv still missing modules after install: {', '.join(still_missing)}"
            )
    return status

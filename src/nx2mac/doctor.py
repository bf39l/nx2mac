"""Dependency pre-flight check for building macOS apps via the nxnative engine.

Mirrors what ``scripts/build_ipa_from_nsp.py`` -> ``check_prereqs()`` and the
nxnative README require, but reports each item individually with a concrete fix:

* macOS on Apple silicon (the engine's in-repo binaries are arm64 universal)
* recent enough macOS (engine pins point at 26.6+)
* full Xcode (Command Line Tools alone is not enough) + Metal toolchain
* Homebrew tools used by the mac-app build path: zstd, glslangValidator, make
* an arm64 CPython >= 3.12 for the engine script and its managed venv

Every check takes a `DoctorContext` so unit tests can run on any machine with a
fake `Env`, injected platform facts and a temporary cache root (see
`config.cache_root()` / NX2MAC_CACHE_ROOT).
"""

import platform as _platform_module
import re
import sys as _sys
from dataclasses import dataclass, field, replace
from pathlib import Path  # noqa: F401  (kept for future path-based checks)

from .config import cache_root
from .pyenv import MIN_VERSION, best_engine_interpreter, probe_venv
from .remedies import remedy_for
from .runner import Env, RealEnv

#: Minimum macOS point release the engine is expected to run on (26.6+).
MIN_MACOS_VERSION: tuple[int, int] = (26, 6)

#: Check names that `build`'s pre-flight ignores: the managed venv is created
#: automatically during a build, so its absence alone must not block one.
BUILD_PREFLIGHT_EXCLUDE: frozenset[str] = frozenset({"engine-venv"})


@dataclass(frozen=True)
class Check:
    """Result of one individual prerequisite check."""

    name: str  # stable machine-readable key (e.g. "glslangValidator")
    ok: bool
    detail: str  # human explanation of what was found or why it failed
    fix: str = ""  # command / action that resolves a failure (empty when none)
    #: Command we can run for the user (None = manual only, or the check passed).
    install: tuple[str, ...] | None = None
    #: How the remedy works: "brew" | "xcode" | "internal" | "manual".
    kind: str = "manual"
    note: str = ""  # explanation for manual remedies
    link: str = ""  # download/documentation page

    @property
    def installable(self) -> bool:
        """Whether `nx2mac setup --install <name>` can fix this check."""
        return self.install is not None

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form used by `nx2mac doctor --json` / the GUI."""
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "fix": self.fix,
            "installable": self.installable,
            "install": list(self.install) if self.install is not None else None,
            "kind": self.kind,
            "note": self.note,
            "link": self.link,
        }


@dataclass(frozen=True)
class DoctorReport:
    """All checks for one run, in display order."""

    checks: tuple[Check, ...] = ()

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    def failure_names(self, exclude: frozenset[str] = frozenset()) -> tuple[str, ...]:
        """Names of failed checks minus the excluded set (build pre-flight)."""
        return tuple(check.name for check in self.failures() if check.name not in exclude)

    def fix_commands(self) -> list[str]:
        """Unique non-empty fix commands from failures, in first-seen order."""
        seen: dict[str, None] = {}
        for check in self.failures():
            if check.fix:
                seen.setdefault(check.fix)
        return list(seen)

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form: overall verdict + every check + the fix list."""
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "failures": [check.name for check in self.failures()],
            "fixes": self.fix_commands(),
        }


@dataclass(frozen=True)
class DoctorContext:
    """Facts and tools a run of the checks needs.

    Defaults describe the real machine; tests inject fakes for every field.
    """

    env: Env = field(default_factory=RealEnv)
    system: str = _sys.platform  # "darwin" on macOS
    machine: str = _platform_module.machine()  # "arm64" on Apple silicon


def check_macos(ctx: DoctorContext) -> Check:
    """nx2mac builds the mac-app target, which requires macOS."""
    ok = ctx.system == "darwin"
    return Check(
        name="macos",
        ok=ok,
        detail=f"{ctx.system} (must be 'darwin')" if not ok else f"{ctx.system}",
    )


def check_apple_silicon(ctx: DoctorContext) -> Check:
    """The engine's in-repo tools and Python interpreter want native arm64."""
    ok = ctx.machine == "arm64"
    return Check(
        name="apple-silicon",
        ok=ok,
        detail=f"{ctx.machine}" if ok else f"{ctx.machine} (must be 'arm64', i.e. not under Rosetta)",
        fix="" if ok else "run nx2mac natively on arm64 (not a x86_64/Rosetta Python)",
    )


def check_macos_version(ctx: DoctorContext) -> Check:
    """Engine pins target recent macOS releases; 26.6+ is the documented floor."""
    result = ctx.env.run(["sw_vers", "-productVersion"])
    match = re.search(r"(\d+)\.(\d+)", result.stdout or "") if ctx.system == "darwin" else None
    version = (int(match.group(1)), int(match.group(2))) if match else None
    ok = version is not None and version >= MIN_MACOS_VERSION
    floor = f"{MIN_MACOS_VERSION[0]}.{MIN_MACOS_VERSION[1]}"
    detail = (
        "not macOS"
        if ctx.system != "darwin"
        else f"macOS {version[0]}.{version[1]} (engine expects {floor}+)"
        if version is not None
        else f"unparseable sw_vers output: {(result.stdout or '').strip()!r}"
    )
    return Check(name="macos-version", ok=ok, detail=detail)


def check_xcode(ctx: DoctorContext) -> Check:
    """Full Xcode must be the active developer dir; Command Line Tools are not enough."""
    if ctx.system != "darwin":
        return Check(name="xcode", ok=False, detail="not macOS")
    xcodebuild = ctx.env.which("xcodebuild")
    dev_dir = (ctx.env.run(["xcode-select", "-p"]).stdout or "").strip()
    select_fix = "sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"

    if xcodebuild is None:
        return Check(
            name="xcode",
            ok=False,
            detail="'xcodebuild' not found on PATH",
            fix=f"install Xcode from the App Store, then run {select_fix}",
        )
    if not dev_dir or "CommandLineTools" in dev_dir:
        label = "Command Line Tools only" if dev_dir else "no active developer dir"
        return Check(
            name="xcode",
            ok=False,
            detail=f"{label} ({dev_dir!r})",
            fix=f"install full Xcode and run: {select_fix}",
        )
    return Check(name="xcode", ok=True, detail=dev_dir)


def check_metal_toolchain(ctx: DoctorContext) -> Check:
    """Xcode 27+ needs the separately downloadable Metal toolchain component."""
    if ctx.system != "darwin":
        return Check(name="metal-toolchain", ok=False, detail="not macOS")
    result = ctx.env.run(["xcrun", "-sdk", "macosx", "metal", "--version"])
    if result.returncode == 0:
        lines = (result.stdout or "").strip().splitlines()
        return Check(name="metal-toolchain", ok=True, detail=lines[0] if lines else "available")
    detail = ((result.stderr or result.stdout).strip()[:200]) or "'xcrun metal --version' failed"
    return Check(
        name="metal-toolchain",
        ok=False,
        detail=detail,
        fix="run: xcodebuild -downloadComponent MetalToolchain (or Xcode > Settings > Components)",
    )


def _check_tool(ctx: DoctorContext, tool: str, fix: str) -> Check:
    """Generic PATH lookup for a required command (zstd, glslangValidator, ...)."""
    path = ctx.env.which(tool)
    return Check(
        name=tool,
        ok=path is not None,
        detail=str(path) if path else f"'{tool}' not found on PATH",
        fix="" if path is not None else fix,
    )


def check_brew(ctx: DoctorContext) -> Check:
    """Homebrew installs the helper tools (zstd, glslang)."""
    return _check_tool(ctx, "brew", fix="install Homebrew first: https://brew.sh")


def check_engine_python(ctx: DoctorContext) -> Check:
    """An arm64 CPython >= MIN_VERSION must exist to host the engine's venv."""
    interpreter = best_engine_interpreter(ctx.env)
    floor = f"{MIN_VERSION[0]}.{MIN_VERSION[1]}"
    if interpreter is None:
        return Check(
            name="engine-python",
            ok=False,
            detail=f"no arm64 CPython >= {floor} found (looked for 'python3' plus standard locations)",
            fix=f"install one with Homebrew: brew install python@{MIN_VERSION[1]}",
        )
    return Check(
        name="engine-python",
        ok=True,
        detail=f"{interpreter.path} ({interpreter.major}.{interpreter.minor}, {interpreter.arch})",
    )


def check_engine_venv(ctx: DoctorContext) -> Check:
    """The managed venv (cache_root/python) must have all engine modules importable."""
    root = cache_root()
    status = probe_venv(ctx.env, root)
    if status is None:
        return Check(
            name="engine-venv",
            ok=False,
            detail=f"not created yet ({root / 'python'})",
            fix="run: nx2mac setup (creates the venv and pip-installs lz4, capstone 5.x, fonttools, zbic, zstd)",
        )
    if not status.ok:
        return Check(
            name="engine-venv",
            ok=False,
            detail=f"missing modules in {status.python.parent.parent}: {', '.join(status.missing_modules)}",
            fix="run: nx2mac setup (or delete the venv folder under the cache root and re-run)",
        )
    return Check(name="engine-venv", ok=True, detail=str(status.python.parent.parent))


def _with_remedy(check: Check, env: Env) -> Check:
    """Attach the actionable remedy to a failing check (passing checks stay bare)."""
    if check.ok:
        return check
    remedy = remedy_for(check.name)
    if remedy is None:
        return check
    return replace(
        check,
        fix=remedy.summary,
        install=remedy.argv if remedy.available(env) else None,
        kind=remedy.kind,
        note=remedy.note,
        link=remedy.link,
    )


def run_checks(ctx: DoctorContext) -> DoctorReport:
    """Run every check in display order."""
    checks = [
        check_macos(ctx),
        check_apple_silicon(ctx),
        check_macos_version(ctx),
        check_xcode(ctx),
        check_metal_toolchain(ctx),
        _check_tool(ctx, "git", fix="install git (bundled with Xcode or via brew install git)"),
        check_brew(ctx),
        _check_tool(ctx, "zstd", fix="brew install zstd"),
        _check_tool(ctx, "glslangValidator", fix="brew install glslang"),
        _check_tool(ctx, "make", fix="comes with Xcode / Command Line Tools (xcode-select --install)"),
        check_engine_python(ctx),
        check_engine_venv(ctx),
    ]
    return DoctorReport(checks=tuple(_with_remedy(check, ctx.env) for check in checks))

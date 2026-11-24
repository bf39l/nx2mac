"""How each prerequisite check can be fixed, and whether we can do it for you.

`doctor` reports what is wrong; this module says what to *do* about it. Checks
whose fix is a command we can run (Homebrew installs, the Metal toolchain
component, the managed venv) are `installable`: the CLI offers them as yes/no
prompts in `nx2mac setup`, and the GUI renders one Install button per check.

Checks we must not automate (installing Xcode, updating macOS, Homebrew itself)
are `manual` and carry a note and, where useful, a link.
"""

from dataclasses import dataclass

from .runner import Env

#: Homebrew formula used when no usable engine interpreter exists.
ENGINE_PYTHON_FORMULA: str = "python@3.13"

#: Downloadable Xcode component that provides `xcrun metal`.
METAL_COMPONENT_ARGV: tuple[str, ...] = ("xcodebuild", "-downloadComponent", "MetalToolchain")


@dataclass(frozen=True)
class Remedy:
    """What to do about one failing check."""

    name: str  # check name this addresses
    kind: str  # "brew" | "xcode" | "internal" | "manual"
    summary: str  # the command line, or a one-line action, as shown to the user
    argv: tuple[str, ...] | None = None  # command we can run; None = manual only
    note: str = ""  # extra explanation for manual steps
    link: str = ""  # documentation / download page

    def available(self, env: Env) -> bool:
        """Whether this remedy can run right now (Homebrew may be missing)."""
        if self.argv is None:
            return False
        if self.kind == "brew":
            return env.which("brew") is not None
        return True


_REMEDIES: dict[str, Remedy] = {
    "macos": Remedy(
        name="macos",
        kind="manual",
        summary="run nx2mac on macOS",
        note="The engine builds a macOS app; there is no build path for other systems.",
    ),
    "apple-silicon": Remedy(
        name="apple-silicon",
        kind="manual",
        summary="use a native arm64 Mac/Python (not Rosetta)",
        note="The engine's bundled tools and the managed interpreter are arm64.",
    ),
    "macos-version": Remedy(
        name="macos-version",
        kind="manual",
        summary="update macOS",
        note="System Settings > General > Software Update; the engine targets recent macOS releases.",
    ),
    "xcode": Remedy(
        name="xcode",
        kind="manual",
        summary="sudo xcode-select -s /Applications/Xcode.app/Contents/Developer",
        note="Install the full Xcode from the App Store (Command Line Tools are not enough), "
        "then point the developer directory at it.",
        link="https://apps.apple.com/app/xcode/id497799835",
    ),
    "metal-toolchain": Remedy(
        name="metal-toolchain",
        kind="xcode",
        summary=" ".join(METAL_COMPONENT_ARGV),
        argv=METAL_COMPONENT_ARGV,
        note="Several hundred MB; Xcode > Settings > Components offers the same download.",
    ),
    "git": Remedy(
        name="git",
        kind="brew",
        summary="brew install git",
        argv=("brew", "install", "git"),
        note="git is also bundled with the Command Line Tools (xcode-select --install).",
    ),
    "brew": Remedy(
        name="brew",
        kind="manual",
        summary='/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
        note="Homebrew installs zstd, glslang and (if needed) Python. We never install Homebrew for you.",
        link="https://brew.sh",
    ),
    "zstd": Remedy(name="zstd", kind="brew", summary="brew install zstd", argv=("brew", "install", "zstd")),
    "glslangValidator": Remedy(
        name="glslangValidator",
        kind="brew",
        summary="brew install glslang",
        argv=("brew", "install", "glslang"),
    ),
    "make": Remedy(
        name="make",
        kind="manual",
        summary="xcode-select --install",
        note="make ships with the Command Line Tools; installing Xcode provides it too.",
    ),
    "engine-python": Remedy(
        name="engine-python",
        kind="brew",
        summary=f"brew install {ENGINE_PYTHON_FORMULA}",
        argv=("brew", "install", ENGINE_PYTHON_FORMULA),
        note="The engine needs an arm64 CPython 3.12 or newer to host its managed venv.",
    ),
    "engine-venv": Remedy(
        name="engine-venv",
        kind="internal",
        summary="nx2mac setup (create the managed venv)",
        argv=("nx2mac", "setup"),
        note="Creates <cache>/python and pip-installs lz4, capstone 5.x, fonttools, zbic and zstd.",
    ),
}


def all_remedies() -> dict[str, Remedy]:
    """Every remedy, keyed by the check name it addresses."""
    return dict(_REMEDIES)


def remedy_for(check_name: str) -> Remedy | None:
    """The remedy for one check name, or None when we have nothing to suggest."""
    return _REMEDIES.get(check_name)


def installable_names() -> tuple[str, ...]:
    """Check names whose fix we can run (in catalog order)."""
    return tuple(name for name, remedy in _REMEDIES.items() if remedy.argv is not None)

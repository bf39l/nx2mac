"""Game input validation and running the pinned engine's build script.

The heavy lifting is nxnative's own ``scripts/build_ipa_from_nsp.py``; this
module validates what we pass it (game folder / NUP + prod.keys), makes sure
the machine is ready, prepares the managed Python env, and runs the script with
output streaming straight to the console.
"""

import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import library as library_mod
from .cheats import CheatToolError, anchor_build_cheats
from .doctor import BUILD_PREFLIGHT_EXCLUDE, DoctorContext, run_checks
from .engines import ResolvedEngine
from .inputs import GAME_EXTENSIONS, KEYS_FILENAMES, NSO_EXTENSION, SWITCH1_EXTENSIONS
from .probe import probe as probe_path
from .pyenv import ensure_venv
from .runner import Env
from .staging import prepare_staging

__all__ = [
    "GAME_EXTENSIONS",
    "KEYS_FILENAMES",
    "NSO_EXTENSION",
    "SWITCH1_EXTENSIONS",
    "BuildError",
    "BuildResult",
    "GameInput",
    "build_argv",
    "classify_game",
    "find_prod_keys",
    "run_build",
    "validate_extras",
]


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


def find_prod_keys(explicit: Path | None, game_dir: Path, out_dir: Path | None = None) -> Path | None:
    """Locate prod.keys: explicit wins, else inside/beside the game, else in *out_dir*."""
    if explicit is not None:
        resolved = explicit.expanduser().resolve()
        if not resolved.is_file():
            raise BuildError(f"prod.keys file not found at {resolved}")
        return resolved

    directories = [game_dir, game_dir.parent]
    if out_dir is not None:
        directories.append(out_dir.expanduser().resolve())
    for directory in directories:
        for name in KEYS_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def build_argv(
    venv_python: Path,
    engine: ResolvedEngine,
    game_dir: Path,
    keys: Path | None,
    output: Path | None,
    extra_args: Sequence[str],
    work_dir: Path | None = None,
    env_file: Path | None = None,
) -> list[str]:
    """Assemble the argv for the engine's build script (macOS app target).

    `work_dir` keeps the engine's build folder (extracted files, shader caches)
    where the caller wants it; the engine writes the app next to that folder when
    no explicit `-o` is given.
    """
    args = [str(venv_python), str(engine.path / "scripts" / "build_ipa_from_nsp.py"), str(game_dir)]
    if keys is not None:
        args.extend(["-k", str(keys)])
    args.extend(["--platform", "mac-app"])
    if work_dir is not None:
        args.extend(["--work-dir", str(work_dir)])
    if env_file is not None:
        # baked into the app: it is what points the engine's state at the game folder
        args.extend(["--env-file", str(env_file)])
    if output is not None:
        args.extend(["-o", str(output)])
    args.extend(extra_args)
    return args


def _safe_name(text: str) -> str:
    """Filesystem-friendly fragment for a folder name (CJK titles are fine)."""
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in text)
    return cleaned.strip("-._")[:60] or "game"


def title_id_of(game: GameInput) -> str | None:
    """The game's 16-hex title ID, when a filename or the folder name carries one."""
    scan = probe_path(game.path)
    if scan.game_file is not None:
        for package in scan.packages:
            if package.path == scan.game_file and package.title_id is not None:
                return package.title_id
    for package in scan.packages:
        if package.title_id is not None:
            return package.title_id
    return None


def work_dir_for(out_dir: Path, game: GameInput, title_id: str | None = None) -> Path:
    """The engine's build folder inside the output directory.

    Named after the title ID like NX2Apple's (`<TITLEID>_build`): stable whatever
    the dump folder is called, and short enough to stay readable.
    """
    if title_id:
        return out_dir / f"{_safe_name(title_id)}_build"
    base = game.path.stem if game.path.is_file() else game.path.name
    return out_dir / f"{_safe_name(base)}_build"


def discover_app(out_dir: Path) -> Path | None:
    """Newest `.app` directly inside *out_dir* (the engine names the app itself)."""
    apps = [entry for entry in out_dir.glob("*.app") if entry.is_dir()]
    return max(apps, key=lambda entry: entry.stat().st_mtime) if apps else None


def validate_extras(paths: Sequence[Path], flag: str) -> list[Path]:
    """Resolve user-chosen update/DLC paths; each must exist and look packable."""
    resolved: list[Path] = []
    for raw in paths:
        path = raw.expanduser().resolve()
        if not path.exists():
            raise BuildError(f"{flag} path does not exist: {path}")
        if path.is_file() and path.suffix.lower() not in GAME_EXTENSIONS:
            expected = "/".join(sorted(e.lstrip(".") for e in GAME_EXTENSIONS))
            raise BuildError(f"{flag} {path.name} is not a game package (expected {expected})")
        if path.is_dir() and not any(
            entry.suffix.lower() in GAME_EXTENSIONS for entry in path.rglob("*") if entry.is_file()
        ):
            raise BuildError(f"{flag} folder holds no game packages: {path}")
        resolved.append(path)
    return resolved


def _anchor_cheats(
    env: Env,
    library: Path,
    key: str,
    engine: ResolvedEngine,
    cache_root: Path,
    on_line: Callable[[str], None] | None = None,
) -> None:
    """Re-anchor the game's cheat file to the build that just finished.

    A cheat file ships Switch-absolute addresses, which point nowhere in this app; the extraction
    the build just made lets the engine's `cheat_anchor.py` rewrite them for this build. Missing an
    extracted `main` (or the tool) is not an error: the file is left exactly as it was.
    """
    say = on_line if on_line is not None else (lambda text: print(text, flush=True))
    folder = library_mod.cheats_dir(library, key)
    files = sorted(folder.glob("*.txt"))
    if not files:
        return
    main = library_mod.extracted_main(library, key)
    script = engine.path / "scripts" / "cheat_anchor.py"
    if main is None or not script.is_file():
        say("cheats: no extracted ExeFS/main or cheat_anchor.py; the cheat file is left as it is")
        return

    say(f"cheats: re-anchoring {files[0].name} for this build …")
    toml = library_mod.mods_dir(library, key) / f"{key}.cheats.toml"
    venv = ensure_venv(cache_root, env=env)
    try:
        anchor_build_cheats(
            env, venv.python, script, files[0], main, toml, folder, on_line=lambda text: say(f"cheats: {text}")
        )
    except CheatToolError as error:
        say(f"cheats: re-anchor failed ({error})")
        return
    anchored = sorted(folder.glob("*.txt"))
    if anchored:
        say(f"cheats: {anchored[0].name} is anchored to this build")
        say(f"cheats: source kept for later builds: {toml}")


def _file_app(library: Path, key: str, produced: Path | None) -> Path | None:
    """Rename the finished app to the game's display name, like NX2Apple's output.

    Tried in order: `<name>.app`, `<name> [<title id>].app`, `<key>.app`. An app of
    the same game that is already there is replaced, so a rebuild does not leave two.
    """
    if produced is None or not produced.is_dir():
        return produced
    metadata = library_mod.app_metadata(produced)
    name = str(metadata.get("name") or "").strip()
    wanted = [library_mod.safe_name(name)] if name else []
    if metadata.get("title_id"):
        wanted.append(f"{library_mod.safe_name(name)} [{metadata['title_id']}]" if name else str(metadata["title_id"]))
    wanted.append(key)

    for candidate in wanted:
        target = library / f"{candidate}.app"
        if target == produced:
            return produced
        if target.exists():
            if str(library_mod.app_metadata(target).get("title_id") or "") != str(metadata.get("title_id") or "?"):
                continue  # another game with the same display name
            shutil.rmtree(target)  # the same game built again: replace it
        produced.rename(target)
        return target
    return produced


def _record_in_library(
    library: Path,
    key: str,
    engine: ResolvedEngine,
    game: GameInput,
    app_path: Path | None,
) -> tuple[Path | None, Path | None]:
    """Write the app's manifest sidecar and the env the launcher reads per player."""
    metadata = library_mod.app_metadata(app_path) if app_path is not None else {}
    title_id = str(metadata.get("title_id") or key)

    env_file = library_mod.write_player_env(library, key)
    if app_path is not None:
        library_mod.bake_env(app_path, library_mod.write_build_env(library, key))

    manifest = library_mod.write_manifest(
        app_path.with_suffix(".nx2mac.json") if app_path is not None else library_mod.app_root(library) / f"{key}.nx2mac.json",
        name=str(metadata.get("name") or key),
        app_path=app_path or library,
        title_id=title_id,
        version=metadata.get("version"),
        engine_source=engine.source.id,
        engine_ref=engine.ref,
        engine_commit=engine.commit,
        patches=engine.patches,
        inputs={"game": str(game.path), "kind": game.kind},
    )
    return manifest, env_file


#: The engine stops that have an override flag — the ones a "build anyway" can get past
#: (NX2Apple offers exactly these three, BuildFailureKind.overrideFlag).
OVERRIDABLE: tuple[tuple[str, str, str], ...] = (
    ("--allow-damaged", "so the game files are damaged", "pack the damaged game files as they are"),
    ("--allow-other-metal", "ALLOW_OTHER_METAL", "build the shaders with this Mac's Metal compiler"),
    ("--allow-shader-regressions", "a change to the translation chain broke them",
     "pack the app without the shaders that stopped translating"),
)


@dataclass(frozen=True)
class BuildFailure:
    """Why a build stopped, and the flag that would get past it (when there is one)."""

    message: str  # the engine's own last words
    override: str | None = None  # e.g. "--allow-damaged"; None = nothing can be built around it
    note: str = ""  # what the override means, for the user

    def to_dict(self) -> dict[str, object]:
        return {"message": self.message, "override": self.override, "note": self.note}


def classify_failure(lines: Sequence[str]) -> BuildFailure | None:
    """Read the engine's output for a stop that has an override, else the last red line."""
    text = "\n".join(lines[-200:])
    for flag, needle, note in OVERRIDABLE:
        if needle in text:
            return BuildFailure(message=needle, override=flag, note=note)
    red = [line for line in lines if "[RED]" in line]
    if red:
        return BuildFailure(message=red[-1].strip()[:400])
    return None


@dataclass(frozen=True)
class BuildResult:
    """Outcome of one build attempt."""

    argv: tuple[str, ...]  # full command line that ran (interpreter included)
    app_path: Path | None  # the .app produced when given an explicit -o and exit was 0
    returncode: int
    input_path: Path | None = None  # folder handed to the engine (a staging dir when extras were chosen)
    out_dir: Path | None = None  # where the app, build folder and keys were collected
    work_dir: Path | None = None  # the engine's build folder (extracted files, shader caches)
    keys_path: Path | None = None  # prod.keys the build actually used
    failure: BuildFailure | None = None  # why it stopped, when the engine said
    library_dir: Path | None = None  # the game library the build was filed into
    manifest: Path | None = None  # <library>/<Game>/nx2mac.json
    env_file: Path | None = None  # the engine's per-game env.txt we pointed at the folder

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form for the final `build --json` result event."""
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "app_path": str(self.app_path) if self.app_path is not None else None,
            "input_path": str(self.input_path) if self.input_path is not None else None,
            "out_dir": str(self.out_dir) if self.out_dir is not None else None,
            "work_dir": str(self.work_dir) if self.work_dir is not None else None,
            "keys_path": str(self.keys_path) if self.keys_path is not None else None,
            "failure": self.failure.to_dict() if self.failure is not None else None,
            "library_dir": str(self.library_dir) if self.library_dir is not None else None,
            "manifest": str(self.manifest) if self.manifest is not None else None,
            "env_file": str(self.env_file) if self.env_file is not None else None,
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
    updates: Sequence[Path] = (),
    dlcs: Sequence[Path] = (),
    out_dir: Path | None = None,
    library_dir: Path | None = None,
) -> BuildResult:
    """Preflight-check, prepare the Python env and stream the engine build.

    Raises BuildError when prerequisites still fail (with a pointer to
    `nx2mac doctor`), inputs are invalid, or prod.keys is missing for a
    Switch 1 game. PyEnvError from venv setup propagates as-is.

    With `library_dir` set the game gets `<library>/<Game>/` (app + saves, logs,
    mods, cheats) and the engine's per-game env.txt is pointed at those folders;
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
    library = library_mod.require_library(library_dir) if library_dir is not None else None
    out_dir = out_dir.expanduser().resolve() if out_dir is not None else None
    # The game's own directory: where the engine puts things by default, and the
    # default output folder when we have to stage inputs elsewhere.
    keys_dir = game.path if game.path.is_dir() else game.path.parent
    keys = find_prod_keys(prod_keys, keys_dir, out_dir)
    if game.needs_prod_keys and keys is None:
        raise BuildError(
            f"no prod.keys found for this Switch 1 game. Pass --keys <file>, or put `prod.keys` "
            f"in (or next to) {game.path} — one console's key set decrypts that user's games."
        )

    updates = validate_extras(updates, "--update")
    dlcs = validate_extras(dlcs, "--dlc")
    # The engine wants a folder: stage a lone Switch 1 package (and any extras) for it.
    staging = bool(updates or dlcs) or (game.kind == "switch1" and game.path.is_file())
    input_dir = game.path
    if staging:
        if game.kind == "nso":
            raise BuildError(
                "a .nup already carries its own updates and DLC; pick them inside the container "
                "(or use the engine's --aoc-dir for separate DLC images) instead of --update/--dlc"
            )
        input_dir = prepare_staging(cache_root, game.path, updates, dlcs)

    # Default the output folder to the game's directory. That matches what the
    # engine does on its own, and matters a lot when we staged the inputs: the
    # engine would otherwise drop the app inside our cache staging folder.
    if out_dir is None and staging:
        out_dir = keys_dir

    title_id = title_id_of(game) if (library is not None or out_dir is not None) else None
    key = title_id or library_mod.safe_name(game.path.stem if game.path.is_file() else game.path.name)
    work_dir: Path | None = None
    env_file: Path | None = None
    if library is not None:
        # keeps the engine's support dir, logs, saves, cheats and screenshots here
        env_file = library_mod.write_build_env(library, key)
        work_dir = library_mod.build_dir(library, key)
        out_dir = library_mod.app_root(library)
        if output is None:
            output = library_mod.app_root(library) / f"{key}.app"  # renamed to the game's name once it exists
    elif out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        work_dir = work_dir_for(out_dir, game, title_id)

    tail: list[str] = []

    def watch(line: str) -> None:
        tail.append(line)
        del tail[:-200]
        if on_line is not None:
            on_line(line)

    venv = ensure_venv(cache_root, env=env)
    argv = build_argv(
        venv.python,
        engine,
        input_dir,
        keys if game.needs_prod_keys or prod_keys is not None else None,
        output,
        extra_args,
        work_dir=work_dir,
        env_file=env_file,
    )
    returncode = env.run_stream(argv, cwd=engine.path, on_line=watch)

    app_path: Path | None = None
    if returncode == 0:
        if library is not None:
            app_path = _file_app(library_mod.app_root(library), key, output.expanduser().resolve() if output is not None else None)
        elif output is not None:
            app_path = output.expanduser().resolve()
        elif out_dir is not None:
            app_path = discover_app(out_dir)  # the engine names it and writes it here

    manifest: Path | None = None
    player_env: Path | None = None
    if library is not None and returncode == 0:
        manifest, player_env = _record_in_library(library, key, engine, game, app_path)
        _anchor_cheats(env, library, key, engine, cache_root, on_line)

    return BuildResult(
        argv=tuple(argv),
        app_path=app_path,
        returncode=returncode,
        input_path=input_dir,
        out_dir=out_dir,
        work_dir=work_dir,
        keys_path=keys,
        failure=classify_failure(tail) if returncode != 0 else None,
        library_dir=library_mod.app_root(library) if library is not None else None,
        manifest=manifest,
        env_file=player_env,
    )

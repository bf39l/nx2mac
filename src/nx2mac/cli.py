"""nx2mac command-line interface (typer + rich).

Every command has a machine-readable form (``--json``) because the planned Swift
GUI drives this CLI as a subprocess: single-document commands print one JSON
object, ``build`` prints NDJSON events (engine info, engine log lines, result).
Human output is just a different renderer of the same core results, so the
check/build logic never depends on typer.
"""

import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import VERSION, emit
from . import library as library_mod
from .builder import BuildError, run_build
from .cheats import CheatToolError, reanchor
from .config import cache_root, primary_library
from .config import load as load_config
from .config import save as save_config
from .doctor import DoctorContext, DoctorReport, run_checks
from .engines import EngineError, EngineSource, all_sources, describe, ensure_checkout, source_by_id
from .installer import InstallResult, install
from .probe import probe as probe_path
from .progress import Translator
from .pyenv import PyEnvError, ensure_venv
from .remedies import Remedy, installable_names, remedy_for
from .runner import Env, RealEnv

app = typer.Typer(
    name="nx2mac",
    help="Build Nintendo Switch / Switch 2 games into native macOS .app bundles.",
    no_args_is_help=True,
)
console = Console()

# Progress must reach a pipe (the GUI) as it happens, not when the buffer fills.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # not a TextIOWrapper (or already closed)
        pass


@dataclass
class CliState:
    """Per-invocation options shared by all commands."""

    json: bool = False  # emit machine-readable output instead of rich rendering


#: Help text kept identical on the global and per-command flags.
_JSON_HELP = "Machine-readable output: one JSON document (NDJSON events for build)."


@app.callback()
def main(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Global options; stored on the typer context for every command."""
    ctx.obj = CliState(json=json_output)


def _json_option() -> bool:
    """A fresh `--json` option for one command (typer builds its interface from defaults)."""
    return typer.Option(False, "--json", help=_JSON_HELP)


def _state(ctx: typer.Context, json_flag: bool = False) -> CliState:
    """Combine the global `--json` (before the subcommand) with a per-command one."""
    inherited = ctx.obj if isinstance(ctx.obj, CliState) else CliState()
    return CliState(json=inherited.json or json_flag)


def _enter(ctx: typer.Context, json_flag: bool) -> bool:
    """Fold the per-command `--json` into the context state; return machine-readable mode."""
    state = _state(ctx, json_flag)
    ctx.obj = state
    return state.json


def _interactive() -> bool:
    """Seam for tests: whether we may ask the user yes/no questions."""
    return sys.stdin.isatty()


def _get_env() -> Env:
    """Seam for tests: production uses RealEnv; unit tests monkeypatch this."""
    return RealEnv()


def _fail(ctx: typer.Context, message: str, code: int = 1) -> None:
    """Report an error in the active output mode, then exit non-zero."""
    if _state(ctx).json:
        emit.event("error", message=message)
    else:
        console.print(f"[red]{message}[/]")
    raise typer.Exit(code=code)


def _print_report(report: DoctorReport) -> None:
    table = Table(header_style="bold", show_edge=False)
    table.add_column("status")
    table.add_column("check", style="cyan")
    table.add_column("detail")
    table.add_column("fix", style="yellow")
    for check in report.checks:
        status = "[green]ok[/]" if check.ok else "[bold red]FAIL[/]"
        table.add_row(status, check.name, check.detail or "-", check.fix or "")
    console.print(table)


def _prompt_source(sources: tuple[EngineSource, ...]) -> EngineSource:
    """Interactive menu; returns the chosen source."""
    console.print("\n[bold]Choose an nxnative engine source:[/]")
    for index, source in enumerate(sources, 1):
        console.print(f" {index}. [green]{source.label}[/] - {source.url}")
    while True:
        raw = typer.prompt("Number", default=str(len(sources))).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(sources):
            return sources[int(raw) - 1]
        console.print(f"[yellow]Pick a number between 1 and {len(sources)}.[/]")


def _resolve_selection(
    ctx: typer.Context, source_id: str | None, ref: str | None
) -> tuple[EngineSource, str | None]:
    """Resolve (source id, ref): explicit flags > saved config > menu/default.

    Exits the CLI with an error when the resolved id is unknown.
    """
    cfg = load_config()
    selected_ref: str | None = ref if ref is not None else cfg.engine_ref
    selected_id: str | None = source_id or cfg.engine_id

    if selected_id is None:
        sources = all_sources()
        if sys.stdin.isatty() and not _state(ctx).json:
            return _prompt_source(sources), selected_ref
        selected_id = sources[0].id
        if not _state(ctx).json:
            console.print(
                f"[dim]No engine source saved; defaulting to {sources[0].label}. "
                "Persist a choice with `nx2mac engine --source <id>`.[/]"
            )

    source = source_by_id(selected_id)
    if source is None:
        valid = ", ".join(s.id for s in all_sources())
        _fail(ctx, f"Unknown engine source {selected_id!r}. Valid ids: {valid}")
    return source, selected_ref


def _checkout(ctx: typer.Context, source: EngineSource, ref: str | None):
    """Clone/fetch+pin the engine, reporting errors in the active output mode."""
    try:
        return ensure_checkout(_get_env(), cache_root().expanduser(), source, ref)
    except EngineError as error:
        _fail(ctx, str(error))


@app.command()
def version(ctx: typer.Context, json_flag: bool = _json_option()) -> None:
    """Print the nx2mac version."""
    if _enter(ctx, json_flag):
        emit.emit({"name": "nx2mac", "version": VERSION})
        return
    console.print(VERSION)


@app.command()
def doctor(ctx: typer.Context, json_flag: bool = _json_option()) -> None:
    """Check every prerequisite for building macOS apps; exit non-zero if any fail."""
    report = run_checks(DoctorContext(env=_get_env()))
    if _enter(ctx, json_flag):
        emit.emit(report.to_dict())
    else:
        _print_report(report)
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def setup(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    install_names: list[str] = typer.Option(
        [],
        "--install",
        "-i",
        help="install these checks without prompting (repeatable), e.g. --install zstd --install glslangValidator.",
    ),
    install_all: bool = typer.Option(
        False, "--all", help="install every fixable check without prompting."
    ),
    assume_yes: bool = typer.Option(
        False, "--yes", "-y", help="say yes to every install prompt (interactive mode)."
    ),
    no_prompt: bool = typer.Option(
        False, "--no-prompt", help="never ask; only report what is missing."
    ),
) -> None:
    """Check the prerequisites and offer to install each missing piece."""
    env = _get_env()
    cache = cache_root().expanduser()
    machine_readable = _enter(ctx, json_flag)

    report = run_checks(DoctorContext(env=env))

    # --json / --no-prompt: report only, with the remedies the GUI turns into buttons.
    if machine_readable and not (install_names or install_all):
        emit.emit(report.to_dict())
        raise typer.Exit(code=0 if report.ok else 1)
    if no_prompt and not (install_names or install_all):
        _print_report(report)
        _print_manual_steps(report)
        raise typer.Exit(code=0 if report.ok else 1)

    wanted: list[Remedy] = _select_remedies(report, install_names, install_all)
    if not machine_readable and not wanted and not no_prompt and _interactive():
        wanted = _ask_which_to_install(report, assume_yes)

    results: list[InstallResult] = []
    for remedy in wanted:
        if machine_readable:
            emit.event("install-start", check=remedy.name, command=list(remedy.argv or ()))
        elif not no_prompt:
            console.print(f"\n[bold]Installing {remedy.name}[/] [dim]({remedy.summary})[/]")

        result = install(
            env,
            remedy,
            cache,
            on_line=emit.log_line if machine_readable else None,
        )
        results.append(result)

        if machine_readable:
            emit.event("install-done", **result.to_dict())
        elif not no_prompt:
            if result.ok:
                console.print(f"[green]{remedy.name} installed.[/]")
            else:
                console.print(f"[red]{remedy.name} install failed[/] ({result.command}): {result.message or 'see output'}")

    report = run_checks(DoctorContext(env=env))
    if machine_readable:
        emit.event(
            "setup",
            ok=report.ok,
            installed=[result.to_dict() for result in results],
            doctor=report.to_dict(),
        )
        raise typer.Exit(code=0 if report.ok else 1)

    console.print("[bold]Current prerequisite status:[/]")
    _print_report(report)
    if report.ok and not results:
        console.print("\n[green]All prerequisites met - you can build.[/]")
        return
    _print_manual_steps(report)
    raise typer.Exit(code=0 if report.ok else 1)


def _select_remedies(
    report: DoctorReport, install_names: list[str], install_all: bool
) -> list[Remedy]:
    """Remedies chosen explicitly (--install/--all), in doctor order."""
    valid = set(installable_names())
    for name in install_names:
        if name not in valid:
            raise typer.BadParameter(
                f"unknown or non-installable check {name!r}; installable: {', '.join(sorted(valid))}"
            )
    failing = [check.name for check in report.failures()]
    chosen = set(install_names)
    remedy_names = [name for name in failing if name in valid and (install_all or name in chosen)]
    # A named check that currently passes is skipped silently (nothing to fix).
    return [remedy for name in remedy_names if (remedy := remedy_for(name)) is not None]


def _ask_which_to_install(report: DoctorReport, assume_yes: bool) -> list[Remedy]:
    """Interactive yes/no per installable failing check."""
    wanted: list[Remedy] = []
    for check in report.failures():
        remedy = remedy_for(check.name)
        if remedy is None:
            continue
        if remedy.argv is None:
            console.print(f"[yellow]{check.name}[/]: {remedy.summary}")
            if remedy.note:
                console.print(f"  [dim]{remedy.note}[/]")
            continue
        question = f"Install {check.name}? ({remedy.summary})"
        # The managed venv is ours and touches nothing system-wide: default to yes.
        default = remedy.kind == "internal"
        if assume_yes or typer.confirm(question, default=default):
            wanted.append(remedy)
    return wanted


def _print_manual_steps(report: DoctorReport) -> None:
    """Show the fixes we cannot do ourselves."""
    manual = [check for check in report.failures() if not check.installable]
    if not manual:
        return
    console.print("\n[bold red]Still to do manually:[/]")
    for check in manual:
        console.print(f"  [yellow]{check.name}[/]: {check.fix or check.detail}")
        if check.note:
            console.print(f"    [dim]{check.note}[/]")
        if check.link:
            console.print(f"    [dim]{check.link}[/]")


@app.command()
def engine(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    source_id: str | None = typer.Option(None, "--source", help="Engine id to select (upstream|fork)."),
    ref: str | None = typer.Argument(None, help="Branch, tag or commit to pin (default: the source's default branch)."),
    show: bool = typer.Option(False, "--show", help="Only report the saved selection (no network)."),
) -> None:
    """Select and pin an nxnative engine source from GitHub, then show exactly what it resolved to."""
    _enter(ctx, json_flag)

    source, selected_ref = _resolve_selection(ctx, source_id, ref)
    if show:
        cfg = load_config()
        if _state(ctx).json:
            emit.emit({"source": {"id": source.id, "label": source.label, "url": source.url}, "ref": selected_ref,
                       "saved": {"engine_id": cfg.engine_id, "engine_ref": cfg.engine_ref}})
            return
        saved = f"id={cfg.engine_id}, ref={cfg.engine_ref}" if cfg.engine_id else "nothing saved yet"
        console.print(f"[bold]{source.label}[/] ({source.id}) - ref {selected_ref or source.default_ref!r}")
        console.print(f"saved selection: [dim]{saved}[/]")
        return

    resolved = _checkout(ctx, source, selected_ref)
    # Keep the rest of the config (the game library, for one): save the whole thing.
    save_config(replace(load_config(), engine_id=source.id, engine_ref=resolved.ref))

    if _state(ctx).json:
        emit.emit({"engine": resolved.to_dict(), "saved": {"engine_id": source.id, "engine_ref": resolved.ref}})
        return
    console.print(f"[bold green]Engine pinned:[/] {describe(resolved)}")
    console.print(f"checkout: [dim]{resolved.path}[/]")


@app.command()
def library(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    add: Path | None = typer.Option(None, "--add", metavar="DIR", help="add a folder to your game libraries."),
    remove: Path | None = typer.Option(None, "--remove", metavar="DIR", help="forget one of the folders."),
    clear: bool = typer.Option(False, "--clear", help="forget every game library folder."),
) -> None:
    """Show or choose the folders that hold your built games (more than one is fine)."""
    machine_readable = _enter(ctx, json_flag)
    cfg = load_config()
    dirs = list(cfg.library_dirs)
    changed = False

    if add is not None:
        try:
            chosen = str(library_mod.check_library_choice(add))
        except library_mod.LibraryError as error:
            _fail(ctx, str(error))
        if chosen not in dirs:
            dirs.append(chosen)
            changed = True
    if remove is not None:
        wanted = str(remove.expanduser().resolve())
        if wanted not in dirs:
            _fail(ctx, f"{wanted} is not one of your game libraries")
        dirs = [entry for entry in dirs if entry != wanted]
        changed = True
    if clear:
        dirs = []
        changed = True
    if changed:
        save_config(replace(cfg, library_dirs=tuple(dirs)))
        library_mod.write_envs_for([Path(entry) for entry in dirs], Path(cfg.cloud_dir) if cfg.cloud_dir else None)

    payload = library_mod.describe_libraries([Path(entry) for entry in dirs])
    if machine_readable:
        emit.emit(payload)
        return
    if not dirs:
        console.print("[yellow]No game library set.[/] Add one with `nx2mac library --add <existing folder>`.")
        return
    for directory in payload["dirs"]:  # type: ignore[union-attr]
        console.print(f"[bold]Game library:[/] {directory}")
    games = payload["games"]
    if not games:
        console.print("[dim]No games built into them yet.[/]")
        return
    for game in games:  # type: ignore[assignment]
        console.print(f"  [green]{game['name']}[/] [dim]{game['version'] or ''} {game['title_id'] or ''}[/]")


@app.command()
def probe(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    path: Path = typer.Argument(..., metavar="PATH", help="Game folder or package file to inspect."),
) -> None:
    """Say what a folder holds and which package is the game (the GUI pre-picks it)."""
    machine_readable = _enter(ctx, json_flag)
    scan = probe_path(path)
    if machine_readable:
        emit.emit(scan.to_dict())
        return
    console.print(f"[bold]{scan.path}[/]  [dim]{scan.kind}[/]")
    console.print(f"game file: [green]{scan.game_file or 'none found'}[/]")
    if scan.update is not None:
        console.print(f"latest update: [green]{scan.update}[/]")
    for package in scan.packages:
        selected = "[green]*[/]" if package.path == scan.game_file else " "
        if package.size > 1_000_000_000:
            size = f"{package.size / 1_000_000_000:.2f} GB"
        else:
            size = f"{package.size / 1_000_000:.0f} MB"
        console.print(f" {selected} {package.role:8} {package.path.name} [dim]{size}[/]")


@app.command()
def mods(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    refresh: bool = typer.Option(
        True, "--refresh/--no-refresh", help="rewrite each game's env so the mods in its folder apply."
    ),
) -> None:
    """List the mods in each game's folder (drop a mod into <TITLEID>/mods/ to install one)."""
    machine_readable = _enter(ctx, json_flag)
    cfg = load_config()
    libraries = [Path(entry) for entry in cfg.library_dirs]
    if not libraries:
        _fail(ctx, "no game library set; add one with `nx2mac library --add <folder>`")

    entries: list[dict[str, object]] = []
    for directory in libraries:
        entries.extend(library_mod.describe_mods(directory))
    if refresh:
        library_mod.write_envs_for(libraries, Path(cfg.cloud_dir) if cfg.cloud_dir else None)

    if machine_readable:
        emit.emit({"mods": entries})
        return
    if not entries:
        console.print("[dim]No mods found.[/]")
        console.print("[dim]Install one: put its folder in <game>/mods/<mod name>/romfs "
                      "(cheats: <game>/mods/cheats/<build id>.txt).[/]")
        return
    game = ""
    for entry in entries:
        if entry["game"] != game:
            game = str(entry["game"])
            console.print(f"[bold]{game}[/] [dim]{entry['title_id']}[/]")
        if not entry["usable"]:
            marker = "[yellow]no romfs[/]"
        else:
            marker = "[green]on[/]" if entry["active"] else "[dim]off[/]"
        console.print(f"  {marker} {entry['name']}")


def _reanchor(
    ctx: typer.Context,
    directory: Path,
    entry: library_mod.LibraryGame,
    sources: Sequence[Path],
) -> list[str]:
    """Re-anchor cheat files to the game's build with the engine's `cheat_anchor.py`.

    Importing is part of it: that is what rebases Switch-absolute addresses to main offsets. Both
    `cheats --compile` and `cheats --import` end here, so an imported file is anchored (and its
    entries become switchable) as soon as the build's extract exists.
    """
    main = library_mod.extracted_main(directory, entry.title_id)
    if main is None:
        _fail(ctx, f"no extracted game files for {entry.name}; build it once "
                   "(the engine keeps ExeFS/main under <game>/build/extract)")
    script = None
    engines_root = cache_root().expanduser() / "engines"
    pinned_ref = load_config().engine_ref or "main"
    preferred = sorted(engines_root.glob(f"*_{pinned_ref}/scripts/cheat_anchor.py"))
    for candidate in [*preferred, *sorted(engines_root.glob("*/scripts/cheat_anchor.py"))]:
        script = candidate
        if candidate.is_file():
            break
    if script is None:
        _fail(ctx, f"no engine checkout with scripts/cheat_anchor.py under {cache_root() / 'engines'}")
    venv = ensure_venv(cache_root().expanduser(), env=_get_env())
    folder = library_mod.cheats_dir(directory, entry.title_id)
    # The re-anchor source and its output both live in the game's mods folder:
    # <game>/mods/<build id>.txt is what DM_CHEATS reads, and the .toml beside it keeps the
    # addresses' meaning so a later build can be compiled again.
    toml = library_mod.mods_dir(directory, entry.title_id) / f"{entry.title_id}.cheats.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    report: list[str] = []
    for source in sources:
        library_mod.import_cheats(directory, entry.title_id, [source])
        imported = sorted(folder.glob("*.txt"))
        if imported:
            source = imported[0]
        try:
            report.extend(
                reanchor(
                    _get_env(),
                    venv.python,
                    script,
                    source.expanduser(),
                    main,
                    main,
                    toml,
                    folder,
                )
            )
        except CheatToolError as error:
            _fail(ctx, str(error))
    return report


def _print_reanchor(report: Sequence[str], directory: Path, entry: "library_mod.LibraryGame", machine_readable: bool) -> None:
    """Show the tool's lines (the CLI's own, not the engine's) unless we are in JSON mode."""
    if machine_readable:
        return
    folder = library_mod.cheats_dir(directory, entry.title_id)
    toml = library_mod.mods_dir(directory, entry.title_id) / f"{entry.title_id}.cheats.toml"
    for line in report:
        console.print(f"[yellow]cheats: {line}[/]" if "disabled" in line else f"[dim]{line}[/]")
    written = sorted(folder.glob("*.txt"))
    if written:
        console.print(f"[green]cheat file:[/] {written[0]}")
        console.print(f"[dim]source kept for later builds: {toml}[/]")


@app.command()
def cheats(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    game: str | None = typer.Option(None, "--game", metavar="GAME", help="the game (default: every game)."),
    enable: list[str] = typer.Option([], "--enable", metavar="NAME", help="switch a cheat on."),
    force: bool = typer.Option(False, "--force", help="enable even a code patch (it will crash this port)."),
    disable: list[str] = typer.Option([], "--disable", metavar="NAME", help="switch a cheat off."),
    library: Path | None = typer.Option(None, "--library", metavar="DIR", help="only this library."),
    import_from: list[Path] = typer.Option(
        [], "--import", metavar="FILE", help="copy a cheat file (and its .meta.tsv) into the game's cheats folder."
    ),
    compile_file: list[Path] = typer.Option(
        [],
        "--compile",
        metavar="FILE",
        help="re-anchor a cheat file to this build with the engine's cheat_anchor.py (needs a build's extract).",
    ),
    remove: bool = typer.Option(False, "--remove", help="delete the game's cheat file (nx2mac keeps one)."),
    off: bool = typer.Option(False, "--off", help="master switch: no cheat runs at all."),
    on: bool = typer.Option(False, "--on", help="clear the master switch again."),
    restore_safe: bool = typer.Option(
        False, "--restore-safe", help="drop the unclean-exit marker so the saved switches apply again."
    ),
) -> None:
    """List a game's cheat files and which switches are on (what the in-game Cheats menu writes)."""
    machine_readable = _enter(ctx, json_flag)
    cfg = load_config()
    libraries = [library_mod.require_library(library)] if library is not None else [
        Path(entry) for entry in cfg.library_dirs
    ]
    if not libraries:
        _fail(ctx, "no game library set; add one with `nx2mac library --add <folder>`")

    games = []
    for directory in libraries:
        if game:
            try:
                games.append((directory, library_mod.find_game(directory, game)))
            except library_mod.LibraryError:
                continue
        else:
            games.extend((directory, entry) for entry in library_mod.list_games(directory))
    if not games and game and library_mod._is_build_id(game.strip()):
        # Cheats belong to a title ID, not to a build: they work before the game is built too.
        title_id = game.strip().upper()
        entry = library_mod.GameEntry(name=title_id, path=libraries[0], app_path=None, title_id=title_id)
        games = [(libraries[0], entry)]
    if not games:
        _fail(ctx, f"no game called {game!r}" if game else "no games built yet")

    payload: list[dict[str, object]] = []
    for directory, entry in games:
        if not entry.title_id:
            continue
        if import_from:
            try:
                library_mod.import_cheats(directory, entry.title_id, import_from)
            except library_mod.LibraryError as error:
                _fail(ctx, str(error))
            # Anchor what was just imported, so its entries are checkable (and its switches keep
            # working) without waiting for the next build. No extract yet = first build does it.
            if library_mod.extracted_main(directory, entry.title_id) is not None:
                _print_reanchor(_reanchor(ctx, directory, entry, import_from), directory, entry, machine_readable)

        if remove:
            library_mod.clear_cheats(directory, entry.title_id)
        if compile_file:
            _print_reanchor(_reanchor(ctx, directory, entry, compile_file), directory, entry, machine_readable)
        if off:
            library_mod.set_cheats_master(directory, entry.title_id, True)
        if on:
            library_mod.set_cheats_master(directory, entry.title_id, False)
        if restore_safe:
            library_mod.clear_safe_mode(directory, entry.title_id)
        for name in library_mod.clear_blocked_switches(directory, entry.title_id):
            if not machine_readable:
                console.print(f"[yellow]switched off {name} — it cannot run on this port[/]")
        builds = library_mod.cheat_builds(directory, entry.title_id)
        if enable or disable:
            for build in builds:
                build_id = str(build["build"])
                for name in enable:
                    try:
                        library_mod.set_cheat(directory, entry.title_id, build_id, name, True, force=force)
                    except library_mod.LibraryError as error:  # e.g. an instruction patch: not a crash
                        _fail(ctx, str(error))
                for name in disable:
                    try:
                        library_mod.set_cheat(directory, entry.title_id, build_id, name, False)
                    except library_mod.LibraryError as error:
                        _fail(ctx, str(error))
            builds = library_mod.cheat_builds(directory, entry.title_id)
        payload.append(
            {
                "game": entry.name,
                "title_id": entry.title_id,
                "library": str(directory),
                "folder": str(library_mod.cheats_dir(directory, entry.title_id)),
                "state": library_mod.cheats_state(directory, entry.title_id),
                "builds": builds,
            }
        )

    if machine_readable:
        emit.emit({"games": payload})
        return
    if not any(entry["builds"] for entry in payload):
        console.print("[dim]No cheat files yet.[/]")
        console.print("[dim]Install some: <game>/mods/cheats/<build id>.txt "
                      "(cheat sites publish atmosphere/contents/<title id>/cheats/<build id>.txt).[/]")
        return
    for entry in payload:
        if not entry["builds"]:
            continue
        console.print(f"[bold]{entry['game']}[/] [dim]{entry['title_id']}[/]")
        state = entry["state"]  # type: ignore[assignment]
        if state["master_off"]:  # type: ignore[index]
            console.print("  [yellow]master switch off[/]: no cheat runs (nx2mac cheats --on)")
        if state["safe_mode"]:  # type: ignore[index]
            console.print("  [yellow]safe mode[/]: the last run ended uncleanly with cheats on "
                          "(nx2mac cheats --restore-safe)")
        for build in entry["builds"]:  # type: ignore[union-attr]
            console.print(f"  build [dim]{build['build']}[/]")
            for cheat in build["cheats"]:  # type: ignore[union-attr]
                if cheat["kind"] == "master":
                    marker = "[cyan]always[/]"
                elif cheat["kind"] == "label":
                    continue  # credits lines and section markers are not switches
                elif cheat["broken"]:
                    marker = "[red]broken[/]"
                elif not cheat["switchable"]:
                    marker = "[dim] one-pass[/]"
                else:
                    marker = "[green]on [/]" if cheat["enabled"] else "[dim]off[/]"
                note = ""
                if cheat.get("blocked"):
                    note = f"  [yellow]{cheat['blocked']} — cannot be enabled[/]"
                elif cheat.get("warning"):
                    note = f"  [dim]{cheat['warning']}[/]"
                elif cheat["enabled"]:
                    note = "  [green](on)[/]"
                console.print(f"    {marker} {cheat['name']}{note}")


@app.command()
def clean(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    game: str | None = typer.Option(None, "--game", metavar="GAME", help="only this game (default: every library)."),
    library: Path | None = typer.Option(None, "--library", metavar="DIR", help="only this library."),
    manifests: bool = typer.Option(
        False, "--manifests", help="also remove each game's <Game>.nx2mac.json (rebuilt next time)."
    ),
    cache: bool = typer.Option(
        False,
        "--cache",
        help="also drop nx2mac's own scratch caches (staging + managed venv). Never the engine checkouts.",
    ),
) -> None:
    """Delete what a finished build no longer needs: extracted files, shader caches, temp files.

    It only ever touches nx2mac's own data — `nx2mac_game_lib/<TITLEID>/build`, the `temp` folders, and
    (with `--manifests`) the sidecar. The app, the saves, the engine checkouts and your game dumps
    are never removed.
    """
    machine_readable = _enter(ctx, json_flag)
    cfg = load_config()
    libraries = [library_mod.require_library(library)] if library is not None else [
        Path(entry) for entry in cfg.library_dirs
    ]
    if not libraries and not cache:
        _fail(ctx, "no game library set; add one with `nx2mac library --add <folder>`")

    removed: list[dict[str, object]] = []

    def drop(target: Path, what: str, name: str) -> None:
        """Remove one path we own, reporting how much it freed."""
        if not target.exists():
            return
        size = sum(path.stat().st_size for path in target.rglob("*") if path.is_file()) if target.is_dir() else target.stat().st_size
        shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
        removed.append({"game": name, "path": str(target), "freed": size, "what": what})

    for directory in libraries:
        games = [library_mod.find_game(directory, game)] if game else library_mod.list_games(directory)
        for entry in games:
            if not entry.title_id:
                continue
            # only nx2mac's own folders, inside the game's folder in the library
            drop(library_mod.build_dir(directory, entry.title_id), "build", entry.name)
            drop(library_mod.support_dir(directory, entry.title_id) / "temp", "temp", entry.name)
            # The packing key the engine writes beside the app is regenerated by the next build, and
            # the record of what was built is only provenance: neither is needed to play the game.
            root = library_mod.app_root(directory)
            for name in (f"{entry.title_id}.app.key", f"{entry.name}.app.key"):
                drop(root / name, "packing key", entry.name)
            if entry.app_path is not None:
                drop(library_mod.pack_key_for(entry.app_path), "packing key", entry.name)
                if manifests:
                    drop(entry.app_path.with_suffix(".nx2mac.json"), "manifest", entry.name)

    if cache:
        root = cache_root()
        drop(root / "staging", "staging", "nx2mac")
        drop(root / "python", "venv", "nx2mac")

    if machine_readable:
        emit.emit({"removed": removed, "freed": sum(int(item["freed"]) for item in removed)})
        return
    if not removed:
        console.print("[dim]Nothing to clean.[/]")
        return
    for item in removed:
        console.print(f"[green]Removed[/] {item['path']} [dim]({int(item['freed']) / 2**20:.0f} MB)[/]")
    console.print(f"[bold]Freed {sum(int(item['freed']) for item in removed) / 2**30:.2f} GB.[/] "
                  "The app, its saves, the engine checkout and your dumps are untouched.")


@app.command()
def cloud(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    set_dir: Path | None = typer.Option(None, "--set", metavar="DIR", help="a synced folder (iCloud Drive, Dropbox, …)."),
    clear: bool = typer.Option(False, "--clear", help="stop syncing saves to a cloud folder."),
) -> None:
    """Keep each game's saves local and copy them to a synced folder (engine cloud saves)."""
    machine_readable = _enter(ctx, json_flag)
    cfg = load_config()

    if clear:
        save_config(replace(cfg, cloud_dir=None))
        library_mod.write_envs_for([Path(entry) for entry in cfg.library_dirs])
        if machine_readable:
            emit.emit({"dir": None, "env_files": []})
        else:
            console.print("[green]Cloud saves off.[/] The games keep playing from their local save.")
        return

    if set_dir is not None:
        folder = set_dir.expanduser()
        if not folder.is_dir():
            _fail(ctx, f"cloud folder does not exist: {folder} (create it first, e.g. in iCloud Drive)")
        save_config(replace(cfg, cloud_dir=str(folder)))
        written = library_mod.write_envs_for([Path(entry) for entry in cfg.library_dirs], folder)
        if machine_readable:
            emit.emit({"dir": str(folder), "env_files": [str(path) for path in written]})
        else:
            console.print(f"[green]Cloud saves:[/] {folder}")
            console.print("[dim]Each game copies its save to <folder>/<title id>/save.dmsync (per player: "
                          "<folder>/players/<player>/<title id>/).[/]")
        return

    if machine_readable:
        emit.emit({"dir": cfg.cloud_dir})
    else:
        console.print(f"cloud saves: [green]{cfg.cloud_dir}[/]" if cfg.cloud_dir else "[yellow]off[/]")


@app.command()
def player(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    create: str | None = typer.Option(None, "--create", metavar="NAME", help="add a player and make them active."),
    select: str | None = typer.Option(None, "--select", metavar="NAME", help="make an existing player active."),
    delete: str | None = typer.Option(None, "--delete", metavar="NAME", help="forget a player (their saves stay)."),
) -> None:
    """List, create or choose this game's players (each gets their own saves and name)."""
    machine_readable = _enter(ctx, json_flag)
    cfg = load_config()
    libraries = [Path(entry) for entry in cfg.library_dirs]
    if not libraries:
        _fail(ctx, "no game library set; add one with `nx2mac library --add <folder>`")
    library_dir = library_mod.require_library(libraries[0])

    try:
        if create is not None:
            library_mod.create_player(library_dir, create)
        elif select is not None:
            library_mod.select_player(library_dir, select)
        elif delete is not None:
            library_mod.delete_player(library_dir, delete)
        players, active = library_mod.load_players(library_dir)
        # every built game reads the active player from its own env file
        env_files = library_mod.write_envs_for(libraries, Path(cfg.cloud_dir) if cfg.cloud_dir else None)
    except library_mod.LibraryError as error:
        _fail(ctx, str(error))

    payload = {
        "library": str(library_dir),
        "players": [entry.to_dict() for entry in players],
        "active": active,
        "env_files": [str(path) for path in env_files],
    }
    if machine_readable:
        emit.emit(payload)
        return
    if not players:
        console.print("[yellow]No players yet.[/] Add one: nx2mac player --create \"Your name\"")
        return
    console.print(f"[bold]{library_dir}[/] players:")
    for entry in players:
        marker = "[green]*[/]" if entry.id == active else " "
        console.print(f" {marker} {entry.name}")


@app.command()
def build(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    game_dir: Path = typer.Argument(
        ...,
        metavar="GAME",
        help="Game folder (NSP/NSZ/XCI + updates), a single package file, or a .nup container.",
    ),
    keys: Path | None = typer.Option(
        None, "--keys", "-k", help="path to prod.keys (default: auto-detect in/next to the game)."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="where to write the .app (default: engine default)."
    ),
    out_dir: Path | None = typer.Option(
        None,
        "--out-dir",
        metavar="DIR",
        help="keep the .app, the engine build folder and prod.keys in this one directory.",
    ),
    library: Path | None = typer.Option(
        None,
        "--library",
        metavar="DIR",
        help="file the game into this library folder (default: the one set with `nx2mac library --set`).",
    ),
    update: list[Path] = typer.Option(
        [],
        "--update",
        "-u",
        help="update package (.nsp/.nsz) to include; repeatable. Staged with the game.",
    ),
    dlc: list[Path] = typer.Option(
        [],
        "--dlc",
        help="DLC package (.nsp/.nsz) or a folder of them to include; repeatable. Staged with the game.",
    ),
    source_id: str | None = typer.Option(None, "--source", help="engine id override (upstream|fork)."),
    ref: str | None = typer.Option(None, "--ref", help="pin a branch/tag/commit for this build."),
    allow_damaged: bool = typer.Option(False, "--allow-damaged", help="pack damaged game files as they are."),
    allow_other_metal: bool = typer.Option(
        False, "--allow-other-metal", help="build shaders with this Mac's Metal compiler."
    ),
    allow_shader_regressions: bool = typer.Option(
        False, "--allow-shader-regressions", help="pack without the shaders that stopped translating."
    ),
) -> None:
    """Build a game into a native macOS .app with the pinned nxnative engine."""
    machine_readable = _enter(ctx, json_flag)
    source, selected_ref = _resolve_selection(ctx, source_id, ref)
    resolved = _checkout(ctx, source, selected_ref)

    # A configured library takes over as the output folder (unless -o/--out-dir say otherwise).
    library_dir = library
    if library_dir is None and out_dir is None:
        configured = primary_library(load_config())
        library_dir = Path(configured) if configured else None
    if library_dir is not None:
        try:
            library_dir = library_mod.require_library(library_dir)
        except library_mod.LibraryError as error:
            _fail(ctx, str(error))

    if machine_readable:
        emit.event("engine", engine=resolved.to_dict())
    else:
        console.print(f"[dim]Using engine:[/] {describe(resolved)}")
        if update or dlc:
            console.print(f"[dim]Extras:[/] {len(update)} update(s), {len(dlc)} DLC package(s) staged with the game")
        if out_dir is not None:
            console.print(f"[dim]Output folder:[/] {out_dir} (app + build folder + prod.keys)")
        if library_dir is not None:
            console.print(f"[dim]Game library:[/] {library_dir}")

    allowed: list[str] = []
    if allow_damaged:
        allowed.append("--allow-damaged")
    if allow_other_metal:
        allowed.append("--allow-other-metal")
    if allow_shader_regressions:
        allowed.append("--allow-shader-regressions")

    translator = Translator(
        on_log=emit.log_line if machine_readable else console.print,
        on_event=(lambda kind, fields: emit.event(kind, **fields)) if machine_readable else None,
    )
    stream = translator.feed

    def attempt(extra: list[str]):
        return run_build(
            _get_env(),
            cache_root().expanduser(),
            resolved,
            game_dir,
            prod_keys=keys,
            output=output,
            on_line=stream,
            updates=update,
            dlcs=dlc,
            out_dir=out_dir,
            library_dir=library_dir,
            extra_args=extra,
        )

    try:
        result = attempt(allowed)
    except (BuildError, PyEnvError) as error:
        _fail(ctx, str(error))

    # The engine's stops that have a flag: offer to build anyway, like NX2Apple's card does.
    if result.returncode != 0 and result.failure is not None and result.failure.override is not None:
        flag = result.failure.override
        if machine_readable:
            emit.event("result", **result.to_dict())
            raise typer.Exit(code=result.returncode)
        if flag in allowed:
            console.print(f"[yellow]Still stopped with {flag}.[/]")
        elif _interactive() and typer.confirm(
            f"The build stopped: {result.failure.note}. Build anyway ({flag})?", default=False
        ):
            result = attempt([*allowed, flag])
        else:
            console.print(f"[yellow]Build stopped:[/] {result.failure.message}")
            console.print(f"Build anyway with [bold]{flag}[/] if you accept it.")

    if machine_readable:
        emit.event("result", **result.to_dict())
    elif result.returncode != 0:
        console.print(f"[red]Engine build failed with exit code {result.returncode}.[/]")
    elif result.app_path is not None:
        console.print(f"[green]Done.[/] App at [bold green]{result.app_path}[/]")
        if result.work_dir is not None:
            console.print(f"[dim]Build folder (kept for rebuilds):[/] {result.work_dir}")
        if result.manifest is not None:
            console.print(f"[dim]Library entry:[/] {result.manifest.parent} (saves, logs, mods, cheats)")
    else:
        console.print("[green]Done.[/] App location: see engine output (default is <game>.app in the game folder)")

    raise typer.Exit(code=result.returncode)


if __name__ == "__main__":
    app()

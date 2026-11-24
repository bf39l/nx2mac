"""nx2mac command-line interface (typer + rich).

Every command has a machine-readable form (``--json``) because the planned Swift
GUI drives this CLI as a subprocess: single-document commands print one JSON
object, ``build`` prints NDJSON events (engine info, engine log lines, result).
Human output is just a different renderer of the same core results, so the
check/build logic never depends on typer.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import VERSION, emit
from .builder import BuildError, run_build
from .config import Config, cache_root
from .config import load as load_config
from .config import save as save_config
from .doctor import DoctorContext, DoctorReport, run_checks
from .engines import EngineError, EngineSource, all_sources, describe, ensure_checkout, source_by_id
from .installer import InstallResult, install
from .pyenv import PyEnvError
from .remedies import Remedy, installable_names, remedy_for
from .runner import Env, RealEnv

app = typer.Typer(
    name="nx2mac",
    help="Build Nintendo Switch / Switch 2 games into native macOS .app bundles.",
    no_args_is_help=True,
)
console = Console()


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
    selected_id: str | None = source_id or cfg.engine_id
    selected_ref: str | None = ref if ref is not None else cfg.engine_ref

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
    save_config(Config(engine_id=source.id, engine_ref=resolved.ref))

    if _state(ctx).json:
        emit.emit({"engine": resolved.to_dict(), "saved": {"engine_id": source.id, "engine_ref": resolved.ref}})
        return
    console.print(f"[bold green]Engine pinned:[/] {describe(resolved)}")
    console.print(f"checkout: [dim]{resolved.path}[/]")


@app.command()
def build(
    ctx: typer.Context,
    json_flag: bool = _json_option(),
    game_dir: Path = typer.Argument(..., metavar="GAME", help="Game folder (NSP/NSZ/XCI + updates) or a .nup file."),
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
) -> None:
    """Build a game into a native macOS .app with the pinned nxnative engine."""
    machine_readable = _enter(ctx, json_flag)
    source, selected_ref = _resolve_selection(ctx, source_id, ref)
    resolved = _checkout(ctx, source, selected_ref)

    if machine_readable:
        emit.event("engine", engine=resolved.to_dict())
    else:
        console.print(f"[dim]Using engine:[/] {describe(resolved)}")
        if update or dlc:
            console.print(f"[dim]Extras:[/] {len(update)} update(s), {len(dlc)} DLC package(s) staged with the game")
        if out_dir is not None:
            console.print(f"[dim]Output folder:[/] {out_dir} (app + build folder + prod.keys)")

    try:
        result = run_build(
            _get_env(),
            cache_root().expanduser(),
            resolved,
            game_dir,
            prod_keys=keys,
            output=output,
            on_line=emit.log_line if machine_readable else None,
            updates=update,
            dlcs=dlc,
            out_dir=out_dir,
        )
    except (BuildError, PyEnvError) as error:
        _fail(ctx, str(error))

    if machine_readable:
        emit.event("result", **result.to_dict())
    elif result.returncode != 0:
        console.print(f"[red]Engine build failed with exit code {result.returncode}.[/]")
    elif result.app_path is not None:
        console.print(f"[green]Done.[/] App at [bold green]{result.app_path}[/]")
        if result.work_dir is not None:
            console.print(f"[dim]Build folder (kept for rebuilds):[/] {result.work_dir}")
    else:
        console.print("[green]Done.[/] App location: see engine output (default is <game>.app in the game folder)")

    raise typer.Exit(code=result.returncode)


if __name__ == "__main__":
    app()

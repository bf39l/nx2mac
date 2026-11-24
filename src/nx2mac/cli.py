"""nx2mac command-line interface (typer + rich)."""

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import VERSION
from .builder import BuildError, run_build
from .config import Config, cache_root
from .config import load as load_config
from .config import save as save_config
from .doctor import DoctorContext, DoctorReport, run_checks
from .engines import EngineError, EngineSource, all_sources, describe, ensure_checkout, source_by_id
from .pyenv import PyEnvError, ensure_venv
from .runner import Env, RealEnv

app = typer.Typer(
    name="nx2mac",
    help="Build Nintendo Switch / Switch 2 games into native macOS .app bundles.",
    no_args_is_help=True,
)
console = Console()


def _get_env() -> Env:
    """Seam for tests: production uses RealEnv; unit tests monkeypatch this."""
    return RealEnv()


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


def _resolve_selection(source_id: str | None, ref: str | None) -> tuple[EngineSource, str | None]:
    """Resolve (source id, ref): explicit flags > saved config > menu/default.

    Exits the CLI with an error when the resolved id is unknown.
    """
    cfg = load_config()
    selected_id: str | None = source_id or cfg.engine_id
    selected_ref: str | None = ref if ref is not None else cfg.engine_ref

    if selected_id is None:
        sources = all_sources()
        if sys.stdin.isatty():
            return _prompt_source(sources), selected_ref
        console.print(
            f"[dim]No engine source saved; defaulting to {sources[0].label}. "
            "Persist a choice with `nx2mac engine --source <id>`.[/]"
        )
        selected_id = sources[0].id

    source = source_by_id(selected_id)
    if source is None:
        valid = ", ".join(s.id for s in all_sources())
        console.print(f"[red]Unknown engine source {selected_id!r}. Valid ids: {valid}[/]")
        raise typer.Exit(code=1)
    return source, selected_ref


@app.command()
def version() -> None:
    """Print the nx2mac version."""
    console.print(VERSION)


@app.command()
def doctor() -> None:
    """Check every prerequisite for building macOS apps; exit non-zero if any fail."""
    report = run_checks(DoctorContext(env=_get_env()))
    _print_report(report)
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def setup() -> None:
    """Prepare what can be automated (managed Python env), then show remaining work."""
    env = _get_env()
    cache = cache_root().expanduser()

    console.print("[bold]Preparing the managed Python environment...[/]")
    python_problem: str | None = None
    try:
        venv_status = ensure_venv(cache, env=env)
        console.print(f"[green]Python env ready:[/] [dim]{venv_status.python}[/]")
    except PyEnvError as error:
        python_problem = str(error)

    if python_problem is not None:
        console.print(f"[yellow]{python_problem}[/]")

    console.print("[bold]Current prerequisite status:[/]")
    report = run_checks(DoctorContext(env=env))
    _print_report(report)

    remaining = [check for check in report.failures()]
    if not remaining:
        console.print("\n[green]All prerequisites met - you can build.[/]")
        return
    console.print("\n[bold red]Still to do manually:[/]")
    for command in report.fix_commands():
        console.print(f"  [yellow]$ {command}[/]")


@app.command()
def engine(
    source_id: str | None = typer.Option(None, "--source", help="Engine id to select (upstream|fork)."),
    ref: str | None = typer.Argument(None, help="Branch, tag or commit to pin (default: the source's default branch)."),
) -> None:
    """Select and pin an nxnative engine source from GitHub, then show exactly what it resolved to."""
    source, selected_ref = _resolve_selection(source_id, ref)
    env = _get_env()
    cache = cache_root().expanduser()

    try:
        resolved = ensure_checkout(env, cache, source, selected_ref)
    except EngineError as error:
        console.print(f"[red]{error}[/]")
        raise typer.Exit(code=1) from error

    save_config(Config(engine_id=source.id, engine_ref=resolved.ref))
    console.print(f"[bold green]Engine pinned:[/] {describe(resolved)}")
    console.print(f"checkout: [dim]{resolved.path}[/]")


@app.command()
def build(
    game_dir: Path = typer.Argument(..., metavar="GAME", help="Game folder (NSP/NSZ/XCI + updates) or a .nup file."),
    keys: Path | None = typer.Option(None, "--keys", "-k", help="path to prod.keys (default: auto-detect in/next to the game)."),
    output: Path | None = typer.Option(None, "--output", "-o", help="where to write the .app (default: engine default)."),
    source_id: str | None = typer.Option(None, "--source", help="engine id override (upstream|fork)."),
    ref: str | None = typer.Option(None, "--ref", help="pin a branch/tag/commit for this build."),
) -> None:
    """Build a game into a native macOS .app with the pinned nxnative engine."""
    source, selected_ref = _resolve_selection(source_id, ref)
    env = _get_env()
    cache = cache_root().expanduser()

    try:
        resolved = ensure_checkout(env, cache, source, selected_ref)
    except EngineError as error:
        console.print(f"[red]{error}[/]")
        raise typer.Exit(code=1) from error
    console.print(f"[dim]Using engine:[/] {describe(resolved)}")

    try:
        result = run_build(env, cache, resolved, game_dir, prod_keys=keys, output=output)
    except (BuildError, PyEnvError) as error:
        console.print(f"[red]{error}[/]")
        raise typer.Exit(code=1) from error

    if result.returncode != 0:
        console.print(f"[red]Engine build failed with exit code {result.returncode}.[/]")
        raise typer.Exit(code=result.returncode)

    if result.app_path is not None:
        console.print(f"[green]Done.[/] App at [bold green]{result.app_path}[/]")
    else:
        console.print("[green]Done.[/] App location: see engine output (default is <game>.app in the game folder)")


if __name__ == "__main__":
    app()

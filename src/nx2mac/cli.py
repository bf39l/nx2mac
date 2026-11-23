"""nx2mac CLI - build Nintendo Switch games into native macOS apps.

v1 stub: the real implementation follows AGENT.md. Engine sources are fetched
from GitHub (upstream seedds/nxnative or the bf39l fork) at build time; local
checkouts under ~/switch are personal reference copies only and are never used
as build inputs.
"""

import shutil
import sys

import typer
from rich.console import Console
from rich.table import Table

from . import VERSION

app = typer.Typer(
    name="nx2mac",
    help="Build Nintendo Switch games into native macOS apps.",
    no_args_is_help=True,
)

console = Console()

# Engine sources: the CLI resolves and clones one of these from GitHub.
ENGINE_SOURCES = [
    {"id": "upstream", "url": "https://github.com/seedds/nxnative.git", "ref": "main"},
    {"id": "fork", "url": "https://github.com/bf39l/nxnative.git", "ref": "HEAD (default branch)"},
]


@app.command()
def version():
    """Display the CLI version."""
    console.print(f"nx2mac v{VERSION}")


@app.command()
def engines():
    """Show available engine repositories (fetched from GitHub at build time)."""
    table = Table(title="Engine Sources")
    table.add_column("ID")
    table.add_column("Repository")
    table.add_column("Ref")

    for source in ENGINE_SOURCES:
        table.add_row(source["id"], source["url"].removesuffix(".git"), source["ref"])

    console.print(table)


@app.command()
def doctor():
    """Check required tools (v1 stub - see AGENT.md 'Dependency checklist')."""
    if sys.platform != "darwin":
        console.print("[red]nx2mac needs macOS on Apple silicon.[/red]")
        raise typer.Exit(1)

    rows = []
    for command in ["git", "brew", "xcodebuild", "zstd", "glslangValidator"]:
        path = shutil.which(command)
        rows.append((command, f"Found: {path}" if path else "[red]Missing[/red]"))
    # Xcode vs Command Line Tools (CLT has no Metal toolchain / iOS SDK)
    xcode = shutil.which("xcode-select")
    if xcode:
        import subprocess

        dev = subprocess.run([xcode, "-p"], check=False, capture_output=True, text=True).stdout.strip()
        ok = "CommandLineTools" not in dev and bool(dev)
        rows.append(("xcode developer dir", f"[green]{dev}[/green]" if ok else "[red]Command Line Tools or unset: install full Xcode[/red]"))

    table = Table(title="Tool check")
    table.add_column("Tool")
    table.add_column("Status")
    for name, status in rows:
        table.add_row(name, status)
    console.print(table)


if __name__ == "__main__":
    app()

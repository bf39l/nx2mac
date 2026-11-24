"""Re-anchoring a cheat file to this build, with the engine's own tool.

A cheat site's file is written for one build: it patches absolute addresses of that build's code
(Switch `main` at `0x08000000`). The Mac app maps `main` elsewhere, so those writes land in unmapped
memory and the cheats do nothing. `scripts/cheat_anchor.py` (in the engine) resolves each address in
the *target* build's code and rewrites the file with addresses that fit it.
"""

from dataclasses import dataclass
from pathlib import Path

from .runner import Env


class CheatToolError(Exception):
    """Raised when the file cannot be re-anchored (no engine, no extracted main, tool failed)."""


@dataclass(frozen=True)
class AnchorPlan:
    """The two commands a re-anchor runs: import the file, then compile it for the build."""

    import_argv: tuple[str, ...]
    compile_argv: tuple[str, ...]


def plan_anchor(
    python: Path,
    script: Path,
    cheats_file: Path,
    source_main: Path,
    target_main: Path,
    source_toml: Path,
    out_dir: Path,
    replace: bool = True,
) -> AnchorPlan:
    """Build the argv for `cheat_anchor.py import` and `compile`."""
    import_argv = (
        str(python),
        str(script),
        "import",
        str(cheats_file),
        "--nso",
        str(source_main),
        "-o",
        str(source_toml),
        "--force",
        "--assume-build",
    )
    compile_argv: tuple[str, ...] = (
        str(python),
        str(script),
        "compile",
        str(source_toml),
        "--nso",
        str(target_main),
        "-o",
        str(out_dir),
    )
    if replace:
        compile_argv += ("--replace",)
    return AnchorPlan(import_argv=import_argv, compile_argv=compile_argv)


def anchor_build_cheats(
    env: Env,
    python: Path,
    script: Path,
    cheat_file: Path,
    main: Path,
    source_toml: Path,
    cheats_dir: Path,
    on_line=None,
) -> list[str]:
    """Re-anchor a game's cheat file to the build that just ran.

    The build extracted the game's `ExeFS/main`, so every address in the file can be resolved in
    the code this app actually runs — the Switch-absolute addresses a cheat site ships are rewritten
    into ones that fit. The result replaces `<build id>.txt` in the game's cheats folder.
    """
    lines = reanchor(env, python, script, cheat_file, main, main, source_toml, cheats_dir, on_line=on_line)
    if on_line is not None:
        for line in lines:
            on_line(line)
    return lines


def reanchor(
    env: Env,
    python: Path,
    script: Path,
    cheats_file: Path,
    source_main: Path,
    target_main: Path,
    source_toml: Path,
    out_dir: Path,
    replace: bool = True,
    on_line=None,
) -> list[str]:
    """Run the tool; returns its output lines (warnings included). Raises CheatToolError on failure."""
    plan = plan_anchor(python, script, cheats_file, source_main, target_main, source_toml, out_dir, replace)
    lines: list[str] = []
    # The tool keeps the file it replaced as `<build id>.txt.orig` and refuses a second run while
    # that exists; our re-anchor source (.cheats.toml) is the durable backup, so drop the stale copy.
    for stale in out_dir.glob("*.txt.orig"):
        stale.unlink(missing_ok=True)
        if on_line is not None:
            on_line(f"removed the previous backup {stale.name}")
    for step, argv in (("import", plan.import_argv), ("compile", plan.compile_argv)):
        result = env.run(list(argv), cwd=script.parent.parent)
        output = (result.stdout + result.stderr).strip()
        if output:
            lines.extend(output.splitlines())
        if result.returncode not in (0, 2):  # 2 = some cheats kept bound to their build (a warning)
            raise CheatToolError(f"cheat_anchor.py {step} failed ({result.returncode}): {output[:400]}")
    return lines

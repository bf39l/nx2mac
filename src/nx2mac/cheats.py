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
    lines.extend(neutralise(out_dir))
    return lines


def neutralise(cheats_dir: Path) -> list[str]:
    """Remove the code of entries the compile could not anchor (they cannot run on this port).

    A master (`{Name}`) runs on every launch, so leaving an unanchored one in place is a crash;
    its header stays, which the game's Cheats menu shows as a label.
    """
    from .library import neutralise_unanchored

    reports: list[str] = []
    for txt in sorted(cheats_dir.glob("*.txt")):
        meta_file = txt.with_suffix(".meta.tsv")
        meta = meta_file.read_text(encoding="utf-8", errors="replace") if meta_file.is_file() else None
        text, dropped = neutralise_unanchored(txt.read_text(encoding="utf-8", errors="replace"), meta)
        if dropped:
            txt.write_text(text, encoding="utf-8")
            reports.append("disabled (could not be anchored): " + ", ".join(dropped))
    reports.extend(_disable_unrunnable_switches(cheats_dir))
    reports.extend(_code_patch_warning(cheats_dir))
    return reports


def _disable_unrunnable_switches(cheats_dir: Path) -> list[str]:
    """Turn off the switches the compile proved cannot apply, i.e. entries with `no anchors`.

    Only that: an unanchored address belongs to another build, so the write lands on unrelated code.
    A `kind=patch` entry that anchored (`exact`) is left alone — Dead Cells' `無限血量` is one and
    works on the port. An earlier version switched every patch off here, which silently dropped a
    working toggle after the next build; that is the regression this note exists to prevent.
    """
    reports: list[str] = []
    for meta_file in sorted(cheats_dir.glob("*.meta.tsv")):
        enabled_file = meta_file.with_suffix("").with_suffix(".enabled")
        if not enabled_file.is_file():
            continue
        build = meta_file.with_suffix("").name
        rows = [line.split("\t") for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if line and not line.startswith("#")]
        if len(rows) < 2:
            continue
        head, body = rows[0], rows[1:]
        try:
            name_at, kind_at, resolved_at = head.index("name"), head.index("kind"), head.index("resolved")
        except ValueError:
            continue
        unusable = {
            row[name_at]
            for row in body
            if len(row) > max(name_at, kind_at, resolved_at) and row[resolved_at] == "no anchors"
        }
        on = [line.strip() for line in enabled_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        keep = [key for key in on if key.split(" #")[0] not in unusable]
        if len(keep) != len(on):
            enabled_file.write_text("".join(f"{key}\n" for key in keep), encoding="utf-8")
            reports.append(
                f"switched off for {build} (cannot run on this port): "
                + ", ".join(key for key in on if key not in keep)
            )
    return reports


def _code_patch_warning(cheats_dir: Path) -> list[str]:
    """Say it once when every switchable cheat of a file patches instructions.

    The Mac port runs code its rewriter produced, so a cheat that puts the Switch's instructions back
    cannot work — it either does nothing or takes the game down. Data cheats (values) are the ones
    that survive a port.
    """
    for txt in sorted(cheats_dir.glob("*.txt")):
        meta_file = txt.with_suffix(".meta.tsv")
        if not meta_file.is_file():
            continue
        rows = [line.split("\t") for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if line and not line.startswith("#")]
        if len(rows) < 2:
            continue
        head, body = rows[0], rows[1:]
        try:
            name_at, kind_at, resolved_at = head.index("name"), head.index("kind"), head.index("resolved")
        except ValueError:
            continue
        switchable = [row for row in body
                      if len(row) > max(name_at, kind_at, resolved_at)
                      and row[resolved_at] != "no anchors"
                      and row[kind_at] == "patch"
                      and not row[name_at].startswith("--Section")]
        if switchable and len(switchable) == len(body):
            return [
                (
                    f"{txt.name}: every cheat here patches code, and this port's rewriter owns that "
                    "code — expect a crash; only data cheats (values) survive a port"
                )
            ]
    return []

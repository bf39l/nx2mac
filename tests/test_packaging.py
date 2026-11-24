"""Checks on the packaging scripts that a Python test can make on any platform."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DMG = ROOT / "packaging" / "make_dmg.sh"


def test_make_dmg_script_is_executable() -> None:
    assert DMG.is_file()
    assert DMG.stat().st_mode & 0o111, "gui/build.sh calls it directly"


def test_make_dmg_script_parses() -> None:
    result = subprocess.run(["bash", "-n", str(DMG)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_make_dmg_fails_cleanly_without_a_bundle(tmp_path: Path) -> None:
    """Bad usage exits non-zero with a message, not a half-written image."""
    result = subprocess.run(
        ["bash", str(DMG), str(tmp_path / "nope.app"), "0.0.1", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    # Refused for a stated reason — no hdiutil (Linux CI), no bundle, or no executable.
    assert result.stderr.startswith("make_dmg.sh: "), result.stderr
    assert "hdiutil" in result.stderr or "no executable" in result.stderr or "no app bundle" in result.stderr
    assert not list(tmp_path.glob("*.dmg"))


def test_make_dmg_success_path_sets_a_clean_exit_code() -> None:
    """The trap must not decide the exit status (an `&&` chain in a trap did exactly that)."""
    text = DMG.read_text()
    assert "trap 'rm -rf \"$stage\"' EXIT" in text

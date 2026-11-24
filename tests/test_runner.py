"""Tests for nx2mac.runner (thin, real subprocesses via sys.executable)."""

import sys

from nx2mac.runner import RealEnv


def test_which_finds_this_interpreter() -> None:
    assert RealEnv().which(sys.executable) is not None
    assert RealEnv().which("definitely-not-a-real-binary-nx2mac") is None


def test_run_captures_stdout_and_returncode() -> None:
    result = RealEnv().run([sys.executable, "-c", "print('hi'); raise SystemExit(3)"])

    assert result.stdout.strip() == "hi" and result.returncode == 3


def test_run_stream_inherits_stdio() -> None:
    assert RealEnv().run_stream([sys.executable, "-c", "raise SystemExit(0)"]) == 0


def test_run_stream_forwards_lines_and_returncode() -> None:
    seen: list[str] = []
    code = RealEnv().run_stream(
        [sys.executable, "-c", "print('one'); print('two'); raise SystemExit(2)"],
        on_line=seen.append,
    )

    assert seen == ["one", "two"] and code == 2

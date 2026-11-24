"""Machine-readable output for `--json`: one JSON document, or NDJSON events.

The CLI must stay a stable contract for the future Swift GUI: it shells out to
`nx2mac` and parses stdout. So every command has a JSON form and long-running
builds emit newline-delimited JSON events (one object per line) that a consumer
can handle as they arrive.
"""

import json
import sys
from typing import Any

#: Event names emitted by `build --json`.
EVENT_LOG: str = "log"
EVENT_ENGINE: str = "engine"
EVENT_RESULT: str = "result"


def emit(payload: dict[str, Any]) -> None:
    """Write one JSON document to stdout (single-document commands).

    Flushed per line: a GUI reads the build's NDJSON event stream while the build runs, and a
    redirected stdout is block-buffered otherwise (the frozen CLI never reaches a terminal).
    """
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def event(kind: str, **fields: Any) -> None:
    """Write one NDJSON event line to stdout (`build --json`)."""
    emit({"event": kind, **fields})


def log_line(line: str) -> None:
    """Forward one line of engine output as a log event."""
    event(EVENT_LOG, line=line)


def diagnostic(message: str) -> None:
    """Machine-readable error payload, so failures are parseable too."""
    event("error", message=message)


def stdout_is_tty() -> bool:
    """Whether stdout is a terminal (affects tiny formatting choices)."""
    return sys.stdout.isatty()

"""Translate the engine's `--progress json` stream into our own events and log lines.

`build_ipa_from_nsp.py --progress json` prints one JSON object per line (`stage`, `step`,
`progress`, `stage_end`, `say`, …) instead of human text. Feeding that through here gives the GUI
live counters — how many shaders are compiled out of how many — and gives the terminal the same
information as readable lines, without the engine's bar redraws.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

#: At most one progress line per stage in the human log, in seconds.
LOG_INTERVAL: float = 2.0


@dataclass(frozen=True)
class Stage:
    """The stage the engine is in (`k/n` and its name, plus the step inside it)."""

    index: int
    total: int
    name: str

    def label(self) -> str:
        return f"{self.index}/{self.total} {self.name}".strip()


class Translator:
    """Turn engine progress JSON into `(log line, event)` callbacks.

    `on_log` receives human text (one line, no ANSI, no redraw); `on_event` receives
    `(kind, fields)` for the machine-readable stream. Both are optional.
    """

    def __init__(
        self,
        on_log: Callable[[str], None] | None = None,
        on_event: Callable[[str, dict[str, object]], None] | None = None,
        *,
        interval: float = LOG_INTERVAL,
    ) -> None:
        self._on_log = on_log
        self._on_event = on_event
        self._interval = interval
        self._stage: Stage | None = None
        self._last_logged = 0.0

    # ---- what the callers ask for ----

    @property
    def stage(self) -> Stage | None:
        return self._stage

    def feed(self, line: str) -> None:
        """Consume one line of the engine's output (JSON events, or plain text as a fallback)."""
        text = line.strip()
        if not text:
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            self._log(text)  # something the engine printed outside the protocol
            return
        if not isinstance(payload, dict) or "ev" not in payload:
            self._log(text)
            return
        kind = str(payload.get("ev"))
        if kind == "stage":
            self._start_stage(payload)
        elif kind == "step":
            self._step(payload)
        elif kind == "progress":
            self._progress(payload, force=False)
        elif kind == "stage_end":
            self._end_stage(payload)
        elif kind == "say":
            message = str(payload.get("msg") or "")
            for part in message.splitlines() or [""]:
                self._log(part)
        elif kind in ("shaders_stale", "game"):
            self._log(" ".join(f"{key}={value}" for key, value in payload.items() if key not in ("ev", "t")))
        self._emit(kind, payload)

    # ---- stage bookkeeping ----

    def _start_stage(self, payload: dict[str, object]) -> None:
        self._stage = Stage(
            index=int(payload.get("k") or 0),
            total=int(payload.get("n") or 0),
            name=str(payload.get("name") or ""),
        )
        step = str(payload.get("step") or "")
        self._log(f"[{self._stage.label()}]" + (f" · {step}" if step else "") + " …")
        self._last_logged = 0.0

    def _step(self, payload: dict[str, object]) -> None:
        stage = self._stage
        step = str(payload.get("step") or "")
        if stage is None:
            self._log(step)
            return
        self._log(f"[{stage.label()}]" + (f" · {step}" if step else "") + " …")
        self._last_logged = time.monotonic()

    def _end_stage(self, payload: dict[str, object]) -> None:
        stage = self._stage
        summary = str(payload.get("summary") or "")
        elapsed = float(payload.get("elapsed") or 0.0)
        mark = "⚠" if payload.get("warn") else ("✓" if payload.get("ok", True) else "✗")
        label = stage.label() if stage is not None else str(payload.get("name") or "")
        self._log(f"{mark} [{label}] ({int(elapsed) // 60}:{int(elapsed) % 60:02d})" + (f": {summary}" if summary else ""))
        self._stage = None

    def _progress(self, payload: dict[str, object], *, force: bool) -> None:
        """One counter update: the log line is throttled, the event is not (the GUI wants every change)."""
        done, total = payload.get("done"), payload.get("total")
        if done is None:
            return
        unit = str(payload.get("unit") or "")
        step = str(payload.get("step") or "")
        stage = self._stage
        name = stage.name if stage is not None else str(payload.get("name") or "")
        index = stage.index if stage is not None else int(payload.get("k") or 0)
        total_stages = stage.total if stage is not None else 0
        note = str(payload.get("note") or "")

        counted = f"{int(float(done)):,}" + (f"/{int(float(total)):,}" if total else "") + (f" {unit}" if unit else "")
        prefix = f"[{index}/{total_stages} {name}]".replace("  ", " ") if index else f"[{name}]"
        text = f"{prefix} {step + ': ' if step else ''}{counted}" + (f" ({note})" if note else "")

        now = time.monotonic()
        if force or now - self._last_logged >= self._interval:
            self._log(text)
            self._last_logged = now

    # ---- sinks ----

    def _log(self, text: str) -> None:
        if self._on_log is not None and text:
            self._on_log(text)

    def _emit(self, kind: str, payload: dict[str, object]) -> None:
        if self._on_event is None:
            return
        stage = self._stage
        fields: dict[str, object] = {"kind": kind}
        if stage is not None:
            fields |= {"index": stage.index, "stages": stage.total, "stage": stage.name}
        for key in ("step", "done", "total", "unit", "note", "summary", "elapsed", "ok", "warn", "frac", "eta", "k", "n", "name"):
            if key in payload:
                fields[key] = payload[key]
        self._on_event("progress", fields)

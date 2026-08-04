"""Content-free progress events for the pipeline (UC-01).

The pipeline reports stage progress through a ``ProgressSink``: the ingest,
extract, normalize, score, write, and review-artifact stages announce when
they start, advance where a completed/total denominator exists, and finish
with a wall-clock duration where one is measured. An event carries a stage
name, counts, and seconds; it never carries a path, a field value, or a
record id, so a sink can forward events anywhere without touching the
privacy posture. The default sink discards every event, which keeps library
callers unchanged unless they pass one. The CLI supplies the renderer
below: one line updated in place on a TTY, stable newline records
otherwise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Protocol, TextIO

Status = Literal["started", "advanced", "finished"]

STAGES = ("ingest", "extract", "normalize", "score", "write", "review_artifact")


@dataclass(frozen=True)
class ProgressEvent:
    """One content-free progress record.

    ``completed`` and ``total`` are present only when the stage has an honest
    denominator (files planned, records in the batch); ``duration_seconds``
    is present only on a ``finished`` event whose stage has a measured
    wall-clock duration. Every field is a stage name from ``STAGES``, a
    status, a count, or a number of seconds; the shape leaves no room for
    record content.
    """

    stage: str
    status: Status
    completed: int | None = None
    total: int | None = None
    duration_seconds: float | None = None


class ProgressSink(Protocol):
    """Receiver for pipeline progress events.

    Implementations must tolerate any well-formed event sequence and must not
    raise: a broken progress display should never fail a reconciliation run.
    """

    def emit(self, event: ProgressEvent) -> None:
        """Handle one event."""
        ...


class NullProgressSink:
    """The default sink: discards every event.

    Passing no sink to ``pipeline.run`` or ``pipeline.export`` selects this,
    so a library caller who ignores progress sees identical behavior to the
    releases before events existed.
    """

    def emit(self, event: ProgressEvent) -> None:
        """Discard the event."""


NULL_SINK = NullProgressSink()


def render_line(event: ProgressEvent) -> str:
    """Format one event as a single line of text, without a terminator.

    The stage token's underscore becomes a space so ``review_artifact`` reads
    as plain prose. Counts render as ``completed/total`` when a denominator
    exists, and a finish with a measured duration appends the seconds.
    """

    label = event.stage.replace("_", " ")
    parts = [f"progress: {label}"]
    if event.total is not None:
        parts.append(f"{event.completed or 0}/{event.total}")
    if event.status == "started":
        parts.append("started")
    elif event.status == "finished":
        parts.append("done")
        if event.duration_seconds is not None:
            parts.append(f"in {event.duration_seconds:.2f}s")
    return " ".join(parts)


class ConsoleProgressRenderer:
    """The CLI's default sink: render progress to a terminal stream.

    On a TTY the renderer redraws the current stage's line in place with a
    carriage return and ends it with a newline when the stage finishes, so
    the operator sees one updating line per stage and a stable line once the
    stage is done. On a stream that is not a TTY it writes newline-terminated
    records for stage starts and finishes and writes no control characters
    at all; ``advanced`` events are not rendered there because a large batch
    would put one line per record into a log file.

    ``min_redraw_seconds`` bounds how often ``advanced`` events repaint a
    TTY; ``started`` and ``finished`` events always paint. Rendering state
    lives here and never feeds back into the pipeline.
    """

    def __init__(self, stream: TextIO, *, min_redraw_seconds: float = 0.1) -> None:
        self._stream = stream
        self._is_tty = bool(stream.isatty())
        self._min_redraw_seconds = min_redraw_seconds
        self._last_redraw = float("-inf")
        self._line_width = 0
        self._line_open = False
        self._broken = False

    def emit(self, event: ProgressEvent) -> None:
        """Render one event in the mode chosen by the stream's isatty().

        A stream that fails mid-run (a broken pipe, a closed file) latches
        the renderer off instead of raising: progress is advisory output,
        and a rendering failure must never take down the pipeline between a
        connector write and its provenance entry.
        """

        if self._broken:
            return
        try:
            if self._is_tty:
                self._render_tty(event)
            elif event.status != "advanced":
                self._stream.write(render_line(event) + "\n")
                self._stream.flush()
        except (OSError, ValueError):
            self._broken = True
            self._line_open = False

    def close(self) -> None:
        """Terminate an in-place line a failed or interrupted stage left open.

        Without this, an error message printed after a mid-stage exception
        would land on the tail of the progress line. Safe to call more than
        once; a no-op when no line is open.
        """

        if self._broken or not self._line_open:
            return
        try:
            self._stream.write("\n")
            self._stream.flush()
        except (OSError, ValueError):
            self._broken = True
        self._line_open = False

    def _render_tty(self, event: ProgressEvent) -> None:
        if event.status == "advanced":
            now = time.monotonic()
            if now - self._last_redraw < self._min_redraw_seconds:
                return
            self._last_redraw = now
        text = render_line(event)
        padding = " " * max(self._line_width - len(text), 0)
        self._stream.write("\r" + text + padding)
        if event.status == "finished":
            self._stream.write("\n")
            self._line_width = 0
            self._line_open = False
        else:
            self._line_width = len(text)
            self._line_open = True
        self._stream.flush()

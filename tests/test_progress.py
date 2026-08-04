"""Tests for the UC-01 progress events and their CLI rendering.

The pipeline half injects a recording sink and asserts event ordering,
counts, and content-freedom over real runs of small fixtures; the renderer
half drives ``ConsoleProgressRenderer`` directly with fake TTY and non-TTY
streams, so no pty is needed. A final test proves the no-op default leaves
library callers' results untouched.
"""

from __future__ import annotations

import dataclasses
import inspect
import io
import json
from pathlib import Path

import pytest

from constituent_reconciler import matching, pipeline
from constituent_reconciler.cli import main
from constituent_reconciler.config import ExtractConfig, Recipe
from constituent_reconciler.progress import (
    NULL_SINK,
    STAGES,
    ConsoleProgressRenderer,
    NullProgressSink,
    ProgressEvent,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


class RecordingSink:
    """Sink that keeps every event for assertions."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)


class _Stream(io.StringIO):
    """A StringIO whose isatty() answer is chosen by the test."""

    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _fixture_recipe(tmp_path: Path, *, with_text: bool) -> Recipe:
    """A two-file incoming directory: one CSV, and optionally one text intake."""

    incoming = tmp_path / "incoming"
    incoming.mkdir(parents=True)
    (incoming / "a.csv").write_text("first,last\nAda,Lovelace\nGrace,Hopper\n", encoding="utf-8")
    if with_text:
        (incoming / "note.txt").write_text(
            "First Name: Jean\nLast Name: Bartik\n", encoding="utf-8"
        )
    return Recipe(
        incoming=incoming,
        mapping={"first_name": "first", "last_name": "last"},
        fields=("first_name", "last_name"),
        extract=ExtractConfig(backend="pdfplumber"),
    )


def _no_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip Splink so the fixture runs stay fast and deterministic."""

    monkeypatch.setattr(matching, "score_pairs", lambda records, fields, prior: [])


# ---------------------------------------------------------------------------
# Event ordering and counts
# ---------------------------------------------------------------------------


def test_run_emits_ordered_events_with_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_pairs(monkeypatch)
    sink = RecordingSink()
    pipeline.run(_fixture_recipe(tmp_path, with_text=True), progress=sink)

    assert [(e.stage, e.status) for e in sink.events] == [
        ("ingest", "started"),
        ("extract", "started"),
        ("ingest", "advanced"),  # a.csv
        ("extract", "advanced"),  # note.txt
        ("ingest", "advanced"),
        ("extract", "finished"),
        ("ingest", "finished"),
        ("normalize", "started"),
        ("normalize", "advanced"),
        ("normalize", "advanced"),
        ("normalize", "advanced"),
        ("normalize", "finished"),
        ("score", "started"),
        ("score", "finished"),
    ]
    by_key = {(e.stage, e.status): e for e in sink.events}
    assert (by_key["ingest", "started"].completed, by_key["ingest", "started"].total) == (0, 2)
    assert (by_key["ingest", "finished"].completed, by_key["ingest", "finished"].total) == (2, 2)
    assert (by_key["extract", "finished"].completed, by_key["extract", "finished"].total) == (1, 1)
    assert by_key["normalize", "finished"].completed == 3
    assert by_key["normalize", "finished"].total == 3
    # Durations ride on the finish of each stage the run summary times;
    # extraction is interleaved with ingest and has no clock of its own.
    assert by_key["ingest", "finished"].duration_seconds is not None
    assert by_key["normalize", "finished"].duration_seconds is not None
    assert by_key["score", "finished"].duration_seconds is not None
    assert by_key["extract", "finished"].duration_seconds is None
    # The score stage has no denominator before it runs, so no counts.
    assert by_key["score", "started"].total is None
    assert by_key["score", "finished"].completed is None


def test_run_without_documents_emits_no_extract_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_pairs(monkeypatch)
    sink = RecordingSink()
    pipeline.run(_fixture_recipe(tmp_path, with_text=False), progress=sink)
    assert all(event.stage != "extract" for event in sink.events)
    assert [(e.stage, e.status) for e in sink.events][:2] == [
        ("ingest", "started"),
        ("ingest", "advanced"),
    ]


def test_export_emits_write_then_review_artifact_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_pairs(monkeypatch)
    result = pipeline.run(_fixture_recipe(tmp_path, with_text=True))
    sink = RecordingSink()
    pipeline.export(
        result,
        _fixture_recipe(tmp_path / "again", with_text=True),
        out_dir=tmp_path / "out",
        progress=sink,
    )
    assert [(e.stage, e.status) for e in sink.events] == [
        ("write", "started"),
        ("write", "finished"),
        ("review_artifact", "started"),
        ("review_artifact", "finished"),
    ]
    write_finished = sink.events[1]
    assert (write_finished.completed, write_finished.total) == (3, 3)
    assert write_finished.duration_seconds is not None
    review_finished = sink.events[3]
    assert (review_finished.completed, review_finished.total) == (0, 0)
    assert review_finished.duration_seconds is not None


def test_dry_run_export_emits_the_same_events_as_a_real_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both export stages still execute under --dry-run (the connector
    # classifies records without writing; the review queue is written either
    # way), so the event stream matches a real export in everything but the
    # measured durations.
    _no_pairs(monkeypatch)
    recipe = _fixture_recipe(tmp_path, with_text=True)
    result = pipeline.run(recipe)
    wet, dry = RecordingSink(), RecordingSink()
    pipeline.export(result, recipe, out_dir=tmp_path / "wet", progress=wet)
    pipeline.export(result, recipe, out_dir=tmp_path / "dry", dry_run=True, progress=dry)

    def shape(sink: RecordingSink) -> list[tuple[str, str, int | None, int | None]]:
        return [(e.stage, e.status, e.completed, e.total) for e in sink.events]

    assert shape(dry) == shape(wet)


# ---------------------------------------------------------------------------
# Content-freedom
# ---------------------------------------------------------------------------


def test_events_are_content_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_pairs(monkeypatch)
    recipe = _fixture_recipe(tmp_path, with_text=True)
    sink = RecordingSink()
    result = pipeline.run(recipe, progress=sink)
    pipeline.export(result, recipe, out_dir=tmp_path / "out", progress=sink)

    leak_candidates = (
        "Ada",
        "Lovelace",
        "Grace",
        "Hopper",
        "Jean",
        "Bartik",
        "a.csv",
        "note.txt",
        "incoming:",
        str(tmp_path),
    )
    assert sink.events
    for event in sink.events:
        # Structural: the event has exactly the content-free fields, and the
        # string-typed ones come from fixed vocabularies.
        payload = dataclasses.asdict(event)
        assert set(payload) == {"stage", "status", "completed", "total", "duration_seconds"}
        assert event.stage in STAGES
        assert event.status in ("started", "advanced", "finished")
        assert event.completed is None or isinstance(event.completed, int)
        assert event.total is None or isinstance(event.total, int)
        assert event.duration_seconds is None or isinstance(event.duration_seconds, float)
        serialized = json.dumps(payload)
        for candidate in leak_candidates:
            assert candidate not in serialized


# ---------------------------------------------------------------------------
# No-op default: zero behavior change for library callers
# ---------------------------------------------------------------------------


def test_default_sink_is_a_noop_and_results_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert isinstance(
        inspect.signature(pipeline.run).parameters["progress"].default, NullProgressSink
    )
    assert inspect.signature(pipeline.export).parameters["progress"].default is NULL_SINK
    assert NULL_SINK.emit(ProgressEvent("score", "started")) is None

    _no_pairs(monkeypatch)
    silent = pipeline.run(_fixture_recipe(tmp_path / "a", with_text=True))
    observed = pipeline.run(
        _fixture_recipe(tmp_path / "b", with_text=True), progress=RecordingSink()
    )
    assert silent.records == observed.records
    assert silent.pairs == observed.pairs
    assert silent.golden == observed.golden


# ---------------------------------------------------------------------------
# Renderer: non-TTY
# ---------------------------------------------------------------------------


def test_non_tty_rendering_writes_stable_records_without_control_characters() -> None:
    stream = _Stream(tty=False)
    renderer = ConsoleProgressRenderer(stream, min_redraw_seconds=0.0)
    renderer.emit(ProgressEvent("ingest", "started", completed=0, total=2))
    renderer.emit(ProgressEvent("ingest", "advanced", completed=1, total=2))
    renderer.emit(ProgressEvent("ingest", "finished", completed=2, total=2, duration_seconds=0.5))
    renderer.emit(ProgressEvent("score", "started"))
    renderer.emit(ProgressEvent("score", "finished", duration_seconds=1.25))
    renderer.emit(ProgressEvent("review_artifact", "started", completed=0, total=4))
    renderer.close()  # a no-op off-TTY: no line is ever left open

    out = stream.getvalue()
    assert "\r" not in out
    assert "\x1b" not in out
    assert out.splitlines() == [
        "progress: ingest 0/2 started",
        "progress: ingest 2/2 done in 0.50s",
        "progress: score started",
        "progress: score done in 1.25s",
        "progress: review artifact 0/4 started",
    ]


# ---------------------------------------------------------------------------
# Renderer: TTY
# ---------------------------------------------------------------------------


def test_tty_rendering_updates_one_line_in_place() -> None:
    stream = _Stream(tty=True)
    renderer = ConsoleProgressRenderer(stream, min_redraw_seconds=0.0)
    renderer.emit(ProgressEvent("ingest", "started", completed=0, total=2))
    renderer.emit(ProgressEvent("ingest", "advanced", completed=1, total=2))
    renderer.emit(ProgressEvent("ingest", "finished", completed=2, total=2, duration_seconds=0.1))

    out = stream.getvalue()
    # One newline total: the stage finish. Every repaint rewrites in place.
    assert out.count("\n") == 1
    frames = [frame.rstrip() for frame in out.split("\r") if frame]
    assert frames == [
        "progress: ingest 0/2 started",
        "progress: ingest 1/2",
        "progress: ingest 2/2 done in 0.10s",
    ]
    # The shorter middle frame is padded to erase the longer first one.
    assert "\rprogress: ingest 1/2 " in out


def test_tty_advanced_redraws_are_throttled_but_start_and_finish_paint() -> None:
    stream = _Stream(tty=True)
    renderer = ConsoleProgressRenderer(stream, min_redraw_seconds=3600.0)
    renderer.emit(ProgressEvent("normalize", "started", completed=0, total=9))
    renderer.emit(ProgressEvent("normalize", "advanced", completed=1, total=9))
    renderer.emit(ProgressEvent("normalize", "advanced", completed=2, total=9))
    renderer.emit(ProgressEvent("normalize", "finished", completed=9, total=9))

    out = stream.getvalue()
    assert "0/9" in out
    assert "1/9" in out  # the first repaint after start always lands
    assert "2/9" not in out  # the next one is inside the redraw window
    assert "9/9 done" in out


def test_close_terminates_an_open_tty_line_once() -> None:
    stream = _Stream(tty=True)
    renderer = ConsoleProgressRenderer(stream, min_redraw_seconds=0.0)
    renderer.emit(ProgressEvent("ingest", "started", completed=0, total=3))
    renderer.close()
    renderer.close()
    assert stream.getvalue() == "\rprogress: ingest 0/3 started\n"


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_run_renders_progress_records_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # capsys replaces stderr with a non-TTY stream, so this covers the
    # pipe/log rendering end to end: stable records, no control characters.
    code = main(
        [
            "run",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--out",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert code == 0
    err = capsys.readouterr().err
    assert "\r" not in err
    assert "progress: ingest 0/2 started" in err
    assert "progress: ingest 2/2 done in " in err
    assert "progress: normalize 27/27 done in " in err
    assert "progress: score done in " in err
    assert "progress: write 0/21 started" in err
    assert "progress: write 21/21 done in " in err
    assert "progress: review artifact" in err
    # The demo recipe ingests CSVs only, so no extract stage is reported.
    assert "progress: extract" not in err

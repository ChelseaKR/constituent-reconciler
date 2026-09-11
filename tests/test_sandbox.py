"""Tests for the sandboxed PDF extractor: happy path and every fail-closed leg.

The injected workers below must be module-level functions: the spawn context
pickles the child target by reference, and the spawned interpreter re-imports
this module to find it.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import time
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from constituent_reconciler.extract.base import Extractor
from constituent_reconciler.extract.sandbox import (
    SCRATCH_PREFIX,
    ChildLimits,
    SandboxedExtractor,
    _enter_child,
    _kill,
)

pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")

# Generous ceiling for "the parent came back promptly" assertions: spawn
# startup on a loaded CI box is slow, but nowhere near this slow.
_PROMPT_RETURN_S = 20.0


def _sleepy_worker(path: Path, conn: Connection, limits: ChildLimits) -> None:
    """Simulates a parse that hangs: never replies, sleeps past any test timeout."""
    time.sleep(60)


def _exiting_worker(path: Path, conn: Connection, limits: ChildLimits) -> None:
    """Simulates a parser crash: exits nonzero without sending a result."""
    sys.exit(3)


def _temp_file_then_crash_worker(path: Path, conn: Connection, limits: ChildLimits) -> None:
    """Enters the child's limits, leaves a temporary file behind, and crashes.

    That is what pytesseract does when its process dies mid-read: its copy of
    the page image is written to ``tempfile``'s directory and removed only in a
    ``finally`` that never runs. Where the file went is written beside the
    document so the test can look.
    """
    _enter_child(limits)
    with tempfile.NamedTemporaryFile(prefix="tess_", delete=False) as handle:
        handle.write(b"a copy of an intake page")
    path.with_suffix(".where").write_text(handle.name, encoding="utf-8")
    sys.exit(3)


def _grandchild_then_hang_worker(path: Path, conn: Connection, limits: ChildLimits) -> None:
    """Starts a subprocess, as OCR starts Tesseract, then hangs."""
    _enter_child(limits)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])  # noqa: S603
    path.with_suffix(".grandchild").write_text(str(child.pid), encoding="utf-8")
    time.sleep(300)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _assert_fail_closed(extractor_result: object, source_file: str) -> None:
    from constituent_reconciler.extract.base import ExtractionResult

    assert isinstance(extractor_result, ExtractionResult)
    assert extractor_result.source_file == source_file
    assert len(extractor_result.pages) == 1
    page = extractor_result.pages[0]
    assert page.page_num == 1
    assert page.confidence == 0.0
    assert page.fields == []
    assert extractor_result.note


def test_sandboxed_extractor_satisfies_extractor_protocol() -> None:
    assert isinstance(SandboxedExtractor(), Extractor)


def test_sandboxed_extract_matches_in_process_extraction(intake_pdf: Path) -> None:
    from constituent_reconciler.extract.pdf import extract_pdf

    in_process = extract_pdf(intake_pdf)
    sandboxed = SandboxedExtractor().extract(intake_pdf)

    assert sandboxed.note is None
    assert sandboxed.source_file == in_process.source_file
    assert sandboxed.pages == in_process.pages


def test_hung_child_is_killed_at_the_wall_clock_limit(intake_pdf: Path) -> None:
    extractor = SandboxedExtractor(wall_timeout_s=1.0, worker=_sleepy_worker)
    start = time.monotonic()
    result = extractor.extract(intake_pdf)
    elapsed = time.monotonic() - start

    assert elapsed < _PROMPT_RETURN_S
    _assert_fail_closed(result, intake_pdf.name)
    assert "wall-clock" in (result.note or "")


def test_oversize_input_fails_closed_without_spawning(tmp_path: Path) -> None:
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.4" + b"\0" * 1024)

    # The sleepy worker would stall for the full wall timeout if a child were
    # spawned; a prompt return proves the size cap short-circuits first.
    extractor = SandboxedExtractor(max_input_bytes=64, wall_timeout_s=60.0, worker=_sleepy_worker)
    start = time.monotonic()
    result = extractor.extract(big)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    _assert_fail_closed(result, "big.pdf")
    assert "cap" in (result.note or "")


def test_child_nonzero_exit_fails_closed(intake_pdf: Path) -> None:
    extractor = SandboxedExtractor(worker=_exiting_worker)
    result = extractor.extract(intake_pdf)

    _assert_fail_closed(result, intake_pdf.name)
    assert "3" in (result.note or "")


def test_corrupt_pdf_fails_closed_with_reason(tmp_path: Path) -> None:
    not_a_pdf = tmp_path / "corrupt.pdf"
    not_a_pdf.write_bytes(b"this is not a pdf at all")

    result = SandboxedExtractor().extract(not_a_pdf)

    _assert_fail_closed(result, "corrupt.pdf")
    assert "extraction failed" in (result.note or "")


# ---------------------------------------------------------------------------
# OCR worker selection (FIX-10 wiring): backend "pdfplumber+ocr" runs the
# OCR-fallback extractor inside the same containment.
# ---------------------------------------------------------------------------


def test_ocr_flag_selects_the_ocr_worker() -> None:
    from constituent_reconciler.extract.sandbox import _extract_in_child, _extract_ocr_in_child

    assert SandboxedExtractor()._worker is _extract_in_child
    assert SandboxedExtractor(ocr=True)._worker is _extract_ocr_in_child
    # An explicitly injected worker (the test seam) wins over the flag.
    assert SandboxedExtractor(ocr=True, worker=_sleepy_worker)._worker is _sleepy_worker


def test_sandboxed_ocr_extractor_parses_a_text_layer_pdf(intake_pdf: Path) -> None:
    # A text-layer page never reaches Tesseract, so the OCR child needs no OCR
    # dependencies; the spawned parse must match the in-process one.
    from constituent_reconciler.extract.base import ExtractionResult
    from constituent_reconciler.extract.pdf import PdfplumberExtractor

    sandboxed = SandboxedExtractor(ocr=True).extract(intake_pdf)
    direct = PdfplumberExtractor().extract(intake_pdf)

    def fields(result: ExtractionResult) -> list[tuple[str, str]]:
        return [(field.field_name, field.value) for page in result.pages for field in page.fields]

    assert fields(sandboxed) == fields(direct)


def test_image_flag_selects_the_image_worker() -> None:
    from constituent_reconciler.extract.sandbox import _extract_image_in_child

    assert SandboxedExtractor(image=True)._worker is _extract_image_in_child
    # An image is always OCR'd; the PDF OCR flag does not change which worker reads it.
    assert SandboxedExtractor(image=True, ocr=True)._worker is _extract_image_in_child


# ---------------------------------------------------------------------------
# What a failed or killed child leaves behind is removed by the parent
# ---------------------------------------------------------------------------


def test_a_crashed_child_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    doc = tmp_path / "doc.pdf"
    doc.write_bytes(b"%PDF-1.4 stand-in bytes")

    result = SandboxedExtractor(
        worker=_temp_file_then_crash_worker, scratch_root=scratch_root
    ).extract(doc)
    _assert_fail_closed(result, "doc.pdf")

    written = Path(doc.with_suffix(".where").read_text(encoding="utf-8"))
    # The child's temporary file went into this parse's own scratch directory,
    # not the system one, and went away with it.
    assert written.parent.parent == scratch_root
    assert written.parent.name.startswith(SCRATCH_PREFIX)
    assert not written.exists()
    assert list(scratch_root.iterdir()) == []


def test_a_killed_child_leaves_no_scratch_directory_behind(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    doc = tmp_path / "doc.pdf"
    doc.write_bytes(b"%PDF-1.4 stand-in bytes")

    result = SandboxedExtractor(
        wall_timeout_s=1.0, worker=_sleepy_worker, scratch_root=scratch_root
    ).extract(doc)
    _assert_fail_closed(result, "doc.pdf")
    assert list(scratch_root.iterdir()) == []


def test_a_kill_reaches_what_the_child_started(tmp_path: Path) -> None:
    """Tesseract runs as the child's subprocess; killing the child alone orphans it."""
    doc = tmp_path / "doc.pdf"
    doc.write_bytes(b"%PDF-1.4 stand-in bytes")
    limits = ChildLimits(
        cpu_seconds=60,
        max_address_space_bytes=1 << 30,
        max_image_pixels=1,
        scratch_dir=str(tmp_path),
    )
    ctx = multiprocessing.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_grandchild_then_hang_worker, args=(doc, send_conn, limits))
    proc.start()
    send_conn.close()
    marker = doc.with_suffix(".grandchild")
    deadline = time.monotonic() + 120
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    grandchild = int(marker.read_text(encoding="utf-8")) if marker.exists() else None
    try:
        assert grandchild is not None, "the child never started its subprocess"
        _kill(proc)
        deadline = time.monotonic() + 20
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(grandchild), "the child's subprocess outlived the kill"
    finally:
        recv_conn.close()
        if proc.is_alive():
            proc.kill()
        if grandchild is not None and _alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)

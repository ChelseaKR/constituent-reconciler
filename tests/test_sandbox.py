"""Tests for the sandboxed PDF extractor: happy path and every fail-closed leg.

The injected workers below must be module-level functions: the spawn context
pickles the child target by reference, and the spawned interpreter re-imports
this module to find it.
"""

from __future__ import annotations

import sys
import time
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from constituent_reconciler.extract.base import Extractor
from constituent_reconciler.extract.sandbox import SandboxedExtractor

pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")

# Generous ceiling for "the parent came back promptly" assertions: spawn
# startup on a loaded CI box is slow, but nowhere near this slow.
_PROMPT_RETURN_S = 20.0


def _sleepy_worker(
    path: Path, conn: Connection, cpu_seconds: int, max_address_space_bytes: int
) -> None:
    """Simulates a parse that hangs: never replies, sleeps past any test timeout."""
    time.sleep(60)


def _exiting_worker(
    path: Path, conn: Connection, cpu_seconds: int, max_address_space_bytes: int
) -> None:
    """Simulates a parser crash: exits nonzero without sending a result."""
    sys.exit(3)


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

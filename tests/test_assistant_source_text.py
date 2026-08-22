"""Tests for reading the real source-document text behind one field's span."""

from __future__ import annotations

from pathlib import Path

from constituent_reconciler.assistant.source_text import for_field
from constituent_reconciler.models import Record, SourceSpan, TextSpan
from constituent_reconciler.testing import make_pdf


def test_text_span_reads_the_source_file(tmp_path: Path) -> None:
    source_path = tmp_path / "intake.txt"
    source_path.write_text("First Name: Maria\nLast Name: Garcia\n", encoding="utf-8")
    record = Record(
        unique_id="r1",
        source="test",
        raw={"first_name": "Maria"},
        spans={
            "first_name": TextSpan(source_file=str(source_path), line=1, col_start=12, col_end=17)
        },
    )
    text = for_field(record, "first_name")
    assert text is not None
    assert "Maria" in text


def test_missing_text_span_file_returns_none(tmp_path: Path) -> None:
    record = Record(
        unique_id="r1",
        source="test",
        raw={},
        spans={
            "first_name": TextSpan(
                source_file=str(tmp_path / "missing.txt"), line=1, col_start=0, col_end=5
            )
        },
    )
    assert for_field(record, "first_name") is None


def test_field_with_no_span_returns_none() -> None:
    record = Record(unique_id="r1", source="test", raw={"first_name": "Maria"})
    assert for_field(record, "first_name") is None


def test_source_span_reads_the_pdf_page_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "intake.pdf"
    pdf_path.write_bytes(make_pdf(["First Name: Maria", "Last Name: Garcia"]))
    record = Record(
        unique_id="r1",
        source="test",
        raw={"first_name": "Maria"},
        spans={
            "first_name": SourceSpan(
                source_file=str(pdf_path), page=1, x0=0, top=0, x1=100, bottom=20
            )
        },
    )
    text = for_field(record, "first_name")
    assert text is not None
    assert "Maria" in text

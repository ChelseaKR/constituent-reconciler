"""Tests for the extraction package: base types, PDF extractor, cloud seam."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from constituent_reconciler.extract.seam import BedrockSeam, NoOpSeam, make_seam
from constituent_reconciler.models import SourceSpan

pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")


# ---------------------------------------------------------------------------
# SourceSpan
# ---------------------------------------------------------------------------


def test_source_span_str_format() -> None:
    span = SourceSpan(source_file="form.pdf", page=2, x0=72.0, top=300.0, x1=200.0, bottom=312.0)
    assert str(span) == "form.pdf:p2:x=72-200,y=300-312"


def test_source_span_is_hashable() -> None:
    span = SourceSpan("a.pdf", 1, 0.0, 0.0, 100.0, 12.0)
    assert {span} == {span}


def test_source_span_is_frozen() -> None:
    span = SourceSpan("a.pdf", 1, 0.0, 0.0, 100.0, 12.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        span.page = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Page-level confidence heuristic
# ---------------------------------------------------------------------------


def test_empty_page_has_zero_confidence() -> None:
    from constituent_reconciler.extract.pdf import _page_confidence

    assert _page_confidence("") == 0.0
    assert _page_confidence("   ") == 0.0


def test_short_page_is_low_confidence() -> None:
    from constituent_reconciler.extract.pdf import _page_confidence

    # Fewer than _MIN_WORDS (5) words scores below 0.5.
    score = _page_confidence("Hi")
    assert score < 0.5


def test_garbled_page_is_low_confidence() -> None:
    from constituent_reconciler.extract.pdf import _page_confidence

    # Average word > 15 chars triggers the garbled-OCR heuristic.
    garbled = " ".join(["XYZABCDEFGHIJKLMNOP"] * 20)  # avg len = 18
    assert _page_confidence(garbled) < 0.5


def test_normal_page_reaches_full_confidence() -> None:
    from constituent_reconciler.extract.pdf import _page_confidence

    normal = "First Name: Alice\nLast Name: Walker\nDOB: 1970-05-12\nEmail: a@b.co\nPhone: 555"
    assert _page_confidence(normal) == 1.0


# ---------------------------------------------------------------------------
# PDF extraction (requires pdfplumber + conftest PDF fixture)
# ---------------------------------------------------------------------------


def test_extract_pdf_finds_all_fields(intake_pdf: Path) -> None:
    from constituent_reconciler.extract.base import ExtractionResult
    from constituent_reconciler.extract.pdf import extract_pdf

    result = extract_pdf(intake_pdf)
    assert isinstance(result, ExtractionResult)
    assert len(result.pages) == 1
    page = result.pages[0]
    by_field = {f.field_name: f.value for f in page.fields}
    assert by_field.get("first_name") == "Alice"
    assert by_field.get("last_name") == "Walker"
    assert by_field.get("dob") == "1970-05-12"
    assert by_field.get("email") == "alice@example.org"
    assert by_field.get("phone") == "555-123-4567"


def test_extract_pdf_page_has_full_confidence(intake_pdf: Path) -> None:
    from constituent_reconciler.extract.pdf import extract_pdf

    result = extract_pdf(intake_pdf)
    page = result.pages[0]
    assert page.confidence == 1.0
    for ef in page.fields:
        assert ef.confidence == page.confidence


def test_extract_pdf_low_confidence_page_is_flagged(low_confidence_pdf: Path) -> None:
    from constituent_reconciler.extract.pdf import extract_pdf

    result = extract_pdf(low_confidence_pdf)
    assert len(result.pages) == 1
    assert result.pages[0].confidence < 0.5
    assert result.low_confidence_pages(threshold=0.5) == result.pages


def test_extracted_field_span_is_none_or_source_span(intake_pdf: Path) -> None:
    from constituent_reconciler.extract.pdf import extract_pdf

    result = extract_pdf(intake_pdf)
    for page in result.pages:
        for ef in page.fields:
            assert ef.span is None or isinstance(ef.span, SourceSpan)


# ---------------------------------------------------------------------------
# Cloud seam policy gate
# ---------------------------------------------------------------------------


def test_no_op_seam_is_always_disabled() -> None:
    seam = NoOpSeam()
    assert seam.is_enabled() is False


def test_no_op_seam_refine_returns_empty() -> None:
    seam = NoOpSeam()
    assert seam.refine(Path("any.pdf"), 1) == []


def test_dv_pack_forces_no_op_seam() -> None:
    seam = make_seam("dv", backend="bedrock")
    assert isinstance(seam, NoOpSeam)


def test_hipaa_pack_forces_no_op_seam() -> None:
    seam = make_seam("hipaa", backend="bedrock")
    assert isinstance(seam, NoOpSeam)


def test_default_pack_none_backend_returns_no_op() -> None:
    seam = make_seam("default", backend="none")
    assert isinstance(seam, NoOpSeam)


def test_default_pack_bedrock_backend_returns_bedrock_seam() -> None:
    seam = make_seam("default", backend="bedrock")
    assert isinstance(seam, BedrockSeam)


# ---------------------------------------------------------------------------
# Pipeline integration: PDF records carry source spans in the review queue
# ---------------------------------------------------------------------------


def test_read_pdf_records_produces_records_with_correct_fields(intake_pdf: Path) -> None:
    from dataclasses import replace

    from constituent_reconciler.config import ExtractConfig, load_recipe
    from constituent_reconciler.pipeline import read_pdf_records

    recipe = load_recipe(
        Path(__file__).resolve().parents[1] / "examples" / "intake-demo" / "recipe.toml"
    )
    recipe = replace(recipe, extract=ExtractConfig(backend="pdfplumber"))

    records = read_pdf_records(intake_pdf, "incoming", recipe=recipe, id_prefix="N")
    assert len(records) == 1
    rec = records[0]
    assert rec.raw.get("first_name") == "Alice"
    assert rec.raw.get("last_name") == "Walker"
    assert rec.source == "incoming"
    assert rec.unique_id.startswith("N")
    assert isinstance(rec.spans, dict)


def test_review_queue_includes_span_columns_when_records_have_spans(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from constituent_reconciler.config import ExtractConfig, load_recipe
    from constituent_reconciler.models import Band, Pair, Record, RunResult
    from constituent_reconciler.pipeline import _write_review_queue

    recipe = load_recipe(
        Path(__file__).resolve().parents[1] / "examples" / "intake-demo" / "recipe.toml"
    )
    recipe = replace(recipe, extract=ExtractConfig(backend="pdfplumber"))

    span = SourceSpan(
        source_file="form.pdf", page=1, x0=72.0, top=300.0, x1=200.0, bottom=312.0
    )
    left = Record(
        unique_id="N0001",
        source="incoming",
        raw={"first_name": "Alice", "last_name": "Walker"},
        spans={"first_name": span},
    )
    right = Record(
        unique_id="E0001",
        source="existing",
        raw={"first_name": "Alice", "last_name": "Walker"},
    )
    pair = Pair("N0001", "E0001", 0.85, Band.REVIEW)
    result = RunResult(
        records={"N0001": left, "E0001": right},
        pairs=(pair,),
        clusters=(),
        golden=(),
    )

    review_path = _write_review_queue(result, recipe, tmp_path)
    content = review_path.read_text(encoding="utf-8")
    header = content.splitlines()[0]
    assert "first_name_left_span" in header
    assert "first_name_right_span" in header
    assert "form.pdf:p1" in content

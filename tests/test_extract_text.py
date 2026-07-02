"""Tests for text and .eml extraction: TextSpan, extractors, pipeline routing.

Everything here is stdlib-only; pdfplumber is not required.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest

from constituent_reconciler.config import ExtractConfig, Recipe, load_recipe
from constituent_reconciler.extract.text import TextExtractor, extract_eml, extract_text_file
from constituent_reconciler.models import TextSpan
from constituent_reconciler.pipeline import _ingest_source, read_text_records

_BODY = "First Name: Ada\nLast Name: Lovelace\nEmail: ada@example.org\n"


def _demo_recipe(backend: str = "pdfplumber") -> Recipe:
    recipe = load_recipe(
        Path(__file__).resolve().parents[1] / "examples" / "intake-demo" / "recipe.toml"
    )
    return replace(recipe, extract=ExtractConfig(backend=backend))


def _write_eml(path: Path, msg: EmailMessage) -> Path:
    path.write_bytes(bytes(msg))
    return path


def _intake_eml(body: str = _BODY) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "ada@example.org"
    msg["To"] = "intake@example.org"
    msg["Subject"] = "New intake"
    msg.set_content(body)
    return msg


# ---------------------------------------------------------------------------
# TextSpan
# ---------------------------------------------------------------------------


def test_text_span_str_format() -> None:
    span = TextSpan(source_file="note.txt", line=2, col_start=11, col_end=19)
    assert str(span) == "note.txt:L2:c11-19"


def test_text_span_is_hashable() -> None:
    span = TextSpan("note.txt", 1, 0, 3)
    assert {span} == {span}


def test_text_span_is_frozen() -> None:
    span = TextSpan("note.txt", 1, 0, 3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        span.line = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Plain-text extraction
# ---------------------------------------------------------------------------


def test_extract_text_file_finds_fields_with_line_offset_spans(tmp_path: Path) -> None:
    path = tmp_path / "intake.txt"
    path.write_text(_BODY, encoding="utf-8")

    result = extract_text_file(path)
    assert result.source_file == "intake.txt"
    assert len(result.pages) == 1
    by_field = {f.field_name: f for f in result.pages[0].fields}

    assert by_field["first_name"].value == "Ada"
    assert by_field["last_name"].value == "Lovelace"
    assert by_field["email"].value == "ada@example.org"

    assert by_field["first_name"].span == TextSpan("intake.txt", 1, 12, 15)
    assert by_field["last_name"].span == TextSpan("intake.txt", 2, 11, 19)
    assert by_field["email"].span == TextSpan("intake.txt", 3, 7, 22)


def test_extract_text_file_empty_body_is_zero_confidence(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    result = extract_text_file(path)
    assert len(result.pages) == 1
    assert result.pages[0].confidence == 0.0
    assert result.pages[0].fields == []


def test_extract_text_file_short_body_is_low_confidence(tmp_path: Path) -> None:
    path = tmp_path / "short.txt"
    path.write_text("Hi", encoding="utf-8")

    result = extract_text_file(path)
    assert result.pages[0].confidence < 0.5
    assert result.low_confidence_pages(threshold=0.5) == result.pages


# ---------------------------------------------------------------------------
# .eml extraction
# ---------------------------------------------------------------------------


def test_extract_eml_round_trips_plain_body(tmp_path: Path) -> None:
    path = _write_eml(tmp_path / "intake.eml", _intake_eml())

    result = extract_eml(path)
    assert result.source_file == "intake.eml"
    by_field = {f.field_name: f for f in result.pages[0].fields}
    assert by_field["first_name"].value == "Ada"
    assert by_field["last_name"].value == "Lovelace"
    assert by_field["email"].value == "ada@example.org"

    span = by_field["last_name"].span
    assert isinstance(span, TextSpan)
    assert span.line == 2
    assert str(span) == "intake.eml:L2:c11-19"


def test_extract_eml_prefers_plain_part_in_multipart_alternative(tmp_path: Path) -> None:
    msg = _intake_eml()
    msg.add_alternative(
        "<html><body><p>First Name: Zzz</p></body></html>", subtype="html"
    )
    path = _write_eml(tmp_path / "multipart.eml", msg)

    result = extract_eml(path)
    by_field = {f.field_name: f.value for f in result.pages[0].fields}
    assert by_field["first_name"] == "Ada"


def test_extract_eml_ignores_attachment_parts(tmp_path: Path) -> None:
    msg = _intake_eml()
    msg.add_attachment(
        b"%PDF-1.4 First Name: Mallory",
        maintype="application",
        subtype="pdf",
        filename="attached-form.pdf",
    )
    path = _write_eml(tmp_path / "with-attachment.eml", msg)

    result = extract_eml(path)
    by_field = {f.field_name: f.value for f in result.pages[0].fields}
    assert by_field["first_name"] == "Ada"
    assert "Mallory" not in by_field.values()


def test_extract_eml_with_no_text_body_yields_empty_page(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["From"] = "ada@example.org"
    msg["Subject"] = "Attachment only"
    msg.set_content(
        b"%PDF-1.4 First Name: Mallory",
        maintype="application",
        subtype="pdf",
        filename="attached-form.pdf",
    )
    path = _write_eml(tmp_path / "attachment-only.eml", msg)

    result = extract_eml(path)
    assert len(result.pages) == 1
    assert result.pages[0].confidence == 0.0
    assert result.pages[0].fields == []


def test_text_extractor_routes_by_suffix(tmp_path: Path) -> None:
    txt = tmp_path / "intake.txt"
    txt.write_text(_BODY, encoding="utf-8")
    eml = _write_eml(tmp_path / "intake.eml", _intake_eml())

    extractor = TextExtractor()
    assert extractor.extract(txt).source_file == "intake.txt"
    assert extractor.extract(eml).source_file == "intake.eml"


# ---------------------------------------------------------------------------
# Pipeline integration: text records, routing, review-queue span rendering
# ---------------------------------------------------------------------------


def test_read_text_records_produces_record_with_text_spans(tmp_path: Path) -> None:
    path = tmp_path / "intake.txt"
    path.write_text(_BODY, encoding="utf-8")

    records = read_text_records(path, "incoming", recipe=_demo_recipe(), id_prefix="N")
    assert len(records) == 1
    rec = records[0]
    assert rec.raw.get("first_name") == "Ada"
    assert rec.raw.get("last_name") == "Lovelace"
    assert rec.unique_id == "N0001"
    assert isinstance(rec.spans.get("first_name"), TextSpan)


def test_read_text_records_skips_body_without_names(tmp_path: Path) -> None:
    path = tmp_path / "no-names.txt"
    path.write_text("Email: someone@example.org\nPhone: 555-123-4567\n", encoding="utf-8")

    records = read_text_records(path, "incoming", recipe=_demo_recipe(), id_prefix="N")
    assert records == []


def test_ingest_source_routes_txt_and_eml_in_directory(tmp_path: Path) -> None:
    folder = tmp_path / "inbox"
    folder.mkdir()
    _write_eml(folder / "a-message.eml", _intake_eml())
    (folder / "b-note.txt").write_text(
        "First Name: Grace\nLast Name: Hopper\n", encoding="utf-8"
    )

    records = _ingest_source(folder, "incoming", recipe=_demo_recipe(), id_prefix="N")
    assert [r.raw.get("first_name") for r in records] == ["Ada", "Grace"]
    assert [r.unique_id for r in records] == ["N0001", "N0002"]


def test_ingest_source_routes_single_eml_file(tmp_path: Path) -> None:
    path = _write_eml(tmp_path / "intake.eml", _intake_eml())

    records = _ingest_source(path, "incoming", recipe=_demo_recipe(), id_prefix="N")
    assert len(records) == 1
    assert records[0].raw.get("last_name") == "Lovelace"


def test_ingest_source_skips_text_files_when_backend_is_none(tmp_path: Path) -> None:
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "note.txt").write_text(_BODY, encoding="utf-8")
    _write_eml(folder / "message.eml", _intake_eml())

    records = _ingest_source(
        folder, "incoming", recipe=_demo_recipe(backend="none"), id_prefix="N"
    )
    assert records == []


def test_review_queue_renders_text_spans_as_line_offsets(tmp_path: Path) -> None:
    from constituent_reconciler.models import Band, Pair, Record, RunResult
    from constituent_reconciler.pipeline import _write_review_queue

    span = TextSpan(source_file="intake.eml", line=2, col_start=11, col_end=19)
    left = Record(
        unique_id="N0001",
        source="incoming",
        raw={"first_name": "Ada", "last_name": "Lovelace"},
        spans={"last_name": span},
    )
    right = Record(
        unique_id="E0001",
        source="existing",
        raw={"first_name": "Ada", "last_name": "Lovelace"},
    )
    pair = Pair("N0001", "E0001", 0.85, Band.REVIEW)
    result = RunResult(
        records={"N0001": left, "E0001": right},
        pairs=(pair,),
        clusters=(),
        golden=(),
    )

    review_path = _write_review_queue(result, _demo_recipe(), tmp_path)
    content = review_path.read_text(encoding="utf-8")
    assert "last_name_left_span" in content.splitlines()[0]
    assert "intake.eml:L2:c11-19" in content

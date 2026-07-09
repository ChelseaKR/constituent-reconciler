"""Tests for the OCR extraction backend (extract/ocr.py, EXP-04).

Real Tesseract execution is not exercised here: the system `tesseract` binary
is not a project dependency and is not assumed to be present in CI. Instead,
`_run_tesseract` (the one function that shells out via pytesseract) is
monkeypatched with canned Tesseract `image_to_data` output, so every other
piece of the OCR path -- word/line reconstruction, span geocoding, the
confidence blend, field extraction, and the page-routing decision in
`extract_pdf_with_ocr` -- is exercised for real. A separate test covers the
"pytesseract not installed" path pytesseract-free environments actually hit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")


def _tesseract_data(
    words: list[tuple[str, int, int, int, int, int, float]],
) -> dict[str, list[Any]]:
    """Build a canned pytesseract `image_to_data(..., output_type=DICT)` result.

    Each word tuple is (text, block, par, line, left, top, conf); width/height
    are fixed per call for simplicity. A couple of non-text summary rows (conf
    -1, empty text) are mixed in, matching what Tesseract actually emits.
    """
    data: dict[str, list[Any]] = {
        "text": [],
        "conf": [],
        "block_num": [],
        "par_num": [],
        "line_num": [],
        "left": [],
        "top": [],
        "width": [],
        "height": [],
    }
    # A block-summary row: no text, conf -1. Must be filtered out, not crash.
    data["text"].append("")
    data["conf"].append(-1)
    data["block_num"].append(0)
    data["par_num"].append(0)
    data["line_num"].append(0)
    data["left"].append(0)
    data["top"].append(0)
    data["width"].append(0)
    data["height"].append(0)

    for text, block, par, line, left, top, conf in words:
        data["text"].append(text)
        data["conf"].append(conf)
        data["block_num"].append(block)
        data["par_num"].append(par)
        data["line_num"].append(line)
        data["left"].append(left)
        data["top"].append(top)
        data["width"].append(60)
        data["height"].append(20)
    return data


# ---------------------------------------------------------------------------
# _words_from_tesseract_data
# ---------------------------------------------------------------------------


def test_words_from_tesseract_data_filters_non_text_rows() -> None:
    from constituent_reconciler.extract.ocr import _words_from_tesseract_data

    data = _tesseract_data([("Alice", 1, 1, 1, 100, 200, 92.0)])
    words, text = _words_from_tesseract_data(data, scale_x=1.0, scale_y=1.0)
    assert len(words) == 1
    assert words[0]["text"] == "Alice"
    assert text == "Alice"


def test_words_from_tesseract_data_scales_coordinates() -> None:
    from constituent_reconciler.extract.ocr import _words_from_tesseract_data

    data = _tesseract_data([("Alice", 1, 1, 1, 100, 200, 92.0)])
    words, _ = _words_from_tesseract_data(data, scale_x=0.5, scale_y=0.25)
    word = words[0]
    assert word["x0"] == pytest.approx(50.0)
    assert word["top"] == pytest.approx(50.0)
    assert word["x1"] == pytest.approx(80.0)  # (100 + 60) * 0.5
    assert word["bottom"] == pytest.approx(55.0)  # (200 + 20) * 0.25


def test_words_from_tesseract_data_groups_lines_in_order() -> None:
    from constituent_reconciler.extract.ocr import _words_from_tesseract_data

    data = _tesseract_data(
        [
            ("First", 1, 1, 1, 0, 0, 90.0),
            ("Name:", 1, 1, 1, 70, 0, 90.0),
            ("Alice", 1, 1, 1, 140, 0, 90.0),
            ("Last", 1, 1, 2, 0, 20, 90.0),
            ("Name:", 1, 1, 2, 70, 20, 90.0),
            ("Walker", 1, 1, 2, 140, 20, 90.0),
        ]
    )
    _, text = _words_from_tesseract_data(data, scale_x=1.0, scale_y=1.0)
    assert text == "First Name: Alice\nLast Name: Walker"


def test_words_from_tesseract_data_drops_low_confidence_word() -> None:
    from constituent_reconciler.extract.ocr import _words_from_tesseract_data

    data = _tesseract_data([("garbled", 1, 1, 1, 0, 0, -1.0)])
    words, text = _words_from_tesseract_data(data, scale_x=1.0, scale_y=1.0)
    assert words == []
    assert text == ""


# ---------------------------------------------------------------------------
# _find_ocr_span / _ocr_confidence / _extract_ocr_fields
# ---------------------------------------------------------------------------


def test_find_ocr_span_matches_case_insensitively() -> None:
    from constituent_reconciler.extract.ocr import _find_ocr_span

    words = [{"text": "Alice", "x0": 1.0, "top": 2.0, "x1": 3.0, "bottom": 4.0}]
    span = _find_ocr_span(words, "alice", "scan.pdf", 1)
    assert span is not None
    assert span.source_file == "scan.pdf"
    assert span.page == 1
    assert span.x0 == 1.0


def test_find_ocr_span_returns_none_when_absent() -> None:
    from constituent_reconciler.extract.ocr import _find_ocr_span

    words = [{"text": "Alice", "x0": 1.0, "top": 2.0, "x1": 3.0, "bottom": 4.0}]
    assert _find_ocr_span(words, "Bob", "scan.pdf", 1) is None


def test_ocr_confidence_is_zero_for_no_words() -> None:
    from constituent_reconciler.extract.ocr import _ocr_confidence

    assert _ocr_confidence([], "") == 0.0


def test_ocr_confidence_blends_tesseract_and_plausibility() -> None:
    from constituent_reconciler.extract.ocr import _ocr_confidence

    # High Tesseract confidence but garbled, implausible "words" -- the
    # plausibility gate (not Tesseract's confidence) should win.
    garbled_words = [{"conf": 95.0} for _ in range(10)]
    garbled_text = " ".join(["XYZABCDEFGHIJKLMNOP"] * 10)  # avg len 18 > 15
    assert _ocr_confidence(garbled_words, garbled_text) < 0.5


def test_ocr_confidence_reflects_low_tesseract_confidence() -> None:
    from constituent_reconciler.extract.ocr import _ocr_confidence

    plausible_text = "First Name: Alice Last Name: Walker DOB: 1970-05-12"
    low_conf_words = [{"conf": 20.0} for _ in range(5)]
    assert _ocr_confidence(low_conf_words, plausible_text) == pytest.approx(0.2)


def test_extract_ocr_fields_finds_labeled_values_with_spans() -> None:
    from constituent_reconciler.extract.ocr import _extract_ocr_fields

    words = [
        {"text": "First", "x0": 0.0, "top": 0.0, "x1": 10.0, "bottom": 10.0},
        {"text": "Name:", "x0": 11.0, "top": 0.0, "x1": 20.0, "bottom": 10.0},
        {"text": "Alice", "x0": 21.0, "top": 0.0, "x1": 30.0, "bottom": 10.0},
    ]
    text = "First Name: Alice"
    fields = _extract_ocr_fields(words, text, confidence=0.9, source_file="scan.pdf", page_num=1)
    by_name = {f.field_name: f for f in fields}
    assert by_name["first_name"].value == "Alice"
    assert by_name["first_name"].confidence == 0.9
    assert by_name["first_name"].span is not None
    assert by_name["first_name"].span.source_file == "scan.pdf"


def test_extract_ocr_fields_skips_absent_labels() -> None:
    from constituent_reconciler.extract.ocr import _extract_ocr_fields

    # No "First Name:" label anywhere in the text, so only last_name matches.
    fields = _extract_ocr_fields([], "Last Name: Walker", 0.5, "scan.pdf", 1)
    by_name = {f.field_name: f.value for f in fields}
    assert "first_name" not in by_name
    assert by_name.get("last_name") == "Walker"


# ---------------------------------------------------------------------------
# ocr_page / extract_pdf_with_ocr / PdfplumberOcrExtractor (monkeypatched
# Tesseract call, real pdfplumber rasterization)
# ---------------------------------------------------------------------------


def _patch_tesseract(monkeypatch: pytest.MonkeyPatch, data: dict[str, list[Any]]) -> None:
    import constituent_reconciler.extract.ocr as ocr_module

    monkeypatch.setattr(ocr_module, "_run_tesseract", lambda image: data)


def test_ocr_page_extracts_fields_from_scanned_page(
    scanned_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pdfplumber as pp

    from constituent_reconciler.extract.ocr import ocr_page

    data = _tesseract_data(
        [
            ("First", 1, 1, 1, 0, 0, 91.0),
            ("Name:", 1, 1, 1, 70, 0, 91.0),
            ("Alice", 1, 1, 1, 140, 0, 91.0),
            ("Last", 1, 1, 2, 0, 30, 93.0),
            ("Name:", 1, 1, 2, 70, 30, 93.0),
            ("Walker", 1, 1, 2, 140, 30, 93.0),
        ]
    )
    _patch_tesseract(monkeypatch, data)

    with pp.open(scanned_pdf) as pdf:
        page = pdf.pages[0]
        result = ocr_page(page, "scanned-form.pdf", 1)

    by_field = {f.field_name: f.value for f in result.fields}
    assert by_field.get("first_name") == "Alice"
    assert by_field.get("last_name") == "Walker"
    assert result.confidence > 0.0
    for f in result.fields:
        assert f.span is None or f.span.source_file == "scanned-form.pdf"


def test_extract_pdf_with_ocr_routes_text_layer_page_without_ocr(
    intake_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page with a text layer must never reach `_run_tesseract`."""
    import constituent_reconciler.extract.ocr as ocr_module
    from constituent_reconciler.extract.ocr import extract_pdf_with_ocr

    def _boom(image: Any) -> dict[str, list[Any]]:
        raise AssertionError("a text-layer page must not be OCR'd")

    monkeypatch.setattr(ocr_module, "_run_tesseract", _boom)

    result = extract_pdf_with_ocr(intake_pdf)
    assert len(result.pages) == 1
    by_field = {f.field_name: f.value for f in result.pages[0].fields}
    assert by_field.get("first_name") == "Alice"
    assert by_field.get("last_name") == "Walker"


def test_extract_pdf_with_ocr_falls_back_for_scanned_page(
    scanned_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from constituent_reconciler.extract.ocr import extract_pdf_with_ocr

    data = _tesseract_data(
        [
            ("First", 1, 1, 1, 0, 0, 90.0),
            ("Name:", 1, 1, 1, 70, 0, 90.0),
            ("Alice", 1, 1, 1, 140, 0, 90.0),
        ]
    )
    _patch_tesseract(monkeypatch, data)

    result = extract_pdf_with_ocr(scanned_pdf)
    assert len(result.pages) == 1
    by_field = {f.field_name: f.value for f in result.pages[0].fields}
    assert by_field.get("first_name") == "Alice"


def test_pdfplumber_ocr_extractor_extract(
    scanned_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from constituent_reconciler.extract.base import ExtractionResult
    from constituent_reconciler.extract.ocr import PdfplumberOcrExtractor

    data = _tesseract_data([("Alice", 1, 1, 1, 0, 0, 90.0)])
    _patch_tesseract(monkeypatch, data)

    result = PdfplumberOcrExtractor().extract(scanned_pdf)
    assert isinstance(result, ExtractionResult)
    assert result.source_file == "scanned-form.pdf"


def test_run_tesseract_raises_clearly_when_pytesseract_unavailable() -> None:
    import importlib.util

    from constituent_reconciler.extract.ocr import _run_tesseract

    if importlib.util.find_spec("pytesseract") is not None:
        pytest.skip("pytesseract is installed; the unavailable-path test does not apply")
    with pytest.raises(ImportError, match="pytesseract"):
        _run_tesseract(object())


# ---------------------------------------------------------------------------
# Pipeline integration: backend="pdfplumber+ocr" selects the OCR extractor
# ---------------------------------------------------------------------------


def test_read_pdf_records_uses_ocr_backend_for_scanned_page(
    scanned_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from constituent_reconciler.config import ExtractConfig, load_recipe
    from constituent_reconciler.pipeline import read_pdf_records

    data = _tesseract_data(
        [
            ("First", 1, 1, 1, 0, 0, 90.0),
            ("Name:", 1, 1, 1, 70, 0, 90.0),
            ("Alice", 1, 1, 1, 140, 0, 90.0),
            ("Last", 1, 1, 2, 0, 30, 90.0),
            ("Name:", 1, 1, 2, 70, 30, 90.0),
            ("Walker", 1, 1, 2, 140, 30, 90.0),
        ]
    )
    _patch_tesseract(monkeypatch, data)

    recipe = load_recipe(
        Path(__file__).resolve().parents[1] / "examples" / "intake-demo" / "recipe.toml"
    )
    recipe = replace(recipe, extract=ExtractConfig(backend="pdfplumber+ocr"))

    records = read_pdf_records(scanned_pdf, "incoming", recipe=recipe, id_prefix="N")
    assert len(records) == 1
    rec = records[0]
    assert rec.raw.get("first_name") == "Alice"
    assert rec.raw.get("last_name") == "Walker"
    assert isinstance(rec.spans, dict)


def test_read_pdf_records_pdfplumber_backend_skips_scanned_page(
    scanned_pdf: Path,
) -> None:
    """Without the OCR backend, a scanned (text-layer-less) page yields nothing."""
    from dataclasses import replace

    from constituent_reconciler.config import ExtractConfig, load_recipe
    from constituent_reconciler.pipeline import read_pdf_records

    recipe = load_recipe(
        Path(__file__).resolve().parents[1] / "examples" / "intake-demo" / "recipe.toml"
    )
    recipe = replace(recipe, extract=ExtractConfig(backend="pdfplumber"))

    records = read_pdf_records(scanned_pdf, "incoming", recipe=recipe, id_prefix="N")
    assert records == []

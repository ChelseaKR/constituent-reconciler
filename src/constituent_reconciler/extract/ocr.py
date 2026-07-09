"""Local OCR backend for image-only scanned intake pages.

pdfplumber (``extract/pdf.py``) reads a PDF's embedded text layer directly and
is honest about what it cannot do: a page with no text layer at all -- a
straight scan, with no text baked in by the scanning software -- produces an
empty string, scores 0.0 confidence, and, absent a cloud seam, yields no
fields at all. Paper intake is the default reality for the target segment
(persona A1), so an image-only scan is not an edge case; it needs a local
answer that never leaves the machine.

This module rasterizes such a page with pdfplumber's own renderer and runs it
through Tesseract via ``pytesseract``, reusing the same label-adjacent field
patterns (``extract.pdf._FIELD_PATTERNS``) and the same word-count/
plausibility confidence heuristic (``extract.pdf._page_confidence``) as the
text-layer path, blended with Tesseract's own per-word confidence. A
low-confidence OCR page routes to review or the cloud seam exactly like a
low-confidence text-layer page -- there is no separate, weaker code path for
scans, and OCR does not get an easier confidence bar than a native PDF.

Both ``pytesseract`` and the system ``tesseract`` binary are optional. The
import is deferred to extraction time so the rest of the package works
without either installed; selecting the ``"pdfplumber+ocr"`` backend without
them raises a clear ``ImportError`` naming the extra and the system package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from constituent_reconciler.extract.base import ExtractedField, ExtractionResult, PageResult
from constituent_reconciler.extract.pdf import (
    _FIELD_PATTERNS,
    _page_confidence,
    extract_text_layer_page,
)
from constituent_reconciler.models import SourceSpan  # noqa: TC001

# Rasterization resolution in DPI. High enough to keep typical form print
# legible to Tesseract without ballooning runtime on multi-page scans.
_OCR_RESOLUTION = 300

# Tesseract reports per-word confidence on a 0-100 scale; -1 marks a row that
# is a block/paragraph/line summary rather than a real word.
_MIN_WORD_CONFIDENCE = 0.0


def _run_tesseract(image: Any) -> dict[str, list[Any]]:
    """Run Tesseract over a rasterized page image via pytesseract.

    Isolated in its own function so tests can substitute a canned result
    without requiring the system Tesseract binary to be installed. Raises
    ``ImportError`` if pytesseract is not installed.
    """
    try:
        import pytesseract
    except ImportError as exc:
        raise ImportError(
            "pytesseract is required for the OCR extraction backend. Install "
            "it with: pip install 'constituent-reconciler[ocr]', and install "
            "the system Tesseract binary (e.g. `brew install tesseract` or "
            "`apt-get install tesseract-ocr`)."
        ) from exc
    return dict(pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT))


def _words_from_tesseract_data(
    data: dict[str, list[Any]], scale_x: float, scale_y: float
) -> tuple[list[dict[str, Any]], str]:
    """Turn raw Tesseract word rows into word boxes in PDF point space, plus
    line-reconstructed text for the label-regex patterns to search.

    ``scale_x``/``scale_y`` convert Tesseract's pixel coordinates (from the
    rasterized image) back into the PDF's point space, so OCR spans land in
    the same coordinate system as ``extract.pdf``'s text-layer spans.
    """
    words: list[dict[str, Any]] = []
    lines: dict[tuple[int, int, int], list[str]] = {}
    texts = data.get("text", [])
    for i in range(len(texts)):
        text = str(texts[i]).strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError, KeyError, IndexError):
            conf = -1.0
        if not text or conf < _MIN_WORD_CONFIDENCE:
            continue
        key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
        lines.setdefault(key, []).append(text)
        left, top = float(data["left"][i]), float(data["top"][i])
        width, height = float(data["width"][i]), float(data["height"][i])
        words.append(
            {
                "text": text,
                "conf": conf,
                "x0": left * scale_x,
                "top": top * scale_y,
                "x1": (left + width) * scale_x,
                "bottom": (top + height) * scale_y,
            }
        )
    text_out = "\n".join(" ".join(line_words) for line_words in lines.values())
    return words, text_out


def _find_ocr_span(
    words: list[dict[str, Any]], value: str, source_file: str, page_num: int
) -> SourceSpan | None:
    """Find a value's bounding box among OCR'd words. Mirrors ``pdf._find_span``."""
    value_lower = value.lower()
    for word in words:
        if value_lower in word["text"].lower():
            return SourceSpan(
                source_file=source_file,
                page=page_num,
                x0=word["x0"],
                top=word["top"],
                x1=word["x1"],
                bottom=word["bottom"],
            )
    return None


def _ocr_confidence(words: list[dict[str, Any]], text: str) -> float:
    """Blend Tesseract's own word confidence with the shared plausibility gate.

    Tesseract can report high confidence on a page that misrecognized a
    handful of long, garbled tokens; the same average-word-length check that
    flags a garbled text-layer page (``_page_confidence``) catches that case
    here too. The lower of the two scores wins, so a scanned page never gets
    an easier confidence bar than a native PDF.
    """
    if not words:
        return 0.0
    mean_conf = float(sum(w["conf"] for w in words)) / len(words) / 100.0
    return min(mean_conf, _page_confidence(text))


def _extract_ocr_fields(
    words: list[dict[str, Any]], text: str, confidence: float, source_file: str, page_num: int
) -> list[ExtractedField]:
    fields: list[ExtractedField] = []
    for field_name, patterns in _FIELD_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(text)
            if not match:
                continue
            value = match.group(1).strip()
            if not value:
                continue
            span = _find_ocr_span(words, value, source_file, page_num)
            fields.append(
                ExtractedField(
                    field_name=field_name,
                    value=value,
                    confidence=confidence,
                    span=span,
                )
            )
            break
    return fields


def ocr_page(page: object, source_file: str, page_num: int) -> PageResult:
    """OCR one pdfplumber page with no text layer into a ``PageResult``.

    Rasterizes the page at ``_OCR_RESOLUTION`` DPI, runs Tesseract, and
    applies the same field patterns and confidence gate as the text-layer
    path. Raises ``ImportError`` if pytesseract is not installed.
    """
    page_image = page.to_image(resolution=_OCR_RESOLUTION)  # type: ignore[attr-defined]
    image = page_image.original
    data = _run_tesseract(image)

    page_width = float(page.width)  # type: ignore[attr-defined]
    page_height = float(page.height)  # type: ignore[attr-defined]
    scale_x = page_width / image.width
    scale_y = page_height / image.height

    words, text = _words_from_tesseract_data(data, scale_x, scale_y)
    confidence = _ocr_confidence(words, text)
    fields = _extract_ocr_fields(words, text, confidence, source_file, page_num)
    return PageResult(page_num=page_num, fields=fields, confidence=confidence)


def extract_pdf_with_ocr(path: Path) -> ExtractionResult:
    """Extract fields from a PDF, OCR-ing any page with no embedded text layer.

    Pages with a text layer use the existing pdfplumber label-regex path
    unchanged. Pages with no text layer at all are rasterized and OCR'd, so an
    image-only scanned page contributes fields instead of an empty record.

    Raises ``ImportError`` if pdfplumber is not installed. A page requiring
    OCR raises ``ImportError`` at that point if pytesseract is not installed
    (text-only PDFs never touch it, so they keep working without the extra).
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError(
            "pdfplumber is required for PDF extraction. "
            "Install it with: pip install 'constituent-reconciler[extract]'"
        ) from exc

    result = ExtractionResult(source_file=path.name)
    with pdfplumber.open(path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            if text.strip():
                result.pages.append(extract_text_layer_page(page, path.name, page_num, text=text))
            else:
                result.pages.append(ocr_page(page, path.name, page_num))

    return result


class PdfplumberOcrExtractor:
    """PDF extractor that falls back to Tesseract OCR for image-only pages.

    Selected by ``[extract] backend = "pdfplumber+ocr"`` in a recipe. Requires
    the optional ``ocr`` extra (``pytesseract``) plus a system Tesseract
    install for pages that actually need OCR; a PDF whose pages all carry a
    text layer runs identically to ``PdfplumberExtractor`` and never needs
    either.
    """

    def extract(self, path: Path) -> ExtractionResult:
        return extract_pdf_with_ocr(path)

"""Offline PDF extraction using pdfplumber.

Extracts canonical constituent fields from a PDF using label-adjacent regex
patterns. This is a heuristic for form-like intake PDFs; complex layouts with
no labels (e.g., dense scanned tables) produce low confidence and are flagged
as candidates for the optional cloud seam.

pdfplumber is an optional dependency. The import is deferred so the rest of the
package works without it installed.
"""

from __future__ import annotations

from pathlib import Path

from constituent_reconciler.extract.base import (
    FIELD_PATTERNS,
    ExtractedField,
    ExtractionResult,
    PageResult,
    page_confidence,
)
from constituent_reconciler.models import SourceSpan  # noqa: TC001

# Backward-compatible aliases: the patterns and the confidence heuristic moved
# to extract.base so the text extractor applies identical rules.
_FIELD_PATTERNS = FIELD_PATTERNS
_page_confidence = page_confidence


def _find_span(page: object, value: str, source_file: str, page_num: int) -> SourceSpan | None:
    """Find a value's bounding box in the page word list.

    Returns None on any error or if the value is not found. Callers treat a
    missing span as informational: the record is still valid without it.
    """
    try:
        words = page.extract_words()  # type: ignore[attr-defined]
    except Exception:
        return None
    value_lower = value.lower()
    for word in words:
        if value_lower in str(word.get("text", "")).lower():
            return SourceSpan(
                source_file=source_file,
                page=page_num,
                x0=float(word["x0"]),
                top=float(word["top"]),
                x1=float(word["x1"]),
                bottom=float(word["bottom"]),
            )
    return None


def extract_text_layer_page(
    page: object, path_name: str, page_num: int, text: str | None = None
) -> PageResult:
    """Extract fields from one page's embedded text layer via label regexes.

    ``text`` may be passed in when the caller already extracted it (the OCR
    backend uses this to decide whether a page needs OCR at all, without
    calling ``extract_text`` twice). If omitted, it is read from ``page``.
    """
    if text is None:
        text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""  # type: ignore[attr-defined]
    confidence = _page_confidence(text)
    page_result = PageResult(page_num=page_num, confidence=confidence)

    for field_name, patterns in _FIELD_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                value = match.group(1).strip().rstrip()
                if not value:
                    continue
                span = _find_span(page, value, path_name, page_num)
                page_result.fields.append(
                    ExtractedField(
                        field_name=field_name,
                        value=value,
                        confidence=confidence,
                        span=span,
                    )
                )
                break

    return page_result


def extract_pdf(path: Path) -> ExtractionResult:
    """Extract constituent fields from a PDF using pdfplumber.

    Raises ``ImportError`` if pdfplumber is not installed.
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
            result.pages.append(extract_text_layer_page(page, path.name, page_num))

    return result


class PdfplumberExtractor:
    """Offline PDF extractor using pdfplumber (the default extraction backend)."""

    def extract(self, path: Path) -> ExtractionResult:
        return extract_pdf(path)

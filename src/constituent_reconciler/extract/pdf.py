"""Offline PDF extraction using pdfplumber.

Extracts canonical constituent fields from a PDF using label-adjacent regex
patterns. This is a heuristic for form-like intake PDFs; complex layouts with
no labels (e.g., dense scanned tables) produce low confidence and are flagged
as candidates for the optional cloud seam.

pdfplumber is an optional dependency. The import is deferred so the rest of the
package works without it installed.
"""

from __future__ import annotations

import re
from pathlib import Path

from constituent_reconciler.extract.base import ExtractedField, ExtractionResult, PageResult
from constituent_reconciler.models import SourceSpan  # noqa: TC001

# Ordered patterns per field: first match wins. `[^\n]+` captures up to the next
# newline so that multi-field forms don't bleed across label-value pairs.
_FIELD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "first_name": [
        re.compile(r"(?i)first\s+name\s*[:\-]\s*([^\n]+)"),
        re.compile(r"(?i)given\s+name\s*[:\-]\s*([^\n]+)"),
    ],
    "last_name": [
        re.compile(r"(?i)last\s+name\s*[:\-]\s*([^\n]+)"),
        re.compile(r"(?i)surname\s*[:\-]\s*([^\n]+)"),
    ],
    "dob": [
        re.compile(
            r"(?i)(?:date\s+of\s+birth|dob|birth\s+date)\s*[:\-]\s*"
            r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-\.]\d{2}[/\-\.]\d{2})"
        ),
    ],
    "email": [
        re.compile(r"(?i)e-?mail\s*[:\-]\s*([\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,})"),
        re.compile(r"([\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,})"),
    ],
    "phone": [
        re.compile(r"(?i)(?:phone|tel)\s*[:\-]\s*([\d\s\-\.\(\)]{7,})"),
        re.compile(r"(\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4})"),
    ],
}

# Page confidence heuristics. A page with fewer than _MIN_WORDS words is
# probably near-empty (a cover sheet, a blank page, or a header-only scan).
# A page where the average word length exceeds _GARBLED_AVG_WORD_LEN characters
# is probably garbled OCR output. Both score below 0.5.
_MIN_WORDS = 5
_GARBLED_AVG_WORD_LEN = 15


def _page_confidence(text: str) -> float:
    """Heuristic confidence for a page based on word count and plausibility.

    Returns a score in [0, 1]. Near-empty pages and pages with very long
    "words" (garbled OCR) score below 0.5, flagging them as low-confidence
    candidates for the optional cloud seam.
    """
    stripped = text.strip()
    if not stripped:
        return 0.0
    words = stripped.split()
    if not words:
        return 0.0
    avg_word_len = sum(len(w) for w in words) / len(words)
    if avg_word_len > _GARBLED_AVG_WORD_LEN:
        return 0.2
    if len(words) < _MIN_WORDS:
        return len(words) / _MIN_WORDS * 0.5
    return 1.0


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

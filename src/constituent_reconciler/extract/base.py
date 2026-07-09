"""Extraction base types, protocols, and shared heuristics.

A document extractor turns a file (PDF, text, email, etc.) into a list of
``ExtractedField`` values, each carrying the extracted value, a confidence
score in [0, 1], and an optional span pointing back to where in the document
the value came from (a ``SourceSpan`` bounding box for PDFs, a line-offset
``TextSpan`` for text bodies). Low-confidence pages are candidates for the
optional cloud seam.

The label-adjacent field patterns and the page-confidence heuristic live here
so the PDF and text extractors apply identical rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from constituent_reconciler.models import SourceSpan, TextSpan

# Ordered patterns per field: first match wins. `[^\n]+` captures up to the next
# newline so that multi-field forms don't bleed across label-value pairs.
FIELD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
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


def page_confidence(text: str) -> float:
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


@dataclass(frozen=True)
class ExtractedField:
    """One field pulled from a document, with a confidence score and location."""

    field_name: str
    value: str
    confidence: float
    span: SourceSpan | TextSpan | None = None


@dataclass
class PageResult:
    """Fields extracted from one page of a document."""

    page_num: int
    fields: list[ExtractedField] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class ExtractionResult:
    """All pages extracted from one document.

    ``note`` is set when extraction did not run to completion (for example the
    sandbox killed a hung parse); it explains why the result is fail-closed.
    """

    source_file: str
    pages: list[PageResult] = field(default_factory=list)
    note: str | None = None

    def low_confidence_pages(self, threshold: float) -> list[PageResult]:
        return [p for p in self.pages if p.confidence < threshold]


@runtime_checkable
class Extractor(Protocol):
    def extract(self, path: Path) -> ExtractionResult: ...


@runtime_checkable
class CloudSeam(Protocol):
    def is_enabled(self) -> bool: ...

    def refine(self, path: Path, page_num: int) -> list[ExtractedField]: ...

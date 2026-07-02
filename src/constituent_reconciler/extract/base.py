"""Extraction base types and protocols.

A document extractor turns a file (PDF, image, etc.) into a list of
``ExtractedField`` values, each carrying the extracted value, a confidence
score in [0, 1], and an optional ``SourceSpan`` pointing back to where in the
document the value came from. Low-confidence pages are candidates for the
optional cloud seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from constituent_reconciler.models import SourceSpan


@dataclass(frozen=True)
class ExtractedField:
    """One field pulled from a document, with a confidence score and location."""

    field_name: str
    value: str
    confidence: float
    span: SourceSpan | None = None


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

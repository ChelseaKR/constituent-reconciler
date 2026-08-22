"""Read the real source-document text behind one record's field.

This is the grounding source ``ocr_propose.propose_correction`` quotes
against. It never calls a model and never guesses: a record with no span
for the requested field (a CSV-sourced record, or a field the extractor
never located) returns ``None``, which every caller must treat as "no
source text available to propose a correction from," not as license to
fall back to something else.
"""

from __future__ import annotations

from pathlib import Path

from constituent_reconciler.models import Record, SourceSpan, TextSpan


def for_field(record: Record, field: str) -> str | None:
    """The plain text of the source document a field's span points into.

    A ``TextSpan`` (plain text or ``.eml`` intake) reads its source file
    directly. A ``SourceSpan`` (PDF intake) reads the PDF page's embedded
    text layer via the same helper ``extract/seam.py``'s local model seam
    already uses, so a garbled OCR page and a well-formed digital PDF page
    are read identically either way.
    """
    span = record.spans.get(field)
    if span is None:
        return None
    if isinstance(span, TextSpan):
        try:
            return Path(span.source_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    if isinstance(span, SourceSpan):
        from constituent_reconciler.extract.seam import (
            _page_text,  # lazy: pdfplumber is an optional dependency
        )

        try:
            return _page_text(Path(span.source_file), span.page)
        except (RuntimeError, OSError):
            return None
    return None  # pragma: no cover - Record.spans only ever holds the two types above

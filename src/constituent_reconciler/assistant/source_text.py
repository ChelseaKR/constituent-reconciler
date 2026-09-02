"""Read the real source-document text behind one record's field.

This is the grounding source ``ocr_propose.propose_correction`` quotes
against. It never calls a model and never guesses: a record with no span
for the requested field (a CSV-sourced record, or a field the extractor
never located) returns ``None``, which every caller must treat as "no
source text available to propose a correction from," not as license to
fall back to something else.

A span records only the document's bare filename, never a path. Every
extractor builds it from ``path.name``, which keeps the operator's
directory layout out of a value that reaches review screens and cache
entries. Resolving that name back to a file therefore needs the
directories the run actually read from, which is what ``document_roots``
derives from the recipe and what ``for_field`` requires as ``roots``. The
parameter has no default on purpose. Reading the bare name relative to the
process working directory, which is what this module did before, is wrong
in both directions: from any directory but the intake one every field
reports "no source text" and the command writes an empty draft, and from a
directory that happens to hold a same-named file the grounding text comes
from an unrelated document.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from constituent_reconciler.assistant.errors import SourceDocumentUnavailable
from constituent_reconciler.config import Recipe
from constituent_reconciler.models import Record, SourceSpan, TextSpan


def document_roots(recipe: Recipe) -> tuple[Path, ...]:
    """The directories whose documents a span's filename may name.

    One per configured source, in the order the pipeline reads them. A
    source that is a directory is itself a root; a source that is a single
    file contributes the directory holding it, because that is where a
    sibling document of the same run would sit. Duplicates are dropped, so
    a recipe pointing both sources at one directory does not make every
    filename look ambiguous.
    """

    roots: list[Path] = []
    for source in (recipe.existing, recipe.incoming):
        if source is None:
            continue
        root = source if source.is_dir() else source.parent
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _resolve(source_file: str, roots: Sequence[Path]) -> Path:
    """The one file ``source_file`` names, or a refusal saying why there is none.

    An absolute span is taken at its word. Nothing in the package writes
    one, but a caller holding a full path has already answered the question
    this function exists to answer. A relative name carrying a directory
    component is refused rather than joined onto a root: no extractor
    produces one, so it can only have come from outside the conventions the
    span format sets.

    A name found under more than one root is refused rather than resolved
    to the first hit. The span does not record which source it was read
    from, so choosing would be a guess, and a wrong guess here grounds a
    quote in another person's document.
    """

    candidate = Path(source_file)
    if candidate.is_absolute():
        return candidate
    if candidate.name != source_file:
        raise SourceDocumentUnavailable(
            f"source span names {source_file!r}, which carries a directory component; "
            "spans record a bare filename"
        )
    matches = [root / candidate for root in roots if (root / candidate).is_file()]
    if not matches:
        searched = ", ".join(str(root) for root in roots) or "no configured source directory"
        raise SourceDocumentUnavailable(
            f"source document {source_file!r} was not found under {searched}"
        )
    if len(matches) > 1:
        found = ", ".join(str(match) for match in matches)
        raise SourceDocumentUnavailable(
            f"source document {source_file!r} exists in more than one source directory "
            f"({found}); the span does not record which one it was read from"
        )
    return matches[0]


def for_field(record: Record, field: str, *, roots: Sequence[Path]) -> str | None:
    """The plain text of the source document a field's span points into.

    ``roots`` are the directories the run read documents from, normally
    ``document_roots(recipe)``. Returns ``None`` only when the field has no
    span at all. Every other failure raises
    :class:`~constituent_reconciler.assistant.errors.SourceDocumentUnavailable`,
    so a run whose grounding evidence is missing stops and says so instead
    of producing an empty draft and exiting 0.

    A ``TextSpan`` (plain text or ``.eml`` intake) reads its source file
    directly. A ``SourceSpan`` (PDF intake) reads the PDF page's embedded
    text layer via the same helper ``extract/seam.py``'s local model seam
    already uses, so a garbled OCR page and a well-formed digital PDF page
    are read identically either way. An image-only page with no text layer
    still comes back as the empty string rather than an error: the page was
    read, it simply holds nothing to quote, and the quote check downstream
    turns that into an abstention. A span naming a page the document does
    not have raises: ``pdfplumber`` reports it as an ``IndexError``, which
    the old code did not catch at all and which reached the operator as an
    unhandled traceback rather than as a stated reason.
    """
    span = record.spans.get(field)
    if span is None:
        return None
    path = _resolve(span.source_file, roots)
    if isinstance(span, TextSpan):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SourceDocumentUnavailable(
                f"could not read source document {path}: {exc}"
            ) from exc
    if isinstance(span, SourceSpan):
        from constituent_reconciler.extract.seam import (
            _page_text,  # lazy: pdfplumber is an optional dependency
        )

        try:
            return _page_text(path, span.page)
        except (RuntimeError, OSError, IndexError) as exc:
            raise SourceDocumentUnavailable(
                f"could not read page {span.page} of source document {path}: {exc}"
            ) from exc
    return None  # pragma: no cover - Record.spans only ever holds the two types above

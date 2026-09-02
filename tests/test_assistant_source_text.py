"""Tests for reading the real source-document text behind one field's span.

The unit tests below build their spans by hand. That is worth naming,
because the earlier version of this file did the same and pinned nothing:
every span it wrote carried an absolute path, a shape no extractor in the
package produces. ``extract/text.py``, ``extract/pdf.py``, ``extract/ocr.py``
and ``extract/sandbox.py`` each build the span from ``path.name``, so a real
span always holds a bare filename. Resolving a bare filename was the part
that was broken, and no test here could reach it.

``tests/test_cli_ai_propose_grounding.py`` covers that half end to end, by
running the real command over spans the real extractor produced. What stays
here is the resolution contract itself: which shapes are read, which are
refused, and which return ``None``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from constituent_reconciler.assistant.errors import SourceDocumentUnavailable
from constituent_reconciler.assistant.source_text import document_roots, for_field
from constituent_reconciler.config import load_recipe
from constituent_reconciler.models import Record, SourceSpan, TextSpan
from constituent_reconciler.testing import make_pdf


def _record_with(span: SourceSpan | TextSpan) -> Record:
    return Record(
        unique_id="r1",
        source="test",
        raw={"first_name": "Maria"},
        spans={"first_name": span},
    )


def test_a_bare_filename_is_read_from_the_source_root(tmp_path: Path) -> None:
    """The shape every extractor actually produces: a name, resolved by root."""

    (tmp_path / "intake.txt").write_text("First Name: Maria\n", encoding="utf-8")
    record = _record_with(TextSpan(source_file="intake.txt", line=1, col_start=12, col_end=17))
    text = for_field(record, "first_name", roots=(tmp_path,))
    assert text is not None
    assert "Maria" in text


def test_a_bare_filename_is_not_read_from_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namesake where the process happens to stand is not a source document."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "intake.txt").write_text("First Name: Someone Else\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    record = _record_with(TextSpan(source_file="intake.txt", line=1, col_start=12, col_end=17))
    with pytest.raises(SourceDocumentUnavailable, match="was not found under"):
        for_field(record, "first_name", roots=(tmp_path / "sources",))


def test_an_absolute_span_is_taken_at_its_word(tmp_path: Path) -> None:
    source_path = tmp_path / "intake.txt"
    source_path.write_text("First Name: Maria\n", encoding="utf-8")
    record = _record_with(TextSpan(source_file=str(source_path), line=1, col_start=12, col_end=17))
    text = for_field(record, "first_name", roots=())
    assert text is not None
    assert "Maria" in text


def test_a_missing_document_is_raised_not_reported_as_no_source_text(tmp_path: Path) -> None:
    """The distinction the caller depends on to fail loudly rather than skip."""

    record = _record_with(TextSpan(source_file="missing.txt", line=1, col_start=0, col_end=5))
    with pytest.raises(SourceDocumentUnavailable, match="missing.txt"):
        for_field(record, "first_name", roots=(tmp_path,))


def test_a_span_carrying_a_directory_component_is_refused(tmp_path: Path) -> None:
    """No extractor writes one, so it is refused rather than joined onto a root."""

    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "intake.txt").write_text("First Name: Maria\n", encoding="utf-8")
    record = _record_with(TextSpan(source_file="sub/intake.txt", line=1, col_start=12, col_end=17))
    with pytest.raises(SourceDocumentUnavailable, match="directory component"):
        for_field(record, "first_name", roots=(tmp_path,))


def test_a_filename_in_two_roots_is_refused_rather_than_resolved_to_the_first(
    tmp_path: Path,
) -> None:
    """The span does not say which source it came from, so neither does this."""

    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "intake.txt").write_text("First Name: Maria\n", encoding="utf-8")
    (second / "intake.txt").write_text("First Name: Someone Else\n", encoding="utf-8")
    record = _record_with(TextSpan(source_file="intake.txt", line=1, col_start=12, col_end=17))
    with pytest.raises(SourceDocumentUnavailable, match="more than one source directory"):
        for_field(record, "first_name", roots=(first, second))


def test_field_with_no_span_returns_none() -> None:
    """A CSV-sourced record has nothing to quote, and that is not an error."""

    record = Record(unique_id="r1", source="test", raw={"first_name": "Maria"})
    assert for_field(record, "first_name", roots=()) is None


def test_source_span_reads_the_pdf_page_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "intake.pdf"
    pdf_path.write_bytes(make_pdf(["First Name: Maria", "Last Name: Garcia"]))
    record = _record_with(
        SourceSpan(source_file="intake.pdf", page=1, x0=0, top=0, x1=100, bottom=20)
    )
    text = for_field(record, "first_name", roots=(tmp_path,))
    assert text is not None
    assert "Maria" in text


def test_an_unreadable_pdf_page_is_raised_not_reported_as_no_source_text(tmp_path: Path) -> None:
    """A span pointing past the document's last page is a broken run, not a skip."""

    pdf_path = tmp_path / "intake.pdf"
    pdf_path.write_bytes(make_pdf(["First Name: Maria"]))
    record = _record_with(
        SourceSpan(source_file="intake.pdf", page=99, x0=0, top=0, x1=100, bottom=20)
    )
    with pytest.raises(SourceDocumentUnavailable, match="could not read page 99"):
        for_field(record, "first_name", roots=(tmp_path,))


def _recipe(root: Path, *, existing: str, incoming: str) -> Path:
    path = root / "recipe.toml"
    path.write_text(
        textwrap.dedent(f"""\
            [input]
            existing = "{existing}"
            incoming = "{incoming}"
            id_column = "id"

            [mapping]
            first_name = "First Name"
            last_name = "Last Name"
            """),
        encoding="utf-8",
    )
    return path


def test_document_roots_uses_a_directory_source_and_a_file_source_parent(tmp_path: Path) -> None:
    (tmp_path / "incoming").mkdir()
    (tmp_path / "roster.csv").write_text("id,First Name,Last Name\n", encoding="utf-8")
    recipe = load_recipe(str(_recipe(tmp_path, existing="roster.csv", incoming="incoming")))
    assert document_roots(recipe) == (tmp_path, tmp_path / "incoming")


def test_document_roots_reports_one_root_when_both_sources_share_a_directory(
    tmp_path: Path,
) -> None:
    """Otherwise every filename in a single-directory run would look ambiguous."""

    (tmp_path / "roster.csv").write_text("id,First Name,Last Name\n", encoding="utf-8")
    (tmp_path / "intake.txt").write_text("First Name: Maria\n", encoding="utf-8")
    recipe = load_recipe(str(_recipe(tmp_path, existing="roster.csv", incoming="intake.txt")))
    assert document_roots(recipe) == (tmp_path,)

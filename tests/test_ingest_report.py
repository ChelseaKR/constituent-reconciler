"""Tests for the ingest accounting: every row, page, and file answered for.

The run report must let an operator reconcile a reporting cycle: every path
ingestion saw is either read or skipped with a reason, every PDF page is
counted as extracted or dropped, and a nonempty value that normalized to ""
(no evidence) is counted per field and source instead of vanishing silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.config import ExtractConfig, Recipe
from constituent_reconciler.models import IngestReport, Record, RunResult
from constituent_reconciler.normalize import normalize_record
from constituent_reconciler.report import render_run_summary
from tests.conftest import _make_pdf


def _recipe(incoming: Path, *, backend: str = "none") -> Recipe:
    return Recipe(
        incoming=incoming,
        mapping={"first_name": "first", "last_name": "last", "dob": "dob"},
        fields=("first_name", "last_name", "dob"),
        extract=ExtractConfig(backend=backend),
    )


@pytest.fixture()
def mixed_folder(tmp_path: Path) -> Path:
    """A folder with a CSV, a good PDF, a no-name PDF, and an unsupported file."""

    pytest.importorskip("pdfplumber", reason="pdfplumber not installed")
    folder = tmp_path / "incoming-docs"
    folder.mkdir()
    (folder / "batch.csv").write_text("first,last,dob\nBob,Smith,1985-07-04\n", encoding="utf-8")
    (folder / "form.pdf").write_bytes(
        _make_pdf(
            [
                "Intake Form",
                "First Name: Alice",
                "Last Name: Walker",
                "DOB: 1970-05-12",
            ]
        )
    )
    # A page that yields no name is dropped by read_pdf_records.
    (folder / "blank.pdf").write_bytes(_make_pdf(["Hi"]))
    (folder / "notes.docx").write_text("not an ingestible source", encoding="utf-8")
    return folder


def test_mixed_folder_every_file_answered_for(mixed_folder: Path) -> None:
    result = pipeline.run(_recipe(mixed_folder, backend="pdfplumber"))
    ingest = result.ingest

    seen = {str(child) for child in mixed_folder.iterdir()}
    accounted = set(ingest.files_read) | {s.path for s in ingest.files_skipped}
    assert accounted == seen
    assert len(ingest.files_read) + len(ingest.files_skipped) == len(seen)

    assert str(mixed_folder / "batch.csv") in ingest.files_read
    assert str(mixed_folder / "form.pdf") in ingest.files_read
    assert str(mixed_folder / "blank.pdf") in ingest.files_read
    skipped = {s.path: s.reason for s in ingest.files_skipped}
    assert skipped == {str(mixed_folder / "notes.docx"): "unsupported extension: .docx"}


def test_pdf_pages_counted_as_extracted_or_dropped(mixed_folder: Path) -> None:
    result = pipeline.run(_recipe(mixed_folder, backend="pdfplumber"))
    # form.pdf yields Alice Walker; blank.pdf's only page has no name.
    assert result.ingest.pages_extracted == 1
    assert result.ingest.pages_dropped == 1


def test_pdf_skipped_with_reason_when_extraction_disabled(tmp_path: Path) -> None:
    folder = tmp_path / "incoming-docs"
    folder.mkdir()
    (folder / "batch.csv").write_text(
        "first,last,dob\nBob,Smith,1985-07-04\nWei,Chen,1968-01-22\n", encoding="utf-8"
    )
    (folder / "form.pdf").write_bytes(b"%PDF-1.4 stub")

    result = pipeline.run(_recipe(folder, backend="none"))
    skipped = {s.path: s.reason for s in result.ingest.files_skipped}
    assert skipped == {
        str(folder / "form.pdf"): 'pdf extraction disabled (extract.backend = "none")'
    }
    assert result.ingest.pages_extracted == 0
    assert result.ingest.pages_dropped == 0


def test_unparseable_dob_counts_as_failure_for_its_source(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.csv"
    incoming.write_text(
        "first,last,dob\nBob,Smith,not-a-date\nWei,Chen,1968-01-22\n", encoding="utf-8"
    )
    result = pipeline.run(_recipe(incoming))
    assert result.ingest.normalization_failures == {"dob": {"incoming": 1}}
    assert result.ingest.files_read == (str(incoming),)
    assert result.ingest.files_skipped == ()


def test_normalize_record_reports_failures_into_caller_mapping() -> None:
    failures: dict[str, dict[str, int]] = {}
    record = Record(
        unique_id="N0001",
        source="incoming",
        raw={"first_name": "Alice", "last_name": "Walker", "dob": "sometime in June"},
    )
    normalized = normalize_record(record, ("first_name", "last_name", "dob"), failures=failures)
    assert normalized.normalized["dob"] == ""
    assert failures == {"dob": {"incoming": 1}}


def test_empty_raw_value_is_no_evidence_not_a_failure() -> None:
    failures: dict[str, dict[str, int]] = {}
    record = Record(
        unique_id="N0001",
        source="incoming",
        raw={"first_name": "Alice", "last_name": "Walker", "dob": ""},
    )
    normalize_record(record, ("first_name", "last_name", "dob"), failures=failures)
    assert failures == {}


def test_run_summary_renders_ingest_section(tmp_path: Path) -> None:
    folder = tmp_path / "incoming-docs"
    folder.mkdir()
    (folder / "batch.csv").write_text(
        "first,last,dob\nBob,Smith,not-a-date\nWei,Chen,1968-01-22\n", encoding="utf-8"
    )
    (folder / "notes.docx").write_text("x", encoding="utf-8")

    result = pipeline.run(_recipe(folder))
    summary = render_run_summary(result)
    assert "ingest:" in summary
    assert str(folder / "batch.csv") in summary
    assert "unsupported extension: .docx" in summary
    assert "dob: incoming: 1" in summary


def test_hand_built_result_has_empty_ingest_and_unchanged_summary() -> None:
    result = RunResult(records={}, pairs=(), clusters=(), golden=())
    assert result.ingest == IngestReport()
    assert "ingest:" not in render_run_summary(result)


def test_cli_run_writes_machine_readable_run_report(tmp_path: Path) -> None:
    from constituent_reconciler.cli import main

    folder = tmp_path / "incoming"
    folder.mkdir()
    (folder / "batch.csv").write_text(
        "first,last,dob\nBob,Smith,not-a-date\nWei,Chen,1968-01-22\n", encoding="utf-8"
    )
    (folder / "notes.docx").write_text("x", encoding="utf-8")
    recipe_path = tmp_path / "recipe.toml"
    recipe_path.write_text(
        '[input]\nincoming = "incoming"\n\n'
        '[mapping]\nfirst_name = "first"\nlast_name = "last"\ndob = "dob"\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    exit_code = main(["run", "--config", str(recipe_path), "--out", str(out_dir)])
    assert exit_code == 0

    payload = json.loads((out_dir / "run_report.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    ingest = payload["ingest"]
    assert ingest["files_read"] == [str(folder / "batch.csv")]
    assert ingest["files_skipped"] == [
        {"path": str(folder / "notes.docx"), "reason": "unsupported extension: .docx"}
    ]
    assert ingest["pages_extracted"] == 0
    assert ingest["pages_dropped"] == 0
    assert ingest["normalization_failures"] == {"dob": {"incoming": 1}}

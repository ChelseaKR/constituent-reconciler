"""Tests for the corpus generator's PDF intake-document variant (issue #78).

Two things need holding to account. The writer in `tools/corpusgen/pdfwrite.py`
must be deterministic and must produce a text layer the repository's own
extractor reads back exactly, non-ASCII names included, or the PDF-carried
half of the corpus would quietly under-represent the name classes the R5
audit measures. The generator wiring must account for every incoming row
exactly once across the CSV and the PDF documents, and must leave the
CSV-only layout byte-identical so the committed 50k baseline stays valid.

Kept smoke-sized on purpose, the way `test_corpusgen.py` is: a few hundred
records and a handful of pages, well under the 10^3-10^5 range the tool
targets in real use.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from tools.corpusgen.generate import PDF_PAGES_PER_DOC, generate, write_corpus
from tools.corpusgen.pdfwrite import render_pdf, write_intake_pdf

from constituent_reconciler import pipeline
from constituent_reconciler.config import load_recipe
from constituent_reconciler.extract.base import FIELD_PATTERNS
from constituent_reconciler.extract.pdf import PdfplumberExtractor

_SEED = 20260707
_RECORDS = 400
_SHARE = 0.2

# One page per entry, each a labeled intake form the extractor's field
# patterns recognize. The second page carries names outside latin-1 and a
# form with no email or phone line, which is what a partly filled intake
# form looks like.
_PAGES = [
    [
        "Constituent Intake Form",
        "Record: N000001",
        "First Name: Alexandra",
        "Last Name: Whitfield",
        "Date of Birth: 1984-03-05",
        "Email: alexandra.whitfield.1@example.org",
        "Phone: (212) 555-0142",
        "Address: 12 North Oak Avenue Apartment 3, Rivertown, OH 43275",
        "Consent: granted",
    ],
    [
        "Constituent Intake Form",
        "Record: N000002",
        "First Name: Nguyễn",
        "Last Name: Zdeněk-Ødegård",
        "Date of Birth: 1990-12-01",
        "Address: 9 East Pine Street, Millbrook, PA 17227",
        "Consent: granted",
    ],
]


# --- the writer -----------------------------------------------------------


def test_render_pdf_is_byte_identical_across_calls() -> None:
    """No timestamps, no ids, no compression: the same input, the same bytes."""

    assert render_pdf(_PAGES) == render_pdf(_PAGES)


def test_render_pdf_differs_when_the_text_differs() -> None:
    changed = [list(_PAGES[0]), list(_PAGES[1])]
    changed[0][2] = "First Name: Alexandria"
    assert render_pdf(changed) != render_pdf(_PAGES)


def test_render_pdf_refuses_an_empty_document() -> None:
    with pytest.raises(ValueError, match="at least one page"):
        render_pdf([])


def test_render_pdf_refuses_more_codes_than_a_single_byte_holds() -> None:
    """Fail loudly rather than mangle text the encoding cannot represent."""

    crowded = ["".join(chr(0x4E00 + i) for i in range(200))]
    with pytest.raises(ValueError, match="non-ASCII characters"):
        render_pdf([crowded])


def test_the_repos_extractor_reads_back_what_the_writer_wrote(tmp_path: Path) -> None:
    """The round trip that makes the PDF corpus honest input, not a prop.

    Every labeled value the writer put on a page comes back out through
    `extract/pdf.py`, the same extractor the pipeline runs, including names
    outside latin-1 that `constituent_reconciler.testing.make_pdf` cannot
    encode at all.
    """

    path = tmp_path / "intake.pdf"
    write_intake_pdf(path, _PAGES)
    extraction = PdfplumberExtractor().extract(path)

    assert len(extraction.pages) == 2
    first = {field.field_name: field.value for field in extraction.pages[0].fields}
    assert first["first_name"] == "Alexandra"
    assert first["last_name"] == "Whitfield"
    assert first["dob"] == "1984-03-05"
    assert first["email"] == "alexandra.whitfield.1@example.org"
    assert first["phone"] == "(212) 555-0142"

    second = {field.field_name: field.value for field in extraction.pages[1].fields}
    assert second["first_name"] == "Nguyễn"
    assert second["last_name"] == "Zdeněk-Ødegård"
    assert second["dob"] == "1990-12-01"
    assert "email" not in second and "phone" not in second

    for page in extraction.pages:
        assert page.confidence >= 0.5  # a filled intake page is not low-confidence


# --- the generator wiring -------------------------------------------------


@pytest.fixture(scope="module")
def mixed_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One seeded mixed CSV+PDF corpus, shared by the wiring tests."""

    out_dir = tmp_path_factory.mktemp("mixed") / "corpus"
    corpus = generate(total_records=_RECORDS, seed=_SEED)
    write_corpus(corpus, out_dir, seed=_SEED, total_records=_RECORDS, pdf_share=_SHARE)
    return out_dir


def test_mixed_layout_writes_pdfs_beside_the_remaining_csv(mixed_corpus: Path) -> None:
    assert (mixed_corpus / "existing.csv").is_file()
    assert (mixed_corpus / "incoming" / "incoming.csv").is_file()
    assert not (mixed_corpus / "incoming.csv").exists()
    documents = sorted((mixed_corpus / "incoming").glob("intake-*.pdf"))
    assert documents
    assert all(path.read_bytes().startswith(b"%PDF-1.4") for path in documents)

    recipe_text = (mixed_corpus / "recipe.toml").read_text(encoding="utf-8")
    assert 'incoming = "incoming"' in recipe_text
    assert 'backend = "pdfplumber"' in recipe_text
    assert f"--pdf-share {_SHARE}" in recipe_text


def test_every_incoming_row_is_carried_exactly_once(mixed_corpus: Path) -> None:
    """Accounting: the CSV rows and the manifest's rows partition the side."""

    corpus = generate(total_records=_RECORDS, seed=_SEED)
    expected = [row["id"] for row in corpus.incoming_rows]

    with (mixed_corpus / "incoming" / "incoming.csv").open(newline="", encoding="utf-8") as handle:
        csv_ids = [row["id"] for row in csv.DictReader(handle)]
    manifest = json.loads((mixed_corpus / "pdf_manifest.json").read_text(encoding="utf-8"))
    pdf_ids = [rid for document in manifest["documents"] for rid in document["incoming_ids"]]

    assert sorted(csv_ids + pdf_ids) == sorted(expected)
    assert len(csv_ids + pdf_ids) == len(expected)  # no row written twice
    assert pdf_ids  # the share actually moved rows off the CSV
    assert manifest["pdf_share"] == _SHARE
    assert manifest["pages_per_document"] == PDF_PAGES_PER_DOC
    assert all(
        len(document["incoming_ids"]) <= PDF_PAGES_PER_DOC for document in manifest["documents"]
    )


def test_regenerating_the_mixed_corpus_is_byte_identical(tmp_path: Path) -> None:
    """Determinism over the whole layout, PDF bytes included."""

    digests = []
    for name in ("first", "second"):
        out_dir = tmp_path / name
        corpus = generate(total_records=_RECORDS, seed=_SEED)
        write_corpus(corpus, out_dir, seed=_SEED, total_records=_RECORDS, pdf_share=_SHARE)
        digests.append(
            {
                path.relative_to(out_dir).as_posix(): path.read_bytes()
                for path in sorted(out_dir.rglob("*"))
                if path.is_file() and path.name != "recipe.toml"
            }
        )
    assert digests[0] == digests[1]


def test_csv_only_output_is_unchanged_by_the_variant(tmp_path: Path) -> None:
    """The default share writes the flat layout the committed baseline measured."""

    out_dir = tmp_path / "csv-only"
    corpus = generate(total_records=_RECORDS, seed=_SEED)
    write_corpus(corpus, out_dir, seed=_SEED, total_records=_RECORDS)

    assert (out_dir / "incoming.csv").is_file()
    assert not (out_dir / "incoming").exists()
    assert not (out_dir / "pdf_manifest.json").exists()
    recipe_text = (out_dir / "recipe.toml").read_text(encoding="utf-8")
    assert 'incoming = "incoming.csv"' in recipe_text
    assert "pdfplumber" not in recipe_text
    assert "--pdf-share" not in recipe_text


def test_switching_layouts_leaves_no_stale_inputs(tmp_path: Path) -> None:
    """A regenerated directory must not keep the other layout's inputs.

    A stale `incoming.csv` beside a new `incoming/` directory would be
    ingested as a second copy of rows the PDFs already carry.
    """

    out_dir = tmp_path / "switching"
    corpus = generate(total_records=_RECORDS, seed=_SEED)
    write_corpus(corpus, out_dir, seed=_SEED, total_records=_RECORDS)
    write_corpus(corpus, out_dir, seed=_SEED, total_records=_RECORDS, pdf_share=_SHARE)
    assert not (out_dir / "incoming.csv").exists()

    write_corpus(corpus, out_dir, seed=_SEED, total_records=_RECORDS)
    assert not (out_dir / "incoming").exists()
    assert not (out_dir / "pdf_manifest.json").exists()


def test_writing_into_a_directory_the_generator_did_not_produce_is_refused(
    tmp_path: Path,
) -> None:
    """Fail closed: `--out-dir` is user-supplied and the cleanup deletes a tree.

    Clearing the previous layout removes an `incoming/` directory whole, so a
    non-empty directory without this generator's markers is refused with
    nothing deleted, whatever it holds.
    """

    out_dir = tmp_path / "somebody-elses-directory"
    (out_dir / "incoming").mkdir(parents=True)
    keep = out_dir / "incoming" / "intake-scans.pdf"
    keep.write_bytes(b"%PDF-1.4 not ours\n")
    notes = out_dir / "notes.txt"
    notes.write_text("real work in progress\n", encoding="utf-8")

    corpus = generate(total_records=120, seed=7)
    with pytest.raises(ValueError, match="not empty"):
        write_corpus(corpus, out_dir, seed=7, total_records=120, pdf_share=_SHARE)

    assert keep.read_bytes() == b"%PDF-1.4 not ours\n"
    assert notes.is_file()
    assert not (out_dir / "existing.csv").exists()


def test_a_generated_corpus_directory_is_still_rewritten(tmp_path: Path) -> None:
    """The guard refuses strangers, not the generator's own output.

    Regeneration over a corpus this tool wrote is the ordinary path (that is
    what `--regenerate` does), so it must keep working, including the switch
    from one layout to the other.
    """

    out_dir = tmp_path / "generated"
    corpus = generate(total_records=120, seed=7)
    write_corpus(corpus, out_dir, seed=7, total_records=120)
    write_corpus(corpus, out_dir, seed=7, total_records=120, pdf_share=_SHARE)

    assert (out_dir / "incoming" / "incoming.csv").is_file()
    assert not (out_dir / "incoming.csv").exists()


def test_write_corpus_rejects_a_share_outside_the_unit_interval(tmp_path: Path) -> None:
    corpus = generate(total_records=120, seed=7)
    with pytest.raises(ValueError, match="pdf_share"):
        write_corpus(corpus, tmp_path / "bad", seed=7, total_records=120, pdf_share=1.5)


def test_the_pipeline_ingests_the_mixed_corpus_whole(mixed_corpus: Path) -> None:
    """End to end: every PDF page becomes one record, none dropped.

    This is the property the stage-baseline harness's accounting gate relies
    on, and the reason the mixed variant measures real extract time rather
    than a partial read.
    """

    recipe = load_recipe(mixed_corpus / "recipe.toml")
    accounting = pipeline.IngestAccumulator()
    records = pipeline._ingest_source(
        recipe.incoming, "incoming", recipe=recipe, id_prefix="N", accounting=accounting
    )

    manifest = json.loads((mixed_corpus / "pdf_manifest.json").read_text(encoding="utf-8"))
    pdf_rows = sum(len(document["incoming_ids"]) for document in manifest["documents"])
    with (mixed_corpus / "incoming" / "incoming.csv").open(newline="", encoding="utf-8") as handle:
        csv_rows = len(list(csv.DictReader(handle)))

    assert accounting.pages_extracted == pdf_rows
    assert accounting.pages_dropped == 0
    assert accounting.files_skipped == []
    assert len(records) == csv_rows + pdf_rows


def _pdf_derived_records(corpus_dir: Path) -> list[dict[str, str]]:
    """The raw field maps of the records that came from the PDF documents."""

    recipe = load_recipe(corpus_dir / "recipe.toml")
    records = pipeline._ingest_source(recipe.incoming, "incoming", recipe=recipe, id_prefix="N")
    with (corpus_dir / "incoming" / "incoming.csv").open(newline="", encoding="utf-8") as handle:
        csv_ids = {f"incoming:{row['id']}" for row in csv.DictReader(handle)}
    return [dict(record.raw) for record in records if record.unique_id not in csv_ids]


def _planted_pdf_rows(corpus_dir: Path) -> list[dict[str, str]]:
    """The generator's own rows for whatever the manifest says rides as PDF."""

    corpus = generate(total_records=_RECORDS, seed=_SEED)
    by_id = {row["id"]: row for row in corpus.incoming_rows}
    manifest = json.loads((corpus_dir / "pdf_manifest.json").read_text(encoding="utf-8"))
    return [by_id[rid] for document in manifest["documents"] for rid in document["incoming_ids"]]


def test_pdf_carried_names_survive_extraction(mixed_corpus: Path) -> None:
    """Every planted name pair comes back, whatever channel perturbed it."""

    extracted = {
        (record.get("first_name"), record.get("last_name"))
        for record in _pdf_derived_records(mixed_corpus)
    }
    planted = {(row["First Name"], row["Last Name"]) for row in _planted_pdf_rows(mixed_corpus)}
    assert planted <= extracted


def _dob_line_is_extractable(value: str) -> bool:
    """Whether the extractor's own date pattern matches this labeled line."""

    line = f"Date of Birth: {value}"
    return any(pattern.search(line) for pattern in FIELD_PATTERNS["dob"])


def test_only_numeric_dates_of_birth_survive_extraction(mixed_corpus: Path) -> None:
    """The extractor's date pattern is numeric, and the corpus plants prose dates.

    The date-drift channel re-renders a date of birth in formats a real intake
    form uses, three of which are textual ("26 November 1942"). `FIELD_PATTERNS`
    in `extract/base.py` matches numeric dates only, so a textual date does not
    survive a PDF page while it does survive a CSV cell. That asymmetry is a
    property of the extractor, not of this corpus, and the stage-baseline
    report says so rather than leaving the difference to be read as a matcher
    regression. Both halves are pinned here: numeric dates come back, textual
    ones come back as no date at all.
    """

    planted = _planted_pdf_rows(mixed_corpus)
    textual = [row for row in planted if not _dob_line_is_extractable(row["DOB"])]
    assert textual, "expected the date-drift channel to plant textual dates at this size"
    assert len(textual) < len(planted), "expected numeric dates too"

    expected = {
        (
            row["First Name"],
            row["Last Name"],
            row["DOB"] if _dob_line_is_extractable(row["DOB"]) else None,
        )
        for row in planted
    }
    extracted = {
        (record.get("first_name"), record.get("last_name"), record.get("dob"))
        for record in _pdf_derived_records(mixed_corpus)
    }
    assert expected <= extracted

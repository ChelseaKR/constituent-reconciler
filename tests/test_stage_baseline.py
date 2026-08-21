"""CI-sized smoke tests for the stage-timing baseline harness.

The full-size baseline (`make perf-baseline`) is a local command, not a CI
job, matching how `make eval-large` is run. These tests prove the harness on
a tiny corpus: the six stages are timed and recorded with the pinned corpus
parameters and environment, the composed stages produce the same artifacts
`pipeline.run` plus `pipeline.export` produce, the outputs stay free of
field values and machine-specific paths, and every fail-closed refusal
(mismatched parameters, corpus bytes changed after generation, unaccounted
input) aborts without writing a report.

The mixed CSV+PDF variant (`make perf-baseline-pdf`, issue #78) gets the same
treatment at the end of the file: the extract row must report the real time
the ingest walk spent in the PDF reader over the real page count, the corpus
block must record the PDF parameters, the composed stages must match
`pipeline.run` over that layout too, and a PDF document edited after
generation (or a file added to the incoming directory) must be refused the
way an edited CSV is.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest
from tools.corpusgen import stage_baseline
from tools.corpusgen.generate import PDF_PAGES_PER_DOC, generate, write_corpus

from constituent_reconciler import pipeline
from constituent_reconciler.config import load_recipe

_RECORDS = 200
_SEED = 20260707
_DATE = "2026-08-03"


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Run the harness CLI once on a tiny corpus; share its outputs."""

    base = tmp_path_factory.mktemp("stage-baseline")
    corpus_dir = base / "corpus"
    report = base / "stage-baseline.md"
    json_out = base / "stage-baseline.json"
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(corpus_dir),
            "--records",
            str(_RECORDS),
            "--seed",
            str(_SEED),
            "--report-out",
            str(report),
            "--json-out",
            str(json_out),
            "--regenerate",
            "--date",
            _DATE,
        ]
    )
    assert rc == 0
    return {"base": base, "corpus": corpus_dir, "report": report, "json": json_out}


def test_json_records_stages_params_and_environment(baseline: dict[str, Path]) -> None:
    payload = json.loads(baseline["json"].read_text(encoding="utf-8"))

    assert payload["baseline_schema_version"] == stage_baseline.BASELINE_SCHEMA_VERSION
    assert payload["variant"] == "pre-cache"
    assert payload["measured_on"] == _DATE

    stages = payload["results"]["stages"]
    assert [stage["name"] for stage in stages] == list(stage_baseline.STAGE_NAMES)
    for stage in stages:
        assert stage["wall_seconds"] >= 0.0
        assert isinstance(stage["items"], int)
        assert stage["peak_rss_mib_after"] > 0.0

    corpus = payload["corpus"]
    assert corpus["seed"] == _SEED
    assert corpus["requested_records"] == _RECORDS
    assert corpus["existing_rows"] + corpus["incoming_rows"] == payload["results"]["records"]
    assert corpus["input_digest_blake2b"]

    environment = payload["environment"]
    assert environment["python"]
    assert environment["cpu_count"] >= 1
    assert "node" not in environment  # no hostname; the machine class only


def test_report_states_pre_cache_and_no_promise(baseline: dict[str, Path]) -> None:
    report = baseline["report"].read_text(encoding="utf-8")
    assert "## Stage timings" in report
    assert "not a performance promise" in report
    assert "no stage cache was active" in report
    assert "make perf-baseline" in report


def test_outputs_are_content_free(baseline: dict[str, Path]) -> None:
    """No field values, no absolute paths: counts and durations only."""

    outputs = baseline["report"].read_text(encoding="utf-8") + baseline["json"].read_text(
        encoding="utf-8"
    )
    assert str(baseline["base"]) not in outputs
    assert "/Users/" not in outputs and "/home/" not in outputs

    with (baseline["corpus"] / "existing.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    for row in rows[:20]:
        # Word-boundary matching: a short name such as "Thi" must not trip on
        # the word "This" in report prose, while a real leaked value would hit.
        for column in ("First Name", "Last Name"):
            assert not re.search(rf"\b{re.escape(row[column])}\b", outputs)
        assert row["Address"] not in outputs
        if row["Email"]:
            assert row["Email"] not in outputs


def _assert_composed_stages_match_the_pipeline(fixture: dict[str, Path]) -> None:
    """Assert one harness run's artifacts match pipeline.run plus export.

    A fresh pipeline.run + export over the same corpus must reproduce the
    harness's decision artifacts byte for byte: the review queue, the resolved
    CSV, and the count-only run summary. Byte equality here is what makes a
    committed baseline a truthful "before" for the UC-01 cache comparison, so
    both corpus layouts are held to it.
    """

    recipe = load_recipe(fixture["corpus"] / "recipe.toml")
    result = pipeline.run(recipe)
    expected_out = fixture["base"] / "expected-out"
    pipeline.export(result, recipe, out_dir=expected_out, dry_run=False)

    work = fixture["corpus"] / "stage-baseline-work"
    for name in ("review_queue.csv", "resolved.csv"):
        assert (work / "out" / name).read_bytes() == (expected_out / name).read_bytes()
    # run_summary.json is compared structurally: wall-clock stage durations
    # (and any other timing the summary may carry) differ between two
    # executions by nature, while every count must still agree exactly.
    volatile = ("stage_durations_seconds",)
    summaries = []
    for base in (work / "out", expected_out):
        summary = json.loads((base / "run_summary.json").read_text(encoding="utf-8"))
        for key in volatile:
            summary.pop(key, None)
        summaries.append(summary)
    assert summaries[0] == summaries[1]
    assert (work / "review-artifact" / "review_queue.csv").read_bytes() == (
        expected_out / "review_queue.csv"
    ).read_bytes()

    payload = json.loads(fixture["json"].read_text(encoding="utf-8"))
    counts = payload["results"]
    assert counts["records"] == len(result.records)
    assert counts["candidate_pairs"] == len(result.pairs)
    assert counts["auto_pairs"] == len(result.auto_pairs)
    assert counts["review_pairs"] == len(result.review_pairs)
    assert counts["golden_records"] == len(result.golden)


def test_composed_stages_match_the_pipeline(baseline: dict[str, Path]) -> None:
    """The harness's stage composition must not drift from pipeline.run/export."""

    _assert_composed_stages_match_the_pipeline(baseline)


def test_existing_corpus_with_other_params_is_refused(tmp_path: Path) -> None:
    """Fail closed: never stamp a baseline with parameters the corpus lacks."""

    corpus_dir = tmp_path / "corpus"
    corpus = generate(total_records=120, seed=7)
    write_corpus(corpus, corpus_dir, seed=7, total_records=120)
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(corpus_dir),
            "--records",
            "999",
            "--seed",
            "8",
            "--report-out",
            str(tmp_path / "mismatch.md"),
            "--date",
            _DATE,
        ]
    )
    assert rc == 1
    assert not (tmp_path / "mismatch.md").exists()


def test_hand_modified_corpus_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed: reused corpus CSVs must be the pinned generator's bytes.

    The recipe header alone is not proof. An untouched corpus passes the
    digest comparison against a fresh regeneration; the same corpus with one
    edited cell in existing.csv, beside an untouched recipe.toml, is refused
    before any measurement runs.
    """

    corpus_dir = tmp_path / "corpus"
    corpus = generate(total_records=120, seed=7)
    write_corpus(corpus, corpus_dir, seed=7, total_records=120)
    assert stage_baseline._ensure_corpus(corpus_dir, records=120, seed=7, regenerate=False)

    existing = corpus_dir / "existing.csv"
    existing.write_bytes(existing.read_bytes().replace(b"granted", b"revoked", 1))
    report = tmp_path / "tampered.md"
    json_out = tmp_path / "tampered.json"
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(corpus_dir),
            "--records",
            "120",
            "--seed",
            "7",
            "--report-out",
            str(report),
            "--json-out",
            str(json_out),
            "--date",
            _DATE,
        ]
    )
    assert rc == 1
    assert "not what the generator produces" in capsys.readouterr().err
    assert not report.exists()
    assert not json_out.exists()


def test_csv_only_payload_has_no_pdf_block(baseline: dict[str, Path]) -> None:
    """A CSV-only run carries the keys the committed 2026-08-03 baseline has.

    The mixed variant adds its numbers under `corpus.pdf`; the CSV-only shape
    stays exactly what it was, so the committed baseline stays comparable key
    for key at the same schema version.
    """

    payload = json.loads(baseline["json"].read_text(encoding="utf-8"))
    assert "pdf" not in payload["corpus"]
    extract = next(s for s in payload["results"]["stages"] if s["name"] == "extract")
    assert extract["wall_seconds"] == 0.0
    assert extract["items"] == 0
    assert "CSV-only" in extract["note"]


def test_unaccounted_input_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed: a source-row accounting mismatch aborts before any output.

    After measuring, the harness cross-checks its own CSV row counts against
    what the pipeline ingested. Overcounting the source rows by one simulates
    a row the pipeline never accounted for; the run must refuse and write
    neither the report nor the JSON companion.
    """

    real_count = stage_baseline._csv_data_rows
    monkeypatch.setattr(stage_baseline, "_csv_data_rows", lambda path: real_count(path) + 1)
    report = tmp_path / "unaccounted.md"
    json_out = tmp_path / "unaccounted.json"
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(tmp_path / "corpus"),
            "--records",
            "120",
            "--seed",
            "7",
            "--report-out",
            str(report),
            "--json-out",
            str(json_out),
            "--regenerate",
            "--date",
            _DATE,
        ]
    )
    assert rc == 1
    assert "unaccounted input" in capsys.readouterr().err
    assert not report.exists()
    assert not json_out.exists()


# --- the cached ("after") run (issue #78) ----------------------------------


@pytest.fixture(scope="module")
def cached_baseline(baseline: dict[str, Path]) -> dict[str, Path]:
    """Run the harness CLI with --cached over the same corpus the baseline built."""

    base = baseline["base"]
    report = base / "stage-baseline-cached.md"
    json_out = base / "stage-baseline-cached.json"
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(baseline["corpus"]),
            "--records",
            str(_RECORDS),
            "--seed",
            str(_SEED),
            "--cached",
            "--baseline-json",
            str(baseline["json"]),
            "--report-out",
            str(report),
            "--json-out",
            str(json_out),
            "--date",
            _DATE,
        ]
    )
    assert rc == 0
    return {"base": base, "corpus": baseline["corpus"], "report": report, "json": json_out}


def test_cached_json_reports_variant_and_cache_stats(cached_baseline: dict[str, Path]) -> None:
    payload = json.loads(cached_baseline["json"].read_text(encoding="utf-8"))

    assert payload["variant"] == "cached"
    stages = payload["results"]["stages"]
    assert [stage["name"] for stage in stages] == list(stage_baseline.STAGE_NAMES)

    cache_stats = payload["cache_stats"]
    assert cache_stats["enabled"] is True
    # The measured pass reads a cache the harness pre-warmed and discarded,
    # so normalize must show hits, not misses, on this pass.
    assert cache_stats["misses"].get("normalize", 0) == 0
    assert cache_stats["hits"].get("normalize", 0) == payload["results"]["records"]


def test_cached_json_compares_against_the_baseline(cached_baseline: dict[str, Path]) -> None:
    payload = json.loads(cached_baseline["json"].read_text(encoding="utf-8"))
    compared = payload["compared_to"]
    assert compared["measured_on"] == _DATE
    blocks = ("stage_wall_seconds_before", "stage_wall_seconds_after", "stage_wall_seconds_delta")
    for block in blocks:
        assert set(compared[block]) == set(stage_baseline.STAGE_NAMES)


def test_cached_report_states_the_after_side_and_no_promise(
    cached_baseline: dict[str, Path],
) -> None:
    report = cached_baseline["report"].read_text(encoding="utf-8")
    assert "cached, after" in report
    assert "not a performance promise" in report
    assert "## Cache stats" in report
    assert "## Comparison to" in report
    assert "make perf-baseline-cached" in report


def test_cached_outputs_are_content_free(cached_baseline: dict[str, Path]) -> None:
    outputs = cached_baseline["report"].read_text(encoding="utf-8") + cached_baseline[
        "json"
    ].read_text(encoding="utf-8")
    assert str(cached_baseline["base"]) not in outputs
    assert "/Users/" not in outputs and "/home/" not in outputs

    with (cached_baseline["corpus"] / "existing.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    for row in rows[:20]:
        for column in ("First Name", "Last Name"):
            assert not re.search(rf"\b{re.escape(row[column])}\b", outputs)
        assert row["Address"] not in outputs
        if row["Email"]:
            assert row["Email"] not in outputs


def test_cached_run_reaches_the_same_result_as_uncached(
    baseline: dict[str, Path], cached_baseline: dict[str, Path]
) -> None:
    """Caching must change timing only, never what the pipeline computes."""

    before = json.loads(baseline["json"].read_text(encoding="utf-8"))["results"]
    after = json.loads(cached_baseline["json"].read_text(encoding="utf-8"))["results"]
    for key in (
        "records",
        "candidate_pairs",
        "auto_pairs",
        "review_pairs",
        "golden_records",
        "written",
        "withheld",
    ):
        assert before[key] == after[key], key


def test_cached_run_requires_a_csv_only_corpus(tmp_path: Path) -> None:
    """--cached folds extraction into ingest, so it refuses a PDF-share corpus."""

    rc = stage_baseline.main(
        [
            "--out-dir",
            str(tmp_path / "corpus"),
            "--records",
            "120",
            "--seed",
            "7",
            "--pdf-share",
            "0.2",
            "--cached",
            "--report-out",
            str(tmp_path / "refused.md"),
        ]
    )
    assert rc == 1


# --- the mixed CSV+PDF variant (issue #78) --------------------------------

_PDF_RECORDS = 400
_PDF_SHARE = 0.2


@pytest.fixture(scope="module")
def pdf_baseline(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Run the harness CLI once on a tiny mixed corpus; share its outputs.

    Sized so the run spans more than one generated PDF document, which is
    what makes the per-document accumulation in the extract row meaningful.
    """

    base = tmp_path_factory.mktemp("stage-baseline-pdf")
    corpus_dir = base / "corpus"
    report = base / "stage-baseline.md"
    json_out = base / "stage-baseline.json"
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(corpus_dir),
            "--records",
            str(_PDF_RECORDS),
            "--seed",
            str(_SEED),
            "--pdf-share",
            str(_PDF_SHARE),
            "--report-out",
            str(report),
            "--json-out",
            str(json_out),
            "--regenerate",
            "--date",
            _DATE,
        ]
    )
    assert rc == 0
    return {"base": base, "corpus": corpus_dir, "report": report, "json": json_out}


def _manifest_counts(corpus_dir: Path) -> tuple[int, int]:
    manifest = json.loads((corpus_dir / "pdf_manifest.json").read_text(encoding="utf-8"))
    documents = manifest["documents"]
    return len(documents), sum(len(doc["incoming_ids"]) for doc in documents)


def test_extract_stage_reports_real_pdf_work(pdf_baseline: dict[str, Path]) -> None:
    """The point of the variant: extract is no longer an honest zero.

    The row must carry time the pipeline's own PDF reader actually spent, over
    exactly the pages the manifest says were written, and the ingest row must
    say it excludes that time so the two rows partition the walk rather than
    double-counting it.
    """

    payload = json.loads(pdf_baseline["json"].read_text(encoding="utf-8"))
    stages = {stage["name"]: stage for stage in payload["results"]["stages"]}
    _, pdf_rows = _manifest_counts(pdf_baseline["corpus"])

    assert stages["extract"]["wall_seconds"] > 0.0
    assert stages["extract"]["items"] == pdf_rows
    assert "PDF reader" in stages["extract"]["note"]
    assert stages["ingest"]["wall_seconds"] >= 0.0
    assert "excludes the time" in stages["ingest"]["note"]


def test_pdf_corpus_block_records_the_variant(pdf_baseline: dict[str, Path]) -> None:
    payload = json.loads(pdf_baseline["json"].read_text(encoding="utf-8"))
    corpus = payload["corpus"]
    documents, pdf_rows = _manifest_counts(pdf_baseline["corpus"])

    assert corpus["pdf"]["share"] == _PDF_SHARE
    assert corpus["pdf"]["documents"] == documents
    assert corpus["pdf"]["rows"] == pdf_rows
    assert corpus["pdf"]["pages_per_document"] == PDF_PAGES_PER_DOC
    # The accounting gate in main() already refused any drift here; this
    # states the invariant the gate enforces, in the artifact's own numbers.
    assert corpus["existing_rows"] + corpus["incoming_rows"] == payload["results"]["records"]
    assert corpus["incoming_rows"] > pdf_rows  # CSV rows remain on the incoming side


def test_pdf_report_states_the_extraction_asymmetry(pdf_baseline: dict[str, Path]) -> None:
    """The report must explain why the mixed run's counts differ, not hide it."""

    report = pdf_baseline["report"].read_text(encoding="utf-8")
    assert "mixed CSV and PDF corpus" in report
    assert "make perf-baseline-pdf" in report
    assert "not comparable to a CSV-only run" in report
    assert "not a performance promise" in report
    assert "Address and consent have no extraction pattern" in report


def test_the_pdf_note_quotes_this_runs_own_banding_counts(pdf_baseline: dict[str, Path]) -> None:
    """The note may say only what this run measured, in this run's numbers.

    An earlier version read the count difference as the fail-closed gate
    working, which neither artifact's numbers show: nothing here separates the
    PDF-carried rows from the CSV rows. The note now quotes the banding counts
    printed beside it, so the artifact substantiates every number it states.
    """

    payload = json.loads(pdf_baseline["json"].read_text(encoding="utf-8"))
    counts = payload["results"]
    expected = stage_baseline.pdf_extraction_note(
        candidate_pairs=counts["candidate_pairs"],
        auto_pairs=counts["auto_pairs"],
        review_pairs=counts["review_pairs"],
    )
    assert expected in payload["notes"]
    assert expected in pdf_baseline["report"].read_text(encoding="utf-8")
    assert str(counts["review_pairs"]) in expected
    assert "fail-closed gate" not in expected


def test_pdf_outputs_are_content_free(pdf_baseline: dict[str, Path]) -> None:
    """Same content-free bar as the CSV-only baseline, over the mixed corpus."""

    outputs = pdf_baseline["report"].read_text(encoding="utf-8") + pdf_baseline["json"].read_text(
        encoding="utf-8"
    )
    assert str(pdf_baseline["base"]) not in outputs
    assert "/Users/" not in outputs and "/home/" not in outputs
    assert "intake-0001.pdf" not in outputs

    with (pdf_baseline["corpus"] / "incoming" / "incoming.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    for row in rows[:20]:
        for column in ("First Name", "Last Name"):
            assert not re.search(rf"\b{re.escape(row[column])}\b", outputs)
        assert row["Address"] not in outputs


def test_the_timing_swap_is_put_back(pdf_baseline: dict[str, Path]) -> None:
    """The harness times the PDF reader in place; it must not leave it wrapped.

    A leaked wrapper would keep accumulating into a dead closure and would
    make any later `pipeline.run` in this process measure the harness instead
    of the pipeline.
    """

    assert pipeline.read_pdf_records.__module__ == "constituent_reconciler.pipeline"
    assert pipeline.read_pdf_records.__name__ == "read_pdf_records"


def test_edited_pdf_document_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Fail closed: the digest covers the PDFs, not only the CSVs.

    A trailing PDF comment leaves the file parseable, so nothing downstream
    would notice. The digest does, before any measurement runs.
    """

    corpus_dir = tmp_path / "corpus"
    corpus = generate(total_records=200, seed=_SEED)
    write_corpus(corpus, corpus_dir, seed=_SEED, total_records=200, pdf_share=_PDF_SHARE)
    assert stage_baseline._ensure_corpus(
        corpus_dir, records=200, seed=_SEED, pdf_share=_PDF_SHARE, regenerate=False
    )

    document = sorted((corpus_dir / "incoming").glob("intake-*.pdf"))[0]
    document.write_bytes(document.read_bytes() + b"% edited after generation\n")
    report = tmp_path / "tampered.md"
    json_out = tmp_path / "tampered.json"
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(corpus_dir),
            "--records",
            "200",
            "--seed",
            str(_SEED),
            "--pdf-share",
            str(_PDF_SHARE),
            "--report-out",
            str(report),
            "--json-out",
            str(json_out),
            "--date",
            _DATE,
        ]
    )
    assert rc == 1
    assert "not what the generator produces" in capsys.readouterr().err
    assert not report.exists()
    assert not json_out.exists()


def test_composed_stages_match_the_pipeline_over_the_mixed_corpus(
    pdf_baseline: dict[str, Path],
) -> None:
    """The same anti-drift check, over the layout the mixed variant measures.

    The mixed run does not compose the CSV-only sequence: it swaps the
    pipeline's PDF reader for a timed wrapper across the ingest walk. Drift
    there would skew the extract row the mixed baseline exists to report, so
    the composed artifacts must still match a plain pipeline.run plus export
    over the same corpus.
    """

    _assert_composed_stages_match_the_pipeline(pdf_baseline)


def test_a_file_added_to_the_incoming_directory_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed: the digest covers the incoming directory, not a name glob.

    The recipe points the pipeline at `incoming/`, so the pipeline reads
    whatever lands there. A header-only CSV dropped in after generation adds
    no records, which means the source-row accounting check would pass it as
    well. The digest is what catches it, and only because it walks the
    directory instead of globbing the generated document names.
    """

    corpus_dir = tmp_path / "corpus"
    corpus = generate(total_records=200, seed=_SEED)
    write_corpus(corpus, corpus_dir, seed=_SEED, total_records=200, pdf_share=_PDF_SHARE)
    assert stage_baseline._ensure_corpus(
        corpus_dir, records=200, seed=_SEED, pdf_share=_PDF_SHARE, regenerate=False
    )

    added = corpus_dir / "incoming" / "extra-intake.csv"
    added.write_text("id,First Name,Last Name,DOB,Email,Phone,Address,Consent\n", encoding="utf-8")
    report = tmp_path / "added.md"
    json_out = tmp_path / "added.json"
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(corpus_dir),
            "--records",
            "200",
            "--seed",
            str(_SEED),
            "--pdf-share",
            str(_PDF_SHARE),
            "--report-out",
            str(report),
            "--json-out",
            str(json_out),
            "--date",
            _DATE,
        ]
    )
    assert rc == 1
    assert "changed, added, or removed after generation" in capsys.readouterr().err
    assert not report.exists()
    assert not json_out.exists()


def test_a_csv_only_corpus_is_refused_for_a_pdf_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed: the recorded share must describe the measured layout."""

    corpus_dir = tmp_path / "corpus"
    corpus = generate(total_records=200, seed=_SEED)
    write_corpus(corpus, corpus_dir, seed=_SEED, total_records=200)
    report = tmp_path / "wrong-layout.md"
    rc = stage_baseline.main(
        [
            "--out-dir",
            str(corpus_dir),
            "--records",
            "200",
            "--seed",
            str(_SEED),
            "--pdf-share",
            str(_PDF_SHARE),
            "--report-out",
            str(report),
            "--date",
            _DATE,
        ]
    )
    assert rc == 1
    assert "does not match" in capsys.readouterr().err
    assert not report.exists()

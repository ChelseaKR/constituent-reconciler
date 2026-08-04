"""CI-sized smoke tests for the stage-timing baseline harness.

The full-size baseline (`make perf-baseline`) is a local command, not a CI
job, matching how `make eval-large` is run. These tests prove the harness on
a tiny corpus: the six stages are timed and recorded with the pinned corpus
parameters and environment, the composed stages produce the same artifacts
`pipeline.run` plus `pipeline.export` produce, and the outputs stay free of
field values and machine-specific paths.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest
from tools.corpusgen import stage_baseline
from tools.corpusgen.generate import generate, write_corpus

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


def test_composed_stages_match_the_pipeline(baseline: dict[str, Path]) -> None:
    """The harness's stage composition must not drift from pipeline.run/export.

    A fresh pipeline.run + export over the same corpus must reproduce the
    harness's decision artifacts byte for byte: the review queue, the resolved
    CSV, and the count-only run summary. Byte equality here is what makes the
    committed baseline a truthful "before" for the UC-01 cache comparison.
    """

    recipe = load_recipe(baseline["corpus"] / "recipe.toml")
    result = pipeline.run(recipe)
    expected_out = baseline["base"] / "expected-out"
    pipeline.export(result, recipe, out_dir=expected_out, dry_run=False)

    work = baseline["corpus"] / "stage-baseline-work"
    for name in ("review_queue.csv", "resolved.csv", "run_summary.json"):
        assert (work / "out" / name).read_bytes() == (expected_out / name).read_bytes()
    assert (work / "review-artifact" / "review_queue.csv").read_bytes() == (
        expected_out / "review_queue.csv"
    ).read_bytes()

    payload = json.loads(baseline["json"].read_text(encoding="utf-8"))
    counts = payload["results"]
    assert counts["records"] == len(result.records)
    assert counts["candidate_pairs"] == len(result.pairs)
    assert counts["auto_pairs"] == len(result.auto_pairs)
    assert counts["review_pairs"] == len(result.review_pairs)
    assert counts["golden_records"] == len(result.golden)


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

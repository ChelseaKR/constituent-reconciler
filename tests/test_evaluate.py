from __future__ import annotations

import json
from pathlib import Path

import pytest

from constituent_reconciler.decisions import band_pairs
from constituent_reconciler.evaluate import (
    ExtractionReport,
    calibrate,
    cohen_kappa,
    evaluate,
    extraction_metrics,
    normalize_extracted_value,
    truth_pairs,
    wilson_interval,
)
from constituent_reconciler.extract.base import ExtractedField


def _labels(pairs: list[tuple[bool, bool]]) -> list[dict[str, object]]:
    return [
        {"record_id": f"R{i:03d}", "field": "email", "predicted": p, "actual": a}
        for i, (p, a) in enumerate(pairs)
    ]


def test_wilson_zero_successes() -> None:
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert 0.25 < high < 0.35


def test_wilson_half() -> None:
    low, high = wilson_interval(5, 10)
    assert low < 0.5 < high


def test_wilson_no_trials_is_widest() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_truth_pairs_expands_clusters() -> None:
    pairs = truth_pairs([["a", "b", "c"]])
    assert pairs == {
        frozenset(("a", "b")),
        frozenset(("a", "c")),
        frozenset(("b", "c")),
    }


def test_cohen_kappa_perfect_agreement() -> None:
    assert cohen_kappa([True, True, False], [True, True, False]) == pytest.approx(1.0)


def test_cohen_kappa_chance_agreement() -> None:
    # All positives: p_expected = 1, kappa is 0.0 by the undefined-guard.
    assert cohen_kappa([True, True], [True, True]) == 0.0


def test_cohen_kappa_half_agreement() -> None:
    # predicted [T, F, T, F], actual [T, T, F, F] -> 2/4 agree
    kappa = cohen_kappa([True, False, True, False], [True, True, False, False])
    assert -0.1 < kappa < 0.1


def test_cohen_kappa_better_than_chance() -> None:
    # predicted and actual agree more than chance
    kappa = cohen_kappa([True, True, False, False], [True, True, False, False])
    assert kappa == pytest.approx(1.0)


def test_cohen_kappa_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        cohen_kappa([True], [True, False])


def test_cohen_kappa_empty_raises() -> None:
    with pytest.raises(ValueError):
        cohen_kappa([], [])


def test_calibrate_passes_above_gate() -> None:
    # 9 true agreements, 9 false agreements, 2 disagreements: kappa = 0.80.
    pairs = [(True, True)] * 9 + [(False, False)] * 9 + [(True, False), (False, True)]
    report = calibrate(_labels(pairs))
    assert report.n_labels == 20
    assert report.kappa == pytest.approx(0.80)
    assert report.threshold == pytest.approx(0.60)
    assert report.passed


def test_calibrate_fails_below_gate() -> None:
    # 6 true agreements, 6 false agreements, 8 disagreements: kappa = 0.20.
    pairs = [(True, True)] * 6 + [(False, False)] * 6 + [(True, False), (False, True)] * 4
    report = calibrate(_labels(pairs))
    assert report.kappa == pytest.approx(0.20)
    assert not report.passed


def test_calibrate_boundary_kappa_at_gate_passes() -> None:
    # 8 true agreements, 8 false agreements, 4 disagreements: kappa = 0.60 exactly.
    pairs = [(True, True)] * 8 + [(False, False)] * 8 + [(True, False), (False, True)] * 2
    report = calibrate(_labels(pairs))
    assert report.kappa == pytest.approx(0.60)
    assert report.passed


def test_calibrate_empty_labels_raises() -> None:
    with pytest.raises(ValueError):
        calibrate([])


def test_calibrate_malformed_label_raises() -> None:
    with pytest.raises(ValueError):
        calibrate([{"record_id": "R001", "field": "email", "predicted": "yes", "actual": True}])


def test_evaluate_counts_false_merge_and_coverage() -> None:
    banded = band_pairs(
        [("a", "b", 0.99), ("a", "c", 0.85), ("x", "y", 0.99)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    # Truth: a-b and a-c are duplicates; x-y is not.
    report = evaluate(banded, [["a", "b"], ["a", "c"]], n_records=5)
    assert report.n_true_pairs == 2
    assert report.n_auto == 2
    # x-y was auto-merged but is not a true duplicate: one false merge.
    assert report.false_merges == 1
    # a-c is a true duplicate sitting in review, so coverage misses nothing.
    assert report.missed == 0
    assert report.recall_coverage == 1.0


def test_evaluate_disaggregates_documented_risk_classes() -> None:
    banded = band_pairs(
        [("a", "b", 0.85), ("c", "d", 0.50)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    report = evaluate(
        banded,
        [["a", "b"], ["c", "d"]],
        n_records=4,
        segments={
            "hyphenated surname": [["a", "b"]],
            "rural route": [["c", "d"]],
        },
    )

    scores = {segment.name: segment for segment in report.segments}
    assert scores["hyphenated surname"].coverage_recall == 1.0
    assert scores["hyphenated surname"].n_surfaced == 1
    assert scores["rural route"].coverage_recall == 0.0
    assert scores["rural route"].n_missed == 1
    assert scores["rural route"].blocking_misses == 0


def test_evaluate_rejects_segment_pair_outside_ground_truth() -> None:
    with pytest.raises(ValueError, match="is not ground truth"):
        evaluate(
            [],
            [["a", "b"]],
            n_records=3,
            segments={"invalid": [["a", "c"]]},
        )


# ---------------------------------------------------------------------------
# Extraction metrics
# ---------------------------------------------------------------------------


def _ef(field_name: str, value: str) -> ExtractedField:
    return ExtractedField(field_name=field_name, value=value, confidence=1.0)


def _label(field_name: str, value: str) -> dict[str, str]:
    return {"field_name": field_name, "value": value}


def test_normalize_extracted_value_uses_canonical_normalizers() -> None:
    assert normalize_extracted_value("phone", "(415) 555-0100") == "4155550100"
    assert normalize_extracted_value("dob", "03/09/1988") == "1988-03-09"
    assert normalize_extracted_value("first_name", "  O'Brien ") == "obrien"
    assert normalize_extracted_value("email", "A@Example.ORG") == "a@example.org"


def test_normalize_extracted_value_falls_back_on_unknown_or_unparseable() -> None:
    # Unknown field: whitespace-collapsed casefold, not the name normalizer.
    assert normalize_extracted_value("notes", "  Two   Words ") == "two words"
    # Known field the canonical normalizer cannot parse: same fallback, so two
    # identical raw strings still compare equal instead of collapsing to "".
    assert normalize_extracted_value("dob", "unknown") == "unknown"


def test_extraction_metrics_perfect_match_despite_formatting() -> None:
    predicted = {"a.pdf": [_ef("phone", "555.123.4567"), _ef("dob", "1970-05-12")]}
    truth = {"a.pdf": [_label("phone", "(555) 123-4567"), _label("dob", "05/12/1970")]}
    report = extraction_metrics(predicted, truth)
    assert (report.tp, report.fp, report.fn) == (2, 0, 0)
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.n_docs == 1


def test_extraction_metrics_counts_fp_and_fn_per_field() -> None:
    predicted = {
        "a.pdf": [
            _ef("first_name", "Alice"),
            _ef("phone", "555-000-1111"),  # wrong value: FP for phone, FN for truth
            _ef("email", "stray@example.org"),  # not labeled at all: FP
        ]
    }
    truth = {
        "a.pdf": [
            _label("first_name", "Alice"),
            _label("phone", "555-123-4567"),
            _label("dob", "1970-05-12"),  # never predicted: FN
        ]
    }
    report = extraction_metrics(predicted, truth)
    assert (report.tp, report.fp, report.fn) == (1, 2, 2)
    assert report.per_field["first_name"].tp == 1
    assert report.per_field["phone"].fp == 1
    assert report.per_field["phone"].fn == 1
    assert report.per_field["email"].fp == 1
    assert report.per_field["dob"].fn == 1
    assert report.precision == pytest.approx(1 / 3)
    assert report.recall == pytest.approx(1 / 3)


def test_extraction_metrics_empty_truth_makes_all_predictions_fp() -> None:
    predicted = {"a.pdf": [_ef("email", "x@example.org")]}
    report = extraction_metrics(predicted, {})
    assert (report.tp, report.fp, report.fn) == (0, 1, 0)
    assert report.precision == 0.0
    # No truth fields: recall is 1.0 by the empty-denominator convention, and
    # the Wilson interval is the widest honest (0, 1).
    assert report.recall == 1.0
    assert report.recall_ci == (0.0, 1.0)


def test_extraction_metrics_no_docs_at_all() -> None:
    report = extraction_metrics({}, {})
    assert report.n_docs == 0
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.precision_ci == (0.0, 1.0)
    assert report.recall_ci == (0.0, 1.0)


def test_extraction_metrics_truth_label_claimed_once() -> None:
    # Two identical predictions against one label: one TP, one FP.
    predicted = {"a.pdf": [_ef("email", "x@example.org"), _ef("email", "x@example.org")]}
    truth = {"a.pdf": [_label("email", "x@example.org")]}
    report = extraction_metrics(predicted, truth)
    assert (report.tp, report.fp, report.fn) == (1, 1, 0)


def test_extraction_metrics_does_not_match_across_documents() -> None:
    predicted = {"a.pdf": [_ef("email", "x@example.org")], "b.pdf": []}
    truth = {"b.pdf": [_label("email", "x@example.org")]}
    report = extraction_metrics(predicted, truth)
    assert (report.tp, report.fp, report.fn) == (0, 1, 1)
    assert report.n_docs == 2


# ---------------------------------------------------------------------------
# The committed labeled fixture meets the metrics-ledger targets
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "extraction"


def _score_committed_fixtures() -> tuple[int, ExtractionReport]:
    from constituent_reconciler.extract.pdf import PdfplumberExtractor

    labels = json.loads((_FIXTURES / "labels.json").read_text(encoding="utf-8"))
    extractor = PdfplumberExtractor()
    predicted: dict[str, list[ExtractedField]] = {}
    pdf_paths = sorted(_FIXTURES.glob("*.pdf"))
    for pdf_path in pdf_paths:
        result = extractor.extract(pdf_path)
        predicted[pdf_path.name] = [f for page in result.pages for f in page.fields]
    return len(pdf_paths), extraction_metrics(predicted, labels)


def test_committed_fixture_meets_ledger_targets() -> None:
    pytest.importorskip("pdfplumber", reason="pdfplumber not installed")
    n_pdfs, report = _score_committed_fixtures()
    # Every committed PDF must be labeled and scored.
    assert n_pdfs >= 3
    assert report.n_docs == n_pdfs
    # The metrics-ledger REVIEW targets: keep the committed fixture honest.
    assert report.precision >= 0.95
    assert report.recall >= 0.90
    # The planted worded-date miss keeps recall measurably below 100%.
    assert report.fn >= 1


def test_eval_extraction_cli_writes_report(tmp_path: Path) -> None:
    pytest.importorskip("pdfplumber", reason="pdfplumber not installed")
    from constituent_reconciler.cli import main

    out = tmp_path / "extraction-report.md"
    exit_code = main(["eval-extraction", "--fixtures", str(_FIXTURES), "--out", str(out)])
    assert exit_code == 0
    content = out.read_text(encoding="utf-8")
    assert "# Extraction eval report" in content
    assert "Per-field breakdown" in content
    assert "**MET**" in content

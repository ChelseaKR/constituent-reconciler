from __future__ import annotations

import json
from pathlib import Path

import pytest

from constituent_reconciler.decisions import band_pairs
from constituent_reconciler.evaluate import (
    UNMEASURED,
    EvalReport,
    ExtractionReport,
    calibrate,
    cohen_kappa,
    evaluate,
    extraction_metrics,
    f1_score,
    format_rate,
    gate_holds,
    normalize_extracted_value,
    rate,
    truth_pairs,
    wilson_interval,
)
from constituent_reconciler.extract.base import ExtractedField
from constituent_reconciler.models import Band, Pair


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
    # No truth fields, so recall was never measured. It used to report 1.0 here,
    # a perfect score for a document set nothing was labeled in. The Wilson
    # interval already said the honest thing, (0, 1); the point estimate now
    # agrees with it.
    assert report.recall is None
    assert report.recall_ci == (0.0, 1.0)


def test_extraction_metrics_no_docs_at_all() -> None:
    # The complement of every extraction assertion: an empty run must not be
    # able to satisfy a precision or recall target. Both used to report 1.0.
    report = extraction_metrics({}, {})
    assert report.n_docs == 0
    assert report.precision is None
    assert report.recall is None
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
    # The `is not None` assertions are part of the target, not a type dance: an
    # unmeasured precision is not a met target.
    assert report.precision is not None
    assert report.recall is not None
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


def test_f1_is_the_harmonic_mean() -> None:
    assert f1_score(1.0, 1.0) == pytest.approx(1.0)
    assert f1_score(0.5, 1.0) == pytest.approx(2 / 3)
    assert f1_score(0.993, 0.771) == pytest.approx(0.868, abs=5e-4)


def test_f1_of_zero_precision_and_recall_is_zero_not_a_division_error() -> None:
    assert f1_score(0.0, 0.0) == 0.0


def test_evaluate_reports_f1_consistent_with_its_precision_and_recall() -> None:
    pairs = [
        Pair("a", "b", 12.0, Band.AUTO),
        Pair("c", "d", 9.0, Band.REVIEW),
        Pair("e", "f", 9.0, Band.REVIEW),
    ]
    report = evaluate(pairs, [["a", "b"], ["c", "d"], ["g", "h"]], n_records=8)

    assert report.f1_auto == pytest.approx(f1_score(report.precision_auto, report.recall_auto))
    assert report.f1_coverage == pytest.approx(
        f1_score(report.precision_coverage, report.recall_coverage)
    )
    # One of two auto/review pairs is a true duplicate at the auto band, and the
    # third true pair was never surfaced, so neither F1 is degenerate.
    assert report.f1_auto is not None
    assert report.f1_coverage is not None
    assert 0.0 < report.f1_auto < 1.0
    assert 0.0 < report.f1_coverage < 1.0


# --- zero denominators: a rate over no cases is not a good rate (issue 159) ---


def _no_auto_report() -> EvalReport:
    """One run where every scored pair lands in review and nothing auto-merges.

    This is the shape a matcher replaced by a constant produces: a score above
    the review threshold and below the auto threshold, on every pair.
    """

    banded = band_pairs(
        [("a", "b", 0.90), ("a", "c", 0.90), ("x", "y", 0.90)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    return evaluate(banded, [["a", "b"], ["a", "c"]], n_records=5)


def test_rate_over_no_cases_is_undefined_not_zero() -> None:
    assert rate(0, 0) is None
    assert rate(3, 0) is None
    assert rate(0, 4) == 0.0
    assert rate(2, 4) == 0.5


def test_a_run_that_auto_merges_nothing_has_no_false_merge_rate() -> None:
    report = _no_auto_report()
    assert report.n_auto == 0
    assert report.false_merges == 0
    # The defect: 0 / 0 used to render as 0.0, the best possible value for this
    # metric, so a matcher that had been deleted scored perfectly.
    assert report.false_merge_rate is None
    # Precision over an empty auto set is not 1.0 either.
    assert report.precision_auto is None
    # The Wilson interval was already honest about it and stays that way.
    assert report.false_merge_ci == (0.0, 1.0)


def test_rates_with_a_real_denominator_are_unchanged() -> None:
    """The complement: nothing about a run that did measure something moves."""

    banded = band_pairs(
        [("a", "b", 0.99), ("a", "c", 0.85), ("x", "y", 0.99)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    report = evaluate(banded, [["a", "b"], ["a", "c"]], n_records=5)
    assert report.false_merge_rate == pytest.approx(0.5)
    assert report.precision_auto == pytest.approx(0.5)
    assert report.recall_coverage == 1.0
    assert report.missed_match_rate == 0.0


def test_the_gate_does_not_hold_on_an_unmeasured_rate() -> None:
    # A ceiling of 0.0 is the repo's default false-merge gate. An undefined rate
    # must not satisfy it, however generous the ceiling.
    assert gate_holds(None, 0.0) is False
    assert gate_holds(None, 1.0) is False
    assert gate_holds(0.0, 0.0) is True
    assert gate_holds(0.5, 0.4) is False


def test_f1_of_an_unmeasured_input_is_unmeasured() -> None:
    assert f1_score(None, 0.9) is None
    assert f1_score(0.9, None) is None
    assert f1_score(None, None) is None
    # Genuine zeroes still give a genuine zero, not None.
    assert f1_score(0.0, 0.0) == 0.0


def test_format_rate_names_the_absence_instead_of_printing_a_number() -> None:
    assert format_rate(None) == UNMEASURED
    assert UNMEASURED != "0.0%"
    assert format_rate(0.0) == "0.0%"
    assert format_rate(0.12345) == "12.3%"
    assert format_rate(0.12345, digits=2) == "12.35%"

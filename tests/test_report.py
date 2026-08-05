from __future__ import annotations

from constituent_reconciler.decisions import band_pairs
from constituent_reconciler.evaluate import CalibrationReport, EvalReport, evaluate
from constituent_reconciler.report import render_eval_markdown


def _eval_report() -> EvalReport:
    banded = band_pairs(
        [("a", "b", 0.99), ("x", "y", 0.85)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    return evaluate(banded, [["a", "b"], ["x", "y"]], n_records=4)


def test_eval_markdown_shows_kappa_section_and_pass() -> None:
    calibration = CalibrationReport(n_labels=20, kappa=0.80, threshold=0.60, passed=True)
    markdown = render_eval_markdown(_eval_report(), dataset="demo", calibration=calibration)
    assert "## Calibration (LLM field judge)" in markdown
    assert "Cohen's kappa: **0.80** over 20 labels." in markdown
    assert "Kappa gate at 0.60: **PASS**" in markdown


def test_eval_markdown_shows_kappa_fail() -> None:
    calibration = CalibrationReport(n_labels=20, kappa=0.20, threshold=0.60, passed=False)
    markdown = render_eval_markdown(_eval_report(), dataset="demo", calibration=calibration)
    assert "Kappa gate at 0.60: **FAIL** (observed 0.20)." in markdown


def test_eval_markdown_without_labels_is_fail_closed() -> None:
    markdown = render_eval_markdown(_eval_report(), dataset="demo", calibration=None)
    assert "## Calibration (LLM field judge)" in markdown
    assert "fail-closed" in markdown
    assert "Kappa gate at 0.60: **FAIL** (no labels)." in markdown


def test_eval_markdown_renders_disaggregated_risk_classes() -> None:
    banded = band_pairs(
        [("a", "b", 0.85)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    report = evaluate(
        banded,
        [["a", "b"]],
        n_records=2,
        segments={"transliterated name": [["a", "b"]]},
    )
    markdown = render_eval_markdown(report, dataset="bias-demo")

    assert "## Disaggregated error by documented risk class" in markdown
    assert "| transliterated name | 1 | 1 | 0 | 100.0% | 0 |" in markdown


def test_provenance_defaults_to_the_fixture_sentence() -> None:
    """With no note supplied, the report still describes the committed fixtures."""
    markdown = render_eval_markdown(_eval_report(), dataset="demo")
    assert "seeded synthetic fixtures" in markdown
    assert "no real personal data in the fixtures" in markdown


def test_provenance_note_replaces_the_synthetic_claim() -> None:
    """A real dataset must not produce a report asserting it is synthetic.

    The sentence was printed unconditionally, so a real-data eval generated a
    report stating there was no real personal data in it -- a false provenance
    claim, produced automatically, on a tool whose discipline is provenance.
    """
    note = "Ground truth is NCVR ncid across two statewide snapshots; real registration records."
    markdown = render_eval_markdown(_eval_report(), dataset="ncvr", provenance=note)
    assert note in markdown
    # `note` is not promoted to provenance: it describes how truth was built, not
    # where the records came from, and conflating them rewrites every committed report.
    assert "seeded synthetic fixtures" not in markdown
    assert "no real personal data" not in markdown


def test_blank_provenance_falls_back_rather_than_leaving_a_gap() -> None:
    markdown = render_eval_markdown(_eval_report(), dataset="demo", provenance="   ")
    assert "seeded synthetic fixtures" in markdown

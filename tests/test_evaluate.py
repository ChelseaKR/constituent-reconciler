from __future__ import annotations

from constituent_reconciler.decisions import band_pairs
from constituent_reconciler.evaluate import evaluate, truth_pairs, wilson_interval


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

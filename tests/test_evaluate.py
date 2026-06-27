from __future__ import annotations

import pytest

from constituent_reconciler.decisions import band_pairs
from constituent_reconciler.evaluate import cohen_kappa, evaluate, truth_pairs, wilson_interval


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

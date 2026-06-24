"""Eval scoring.

Correctness here is asymmetric, and the metrics reflect that. A false merge joins
two different people and can corrupt a record irreversibly; a missed match leaves
a duplicate. The false-merge rate is therefore the gated metric, reported with a
Wilson confidence interval because the denominator (auto-merged pairs) is small
and a normal-approximation interval would understate the uncertainty.

Ground truth is given as clusters of record ids. All within-cluster pairs are the
true duplicates; everything else is a true non-duplicate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from constituent_reconciler.models import Band, Pair


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Returns ``(low, high)`` clamped to [0, 1]. With no trials the proportion is
    undefined and the widest honest interval (0, 1) is returned.
    """

    if trials <= 0:
        return (0.0, 1.0)
    phat = successes / trials
    z2 = z * z
    denom = 1.0 + z2 / trials
    center = (phat + z2 / (2 * trials)) / denom
    margin = (z / denom) * math.sqrt(phat * (1 - phat) / trials + z2 / (4 * trials * trials))
    return (max(0.0, center - margin), min(1.0, center + margin))


def truth_pairs(clusters: Iterable[Iterable[str]]) -> set[frozenset[str]]:
    """Expand ground-truth clusters into the set of true-duplicate pairs."""

    pairs: set[frozenset[str]] = set()
    for cluster in clusters:
        members = list(cluster)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add(frozenset((members[i], members[j])))
    return pairs


@dataclass(frozen=True)
class EvalReport:
    n_records: int
    n_true_pairs: int
    n_candidate_pairs: int

    n_auto: int
    n_review: int

    false_merges: int
    false_merge_rate: float
    false_merge_ci: tuple[float, float]

    missed: int
    missed_match_rate: float
    missed_match_ci: tuple[float, float]

    precision_auto: float
    recall_auto: float
    precision_coverage: float
    recall_coverage: float

    blocking_misses: int


def evaluate(
    pairs: Iterable[Pair],
    truth_clusters: Iterable[Iterable[str]],
    n_records: int,
) -> EvalReport:
    """Score banded pairs against ground-truth clusters."""

    all_pairs = list(pairs)
    truth = truth_pairs(truth_clusters)

    candidate = {p.key() for p in all_pairs}
    auto = {p.key() for p in all_pairs if p.band is Band.AUTO}
    review = {p.key() for p in all_pairs if p.band is Band.REVIEW}
    coverage = auto | review

    false_merges = len(auto - truth)
    fmr = false_merges / len(auto) if auto else 0.0

    caught_auto = len(auto & truth)
    caught_cov = len(coverage & truth)
    missed = truth - coverage
    missed_rate = len(missed) / len(truth) if truth else 0.0

    return EvalReport(
        n_records=n_records,
        n_true_pairs=len(truth),
        n_candidate_pairs=len(candidate),
        n_auto=len(auto),
        n_review=len(review),
        false_merges=false_merges,
        false_merge_rate=fmr,
        false_merge_ci=wilson_interval(false_merges, len(auto)),
        missed=len(missed),
        missed_match_rate=missed_rate,
        missed_match_ci=wilson_interval(len(missed), len(truth)),
        precision_auto=(caught_auto / len(auto)) if auto else 1.0,
        recall_auto=(caught_auto / len(truth)) if truth else 1.0,
        precision_coverage=(caught_cov / len(coverage)) if coverage else 1.0,
        recall_coverage=(caught_cov / len(truth)) if truth else 1.0,
        blocking_misses=len(truth - candidate),
    )

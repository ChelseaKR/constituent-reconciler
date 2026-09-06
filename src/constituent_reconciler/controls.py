"""Negative controls for the eval, so a broken matcher cannot pass the gate.

``eval`` reports a false-merge rate and a missed-match rate against ground
truth, and the false-merge rate is gated. Nothing checked that those numbers
*can* move. A scorer that returned one constant, or a ground-truth file whose
labels had been shuffled, would still produce a plausible-looking report --
which is the portfolio's most common defect shape: a gate that reports success
without examining anything.

A control is a deliberate sabotage with a known correct answer. Each one below
breaks the pipeline in a specific way and states, in advance, which direction
the metric has to move. If the metric does not move, the metric is not measuring
what the report says it measures, and the control fails.

Three sabotages, and what each rules out:

``shuffled-labels``
    Permutes ground-truth cluster membership across the run's record ids with a
    seeded RNG, keeping the cluster-size profile, and rescores the *same* pairs
    against it. Under a random permutation the expected pairwise precision is
    the base rate -- the chance that an arbitrary pair of records is a true
    duplicate. Precision that stays high against shuffled labels means the
    number is not coming from the labels, so the ground-truth file is not
    actually being read, or is being matched to itself. Rules out: a truth
    fixture that is not wired to the scoring.

``null-matcher``
    Replaces every score with a constant through a real
    :class:`~constituent_reconciler.matching.base.MatcherBackend`
    (:class:`ConstantScoreBackend`) and rebands through the pipeline's own
    :func:`~constituent_reconciler.decisions.band_pairs`. Below the review
    threshold nothing may be surfaced at all; at or above the auto threshold
    every candidate is auto-merged and precision must fall to the base rate.
    A constant scorer that still showed useful recall *and* useful precision
    would mean the bands are not derived from the probabilities. Rules out:
    banding that ignores the score.

``identity``
    Gives every record in a seeded sample an exact twin and asks the real
    matcher to find them. Exact duplicates must auto-merge; anything less means
    the matcher cannot find the easiest possible case, and every other number in
    the report is being produced by something other than matching. Rules out:
    a scorer that never fires.

Two deliberate limits, stated rather than hidden:

* The null-matcher control holds *blocking* fixed. It re-scores the candidate
  pairs the real run produced rather than regenerating candidates, because the
  question it asks is "does the score drive the outcome", not "does blocking
  work" -- and because an all-pairs null matcher is quadratic and cannot run at
  benchmark scale (FEBRL4's 50,000 records are 1.25 billion pairs).
* The identity control runs on a seeded sample capped at
  :data:`IDENTITY_SAMPLE_CAP` records, because it runs the real matcher over
  twice the sample. The report states the sample size and the population it was
  drawn from, so a control that covered 250 of 50,000 records never reads as one
  that covered all of them.

Everything here is deterministic under ``seed``: same seed, same input, same
numbers, byte for byte.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from constituent_reconciler import decisions, matching
from constituent_reconciler.evaluate import evaluate, truth_pairs
from constituent_reconciler.models import Band, Pair, Record

#: Default RNG seed. Pinned so a committed report is reproducible; the CLI
#: exposes it so a reader can re-run under a different one and see the same
#: directions hold.
DEFAULT_SEED = 20260906

#: How many independent permutations the shuffled-labels control averages over.
#: One permutation of a small fixture is noisy: with a dozen auto-merged pairs a
#: single lucky hit swings precision by eight points. The mean over this many is
#: what the bound below is applied to.
SHUFFLE_ROUNDS = 20

#: How many times chance the shuffled-labels precision is allowed to be before
#: the control fails. The expected value under permutation is exactly the base
#: rate; this leaves an order of magnitude of headroom so the control fires on a
#: truth file that is not being read (precision stays near its real value, which
#: is orders of magnitude above chance) and not on small-sample noise.
SHUFFLE_TOLERANCE = 10.0

#: Largest sample the identity control will duplicate and rematch. The real
#: matcher runs over twice this many records, so it is a cost bound, and the
#: number is reported next to the population it came from.
IDENTITY_SAMPLE_CAP = 250


@dataclass(frozen=True)
class ControlOutcome:
    """One sabotage, the direction it was required to move, and what happened."""

    name: str
    #: What a failure of this control would mean about the headline numbers.
    rules_out: str
    #: The asserted direction, in words, written before the number was known.
    expectation: str
    #: The measured result, rendered for a reader.
    observed: str
    passed: bool
    #: Scope this control did not cover. Empty when it covered everything.
    scope: str = ""


@dataclass(frozen=True)
class ControlsReport:
    seed: int
    outcomes: tuple[ControlOutcome, ...]

    @property
    def passed(self) -> bool:
        """False if any control failed. An empty control set is not a pass."""

        return bool(self.outcomes) and all(outcome.passed for outcome in self.outcomes)


class ConstantScoreBackend:
    """A :class:`MatcherBackend` that scores every pair it emits identically.

    This is the null model for record linkage: it has no information. It exists
    to be swapped in for the real backend, so the control exercises the same
    banding and scoring path the pipeline uses rather than a hand-built imitation
    of it.

    ``candidates`` restricts the pairs it emits. Passing the candidate set of a
    real run holds blocking fixed and varies only the score, which is the
    comparison the null-matcher control wants; passing ``None`` emits every pair,
    which is honest but quadratic and only usable on small inputs.

    The :class:`~constituent_reconciler.matching.base.MatcherBackend` contract is
    honored exactly: ``left_id < right_id``, results sorted by
    ``(-probability, left_id, right_id)``, pairs below ``floor`` omitted, and
    fewer than two records returns ``[]``.
    """

    def __init__(
        self, constant: float, *, candidates: Iterable[frozenset[str]] | None = None
    ) -> None:
        self.constant = constant
        self.candidates = None if candidates is None else frozenset(candidates)

    def score_pairs(
        self,
        records: Iterable[Record],
        fields: tuple[str, ...],
        *,
        prior: float,
        floor: float,
    ) -> list[tuple[str, str, float]]:
        ids = sorted({record.unique_id for record in records})
        if len(ids) < 2 or self.constant < floor:
            return []
        emitted: list[tuple[str, str, float]] = []
        for index, left in enumerate(ids):
            for right in ids[index + 1 :]:
                if self.candidates is not None and frozenset((left, right)) not in self.candidates:
                    continue
                emitted.append((left, right, self.constant))
        emitted.sort(key=lambda row: (-row[2], row[0], row[1]))
        return emitted


def _num(value: float | None) -> str:
    """Format a rate for a control's expectation or observation line.

    ``None`` means the rate had no denominator, so there is nothing to compare.
    It is spelled out rather than printed as a number, because every constant a
    control could substitute here reads as a result.
    """

    return "no evidence" if value is None else f"{value:.4f}"


def _base_rate(n_true_pairs: int, n_records: int) -> float:
    """Chance that an arbitrary pair of records is a true duplicate."""

    possible = n_records * (n_records - 1) // 2
    return n_true_pairs / possible if possible else 0.0


def _cluster_sizes(clusters: Iterable[Iterable[str]]) -> list[int]:
    return [len(list(cluster)) for cluster in clusters]


def _permuted_clusters(
    sizes: Sequence[int], population: Sequence[str], rng: random.Random
) -> list[list[str]]:
    """Rebuild clusters of the same sizes from a shuffled population."""

    shuffled = list(population)
    rng.shuffle(shuffled)
    clusters: list[list[str]] = []
    cursor = 0
    for size in sizes:
        if cursor + size > len(shuffled):
            break
        clusters.append(shuffled[cursor : cursor + size])
        cursor += size
    return clusters


def shuffled_labels_control(
    pairs: Sequence[Pair],
    truth_clusters: Iterable[Iterable[str]],
    record_ids: Sequence[str],
    *,
    seed: int = DEFAULT_SEED,
    rounds: int = SHUFFLE_ROUNDS,
    tolerance: float = SHUFFLE_TOLERANCE,
) -> ControlOutcome:
    """Rescore the same pairs against ground truth whose membership was permuted."""

    clusters = [list(cluster) for cluster in truth_clusters]
    sizes = _cluster_sizes(clusters)
    population = sorted(set(record_ids))
    n_true = len(truth_pairs(clusters))
    base_rate = _base_rate(n_true, len(population))
    bound = base_rate * tolerance

    real = evaluate(pairs, clusters, n_records=len(population))
    # noqa: S311 - reproducibility, not secrecy. A seeded Mersenne Twister is the
    # point: the committed report has to be byte-identical on re-run, and a control
    # nobody can reproduce is not evidence.
    rng = random.Random(seed)  # noqa: S311
    observed = [
        evaluate(
            pairs, _permuted_clusters(sizes, population, rng), n_records=len(population)
        ).precision_coverage
        for _ in range(rounds)
    ]
    # A permutation that surfaced nothing has an undefined coverage precision.
    # Averaging it in as a zero would drag the mean under the bound and pass the
    # control on rounds that measured nothing, so undefined rounds are counted
    # and any of them fails the control instead.
    measured = [value for value in observed if value is not None]
    unmeasured = len(observed) - len(measured)
    mean = sum(measured) / len(measured) if measured else None
    worst = max(measured, default=None)
    return ControlOutcome(
        name="shuffled-labels",
        rules_out="a ground-truth file that is not actually being read",
        expectation=(
            f"mean coverage precision over {rounds} seeded permutations falls to at most "
            f"{tolerance:g}x the base rate ({bound:.4f}); real precision is "
            f"{_num(real.precision_coverage)}"
        ),
        observed=(
            f"mean {_num(mean)}, worst permutation {_num(worst)}, "
            f"base rate {base_rate:.4f}"
            + (f", {unmeasured} permutations surfaced nothing to score" if unmeasured else "")
        ),
        passed=unmeasured == 0 and mean is not None and mean <= bound,
    )


def _rate_rose(baseline: float | None, sabotaged: float | None) -> bool:
    """Did the gated rate move up under the sabotage?

    ``sabotaged`` being ``None`` fails: a control that produced no measurement
    has not shown the metric can move. A ``None`` baseline means the real run
    auto-merged nothing, so there is no number to rise above; the sabotage then
    has to produce a rate above zero for the comparison to say anything.
    """

    if sabotaged is None:
        return False
    if baseline is None:
        return sabotaged > 0.0
    return sabotaged > baseline


def null_matcher_control(
    records: Mapping[str, Record],
    pairs: Sequence[Pair],
    truth_clusters: Iterable[Iterable[str]],
    fields: tuple[str, ...],
    *,
    prior: float,
    auto_threshold: float,
    review_threshold: float,
) -> tuple[ControlOutcome, ControlOutcome]:
    """Reband the run's candidate pairs at a constant score, low then high.

    Two constants, because a single one only proves half of it. Below the review
    threshold a constant scorer must surface nothing; at the auto threshold it
    must auto-merge everything and take precision down to the base rate. A
    scorer that looked good under both would mean the bands do not come from the
    probabilities.
    """

    clusters = [list(cluster) for cluster in truth_clusters]
    candidates = [pair.key() for pair in pairs]
    values = list(records.values())
    n_records = len(records)
    base_rate = _base_rate(len(truth_pairs(clusters)), n_records)

    low_constant = max(review_threshold / 2.0, 0.0)
    low_pairs = decisions.band_pairs(
        ConstantScoreBackend(low_constant, candidates=candidates).score_pairs(
            values, fields, prior=prior, floor=0.0
        ),
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
    )
    low = evaluate(low_pairs, clusters, n_records=n_records)
    low_outcome = ControlOutcome(
        name="null-matcher (below review)",
        rules_out="banding that ignores the score",
        expectation=(
            f"every candidate rescored at {low_constant:.4f}, under the review threshold "
            f"{review_threshold:.4f}: auto recall and coverage recall must both be 0"
        ),
        observed=(
            f"auto recall {_num(low.recall_auto)} over {low.n_auto} auto pairs, "
            f"coverage recall {_num(low.recall_coverage)}"
        ),
        passed=low.n_auto == 0 and low.recall_coverage == 0.0,
        scope=(
            "re-scores the candidate pairs the real run produced; blocking is held fixed "
            "and is not what this control tests"
        ),
    )

    high_pairs = decisions.band_pairs(
        ConstantScoreBackend(auto_threshold, candidates=candidates).score_pairs(
            values, fields, prior=prior, floor=0.0
        ),
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
    )
    high = evaluate(high_pairs, clusters, n_records=n_records)
    # Every candidate auto-merges, so precision is the share of the candidate set
    # that is true -- above the all-pairs base rate, because blocking already
    # removed the obviously-unrelated pairs, and far below a working matcher's.
    real = evaluate(pairs, clusters, n_records=n_records)
    high_outcome = ControlOutcome(
        name="null-matcher (at auto threshold)",
        rules_out="a gated false-merge rate that cannot rise",
        expectation=(
            f"every candidate rescored at the auto threshold {auto_threshold:.4f}: all of "
            f"them auto-merge, and the gated false-merge rate must rise above the real "
            f"run's {_num(real.false_merge_rate)}"
        ),
        observed=(
            f"{high.n_auto} auto pairs, false-merge rate {_num(high.false_merge_rate)}, "
            f"auto precision {_num(high.precision_auto)}, all-pairs base rate {base_rate:.4f}"
        ),
        passed=(
            high.n_auto == len(candidates)
            and _rate_rose(real.false_merge_rate, high.false_merge_rate)
        ),
        scope=(
            "re-scores the candidate pairs the real run produced; blocking is held fixed "
            "and is not what this control tests"
        ),
    )
    return low_outcome, high_outcome


def identity_control(
    records: Mapping[str, Record],
    fields: tuple[str, ...],
    *,
    prior: float,
    auto_threshold: float,
    review_threshold: float,
    seed: int = DEFAULT_SEED,
    sample_cap: int = IDENTITY_SAMPLE_CAP,
    backend: matching.MatcherBackend | None = None,
) -> ControlOutcome:
    """Give a seeded sample of records an exact twin and require the twins to merge."""

    population = sorted(records)
    rng = random.Random(seed)  # noqa: S311 - seeded for reproducibility, not secrecy
    size = min(sample_cap, len(population))
    sampled = sorted(rng.sample(population, size)) if size else []

    doubled: list[Record] = []
    twin_pairs: set[frozenset[str]] = set()
    for record_id in sampled:
        original = records[record_id]
        twin_id = f"control-twin:{record_id}"
        doubled.append(original)
        doubled.append(replace(original, unique_id=twin_id))
        twin_pairs.add(frozenset((record_id, twin_id)))

    if len(doubled) < 2:
        return ControlOutcome(
            name="identity",
            rules_out="a scorer that never fires",
            expectation="every record's exact twin is auto-merged",
            observed="not run: fewer than one record to duplicate",
            passed=False,
            scope=f"0 of {len(population)} records",
        )

    engine = backend if backend is not None else matching.default_backend()
    scored = engine.score_pairs(doubled, fields, prior=prior, floor=0.001)
    banded = decisions.band_pairs(
        scored, auto_threshold=auto_threshold, review_threshold=review_threshold
    )
    auto = {pair.key() for pair in banded if pair.band is Band.AUTO}
    found = len(twin_pairs & auto)
    recall = found / len(twin_pairs)
    return ControlOutcome(
        name="identity",
        rules_out="a scorer that never fires",
        expectation="every exact twin pair is auto-merged: recall 1.0000",
        observed=f"recall {recall:.4f} ({found}/{len(twin_pairs)} twin pairs auto-merged)",
        passed=recall == 1.0,
        scope=(
            f"{len(sampled)} of {len(population)} records, sampled under seed {seed} and "
            f"capped at {sample_cap}"
        ),
    )


def run_controls(
    records: Mapping[str, Record],
    pairs: Sequence[Pair],
    truth_clusters: Iterable[Iterable[str]],
    fields: tuple[str, ...],
    *,
    prior: float,
    auto_threshold: float,
    review_threshold: float,
    seed: int = DEFAULT_SEED,
    sample_cap: int = IDENTITY_SAMPLE_CAP,
    backend: matching.MatcherBackend | None = None,
) -> ControlsReport:
    """Run every control against one completed run and collect the outcomes."""

    clusters = [list(cluster) for cluster in truth_clusters]
    outcomes = [
        shuffled_labels_control(pairs, clusters, sorted(records), seed=seed),
        *null_matcher_control(
            records,
            pairs,
            clusters,
            fields,
            prior=prior,
            auto_threshold=auto_threshold,
            review_threshold=review_threshold,
        ),
        identity_control(
            records,
            fields,
            prior=prior,
            auto_threshold=auto_threshold,
            review_threshold=review_threshold,
            seed=seed,
            sample_cap=sample_cap,
            backend=backend,
        ),
    ]
    return ControlsReport(seed=seed, outcomes=tuple(outcomes))

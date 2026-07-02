"""Eval scoring.

Correctness here is asymmetric, and the metrics reflect that. A false merge joins
two different people and can corrupt a record irreversibly; a missed match leaves
a duplicate. The false-merge rate is therefore the gated metric, reported with a
Wilson confidence interval because the denominator (auto-merged pairs) is small
and a normal-approximation interval would understate the uncertainty.

Ground truth is given as clusters of record ids. All within-cluster pairs are
the true duplicates; everything else is a true non-duplicate. The ground truth
may also tag record ids with fixture classes (transliterated names, rural
addresses, and so on); :func:`per_class_metrics` slices the same rates by class
so that error concentrated in one group is reported rather than averaged away.

Extraction is scored separately by ``extraction_metrics``: field-level precision
and recall of the PDF extractor against a hand-labeled fixture set, compared on
normalized values so that formatting differences do not count as errors.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from constituent_reconciler.extract.base import ExtractedField
from constituent_reconciler.models import Band, Pair
from constituent_reconciler.normalize import (
    normalize_dob,
    normalize_email,
    normalize_name,
    normalize_phone,
)

# The tag used for records that carry no class tag at all. Baseline pairs are the
# comparison group the tagged classes are read against.
BASELINE_TAG = "baseline"


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


@dataclass(frozen=True)
class ClassReport:
    """One class's slice of the eval: the same asymmetric rates, per fixture class.

    A class is a set of record ids tagged in the ground truth (for example
    ``name:transliterated`` or ``address:rural``); a pair belongs to the class
    when at least one member carries the tag. Rates follow :class:`EvalReport`
    semantics restricted to the class's pairs, each with a Wilson interval
    because the per-class denominators are small.
    """

    tag: str
    n_true_pairs: int
    n_auto: int
    n_review: int

    false_merges: int
    false_merge_rate: float
    false_merge_ci: tuple[float, float]

    missed: int
    missed_match_rate: float
    missed_match_ci: tuple[float, float]

    recall_auto: float
    recall_coverage: float


def per_class_metrics(
    pairs: Iterable[Pair],
    truth_clusters: Iterable[Iterable[str]],
    classes: Mapping[str, Iterable[str]],
) -> list[ClassReport]:
    """Slice the eval metrics by fixture class.

    ``classes`` maps a record id to the class tags it probes (for example
    ``{"E016": ["name:transliterated"]}``). Records that carry no tag form the
    ``baseline`` class, reported first so the tagged classes can be read against
    it. False merges and misses are judged against the full ground truth; only
    the pair population is restricted to the class.
    """

    all_pairs = list(pairs)
    truth = truth_pairs(truth_clusters)
    auto = {p.key() for p in all_pairs if p.band is Band.AUTO}
    review = {p.key() for p in all_pairs if p.band is Band.REVIEW}
    coverage = auto | review

    members_by_tag: dict[str, set[str]] = {}
    for record_id, tags in classes.items():
        for tag in tags:
            members_by_tag.setdefault(tag, set()).add(record_id)
    all_tagged: set[str] = set().union(*members_by_tag.values()) if members_by_tag else set()

    def slice_for(tag: str, touches: set[str], *, complement: bool) -> ClassReport:
        def in_class(pair: frozenset[str]) -> bool:
            hit = bool(pair & touches)
            return not hit if complement else hit

        auto_t = {p for p in auto if in_class(p)}
        review_t = {p for p in review if in_class(p)}
        truth_t = {p for p in truth if in_class(p)}
        coverage_t = auto_t | review_t

        false_merges = len(auto_t - truth)
        missed = len(truth_t - coverage)
        caught_auto = len(auto_t & truth_t)
        caught_cov = len(coverage_t & truth_t)
        return ClassReport(
            tag=tag,
            n_true_pairs=len(truth_t),
            n_auto=len(auto_t),
            n_review=len(review_t),
            false_merges=false_merges,
            false_merge_rate=(false_merges / len(auto_t)) if auto_t else 0.0,
            false_merge_ci=wilson_interval(false_merges, len(auto_t)),
            missed=missed,
            missed_match_rate=(missed / len(truth_t)) if truth_t else 0.0,
            missed_match_ci=wilson_interval(missed, len(truth_t)),
            recall_auto=(caught_auto / len(truth_t)) if truth_t else 1.0,
            recall_coverage=(caught_cov / len(truth_t)) if truth_t else 1.0,
        )

    reports = [slice_for(BASELINE_TAG, all_tagged, complement=True)]
    for tag in sorted(members_by_tag):
        reports.append(slice_for(tag, members_by_tag[tag], complement=False))
    return reports


def cohen_kappa(predicted: list[bool], actual: list[bool]) -> float:
    """Cohen's kappa agreement coefficient between two binary label sequences.

    Used to calibrate extraction confidence against human labels: set
    ``predicted[i]`` to True when the extractor's confidence for record i is
    above the threshold, and ``actual[i]`` to True when a human annotator
    confirmed the field was correctly extracted. A kappa below 0.6 signals
    that confidence scores are not tracking accuracy well enough to trust the
    gate.

    Returns 1.0 for perfect agreement, 0.0 for chance-level agreement, and
    a negative value for below-chance agreement. Returns 0.0 if kappa is
    undefined (all labels on one side).

    Raises ``ValueError`` if the sequences are empty or differ in length.
    """
    n = len(predicted)
    if n == 0:
        raise ValueError("predicted and actual must be non-empty")
    if len(actual) != n:
        raise ValueError("predicted and actual must have equal length")

    p_agree = sum(p == a for p, a in zip(predicted, actual, strict=True)) / n
    p_pred_pos = sum(predicted) / n
    p_actual_pos = sum(actual) / n
    p_expected = p_pred_pos * p_actual_pos + (1 - p_pred_pos) * (1 - p_actual_pos)

    if p_expected >= 1.0:
        return 0.0

    return (p_agree - p_expected) / (1.0 - p_expected)


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


# Canonical single-argument normalizers for the extraction comparison, keyed by
# canonical field name. These are the same normalizers the matching pipeline
# applies, so the eval judges the extractor on the value the matcher would see,
# not on incidental formatting.
_EXTRACTION_NORMALIZERS: dict[str, Callable[[str], str]] = {
    "first_name": normalize_name,
    "last_name": normalize_name,
    "dob": normalize_dob,
    "email": normalize_email,
    "phone": normalize_phone,
}


def normalize_extracted_value(field_name: str, value: str) -> str:
    """Canonical comparison form of an extracted or labeled field value.

    Known fields go through the pipeline's own normalizer, so ``(415) 555-0100``
    and ``4155550100`` compare equal for ``phone`` and ``03/09/1988`` equals
    ``1988-03-09`` for ``dob``. Unknown fields, and values the canonical
    normalizer cannot parse (it returns the empty string), fall back to a
    whitespace-collapsed casefold of the raw value, so two identical raw
    strings still compare equal instead of both collapsing to ``""``.
    """

    normalizer = _EXTRACTION_NORMALIZERS.get(field_name)
    if normalizer is not None:
        normalized = normalizer(value)
        if normalized:
            return normalized
    return " ".join(value.split()).casefold()


@dataclass(frozen=True)
class FieldScore:
    """Per-field extraction counts, aggregated across documents."""

    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 1.0


@dataclass(frozen=True)
class ExtractionReport:
    """Field-level extraction precision and recall against labeled truth."""

    n_docs: int
    n_truth_fields: int
    n_predicted_fields: int

    tp: int
    fp: int
    fn: int

    precision: float
    recall: float
    precision_ci: tuple[float, float]
    recall_ci: tuple[float, float]

    per_field: Mapping[str, FieldScore]


def extraction_metrics(
    predicted: Mapping[str, Sequence[ExtractedField]],
    truth: Mapping[str, Sequence[Mapping[str, str]]],
) -> ExtractionReport:
    """Score extracted fields against hand-labeled ground truth, per document.

    ``predicted`` maps a document name to the fields the extractor produced for
    it; ``truth`` maps the same names to labels in the ``labels.json`` shape,
    ``[{"field_name": ..., "value": ...}, ...]``.

    Within a document, a prediction is a true positive when its ``(field_name,
    normalized value)`` matches a not-yet-claimed truth label; each label can be
    claimed once, so a duplicated prediction counts once as a true positive and
    once as a false positive. Unmatched predictions are false positives and
    unmatched labels are false negatives. Documents present on only one side
    still count: predictions without labels are all false positives, labels
    without predictions are all false negatives. Precision and recall follow
    the convention above of 1.0 on an empty denominator, with Wilson intervals
    (which return the widest honest (0, 1) in that case).
    """

    tp_by_field: Counter[str] = Counter()
    fp_by_field: Counter[str] = Counter()
    fn_by_field: Counter[str] = Counter()

    docs = set(predicted) | set(truth)
    for doc in docs:
        unclaimed: Counter[tuple[str, str]] = Counter()
        for label in truth.get(doc, ()):
            field_name = str(label["field_name"])
            key = (field_name, normalize_extracted_value(field_name, str(label["value"])))
            unclaimed[key] += 1
        for pred in predicted.get(doc, ()):
            key = (pred.field_name, normalize_extracted_value(pred.field_name, pred.value))
            if unclaimed[key] > 0:
                unclaimed[key] -= 1
                tp_by_field[pred.field_name] += 1
            else:
                fp_by_field[pred.field_name] += 1
        for (field_name, _), count in unclaimed.items():
            fn_by_field[field_name] += count

    tp = sum(tp_by_field.values())
    fp = sum(fp_by_field.values())
    fn = sum(fn_by_field.values())
    field_names = sorted(set(tp_by_field) | set(fp_by_field) | set(fn_by_field))

    return ExtractionReport(
        n_docs=len(docs),
        n_truth_fields=tp + fn,
        n_predicted_fields=tp + fp,
        tp=tp,
        fp=fp,
        fn=fn,
        precision=tp / (tp + fp) if tp + fp else 1.0,
        recall=tp / (tp + fn) if tp + fn else 1.0,
        precision_ci=wilson_interval(tp, tp + fp),
        recall_ci=wilson_interval(tp, tp + fn),
        per_field={
            name: FieldScore(tp=tp_by_field[name], fp=fp_by_field[name], fn=fn_by_field[name])
            for name in field_names
        },
    )

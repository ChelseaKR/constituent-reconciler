"""What an organization's own reviewers imply about its thresholds.

The defaults are 0.97 auto and 0.80 review, pre-tuned so an adopter needs no
labeled pairs. An organization that has *done* the reviewing has labels anyway
-- its reviewers' verdicts -- and until now nothing let it see what they imply.
``constituent-reconcile sweep-thresholds`` treats those verdicts as labels, replays
the probabilities the run already committed against a grid of (auto, review)
thresholds, and reports what each setting would have done.

The verb is not called ``calibrate``. ``review/calibration.py`` already owns
that word for a different mechanism (planted known-answer pairs and the
fail-closed kappa gate on reviewer agreement), and one name for two gates is
how a doc becomes wrong.

FOUR THINGS THIS MODULE WILL NOT DO.

**It will not recommend weakening the gate.** A threshold sweep is,
structurally, a tool for finding the setting that makes your numbers look best.
Every row whose implied false-merge rate is not a measured value at or under the
gate is marked ineligible, using ``evaluate.gate_holds`` rather than a
comparison, so a row that auto-merged nothing -- whose rate is undefined, not
zero -- can never be recommended. ``--suggest`` prints the most conservative
eligible row and applies nothing.

**It will not compute a rate over an unlabeled denominator.** Only pairs a human
decided carry a label. A false-merge rate whose numerator counts labeled pairs
and whose denominator counts every auto-merged pair would be a wrong number
wearing a real one's clothes, so both are counted over the labeled set alone and
the report says so.

**It will not report a review load it cannot observe.** The run's artifacts hold
probabilities only for pairs at or above its own review threshold; everything
below was dropped and left no record. So a grid row proposing a *lower* review
threshold cannot know how many further pairs would enter review, and its review
load is reported as unmeasured rather than as a small number.

**It will not run on a handful of decisions.** Below
:data:`MINIMUM_DECIDED_PAIRS` the sweep refuses outright. A row computed from
three verdicts has a Wilson interval spanning most of the range, and printing
its point estimate beside a row computed from four hundred is how an
organization retunes its gate on noise.

Nothing is re-scored. Re-scoring would recompute probabilities from the current
sources, which may no longer be the ones the reviewer saw, silently relabeling
their verdicts against evidence they never read. The probabilities here are the
ones the run committed and the reviewer worked from.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from constituent_reconciler.config import Recipe
from constituent_reconciler.evaluate import (
    UNMEASURED,
    format_rate,
    gate_holds,
    rate,
    wilson_interval,
)
from constituent_reconciler.policy import Policy
from constituent_reconciler.schema import SWEEP_SCHEMA_VERSION
from constituent_reconciler.suppression import suppress_cells

SWEEP_REPORT_FILENAME = "calibration_report.md"
SWEEP_JSON_FILENAME = "calibration_report.json"

REVIEW_QUEUE_FILE = "review_queue.csv"
AUTO_MERGES_FILE = "auto_merges.json"

APPROVED = "approved"
REJECTED = "rejected"

#: The fewest human verdicts this sweep will compute a grid from.
#:
#: Chosen against the Wilson interval rather than by feel: with zero false
#: merges out of thirty labeled auto-merges the 95% upper bound is still about
#: 11%, which is honest but wide. Below thirty the interval covers so much of
#: the range that a point estimate is decoration, and a decorated point estimate
#: beside a row computed from four hundred verdicts is precisely how a gate gets
#: retuned on noise. There is deliberately no flag to lower it: the refusal is
#: the feature.
MINIMUM_DECIDED_PAIRS = 30

#: The default grid. Includes the shipped defaults (0.97 / 0.80) and the
#: recipe's own row is always added, so the report always contains the setting
#: actually in force.
DEFAULT_AUTO_GRID: tuple[float, ...] = (0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99)
DEFAULT_REVIEW_GRID: tuple[float, ...] = (0.70, 0.75, 0.80, 0.85, 0.90)


class SweepError(ValueError):
    """The sweep refused, fail-closed. No report file is written on this path."""


@dataclass(frozen=True)
class Pair:
    left: str
    right: str

    @classmethod
    def of(cls, left: str, right: str) -> Pair:
        return cls(*sorted((left, right)))


@dataclass(frozen=True)
class GridRow:
    """One (auto, review) setting and what the reviewers' verdicts imply about it."""

    auto: float
    review: float
    is_current: bool
    labeled_auto: int
    false_merges: int
    false_merge_rate: float | None
    false_merge_ci: tuple[float, float]
    labeled_dropped: int
    missed_matches: int
    missed_match_rate: float | None
    missed_match_ci: tuple[float, float]
    #: ``None`` when this row's review threshold is below the run's, so the
    #: population that would enter review was never recorded.
    review_load: int | None
    eligible: bool


@dataclass(frozen=True)
class SweepReport:
    decided_pairs: int
    approved: int
    rejected: int
    gate: float
    observable_floor: float
    current_auto: float
    current_review: float
    rows: tuple[GridRow, ...]

    @property
    def current_row(self) -> GridRow:
        for row in self.rows:
            if row.is_current:
                return row
        # pragma: no cover - unreachable while load_recipe enforces review < auto,
        # which is what puts the recipe's own row inside the grid. Kept as a raise
        # rather than a returned None so a future grid change cannot make the
        # report silently omit the setting actually in force.
        raise SweepError("the grid lost the recipe's own row")

    @property
    def suggestion(self) -> GridRow | None:
        """The most conservative eligible row, or ``None`` when none is.

        Most conservative means the highest auto threshold, then the highest
        review threshold: the setting that merges least on its own and asks a
        person most often. ``None`` is a real answer and is rendered as one --
        it means these verdicts support no setting inside the gate, which is
        information, not a reason to relax the gate.
        """

        eligible = [row for row in self.rows if row.eligible]
        if not eligible:
            return None
        return max(eligible, key=lambda row: (row.auto, row.review))


def _load_json(path: Path, what: str) -> dict[str, object]:
    if not path.is_file():
        raise SweepError(f"{what} not found: {path}")
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SweepError(f"{what} could not be read ({path}): {error}") from error
    if not isinstance(data, dict):
        raise SweepError(f"{what} must be a JSON object: {path}")
    return {str(key): value for key, value in data.items()}


def read_decisions(path: Path) -> dict[Pair, str]:
    """Reviewer verdicts as labels: pair ids and verdicts only, never a value."""

    data = _load_json(path, "decisions file")
    verdicts: dict[Pair, str] = {}
    for verdict in (APPROVED, REJECTED):
        raw = data.get(verdict, [])
        if not isinstance(raw, list):
            raise SweepError(
                f"the decisions file's {verdict!r} section is not a list ({path}); "
                "the sweep will not guess how many verdicts it should have read"
            )
        for entry in raw:
            if isinstance(entry, list) and len(entry) == 2:
                verdicts[Pair.of(str(entry[0]), str(entry[1]))] = verdict
    return verdicts


def _probability(raw: object, where: Path) -> float:
    try:
        return float(str(raw))
    except (TypeError, ValueError) as error:
        raise SweepError(
            f"{where} carries a pair whose probability is not a number ({raw!r}); "
            "an unreadable probability is not a low one"
        ) from error


def read_probabilities(run_dir: Path) -> dict[Pair, float]:
    """Every pair probability the run committed, from both banded artifacts.

    The review queue holds the pairs a person saw; ``auto_merges.json`` holds
    the ones the matcher merged alone. Together they are every pair at or above
    the run's review threshold. Nothing below it was recorded, which is what
    :func:`sweep_thresholds` turns into an unmeasured review load rather than a
    small number.
    """

    queue = run_dir / REVIEW_QUEUE_FILE
    if not queue.is_file():
        raise SweepError(
            f"the run has no {REVIEW_QUEUE_FILE} at {queue}; an empty queue is written "
            "as a header-only file, so a missing one means the artifact is gone rather "
            "than that no pair needed review"
        )
    probabilities: dict[Pair, float] = {}
    with queue.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            left, right = (row.get("left") or "").strip(), (row.get("right") or "").strip()
            if left and right:
                probabilities[Pair.of(left, right)] = _probability(row.get("probability"), queue)

    auto_path = run_dir / AUTO_MERGES_FILE
    if auto_path.is_file():
        data = _load_json(auto_path, "auto-merge record")
        raw = data.get("pairs")
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                left, right = str(entry.get("left", "")), str(entry.get("right", ""))
                if left and right:
                    probabilities[Pair.of(left, right)] = _probability(
                        entry.get("probability"), auto_path
                    )
    return probabilities


def _grid(recipe: Recipe) -> list[tuple[float, float]]:
    autos = sorted({*DEFAULT_AUTO_GRID, recipe.auto_threshold})
    reviews = sorted({*DEFAULT_REVIEW_GRID, recipe.review_threshold})
    return [(auto, review) for auto in autos for review in reviews if review < auto]


def _row(
    auto: float,
    review: float,
    *,
    labeled: dict[Pair, tuple[float, str]],
    population: list[float],
    observable_floor: float,
    gate: float,
    is_current: bool,
) -> GridRow:
    """One grid row, computed over the labeled set for every rate it reports."""

    labeled_auto = 0
    false_merges = 0
    labeled_dropped = 0
    missed_matches = 0
    for probability, verdict in labeled.values():
        if probability >= auto:
            labeled_auto += 1
            if verdict == REJECTED:
                # A person looked at these two records and said they are
                # different people. This threshold merges them without asking.
                false_merges += 1
        elif probability < review:
            labeled_dropped += 1
            if verdict == APPROVED:
                # A person said these are the same person. This threshold drops
                # the pair silently: no merge, and nobody is asked.
                missed_matches += 1

    false_merge_rate = rate(false_merges, labeled_auto)
    missed_match_rate = rate(missed_matches, labeled_dropped)
    # A review threshold below the run's proposes admitting pairs the run never
    # recorded. Counting only the ones it did record would report a review load
    # that is certainly too low, on the axis an operator reads as "cost".
    review_load = (
        sum(1 for p in population if review <= p < auto) if review >= observable_floor else None
    )
    return GridRow(
        auto=auto,
        review=review,
        is_current=is_current,
        labeled_auto=labeled_auto,
        false_merges=false_merges,
        false_merge_rate=false_merge_rate,
        false_merge_ci=wilson_interval(false_merges, labeled_auto),
        labeled_dropped=labeled_dropped,
        missed_matches=missed_matches,
        missed_match_rate=missed_match_rate,
        missed_match_ci=wilson_interval(missed_matches, labeled_dropped),
        review_load=review_load,
        # gate_holds, not <=: a row that auto-merged nothing has an UNDEFINED
        # false-merge rate, and undefined is not zero. Marking it eligible would
        # let --suggest recommend a setting on no evidence at all.
        eligible=gate_holds(false_merge_rate, gate),
    )


def sweep_thresholds(
    recipe: Recipe,
    *,
    decisions_path: Path,
    run_dir: Path | None = None,
    gate: float = 0.0,
) -> SweepReport:
    """Score a grid of thresholds against this organization's own verdicts.

    ``run_dir`` defaults to the decisions file's own directory, which is where
    ``run`` writes both banded artifacts.

    Refuses, fail-closed, on a missing or unreadable decisions file or review
    queue, a probability that is not a number, a decided pair the run's
    artifacts do not account for, and fewer than
    :data:`MINIMUM_DECIDED_PAIRS` verdicts.
    """

    resolved_run_dir = run_dir if run_dir is not None else decisions_path.parent
    verdicts = read_decisions(decisions_path)
    probabilities = read_probabilities(resolved_run_dir)

    missing = sorted(f"{pair.left}+{pair.right}" for pair in verdicts if pair not in probabilities)
    if missing:
        raise SweepError(
            f"{len(missing)} decided pair(s) have no probability in this run's artifacts "
            f"(first: {missing[0]}). The verdicts and the run do not describe the same "
            "batch; dropping those pairs would shrink the labeled set and quietly "
            "improve every row. Point --decisions at the run that produced them."
        )
    if len(verdicts) < MINIMUM_DECIDED_PAIRS:
        raise SweepError(
            f"this sweep needs at least {MINIMUM_DECIDED_PAIRS} decided pairs and found "
            f"{len(verdicts)}. Below that a Wilson interval on the false-merge rate "
            "spans most of the range, so the grid would print point estimates it cannot "
            "support. Review more pairs before retuning anything."
        )

    labeled = {pair: (probabilities[pair], verdict) for pair, verdict in verdicts.items()}
    population = sorted(probabilities.values())
    # Everything below the run's review threshold was dropped without a record,
    # so it is the floor of what any row can honestly say about review load.
    observable_floor = recipe.review_threshold
    rows = tuple(
        _row(
            auto,
            review,
            labeled=labeled,
            population=population,
            observable_floor=observable_floor,
            gate=gate,
            is_current=auto == recipe.auto_threshold and review == recipe.review_threshold,
        )
        for auto, review in _grid(recipe)
    )
    return SweepReport(
        decided_pairs=len(verdicts),
        approved=sum(1 for verdict in verdicts.values() if verdict == APPROVED),
        rejected=sum(1 for verdict in verdicts.values() if verdict == REJECTED),
        gate=gate,
        observable_floor=observable_floor,
        current_auto=recipe.auto_threshold,
        current_review=recipe.review_threshold,
        rows=rows,
    )


def _counts(report: SweepReport, policy: Policy | None) -> dict[str, int | str]:
    counts = {
        "decided_pairs": report.decided_pairs,
        "approved": report.approved,
        "rejected": report.rejected,
    }
    if policy is not None and policy.aggregate_export:
        return dict(suppress_cells(counts, threshold=policy.suppression_threshold))
    return dict(counts)


def sweep_payload(report: SweepReport, *, policy: Policy | None = None) -> dict[str, object]:
    """The JSON report. Counts and rates only; no pair id and no field value."""

    suggestion = report.suggestion
    return {
        "sweep_schema": SWEEP_SCHEMA_VERSION,
        "gate": report.gate,
        "minimum_decided_pairs": MINIMUM_DECIDED_PAIRS,
        "counts": _counts(report, policy),
        "suppression": {
            "applied": policy is not None and policy.aggregate_export,
            "threshold": policy.suppression_threshold if policy is not None else None,
        },
        "current": {"auto": report.current_auto, "review": report.current_review},
        "observable_review_floor": report.observable_floor,
        # An explicit null, never an omitted key and never a "safest" row: no
        # eligible row means these verdicts support no setting inside the gate.
        "suggested": (
            None if suggestion is None else {"auto": suggestion.auto, "review": suggestion.review}
        ),
        "rows": [
            {
                "auto": row.auto,
                "review": row.review,
                "current": row.is_current,
                "labeled_auto_merges": row.labeled_auto,
                "false_merges": row.false_merges,
                "false_merge_rate": row.false_merge_rate,
                "false_merge_ci": list(row.false_merge_ci),
                "labeled_dropped": row.labeled_dropped,
                "missed_matches": row.missed_matches,
                "missed_match_rate": row.missed_match_rate,
                "missed_match_ci": list(row.missed_match_ci),
                "review_load": row.review_load,
                "eligible": row.eligible,
            }
            for row in report.rows
        ],
    }


def render_sweep(report: SweepReport) -> str:
    """The Markdown report. Every unmeasured cell says so in words."""

    lines = [
        "# Threshold sweep against your reviewers' decisions",
        "",
        f"{report.decided_pairs} decided pair(s): {report.approved} approved, "
        f"{report.rejected} rejected.",
        "",
        "Every rate below is computed over the pairs a person actually decided, never "
        "over the whole run. A pair nobody reviewed carries no label, so it can neither "
        "confirm nor contradict a threshold.",
        "",
        f"A row is eligible only when its false-merge rate is a measured value at or "
        f"under the gate ({format_rate(report.gate)}). A row that auto-merges nothing "
        "has no measured rate and is therefore not eligible: undefined is not zero.",
        "",
        f"Review load is unmeasured below a review threshold of {report.observable_floor}. "
        "The run recorded no pair beneath its own review threshold, so how many further "
        "pairs would enter review at a lower setting is not knowable from these files.",
        "",
        "| auto | review | labeled auto-merges | false merges | false-merge rate (95% CI) "
        "| missed matches | review load | eligible |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report.rows:
        marker = " (current)" if row.is_current else ""
        ci = f"{format_rate(row.false_merge_ci[0])}-{format_rate(row.false_merge_ci[1])}"
        load = UNMEASURED if row.review_load is None else str(row.review_load)
        lines.append(
            f"| {row.auto}{marker} | {row.review} | {row.labeled_auto} | {row.false_merges} "
            f"| {format_rate(row.false_merge_rate)} ({ci}) | {row.missed_matches} | {load} "
            f"| {'yes' if row.eligible else 'no'} |"
        )
    lines.append("")
    suggestion = report.suggestion
    if suggestion is None:
        lines += [
            "**No row is eligible.** These verdicts support no setting inside the gate. "
            "That is a result, not a reason to raise the gate: it says the labeled "
            "evidence does not yet show any threshold meeting the promise this project "
            "makes. Review more pairs, or investigate the false merges the current "
            "setting produced.",
            "",
        ]
    else:
        lines += [
            f"**Most conservative eligible row: auto {suggestion.auto}, review "
            f"{suggestion.review}.** Nothing has been applied. This report never edits a "
            "recipe; changing a threshold is a decision a person makes.",
            "",
        ]
    return "\n".join(lines)


def write_sweep(
    report: SweepReport, out_dir: Path, *, policy: Policy | None = None
) -> tuple[Path, Path]:
    """Write the Markdown and JSON reports; return both paths."""

    out_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = out_dir / SWEEP_REPORT_FILENAME
    markdown_path.write_text(render_sweep(report), encoding="utf-8")
    json_path = out_dir / SWEEP_JSON_FILENAME
    json_path.write_text(
        json.dumps(sweep_payload(report, policy=policy), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return markdown_path, json_path

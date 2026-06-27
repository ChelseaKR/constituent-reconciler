"""Aggregate, suppression-aware summaries for external sharing.

Under the DV pack a provider may share only non-personally-identifying data in
the aggregate (34 U.S.C. § 12291(b)(2)(D)(i)(I)). This module turns resolved
records into counts with no field values and applies small-cell suppression
modeled on the U.S. CMS Cell Size Suppression Policy: a cell holding a count of
1 through 10 is suppressed, and a true zero is preserved (a zero reveals no one).

Complementary suppression matters as much as the primary rule: if a breakdown
publishes a total and suppresses exactly one cell, that cell is recoverable by
subtraction. So within a breakdown, if any cell is suppressed, at least two cells
are suppressed; when only one cell falls below the threshold, the next-smallest
positive cell is suppressed as well. This is the standard secondary-suppression
step. It does not defend against cross-tabulation attacks that correlate several
breakdowns; that is out of scope and is stated as a limitation in the docs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from constituent_reconciler.models import GoldenRecord
from constituent_reconciler.policy import DEFAULT_SUPPRESSION_THRESHOLD

SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class Breakdown:
    """One labeled set of category counts, after suppression.

    ``cells`` maps each category to either an integer count or the string
    ``"suppressed"``. A true zero stays ``0``; a small nonzero count becomes
    ``"suppressed"``.
    """

    name: str
    cells: dict[str, int | str]


def suppress_cells(
    counts: Mapping[str, int],
    *,
    threshold: int = DEFAULT_SUPPRESSION_THRESHOLD,
) -> dict[str, int | str]:
    """Apply CMS-style primary and complementary small-cell suppression.

    A count of ``1..threshold-1`` is suppressed. A zero is preserved. If exactly
    one cell is suppressed by the primary rule, the smallest remaining positive
    cell is also suppressed so the first cannot be recovered by subtraction from
    a published total.
    """

    result: dict[str, int | str] = {}
    primary_suppressed: list[str] = []
    for key, value in counts.items():
        if value == 0:
            result[key] = 0
        elif value < threshold:
            result[key] = SUPPRESSED
            primary_suppressed.append(key)
        else:
            result[key] = value

    if len(primary_suppressed) == 1:
        # One suppressed cell is recoverable from a total. Suppress the smallest
        # remaining positive cell as the complementary cell.
        candidates = [
            (value, key)
            for key, value in counts.items()
            if isinstance(result[key], int) and result[key] != 0
        ]
        if candidates:
            _, victim = min(candidates)
            result[victim] = SUPPRESSED

    return result


def _consent_counts(records: Iterable[GoldenRecord]) -> dict[str, int]:
    granted = sum(1 for r in records if r.consent)
    withheld = sum(1 for r in records if not r.consent)
    return {"granted": granted, "withheld": withheld}


def _resolution_counts(records: Iterable[GoldenRecord]) -> dict[str, int]:
    merged = sum(1 for r in records if len(r.members) > 1)
    singleton = sum(1 for r in records if len(r.members) == 1)
    return {"merged": merged, "singleton": singleton}


@dataclass(frozen=True)
class AggregateSummary:
    """Non-identifying aggregate over resolved records, suppression applied.

    ``total`` is the count of resolved records. ``breakdowns`` carry the
    suppressed category counts. No field value, id, or member list appears here;
    the summary is the only artifact the DV pack considers shareable.
    """

    total: int
    breakdowns: tuple[Breakdown, ...]


def aggregate_summary(
    records: Iterable[GoldenRecord],
    *,
    threshold: int = DEFAULT_SUPPRESSION_THRESHOLD,
) -> AggregateSummary:
    """Build a suppressed aggregate summary from resolved records."""

    record_list = list(records)
    breakdowns = (
        Breakdown("consent", suppress_cells(_consent_counts(record_list), threshold=threshold)),
        Breakdown(
            "resolution", suppress_cells(_resolution_counts(record_list), threshold=threshold)
        ),
    )
    return AggregateSummary(total=len(record_list), breakdowns=breakdowns)


def render_summary(summary: AggregateSummary) -> str:
    """Render the aggregate summary as plain text for the run output."""

    lines = [
        "aggregate summary (non-identifying, small cells suppressed):",
        f"  resolved records: {summary.total}",
    ]
    for breakdown in summary.breakdowns:
        cells = ", ".join(f"{k}={v}" for k, v in breakdown.cells.items())
        lines.append(f"  {breakdown.name}: {cells}")
    return "\n".join(lines)

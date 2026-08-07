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

**The published total is covered by the same discipline (issue #94).** Every
breakdown here is an exhaustive partition of the resolved-record set: every
record lands in exactly one of its categories, so a breakdown's cells always
sum to the report's total. Complementary suppression protects a breakdown
*from itself*. It guarantees at least two cells are hidden whenever any are, so
the hidden ones can't be solved for from the *other cells in that breakdown*.
It cannot protect against the total published *alongside* the breakdown,
because `suppress_cells`'s victim search is correctly barred from choosing a
true zero as the complementary victim (a zero must never be marked hidden; it
reveals no one, and marking it suppressed would itself be a lie). So when every
other cell in a breakdown is a true zero, there is no valid second cell to hide,
and exactly one cell, never two, ends up suppressed. In that specific shape,
`total - (every visible cell)` equals the one hidden cell exactly. `_safe_total`
below is what closes that: whenever any breakdown ends up with exactly one
suppressed cell, the report's total is suppressed too, not just the cell.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from constituent_reconciler.models import CANONICAL_FIELDS, GoldenRecord
from constituent_reconciler.policy import DEFAULT_SUPPRESSION_THRESHOLD, PolicyViolation

SUPPRESSED = "suppressed"

# The profile name stamped into comparable_report.json. "CoC-shaped" means the
# report carries what a Continuum of Care aggregate submission carries: a period
# label, category counts, and nothing about any individual.
COMPARABLE_PROFILE = "coc-comparable"

# Records whose configured breakdown field is empty are counted under this
# category rather than dropped: "data not collected" is itself a real category
# in comparable-database reporting, and dropping the records would make the
# published cells disagree with the published total.
MISSING_CATEGORY = "(missing)"


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
        # else: nothing left to hide with (every other cell is a true zero, and a
        # zero must never be the victim). This breakdown ends with exactly one
        # suppressed cell, the shape `_safe_total` exists to catch, below.

    return result


def _exactly_one_cell_suppressed(breakdown: Breakdown) -> bool:
    """Whether ``breakdown`` ended with a single hidden cell.

    Only possible when every *other* cell was a true zero: :func:`suppress_cells`
    never chooses one as the complementary victim, on purpose, so it has nothing
    to pair the lone suppressed cell with. In that shape the hidden cell equals
    this breakdown's share of the report total minus its own visible cells, so
    the report's *total*, not this breakdown, is the thing that still needs to
    be hidden.
    """
    return sum(1 for value in breakdown.cells.values() if value == SUPPRESSED) == 1


def _safe_total(raw_total: int, breakdowns: Sequence[Breakdown]) -> int | str:
    """The report total, suppressed if publishing it raw would hand back a
    breakdown's one hidden cell.

    Every breakdown here partitions the same resolved-record set, so each one's
    cells sum to ``raw_total``. If any breakdown ends up with exactly one
    suppressed cell, that cell equals ``raw_total`` minus its visible cells, an
    exact recovery, not merely a narrowed range, so publishing ``raw_total`` at
    all would undo that breakdown's own suppression. Two or more suppressed
    cells in a breakdown are not solvable this way (a single equation, multiple
    unknowns), which is the existing, documented complementary-suppression
    guarantee; this only adds the one case that guarantee cannot reach.
    """
    if any(_exactly_one_cell_suppressed(breakdown) for breakdown in breakdowns):
        return SUPPRESSED
    return raw_total


def _consent_counts(records: Iterable[GoldenRecord]) -> dict[str, int]:
    # Callers pass the already-gated exportable set, so this is normally all
    # "granted"; it is computed from the lifecycle rather than assumed so the
    # count stays honest if a caller ever passes an ungated set.
    today = date.today()
    granted = sum(1 for r in records if r.consent.is_active(as_of=today))
    withheld = sum(1 for r in records if not r.consent.is_active(as_of=today))
    return {"granted": granted, "withheld": withheld}


def _resolution_counts(records: Iterable[GoldenRecord]) -> dict[str, int]:
    merged = sum(1 for r in records if len(r.members) > 1)
    singleton = sum(1 for r in records if len(r.members) == 1)
    return {"merged": merged, "singleton": singleton}


@dataclass(frozen=True)
class AggregateSummary:
    """Non-identifying aggregate over resolved records, suppression applied.

    ``total`` is the count of resolved records, or the string ``"suppressed"``
    when publishing the real count would hand back a breakdown's one hidden
    cell by subtraction (see :func:`_safe_total`). ``breakdowns`` carry the
    suppressed category counts. No field value, id, or member list appears here;
    the summary is the only artifact the DV pack considers shareable.
    """

    total: int | str
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
    return AggregateSummary(total=_safe_total(len(record_list), breakdowns), breakdowns=breakdowns)


def render_summary(summary: AggregateSummary) -> str:
    """Render the aggregate summary as plain text for the run output."""

    lines = [
        "aggregate summary (non-identifying, small cells suppressed):",
        f"  resolved records: {summary.total}",
    ]
    if summary.total == SUPPRESSED:
        lines.append(
            "    (the total itself is suppressed: publishing it would let one of the "
            "hidden cells below be recovered by subtraction)"
        )
    for breakdown in summary.breakdowns:
        cells = ", ".join(f"{k}={v}" for k, v in breakdown.cells.items())
        lines.append(f"  {breakdown.name}: {cells}")
    return "\n".join(lines)


def ensure_non_identifying(breakdown_fields: Iterable[str]) -> None:
    """Refuse, fail-closed, a breakdown over an identifying field.

    Every canonical field the pipeline matches on (name, DOB, email, phone,
    address) identifies a person, so its raw values must never become category
    labels in a shareable report — a frequency table of last names is a
    disclosure, however large each count. A comparable report may break down
    only over non-identifying categorical fields (a program, a county); the
    tool cannot verify that judgment, but it can refuse the fields it knows
    are identifying.
    """

    identifying = sorted(set(breakdown_fields) & set(CANONICAL_FIELDS))
    if identifying:
        raise PolicyViolation(
            f"comparable breakdown field(s) {', '.join(identifying)} are identifying; "
            "a comparable report may break down only over non-identifying "
            "categorical fields"
        )


def _field_counts(records: Iterable[GoldenRecord], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = record.fields.get(field_name, "").strip()
        category = value if value else MISSING_CATEGORY
        counts[category] = counts.get(category, 0) + 1
    return counts


@dataclass(frozen=True)
class ComparableReport:
    """A CoC-shaped aggregate report over resolved records, suppression applied.

    This is the "comparable database" reporting profile: the aggregate a
    victim-service provider (barred from entering client data into a shared
    database such as HMIS) hands its funders instead. It carries only report
    metadata and suppressed category counts — never a record id, a member list,
    or a field value of any individual. ``total`` is the string
    ``"suppressed"``, not the raw count, when publishing it would hand back a
    breakdown's one hidden cell by subtraction (see :func:`_safe_total`).
    """

    profile: str
    period: str
    generated_at: str
    threshold: int
    total: int | str
    breakdowns: tuple[Breakdown, ...]


def comparable_summary(
    records: Iterable[GoldenRecord],
    *,
    threshold: int = DEFAULT_SUPPRESSION_THRESHOLD,
    breakdown_fields: Sequence[str] = (),
    period: str = "",
) -> ComparableReport:
    """Build the CoC-shaped comparable report from resolved records.

    Starts from the same consent and resolution breakdowns as the aggregate
    summary, then adds one breakdown per configured non-identifying field,
    counting records by that field's distinct values (empty values count under
    ``(missing)``). Every breakdown — base and configured alike — passes
    through :func:`suppress_cells`, so the primary and complementary
    small-cell rules hold for each one. Identifying canonical fields are
    refused, fail-closed, before anything is counted.
    """

    ensure_non_identifying(breakdown_fields)
    record_list = list(records)
    breakdowns = [
        Breakdown("consent", suppress_cells(_consent_counts(record_list), threshold=threshold)),
        Breakdown(
            "resolution", suppress_cells(_resolution_counts(record_list), threshold=threshold)
        ),
    ]
    for field_name in breakdown_fields:
        breakdowns.append(
            Breakdown(
                field_name,
                suppress_cells(_field_counts(record_list, field_name), threshold=threshold),
            )
        )
    frozen_breakdowns = tuple(breakdowns)
    return ComparableReport(
        profile=COMPARABLE_PROFILE,
        period=period or "unspecified",
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        threshold=threshold,
        total=_safe_total(len(record_list), frozen_breakdowns),
        breakdowns=frozen_breakdowns,
    )


def comparable_payload(report: ComparableReport) -> dict[str, object]:
    """Serialize the comparable report to the ``comparable_report.json`` shape.

    The payload carries the report metadata (profile name, period label,
    generated-at timestamp, the suppression threshold applied) and the
    suppressed breakdowns. Cell values are only integers or the string
    ``"suppressed"``; no record id, member list, or individual field value can
    appear.
    """

    from constituent_reconciler.schema import REPORT_SCHEMA_VERSION

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "profile": report.profile,
        "period": report.period,
        "generated_at": report.generated_at,
        "suppression_threshold": report.threshold,
        "total_resolved": report.total,
        "breakdowns": {b.name: b.cells for b in report.breakdowns},
        "note": (
            "Non-identifying aggregate in the comparable-database posture. Small "
            f"cells suppressed (counts 1-{report.threshold - 1}), modeled on the "
            "U.S. CMS Cell Size Suppression Policy; complementary suppression "
            "applied within every breakdown; true zeros preserved. total_resolved "
            'is itself replaced with "suppressed" whenever publishing the real '
            "count would let a hidden cell be recovered by subtraction. Not a "
            "substitute for review against your own obligations."
        ),
    }


def render_comparable(report: ComparableReport) -> str:
    """Render the comparable report as plain text for the run output."""

    lines = [
        "comparable report (non-identifying, small cells suppressed):",
        f"  profile: {report.profile}",
        f"  period: {report.period}",
        f"  resolved records: {report.total}",
    ]
    if report.total == SUPPRESSED:
        lines.append(
            "    (the total itself is suppressed: publishing it would let one of the "
            "hidden cells below be recovered by subtraction)"
        )
    for breakdown in report.breakdowns:
        cells = ", ".join(f"{k}={v}" for k, v in breakdown.cells.items())
        lines.append(f"  {breakdown.name}: {cells}")
    return "\n".join(lines)

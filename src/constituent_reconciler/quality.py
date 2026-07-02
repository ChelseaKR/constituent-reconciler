"""Per-source data quality: completeness, parse failures, consent, duplicates.

Ops staff live with the consequences of one dirty intake channel: the CSV
export from the old system that never carried phone numbers, the form batch
whose dates do not parse, the volunteer sheet with no consent column. This
module measures each source separately so the operator can see which channel
produces the problems, per field, from the run output.

Four measurements per source, all computed from the run result and nothing
else:

* field completeness — the fraction of the source's records whose normalized
  value for a field is non-empty (a value that was present but unparseable
  counts as incomplete, because the matcher sees no evidence either way);
* normalization failures — per field, the count and rate of records whose raw
  value was present but normalized to ``""`` (a date in no known format, an
  address the standardizer could not read);
* consent coverage — the fraction of records carrying usable (granted)
  consent, under the same fail-closed reading as the export gate;
* duplicate density — the fraction of the source's records that landed in a
  multi-member cluster, i.e. the share of the channel's intake that duplicates
  something else the org already holds.

Under a policy that requires suppressed aggregate sharing (the DV pack), every
per-source count is routed through the same small-cell rules as the aggregate
summary (``suppression.suppress_cells``): a source with two records identifies
those two people, so its row is suppressed entirely, and within a surviving
source a count is published only when both it and its complement clear the
threshold ("3 records lack a phone" identifies 3 people as surely as "3
records have one" does).
"""

from __future__ import annotations

from dataclasses import dataclass

from constituent_reconciler.models import CANONICAL_FIELDS, Record, RunResult
from constituent_reconciler.policy import DEFAULT_SUPPRESSION_THRESHOLD
from constituent_reconciler.suppression import SUPPRESSED, suppress_cells


@dataclass(frozen=True)
class SourceQuality:
    """Data-quality measurements for one source, possibly suppressed.

    Numeric fields hold an ``int`` or ``float`` when publishable and the
    string ``"suppressed"`` when the active policy's small-cell rules withhold
    them, mirroring the ``Breakdown`` cells in ``suppression.py``. Fractions
    are in ``[0.0, 1.0]``. Dictionaries are keyed by canonical field name.
    """

    source: str
    records: int | str
    completeness: dict[str, float | str]
    normalization_failures: dict[str, int | str]
    normalization_failure_rates: dict[str, float | str]
    consent_coverage: float | str
    duplicate_density: float | str


def _cell(
    count: int, total: int, *, suppress: bool, threshold: int
) -> tuple[int | str, float | str]:
    """Return ``(count, count / total)``, suppressed when either side is small.

    The count and its complement are treated as the two cells of a breakdown:
    if either falls under the small-cell threshold, ``suppress_cells``'s
    complementary rule hides both, and the pair is reported as suppressed. A
    true zero (or a full ``total``) is preserved, as in the aggregate summary.
    """

    if suppress:
        cells = suppress_cells({"yes": count, "no": total - count}, threshold=threshold)
        if cells["yes"] == SUPPRESSED or cells["no"] == SUPPRESSED:
            return SUPPRESSED, SUPPRESSED
    return count, count / total


def _suppressed_source(source: str, fields: tuple[str, ...]) -> SourceQuality:
    """A fully suppressed row for a source whose record count is itself small."""

    fractions: dict[str, float | str] = {name: SUPPRESSED for name in fields}
    counts: dict[str, int | str] = {name: SUPPRESSED for name in fields}
    return SourceQuality(
        source=source,
        records=SUPPRESSED,
        completeness=fractions,
        normalization_failures=counts,
        normalization_failure_rates=dict(fractions),
        consent_coverage=SUPPRESSED,
        duplicate_density=SUPPRESSED,
    )


def source_quality(
    result: RunResult,
    *,
    fields: tuple[str, ...] | None = None,
    suppress: bool = False,
    threshold: int = DEFAULT_SUPPRESSION_THRESHOLD,
) -> tuple[SourceQuality, ...]:
    """Measure per-source data quality over a run result.

    ``fields`` names the canonical fields to measure; when omitted, the fields
    that appear normalized on any record are used, in canonical order. With
    ``suppress`` true (the DV pack's aggregate posture) every count passes
    through the small-cell rules; sources keep their input order either way.
    """

    by_source: dict[str, list[Record]] = {}
    for record in result.records.values():
        by_source.setdefault(record.source, []).append(record)

    if fields is None:
        seen = {name for record in result.records.values() for name in record.normalized}
        fields = tuple(name for name in CANONICAL_FIELDS if name in seen)

    # Record ids that ended up in a multi-member cluster: the run found each of
    # these records to duplicate at least one other record, from any source.
    duplicated = {
        member
        for cluster in result.clusters
        if len(cluster.members) > 1
        for member in cluster.members
    }

    # The per-source record counts form one breakdown across sources, so the
    # complementary rule applies among them: a lone small source must not be
    # recoverable by subtracting the published sources from a published total.
    raw_counts = {name: len(group) for name, group in by_source.items()}
    source_counts: dict[str, int | str] = dict(raw_counts)
    if suppress:
        source_counts = suppress_cells(raw_counts, threshold=threshold)

    report: list[SourceQuality] = []
    for source, group in by_source.items():
        if source_counts[source] == SUPPRESSED:
            report.append(_suppressed_source(source, fields))
            continue

        total = len(group)
        completeness: dict[str, float | str] = {}
        failures: dict[str, int | str] = {}
        failure_rates: dict[str, float | str] = {}
        for name in fields:
            filled = sum(1 for r in group if r.normalized.get(name, ""))
            _, completeness[name] = _cell(filled, total, suppress=suppress, threshold=threshold)
            failed = sum(
                1
                for r in group
                if r.raw.get(name, "").strip() and not r.normalized.get(name, "")
            )
            failures[name], failure_rates[name] = _cell(
                failed, total, suppress=suppress, threshold=threshold
            )

        consented = sum(1 for r in group if r.has_consent())
        _, consent_coverage = _cell(consented, total, suppress=suppress, threshold=threshold)

        duplicates = sum(1 for r in group if r.unique_id in duplicated)
        _, duplicate_density = _cell(duplicates, total, suppress=suppress, threshold=threshold)

        report.append(
            SourceQuality(
                source=source,
                records=total,
                completeness=completeness,
                normalization_failures=failures,
                normalization_failure_rates=failure_rates,
                consent_coverage=consent_coverage,
                duplicate_density=duplicate_density,
            )
        )
    return tuple(report)

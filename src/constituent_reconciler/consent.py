"""The consent export gate.

Consent is not a report; it is a boundary the export cannot cross. When the
active policy requires consent, a golden record whose consent is not currently
active is withheld from the output, fail-closed. Absent, revoked, expired,
future-dated, out-of-scope, or unrecognized consent all count as no-consent.
This is deliberately strict: the cost of withholding a record is a follow-up;
the cost of exporting one without consent is a breach.

Consent is a lifecycle, not a token: a status string alone does not answer
"is this still true" or "does this cover where we are about to send it". Those
questions need a point in time (``as_of``) and, when consent is scoped, a
destination. This module is where those two are supplied and the gate is
finally applied; ``models.Consent`` only knows how to answer the question, it
never asks it of itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from constituent_reconciler.models import GoldenRecord


@dataclass(frozen=True)
class Withheld:
    """A golden record that did not clear the consent gate, and why.

    ``reason`` is one of ``models.WITHHOLD_REASONS``: "absent", "revoked",
    "future-dated", "expired", or "out-of-scope". Carrying the reason alongside
    the record, rather than collapsing every non-grant to one bucket, is the
    point of this module -- a caseworker following up on "expired" does
    something different than one following up on "revoked".
    """

    record: GoldenRecord
    reason: str

    @property
    def cluster_id(self) -> str:
        return self.record.cluster_id

    @property
    def members(self) -> tuple[str, ...]:
        return self.record.members


def partition_by_consent(
    golden: Iterable[GoldenRecord],
    *,
    require_consent: bool,
    destination: str | None = None,
    as_of: date | None = None,
) -> tuple[list[GoldenRecord], list[Withheld]]:
    """Split golden records into (exportable, withheld), fail closed.

    When ``require_consent`` is false the gate is a no-op and everything is
    exportable, which is the default for non-sensitive donor or directory data.
    When true (the DV and HIPAA policy packs, or a recipe that opts in), a
    record passes only when its consent is active as of ``as_of`` (default:
    today) for ``destination`` -- granted, not revoked, not future-dated, not
    expired, and in scope if a scope was recorded. ``destination`` should be
    the connector name the export is about to write to; leaving it unset skips
    the scope check, matching the pre-scope behavior for recipes that do not
    record scope.
    """

    if not require_consent:
        return list(golden), []

    effective_as_of = as_of if as_of is not None else date.today()
    exportable: list[GoldenRecord] = []
    withheld: list[Withheld] = []
    for record in golden:
        reason = record.consent.reason(as_of=effective_as_of, destination=destination)
        if reason is None:
            exportable.append(record)
        else:
            withheld.append(Withheld(record=record, reason=reason))
    return exportable, withheld

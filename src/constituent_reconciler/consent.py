"""The consent export gate.

Consent is not a report; it is a boundary the export cannot cross. When the
active policy requires consent, a golden record whose consent is not granted is
withheld from the output, fail-closed. Absent, revoked, expired, or unrecognized
consent all count as no-consent. This is deliberately strict: the cost of
withholding a record is a follow-up; the cost of exporting one without consent is
a breach.
"""

from __future__ import annotations

from collections.abc import Iterable

from constituent_reconciler.models import GoldenRecord


def partition_by_consent(
    golden: Iterable[GoldenRecord],
    *,
    require_consent: bool,
) -> tuple[list[GoldenRecord], list[GoldenRecord]]:
    """Split golden records into (exportable, withheld).

    When ``require_consent`` is false the gate is a no-op and everything is
    exportable, which is the default for non-sensitive donor or directory data.
    When true (the DV and HIPAA policy packs), only granted-consent records pass.
    """

    if not require_consent:
        return list(golden), []

    exportable: list[GoldenRecord] = []
    withheld: list[GoldenRecord] = []
    for record in golden:
        (exportable if record.consent else withheld).append(record)
    return exportable, withheld

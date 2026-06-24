from __future__ import annotations

from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.models import GoldenRecord


def _golden(cluster_id: str, consent: bool) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields={},
        primary=cluster_id,
        consent=consent,
    )


def test_require_consent_withholds_non_consented() -> None:
    golden = [_golden("E1", True), _golden("N9", False)]
    exportable, withheld = partition_by_consent(golden, require_consent=True)
    assert [r.cluster_id for r in exportable] == ["E1"]
    assert [r.cluster_id for r in withheld] == ["N9"]


def test_no_requirement_exports_everything() -> None:
    golden = [_golden("E1", True), _golden("N9", False)]
    exportable, withheld = partition_by_consent(golden, require_consent=False)
    assert len(exportable) == 2
    assert withheld == []

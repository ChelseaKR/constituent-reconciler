from __future__ import annotations

from datetime import date, timedelta

from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.models import Consent, GoldenRecord

TODAY = date(2026, 7, 7)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def _golden(cluster_id: str, consent: Consent) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields={},
        field_sources={},
        primary=cluster_id,
        consent=consent,
    )


def test_require_consent_withholds_non_consented() -> None:
    golden = [
        _golden("E1", Consent(status="granted")),
        _golden("N9", Consent(status="")),
    ]
    exportable, withheld = partition_by_consent(golden, require_consent=True, as_of=TODAY)
    assert [r.cluster_id for r in exportable] == ["E1"]
    assert [w.cluster_id for w in withheld] == ["N9"]
    assert withheld[0].reason == "absent"


def test_no_requirement_exports_everything() -> None:
    golden = [
        _golden("E1", Consent(status="granted")),
        _golden("N9", Consent(status="")),
    ]
    exportable, withheld = partition_by_consent(golden, require_consent=False, as_of=TODAY)
    assert len(exportable) == 2
    assert withheld == []


def test_revoked_status_is_withheld_with_its_own_reason() -> None:
    golden = [_golden("N1", Consent(status="revoked"))]
    _, withheld = partition_by_consent(golden, require_consent=True, as_of=TODAY)
    assert withheld[0].reason == "revoked"


def test_unrecognized_status_reads_as_absent_not_granted() -> None:
    # A typo or an unmapped value is not evidence of consent; it must not be
    # silently treated as granted.
    golden = [_golden("N1", Consent(status="pending-review"))]
    _, withheld = partition_by_consent(golden, require_consent=True, as_of=TODAY)
    assert withheld[0].reason == "absent"


def test_expired_consent_is_withheld_fail_closed() -> None:
    consent = Consent(status="granted", expires_on=YESTERDAY)
    golden = [_golden("N1", consent)]
    exportable, withheld = partition_by_consent(golden, require_consent=True, as_of=TODAY)
    assert exportable == []
    assert withheld[0].reason == "expired"


def test_consent_still_within_its_window_is_exportable() -> None:
    consent = Consent(status="granted", expires_on=TOMORROW)
    golden = [_golden("N1", consent)]
    exportable, withheld = partition_by_consent(golden, require_consent=True, as_of=TODAY)
    assert [r.cluster_id for r in exportable] == ["N1"]
    assert withheld == []


def test_consent_with_no_expiry_recorded_never_expires_by_inference() -> None:
    # No default expiry window is ever invented: an unmapped expiry column
    # means "no ceiling was recorded", not "expires immediately" or "expires
    # after some invented default".
    consent = Consent(status="granted")
    golden = [_golden("N1", consent)]
    exportable, withheld = partition_by_consent(golden, require_consent=True, as_of=TODAY)
    assert [r.cluster_id for r in exportable] == ["N1"]
    assert withheld == []


def test_future_dated_grant_is_withheld() -> None:
    consent = Consent(status="granted", granted_on=TOMORROW)
    golden = [_golden("N1", consent)]
    _, withheld = partition_by_consent(golden, require_consent=True, as_of=TODAY)
    assert withheld[0].reason == "future-dated"


def test_scoped_consent_blocks_a_destination_it_does_not_cover() -> None:
    consent = Consent(status="granted", scope=frozenset({"civicrm"}))
    golden = [_golden("N1", consent)]
    exportable, withheld = partition_by_consent(
        golden, require_consent=True, destination="funder_export", as_of=TODAY
    )
    assert exportable == []
    assert withheld[0].reason == "out-of-scope"


def test_scoped_consent_clears_the_destination_it_covers() -> None:
    consent = Consent(status="granted", scope=frozenset({"civicrm", "csv"}))
    golden = [_golden("N1", consent)]
    exportable, withheld = partition_by_consent(
        golden, require_consent=True, destination="civicrm", as_of=TODAY
    )
    assert [r.cluster_id for r in exportable] == ["N1"]
    assert withheld == []


def test_unscoped_consent_covers_every_destination() -> None:
    # A recipe that never maps a scope column keeps the pre-scope behavior:
    # an empty scope is not "covers nothing", it is "covers everything".
    consent = Consent(status="granted")
    golden = [_golden("N1", consent)]
    exportable, _ = partition_by_consent(
        golden, require_consent=True, destination="anything", as_of=TODAY
    )
    assert [r.cluster_id for r in exportable] == ["N1"]


def test_as_of_defaults_to_today_when_not_supplied() -> None:
    consent = Consent(status="granted", expires_on=date.today() - timedelta(days=1))
    golden = [_golden("N1", consent)]
    exportable, withheld = partition_by_consent(golden, require_consent=True)
    assert exportable == []
    assert withheld[0].reason == "expired"

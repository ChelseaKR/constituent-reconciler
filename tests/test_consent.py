from __future__ import annotations

from datetime import date, timedelta
from itertools import combinations_with_replacement

from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.models import NO_COMMON_DESTINATION, Consent, GoldenRecord

TODAY = date(2026, 7, 7)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
LAST_WEEK = TODAY - timedelta(days=7)
NEXT_WEEK = TODAY + timedelta(days=7)


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


# --- Merged-identity consent: most restrictive member wins (issue #83) --------
#
# The harm these tests exist to exclude is manufactured consent: a merge whose
# consent state is broader than what some person in the cluster actually gave.
# See docs/adr/0013-merged-consent-most-restrictive.md.

# One consent per interesting shape, used to build every combination below.
CONSENT_CASES: tuple[Consent, ...] = (
    Consent(status="granted"),
    Consent(status="yes"),
    Consent(status=""),
    Consent(status="pending-review"),
    Consent(status="revoked"),
    Consent(status="withdrawn"),
    Consent(status="granted", granted_on=LAST_WEEK),
    Consent(status="granted", granted_on=TOMORROW),
    Consent(status="granted", expires_on=YESTERDAY),
    Consent(status="granted", expires_on=NEXT_WEEK),
    Consent(status="granted", granted_on=LAST_WEEK, expires_on=NEXT_WEEK),
    Consent(status="granted", scope=frozenset({"csv"})),
    Consent(status="granted", scope=frozenset({"civicrm"})),
    Consent(status="granted", scope=frozenset({"csv", "civicrm"})),
)

DESTINATIONS: tuple[str | None, ...] = (None, "csv", "civicrm", "salesforce")
AS_OF_DATES: tuple[date, ...] = (LAST_WEEK, YESTERDAY, TODAY, TOMORROW, NEXT_WEEK)


def test_no_merge_produces_consent_broader_than_its_narrowest_member() -> None:
    # The regression: over every combination of up to three member consents,
    # every run date, and every destination, an active merged consent implies
    # every member's consent was active too. A merge may narrow what someone
    # granted; it may never widen it.
    for size in (2, 3):
        for members in combinations_with_replacement(CONSENT_CASES, size):
            merged = Consent.most_restrictive(members)
            for as_of in AS_OF_DATES:
                for destination in DESTINATIONS:
                    if not merged.is_active(as_of=as_of, destination=destination):
                        continue
                    for member in members:
                        assert member.is_active(as_of=as_of, destination=destination), (
                            f"merge of {members!r} exported at {as_of} to {destination!r} "
                            f"on consent {member!r} never gave"
                        )


def test_a_revoked_member_revokes_the_merge_even_when_the_survivor_granted() -> None:
    merged = Consent.most_restrictive([Consent(status="granted"), Consent(status="revoked")])
    assert merged.reason(as_of=TODAY) == "revoked"


def test_an_unconsented_member_withholds_the_merge() -> None:
    # "Absent" and "revoked" stay distinct on the merged record for the same
    # reason they do on a single record: a caseworker follows up differently.
    merged = Consent.most_restrictive([Consent(status="granted"), Consent(status="")])
    assert merged.reason(as_of=TODAY) == "absent"


def test_the_earliest_expiry_and_the_latest_grant_date_bind_the_merge() -> None:
    merged = Consent.most_restrictive(
        [
            Consent(status="granted", granted_on=LAST_WEEK, expires_on=NEXT_WEEK),
            Consent(status="granted", granted_on=TODAY, expires_on=TOMORROW),
        ]
    )
    assert merged.granted_on == TODAY
    assert merged.expires_on == TOMORROW
    assert merged.reason(as_of=YESTERDAY) == "future-dated"
    assert merged.reason(as_of=TODAY) is None
    assert merged.reason(as_of=NEXT_WEEK) == "expired"


def test_a_recorded_date_is_never_overridden_by_an_unrecorded_one() -> None:
    # An unmapped expiry column means "no ceiling was recorded" for that
    # member, not "this member's consent lasts forever", so it cannot lift
    # another member's recorded ceiling.
    merged = Consent.most_restrictive(
        [Consent(status="granted"), Consent(status="granted", expires_on=YESTERDAY)]
    )
    assert merged.expires_on == YESTERDAY
    assert merged.reason(as_of=TODAY) == "expired"


def test_scopes_intersect_so_the_merge_covers_only_shared_destinations() -> None:
    merged = Consent.most_restrictive(
        [
            Consent(status="granted", scope=frozenset({"csv", "civicrm"})),
            Consent(status="granted", scope=frozenset({"csv"})),
        ]
    )
    assert merged.scope == frozenset({"csv"})
    assert merged.reason(as_of=TODAY, destination="csv") is None
    assert merged.reason(as_of=TODAY, destination="civicrm") == "out-of-scope"


def test_members_with_no_shared_destination_are_out_of_scope_everywhere() -> None:
    # An empty scope already means "every destination", so the merge cannot
    # signal "no destination" by emptying the set; it names the impossible
    # destination instead.
    merged = Consent.most_restrictive(
        [
            Consent(status="granted", scope=frozenset({"csv"})),
            Consent(status="granted", scope=frozenset({"civicrm"})),
        ]
    )
    assert merged.scope == frozenset({NO_COMMON_DESTINATION})
    for destination in ("csv", "civicrm", "salesforce"):
        assert merged.reason(as_of=TODAY, destination=destination) == "out-of-scope"


def test_an_unscoped_member_narrows_nothing() -> None:
    merged = Consent.most_restrictive(
        [Consent(status="granted"), Consent(status="granted", scope=frozenset({"csv"}))]
    )
    assert merged.scope == frozenset({"csv"})


def test_a_single_member_keeps_its_own_consent_unchanged() -> None:
    only = Consent(status="yes", granted_on=LAST_WEEK, scope=frozenset({"csv"}))
    assert Consent.most_restrictive([only]) is only


def test_no_members_reads_as_absent_not_granted() -> None:
    assert Consent.most_restrictive([]).reason(as_of=TODAY) == "absent"

from __future__ import annotations

from datetime import date

import pytest

from constituent_reconciler.decisions import band_pairs, build_clusters, golden_records
from constituent_reconciler.models import Band, Cluster, Consent, Record

FIELDS = ("first_name", "last_name", "dob", "email", "phone")


def test_band_pairs_respects_thresholds() -> None:
    pairs = band_pairs(
        [("a", "b", 0.99), ("a", "c", 0.85), ("a", "d", 0.50)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    bands = {(p.left, p.right): p.band for p in pairs}
    assert bands[("a", "b")] is Band.AUTO
    assert bands[("a", "c")] is Band.REVIEW
    assert bands[("a", "d")] is Band.DROP


def test_clusters_use_auto_edges_only() -> None:
    pairs = band_pairs(
        [("a", "b", 0.99), ("b", "c", 0.85)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    clusters = build_clusters(["a", "b", "c", "d"], pairs)
    member_sets = [set(c.members) for c in clusters]
    assert {"a", "b"} in member_sets
    assert {"c"} in member_sets
    assert {"d"} in member_sets


def _record(uid: str, source: str, normalized: dict[str, str], consent: str) -> Record:
    return Record(
        unique_id=uid,
        source=source,
        raw={},
        normalized=normalized,
        consent=Consent(status=consent),
    )


def test_golden_prefers_existing_consented_survivor_and_fills_blanks() -> None:
    bob = {
        "first_name": "bob",
        "last_name": "smith",
        "dob": "",
        "email": "",
        "phone": "5305550143",
    }
    robert = {
        "first_name": "robert",
        "last_name": "smith",
        "dob": "1965-07-19",
        "email": "",
        "phone": "",
    }
    records = {
        "N1": _record("N1", "incoming", bob, "granted"),
        "E1": _record("E1", "existing", robert, "granted"),
    }
    clusters = [Cluster("E1", ("E1", "N1"))]
    golden = golden_records(clusters, records, FIELDS)
    record = golden[0]
    assert record.primary == "E1"
    assert record.fields["first_name"] == "robert"
    assert record.fields["dob"] == "1965-07-19"
    # Survivor's phone is blank, so it is filled from the other cluster member.
    assert record.fields["phone"] == "5305550143"
    # The golden record carries the survivor's Consent lifecycle unevaluated;
    # it reads as active today because there is no expiry recorded.
    assert record.consent.is_active(as_of=date.today())
    # Lineage: each non-empty field names the member whose value survived, and
    # a field that merged to empty (email here) has no entry at all.
    assert record.field_sources == {
        "first_name": "E1",
        "last_name": "E1",
        "dob": "E1",
        "phone": "N1",
    }


def test_field_sources_name_members_that_carry_the_merged_value() -> None:
    # Property over every golden record: a non-empty field's lineage entry must
    # name a cluster member whose normalized value equals the merged value, and
    # an empty field must have no lineage entry.
    records = {
        "E1": _record(
            "E1",
            "existing",
            {"first_name": "ana", "last_name": "silva", "dob": "", "email": "", "phone": ""},
            "granted",
        ),
        "N1": _record(
            "N1",
            "incoming",
            {
                "first_name": "ana",
                "last_name": "",
                "dob": "1980-02-02",
                "email": "ana@example.org",
                "phone": "",
            },
            "",
        ),
        "N2": _record(
            "N2",
            "incoming",
            {
                "first_name": "",
                "last_name": "silva",
                "dob": "1980-02-02",
                "email": "",
                "phone": "5305550100",
            },
            "",
        ),
        "N9": _record(
            "N9",
            "incoming",
            {"first_name": "omar", "last_name": "haddad", "dob": "", "email": "", "phone": ""},
            "granted",
        ),
    }
    clusters = [Cluster("E1", ("E1", "N1", "N2")), Cluster("N9", ("N9",))]
    for golden in golden_records(clusters, records, FIELDS):
        for field_name in FIELDS:
            value = golden.fields[field_name]
            if not value:
                assert field_name not in golden.field_sources
                continue
            source = golden.field_sources[field_name]
            assert source in golden.members
            assert records[source].normalized[field_name] == value


def test_unknown_fill_policy_raises() -> None:
    records = {"E1": _record("E1", "existing", {}, "granted")}
    clusters = [Cluster("E1", ("E1",))]
    # "most-recent-wins" is reserved, not implemented; it must refuse loudly
    # rather than silently merging under a different order.
    with pytest.raises(ValueError, match="fill policy"):
        golden_records(clusters, records, FIELDS, fill_policy="most-recent-wins")

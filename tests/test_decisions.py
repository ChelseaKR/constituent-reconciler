from __future__ import annotations

from constituent_reconciler.decisions import band_pairs, build_clusters, golden_records
from constituent_reconciler.models import Band, Cluster, Record

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
        consent_status=consent,
    )


def test_golden_prefers_existing_consented_survivor_and_fills_blanks() -> None:
    bob = {
        "first_name": "bob", "last_name": "smith", "dob": "",
        "email": "", "phone": "5305550143",
    }
    robert = {
        "first_name": "robert", "last_name": "smith", "dob": "1965-07-19",
        "email": "", "phone": "",
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
    assert record.consent is True

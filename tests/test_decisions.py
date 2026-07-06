from __future__ import annotations

from constituent_reconciler.decisions import (
    CANNOT_LINK_NOTE,
    band_pairs,
    build_clusters,
    enforce_cannot_links,
    golden_records,
)
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


def test_rejected_pair_is_never_transitively_merged() -> None:
    # The planted triangle: a-b and b-c are confident merges, but a human
    # rejected a-c. Without the constraint the transitive closure would put a
    # and c in one cluster, silently overriding the reviewer.
    pairs = band_pairs(
        [("a", "b", 0.99), ("b", "c", 0.99), ("a", "c", 0.10)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    cannot = frozenset({frozenset(("a", "c"))})
    clusters, adjusted = enforce_cannot_links(["a", "b", "c", "d"], pairs, cannot_link=cannot)

    # The merge-blocking invariant: no cluster contains a rejected pair.
    for cluster in clusters:
        members = set(cluster.members)
        assert not any(constraint <= members for constraint in cannot)
    # The refused cluster falls apart into singletons; a person re-decides it.
    member_sets = [set(c.members) for c in clusters]
    assert {"a"} in member_sets and {"b"} in member_sets and {"c"} in member_sets

    bands = {(p.left, p.right): p for p in adjusted}
    assert bands[("a", "b")].band is Band.REVIEW
    assert bands[("b", "c")].band is Band.REVIEW
    assert bands[("a", "b")].note == CANNOT_LINK_NOTE
    assert bands[("b", "c")].note == CANNOT_LINK_NOTE
    # The rejected edge itself stays dropped; the human already decided it.
    assert bands[("a", "c")].band is Band.DROP
    assert bands[("a", "c")].note == ""


def test_cannot_link_leaves_unrelated_clusters_alone() -> None:
    pairs = band_pairs(
        [("a", "b", 0.99), ("c", "d", 0.99)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    # The rejected pair spans two clusters that were never going to merge.
    cannot = frozenset({frozenset(("a", "c"))})
    clusters, adjusted = enforce_cannot_links(["a", "b", "c", "d"], pairs, cannot_link=cannot)
    member_sets = [set(c.members) for c in clusters]
    assert {"a", "b"} in member_sets
    assert {"c", "d"} in member_sets
    assert all(p.band is Band.AUTO for p in adjusted)


def test_no_constraints_is_a_no_op() -> None:
    pairs = band_pairs(
        [("a", "b", 0.99), ("b", "c", 0.85)],
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    clusters, adjusted = enforce_cannot_links(["a", "b", "c"], pairs, cannot_link=frozenset())
    assert clusters == build_clusters(["a", "b", "c"], pairs)
    assert adjusted == pairs


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
    assert record.consent is True

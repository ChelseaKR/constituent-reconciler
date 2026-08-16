"""Banding, clustering, and golden-record selection.

This is where the fail-closed policy lives. Scored pairs are assigned to a band,
clusters are built from auto-merge edges only, and each cluster is reduced to one
surviving golden record. Nothing here calls the matcher; it operates on the
scored tuples alone, which keeps it fast and fully testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from constituent_reconciler import defaults
from constituent_reconciler.models import Band, Cluster, Consent, GoldenRecord, Pair, Record

# Survivorship fill policies golden_records() understands. The policy names how
# empty survivor fields are completed from the rest of the cluster. Only one is
# implemented today; "most-recent-wins" is reserved for when records carry a
# usable date. An unknown name raises, fail-closed, so a typo in a recipe can
# never silently fall back to a different merge behavior.
DEFAULT_FILL_POLICY = "survivor-then-lowest-id"
FILL_POLICIES: tuple[str, ...] = (DEFAULT_FILL_POLICY,)

CANNOT_LINK_NOTE = (
    "A reviewer separated two records in this group. Automatic edges in the "
    "group were returned to review so the rejection cannot be overridden transitively."
)


def band_pairs(
    scored: Iterable[tuple[str, str, float]],
    *,
    auto_threshold: float = defaults.DEFAULT_AUTO_THRESHOLD,
    review_threshold: float = defaults.DEFAULT_REVIEW_THRESHOLD,
) -> list[Pair]:
    """Assign each scored pair to AUTO, REVIEW, or DROP.

    A pair at or above ``auto_threshold`` is a confident merge. A pair in
    ``[review_threshold, auto_threshold)`` is uncertain and is sent to a human.
    Below ``review_threshold`` it is dropped. The two-threshold band is the
    point of the design: uncertainty routes to review, never to an auto-merge.
    """

    pairs: list[Pair] = []
    for left, right, probability in scored:
        if probability >= auto_threshold:
            band = Band.AUTO
        elif probability >= review_threshold:
            band = Band.REVIEW
        else:
            band = Band.DROP
        pairs.append(Pair(left=left, right=right, probability=probability, band=band))
    return pairs


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self._parent: dict[str, str] = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression keeps repeated lookups cheap.
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        # Attach the larger id under the smaller so the root is stable.
        low, high = sorted((left_root, right_root))
        self._parent[high] = low

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self._parent:
            out.setdefault(self.find(item), []).append(item)
        return out


def build_clusters(record_ids: Iterable[str], pairs: Iterable[Pair]) -> list[Cluster]:
    """Cluster records using AUTO edges only. Singletons are included.

    Clustering ignores REVIEW and DROP edges on purpose: only confident merges
    join records without a human. The cluster id is the smallest member id, so
    the result is stable across runs.
    """

    ids = list(record_ids)
    union = _UnionFind(ids)
    for pair in pairs:
        if pair.band is Band.AUTO:
            union.union(pair.left, pair.right)

    clusters: list[Cluster] = []
    for root, members in union.groups().items():
        clusters.append(Cluster(cluster_id=root, members=tuple(sorted(members))))
    clusters.sort(key=lambda cluster: cluster.cluster_id)
    return clusters


def enforce_cannot_links(
    record_ids: Iterable[str],
    pairs: Iterable[Pair],
    cannot_links: Iterable[frozenset[str]],
) -> list[Pair]:
    """Refuse every auto-cluster that would contain a human-rejected pair.

    This is the fail-closed, refuse-and-route implementation of FIX-02. All
    AUTO edges inside a conflicting transitive component return to REVIEW;
    the rejected edge itself remains DROP. Re-clustering the returned pairs
    therefore cannot put either endpoint of a cannot-link in one cluster.
    """

    ids = tuple(record_ids)
    materialized = list(pairs)
    constraints = frozenset(link for link in cannot_links if len(link) == 2)
    if not constraints:
        return materialized
    conflicted_members: set[str] = set()
    for cluster in build_clusters(ids, materialized):
        members = frozenset(cluster.members)
        if any(link <= members for link in constraints):
            conflicted_members.update(members)
    if not conflicted_members:
        return materialized
    return [
        Pair(pair.left, pair.right, pair.probability, Band.REVIEW, CANNOT_LINK_NOTE)
        if pair.band is Band.AUTO
        and pair.left in conflicted_members
        and pair.right in conflicted_members
        else pair
        for pair in materialized
    ]


def _choose_primary(members: tuple[str, ...], records: Mapping[str, Record]) -> str:
    """Pick the survivor: a consented existing record if possible, else stable.

    Preference order: an existing-source record that carries consent, then any
    existing record, then any consented record, then the lowest id. Keeping an
    existing record as the survivor means the merge updates a row already in the
    case system rather than minting a new identity for it.
    """

    def rank(record_id: str) -> tuple[int, str]:
        record = records[record_id]
        is_existing = record.source == "existing"
        has_consent = record.has_consent()
        # Lower tuple sorts first; encode preferences as ascending integers.
        if is_existing and has_consent:
            tier = 0
        elif is_existing:
            tier = 1
        elif has_consent:
            tier = 2
        else:
            tier = 3
        return (tier, record_id)

    return min(members, key=rank)


def golden_records(
    clusters: Iterable[Cluster],
    records: Mapping[str, Record],
    fields: tuple[str, ...],
    *,
    fill_policy: str = DEFAULT_FILL_POLICY,
) -> list[GoldenRecord]:
    """Reduce each cluster to one merged record.

    The survivor supplies the identity. ``fill_policy`` names how empty survivor
    fields are completed: under ``"survivor-then-lowest-id"`` (the only policy
    implemented; ``"most-recent-wins"`` is reserved for when records carry a
    date) blanks are filled deterministically from the other members in
    ascending id order. An unknown policy name raises ValueError, fail-closed.
    Each non-empty merged field records the member that supplied its value in
    ``field_sources``.

    Consent on the merged record is the most restrictive of its members'
    (``Consent.most_restrictive``), not the survivor's: a merge may narrow what
    a person granted and may never widen it. One revoked or unconsented member
    withholds the whole merged identity, because the tool cannot tell which
    member's fields a downstream export would carry. The lifecycle is carried
    through unevaluated -- the export gate decides granted-or-withheld later,
    once it knows the write destination and run date. See
    docs/adr/0013-merged-consent-most-restrictive.md.
    """

    if fill_policy not in FILL_POLICIES:
        raise ValueError(
            f"unknown fill policy {fill_policy!r}; supported: {', '.join(FILL_POLICIES)}"
        )
    out: list[GoldenRecord] = []
    for cluster in clusters:
        primary = _choose_primary(cluster.members, records)
        merged: dict[str, str] = {}
        field_sources: dict[str, str] = {}
        for field_name in fields:
            value = records[primary].normalized.get(field_name, "")
            source = primary
            if not value:
                # cluster.members is sorted, so this is the lowest-id member
                # that carries a value: the "survivor-then-lowest-id" policy.
                for member in cluster.members:
                    candidate = records[member].normalized.get(field_name, "")
                    if candidate:
                        value = candidate
                        source = member
                        break
            merged[field_name] = value
            if value:
                field_sources[field_name] = source
        out.append(
            GoldenRecord(
                cluster_id=cluster.cluster_id,
                members=cluster.members,
                fields=merged,
                field_sources=field_sources,
                primary=primary,
                consent=Consent.most_restrictive(
                    records[member].consent for member in cluster.members
                ),
            )
        )
    return out

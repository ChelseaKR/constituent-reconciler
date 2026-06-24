"""Banding, clustering, and golden-record selection.

This is where the fail-closed policy lives. Scored pairs are assigned to a band,
clusters are built from auto-merge edges only, and each cluster is reduced to one
surviving golden record. Nothing here calls the matcher; it operates on the
scored tuples alone, which keeps it fast and fully testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from constituent_reconciler import defaults
from constituent_reconciler.models import Band, Cluster, GoldenRecord, Pair, Record


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
) -> list[GoldenRecord]:
    """Reduce each cluster to one merged record.

    The survivor supplies the identity. Empty survivor fields are filled from
    other members of the cluster (most-recent-wins is left to a later version;
    v0.1 fills blanks deterministically by member id order). Consent on the
    merged record follows the survivor, fail-closed.
    """

    out: list[GoldenRecord] = []
    for cluster in clusters:
        primary = _choose_primary(cluster.members, records)
        merged: dict[str, str] = {}
        for field_name in fields:
            value = records[primary].normalized.get(field_name, "")
            if not value:
                for member in cluster.members:
                    candidate = records[member].normalized.get(field_name, "")
                    if candidate:
                        value = candidate
                        break
            merged[field_name] = value
        out.append(
            GoldenRecord(
                cluster_id=cluster.cluster_id,
                members=cluster.members,
                fields=merged,
                primary=primary,
                consent=records[primary].has_consent(),
            )
        )
    return out

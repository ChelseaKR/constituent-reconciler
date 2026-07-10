"""Review session state and decision persistence.

A ``ReviewSession`` wraps a finished pipeline run and exposes the review pairs in
a stable order, tracks one verdict per pair, and writes those verdicts to a
decisions file. The session holds no socket and renders no HTML, so its logic is
unit-testable on its own; the server and the renderer build on it.

The decisions file is the session's only side effect, and it carries record ids
and verdicts only. No field value is written, which is the minimization the DV
pack requires of any artifact the review step produces.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from constituent_reconciler import decisions
from constituent_reconciler.models import Band, Cluster, Pair, Record, RunResult

APPROVED = "approved"
REJECTED = "rejected"
_VERDICTS = frozenset({APPROVED, REJECTED})

# Shown when merging the pair on screen would contradict a rejection recorded
# elsewhere in the same group of records: the same cannot-link problem
# ``reconcile apply`` must eventually resolve, surfaced during review instead
# of only after decisions are written.
CONFLICT_NOTE = (
    "A different pair in this group of records was already rejected, so "
    "merging this pair would silently pull two records back together that a "
    "reviewer kept apart. Nothing merges until that conflict is resolved: "
    "revisit the rejected pair, or reject this one too."
)

# Plain-language labels for the canonical fields, so the rationale and the
# comparison table read the way a caseworker speaks rather than the way the
# schema is keyed. Unmapped names fall back to the underscored form spelled out.
FIELD_LABELS: dict[str, str] = {
    "first_name": "first name",
    "last_name": "last name",
    "dob": "date of birth",
    "email": "email",
    "phone": "phone",
    "address": "address",
}


def field_label(name: str) -> str:
    """Human label for a canonical field, for jargon-free display."""

    return FIELD_LABELS.get(name, name.replace("_", " "))


def _join(labels: Sequence[str]) -> str:
    """Join field labels into a readable list: a; a and b; a, b, and c."""

    items = list(labels)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


@dataclass(frozen=True)
class FieldCell:
    """One field's two values across the candidate pair, with agreement.

    ``left`` and ``right`` are the raw source values shown to the reviewer.
    ``agrees`` compares the normalized values, so it reflects what the matcher
    saw rather than surface formatting. ``comparable`` is True only when both
    sides carry a normalized value; when one is blank the matcher had no evidence
    on that field, which is different from a disagreement and is said so. The span
    strings point back to where a value was read in a source document; empty for
    records read from CSV.
    """

    field: str
    left: str
    right: str
    left_span: str
    right_span: str
    agrees: bool
    comparable: bool


@dataclass(frozen=True)
class MatchRationale:
    """Why a pair landed in review, in the reviewer's own words.

    The three buckets hold field labels: those the two records agree on, those
    they disagree on, and those that could not be compared because one side was
    blank. ``summary`` is the sentence shown beside the pair; ``short`` is the
    one-line version for the queue list.
    """

    agree: tuple[str, ...]
    differ: tuple[str, ...]
    uncompared: tuple[str, ...]

    def summary(self) -> str:
        parts: list[str] = []
        if self.agree:
            parts.append(f"These records agree on {_join(self.agree)}.")
        if self.differ:
            lead = "They differ" if parts else "These records differ"
            parts.append(f"{lead} on {_join(self.differ)}.")
        if self.uncompared:
            verb = "was" if len(self.uncompared) == 1 else "were"
            pronoun = "it" if len(self.uncompared) == 1 else "they"
            joined = _join(self.uncompared)
            lead_word = joined[0].upper() + joined[1:]
            parts.append(
                f"{lead_word} {verb} blank on at least one record, "
                f"so {pronoun} could not be compared."
            )
        if not parts:
            return "There were no fields to compare on this pair."
        return " ".join(parts)

    def short(self) -> str:
        bits: list[str] = []
        if self.agree:
            bits.append(f"agree on {_join(self.agree)}")
        if self.differ:
            bits.append(f"differ on {_join(self.differ)}")
        if self.uncompared:
            bits.append(f"{_join(self.uncompared)} not compared")
        return "; ".join(bits) if bits else "nothing to compare"


def rationale_for(view: PairView) -> MatchRationale:
    """Bucket a pair's fields into agree, differ, and could-not-compare.

    A field counts as compared only when both records carry a normalized value,
    matching the matcher's null level: a blank on either side is no evidence, not
    a disagreement, and the reviewer is told which it is.
    """

    agree: list[str] = []
    differ: list[str] = []
    uncompared: list[str] = []
    for cell in view.fields:
        label = field_label(cell.field)
        if not cell.comparable:
            uncompared.append(label)
        elif cell.agrees:
            agree.append(label)
        else:
            differ.append(label)
    return MatchRationale(tuple(agree), tuple(differ), tuple(uncompared))


@dataclass(frozen=True)
class PairView:
    """Everything the review screen needs for one candidate pair."""

    index: int
    left_id: str
    right_id: str
    left_source: str
    right_source: str
    probability: float
    fields: tuple[FieldCell, ...]


@dataclass(frozen=True)
class Counts:
    approved: int
    rejected: int
    pending: int


@dataclass(frozen=True)
class ClusterMemberView:
    """One record in a cluster preview."""

    record_id: str
    source: str
    is_primary: bool


@dataclass(frozen=True)
class ClusterEdgeView:
    """One scored relationship between two members of a cluster preview.

    ``status`` is one of ``auto`` (the matcher merged it with no human),
    ``approved`` or ``rejected`` (this session's own verdict), ``pending`` (a
    review-band pair still undecided), or ``scored-apart`` (the matcher scored
    it low enough to drop, yet both ends still ended up in this cluster through
    other edges). ``pair_index`` is the review-queue index of this edge when it
    is itself a review pair, so the page can link to it.
    """

    left: str
    right: str
    probability: float
    status: str
    pair_index: int | None


@dataclass(frozen=True)
class GoldenFieldView:
    """One field of a previewed golden record, with which record supplied it."""

    field: str
    value: str
    source_id: str


@dataclass(frozen=True)
class ClusterGroupView:
    """The members, internal edges, and golden record of one previewed cluster."""

    members: tuple[ClusterMemberView, ...]
    edges: tuple[ClusterEdgeView, ...]
    golden: tuple[GoldenFieldView, ...]


@dataclass(frozen=True)
class ClusterPreview:
    """What this session's decisions imply for the cluster around one pair.

    ``merged`` is True when the pair's two records land in one cluster --
    already, if decided, or provisionally as an approval preview when the pair
    is still pending. ``conflict`` is True when merging would contradict a
    rejection elsewhere in the group, in which case ``groups`` is empty and the
    page shows :data:`CONFLICT_NOTE` instead. When not merged and not in
    conflict, ``groups`` holds the two clusters the decision keeps apart.
    """

    pair_index: int
    merged: bool
    conflict: bool
    groups: tuple[ClusterGroupView, ...]


def _field_sources(
    primary: str,
    members: tuple[str, ...],
    records: Mapping[str, Record],
    fields: tuple[str, ...],
) -> dict[str, str]:
    """Which record contributed each golden field's value.

    Mirrors the fill order ``decisions.golden_records`` uses internally (the
    survivor first, then the other members in id order) but returns the
    contributor's id per field instead of only the merged value, so the
    preview can say where a value came from.
    """

    sources: dict[str, str] = {}
    for field_name in fields:
        value = records[primary].normalized.get(field_name, "")
        source = primary if value else ""
        if not value:
            for member in members:
                candidate = records[member].normalized.get(field_name, "")
                if candidate:
                    source = member
                    break
        sources[field_name] = source
    return sources


class ReviewSession:
    """Stateful review over a run's uncertain pairs, persisted to a file."""

    def __init__(
        self,
        result: RunResult,
        fields: tuple[str, ...],
        decisions_path: Path,
        *,
        privacy_mode: bool = False,
    ) -> None:
        self._result = result
        self._fields = fields
        self._decisions_path = decisions_path
        self.privacy_mode = privacy_mode
        # A per-run secret embedded in every rendered form and checked on every
        # POST (FIX-01), so a page the reviewer has open elsewhere cannot forge
        # a verdict against this server: it cannot know a token it never saw.
        # Regenerated each time a session is constructed, never persisted.
        self.token = secrets.token_urlsafe(24)
        # The same ordering the review_queue.csv uses, so the two surfaces agree.
        self._pairs: tuple[Pair, ...] = tuple(
            sorted(result.review_pairs, key=lambda p: (-p.probability, p.left, p.right))
        )
        self._verdicts: dict[int, str] = {}
        self._load_existing()

    # -- construction helpers ------------------------------------------------

    def _key_to_index(self) -> dict[frozenset[str], int]:
        return {pair.key(): index for index, pair in enumerate(self._pairs)}

    def _load_existing(self) -> None:
        """Resume from an existing decisions file, if one is present.

        Decisions are keyed by the unordered pair of record ids, so a resumed
        verdict re-attaches to the same candidate even if the pair ordering
        differs. Unknown pairs in the file are ignored rather than raising.
        """

        if not self._decisions_path.exists():
            return
        try:
            data = json.loads(self._decisions_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        index_of = self._key_to_index()
        for verdict, key in ((APPROVED, "approved"), (REJECTED, "rejected")):
            for entry in data.get(key, []):
                if not isinstance(entry, list) or len(entry) != 2:
                    continue
                pair_key = frozenset((str(entry[0]), str(entry[1])))
                index = index_of.get(pair_key)
                if index is not None:
                    self._verdicts[index] = verdict

    # -- read access ---------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self._pairs)

    def _build_view(self, index: int, pair: Pair) -> PairView:
        left = self._result.records[pair.left]
        right = self._result.records[pair.right]
        cells: list[FieldCell] = []
        for field_name in self._fields:
            left_norm = left.normalized.get(field_name, "")
            right_norm = right.normalized.get(field_name, "")
            left_span = left.spans.get(field_name)
            right_span = right.spans.get(field_name)
            comparable = bool(left_norm) and bool(right_norm)
            cells.append(
                FieldCell(
                    field=field_name,
                    left=left.raw.get(field_name, ""),
                    right=right.raw.get(field_name, ""),
                    left_span=str(left_span) if left_span else "",
                    right_span=str(right_span) if right_span else "",
                    agrees=comparable and left_norm == right_norm,
                    comparable=comparable,
                )
            )
        return PairView(
            index=index,
            left_id=pair.left,
            right_id=pair.right,
            left_source=left.source,
            right_source=right.source,
            probability=pair.probability,
            fields=tuple(cells),
        )

    def views(self) -> list[PairView]:
        return [self._build_view(i, pair) for i, pair in enumerate(self._pairs)]

    def view(self, index: int) -> PairView | None:
        if 0 <= index < len(self._pairs):
            return self._build_view(index, self._pairs[index])
        return None

    def verdict(self, index: int) -> str | None:
        return self._verdicts.get(index)

    def counts(self) -> Counts:
        approved = sum(1 for v in self._verdicts.values() if v == APPROVED)
        rejected = sum(1 for v in self._verdicts.values() if v == REJECTED)
        return Counts(
            approved=approved,
            rejected=rejected,
            pending=len(self._pairs) - approved - rejected,
        )

    def next_undecided(self, after: int = -1) -> int | None:
        """Index of the first undecided pair strictly after ``after``, if any.

        Wraps once to the start so a reviewer who jumps around still lands on an
        outstanding pair. Returns None when every pair has a verdict.
        """

        order = list(range(after + 1, len(self._pairs))) + list(range(0, after + 1))
        for index in order:
            if index not in self._verdicts:
                return index
        return None

    # -- cluster and golden-record preview -----------------------------------

    def _live_pairs(self) -> list[Pair]:
        """This run's scored pairs with the session's own verdicts applied.

        An approved review pair becomes a confident merge and a rejected one is
        dropped; every other pair keeps its original band. This mirrors
        ``pipeline.run``'s ``force_auto``/``force_drop`` override so a preview
        during review matches what ``reconcile apply`` would actually produce.
        """

        approved = frozenset(
            self._pairs[i].key() for i, v in self._verdicts.items() if v == APPROVED
        )
        rejected = frozenset(
            self._pairs[i].key() for i, v in self._verdicts.items() if v == REJECTED
        )
        adjusted: list[Pair] = []
        for pair in self._result.pairs:
            band = pair.band
            if pair.key() in rejected:
                band = Band.DROP
            elif pair.key() in approved:
                band = Band.AUTO
            adjusted.append(Pair(pair.left, pair.right, pair.probability, band))
        return adjusted

    def _edges_within(self, members: tuple[str, ...]) -> tuple[ClusterEdgeView, ...]:
        """Every scored pair whose two ends both fall inside ``members``.

        Surfaces relationships beyond the single edge that grew the cluster: a
        three-record cluster built from two approvals still has a third,
        possibly still-pending, scored pair between its outer two members, and
        a reviewer deciding one pair should see that the other exists.
        """

        member_set = frozenset(members)
        key_to_index = self._key_to_index()
        edges: list[ClusterEdgeView] = []
        for pair in self._result.pairs:
            if pair.left not in member_set or pair.right not in member_set:
                continue
            index = key_to_index.get(pair.key())
            verdict = self._verdicts.get(index) if index is not None else None
            if verdict == APPROVED:
                status = "approved"
            elif verdict == REJECTED:
                status = "rejected"
            elif pair.band is Band.AUTO:
                status = "auto"
            elif pair.band is Band.REVIEW:
                status = "pending"
            else:
                status = "scored-apart"
            edges.append(
                ClusterEdgeView(
                    left=pair.left,
                    right=pair.right,
                    probability=pair.probability,
                    status=status,
                    pair_index=index,
                )
            )
        edges.sort(key=lambda edge: (-edge.probability, edge.left, edge.right))
        return tuple(edges)

    def _cluster_group(self, cluster: Cluster) -> ClusterGroupView:
        edges = self._edges_within(cluster.members)
        if len(cluster.members) < 2:
            only = cluster.members[0]
            singleton: tuple[ClusterMemberView, ...] = (
                ClusterMemberView(
                    record_id=only,
                    source=self._result.records[only].source,
                    is_primary=True,
                ),
            )
            return ClusterGroupView(members=singleton, edges=edges, golden=())

        [golden] = decisions.golden_records([cluster], self._result.records, self._fields)
        sources = _field_sources(
            golden.primary, cluster.members, self._result.records, self._fields
        )
        members = tuple(
            ClusterMemberView(
                record_id=member,
                source=self._result.records[member].source,
                is_primary=(member == golden.primary),
            )
            for member in cluster.members
        )
        golden_fields = tuple(
            GoldenFieldView(
                field=field_name,
                value=golden.fields.get(field_name, ""),
                source_id=sources.get(field_name, ""),
            )
            for field_name in self._fields
        )
        return ClusterGroupView(members=members, edges=edges, golden=golden_fields)

    def cluster_preview(self, index: int) -> ClusterPreview | None:
        """The cluster(s) and golden record(s) this pair's decision implies.

        Approving (or, for a still-undecided pair, previewing an approval)
        projects the pair as a confident merge on top of every other verdict
        already recorded, then clusters and reduces to a golden record exactly
        as ``reconcile apply`` would. Rejecting previews the two clusters kept
        apart instead. When the projected merge would pull in a record another
        rejection already separated, the preview reports a conflict rather than
        showing a cluster that ``reconcile apply`` would refuse to honor.
        """

        if not (0 <= index < len(self._pairs)):
            return None
        pair = self._pairs[index]
        verdict = self._verdicts.get(index)
        rejected_keys = frozenset(
            self._pairs[i].key() for i, v in self._verdicts.items() if v == REJECTED
        )
        intends_merge = verdict != REJECTED

        projected: list[Pair] = []
        for live in self._live_pairs():
            if intends_merge and live.key() == pair.key():
                live = Pair(live.left, live.right, live.probability, Band.AUTO)
            projected.append(live)

        clusters = decisions.build_clusters(self._result.records.keys(), projected)
        by_member = {member: cluster for cluster in clusters for member in cluster.members}
        left_cluster = by_member[pair.left]
        right_cluster = by_member[pair.right]

        naive_merged = pair.right in left_cluster.members
        conflict = False
        if naive_merged and rejected_keys:
            cluster_members = frozenset(left_cluster.members)
            conflict = any(key <= cluster_members for key in rejected_keys)
        merged = naive_merged and not conflict

        if conflict:
            groups: tuple[ClusterGroupView, ...] = ()
        elif merged:
            groups = (self._cluster_group(left_cluster),)
        else:
            groups = (self._cluster_group(left_cluster), self._cluster_group(right_cluster))

        return ClusterPreview(pair_index=index, merged=merged, conflict=conflict, groups=groups)

    # -- write access --------------------------------------------------------

    def record(self, index: int, verdict: str) -> None:
        """Record a verdict for one pair and write the decisions file through.

        Writing through on every decision means a reviewer who closes the browser
        keeps their progress. An unknown verdict raises rather than being stored,
        fail-closed.
        """

        if not (0 <= index < len(self._pairs)):
            raise IndexError(f"pair index {index} out of range")
        if verdict not in _VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}; expected approved or rejected")
        self._verdicts[index] = verdict
        self.save()

    def clear(self, index: int) -> None:
        """Reset a pair to undecided (the reviewer changed their mind)."""

        if self._verdicts.pop(index, None) is not None:
            self.save()

    def to_decisions(self) -> dict[str, list[list[str]]]:
        """The decisions payload in the shape ``reconcile apply`` consumes.

        Record ids and verdicts only; no field value is included, so the artifact
        carries no PII. This is the same JSON the CLI's ``apply`` reads.
        """

        approved: list[list[str]] = []
        rejected: list[list[str]] = []
        for index, verdict in sorted(self._verdicts.items()):
            pair = self._pairs[index]
            bucket = approved if verdict == APPROVED else rejected
            bucket.append([pair.left, pair.right])
        return {"approved": approved, "rejected": rejected}

    def save(self) -> None:
        self._decisions_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_decisions()
        self._decisions_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    @property
    def decisions_path(self) -> Path:
        return self._decisions_path

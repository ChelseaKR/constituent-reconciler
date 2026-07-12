"""Review session state and decision persistence.

A ``ReviewSession`` wraps a finished pipeline run and exposes the review pairs in
a stable order, tracks the verdicts on each pair, and writes those verdicts to a
decisions file. The session holds no socket and renders no HTML, so its logic is
unit-testable on its own; the server and the renderer build on it.

Every verdict is attributed: the session is opened by a named reviewer, and each
decision carries that name and a timestamp into the decisions file's ``audit``
section, so who decided each pair is answerable after the fact. Under two-person
mode (the DV pack's default) a merge only lands in the ``approved`` list once two
distinct reviewers have approved it; a lone approval is held as awaiting a second
reviewer, and any rejection rejects immediately, fail-closed.

The decisions file is the session's only side effect, and it carries record ids,
verdicts, reviewer names, and timestamps only. No field value of a reviewed
record is written, which is the minimization the DV pack requires of any artifact
the review step produces. Field corrections are the exception: because their
replacement values are PII, they are attributed and stored separately in
``corrections.json`` with the same local handling as resolved output.

Optional planted calibration pairs are interleaved into the in-memory queue.
Their verdicts are never written to the decisions file, so synthetic records
cannot reach ``reconcile apply`` or a connector.
"""

from __future__ import annotations

import json
import secrets
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from constituent_reconciler import decisions
from constituent_reconciler.models import Band, Cluster, Correction, Pair, Record, RunResult
from constituent_reconciler.review.calibration import PlantedPair
from constituent_reconciler.schema import DECISIONS_SCHEMA_VERSION

APPROVED = "approved"
REJECTED = "rejected"
# Not a verdict a reviewer can record: the derived state of a pair that has one
# approval under two-person mode and is waiting on a second, distinct name.
AWAITING_SECOND = "awaiting_second_reviewer"
_VERDICTS = frozenset({APPROVED, REJECTED})


def _warn_stale_pair(left: str, right: str) -> None:
    print(
        f"decision pair {left!r}, {right!r} is not in this run's review queue",
        file=sys.stderr,
    )


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
    synthetic: bool = False
    note: str = ""


@dataclass(frozen=True)
class Counts:
    approved: int
    rejected: int
    pending: int
    awaiting_second: int = 0
    corrected: int = 0


@dataclass(frozen=True)
class ReviewEntry:
    """One reviewer's recorded verdict on one pair, with when it was made."""

    reviewer: str
    verdict: str
    decided_at: str


def _clean_reviewer(name: str) -> str:
    """Validate a reviewer name. Blank is refused, fail-closed.

    An unattributed verdict would defeat the audit trail, so an empty or
    whitespace-only name raises rather than being stored.
    """

    cleaned = name.strip()
    if not cleaned:
        raise ValueError("reviewer name must not be blank; every verdict is attributed")
    return cleaned


# The reviewer name attached to verdicts resumed from a version-1 decisions
# file, which carried no attribution. Under two-person mode such an approval
# counts as one name, so a real second reviewer is still required, fail-closed.
UNRECORDED_REVIEWER = "unrecorded"


def _interleave_pairs(
    real: Sequence[Pair], planted: Sequence[PlantedPair]
) -> tuple[tuple[Pair, ...], frozenset[int]]:
    """Spread planted pairs through the queue without reordering real pairs."""

    entries: list[tuple[Pair, bool]] = [(pair, False) for pair in real]
    total = len(real) + len(planted)
    for offset, item in enumerate(planted):
        position = ((offset + 1) * total) // (len(planted) + 1)
        entries.insert(min(position, len(entries)), (item.pair, True))
    pairs = tuple(pair for pair, _ in entries)
    synthetic = frozenset(index for index, (_, planted_pair) in enumerate(entries) if planted_pair)
    return pairs, synthetic


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
    """Stateful review over a run's uncertain pairs, persisted to a file.

    The session is opened by one named reviewer; every verdict it records is
    attributed to that name (or to an explicit ``reviewer`` argument) with a
    UTC timestamp. With ``require_second_reviewer`` a pair only becomes
    approved once two distinct reviewer names have approved it.
    """

    def __init__(
        self,
        result: RunResult,
        fields: tuple[str, ...],
        decisions_path: Path,
        *,
        reviewer: str,
        privacy_mode: bool = False,
        require_second_reviewer: bool = False,
        calibration: Sequence[PlantedPair] = (),
    ) -> None:
        self._result = result
        self._fields = fields
        self._decisions_path = decisions_path
        self.reviewer = _clean_reviewer(reviewer)
        self.privacy_mode = privacy_mode
        self.require_second_reviewer = require_second_reviewer
        # A per-run secret embedded in every rendered form and checked on every
        # POST (FIX-01), so a page the reviewer has open elsewhere cannot forge
        # a verdict against this server: it cannot know a token it never saw.
        # Regenerated each time a session is constructed, never persisted.
        self.token = secrets.token_urlsafe(24)
        # The same ordering the review_queue.csv uses, so the two surfaces agree.
        real_pairs = tuple(
            sorted(result.review_pairs, key=lambda p: (-p.probability, p.left, p.right))
        )
        self._pairs, self._synthetic_indexes = _interleave_pairs(real_pairs, calibration)
        self._planted_records = {
            record.unique_id: record for item in calibration for record in (item.left, item.right)
        }
        self._known_answers = {item.pair.key(): item.known_answer for item in calibration}
        self._entries: dict[int, list[ReviewEntry]] = {}
        self._corrections: dict[int, dict[str, Correction]] = {}
        self._load_existing()
        self._load_corrections()

    # -- construction helpers ------------------------------------------------

    def _key_to_index(self) -> dict[frozenset[str], int]:
        return {
            pair.key(): index
            for index, pair in enumerate(self._pairs)
            if index not in self._synthetic_indexes
        }

    def _load_existing(self) -> None:
        """Resume from an existing decisions file, if one is present.

        Decisions are keyed by the unordered pair of record ids, so a resumed
        verdict re-attaches to the same candidate even if the pair ordering
        differs. Unknown pairs in the file are reported and skipped; a stale
        decision should not fail a review session, but it should be visible.
        Both shapes are read: the version-2 ``audit`` section with attributed
        verdicts, and the version-1 flat ``approved``/``rejected`` lists, whose
        verdicts resume attributed to ``unrecorded``.
        """

        if not self._decisions_path.exists():
            return
        try:
            data = json.loads(self._decisions_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        index_of = self._key_to_index()
        audit = data.get("audit")
        if isinstance(audit, dict):
            self._load_audit(audit, index_of)
            return
        for verdict, key in ((APPROVED, "approved"), (REJECTED, "rejected")):
            for entry in data.get(key, []):
                if not isinstance(entry, list) or len(entry) != 2:
                    continue
                pair_key = frozenset((str(entry[0]), str(entry[1])))
                index = index_of.get(pair_key)
                if index is not None:
                    self._entries[index] = [
                        ReviewEntry(reviewer=UNRECORDED_REVIEWER, verdict=verdict, decided_at="")
                    ]
                else:
                    _warn_stale_pair(str(entry[0]), str(entry[1]))

    def _load_audit(self, audit: dict[str, object], index_of: dict[frozenset[str], int]) -> None:
        for key, raw_entries in audit.items():
            ids = key.split("|")
            if len(ids) != 2 or not isinstance(raw_entries, list):
                continue
            index = index_of.get(frozenset(ids))
            if index is None:
                _warn_stale_pair(ids[0], ids[1])
                continue
            entries: list[ReviewEntry] = []
            for raw in raw_entries:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("reviewer", "")).strip()
                verdict = str(raw.get("verdict", ""))
                if not name or verdict not in _VERDICTS:
                    continue
                entries.append(
                    ReviewEntry(
                        reviewer=name,
                        verdict=verdict,
                        decided_at=str(raw.get("decided_at", "")),
                    )
                )
            if entries:
                self._entries[index] = entries

    def _load_corrections(self) -> None:
        """Resume only attributed corrections backed by the matching audit entry."""

        if not self.corrections_path.exists():
            return
        try:
            data = json.loads(self.corrections_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        index_of = self._key_to_index()
        raw_corrections = data.get("corrections", []) if isinstance(data, dict) else []
        if not isinstance(raw_corrections, list):
            return
        for raw in raw_corrections:
            if not isinstance(raw, dict):
                continue
            left, right = str(raw.get("left", "")), str(raw.get("right", ""))
            side, field_name = str(raw.get("side", "")), str(raw.get("field", ""))
            value = str(raw.get("value", ""))
            reviewer, corrected_at = str(raw.get("reviewer", "")), str(raw.get("corrected_at", ""))
            index = index_of.get(frozenset((left, right)))
            if (
                index is None
                or side not in {"left", "right"}
                or field_name not in self._fields
                or not value.strip()
                or not reviewer.strip()
                or not corrected_at.strip()
            ):
                continue
            matching_approval = any(
                entry.reviewer == reviewer
                and entry.verdict == APPROVED
                and entry.decided_at == corrected_at
                for entry in self._entries.get(index, ())
            )
            if not matching_approval:
                continue
            pair = self._pairs[index]
            self._corrections.setdefault(index, {})[field_name] = Correction(
                record_id=pair.left if side == "left" else pair.right,
                field=field_name,
                value=value,
                reviewer=reviewer,
                corrected_at=corrected_at,
                pair=pair.key(),
            )

    # -- read access ---------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self._pairs)

    @property
    def calibration_total(self) -> int:
        return len(self._synthetic_indexes)

    def _record_for(self, record_id: str) -> Record:
        planted = self._planted_records.get(record_id)
        if planted is not None:
            return planted
        return self._result.records[record_id]

    def _build_view(self, index: int, pair: Pair) -> PairView:
        left = self._record_for(pair.left)
        right = self._record_for(pair.right)
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
            synthetic=index in self._synthetic_indexes,
            note=pair.note,
        )

    def views(self) -> list[PairView]:
        return [self._build_view(i, pair) for i, pair in enumerate(self._pairs)]

    def view(self, index: int) -> PairView | None:
        if 0 <= index < len(self._pairs):
            return self._build_view(index, self._pairs[index])
        return None

    def verdict(self, index: int) -> str | None:
        """The pair's effective state: approved, rejected, awaiting, or None.

        A rejection by any reviewer rejects the pair outright; disagreement
        never merges. Under two-person mode an approval only becomes
        ``approved`` once two distinct reviewer names have approved, and until
        then the pair reads as awaiting a second reviewer.
        """

        entries = self._entries.get(index)
        if not entries:
            return None
        if any(entry.verdict == REJECTED for entry in entries):
            return REJECTED
        if index in self._synthetic_indexes:
            return APPROVED
        if self.require_second_reviewer and len(self.approvers(index)) < 2:
            return AWAITING_SECOND
        return APPROVED

    def audit(self, index: int) -> tuple[ReviewEntry, ...]:
        """Every recorded verdict on the pair, in the order they were made."""

        return tuple(self._entries.get(index, ()))

    def approvers(self, index: int) -> frozenset[str]:
        """The distinct reviewer names that currently approve the pair."""

        return frozenset(
            entry.reviewer for entry in self._entries.get(index, ()) if entry.verdict == APPROVED
        )

    def counts(self) -> Counts:
        states = [self.verdict(index) for index in range(len(self._pairs))]
        corrected = sum(
            1
            for index, state in enumerate(states)
            if state == APPROVED and bool(self._corrections.get(index))
        )
        approved = states.count(APPROVED) - corrected
        rejected = states.count(REJECTED)
        awaiting = states.count(AWAITING_SECOND)
        return Counts(
            approved=approved,
            rejected=rejected,
            pending=len(self._pairs) - approved - corrected - rejected - awaiting,
            awaiting_second=awaiting,
            corrected=corrected,
        )

    def corrections_for(self, index: int) -> dict[str, Correction]:
        return dict(self._corrections.get(index, {}))

    def next_undecided(self, after: int = -1) -> int | None:
        """Index of the first pair still open for this reviewer after ``after``.

        Wraps once to the start so a reviewer who jumps around still lands on an
        outstanding pair. A pair is open when it has no effective verdict, or
        when it awaits a second reviewer and this session's reviewer is not the
        one who already approved it. Returns None when nothing is left.
        """

        order = list(range(after + 1, len(self._pairs))) + list(range(0, after + 1))
        for index in order:
            state = self.verdict(index)
            if state is None:
                return index
            if state == AWAITING_SECOND and self.reviewer not in self.approvers(index):
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
            self._pairs[i].key() for i in range(len(self._pairs)) if self.verdict(i) == APPROVED
        )
        rejected = frozenset(
            self._pairs[i].key() for i in range(len(self._pairs)) if self.verdict(i) == REJECTED
        )
        adjusted: list[Pair] = []
        for pair in self._result.pairs:
            band = pair.band
            if pair.key() in rejected:
                band = Band.DROP
            elif pair.key() in approved:
                band = Band.AUTO
            adjusted.append(Pair(pair.left, pair.right, pair.probability, band, pair.note))
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
            verdict = self.verdict(index) if index is not None else None
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

        if not (0 <= index < len(self._pairs)) or index in self._synthetic_indexes:
            return None
        pair = self._pairs[index]
        verdict = self.verdict(index)
        rejected_keys = frozenset(
            self._pairs[i].key() for i in range(len(self._pairs)) if self.verdict(i) == REJECTED
        )
        intends_merge = verdict != REJECTED

        projected: list[Pair] = []
        for live in self._live_pairs():
            if intends_merge and live.key() == pair.key():
                live = Pair(live.left, live.right, live.probability, Band.AUTO, live.note)
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

    def record(self, index: int, verdict: str, reviewer: str | None = None) -> None:
        """Record one reviewer's verdict on a pair and write the file through.

        The verdict is attributed to ``reviewer`` (the session's reviewer when
        omitted) with a UTC timestamp. A reviewer who records on a pair they
        already decided overwrites their own entry; a different reviewer's
        entry is appended, which is how the second approval of two-person mode
        arrives. Writing through on every decision means a reviewer who closes
        the browser keeps their progress. An unknown verdict or a blank
        reviewer raises rather than being stored, fail-closed.
        """

        if not (0 <= index < len(self._pairs)):
            raise IndexError(f"pair index {index} out of range")
        if verdict not in _VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}; expected approved or rejected")
        name = self.reviewer if reviewer is None else _clean_reviewer(reviewer)
        if verdict == REJECTED:
            # A rejection means no corrected value from this pair may flow into
            # apply, regardless of who proposed it.
            self._corrections.pop(index, None)
        elif any(
            correction.reviewer == name for correction in self._corrections.get(index, {}).values()
        ):
            # The correcting reviewer explicitly chose approve-as-is later;
            # abandon only their own correction, not a different reviewer's.
            remaining = {
                field_name: correction
                for field_name, correction in self._corrections.get(index, {}).items()
                if correction.reviewer != name
            }
            if remaining:
                self._corrections[index] = remaining
            else:
                self._corrections.pop(index, None)
        entry = ReviewEntry(
            reviewer=name,
            verdict=verdict,
            decided_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        entries = [e for e in self._entries.get(index, []) if e.reviewer != name]
        entries.append(entry)
        self._entries[index] = entries
        self.save()

    def correct(self, index: int, *, field: str, side: str, value: str) -> None:
        """Replace one displayed value and restart approval from this reviewer.

        Changing evidence invalidates every earlier verdict on the pair. The
        correction itself counts as this reviewer's approval; in two-person
        mode a later distinct reviewer must see it and concur before apply.
        """

        if not (0 <= index < len(self._pairs)) or index in self._synthetic_indexes:
            raise IndexError(f"pair index {index} cannot be corrected")
        if field not in self._fields:
            raise ValueError(f"unknown field {field!r}; expected one of {self._fields}")
        if side not in {"left", "right"}:
            raise ValueError(f"unknown side {side!r}; expected 'left' or 'right'")
        if not value.strip():
            raise ValueError("a correction requires a non-blank replacement value")
        decided_at = datetime.now(UTC).isoformat(timespec="seconds")
        pair = self._pairs[index]
        correction = Correction(
            record_id=pair.left if side == "left" else pair.right,
            field=field,
            value=value,
            reviewer=self.reviewer,
            corrected_at=decided_at,
            pair=pair.key(),
        )
        # A new value changes the evidence; prior approvals and rejections no
        # longer describe what the next reviewer sees.
        self._entries[index] = [
            ReviewEntry(reviewer=self.reviewer, verdict=APPROVED, decided_at=decided_at)
        ]
        self._corrections[index] = {field: correction}
        self.save()

    def clear(self, index: int) -> None:
        """Reset a pair to undecided, clearing every reviewer's entry on it."""

        changed = self._entries.pop(index, None) is not None
        changed = self._corrections.pop(index, None) is not None or changed
        if changed:
            self.save()

    def _pair_key(self, index: int) -> str:
        pair = self._pairs[index]
        return "|".join(sorted((pair.left, pair.right)))

    def calibration_results(self) -> tuple[list[bool], list[bool]]:
        """Return this reviewer's decided planted verdicts and known answers."""

        reviewer_verdicts: list[bool] = []
        known_answers: list[bool] = []
        for index in sorted(self._synthetic_indexes):
            own_entries = [
                entry for entry in self._entries.get(index, ()) if entry.reviewer == self.reviewer
            ]
            if not own_entries:
                continue
            reviewer_verdicts.append(own_entries[-1].verdict == APPROVED)
            known_answers.append(self._known_answers[self._pairs[index].key()])
        return reviewer_verdicts, known_answers

    def to_decisions(self) -> dict[str, object]:
        """The decisions payload in the shape ``reconcile apply`` consumes.

        The top-level ``approved`` and ``rejected`` lists keep the version-1
        shape, and ``approved`` holds only pairs whose approval is complete, so
        ``apply`` cannot merge a pair still awaiting its second reviewer. The
        ``audit`` section maps ``left|right`` to every recorded verdict with
        its reviewer and timestamp, including held single approvals. Record
        ids, verdicts, reviewer names, and timestamps only; no field value of
        a reviewed record is included, so the artifact carries no client PII.
        """

        approved: list[list[str]] = []
        rejected: list[list[str]] = []
        audit: dict[str, list[dict[str, str]]] = {}
        for index in sorted(self._entries):
            if index in self._synthetic_indexes:
                continue
            pair = self._pairs[index]
            state = self.verdict(index)
            if state == APPROVED:
                approved.append([pair.left, pair.right])
            elif state == REJECTED:
                rejected.append([pair.left, pair.right])
            audit[self._pair_key(index)] = [
                {
                    "reviewer": entry.reviewer,
                    "verdict": entry.verdict,
                    "decided_at": entry.decided_at,
                }
                for entry in self._entries[index]
            ]
        return {
            "decisions_schema": DECISIONS_SCHEMA_VERSION,
            "approved": approved,
            "rejected": rejected,
            "audit": audit,
        }

    def save(self) -> None:
        self._decisions_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_decisions()
        self._decisions_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        corrections = self.to_corrections()
        if corrections["corrections"] or self.corrections_path.exists():
            self.corrections_path.write_text(
                json.dumps(corrections, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

    def to_corrections(self) -> dict[str, list[dict[str, str]]]:
        entries: list[dict[str, str]] = []
        for index in sorted(self._corrections):
            pair = self._pairs[index]
            for field_name in sorted(self._corrections[index]):
                correction = self._corrections[index][field_name]
                entries.append(
                    {
                        "left": pair.left,
                        "right": pair.right,
                        "side": "left" if correction.record_id == pair.left else "right",
                        "field": correction.field,
                        "value": correction.value,
                        "reviewer": correction.reviewer,
                        "corrected_at": correction.corrected_at,
                    }
                )
        return {"corrections": entries}

    @property
    def decisions_path(self) -> Path:
        return self._decisions_path

    @property
    def corrections_path(self) -> Path:
        return self._decisions_path.parent / "corrections.json"

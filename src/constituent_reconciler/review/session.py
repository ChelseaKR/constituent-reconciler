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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from constituent_reconciler.models import Pair, RunResult

APPROVED = "approved"
REJECTED = "rejected"
_VERDICTS = frozenset({APPROVED, REJECTED})

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
    """Everything the review screen needs for one candidate pair.

    ``note`` carries a routing explanation when the pipeline sent the pair to
    review for a reason beyond its score (a cannot-link constraint elsewhere in
    its cluster); it is empty for an ordinary uncertain pair.
    """

    index: int
    left_id: str
    right_id: str
    left_source: str
    right_source: str
    probability: float
    fields: tuple[FieldCell, ...]
    note: str = ""


@dataclass(frozen=True)
class Counts:
    approved: int
    rejected: int
    pending: int


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
            note=pair.note,
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

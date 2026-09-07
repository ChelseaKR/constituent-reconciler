"""Household grouping: a reviewed, off-by-default suggestion artifact.

This is a post-clustering step over golden records, not a matcher rule. It never
changes a match decision and it never auto-links records into a CRM's household
or relationship object; it produces a suggestion list a human reviews and, for
each suggestion they confirm, a CRM-import column carries the shared household
id (see ``connectors/crm_csv.py``).

Evidence is deliberately narrow: two golden records are suggested as the same
household only when they share an exact standardized address (``address.py``)
*and* an exact normalized surname. Address alone is not evidence of a household:
the ``defaults._address_comparison`` docstring already notes that shelter
residents share an address without being a family, and inferring co-residence
from address alone would turn that shared address into a household suggestion
for unrelated people, which is itself sensitive information to assert. Requiring
surname agreement on top of address agreement keeps the suggestion to the
narrower, defensible case (a shared last name at a shared address), and pushes
the harder case (a spouse with a different surname, an unrelated household)
to what it is: something only a human reviewer, with local knowledge, should add.

Every part of the pipeline that reasons about a "same person" edge stays in
``matching.py`` and ``decisions.py``; this module never feeds back into
clustering. A run must opt in explicitly (``[household] enabled = true`` in the
recipe); the default is off under every policy pack, including ``dv``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from constituent_reconciler.models import GoldenRecord

# The reviewer-facing artifact never auto-confirms a suggestion; this note is
# written into the suggestion CSV so a reviewer opening the file without other
# context still understands what the row is and is not.
REVIEW_NOTE = (
    "Suggestion only: shared standardized address and shared surname. Confirm "
    "or reject before this grouping is used anywhere. Not a match decision."
)


@dataclass(frozen=True)
class HouseholdSuggestion:
    """One candidate household: golden records sharing address and surname.

    ``household_id`` is stable across runs (it is derived from the sorted
    member cluster ids), so re-running the pipeline with unchanged input
    produces the same id and a reviewer's earlier confirmation (tracked by id
    in the decisions file) still applies.
    """

    household_id: str
    members: tuple[str, ...]
    address: str
    surname: str


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self._parent: dict[str, str] = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        self._parent[high] = low

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self._parent:
            out.setdefault(self.find(item), []).append(item)
        return out


def suggest_households(golden: Sequence[GoldenRecord]) -> list[HouseholdSuggestion]:
    """Group golden records into household suggestions from address+surname.

    Only golden records carrying both a non-empty normalized ``address`` and a
    non-empty normalized ``last_name`` are considered; a recipe that does not
    map both fields yields no suggestions (there is no evidence to group on).
    A group of size one (no agreeing peer) produces no suggestion: this is a
    grouping step, not a per-record label.

    Deterministic and side-effect free: it does not read or write files and it
    never touches ``result.golden`` or the clustering that produced it.
    """

    candidates = [
        g for g in golden if g.fields.get("address", "") and g.fields.get("last_name", "")
    ]
    if len(candidates) < 2:
        return []

    by_address: dict[str, list[GoldenRecord]] = {}
    for record in candidates:
        by_address.setdefault(record.fields["address"], []).append(record)

    suggestions: list[HouseholdSuggestion] = []
    for address, members in by_address.items():
        if len(members) < 2:
            continue
        union = _UnionFind(m.cluster_id for m in members)
        by_id = {m.cluster_id: m for m in members}
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                if left.fields["last_name"] == right.fields["last_name"]:
                    union.union(left.cluster_id, right.cluster_id)
        for ids in union.groups().values():
            if len(ids) < 2:
                continue
            ordered = tuple(sorted(ids))
            surname = by_id[ordered[0]].fields["last_name"]
            suggestions.append(
                HouseholdSuggestion(
                    household_id=f"HH-{ordered[0]}",
                    members=ordered,
                    address=address,
                    surname=surname,
                )
            )

    suggestions.sort(key=lambda s: s.household_id)
    return suggestions


def confirmed_member_map(
    suggestions: Sequence[HouseholdSuggestion],
    confirmed_ids: frozenset[str],
) -> dict[str, str]:
    """Map each member's cluster id to its household id, confirmed ids only.

    This is the only bridge from a suggestion to anything written into a CRM
    export: a suggestion whose ``household_id`` is not in ``confirmed_ids``
    contributes nothing here, so an un-reviewed grouping never reaches an
    import file.
    """

    out: dict[str, str] = {}
    for suggestion in suggestions:
        if suggestion.household_id not in confirmed_ids:
            continue
        for member in suggestion.members:
            out[member] = suggestion.household_id
    return out


# -- writing a confirmed household to a live CRM (EXP-07, #151) ---------------


#: External-id prefix for the household record a connector creates. Keyed so a
#: re-run finds the household it made last time instead of making a second one.
HOUSEHOLD_EXTERNAL_ID_PREFIX = "hh-"

#: Why a confirmed household was not written. Each is a different follow-up, so
#: they are never collapsed into one bucket, and none of them is silence.
SKIP_MEMBER_WITHHELD = "member-withheld"
SKIP_MEMBER_NOT_WRITTEN = "member-not-written"
SKIP_SINGLE_MEMBER = "single-member"


@dataclass(frozen=True)
class HouseholdWrite:
    """One confirmed household, ready to write, with every member accounted for."""

    household_id: str
    #: Member cluster id -> the external id that member was actually written
    #: under, in sorted member order. Both are needed: the relationship is
    #: created against the destination's id, and the report names the cluster.
    members: tuple[tuple[str, str], ...]

    @property
    def external_id(self) -> str:
        return f"{HOUSEHOLD_EXTERNAL_ID_PREFIX}{self.household_id}"


@dataclass(frozen=True)
class HouseholdSkip:
    """A confirmed household that was not written, and exactly why."""

    household_id: str
    reason: str
    #: The member ids responsible, so the report names people rather than a count.
    members: tuple[str, ...]


def plan_household_writes(
    suggestions: Sequence[HouseholdSuggestion],
    confirmed_ids: frozenset[str],
    written_external_ids: Mapping[str, str],
    withheld_ids: frozenset[str] = frozenset(),
) -> tuple[list[HouseholdWrite], list[HouseholdSkip]]:
    """Split confirmed households into the ones that may be written and the rest.

    ``written_external_ids`` maps a member's cluster id to the external id its
    own write landed under; a member absent from it was not written, whatever
    the reason. ``withheld_ids`` are the cluster ids the consent gate held back,
    passed separately so a withheld member is reported as withheld rather than
    as a generic failure.

    **A household with any withheld or unwritten member is not written at all.**
    The issue could be read as writing the household minus that member, and the
    "Done when" line about a withheld member yielding no relationship row is
    satisfied either way. This takes the more protective reading, for the reason
    the triage names: a partial household asserts a family relationship in the
    CRM on incomplete evidence, and the withheld member's *absence* from a
    household the reviewer confirmed as theirs is itself an inference about that
    person. Recording the skip with a named reason keeps the fact visible
    without publishing the inference. This is the one judgement in this feature
    that is arguable, and it is stated in the PR rather than buried.

    A single-member household is skipped too: there is no relationship to
    assert, and creating a household record for one person adds a grouping
    nobody confirmed.
    """

    writes: list[HouseholdWrite] = []
    skips: list[HouseholdSkip] = []
    for suggestion in sorted(suggestions, key=lambda s: s.household_id):
        if suggestion.household_id not in confirmed_ids:
            continue
        members = tuple(sorted(suggestion.members))
        withheld = tuple(member for member in members if member in withheld_ids)
        if withheld:
            skips.append(HouseholdSkip(suggestion.household_id, SKIP_MEMBER_WITHHELD, withheld))
            continue
        unwritten = tuple(member for member in members if member not in written_external_ids)
        if unwritten:
            skips.append(HouseholdSkip(suggestion.household_id, SKIP_MEMBER_NOT_WRITTEN, unwritten))
            continue
        if len(members) < 2:
            skips.append(HouseholdSkip(suggestion.household_id, SKIP_SINGLE_MEMBER, members))
            continue
        writes.append(
            HouseholdWrite(
                household_id=suggestion.household_id,
                members=tuple((member, written_external_ids[member]) for member in members),
            )
        )
    return writes, skips

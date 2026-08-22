"""Review-queue triage: order uncertain pairs by real signal, never a decision.

This module never calls a model. Ordering the review queue is a ranking
over data the deterministic pipeline already produced -- score, per-field
agreement pattern, and consent status -- so it stays available even when no
AI provider is configured, and a provider outage can never change queue
order. A caller that wants an AI-narrated reason for one item's position
passes that item's real evidence through ``match_explain.explain_match``
separately; narration and ranking are deliberately two different code
paths.

The ordering itself is a suggestion, not a decision: every pair still goes
through the same review UI and the same approve/correct/reject choice
regardless of its position in this list. ``docs/adr/0014`` records the
ordering rule below as the one this module implements, so it stays
auditable rather than an opaque score.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from constituent_reconciler.matching.evidence import PairEvidence
from constituent_reconciler.models import Consent, Pair

#: Comparison levels that count as a "disagreement" worth surfacing to a
#: reviewer -- not an exact match, and not simply absent (a null level is
#: "no evidence either way," not disagreement).
_DISAGREEMENT_LEVELS: frozenset[str] = frozenset({"different", "close"})


@dataclass(frozen=True)
class TriageItem:
    """One review-queue pair, ranked, with the real signal behind its rank.

    ``reasons`` is built entirely from real pipeline data (the pair's own
    score, its field-level evidence when available, and the two records'
    consent status) -- never from a model.
    """

    left_id: str
    right_id: str
    match_probability: float
    priority_rank: int
    consent_conflict: bool
    disagreeing_fields: tuple[str, ...]
    reasons: tuple[str, ...]


def _disagreeing_fields(evidence: PairEvidence | None) -> tuple[str, ...]:
    if evidence is None:
        return ()
    return tuple(
        fe.field
        for fe in evidence.fields
        if not fe.is_null_level and fe.level_label in _DISAGREEMENT_LEVELS
    )


def _consent_conflict(left_consent: Consent, right_consent: Consent, *, as_of: date) -> bool:
    return left_consent.is_active(as_of=as_of) != right_consent.is_active(as_of=as_of)


def triage_queue(
    pairs: Iterable[Pair],
    *,
    consents: Mapping[str, Consent],
    evidence: Mapping[tuple[str, str], PairEvidence] | None = None,
    as_of: date | None = None,
) -> tuple[TriageItem, ...]:
    """Order review-band pairs by real signal, most attention-worthy first.

    Ordering rule, stated so it is auditable rather than an opaque score:

    1. A consent conflict between the two members -- one active, one not --
       sorts first. A wrong decision here has a live consent-boundary
       consequence (which consent the merged identity inherits), not just
       a record-quality one, per ``models.Consent.most_restrictive``.
    2. Within each group, more disagreeing fields sorts first: a pair that
       disagrees on more than one field carries more for a reviewer to
       weigh than one differing on a single field, so it is less likely to
       be a fast, obvious call and more likely to need real attention.
    3. Ties break by match probability, highest first (the closest calls to
       an auto-merge among the remaining pairs), then by record id pair for
       a fully deterministic order.
    """

    effective_as_of = as_of if as_of is not None else date.today()
    items: list[TriageItem] = []
    for pair in pairs:
        left_consent = consents.get(pair.left, Consent())
        right_consent = consents.get(pair.right, Consent())
        conflict = _consent_conflict(left_consent, right_consent, as_of=effective_as_of)
        ordered_left, ordered_right = sorted((pair.left, pair.right))
        pair_evidence = (evidence or {}).get((ordered_left, ordered_right))
        disagreeing = _disagreeing_fields(pair_evidence)

        reasons: list[str] = []
        if conflict:
            reasons.append("one record's consent is currently active and the other's is not")
        if disagreeing:
            reasons.append(f"disagrees on: {', '.join(disagreeing)}")
        reasons.append(f"match probability {pair.probability:.2f}")

        items.append(
            TriageItem(
                left_id=pair.left,
                right_id=pair.right,
                match_probability=pair.probability,
                priority_rank=0,
                consent_conflict=conflict,
                disagreeing_fields=disagreeing,
                reasons=tuple(reasons),
            )
        )

    ordered = sorted(
        items,
        key=lambda item: (
            not item.consent_conflict,
            -len(item.disagreeing_fields),
            -item.match_probability,
            item.left_id,
            item.right_id,
        ),
    )
    return tuple(
        dataclasses.replace(item, priority_rank=index + 1) for index, item in enumerate(ordered)
    )

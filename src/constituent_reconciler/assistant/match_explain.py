"""Match explanation: plain-language narration of Splink's real comparison evidence.

The model narrates the arithmetic; it never re-scores. Every per-field claim
the model returns is checked against the real ``FieldEvidence`` for that
field (built by ``matching.evidence.comparison_evidence``, never by this
module) before it is allowed into a ``MatchExplanation``: the claimed
``level_label`` must exactly equal the level Splink actually assigned that
field for that pair. A claim that does not verify -- a hallucinated field, a
wrong level, a field the model invented that Splink never scored -- is
withheld from display and counted, never shown "on the model's word."

``refusal.enforce`` runs on the summary and on every per-field narrative
before anything else happens to it, so even a verified claim about a real
field can still be scrubbed if its wording crosses into a merge
recommendation or a certainty claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from constituent_reconciler.assistant import refusal
from constituent_reconciler.assistant._json import parse_json_object
from constituent_reconciler.assistant.evidence_payload import evidence_payload
from constituent_reconciler.assistant.prompts import (
    PROMPT_VERSION,
    REFUSAL_RULES,
    SCRUBBED_RESPONSE,
)
from constituent_reconciler.assistant.provider import Provider
from constituent_reconciler.matching.evidence import FieldEvidence, PairEvidence

_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    REFUSAL_RULES
    + """
You will be given, as JSON, the real field-by-field comparison evidence a
deterministic matching engine (Splink) computed for one candidate pair, plus
the pair's overall match probability. Your only job is to narrate that
evidence in plain, caseworker-friendly language: one short sentence per
field explaining what the comparison level means in practice (for example,
a "nickname" level means the two given names are a recognized nickname
pair, not a typo or an error), and a two-sentence overall summary
describing the pattern of agreement and disagreement without stating a
verdict.

Respond with ONLY a JSON object of exactly this shape, and nothing else:
{"summary": "<two sentences, no verdict>",
 "fields": [{"field": "<field name from the evidence>",
             "level_label": "<the exact level_label you were given for this field>",
             "narrative": "<one sentence>"}]}
Use only field names and level_label values that appear in the evidence you
were given -- copy level_label exactly, character for character. Do not
invent a field, a level, or a probability. A field marked "withheld" in the
evidence has no value available to you; do not describe or guess it.
"""
)


@dataclass(frozen=True)
class FieldClaim:
    """One field-level claim from the model, after verification.

    ``narrative`` is ``None`` when the claim did not verify (or was
    scrubbed by ``refusal.enforce``) -- the reviewer never sees an
    unverified claim's text, only that one was withheld. ``level_label`` is
    always the model's claimed value, kept for the eval harness even when
    unverified, so a grounding failure is inspectable.
    """

    field: str
    level_label: str
    narrative: str | None
    verified: bool
    withheld_reason: str | None = None


@dataclass(frozen=True)
class MatchExplanation:
    """A verified, advisory explanation of one pair's real comparison evidence.

    Always label AI-generated and advisory in any surface that renders
    this. ``summary`` never states a merge verdict (enforced by
    ``refusal.enforce``); it is replaced with the canned redirect message
    when the model's summary was scrubbed.
    """

    left_id: str
    right_id: str
    match_probability: float
    summary: str
    claims: tuple[FieldClaim, ...]
    withheld_fields: tuple[str, ...]
    provider: str
    model: str
    prompt_version: str
    scrubbed: bool

    def verified_claims(self) -> tuple[FieldClaim, ...]:
        return tuple(c for c in self.claims if c.verified)

    def withheld_claim_count(self) -> int:
        return sum(1 for c in self.claims if not c.verified)


def _verify_claim(entry: object, real_by_field: dict[str, FieldEvidence]) -> FieldClaim | None:
    if not isinstance(entry, dict):
        return None
    field_name = entry.get("field")
    narrative = entry.get("narrative")
    level_label = entry.get("level_label")
    if not isinstance(field_name, str) or not isinstance(narrative, str):
        return None
    claimed_level = level_label if isinstance(level_label, str) else ""

    safe_narrative, hits = refusal.enforce(narrative)
    if hits:
        return FieldClaim(
            field=field_name,
            level_label=claimed_level,
            narrative=None,
            verified=False,
            withheld_reason="scrubbed: prohibited language",
        )

    real = real_by_field.get(field_name)
    if real is None:
        return FieldClaim(
            field=field_name,
            level_label=claimed_level,
            narrative=None,
            verified=False,
            withheld_reason="unverifiable: field not in real evidence",
        )
    if claimed_level != real.level_label:
        return FieldClaim(
            field=field_name,
            level_label=claimed_level,
            narrative=None,
            verified=False,
            withheld_reason="unverifiable: claimed level does not match real evidence",
        )
    return FieldClaim(
        field=field_name, level_label=claimed_level, narrative=safe_narrative, verified=True
    )


def explain_match(
    evidence: PairEvidence,
    *,
    provider: Provider,
    withheld_fields: tuple[str, ...] = (),
) -> MatchExplanation:
    """Build a verified, plain-language explanation of one pair's real evidence.

    ``evidence`` must come from ``matching.evidence.comparison_evidence`` --
    the only source of truth this function narrates and checks against.
    ``withheld_fields`` names fields the consent/policy filter
    (``consent_filter.filter_record``) already withheld upstream; they are
    described to the model as withheld, never as absent-and-guessable.
    """

    payload = evidence_payload(evidence, withheld_fields)
    user_message = json.dumps(payload, sort_keys=True)
    result = provider.complete(system=_SYSTEM_PROMPT, user=user_message, max_tokens=_MAX_TOKENS)

    safe_text, scrub_hits = refusal.enforce(result.text)
    scrubbed = bool(scrub_hits)
    data = None if scrubbed else parse_json_object(safe_text)
    real_by_field = {fe.field: fe for fe in evidence.fields}

    summary = ""
    claims: list[FieldClaim] = []
    if data is not None:
        raw_summary = data.get("summary")
        if isinstance(raw_summary, str) and raw_summary.strip():
            safe_summary, summary_hits = refusal.enforce(raw_summary)
            if summary_hits:
                scrubbed = True
                summary = SCRUBBED_RESPONSE
            else:
                summary = safe_summary
        raw_fields = data.get("fields")
        if isinstance(raw_fields, list):
            for entry in raw_fields:
                claim = _verify_claim(entry, real_by_field)
                if claim is not None:
                    claims.append(claim)

    if not summary:
        summary = (
            SCRUBBED_RESPONSE if scrubbed else "No verified explanation is available for this pair."
        )

    return MatchExplanation(
        left_id=evidence.left_id,
        right_id=evidence.right_id,
        match_probability=evidence.match_probability,
        summary=summary,
        claims=tuple(claims),
        withheld_fields=withheld_fields,
        provider=result.provider,
        model=result.model,
        prompt_version=PROMPT_VERSION,
        scrubbed=scrubbed,
    )

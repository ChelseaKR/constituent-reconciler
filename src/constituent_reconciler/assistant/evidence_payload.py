"""Build the JSON payload sent to a model for one pair's real comparison evidence.

Shared by ``match_explain`` and ``ask`` so both features describe the same
pair to a model in exactly the same shape -- the explanation and the answer
to a follow-up question are always grounded in identical evidence, never two
slightly different renderings of it.
"""

from __future__ import annotations

from constituent_reconciler.matching.evidence import PairEvidence

#: Fields never described by literal value in a prompt, even though the
#: comparison *level* for them is shown. Explaining a "close" or
#: "different" email/phone comparison never requires the literal contact
#: value, and those two fields are the most directly reusable for
#: contacting or locating a person -- a deliberate minimization choice for
#: these two narration-only features specifically (``ocr_propose`` does not
#: use this constant: proposing a corrected email or phone from OCR text is
#: exactly what it exists to do).
REDACT_FROM_EXPLANATION: frozenset[str] = frozenset({"email", "phone"})


def evidence_payload(
    evidence: PairEvidence, withheld_fields: tuple[str, ...] = ()
) -> dict[str, object]:
    """Render one pair's real evidence as a JSON-serializable payload.

    ``withheld_fields`` names fields the consent/policy filter
    (``consent_filter.filter_record``) already withheld upstream of the
    matcher even seeing them; they are described to the model as withheld,
    never as silently absent (which would read as "no evidence" rather than
    "not authorized to see"). Critically, a field named in
    ``withheld_fields`` is redacted here even when Splink's own
    ``evidence.fields`` also carries a real entry for it (the matcher scores
    every configured field regardless of consent, so it very much can) --
    the ``withheld_fields`` check runs first and ``continue``s, so the real
    ``left_value``/``right_value`` for that field is never appended
    alongside the withheld marker. An earlier version of this function only
    *appended* a withheld marker without suppressing the real entry, which
    let a consent-withheld field's actual value reach the prompt payload
    anyway; ``tools/ai_eval/consent_leakage.py`` exists to catch exactly
    that class of bug, and ``tests/test_assistant_evidence_payload.py``
    pins the fix as a regression test.
    """
    withheld = set(withheld_fields)
    fields: list[dict[str, object]] = []
    seen: set[str] = set()
    for fe in evidence.fields:
        seen.add(fe.field)
        if fe.field in REDACT_FROM_EXPLANATION or fe.field in withheld:
            fields.append({"field": fe.field, "status": "withheld", "level_label": fe.level_label})
            continue
        fields.append(
            {
                "field": fe.field,
                "left_value": fe.left_value,
                "right_value": fe.right_value,
                "level_label": fe.level_label,
                "m_probability": fe.m_probability,
                "u_probability": fe.u_probability,
                "bayes_factor": round(fe.bayes_factor, 4),
            }
        )
    for name in withheld_fields:
        if name not in seen:
            fields.append({"field": name, "status": "withheld"})
    return {
        "left_id": evidence.left_id,
        "right_id": evidence.right_id,
        "match_probability": round(evidence.match_probability, 4),
        "fields": fields,
    }

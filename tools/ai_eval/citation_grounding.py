"""Citation grounding eval: how often the model's claims verify against
real Splink comparison evidence.

Sends synthetic pairs through the real ``assistant.match_explain.
explain_match()`` path against a live provider. Every claim the model
returns is already checked by ``explain_match`` itself before this eval
ever sees it (``FieldClaim.verified``); this eval's job is to measure and
report the live grounding rate, not to re-implement the check. A low
grounding rate here is a model-quality signal (the model is not reliably
copying ``level_label`` verbatim), not a safety failure -- an unverified
claim is withheld from the reviewer either way, by construction.
"""

from __future__ import annotations

from typing import Any

from constituent_reconciler.assistant.match_explain import explain_match
from constituent_reconciler.assistant.provider import Provider
from tools.ai_eval.fixtures import ambiguous_pair, nickname_pair
from tools.ai_eval.provenance import Provenance


def run(provider: Provider) -> dict[str, Any]:
    pairs = [nickname_pair(), ambiguous_pair()]
    per_pair: list[dict[str, Any]] = []
    total_claims = 0
    total_verified = 0

    for evidence in pairs:
        explanation = explain_match(evidence, provider=provider)
        total_claims += len(explanation.claims)
        total_verified += len(explanation.verified_claims())
        per_pair.append(
            {
                "left_id": explanation.left_id,
                "right_id": explanation.right_id,
                "claims_returned": len(explanation.claims),
                "claims_verified": len(explanation.verified_claims()),
                "claims_withheld": explanation.withheld_claim_count(),
                "scrubbed": explanation.scrubbed,
                "summary": explanation.summary,
                "fields": [
                    {
                        "field": c.field,
                        "verified": c.verified,
                        "level_label_claimed": c.level_label,
                        "narrative": c.narrative,
                        "withheld_reason": c.withheld_reason,
                    }
                    for c in explanation.claims
                ],
            }
        )

    grounding_rate = (total_verified / total_claims) if total_claims else None
    provenance = Provenance.stamp(provider=provider.name, model=provider.model, status="ran")
    return {
        "eval": "citation_grounding",
        **provenance.as_dict(),
        "pairs_evaluated": len(pairs),
        "total_claims_returned": total_claims,
        "total_claims_verified": total_verified,
        "grounding_rate": grounding_rate,
        "pairs": per_pair,
    }


def not_run() -> dict[str, Any]:
    provenance = Provenance.stamp(provider="not run", model="not run", status="not run")
    return {
        "eval": "citation_grounding",
        **provenance.as_dict(),
        "reason": "no AI provider was configured/enabled when the eval harness ran",
    }

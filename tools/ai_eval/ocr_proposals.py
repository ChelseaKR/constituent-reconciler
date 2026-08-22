"""OCR correction proposal eval: precision, abstention, and invention.

Sends every case in ``fixtures.OCR_CASES`` through the real
``assistant.ocr_propose.propose_correction()`` path against a live
provider. Three numbers matter, in order of how much a wrong answer costs:

* **Invented a plausible value** (the failure mode that matters most): the
  model returned ``verified=True`` on a case whose gold answer is
  "abstain" -- a hallucinated or misattributed correction that quote-
  verification's substring check did not catch because the quoted text is
  real but describes the wrong thing (see ``wrong_person_trap`` and
  ``similar_but_different_person_trap`` in fixtures.py, which exist
  specifically to probe this). A hallucinated field value is worse than a
  blank, so this number is reported on its own, not folded into precision.
* **Abstained when the document doesn't say**: correct abstention rate on
  the cases whose gold answer is "abstain."
* **Precision**: of the cases where a correction was gold-expected, the
  share where the model's verified proposal matches the gold value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from constituent_reconciler.assistant.ocr_propose import propose_correction
from constituent_reconciler.assistant.provider import Provider
from tools.ai_eval.fixtures import OCR_CASES, OCRCase
from tools.ai_eval.provenance import Provenance


@dataclass(frozen=True)
class OCRCaseResult:
    case: OCRCase
    verified: bool
    proposed_value: str | None
    abstained: bool
    abstain_reason: str | None
    correct: bool
    invented: bool


def _matches_gold(proposed: str | None, gold: str | None) -> bool:
    if proposed is None or gold is None:
        return False
    return proposed.strip().casefold() == gold.strip().casefold()


def run(provider: Provider) -> dict[str, Any]:
    results: list[OCRCaseResult] = []
    for case in OCR_CASES:
        proposal = propose_correction(
            record_id=case.name,
            field=case.field,
            original_value=case.garbled_value,
            source_text=case.source_text,
            provider=provider,
        )
        should_abstain = case.gold_value is None
        invented = proposal.verified and should_abstain
        correct = (
            (should_abstain and proposal.abstained and not invented)
            if should_abstain
            else _matches_gold(proposal.proposed_value, case.gold_value)
        )
        results.append(
            OCRCaseResult(
                case=case,
                verified=proposal.verified,
                proposed_value=proposal.proposed_value,
                abstained=proposal.abstained,
                abstain_reason=proposal.abstain_reason,
                correct=correct,
                invented=invented,
            )
        )

    propose_expected = [r for r in results if r.case.gold_value is not None]
    abstain_expected = [r for r in results if r.case.gold_value is None]
    invented_cases = [r for r in results if r.invented]

    precision = (
        sum(r.correct for r in propose_expected) / len(propose_expected)
        if propose_expected
        else None
    )
    abstention_rate = (
        sum(r.correct for r in abstain_expected) / len(abstain_expected)
        if abstain_expected
        else None
    )

    provenance = Provenance.stamp(provider=provider.name, model=provider.model, status="ran")
    return {
        "eval": "ocr_proposals",
        **provenance.as_dict(),
        "total_cases": len(results),
        "propose_expected_cases": len(propose_expected),
        "abstain_expected_cases": len(abstain_expected),
        "precision_on_propose_expected": precision,
        "correct_abstention_rate": abstention_rate,
        "invented_plausible_value_count": len(invented_cases),
        "invented_cases": [
            {"case": r.case.name, "field": r.case.field, "proposed_value": r.proposed_value}
            for r in invented_cases
        ],
        "cases": [
            {
                "case": r.case.name,
                "field": r.case.field,
                "gold_expects_abstain": r.case.gold_value is None,
                "gold_value": r.case.gold_value,
                "verified": r.verified,
                "proposed_value": r.proposed_value,
                "abstained": r.abstained,
                "abstain_reason": r.abstain_reason,
                "correct": r.correct,
                "invented": r.invented,
            }
            for r in results
        ],
    }


def not_run() -> dict[str, Any]:
    provenance = Provenance.stamp(provider="not run", model="not run", status="not run")
    return {
        "eval": "ocr_proposals",
        **provenance.as_dict(),
        "total_cases": len(OCR_CASES),
        "reason": "no AI provider was configured/enabled when the eval harness ran",
    }

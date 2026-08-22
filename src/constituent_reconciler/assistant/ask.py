"""A constrained Q&A surface for a reviewer, refusal-first by design.

This is the primary target of the auto-merge-refusal adversarial eval
(``eval/ai/adversarial_refusal.py``): every prohibited phrasing in that
suite ("just merge these," "are these the same person," "which should I
keep," "do it for me," fatigue-framing, authority-framing, and their
Spanish equivalents) is sent through :func:`ask`. Two independent defenses
apply, in order:

1. ``prompts.REFUSAL_RULES`` in the system prompt instructs the model never
   to answer a merge question, in either direction, regardless of how the
   question is phrased or how many times it has been asked.
2. ``refusal.enforce`` runs on the raw response before anything else
   happens to it. A reviewer never sees unscrubbed model text; a response
   the scanner flags is replaced wholesale with the canned redirect
   message, regardless of what the model actually produced.

The eval scores the combined system end to end, not the prompt alone --
that is the honest thing to measure, since (2) is what actually protects a
reviewer if (1) is ever insufficient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from constituent_reconciler.assistant import refusal
from constituent_reconciler.assistant.evidence_payload import evidence_payload
from constituent_reconciler.assistant.prompts import PROMPT_VERSION, REFUSAL_RULES
from constituent_reconciler.assistant.provider import Provider
from constituent_reconciler.matching.evidence import PairEvidence

_MAX_TOKENS = 512
_MAX_QUESTION_CHARS = 2000

_SYSTEM_PROMPT = (
    REFUSAL_RULES
    + """
A reviewer is looking at one candidate pair from a review queue and has
typed a free-text question. You will be given the same real, field-by-field
comparison evidence used for match explanations (never anything beyond it)
and the reviewer's question. Answer using only that evidence. If the
evidence does not answer the question, say so plainly rather than guessing.

Respond in plain text, not JSON: one to three sentences, no preamble, no
markdown formatting.
"""
)


@dataclass(frozen=True)
class AskResponse:
    """A reviewer's question and the (possibly scrubbed) answer.

    Always label AI-generated and advisory in any surface that renders
    this. ``scrubbed`` is True whenever the deterministic scanner replaced
    the model's actual text with the canned redirect message; the
    adversarial eval's pass/fail criterion is built from this field and
    ``scrub_reasons``, not from inspecting the model's raw output, because
    the raw output is never what a reviewer would have seen.
    """

    question: str
    answer: str
    scrubbed: bool
    scrub_reasons: tuple[str, ...]
    provider: str
    model: str
    prompt_version: str


def ask(
    question: str,
    *,
    evidence: PairEvidence,
    provider: Provider,
    withheld_fields: tuple[str, ...] = (),
) -> AskResponse:
    """Answer a reviewer's free-text question about one pair, refusal-first."""

    truncated_question = question.strip()[:_MAX_QUESTION_CHARS]
    payload = {
        "evidence": evidence_payload(evidence, withheld_fields),
        "question": truncated_question,
    }
    user_message = json.dumps(payload, sort_keys=True)
    result = provider.complete(system=_SYSTEM_PROMPT, user=user_message, max_tokens=_MAX_TOKENS)
    safe_text, hits = refusal.enforce(result.text)
    return AskResponse(
        question=truncated_question,
        answer=safe_text,
        scrubbed=bool(hits),
        scrub_reasons=hits,
        provider=result.provider,
        model=result.model,
        prompt_version=PROMPT_VERSION,
    )

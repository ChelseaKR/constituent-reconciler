"""OCR correction proposals: quote-bound, draft-only, never auto-applied.

Intake OCR (``extract/ocr.py``, ``extract/pdf.py``, and the low-confidence
cloud/local seams in ``extract/seam.py``) can garble a field. This module
lets a model propose a correction for one such field, but only under one
hard rule: a proposal is accepted only when the model quotes, character for
character (after whitespace normalization), the exact substring of the
source document text that supports it. A quote that does not verify against
the real source text -- or a model response that proposes nothing, or
proposes without a quote -- is withheld and treated as an abstention. The
failure mode this exists to prevent is not "too many abstentions"; it is a
plausible-looking, invented value (an address or a donation amount the
document never actually says), which is worse than no proposal at all.

A proposal is always a draft. Nothing here writes to a record, to
``out/corrections.json``, or anywhere the pipeline reads from. Turning an
accepted proposal into an applied correction is the same human, reviewer-
attributed path every other correction already goes through
(``models.Correction``, the review server's correct action, or
``constituent-reconcile apply --decisions``) -- this module does not shortcut it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from constituent_reconciler.assistant import refusal
from constituent_reconciler.assistant._json import parse_json_object
from constituent_reconciler.assistant.prompts import PROMPT_VERSION, REFUSAL_RULES
from constituent_reconciler.assistant.provider import Provider

_MAX_TOKENS = 512
#: Keep the prompt small and bounded; a source excerpt beyond this is
#: truncated before it ever reaches the model, and the quote check runs
#: against the same truncated text the model saw.
_MAX_SOURCE_CHARS = 6000

_SYSTEM_PROMPT = (
    REFUSAL_RULES
    + """
You are checking one intake-form field an offline OCR/extraction step read
from a source document; the value may be garbled. You will be given the
field name, the value that was extracted, and the plain text of the source
document (or the relevant excerpt of it). Your only job is to say whether
the source text actually supports a different, corrected value for that
field.

You may propose a correction ONLY if you can quote, character for
character, the exact substring of the source text that supports it. If the
source text does not clearly support a specific corrected value, or you are
not confident, abstain -- never guess, and never invent a plausible-looking
value the text does not actually contain.

Respond with ONLY a JSON object of exactly one of these two shapes:
{"abstain": true, "reason": "<why the source does not support a correction>"}
or
{"abstain": false, "proposed_value": "<the corrected field value>",
 "quote": "<the exact substring of the source text that supports it>"}
"""
)


@dataclass(frozen=True)
class OCRProposal:
    """One field's draft correction, or a recorded abstention.

    ``verified`` is True only for an accepted proposal whose ``quote``
    passed :func:`_quote_verifies` against the real source text. Every
    other outcome -- the model abstained, proposed without a quote, or
    proposed a quote that does not actually appear in the source -- comes
    back as ``abstained=True`` with ``verified=False``; a caller must never
    show ``proposed_value`` unless ``verified`` is True.
    """

    field: str
    record_id: str
    original_value: str
    proposed_value: str | None
    quote: str | None
    abstained: bool
    abstain_reason: str | None
    verified: bool
    provider: str
    model: str
    prompt_version: str


def _normalize_for_match(text: str) -> str:
    return " ".join(text.split())


def _quote_verifies(quote: str, source_text: str) -> bool:
    """Whether ``quote`` is an exact, whitespace-normalized substring of
    ``source_text``. This is the entire trust boundary for a proposal.
    """
    if not quote.strip():
        return False
    return _normalize_for_match(quote) in _normalize_for_match(source_text)


def propose_correction(
    *,
    record_id: str,
    field: str,
    original_value: str,
    source_text: str,
    provider: Provider,
) -> OCRProposal:
    """Propose a quote-bound correction for one garbled field, or abstain."""

    truncated_source = source_text[:_MAX_SOURCE_CHARS]
    payload = {
        "field": field,
        "extracted_value": original_value,
        "source_text": truncated_source,
    }
    user_message = json.dumps(payload, sort_keys=True)
    result = provider.complete(system=_SYSTEM_PROMPT, user=user_message, max_tokens=_MAX_TOKENS)

    safe_text, hits = refusal.enforce(result.text)
    data = None if hits else parse_json_object(safe_text)

    def abstain(reason: str) -> OCRProposal:
        return OCRProposal(
            field=field,
            record_id=record_id,
            original_value=original_value,
            proposed_value=None,
            quote=None,
            abstained=True,
            abstain_reason=reason,
            verified=False,
            provider=result.provider,
            model=result.model,
            prompt_version=PROMPT_VERSION,
        )

    if data is None:
        return abstain("no usable response")
    if data.get("abstain") is True:
        reason = data.get("reason")
        return abstain(reason if isinstance(reason, str) and reason.strip() else "model abstained")
    if data.get("abstain") is not False:
        return abstain("response did not clearly abstain or propose")

    proposed_value = data.get("proposed_value")
    quote = data.get("quote")
    if not isinstance(proposed_value, str) or not proposed_value.strip():
        return abstain("no proposed value given")
    if not isinstance(quote, str) or not _quote_verifies(quote, truncated_source):
        return abstain("quote could not be verified against the source text")

    return OCRProposal(
        field=field,
        record_id=record_id,
        original_value=original_value,
        proposed_value=proposed_value.strip(),
        quote=quote.strip(),
        abstained=False,
        abstain_reason=None,
        verified=True,
        provider=result.provider,
        model=result.model,
        prompt_version=PROMPT_VERSION,
    )

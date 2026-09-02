"""AI at the edges: an opt-in advisory layer over the deterministic pipeline.

Nothing in this package decides a match. ``matching.evidence`` and
``decisions.py`` remain the only code that ever produces a match
probability or a band; this package only narrates, proposes drafts, orders
a queue for attention, and answers grounded questions, always labeled
AI-generated and advisory. Design and architecture:
docs/adr/0014-runtime-ai-at-the-edges.md.

No module in ``constituent_reconciler`` outside this package imports
anything from it, and nothing in this package is imported by ``pipeline.py``,
``decisions.py``, or the ``run``/``review``/``apply`` CLI commands --
``tests/test_no_ai_in_deterministic_path.py`` asserts this by construction.
The offline-first deterministic pipeline runs, byte-for-byte, exactly as it
did before this package existed.

Every feature module in this package follows the same shape:

* ``consent_filter.filter_record`` runs on every ``Record`` before a prompt
  is built, so a withheld field never reaches a provider call.
* ``consent_filter.assert_cloud_ai_allowed`` runs once, before anything
  else, and refuses outright under a policy pack that forbids cloud calls
  (``dv``, ``hipaa``) -- the same non-egress invariant already binding
  ``extract/seam.py``'s cloud extraction seam.
* ``provider.make_provider`` returns a configured model client; credentials
  come from the environment only, and this module never writes one to disk.
* ``rate_limit.RateLimiter`` enforces a per-minute rate and a hard daily
  cap before any provider call.
* ``refusal.enforce`` runs on every model response before display; a
  response it flags is replaced with a canned redirect message regardless
  of what the model said.
* Every AI-sourced claim shown to a reviewer is checked against real
  pipeline data (``matching.evidence.PairEvidence`` for match explanations
  and Q&A, or an exact source-text quote for OCR proposals) before display;
  an unverifiable claim is withheld and counted, never shown "on the
  model's word."
"""

from __future__ import annotations

from constituent_reconciler.assistant.ask import AskResponse, ask
from constituent_reconciler.assistant.consent_filter import (
    AI_DESTINATION,
    FilteredField,
    FilteredRecord,
    assert_cloud_ai_allowed,
    filter_record,
)
from constituent_reconciler.assistant.errors import (
    AssistantError,
    ProviderCallFailed,
    ProviderNotConfigured,
    RateLimitExceeded,
    SourceDocumentUnavailable,
    VerificationFailed,
)
from constituent_reconciler.assistant.match_explain import (
    FieldClaim,
    MatchExplanation,
    explain_match,
)
from constituent_reconciler.assistant.ocr_propose import OCRProposal, propose_correction
from constituent_reconciler.assistant.prompts import PROMPT_VERSION
from constituent_reconciler.assistant.provider import (
    AnthropicProvider,
    BedrockProvider,
    Provider,
    ProviderResult,
    make_provider,
)
from constituent_reconciler.assistant.rate_limit import RateLimiter
from constituent_reconciler.assistant.triage import TriageItem, triage_queue

__all__ = [
    "AI_DESTINATION",
    "AnthropicProvider",
    "AskResponse",
    "AssistantError",
    "BedrockProvider",
    "FieldClaim",
    "FilteredField",
    "FilteredRecord",
    "MatchExplanation",
    "OCRProposal",
    "PROMPT_VERSION",
    "Provider",
    "ProviderCallFailed",
    "ProviderNotConfigured",
    "ProviderResult",
    "RateLimitExceeded",
    "RateLimiter",
    "SourceDocumentUnavailable",
    "TriageItem",
    "VerificationFailed",
    "ask",
    "assert_cloud_ai_allowed",
    "explain_match",
    "filter_record",
    "make_provider",
    "propose_correction",
    "triage_queue",
]

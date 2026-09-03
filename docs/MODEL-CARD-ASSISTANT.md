# Model card: the AI assistant package

Companion to [MODEL-CARD.md](MODEL-CARD.md), which covers the extraction
seam. This card covers the second, larger model-touching surface:
`constituent_reconciler.assistant`, the opt-in advisory layer over the
review queue (match explanation, a refusal-first Q&A surface, quote-bound
OCR correction proposals, and deterministic review-queue triage). Design
and architecture: [docs/adr/0014-runtime-ai-at-the-edges.md](adr/0014-runtime-ai-at-the-edges.md).
Source of truth for every claim here: `src/constituent_reconciler/assistant/`.

## Model details

* **What it is.** Four opt-in CLI commands (`ai-explain`, `ai-ask`,
  `ai-propose-corrections`, `ai-triage`) that call a hosted model to help a
  human reviewer, never to decide a match. `ai-triage` calls no model at
  all (see docs/adr/0014's triage section).
* **Model and provider.** Claude, built by Anthropic. Default: the public
  Anthropic API via the `anthropic` SDK, model `claude-sonnet-5`. Also
  supported: Claude on Amazon Bedrock via the Converse API (model default
  `global.anthropic.claude-sonnet-4-6`), used for this project's own live
  evals because this portfolio's AWS account can reach that model but not
  `claude-sonnet-5` directly (docs/adr/0014).
* **Where it lives in the code.** `src/constituent_reconciler/assistant/`.
  `provider.py` is the model client seam; every feature module
  (`match_explain.py`, `ask.py`, `ocr_propose.py`) calls it through the
  same `Provider` protocol, never directly.
* **Implementation status.** Implemented and opt-in, behind four CLI
  subcommands a reviewer must deliberately invoke. Not reachable from
  `constituent-reconcile run`, `review`, or `apply`; not wired into the browser review
  UI (`constituent-reconcile review`) as of this writing -- see docs/adr/0014
  Consequences for that as explicit follow-up work.
* **This project trains no model.** Same as MODEL-CARD.md: this card
  documents a third-party hosted model the pipeline can optionally call.

## Intended use

A human reviewer, already looking at an uncertain pair or a garbled
intake field in `constituent-reconcile review` or the CSV review queue, runs one of
the four commands against the same run directory to get: a plain-language,
citation-checked explanation of why Splink scored a pair the way it did
(`ai-explain`); a grounded answer to a specific question about that pair
that will not state a verdict (`ai-ask`); a draft, quote-bound correction
proposal for one garbled field, to accept or reject like any other
correction (`ai-propose-corrections`); or a re-ordering of the review
queue by real signal, to work the highest-attention pairs first
(`ai-triage`).

## Out-of-scope and prohibited use

* **Never a merge decision.** No output from any of these four commands
  is, or is treated as, an approval, a rejection, or a certainty claim.
  `decisions.py`'s fail-closed banding is the only code that ever produces
  a verdict; nothing in `assistant/` calls it, is called by it, or can
  influence its output.
* **Never applied automatically.** An OCR proposal is a draft file
  (`out/ai_ocr_proposals.json`); nothing in this package writes to a
  record, to `out/corrections.json`, or to any connector.
* **Never used under a policy pack that forbids cloud calls.**
  `assert_cloud_ai_allowed()` refuses the entire package, before any
  record is touched, under the `dv` and `hipaa` policy packs.
* **Never sent real constituent data in this project's own evals.**
  `tools/ai_eval/fixtures.py` is entirely synthetic; see docs/adr/0014.

## Limitations

* **Quote verification grounds against presence, not correct attribution.**
  `ocr_propose.py`'s quote check confirms a proposed correction's quote is
  a real, exact substring of the source text -- it does not confirm the
  quote is about the *right person* or the *right field*. The live eval
  (`eval/ai/report.md` §2) found exactly this failure mode on a fixture
  built to probe it (`wrong_person_trap`): a real quote, correctly
  attributed to the wrong person. Every accepted proposal is still a draft
  a human reviews before anything happens; this limitation is why that
  human step is not optional.
* **The prohibited-language scanner (`refusal.py`) is pattern-based, not a
  classifier.** It is deliberately biased toward over-triggering (a false
  positive costs a reviewer one generic redirect message, always a safe
  outcome); the live adversarial eval measures the combined system
  (prompt instructions plus scanner), not the scanner alone, and found
  zero misses across 20 prompts spanning direct, indirect, fatigue-framed,
  authority-framed, and bilingual (English/Spanish) phrasings -- but a
  pattern list is not a proof of coverage against every possible phrasing.
* **`email`/`phone` are never described by literal value** in
  `match_explain`/`ask` prompts (`evidence_payload.py`), even when not
  otherwise withheld -- a deliberate minimization choice, not a bug; a
  reviewer asking specifically about those fields gets the comparison
  *level* (agree/differ), not the value.
* **Grounding measures model quality, not safety.** The citation-grounding
  eval (§3) reports how often the model's claims verify against real
  evidence; a low number there would mean fewer useful claims shown (they
  are withheld, by construction), not a leaked or fabricated one shown as
  verified.

## Evaluation status

Full detail: [docs/adr/0014-runtime-ai-at-the-edges.md](adr/0014-runtime-ai-at-the-edges.md)
and [eval/ai/report.md](../eval/ai/report.md) (committed, regenerated by
`make eval-ai`). Summary as of the date in that report: 0/20 adversarial
merge-refusal prompts reached the reviewer unsafe; OCR proposals 100%
precision / 80% correct abstention / 1 invented-value case (disclosed
above); 100% citation grounding across the pairs evaluated; 0 consent/
policy leaks across 15 deterministic checks; 0/8 unanswerable-query
prompts fabricated a specific value.

## Ethical considerations

This tool exists in the same context MODEL-CARD.md and the `dv` policy
pack (0005) already establish: constituent records can include
DV-survivor data, and a wrong merge is expensive and sometimes
irreversible. The assistant package's entire design constraint -- Splink
decides, the model narrates, a verifier sits before display -- follows
from that context. The subprocessor question (does sending a name and
date of birth to a third-party model provider fit an adopting
organization's own donor/client consent language) is recorded as a
DECISION NEEDED in docs/adr/0014, not assumed here.

## Caveats and recommendations

* An adopting organization's own counsel should review the subprocessor
  question before enabling the `ai` extra in any deployment handling real
  constituent data, per docs/adr/0014.
* Treat every command's output as advisory and label it as such in any
  downstream artifact a reviewer produces from it (a case note, a board
  report) -- the commands already print an "AI-GENERATED, ADVISORY" label,
  but that label does not travel automatically if the text is copied
  elsewhere.
* Re-run `make eval-ai` after any change to `assistant/prompts.py`,
  `refusal.py`, or the provider default model, and commit the refreshed
  `eval/ai/` report alongside the change.

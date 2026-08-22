# 0014 — Runtime AI at the edges

Status: accepted (2026-08-22)

Deciders: Chelsea Kelly-Reif (owner-directed)

## Context

Before this decision, the only place a model touched this codebase was the
optional extraction seam (0003, 0010): `extract/seam.py` sends a
low-confidence intake page to Claude on Bedrock or a local Ollama model,
policy-gated, to help the offline extractor read a garbled field. Nothing
in the resolution or review path -- `matching/`, `decisions.py`, or the
review queue itself -- called a model at all. This was verified before any
code was written here: a grep for "ai" only matches "domain," "chain," and
"available"; there is no `anthropic` or `boto3` import anywhere in
`pipeline.py`, `decisions.py`, or the CLI's module-level imports.

The owner directed a change in scope: build a real, substantial AI layer
that makes the human reviewer faster and better-informed, while keeping the
deterministic matcher as the only thing that ever decides a match. This ADR
records that direction and the architecture that implements it.

The trust-pattern shape ("AI at the edges, the deterministic engine
decides, a verifier sits before display") is the same one adopted in
`ChelseaKR/permit-bearings`'s ADR 0004 (public repo, read for shape only).
No source was copied from that repo or any other; every module here is
written fresh against the public `anthropic` SDK and the standard library,
per this portfolio's clean-room rule (a sibling repo had a real vendoring
incident on the day this work started).

## Decision

### Package layout

`src/constituent_reconciler/assistant/` is new. Nothing outside it imports
from it, and it is never imported at module level by `pipeline.py`,
`decisions.py`, or `cli.py` -- every `constituent_reconciler.assistant`
import in `cli.py` lives inside a `_cmd_ai_*` function body.
`tests/test_no_ai_in_deterministic_path.py` proves this two ways: a static
AST walk of module-level imports, and a subprocess that runs `reconcile
run` with `anthropic` and `boto3` sabotaged out of `sys.modules` (the
standard "assign `None`" trick, which turns any `import anthropic` into an
`ImportError`) and asserts the deterministic pipeline still produces its
normal output. The offline-first pipeline is unchanged, and the existing
979-test suite (now 988 with this package's own tests) still passes
byte-for-byte with zero edits to `pipeline.py`, `decisions.py`,
`matching/splink_backend.py`, or any connector.

```
assistant/
├── __init__.py          # public surface; module docstring states the invariants
├── errors.py             # AssistantError and its subtypes
├── prompts.py             # REFUSAL_RULES, SCRUBBED_RESPONSE, PROMPT_VERSION
├── provider.py            # AnthropicProvider (default) + BedrockProvider (eval)
├── rate_limit.py          # per-minute + daily hard cap, file-backed
├── consent_filter.py      # the payload gate every feature calls first
├── refusal.py             # deterministic prohibited-language scanner
├── evidence_payload.py    # shared prompt-payload builder (match_explain + ask)
├── match_explain.py       # feature 1: explain real Splink evidence
├── ask.py                 # feature 2: refusal-first grounded Q&A
├── ocr_propose.py         # feature 3: quote-bound OCR correction drafts
├── source_text.py         # reads the real source-document text a quote is checked against
└── triage.py              # feature 5: deterministic review-queue ordering

matching/evidence.py        # NEW, but not in assistant/: the real comparison
                             # evidence match_explain and ask narrate and verify
                             # claims against. Splink decides; this module only
                             # reads back what Splink already decided.
```

`matching/evidence.py` deserves its own callout. `matching/splink_backend.py`
already discards Splink's intermediate columns
(`retain_intermediate_calculation_columns=False`) because `score_pairs()`
only needs the final probability. Explaining *why* a pair scored the way it
did needs the field-level detail: which comparison level fired, the m/u
probabilities that level carries (the same hand-set constants in
`defaults.py` a human reviewer of this codebase can already read), the
realized Bayes factor, and any term-frequency adjustment. `evidence.py`
reruns the identical deterministic prediction with that flag flipped on and
reads the result back into a typed `PairEvidence`/`FieldEvidence` pair.
Two things about how it does that are worth recording:

* Splink numbers a comparison's non-null levels in *reverse* declaration
  order -- the first, most-similar level in a `comparison_levels` list gets
  the *highest* `comparison_vector_value` ("gamma"), the last ("else")
  level gets 0, and any null level is always -1, independent of where it
  sits in the list. This was not assumed; it was confirmed empirically
  against a live `Linker.inference.predict()` call (first_name: JOHN/JON
  scores gamma 2 -> "nickname," Bayes factor 6.0, matching
  `_first_name_comparison`'s m=.06/u=.01 exactly; last_name: SMITH/SMITH
  scores gamma 4 -> "exact," Bayes factor 87.0, matching m=.87/u=.01) before
  `_level_for_gamma` was written to replay it, and `tests/
  test_matching_evidence.py` pins the same cases as regression tests.
* A column that is entirely null across a batch can come back from
  DuckDB/pandas as `pd.NA` rather than Python `None`, and `bool(pd.NA)`
  itself raises `TypeError`. The first version of this module used
  `value or ""` to fall back on a missing cell and crashed on exactly that
  case; `_is_missing()` now checks `pd.isna()` explicitly. Caught by
  `tests/test_matching_evidence.py` before merge, not in production.

### Where consent filtering sits, relative to the model

This is the sharpest part of the design, so it is stated precisely.
`consent_filter.py` runs on every `Record` *before* any prompt string
exists, and it is the only place PII is allowed or refused entry into this
package:

1. `assert_cloud_ai_allowed(policy)` runs once, before a single `Record` is
   read, and reuses `policy.forbid_cloud_seam` -- the exact field
   `extract/seam.py`'s cloud seam is already gated on -- rather than a new
   field that could drift out of sync. The `dv` and `hipaa` packs, which
   set that field today, disable the AI assistant entirely; a merge-blocking
   test (`test_dv_pack_forbids_the_assistant_entirely`,
   `test_hipaa_pack_forbids_the_assistant_entirely`) pins this.
2. `filter_record()` reduces a `Record` to only the *normalized* field
   values consent and policy actually clear, for a new named destination,
   `"ai-assistant"`, that a recipe's consent-scope column can exclude a
   record from exactly the way it already can exclude `"civicrm"` or
   `"csv"` -- reusing `models.Consent`'s existing scope mechanism rather
   than inventing a parallel one.
3. `evidence_payload()` (shared by `match_explain` and `ask`) takes the set
   of withheld field names and is the boundary that actually decides what
   text a provider ever sees.

That third point is where a real bug was found and fixed during this work,
and it is recorded here rather than quietly folded into the diff. The
dedicated leakage eval (`tools/ai_eval/consent_leakage.py`) builds records
whose consent does not clear the gate for some fields, and asserts a
sentinel value planted in a withheld field never appears anywhere in the
actual JSON text `evidence_payload()` produces. The first live run of that
eval found **20 leaks**: `evidence_payload()`'s withheld-fields loop only
*appended* an extra `{"status": "withheld"}` marker for a named field --
it never suppressed the real `left_value`/`right_value` entry Splink's own
evidence already carried for that same field (Splink scores every
configured field regardless of consent, so a withheld field almost always
*does* have a real entry sitting right next to the marker). The fix
(`evidence_payload.py`) checks `withheld_fields` in the same loop that
walks `evidence.fields` and `continue`s before ever building the real
entry, so a withheld field's value cannot appear under any key. A
regression suite (`tests/test_assistant_evidence_payload.py`) pins it, and
re-running the eval after the fix shows **0 leaks across 15 checks** (5
fixtures x 3 policy packs). This is exactly why the eval harness below was
built before this repo's own AI feature was allowed to be called
"finished": it caught a real, shippable leak that every existing unit test
missed, because the unit tests exercised each feature module in isolation
and never built a `PairEvidence` whose fields overlapped its
`withheld_fields`.

No additional opt-in flag was added on top of the policy gate. Unlike the
extraction seam, which the deterministic `run` command can reach implicitly
via `[extract] backend = "bedrock"`, every AI feature here lives behind its
own CLI subcommand (`ai-explain`, `ai-ask`, `ai-propose-corrections`,
`ai-triage`) that a reviewer must deliberately invoke; that command
boundary is the opt-in, and a second flag on top of it would not add a real
guarantee the subcommand boundary does not already provide.

### The five features

1. **Match explanation** (`match_explain.py`). Given a `PairEvidence`, the
   model returns per-field `level_label` + one-sentence narrative plus a
   two-sentence summary, as JSON. Every claim is checked before display:
   `_verify_claim()` requires the claimed `level_label` to exactly match
   the real `FieldEvidence.level_label` for that field, and an unverifiable
   claim (wrong level, or a field the model invented that Splink never
   scored) is withheld and counted, never shown "on the model's word."
   `email`/`phone` are never sent by literal value (`REDACT_FROM_EXPLANATION`
   in `evidence_payload.py`) -- explaining a "different" email comparison
   never needs the actual address, and those two fields are the most
   directly reusable for contacting or locating a person.
2. **Auto-merge refusal** (`ask.py`) -- the eval that matters most. A
   reviewer's free-text question about one pair, answered from the same
   real evidence, with two independent defenses: `prompts.REFUSAL_RULES` in
   the system prompt instructs the model to never recommend a merge, claim
   two records are the same person (or are not -- both directions are
   barred, not just the affirmative one), tell a reviewer which record to
   keep, or claim certainty, in any phrasing, language, or framing; and
   `refusal.enforce()` runs a deterministic pattern scanner (English and
   Spanish) on the raw response *before* it is shown to anyone, replacing
   any hit with a canned redirect message regardless of what the model
   actually said. `eval/ai/report.md` §1 is the live measurement of the
   combined system, not the prompt alone.
3. **OCR correction proposals** (`ocr_propose.py`). A proposal is accepted
   only when the model quotes, character for character (whitespace-
   normalized), an exact substring of the real source-document text
   (`source_text.py` reads that text from the record's actual `SourceSpan`/
   `TextSpan`, reusing `extract/seam.py`'s own page-text helper for PDFs).
   A quote that does not verify -- or no quote, or an explicit model
   abstention -- comes back as an abstention. Nothing here writes to a
   record or to `out/corrections.json`; `reconcile ai-propose-corrections`
   writes a labeled draft file (`out/ai_ocr_proposals.json`) a human
   reviews, and turning an accepted proposal into a real correction still
   goes through the existing, tested, reviewer-attributed correction path.
4. **Consent/policy faithfulness** -- not a separate feature module; it is
   `consent_filter.py`, described above, and it is enforced identically by
   every other feature.
5. **Review-queue triage** (`triage.py`). Deterministic, no model call:
   orders review-band pairs by (a) a consent conflict between the two
   members, (b) how many fields disagree, (c) match probability, in that
   order, with every reason string built from real pipeline data. It never
   calls a model so a provider outage can never change queue order, and it
   runs the same under every policy pack including `dv`/`hipaa` (ordering
   carries no PII off the machine).
6. **Honest refusals** run through all five: an unknown record/pair id, a
   field withheld by policy, a document with no source text available, or
   a question the given evidence cannot answer all produce an explicit
   "withheld"/"no evidence"/"cannot answer" response rather than a guess or
   a silent gap. `eval/ai/report.md` §5 measures the "cannot answer"
   surface specifically.

### Provider and cost controls

`provider.py` exposes `AnthropicProvider` (default; the public `anthropic`
SDK, model default `claude-sonnet-5`, credentials from `$ANTHROPIC_API_KEY`
only, never written to disk) and `BedrockProvider` (boto3 `bedrock-runtime`
Converse, mirroring `extract/seam.py`'s `BedrockSeam` call shape rather
than the `anthropic` SDK's own `AnthropicBedrockMantle` client -- the
Mantle client returned a live 404 for
`global.anthropic.claude-sonnet-4-6` against this account in testing, while
the boto3 path is already proven). `make_provider()` selects by name
(`$RECONCILER_AI_PROVIDER`, default `"anthropic"`) with an explicit model
override (`$RECONCILER_AI_MODEL`); an unrecognized provider name raises,
fail-closed.

This portfolio's dev/eval AWS account was verified live to accept Converse
calls at `global.anthropic.claude-sonnet-4-6` on Bedrock, but returns
`AccessDeniedException` for `claude-sonnet-5` directly, despite the
Bedrock availability API reporting it as `AUTHORIZED` -- a known,
previously-settled account quirk, not re-probed here. `BedrockProvider`'s
default model is therefore `global.anthropic.claude-sonnet-4-6` (the
`global.` prefix carries no regional markup in the vendored pricing table,
unlike `extract/seam.py`'s `BedrockSeam`, which defaults to the pricier
`us.` prefix for an unrelated, earlier reason). Every live eval in this
repo runs on Bedrock/Sonnet 4.6 for exactly this reason; the code's own
default provider and model stay Anthropic/`claude-sonnet-5` for a deployer
with ordinary API access.

Cost controls: `rate_limit.py` enforces a per-minute call rate (default 20)
and a hard daily cap (default 200), backed by a small, PII-free JSON state
file (call timestamps only) under the run's `--out` directory, so the cap
holds across separate CLI invocations and across a reviewer's separate
requests within one session. `RateLimitExceeded` is a normal, caught
exception in every `_cmd_ai_*` command (reported as a clear CLI message,
never a crash) and is structurally incapable of touching
`pipeline.run`/`decisions.py`/the `run`/`review`/`apply` commands --
nothing in those code paths can raise or catch it, because nothing in them
imports `assistant` at all. Prompt caching: every provider call marks its
system prompt (`REFUSAL_RULES` plus the feature-specific instructions, the
stable, repeated part of every call) with `cache_control: {type:
"ephemeral"}` (Anthropic) or a `cachePoint` content block (Bedrock
Converse); the per-pair evidence and the reviewer's question are volatile
and sit after the cache boundary, matching the placement rule (stable
content first, volatile content after the last breakpoint).

### Deterministic parsing conventions

Every structured response follows the same shape the extraction seam
already established: "respond with ONLY a JSON object of exactly this
shape," parsed by `assistant/_json.py` (a small, shared fence-stripping/
`json.loads` wrapper mirroring `extract/seam.py`'s own
`_strip_json_fence`), never raising on a malformed response -- a bad parse
is "nothing usable came back," handled the same way an abstention or an
unverifiable claim is, not a crash.

## Eval harness (`tools/ai_eval/`, results in `eval/ai/`)

Five evals, all synthetic fixtures (`tools/ai_eval/fixtures.py`; no real
constituent data is ever sent to a provider by this harness), each
producing a result with full provenance (provider, model, prompt_version,
commit, date, status) via `tools/ai_eval/provenance.py`.
`tests/test_ai_eval_provenance.py` rejects a committed
`eval/ai/results.json` missing any of it. Run with `make eval-ai`
(equivalently `python -m tools.ai_eval.run_eval`); like `make
eval-benchmark`, this is a live, credentialed command, not part of `make
verify` or CI.

Live results as of this ADR (provider `aws.bedrock`, model
`global.anthropic.claude-sonnet-4-6`, commit `ca18a069bf03`, 2026-08-22 --
full detail and per-case data in `eval/ai/report.md` and
`eval/ai/results.json`):

| Eval | Headline result | Gate |
|---|---|---|
| Auto-merge refusal (adversarial, 20 prompts: direct/indirect/fatigue/authority-framed, EN+ES) | 0 of 20 reached the reviewer with a merge recommendation or certainty claim | **PASS** |
| OCR proposals (10 synthetic documents) | Precision 100% (5 propose-expected cases), correct abstention 80% (5 abstain-expected cases), **1 invented-value case** (see below) | reported, not gated |
| Citation grounding (2 synthetic pairs) | 100% of returned claims verified against real evidence | informational |
| Consent/policy leakage (5 fixtures x 3 policy packs, deterministic) | 0 leaks (after the fix above; 20 before it) | **PASS** |
| Unanswerable / query-structuring (8 prompts) | 0 of 8 answers fabricated a specific-looking value the evidence never gave | **PASS** |

The one OCR finding is reported, not smoothed over: `wrong_person_trap`
(fixtures.py) is a source document whose only legible surname belongs to
the caseworker, not the client (`"Caseworker: Angela Halloway"`), with the
client's own surname garbled and unrecoverable. The model proposed
`"Halloway"` for the client's `last_name`, and the proposal's quote *does*
verify -- the substring is real, it just describes the wrong person.
Automatic quote-verification grounds a claim against *presence* in the
source text, not against *correct attribution to the right field or the
right person*; this fixture exists specifically to surface that limitation
rather than let precision/abstention numbers alone imply a stronger
guarantee than the mechanism actually gives. This is disclosed here and in
`eval/ai/report.md`, not hidden.

## DECISION NEEDED

Two questions this ADR does not settle, per the project's own rule against
inventing legal or compliance facts:

1. **Subprocessor / donor-consent question.** Sending any constituent field
   value to a third-party model provider (Anthropic directly, or Amazon
   Bedrock) is a subprocessor relationship the adopting nonprofit's own
   privacy policy and donor/client consent agreements may not authorize,
   independent of this project's own consent-scope mechanism. `dv` and
   `hipaa` already refuse the whole feature, but the `default` pack does
   not, and most real deployments run under `default`. Whether an
   organization's existing consent language covers "an AI vendor may
   process a name and date of birth to help a staff reviewer" is a
   question for that organization's own counsel, not this codebase's to
   assume. Recorded, not resolved.
2. **Deployment.** No cloud infrastructure was provisioned for this
   feature; a not-applied shape is documented in
   `docs/deploy/ai-assistant-deployment.md`. Open: whether the `ai` extra
   ever becomes a default extra in the published Docker image or PyPI
   package (today it is opt-in, matching `extract`/`ocr`), and whether a
   centrally-hosted proxy is ever built (explicitly: not built, not
   planned by this ADR).

## Consequences

- The offline-first claim in `README.md` needed to be scoped honestly: the
  deterministic pipeline (`run`/`review`/`apply`/`compare`/`destroy`/etc.)
  is unchanged and still makes zero network calls by default; the four new
  `ai-*` commands are explicitly not offline, and the README now says so in
  the same place it makes the offline claim, not in a buried caveat.
- A reviewer gains four new, clearly-labeled advisory tools without any
  change to what gets auto-merged, what the review queue contains, or what
  gets written to a CRM.
- The eval harness is a real, committed asset for future feature work in
  this area: `tools/ai_eval/fixtures.py`'s adversarial-prompt and OCR-trap
  fixtures are reusable for any future prompt or model change, and the
  provenance-completeness test means a future contributor cannot commit an
  eval result without saying what produced it.
- Left deliberately open: no review-server (`reconcile review`) UI wiring
  for these features yet -- they ship as CLI commands
  (`ai-explain`/`ai-ask`/`ai-propose-corrections`/`ai-triage`) callable
  against any run directory. Wiring an "explain this pair" control into the
  browser review queue is real, valuable follow-up work, not done here.
  Revisit if a reviewer workflow makes the CLI round-trip (run the command,
  read the output, go back to the browser queue) a real friction point.
- Revisit this ADR if: the account's Bedrock model access changes (the
  `claude-sonnet-5` `AccessDeniedException` clears, or the AUTHORIZED-but-
  denied availability-API quirk is otherwise resolved); a policy pack is
  ever added where `require_consent=True` but `forbid_cloud_seam=False`
  (today no such pack exists, so `evidence_payload()`'s withheld-field
  path is reachable only through this eval harness and the unit tests
  built for it, not through any currently-shipped policy pack in
  production use); or an adopting organization's counsel produces a
  written answer to the subprocessor question above.

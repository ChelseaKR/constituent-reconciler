# AI assistant deployment: not-applied shape

Status: **not applied.** No cloud infrastructure has been provisioned for
the AI assistant package (`constituent_reconciler.assistant`,
[docs/adr/0014-runtime-ai-at-the-edges.md](../adr/0014-runtime-ai-at-the-edges.md)).
This document records the shape a real deployment would take if one is
ever authorized, so the decision is scoped before it is made rather than
inferred from whatever got built first. Deployment itself is a
**DECISION NEEDED**, owner-level, not made by this document.

## What "deployment" means for this feature specifically

Unlike a hosted web service, this project ships as a CLI tool and a Docker
image an operator runs on their own machine or their own infrastructure.
There is no server this project operates on anyone's behalf today, and the
AI assistant package does not change that: `constituent-reconcile ai-explain`,
`ai-ask`, `ai-propose-corrections`, and `ai-triage` are commands a
deployer's own operator runs, against their own recipe and their own
run directory, using their own model-provider credentials
(`$ANTHROPIC_API_KEY`, or AWS credentials for `--ai-provider bedrock`).
"Deployment" here means two narrower, still-real questions:

1. Does the `ai` optional dependency group ship *by default* in the
   published artifacts (the Docker image, a future PyPI release), or does
   an operator opt in explicitly the way `extract`/`ocr` already work?
2. Does this project ever build and operate infrastructure *of its own*
   that proxies or centralizes model calls on behalf of multiple
   deployers (a hosted API key, a shared rate limiter, a shared cache)?

## Current state

* **Packaging.** `ai` is an optional dependency group in `pyproject.toml`
  (`anthropic>=0.40`), not installed by `make install`'s default flags
  (`--extra extract` only) and not present in the published `Dockerfile`'s
  build. An operator who wants the AI commands installs it explicitly:
  `pip install -e ".[ai]"`. This matches the existing convention for
  `extract` and `ocr`, not a new pattern.
* **Credentials.** Read from the environment only
  (`$ANTHROPIC_API_KEY` for the default provider; the AWS SDK's normal
  credential chain for `--ai-provider bedrock`). Never written to a file
  by this codebase, never logged, never included in the PII-free
  provenance/telemetry this project already ships
  (`telemetry.py`, `provenance.py`).
* **No proxy, no shared infrastructure.** Every call goes directly from
  the operator's own machine to Anthropic's API or to their own AWS
  account's Bedrock endpoint. This project does not sit in the middle of
  that call today, and this document does not propose that it start.
* **Rate limiting and cost caps are local, not centralized.**
  `rate_limit.py`'s per-minute and daily caps are enforced per `--out`
  directory on the operator's own machine, not by any service this
  project runs. An operator who wants an organization-wide cap across
  multiple staff would need their own control (for example, a
  provider-side spend limit on their own Anthropic or AWS account) --
  this project does not provide one.

## If deployment is authorized: the not-applied shape

Recorded so a future decision does not start from a blank page, and so
provisioning does not happen by accident during unrelated feature work.
None of this exists today.

* **Option A: ship `ai` as a default extra.** Lowest-effort change --
  add `ai` to the Docker image's installed extras and to a future PyPI
  release's default dependency set. Consequence: every deployer's image
  gets slightly larger and gains a dependency (`anthropic`) whether or
  not they use the AI commands, and the "offline-first" framing in the
  README would need another look (the *code path* stays opt-in --
  nothing calls a provider unless an `ai-*` command is invoked -- but a
  reviewer of the shipped artifact would see the SDK present by default).
* **Option B: a hosted proxy/gateway.** Not proposed here. Would require,
  at minimum: its own threat model (a shared credential is a much bigger
  blast radius than a per-operator one), its own data-handling review
  (constituent PII would transit infrastructure this project operates,
  which changes the answer to the subprocessor question in
  docs/adr/0014 §DECISION NEEDED from "the deployer's relationship with
  Anthropic/AWS" to "the deployer's relationship with this project *and*
  with Anthropic/AWS"), a cost model (who pays, and how spend per
  deployer is capped), and an uptime/support commitment this is
  independent, unpaid work (README's own framing) is not currently
  positioned to make. If ever considered, it should be its own ADR, not
  an amendment to this one.
* **Option C (default, until a decision is made): stay exactly as built.**
  `ai` stays an explicit extra, no infrastructure is provisioned, and
  every deployer's model relationship stays theirs and theirs alone.

## Cross-references

* Subprocessor / donor-consent question: docs/adr/0014 §DECISION NEEDED.
* Data flow for the deterministic pipeline (unaffected by this package):
  [docs/DATA-FLOW-AND-RETENTION.md](../DATA-FLOW-AND-RETENTION.md).
* The extraction seam's existing model/data cards, which this package's
  design pattern (policy-gated, non-egress-by-default, credentials from
  env only) follows: [docs/MODEL-CARD.md](../MODEL-CARD.md),
  [docs/DATA-CARD.md](../DATA-CARD.md).

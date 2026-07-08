# Roadmap

Planned direction for constituent-reconciler. Dates are intentions, not
promises; items move earlier when users ask for them. Feedback and feature
requests are welcome as GitHub issues.

The sequencing rule for this project: ship the differentiator first with the
least-risky subsystems, then grow the chain outward. The differentiator is a
non-technical review queue over probabilistic matching, plus a privacy mode a
shelter can legally use. The risky parts (document extraction on messy scans,
connector API churn) come after the core has proven out.

## Architecture

A deterministic-by-default state machine with discrete logged steps, explicit
tool calls, and one hard human gate. No silent auto-merge, ever.

```mermaid
flowchart TD
    A[INGEST: walk folder or inbox<br/>PDFs, scans, CSVs, email bodies] --> B[EXTRACT: offline parser default<br/>optional cloud seam for low-confidence pages only<br/>emits field, value, confidence, source-span pointer]
    B --> C[NORMALIZE: standardize names and dates<br/>libpostal parse plus deterministic CASS-style ruleset]
    C --> D[RESOLVE: block and score against existing constituents<br/>pre-tuned matcher, sane defaults, no labeled pairs required<br/>emits match, non-match, possible-match]
    D --> E{Fail-closed gate:<br/>confidence at or above threshold?}
    E -->|high confidence| F[Auto-flow candidate]
    E -->|low confidence, possible-match, failed address| G[REVIEW QUEUE]
    F --> H[HUMAN CHECKPOINT: WCAG 2.2 AA UI<br/>source span beside candidate duplicate<br/>approve, correct, reject]
    G --> H
    H --> I[WRITE: push only approved and consented records<br/>connector adapter: CiviCRM, Salesforce, Sheets, CSV, webhook]
    I --> J[Append-only provenance log<br/>BLAKE2b content hashing plus RFC 3161 timestamp]
    I -->|write failure| G
    J --> K[Run report: per-stage counts plus eval score on planted ground truth]
```

## v0.1.0 — Resolve and review, no extraction

The smallest version that ships the differentiator.

* CSV or Sheets in, a pre-tuned matcher backing dedup, a WCAG 2.2 AA review
  queue, CSV out.
* Pre-tuned defaults so the operator supplies no labeled pairs and no blocking
  rules. Confidence gate is fail-closed.
* Committed eval report on seeded synthetic fixtures (zero real PII) with
  planted ground-truth merges and planted near-misses.
* Definition of done: a messy two-source CSV resolves to a reviewed record set,
  the eval report shows false-merge and missed-match rates with Wilson
  confidence intervals, and the false-merge metric is a merge-blocking gate.

## v0.2.0 — CiviCRM write-back and provenance (shipped)

* CiviCRM write-back via API v4, as an upsert keyed on an external identifier so
  a re-run updates contacts rather than duplicating them. Built on an injected
  transport, so request construction and upsert logic are tested without a live
  server. Chosen first because CiviCRM is fully open and self-hostable.
* A connector interface (`connectors/`) with the CSV writer refactored onto it,
  so destinations stay isolated and a new one is a single module.
* Append-only, tamper-evident provenance log: each write records a BLAKE2b hash
  of the written fields and the previous entry's hash, forming a chain that
  `reconcile verify` checks. Time comes from a pluggable timestamp authority; the
  default is the local clock, and an RFC 3161 trusted-timestamp authority is the
  seam a production deployment plugs in.
* The consent gate runs before any connector is touched, so non-consented
  records are withheld and never handed to a destination.
* Still open: a recorded demo of messy input landing in a running CiviCRM
  instance, and email and phone written through dedicated CiviCRM entities
  rather than the API v4 join-field shorthand.

## v0.3.0 — The extraction seam (shipped)

* Offline document extraction (pdfplumber) with source-span pointers surfaced
  in the review queue CSV. Each extracted field carries the PDF filename, page
  number, and bounding box so a reviewer can navigate back to the source.
* Policy-gated cloud seam: `CloudSeam` protocol with `NoOpSeam` (default) and
  `BedrockSeam` (the documented extension point for deployers with AWS
  credentials). Under DV and HIPAA policy packs the seam is always `NoOpSeam`
  at construction time; PII cannot egress regardless of recipe settings.
* Folder-based ingestion: `incoming` in the recipe can now point to a directory;
  the pipeline walks it and routes `.csv` files through the structured reader and
  `.pdf` files through the extractor.
* `cohen_kappa()` in `evaluate.py` is the calibration seam for when an LLM
  extraction judge is wired in and its confidence scores are compared against
  human-labeled field accuracy.
* Still open: the WCAG 2.2 AA web review UI. The full `BedrockSeam.refine()`
  implementation (page-to-image conversion and Converse response parsing) has
  since shipped, with a fake-able injected client so the parsing and
  fault-tolerance paths are tested without boto3 or network access.

## v0.4.0 — Address normalization (shipped)

* A vendored, deterministic CASS-style ruleset (`address.py`) standardizes an
  address into USPS-style abbreviations from USPS Publication 28 tables, so two
  writings of the same address ("123 North Main Street" / "123 N Main St") reduce
  to the same matching key. Idempotent, offline, no model.
* Labeled in the code and docs as CASS-style and **not USPS-certified**; the
  standardization shipped position-insensitive, a documented simplification
  retired by the position-aware pass that landed with E6 (see the changelog).
* libpostal is an optional backend (`address_backend = "libpostal"`), never
  required; selecting it without the library installed raises a clear error
  rather than silently falling back.
* Address is added to the canonical schema but a recipe activates it only when it
  maps the field, so the committed demo eval is unchanged (CI verifies this). A
  separate `examples/address-demo/` fixture exercises the field end to end.
* Address agreement is weighted below email and a loose match routes to review,
  because families and shelter residents share an address and people move.

## v0.5.0 — The DV policy pack (shipped)

The fundability unlock. A `--policy-pack dv` flag (or `[policy] pack = "dv"`)
that, as four merge-blocking invariants:

* fuses the cloud seam off so PII never egresses (enforced at seam construction),
* refuses any non-local write target before a write happens, so client data stays
  on the machine (the comparable-database posture),
* emits an aggregate, suppression-aware summary (CMS-style small-cell
  suppression, complementary suppression, true zeros preserved) as the only
  shareable artifact,
* withholds any record without granted consent, recorded by id and reason only.

The invariants are grounded in primary VAWA, FVPSA, and CMS sources, with the
citations and three honesty corrections recorded in
docs/decisions/0005-dv-policy-pack.md and docs/RESPONSIBLE-TECH-AUDITS.md. The
`policy.py` model maps a pack name to its invariants and fails closed on an
unknown name. `hipaa` is a partial pack (consent plus no cloud seam) and does not
claim the DV local-target and aggregate rules.

## v0.6.0 — The v1.0 engineering deliverables (shipped)

The concrete build items of the 1.0 milestone, shipped without the 1.0 tag,
because the tag itself is gated on adoption (see below):

* A second connector, **Salesforce NPSP**, on the same `Connector` interface and
  injected-transport pattern as CiviCRM, using the REST upsert-by-external-id
  endpoint. Proves the connector interface is real, not a one-off.
* **One-command Docker self-host** (`Dockerfile`, `make docker`), with the PDF
  extraction extra included.
* A **DPG Standard conformance note** (`docs/DPG-CONFORMANCE.md`) mapping the
  project against the nine indicators, honestly.
* **Declared schema and interface versions** (`schema.py`, `reconcile schema`)
  for the config, the connector interface, and the JSON artifacts, with the
  versioning contract in docs/decisions/0006-schema-stability.md.

## v0.7.0 — Web review UI and offline CRM export (shipped)

The differentiator's review surface, and the offline-first write path, two items
the 1.0 milestone named.

* **Local WCAG 2.2 AA web review queue** (`review/`, `reconcile review`): a
  non-technical reviewer steps through the uncertain pairs in a browser, sees the
  two records side by side with source spans, and approves or rejects each merge.
  Verdicts write to the same `decisions.json` that `reconcile apply` consumes, so
  the web step replaces the hand-edited CSV without changing the pipeline. Built
  on `http.server`, no web-framework dependency.
* **Offline by construction**: loopback-only bind, all assets inlined, and under
  the DV pack a non-loopback bind is refused fail-closed (mirroring the connector
  local-target gate). The only persisted artifact is `decisions.json` with ids and
  verdicts and no field value, the minimization the DV pack requires.
* **Import-ready CRM export connectors** (`salesforce_csv`, `civicrm_csv`): a CSV
  mapped to the CRM's import schema plus an external-id column for an idempotent
  CRM-side upsert. The offline-first default path; the live API push stays opt-in.
  Both are local-file targets, so the DV pack permits them.
* Still open before the 1.0 accessibility gate: a screen-reader walkthrough.
  The structural AA work (table semantics, non-colour status,
  keyboard-complete controls, no-JS fallback) is in place, and an automated
  axe-core audit of the review queue's rendered HTML now runs as a CI job
  (`accessibility` in `.github/workflows/ci.yml`; `make axe` locally; see
  docs/decisions/0009-automated-axe-audit.md). The walkthrough is a manual
  pass with real assistive technology, tracked with a checklist in
  docs/reviews/SCREEN-READER-WALKTHROUGH.md, not yet performed.

## v1.0.0 — Stability commitments

Gated on the pipeline proving out against more than one real organization and on
no breaking change to the named surfaces for two consecutive releases. The
engineering deliverables landed in v0.6 and the web review UI in v0.7; what
remains for the 1.0 tag is the adoption evidence and the demonstrated-stability
window, plus a full accessibility audit and supply-chain hardening (SBOM, signed
releases, SHA-pinned actions). The tag is deliberately withheld until those are
real, rather than claimed early: 1.0 means a stability promise, and a promise that
depends on adoption cannot be made by a release script.

## Eval and quality plan

Correctness here is asymmetric, and the eval is built around that.

* A **false merge** corrupts a constituent's history and is sometimes
  irreversible. A **missed match** leaves a duplicate. The first is the worse
  error, so it is the gated one.
* Fixtures are seeded and synthetic, with zero real PII: a record whose name
  varies three ways, a true duplicate split across two scans, a non-duplicate
  that looks like one, an address that fails to parse.
* The eval scores extraction field-level precision and recall, matching
  pairwise precision and recall, and the two asymmetric rates (false-merge,
  missed-match) with Wilson confidence intervals.
* The false-merge rate is a merge-blocking AUTO-GATE in CI.

## Metrics ledger

Per-repo target values. Filled in as phases land; this table is the
conformance record the STANDARDS expect to live here rather than in the
shared standard.

| Attribute | Target | Gate |
|-----------|--------|------|
| Test coverage (logic) | At least 85% branch coverage on `src/`; gate temporarily set to 84% (84.63% measured 2026-07-05 for this PR's scope — see note below) | AUTO |
| False-merge rate (eval) | 0% among auto-merged pairs on the committed fixtures, fail-closed; CI runs the gate at 0.0 | AUTO |
| Matching pairwise precision and recall | Auto-merge precision 100% (a false merge fails the gate); auto+review coverage recall at least 95%, reported with Wilson CIs | REVIEW |
| Extraction field precision and recall | At least 0.95 precision and 0.90 recall on a labeled extraction fixture; target only, the fixture and its measurement are not landed | REVIEW |
| LLM field-judge calibration (Cohen's kappa) | Kappa at least 0.60, fail-closed on drift, the 0.6 line `evaluate.cohen_kappa` documents; wired into the eval in R10 | AUTO |
| Review queue accessibility | WCAG 2.2 AA; axe clean (automated, `accessibility` CI job, 2026-07-07) and screen-reader walkthrough (manual, not yet performed — docs/reviews/SCREEN-READER-WALKTHROUGH.md) | AUTO + REVIEW |
| i18n parity (EN, ES) | key and placeholder parity | AUTO |
| Supply chain | SBOM, Sigstore, SHA-pinned actions, OIDC | AUTO |
| DV policy-pack invariants | PII non-egress, consent-gated write | AUTO |

Enforcement today: the false-merge gate and the DV policy-pack invariants (PII
non-egress and consent-gated write) run as merge-blocking checks in CI now, and
CI also fails if the committed eval report drifts. The coverage floor is now a
merge-blocking `pytest` gate too (`--cov-fail-under=84` in `pyproject.toml`,
`pytest-cov` a committed dev dependency, 2026-07-05) — set to 84, not the 85
target, because this PR intentionally excludes an in-progress, pre-existing
feature branch (cannot-link constraints, review-server web-boundary checks,
strict recipe validation) that was sitting uncommitted in the working tree
alongside this remediation work; that branch's own tests are what push
measured coverage to 85%+. Raise the floor back to 85 when that branch lands.
The false-merge rate stays the primary correctness metric because a wrong
merge is the expensive error, but a coverage regression now fails the build as
well. The secret-scan
and dependency-vulnerability items are also merge-blocking CI jobs now
(`secrets`, `security` in `ci.yml`); SAST, container scanning, and SBOM/signing
remain committed targets not yet wired (see the remediation plan's P1-2,
P1-4, P1-7). The new `accessibility` job (axe-core over jsdom against the
rendered review queue) runs on every PR the same way, but like `sast`,
`zizmor`, and `container-scan` it is not yet in docs/rulesets/main.json's
required-status-checks list, so a red run there does not block a merge today. The kappa drift gate and the i18n parity check land with the
phases named beside them (the kappa gate with R10, EN/ES parity with R1). The
matching and extraction precision and recall figures are REVIEW metrics a
person reads from the eval report rather than pass-or-fail gates.

## AI Evaluation Standard applicability

`AI-Evaluation-Standard: N/A — no model inference in any user-facing or
decision path (BedrockSeam is an unimplemented stub; NoOpSeam default).
Reviewed 2026-07-05.` `BedrockSeam.refine()` raises `NotImplementedError`
(`src/constituent_reconciler/extract/seam.py`), and every policy pack ships
`NoOpSeam` by default; the DV and HIPAA packs fuse the seam off entirely. The
day `BedrockSeam.refine()` gains a real implementation, this line flips to
Applies and the AI-Evaluation standard's controls (eval harness, calibration
gate, model card) become binding before that PR merges.

## Observability

Tier C (library/CLI): `reconcile` is a local command-line pipeline and a
loopback-only review server, not a hosted service, so there is no request-rate,
latency-SLO, or distributed-tracing surface for OTel/RUM tiers A or B to apply
to. What Tier C asks for:

* **Logging posture:** opt-in, human-readable stdout/stderr from the CLI (run
  report, per-stage counts); no structured JSON log sink is shipped or planned
  while the tool stays local-only. The review server explicitly suppresses
  per-request access logging (`review/server.py`).
* **No secrets or PII in logs:** enforced by construction, not just convention —
  `decisions.json` carries ids and verdicts only, `withheld` records are logged
  by id and reason only, and provenance entries store BLAKE2b hashes rather than
  raw field values, each backed by a merge-blocking test
  (`tests/test_consent.py`, `tests/test_provenance.py`, `tests/test_review.py`).
* **Out of scope:** OpenTelemetry traces/metrics, RUM, and log aggregation — all
  presuppose a hosted, multi-request service this tool is not. Revisit if a
  hosted review-server mode is ever built.

## Out of scope

* Becoming a CRM or a system of record. The tool writes into existing systems.
* USPS CASS certification (requires licensed USPS data).
* Reimplementing a record-linkage engine. The project wraps an existing
  matcher and contributes defaults, orchestration, and review.
* GTFS, transit, and the other portfolio domains. This is human-services
  constituent data only.

## Open questions to resolve early

Resolve these from primary sources before building the affected phase; do not
guess.

1. Which matcher to wrap (Splink versus dedupe), judged on default quality
   without labeled pairs and on packaging weight for a CI install.
2. The CiviCRM write path: API entity shapes, dedupe-rule interaction, and how
   to make a write idempotent and reversible. The dedupe-rule interaction (for
   CiviCRM and Salesforce/NPSP both) is now documented in
   [CRM-DEDUPE-COOPERATION.md](./CRM-DEDUPE-COOPERATION.md).
3. The exact VAWA and FVPSA invariants the DV pack must enforce, sourced from
   NNEDV Safety Net guidance, expressed as tests.
4. The output record shape and how far to map toward HSDS organization and
   contact fields and HMIS CSV client fields without overreaching.
5. Whether the review queue is a local web UI or a TUI for the first cut, judged
   on what a non-technical reviewer can actually run.

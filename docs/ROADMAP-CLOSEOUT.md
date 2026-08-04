# Roadmap closeout

**Closed against main:** 2026-07-22  
**Commit reviewed:** `b6ac2fb1cf9e55511ff56d4b59b3d8180af8d407` before the
changes recorded here

This document resolves every item that was still presented as open in
`ROADMAP.md`, `RESEARCH-ROADMAP.md`, and `ideation/`. “Resolved” does not mean
every idea became code. It means each item now has one truthful terminal state:

- **Done:** implemented and covered by repository evidence.
- **External gate:** completion requires a human, authorized system, live
  repository setting, or real adopter.
- **Closed by decision:** the proposed shape does not fit this application's
  contract and is not an implementation promise.
- **Conditional study:** no implementation is authorized until its named gate
  is met.

The active next-work plan is `NOVEL-USE-CASES-PLAN.md`. Historical roadmap
documents remain the rationale and acceptance-criteria record.

## Canonical 1.0 gates

| Item | State | Repository evidence | What remains |
| --- | --- | --- | --- |
| Accessibility structure and automated audit | Done | semantic review UI, keyboard/no-JS paths, `make axe`, CI accessibility job | none in code |
| Screen-reader walkthrough, reviewed Spanish UI copy, ACR | External gate | `docs/reviews/SCREEN-READER-WALKTHROUGH.md`, `docs/I18N.md` | assistive-technology user, native Spanish reviewer, signed audit result |
| Supply-chain implementation | Done | pinned Actions, CodeQL, Semgrep, secret scans, Trivy, Scorecard, tag release workflow, SBOM and provenance attestation | none in code |
| First signed release and live required-check ruleset | External gate | `.github/workflows/release.yml`, `docs/rulesets/main.json` | authorized tag/release and GitHub repository-settings change |
| CiviCRM adapter behavior | Done | dedicated Contact/Email/Phone writes and injected-transport tests | none in code |
| Recorded CiviCRM end-to-end demonstration | External gate | adoption kit and demo recipe | authorized running CiviCRM instance and recording |
| Pilot readiness | Done | `docs/ADOPTION-KIT.md`, validation command, manifests and reports | none in code |
| More than one real adopting organization | External gate | adoption materials only | real organizations; synthetic evidence is not substituted |
| Demonstrated schema-stability window | External gate | declared versions and ADR 0006 | two real releases without a breaking change |

These external gates remain the only blockers to a truthful 1.0 tag. They are
not engineering backlog and cannot be completed by adding fixtures.

## Research roadmap R1–R11

| ID | State | Resolution |
| --- | --- | --- |
| R1 | External gate | Automated accessibility is done. The remaining assistive-technology, reviewed Spanish, and ACR work requires qualified humans. |
| R2 | Done | RFC 3161 authority with fail-closed response verification. |
| R3 | Done + external evidence | Supply-chain code is done; first-tag and live-ruleset evidence are external. |
| R4 | Done | Parse-path threat model is committed and the sandbox is wired as the default. |
| R5 | Done | Risk-class evaluation and committed bias report. |
| R6 | Done | Metric targets and enforcement owners are filled. |
| R7 | Done + external evidence | CiviCRM entity writes are done; the live demonstration is external. |
| R8 | Done | Retention model, data-flow map, destruction executor, and certificates. |
| R9 | Done | Model and data cards cover hosted and local seams. |
| R10 | Done | Kappa calibration is a fail-closed eval gate. |
| R11 | Done | Plain-language evidence rationale appears in review. |

## Research roadmap E1–E10

| ID | State | Resolution |
| --- | --- | --- |
| E1 | Closed by decision | A generic “HSDS constituent export” is a category error: HSDS publishes organizations, services, and locations, not client records. A complete HMIS CSV export is also not a field mapping; current HUD guidance requires all HMIS CSV tables and headers and funding-specific reporting behavior. The shipped suppressed comparable report remains the bounded fit. A separate HMIS product is not smuggled into this reconciler. |
| E2 | Done | `reconcile export-comparable` emits the suppressed report without a CRM write. |
| E3 | Partly done, remainder terminal | Webhook and Airtable are implemented. Google Sheets is closed as a direct connector because its read-scan-then-write path is not atomic or safely idempotent; local CSV is the supported Sheets interchange. Apricot is externally blocked until an authorized account and verifiable vendor contract exist. |
| E4 | Done | Reviewer attribution and optional/two-person DV review. |
| E5 | Done | CRM dedupe-cooperation guidance. |
| E6 | Done | Position-aware matching and real-libpostal CI coverage. |
| E7 | Closed by decision | The generic connector protocol cannot truthfully promise post-write reversal: remote destinations differ on delete, merge, restore, and audit semantics, and destructive repair must not be guessed. Durable cannot-link constraints prevent the same records from re-merging; a local output can be regenerated. Destination-specific repair plans belong in a future capability protocol, not in `Connector.write_all`. |
| E8 | Done + external evidence | The adoption kit is done; adoption itself is external. |
| E9 | Closed by decision | Skipping scoring for “unchanged” rows is not score-invariant when term-frequency evidence and new cross-batch candidates can change probabilities. Content-derived ids, manifests, and the large-corpus benchmark provide safe reproducibility. A future cache may skip extraction and normalization, but it must still rescore any globally dependent matcher stage. |
| E10 | External gate | The conformance note is ready. Registry nomination is an external submission and must be made by the maintainer with current project/contact information. |

## Large-fix inventory

FIX-01 through FIX-12 are implemented. The status is evidenced by the review
server boundary checks, cannot-link clustering, content-derived ids,
fail-closed recipe validation, ingest accounting, consent lifecycle,
field-level lineage, run manifests, connector registry/conformance tests,
sandbox wiring, corpus generator, and the dated claims audit.

## Expansion inventory

| IDs | State | Resolution |
| --- | --- | --- |
| EXP-01–EXP-06 | Done | Correction, cluster preview, matching depth, OCR, local model seam, and source-quality reporting are shipped. |
| EXP-07–EXP-13 | Done | Household suggestions, email/text ingest, reviewer calibration, destruction, narrative report, matcher seam, and offline bundle are shipped. |
| EXP-14 | Conditional study complete | The legal/technical study is committed. No prototype is authorized without counsel and a real non-VSP coalition partner; DV data is excluded. |
| EXP-15 | Conditional | Extracting a generic human-gate library remains gated on a second shipping consumer. Without one, API generalization would be speculative. |
| EXP-16 | Core done + external evidence | The seeded corpus generator, scoring CLI, intervals, risk classes, and committed large-corpus report are present. A hosted leaderboard is intentionally not promised; third-party reproduction remains external community evidence. |

## Product principles preserved by the closeout

1. No new system-of-record claim: the application reconciles records and
   writes reviewed results; it does not become an HMIS, case-management, or
   service-directory platform.
2. No weaker connector hidden behind an existing name: network adapters must
   be dry-run pure, consent-gated, non-local when appropriate, and safely
   repeatable by an external key.
3. No fabricated evidence: live integrations, accessibility review, adoption,
   and registry submission stay visibly external.
4. No unsafe optimization: a performance feature may cache deterministic local
   work, but it cannot reuse probabilistic decisions when the evidence
   population has changed.

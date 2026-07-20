# CLAUDE.md — constituent-reconciler

> Source of truth for project intent, scope, conventions, and the build plan.
> Read it fully before writing any code.

## What this is

`constituent-reconciler` is an offline-first pipeline that turns intake
documents plus a target case system into verified, deduplicated constituent
records written back into that system, with a non-technical human review queue
over every uncertain match. The inputs the code reads today are CSVs and
digitally created (text-layer) PDFs. Scanned documents (OCR) and email bodies
are planned, tracked as EXP-04 and EXP-08 in docs/ideation/03-expansions.md,
and the README says the same. It chains five
steps that already exist as separate libraries: extract, normalize, resolve,
review, write. The contribution is the chain, the pre-tuned defaults, the
review queue, and a privacy mode a victim-service provider can legally use.

## Why it exists (strategic context — read this, it shapes decisions)

This is a portfolio and contribution project by Chelsea Kelly-Reif. The
research that produced it found that the category (an agentic nonprofit
data-intake workflow) is real whitespace, while every individual capability
inside it is solved art. So the entire game is opinionated orchestration, an
unoccupied privacy and eval posture, and a wedge narrow enough for one person
to ship. Build accordingly: depth and correctness on a narrow chain beat
breadth.

Two audiences:

1. **Human-services data and operations staff** at small and mid-sized
   nonprofits who re-type intake into a CRM and fight duplicate records every
   reporting cycle. The 43% running on one or two IT staff cannot stand up
   Splink themselves; they are the user.
2. **The nonprofit-tech and open-source community** (Open Referral, NNEDV
   Safety Net, Code for America) who will judge the repo as a work sample and as
   something to adopt or coordinate with.

Do not frame this repo anywhere in its docs as a job-search artifact. The
README speaks only to nonprofit practitioners.

## Ground rules for Claude Code

* **Never invent legal or compliance facts.** Before implementing the DV policy
  pack or any consent rule, fetch and read the actual NNEDV Safety Net and
  VAWA/FVPSA guidance. Confidentiality obligations come from the source, not
  from memory. Where guidance is ambiguous, implement the more protective
  interpretation and record the question.
* **Fail closed, everywhere.** Missing data, an errored step, a low-confidence
  match, an ambiguous or expired consent: all route to a human or block the
  write. Never silent-pass, never silent-merge.
* **Do not reimplement record linkage.** Wrap an existing matcher (Splink or
  dedupe). The contribution is the pre-tuned defaults and the orchestration, not
  a new Fellegi-Sunter implementation.
* **Deterministic by default; the LLM is an optional seam.** Extraction,
  normalization, and resolution must run with zero cloud calls. The Bedrock
  (Claude) seam handles only low-confidence pages and only when the active
  policy pack allows it. Under `--policy-pack dv` the seam is fused off and PII
  never egresses; that non-egress is a merge-blocking test.
* **The privacy invariants are tests, not prose.** Inject a revoked-consent
  field and assert it never appears in any export or log. Inject PII under the
  DV pack and assert nothing leaves the machine. These are AUTO-GATES.
* **Be honest about CASS.** Ship a CASS-style standardizer and say in the code
  and docs that it is not USPS-certified. Overclaiming certification is a
  credibility failure with the exact technical audience the repo wants.
* Python 3.12+. The matcher (Splink) is the one heavy dependency; keep
  everything around it on the standard library. v0.1 uses stdlib `dataclasses`
  for records, `tomllib` for the recipe, `argparse` for the CLI, and stdlib
  `csv`. pandas appears only inside the Splink wrapper, nowhere else. libpostal
  arrives with address normalization (v0.4). The rationale for these choices is
  recorded in docs/adr/0001-matcher-and-defaults.md.
* License: Apache-2.0, matching the portfolio default.

## Architecture

This map is the as-built layout. Keep it in step with `ls
src/constituent_reconciler`; docs/CLAIMS-AUDIT.md records the last audit.

```
constituent-reconciler/
├── CLAUDE.md                      # this file
├── README.md                      # practitioner-facing
├── pyproject.toml                 # PEP 621, console_scripts entry: reconcile
├── src/constituent_reconciler/
│   ├── __init__.py                # public API surface, intentionally small
│   ├── address.py                 # deterministic CASS-style standardizer, not USPS-certified
│   ├── cli.py                     # subcommands: run, eval, apply, review, validate, verify, schema
│   ├── config.py                  # recipe.toml loading: sources, connector, thresholds, policy pack
│   ├── connectors/
│   │   ├── base.py                # connector interface (the jobradar adapter pattern)
│   │   ├── civicrm.py             # CiviCRM live write-back (API v4 upsert)
│   │   ├── crm_csv.py             # import-ready CRM export files (salesforce_csv, civicrm_csv)
│   │   ├── csv_out.py             # default local CSV output
│   │   └── salesforce.py          # Salesforce live write-back (REST upsert, NPSP Contact)
│   ├── consent.py                 # consent export gate; absent/revoked/expired withheld, fail-closed
│   ├── decisions.py               # banding, clustering, golden-record selection; the fail-closed gate
│   ├── defaults.py                # pre-tuned matching defaults
│   ├── evaluate.py                # eval scoring: false-merge and missed-match rates, Wilson intervals
│   ├── extract/
│   │   ├── __init__.py            # public surface: the offline extractor and the seam gate
│   │   ├── base.py                # extractor protocol and extraction result types
│   │   ├── pdf.py                 # offline pdfplumber extraction; text-layer PDFs only, no OCR
│   │   └── seam.py                # optional Bedrock seam, policy-gated, low-confidence pages only
│   ├── matching.py                # Splink wrapper; pandas appears here and nowhere else
│   ├── models.py                  # core dataclasses, free of matcher and framework types
│   ├── normalize.py               # deterministic name/date/address normalization, offline
│   ├── pipeline.py                # orchestrator: ingest -> extract -> normalize -> resolve -> review -> write
│   ├── policy.py                  # policy packs: default, dv (VAWA/FVPSA), hipaa
│   ├── provenance.py              # append-only BLAKE2b hash chain; RFC 3161 is a pluggable seam, not shipped
│   ├── report.py                  # run summary + committed eval report renderers
│   ├── review/                    # local WCAG 2.2 AA queue UI: render.py, server.py, session.py
│   ├── schema.py                  # declared schema/interface versions for the stability contract
│   └── suppression.py             # aggregate suppression-aware summaries for external sharing
├── tests/
│   ├── fixtures/                  # seeded synthetic data, zero real PII, planted ground truth
│   └── test_*.py                  # incl. test_no_egress.py, test_consent.py
├── eval/                          # committed eval report + the fixtures it scores
├── .github/workflows/ci.yml
└── docs/
    ├── CLAIMS-AUDIT.md            # dated capability-claims audit: claim, where stated, code, status
    ├── ROADMAP.md
    └── RESPONSIBLE-TECH-AUDITS.md
```

Decisions made now so they are not relitigated:

* **The pipeline is a state machine of small steps, not a framework.** Each step
  takes a typed record set and returns one, plus structured findings. Resist a
  plugin system; a registry of steps is enough.
* **The review queue is the product.** A non-technical reviewer must understand
  a match decision from the source span beside the candidate duplicate, with no
  jargon. Analyst-grade screens are a failure mode.
* **Connectors are isolated, version-pinned plugins.** Connector API churn is
  the tax that kills solo maintainers; keep each adapter behind the `base.py`
  interface, the way jobradar isolates its 15 ATS adapters.
* **Consent is a field, and the write step enforces it.** Not a checkbox
  attribute; a value the write path reads on every field on every emit.

## Build plan

Phases match docs/ROADMAP.md. In brief:

* **v0.1** Resolve and review only. CSV in, pre-tuned matcher, WCAG review
  queue, CSV out, committed eval with planted-duplicate fixtures and a gated
  false-merge rate.
* **v0.2** CiviCRM write-back, append-only provenance, consent-gated write.
* **v0.3** Offline extraction seam with source spans; optional policy-gated
  cloud seam; LLM field-judge calibration if used.
* **v0.4** Address normalization, labeled CASS-style not certified.
* **v0.5** The DV policy pack: seam fused off, aggregate suppression-aware
  export, invariants as tests.
* **v1.0** Second connector, Docker self-host, committed audits, schema
  stability guarantees.

## Quality bar

* Every step has a passing and a failing fixture. The privacy invariants
  (`test_no_egress.py`, `test_consent.py`) are merge-blocking.
* `ruff check`, `mypy --strict` on `src/`, and pytest green in CI before any
  feature work continues. No skipped tests on `main`.
* The eval report is committed and regenerated on release, with false-merge and
  missed-match rates and Wilson confidence intervals. No cherry-picking; failures
  are shown.
* Conformance to the portfolio STANDARDS is declared in the README, with values
  in docs/ROADMAP.md and findings in docs/RESPONSIBLE-TECH-AUDITS.md.
* Conventional commits; PR-sized changes even when working solo.

## Writing style for docs and messages

Plain, concrete prose. At most one em dash per document, prefer zero. No
rule-of-three rhetorical constructions. No "simply," "just," "powerful,"
"seamless." Vary paragraph openings. A finding or a review-queue label must say
what is true, where, and what the reviewer should do, in language a caseworker
can act on. Write like a careful engineer, not a launch tweet.

## Open questions to resolve early (do not guess)

1. Splink versus dedupe: default quality without labeled pairs, and packaging
   weight in a CI install.
2. CiviCRM write path: entity shapes, dedupe-rule interaction, idempotent and
   reversible writes.
3. The VAWA and FVPSA invariants for the DV pack, from NNEDV Safety Net
   guidance, expressed as tests.
4. Output record shape and how far to map toward HSDS and HMIS CSV without
   overreaching.
5. Review surface for v0.1: local web UI or TUI, judged on what a non-technical
   reviewer can run.

## Build status and entrypoint (moved from the README, 2026-07-19)

This file is the source of truth for scope, conventions, and the build plan,
and it states the hard guardrails: fail-closed gates, the privacy invariants
that are merge-blocking tests, and the rule against reimplementing the
matcher. Read [docs/ROADMAP.md](docs/ROADMAP.md) next and build phase by
phase. A phase is done when its acceptance criteria and its merge-blocking
metrics pass, not before. v0.1 through v0.7 are implemented and green:
resolve and review (v0.1), CiviCRM write-back and provenance (v0.2), the
pdfplumber extraction seam (v0.3), CASS-style address normalization (v0.4),
the DV policy pack (v0.5), the v1.0 engineering deliverables — Salesforce
connector, Docker self-host, schema-version declarations, DPG conformance
note (v0.6), and the WCAG 2.2 AA web review UI plus import-ready CRM export
files (v0.7). What remains before the 1.0 stability tag is a full
accessibility audit, supply-chain hardening, and the real-organization
adoption the tag is gated on.

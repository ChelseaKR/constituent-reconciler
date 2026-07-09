# Project Scope

Last reviewed: 2026-07-08. Base branch: `main`.

This file is a plain-language map of the project as it exists on `main`. It does not replace the README, roadmap, audit docs, or source comments. It points to them so a reviewer can see the whole shape without reading every file first.

## What This Project Is

This repo helps nonprofits turn intake records into deduplicated constituent records. It is built around offline processing, review before write-back, CRM connectors, provenance, and privacy rules for sensitive service contexts.

Package metadata checked in this pass:

- Python package `constituent-reconciler` for Python `>=3.12`.

## Who It Serves

- Nonprofit operations staff dealing with duplicate records from forms, PDFs, or spreadsheets.
- Data stewards who need to approve uncertain matches before a CRM changes.
- Engineers building safer intake pipelines for human-services organizations.

## What It Covers

- CSV and PDF intake paths with extraction and normalization.
- Matching, review sessions, decision records, and provenance logs.
- CiviCRM, Salesforce, and CSV connector surfaces.
- Policy packs, rulesets, adoption docs, and evaluation material.
- Tests for matching, connectors, consent, review, and the pipeline.

## How It Is Put Together

- src/constituent_reconciler/ holds the CLI, models, matching, pipeline, connectors, review UI, extraction, and policy code.
- examples/ contains demo recipes and inputs.
- eval/ contains evaluation notes and reports.
- docs/decisions/ records architecture choices.
- docs/rulesets/ contains policy data.

Observed source and operations surfaces:

- `Dockerfile`
- `Makefile`
- `eval/`
- `pyproject.toml`
- `src/`
- `tools/`

GitHub workflow files checked:

- `.github/workflows/ci.yml`
- `.github/workflows/codeql.yml`
- `.github/workflows/trufflehog-scheduled.yml`

## Trust Boundaries

- Uncertain matches are routed through a human review step rather than silently merged.
- The project records why a decision was made and which source data fed it.
- Privacy-sensitive modes are documented, but real deployments still need organization-specific rules.

## Outside This Scope

- It does not replace a CRM or become the system of record.
- It does not promise perfect matching.
- Some privacy, retention, and consent decisions still require counsel or organizational policy.

## Docs And Evidence Checked

This pass checked 33 hand-authored doc or metadata files, 24 test files, and 3 workflow files on `main`. The count excludes vendored provider licenses, dependency folders, generated cache files, and large generated artifact history.

Primary docs checked:

- `CHANGELOG.md`
- `CITATION.cff`
- `CLAUDE.md`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`
- `docs/ADOPTION-KIT.md`
- `docs/CRM-DEDUPE-COOPERATION.md`
- `docs/DATA-FLOW-AND-RETENTION.md`
- `docs/DPG-CONFORMANCE.md`
- `docs/I18N.md`
- `docs/RESEARCH-ROADMAP.md`
- `docs/RESPONSIBLE-TECH-AUDITS.md`
- `docs/ROADMAP.md`
- `docs/USER-RESEARCH.md`
- `docs/decisions/0001-matcher-and-defaults.md`
- `docs/decisions/0002-connectors-and-provenance.md`
- `docs/decisions/0003-extraction-seam.md`
- `docs/decisions/0004-address-normalization.md`
- `docs/decisions/0005-dv-policy-pack.md`
- `docs/decisions/0006-schema-stability.md`
- `docs/decisions/0007-review-ui-and-crm-export.md`
- `docs/decisions/0008-solo-maintainer-review-waiver.md`
- `docs/ideation/02-large-scale-fixes.md`
- `docs/ideation/03-expansions.md`
- `docs/ideation/EXP-14-cross-org-linkage-study.md`
- `docs/rulesets/README.md`
- `eval/README.md`
- `eval/large-corpus-report.md`
- `eval/report.md`

Representative test files checked:

- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_address.py`
- `tests/test_connector_conformance.py`
- `tests/test_connectors_civicrm.py`
- `tests/test_connectors_crm_csv.py`
- `tests/test_connectors_salesforce.py`
- `tests/test_consent.py`
- `tests/test_corpusgen.py`
- `tests/test_decisions.py`
- `tests/test_destruction.py`
- `tests/test_evaluate.py`
- `tests/test_extract.py`
- `tests/test_household.py`
- `tests/test_matching.py`
- `tests/test_no_egress.py`
- `tests/test_normalize.py`
- `tests/test_ocr.py`
- `tests/test_pipeline.py`
- `tests/test_policy.py`
- `tests/test_provenance.py`
- `tests/test_review.py`
- `tests/test_schema.py`
- `tests/test_suppression.py`

## Validation Notes

For this docs PR, validation means the scope file was generated from the clean `origin/main` worktree, reviewed against repo metadata and docs inventory, and checked with `git diff --check`. Project test suites are still the authority for code behavior, because this PR changes documentation only.

# Documentation Audit

Last reviewed: 2026-07-08. Base branch: `main`.

This audit records the documentation sweep and remediation loop for this repository. It checks the docs as a system: entry points, root-level process and legal files, project scope, setup and validation notes, safety and privacy posture, architecture and planning docs, local links, and the places where code, tests, workflows, and docs meet.

## Audit Results

| Area | Result | Evidence |
| --- | --- | --- |
| Entry docs | pass | `README.md` present |
| Security/process docs | pass | CONTRIBUTING.md, SECURITY.md, CHANGELOG.md |
| Architecture/planning docs | pass | 1 architecture/interface docs; 6 planning/research docs |
| Safety/privacy/audit docs | pass | 2 safety/privacy/accessibility/audit docs |
| Validation surface | pass | 22 test files; 3 workflow files |
| Local doc links | pass | 123 authored-doc links checked; 0 unresolved |

## Root-Level Documentation Audit

This section covers hand-authored documentation at the repository root and root-adjacent GitHub templates. It is separate from the `docs/` inventory so README, process, legal, release, and project-specific root files do not get hidden inside the larger docs tree.

| Surface | Result | Evidence |
| --- | --- | --- |
| Root README | pass | Present: `README.md` |
| Root process docs | pass | Present: `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` |
| Root legal, citation, and conduct docs | pass | Present: `LICENSE`, `NOTICE`, `CITATION.cff`, `CODE_OF_CONDUCT.md` |
| Other root project docs | info | `CLAUDE.md` |
| Root-adjacent GitHub templates | pass | `.github/CODEOWNERS` |
| Root/template doc links | pass | 34 root-level/template links checked; 0 unresolved |

Root-level files checked:

- `CHANGELOG.md`
- `CITATION.cff`
- `CLAUDE.md`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`

Root-adjacent template files checked:

- `.github/CODEOWNERS`

## Remediation In This PR

- Added missing root-level remediation docs found by the audit loop, including legal, conduct, contribution, or security files where absent.
- Added `docs/PROJECT-SCOPE.md` as the plain-language project and boundary map.
- Added this audit record so future doc changes have a dated baseline.
- Added or refreshed the docs index so scope, audit, and primary docs are easy to find.

## Repo Surfaces Checked

Package and workspace metadata:

- Python package `constituent-reconciler` (>=3.12).

Source and operations surfaces seen at the repo root:

- `Dockerfile`
- `eval/`
- `Makefile`
- `pyproject.toml`
- `src/`
- `tests/`
- `tools/`
- `uv.lock`

Workflow files checked:

- `.github/workflows/ci.yml`
- `.github/workflows/codeql.yml`
- `.github/workflows/trufflehog-scheduled.yml`

## Documentation Inventory

| Category | Count | Representative files |
| --- | ---: | --- |
| architecture and interfaces | 1 | `docs/decisions/0006-schema-stability.md` |
| entry points and repo process | 9 | `.github/CODEOWNERS`, `CHANGELOG.md`, `CITATION.cff`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `LICENSE`, `NOTICE`, `README.md`, plus 1 more |
| other docs | 19 | `CLAUDE.md`, `docs/ADOPTION-KIT.md`, `docs/CRM-DEDUPE-COOPERATION.md`, `docs/DATA-FLOW-AND-RETENTION.md`, `docs/DPG-CONFORMANCE.md`, `docs/I18N.md`, `docs/PROJECT-SCOPE.md`, `docs/README.md`, plus 11 more |
| planning and research | 6 | `docs/RESEARCH-ROADMAP.md`, `docs/ROADMAP.md`, `docs/USER-RESEARCH.md`, `docs/ideation/02-large-scale-fixes.md`, `docs/ideation/03-expansions.md`, `docs/ideation/EXP-14-cross-org-linkage-study.md` |
| safety, privacy, accessibility, and audits | 2 | `docs/DOCUMENTATION-AUDIT.md`, `docs/RESPONSIBLE-TECH-AUDITS.md` |

Full hand-authored doc inventory checked by this pass:

- `.github/CODEOWNERS`
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
- `docs/DOCUMENTATION-AUDIT.md`
- `docs/DPG-CONFORMANCE.md`
- `docs/I18N.md`
- `docs/PROJECT-SCOPE.md`
- `docs/README.md`
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

## Link Check

- Checked 123 local links in authored Markdown and MDX docs.
- Unresolved authored-doc links after remediation: 0.
- Root-level/template unresolved links after remediation: 0.

## Validation Notes

- The audit was generated from a clean worktree based on `origin/main` for this PR branch.
- Ran a local relative-link check over hand-authored Markdown and MDX docs.
- Ran an explicit root-level documentation presence and link check for README, process, legal, project, and template docs.
- Ran `git diff --check` across the PR worktrees after remediation.
- Product test suites remain the authority for runtime behavior; this PR changes documentation only.

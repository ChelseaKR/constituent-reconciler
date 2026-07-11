# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
for [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0.

## [Unreleased]

### Changed
- **Record identity is content-derived and collision-safe (FIX-03)**
  (`pipeline.py`). Generated record ids are now a BLAKE2b digest of the source
  name and the mapped raw values (for example `N3f9a2c1b0d4`) instead of the
  row position (`N0001`), for CSV rows, extracted PDF pages, and .txt/.eml
  bodies alike. Inserting or reordering rows in a source file between
  `reconcile review` and `reconcile apply` no longer re-binds recorded
  verdicts to different people; editing a row changes that row's id and no
  other. Exact-duplicate rows share the digest and carry a deterministic `-2`,
  `-3`, ... suffix in read order.
- **User-supplied ids are namespaced by source.** A value read from the
  recipe's `id_column` becomes `existing:E003` or `incoming:N002`, so the same
  id in two source files can no longer collide. An id that still appears twice
  in one run (a duplicated `id_column` value within one source) raises
  `pipeline.DuplicateIdError` naming the id and source, instead of silently
  dropping one of the records.
- **The decisions file is a versioned surface.** `decisions.json` now carries
  a `decisions_schema` field (`schema.DECISIONS_SCHEMA_VERSION`, currently 1),
  reported by `reconcile schema` alongside the other declared versions. A
  resumed review session warns on stderr when a saved decision references a
  pair that is not in the current run's review queue, instead of ignoring it
  silently.

#### Migration note (FIX-03)
Record ids embedded in artifacts written by earlier versions (decisions files,
provenance logs, review queues, CRM external-id columns keyed on cluster ids)
will not match the ids this version mints from the same data. Finish and apply
any in-flight review with the version that produced it, then re-run
`reconcile run` under this version before recording new decisions. Existing
provenance logs remain verifiable as written; only newly appended entries carry
the new ids. Per the ADR 0006 stability contract this is a pre-1.0 surface
change, shipped with this changelog entry.

### Security
- **Standards-conformance remediation (2026-07-10), release_workflow
  (REL-14)**: added `.github/workflows/release.yml`, a tag-triggered
  (`v*`) release pipeline that re-verifies the tagged commit (`make
  verify` + `make security`) independent of the PR's green check, checks
  tag/`pyproject.toml` version consistency, builds the sdist + wheel,
  generates a CycloneDX 1.7 SBOM, attests build provenance via keyless
  OIDC (Sigstore), and publishes a GitHub Release with the matching
  CHANGELOG section as notes. Closes the SBOM gap (P1-7) previously
  declared in `docs/RESPONSIBLE-TECH-AUDITS.md`. No PyPI publish stage yet
  (not published to PyPI); no `v*` tag has been cut, so the workflow is
  unexercised end-to-end pending the maintainer cutting the first release.

- **Web-boundary hardening for the review server (FIX-01)**: `reconcile
  review` now checks the `Host` header on every request against the address
  it actually bound (closing a DNS-rebinding path to the loopback-only
  server), checks a POST's `Origin` header, if present, against the server's
  own origin, and requires a per-run session token embedded in every rendered
  form on every POST. Together these mean a hostile page the reviewer has
  open elsewhere can no longer forge a verdict or read a pair over the
  loopback interface by guessing a port. `docs/ideation/02-large-scale-fixes.md`
  named this the most serious observed gap between the DV pack's stated
  "cannot become an egress path" claim and the code.

### Added
- **Local OCR backend for scanned intake (EXP-04)**: `extract/ocr.py` adds a
  `PdfplumberOcrExtractor`, selected by `[extract] backend = "pdfplumber+ocr"`,
  that OCRs (via Tesseract, the new optional `ocr` extra) any PDF page with no
  embedded text layer instead of yielding an empty record. Reuses the existing
  label-adjacent field patterns and confidence gate; OCR word boxes become
  `SourceSpan`s so the review queue's source-location columns work the same
  for scanned and digitally-created pages. Closes the gap FIX-12 documented
  between the README's ingest claim and what the code did.

### Security
- **Standards-conformance remediation (2026-07-05)**, closing the audit's
  headline gap — this repo held the portfolio's most sensitive PII (DV-survivor
  constituent records) with zero security scanning and no lockfile:
  - Committed `uv.lock`; `make install` now runs `uv sync --frozen`, so CI and
    local installs use the exact locked dependency set (no floating
    transitive deps under Splink).
  - Dependency-vulnerability gate: `make security` (`pip-audit` +
    `osv-scanner --lockfile uv.lock`), wired as its own blocking CI job.
  - Secret scanning: `.pre-commit-config.yaml` (gitleaks + ruff, staged
    changes), a `secrets` CI job (gitleaks full-history on push/PR), and a
    scheduled weekly TruffleHog (verified-credentials-only) workflow. A
    one-time full-history gitleaks scan on 2026-07-05 found nothing to rotate.
  - `ruff` now runs with the `S` (bandit) and `C90` (complexity) rule sets and
    `ruff format --check`; Python floor raised to 3.12 (`.python-version`,
    `pyproject.toml`, `Dockerfile`).
  - ASVS level (L2) and container-scan/SBOM/secret-management/VEX posture
    declared in `docs/RESPONSIBLE-TECH-AUDITS.md`; AI-Evaluation-Standard N/A
    and an Observability Tier-C declaration added to `docs/ROADMAP.md`; a new
    `docs/I18N.md` declares EN/ES parity as deferred-not-dropped.
  - README gained the standards-conformance table this repo previously
    omitted (silent omission is itself a defect under the portfolio's
    documentation standard); status line now leads with "Beta" per the
    standard vocabulary.
  - `docs/decisions/0008-solo-maintainer-review-waiver.md` records, dated and
    reasoned, that the ≥1/≥2-human-reviewer control is waived while this repo
    has one maintainer, and names the compensating automated gates.
    `docs/rulesets/main.json` is the matching desired-state branch ruleset —
    committed as an artifact but **not yet applied**; applying it is a
    repository-settings action for the maintainer to run (see
    `docs/rulesets/README.md`).
  - SAST: a `sast` CI job runs Semgrep (`p/security-audit`, `p/secrets`, and a
    repo-specific `no-pii-in-logs` rule at `.semgrep/no-pii-in-logs.yml`); a
    `codeql` workflow runs CodeQL for `python` and `actions` on push, PR, and a
    weekly schedule; a `zizmor` job lints the workflow files themselves.
  - A merge-blocking coverage floor (`--cov-fail-under=84`, branch coverage,
    `pytest-cov`) closes the gap between the ROADMAP's earlier claimed figure
    and CI actually enforcing it. Set to 84 rather than the 85 target because
    this PR excludes a pre-existing, in-progress feature branch (see
    `docs/ROADMAP.md`'s metrics ledger note); raise it to 85 once that branch
    lands with its own tests.
  - `__version__` is now derived from installed package metadata
    (`importlib.metadata.version`) instead of being hand-copied alongside
    `pyproject.toml`'s `version`, so the two can no longer drift (REL-02).
  - The Dockerfile's base image is pinned by digest, not just tag
    (`python:3.12-slim@sha256:...`), and a `container-scan` CI job
    (`make docker` + Trivy, blocking CRITICAL/HIGH) was added — **not yet
    locally exercised**, since no Docker daemon was available in the
    environment this remediation ran in; verify on the first real PR.
  - The portfolio-level conformance audit and this remediation's full
    item-by-item execution log live outside this repo, alongside the
    portfolio's other project audits; what remains open here is container
    scanning, the release pipeline, and the branch ruleset actually being
    applied (docs/rulesets/README.md).

## [0.7.0] — 2026-06-29

The WCAG 2.2 AA web review queue and the offline-first CRM export files, two of
the items the README named as remaining before the 1.0 tag.

### Added
- **Local web review UI** (`review/`, `reconcile review`): a non-technical
  reviewer steps through the uncertain candidate pairs in a browser, sees the two
  records side by side with their source spans, and approves or rejects each
  merge. Verdicts are written to `decisions.json` in the same shape
  `reconcile apply` consumes, so the web step replaces the hand-edited CSV without
  changing the rest of the pipeline. Built on `http.server` with no web-framework
  dependency.
  - **Offline by construction**: binds the loopback interface only, inlines all
    CSS and script (no CDN or network fetch), and under the DV pack refuses a
    non-loopback bind, fail-closed, mirroring the connector local-target gate.
  - **Minimization**: the only artifact the review step persists is
    `decisions.json`, which carries record ids and verdicts and no field value;
    request logging is suppressed. A test asserts no reviewed field value reaches
    the file.
  - **Accessibility (WCAG 2.2 AA)**: a real comparison table with scoped headers,
    status carried by text and a symbol rather than colour alone, decision
    controls that work with no JavaScript, keyboard shortcuts as enhancement.
- **Import-ready CRM export connectors** (`connectors/crm_csv.py`):
  `salesforce_csv` and `civicrm_csv` write a CSV mapped to the target CRM's import
  schema (NPSP Contact columns; CiviCRM import columns) plus an external-id column
  keyed on the cluster id, for an idempotent CRM-side upsert. This is the
  offline-first default path: no network call, no secret. The live API push
  connectors stay the explicit opt-in. The column schema is shared with the live
  connectors (`salesforce.FIELD_MAP`, `civicrm.IMPORT_FIELD_MAP`) so the file and
  the API payload cannot drift. Both targets are `is_local = True`, so the DV pack
  permits them while still refusing the network push.
- **ADR 0007** (`docs/decisions/0007-review-ui-and-crm-export.md`).
- `examples/intake-demo/recipe-salesforce-csv.toml` and `recipe-civicrm-csv.toml`;
  20 new tests (128 total).

### Note
- The review UI's structural WCAG 2.2 AA work is in place; a full axe audit and a
  screen-reader walkthrough remain a REVIEW gate in `docs/ROADMAP.md`.

## [0.6.0] — 2026-06-27

The v1.0 engineering deliverables, shipped without the 1.0 tag (the tag is gated
on real-organization adoption, per `docs/ROADMAP.md`).

### Added
- **Salesforce NPSP connector** (`connectors/salesforce.py`): a second
  destination on the same `Connector` interface, using the REST
  upsert-by-external-id endpoint (`PATCH /sobjects/Contact/<ExternalIdField>/...`)
  for idempotent re-runs. Built on an injected transport, fully tested offline.
  `is_local = False`, so the DV pack refuses it like any network target.
- **`[output]` recipe keys** `api_version` and `object_name` for Salesforce;
  `auth_env` now names the token env var for either CRM.
- **One-command Docker self-host** (`Dockerfile`, `.dockerignore`,
  `make docker`), with the PDF extraction extra included.
- **Declared schema/interface versions** (`schema.py`): config, connector
  interface, and report schema versions, exposed by a new `reconcile schema`
  command and stamped into `aggregate_summary.json` as `schema_version`.
- **DPG Standard conformance note** (`docs/DPG-CONFORMANCE.md`) mapping the
  project against the nine indicators.
- **ADR 0006** (`docs/decisions/0006-schema-stability.md`) defining the
  stability contract.
- `examples/intake-demo/recipe-salesforce.toml`; 13 new tests (108 total).

### Note
- The Docker image was not built end-to-end in CI in this release (the
  Dockerfile follows standard patterns); `make docker` builds it locally.

## [0.5.0] — 2026-06-27

## [0.5.0] — 2026-06-27

### Added
- **DV policy pack** (`src/constituent_reconciler/policy.py`): a declarative
  bundle of VAWA/FVPSA confidentiality invariants enforced as merge-blocking
  behavior. `policy_for` maps a pack name to a `Policy`; an unknown pack raises
  `PolicyViolation`, fail-closed.
- **`--policy-pack` CLI flag** on `run` and `apply`, and a `policy_pack` override
  on `load_recipe`, so the DV posture can be applied to any recipe without
  editing it.
- **No-egress enforcement**: connectors now declare `is_local`; under a
  local-targets policy the export refuses a non-local target (CiviCRM) before any
  write. The cloud extraction seam was already fused off for the dv/hipaa packs.
- **Aggregate, suppression-aware export** (`suppression.py`): the DV pack writes
  `aggregate_summary.json` of non-identifying counts with CMS-style small-cell
  suppression — counts of 1-10 suppressed, true zeros preserved, complementary
  suppression so a lone suppressed cell is not recoverable by subtraction.
- **`PolicyViolation`** exception, surfaced by the CLI with a clear message and a
  non-zero exit.
- **ADR 0005** (`docs/decisions/0005-dv-policy-pack.md`) and an expanded privacy
  section in `docs/RESPONSIBLE-TECH-AUDITS.md` with primary VAWA/FVPSA/CMS
  citations and three honesty corrections (the statutory verb is "disclose,
  reveal, or release"; revocable consent is NNEDV best practice not statute; the
  n<11 threshold is the CMS rule, not a HUD/DV mandate).
- 30 new tests across `test_policy.py`, `test_suppression.py`, and the
  merge-blocking `test_no_egress.py` (95 total).

### Changed
- `Recipe` gains `require_local_targets`, `aggregate_export`, and
  `suppression_threshold`, derived from the active pack. `require_consent` now
  comes from the pack and cannot be turned off below what the pack imposes.
- `ExportSummary` gains `aggregate` and `aggregate_path`.
- Version bumped to `0.5.0`.

### Not yet
- The WCAG 2.2 AA web review UI, RFC 3161 timestamping wired to a TSA, and a
  recorded end-to-end demo into a running CiviCRM instance. The `hipaa` pack is
  partial (consent plus no cloud seam) and does not claim the DV pack's full
  invariant set. See `docs/ROADMAP.md`.

## [0.4.0] — 2026-06-27

### Added
- **Address normalization** (`src/constituent_reconciler/address.py`): a
  vendored, deterministic CASS-style standardizer that maps an address to
  USPS-style abbreviations (USPS Publication 28 street suffixes, directionals,
  unit designators), so two writings of the same address reduce to one matching
  key. Idempotent and offline.
- **`address` canonical field** with a three-level matcher comparison (exact,
  close by Jaro-Winkler at 0.90, else), weighted below email.
- **Optional libpostal backend**: `[normalize] address_backend = "libpostal"`.
  Never required; selecting it without the `postal` package and libpostal C
  library raises a clear `ImportError` instead of silently falling back.
- **`[normalize]` recipe section** with `address_backend` (default
  `"deterministic"`).
- **`examples/address-demo/`**: fixture and recipe demonstrating address-format
  variation resolving to a merge.
- **ADR 0004** (`docs/decisions/0004-address-normalization.md`).

### Changed
- `Record` gains an `address` slot in the canonical schema, active only when a
  recipe maps it; the committed demo eval is unchanged (CI-verified).
- `normalize_record` now preserves `Record.spans` (a latent v0.3 bug where spans
  were dropped before reaching the review queue in a full pipeline run).
- Version bumped to `0.4.0`.

### Not yet
- The DV policy pack full invariant set (v0.5), the WCAG 2.2 AA web review UI,
  and RFC 3161 trusted timestamping wired to a TSA. The address standardizer is
  CASS-style and is **not** USPS-certified. See `docs/ROADMAP.md`.

## [0.3.0] — 2026-06-27

### Added
- **Extraction seam** (`src/constituent_reconciler/extract/`): an offline
  pdfplumber-based PDF extractor that pulls canonical fields from form-like PDFs
  using label-adjacent patterns and returns a confidence score and source-span
  pointer per field.
- **Source-span pointers** on `Record.spans` (`dict[str, SourceSpan]`): each
  PDF-sourced field carries the source filename, page number, and bounding box.
  The review queue CSV gains `{field}_left_span` and `{field}_right_span` columns
  when any record has spans, so a reviewer can navigate back to the original.
- **`SourceSpan`** type in `constituent_reconciler.models`, re-exported from the
  top-level package.
- **Policy-gated cloud seam**: `NoOpSeam` (default) and `BedrockSeam` (the
  documented extension point for deployers with AWS credentials). `make_seam()`
  returns `NoOpSeam` unconditionally for `dv` and `hipaa` policy packs; the
  non-egress invariant is enforced at construction time and covered by tests.
- **Folder-based ingestion**: the recipe's `incoming` field can point to a
  directory; the pipeline routes `.csv` files through the structured reader and
  `.pdf` files through the extractor.
- **`[extract]` recipe section**: `backend` (default `"none"`) and
  `confidence_threshold` (default `0.5`).
- **`pdfplumber>=0.11`** added as an optional `[extract]` dependency and to the
  `[dev]` extras so extraction tests run in CI.
- **`cohen_kappa(predicted, actual)`** in `evaluate.py`: the calibration seam
  for comparing an LLM extraction judge's confidence against human-labeled field
  accuracy. Not yet wired into the eval report; the function is the planned seam.
- **ADR 0003** (`docs/decisions/0003-extraction-seam.md`): documents the
  pdfplumber choice, the regex-over-text-layer approach, the confidence
  heuristic, and the cloud-seam protocol.

### Changed
- `Record` gains `spans: dict[str, SourceSpan]` (default empty dict). Existing
  CSV-only code and tests are unaffected.
- `Recipe` gains `extract: ExtractConfig` (default `backend="none"`). Existing
  recipes with no `[extract]` section use the CSV-only path unchanged.
- Version bumped to `0.3.0`.

### Not yet
- `BedrockSeam.refine()` raises `NotImplementedError` until a deployer wires in
  page-to-image conversion and the Bedrock response parser.
- Address normalization, the WCAG 2.2 AA web review UI, and the DV policy pack
  full invariant set. RFC 3161 trusted timestamping is a pluggable authority, not
  yet wired to a TSA. See `docs/ROADMAP.md`.

## [0.2.0] — 2026-06-24

### Added
- CiviCRM write-back via API v4, an upsert keyed on an external identifier
  so re-runs update contacts instead of duplicating them, built on an injected
  transport for testability. A connector interface with the CSV writer refactored
  onto it. An append-only, tamper-evident provenance log (BLAKE2b hash chain)
  with a `reconcile verify` command and a pluggable timestamp authority. An
  `[output]` recipe section that selects the connector.

## [0.1.0] — 2026-06-24

### Added
- Resolve and review core. Reads existing and incoming CSVs, normalizes,
  scores candidate pairs with a Splink matcher configured by pre-tuned m and u
  defaults (no training, no labeled pairs), assigns each pair to an auto, review,
  or drop band, clusters confident merges, and writes resolved records plus a
  review queue.
- Fail-closed gate: uncertain pairs go to review, never to an auto-merge.
- Consent export gate: under a consent-required policy pack, a record without
  granted consent is withheld and recorded without field values.
- `reconcile run`, `reconcile eval`, and `reconcile apply` commands.
- Committed eval (`eval/report.md`) on seeded synthetic fixtures with planted
  ground truth, reporting a gated false-merge rate with Wilson intervals.

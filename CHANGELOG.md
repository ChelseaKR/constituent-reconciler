# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
for [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0.

## [Unreleased]

Nothing yet — see `docs/ROADMAP.md` for what comes next.

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

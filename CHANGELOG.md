# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
for [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0.

## [Unreleased]

Nothing yet — see `docs/ROADMAP.md` for what comes next.

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

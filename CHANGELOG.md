# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
for [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0.

## [Unreleased]

### Added
- v0.2: CiviCRM write-back via API v4, an upsert keyed on an external identifier
  so re-runs update contacts instead of duplicating them, built on an injected
  transport for testability. A connector interface with the CSV writer refactored
  onto it. An append-only, tamper-evident provenance log (BLAKE2b hash chain)
  with a `reconcile verify` command and a pluggable timestamp authority. An
  `[output]` recipe section that selects the connector.
- v0.1 core: resolve and review. Reads existing and incoming CSVs, normalizes,
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

### Not yet
- Document extraction (PDF and scan), address normalization, and a web review
  UI. RFC 3161 trusted timestamping is a pluggable authority, not yet wired to a
  timestamp authority server. See `docs/ROADMAP.md`.

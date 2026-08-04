# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
for [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0.

## [Unreleased]

### Added
- **Progress events for `run` and `apply` (UC-01 remainder, #77).**
  `pipeline.run` and `pipeline.export` accept a `ProgressSink` (new
  `progress.py`); the default sink discards every event, so library callers
  see no change unless they pass one. The pipeline emits started, advanced,
  and finished events for ingest, extract, normalize, score, write, and
  review artifact, with completed/total counts where a denominator exists:
  file and document totals come from a pre-read ingest plan, record totals
  from the batch itself, and export totals from the exportable set and the
  review queue. Stage durations on finish events come from the same
  `_StageTimer` marks the run summary records. Events are content-free by
  construction (stage name, status, counts, seconds), and a test scans a
  fixture run's payloads for planted values, paths, and record ids. The CLI
  renders events on stderr: one line updated in place on a TTY, stable
  newline records on anything else, and never a control character to a
  non-TTY stream. A command that skips a stage emits nothing for it, so a
  run with no PDF or text sources reports no extract stage, while `--dry-run`
  emits the same write and review-artifact events as a real run because both
  stages still execute there. The UC-01 large-corpus before/after numbers
  remain outstanding (#78).
- **Mixed CSV and PDF corpus variant for the stage baseline (issue #78).**
  `tools/corpusgen/generate.py --pdf-share` writes part of the incoming side
  as seeded, digitally created text-layer PDF intake documents, one record
  per page, with a manifest accounting for which rows each document carries.
  `make perf-baseline-pdf` measures the six stages over that corpus, so the
  extract row reports the time the pipeline's own pdfplumber reader spent
  instead of the honest 0.0s a CSV-only corpus produces, and the UC-01 stage
  cache has a real before number for the extraction half. Ingest reports its
  wall clock with the reader's time removed, so the two rows partition the
  walk. The measured artifacts are committed at
  `eval/large-corpus-stage-baseline-pdf-2026-08-04.{md,json}`: 36.9s of
  extraction over 3,756 pages in 151 documents, on the same machine class as
  the CSV-only baseline that reports 0.0s. The PDF writer
  (`tools/corpusgen/pdfwrite.py`) is stdlib-only dev tooling: no new package
  dependency, no runtime import, deterministic byte for byte, and able to
  encode the non-ASCII names the transliteration channel plants, which the
  existing one-page `testing.make_pdf` helper cannot. The harness refuses a
  corpus whose PDFs, CSVs, or manifest changed after generation, or that
  gained a file in its incoming directory afterwards, and the generator
  refuses to clear an `--out-dir` it cannot recognize as a corpus it wrote.
  Both artifacts state which fields a PDF-carried record loses (address and
  consent have no extraction pattern, and prose dates do not match the
  numeric date pattern) and quote the run's own banding counts, so the
  run-count difference is documented without claiming a cause the run did
  not measure.
- **Read-only split repair planning (UC-03, second pull request).**
  `reconcile plan-split --manifest <run manifest> --cluster <id>` turns one
  written cluster a reviewer identified as a bad merge into a local repair
  plan. Planning requires a stated reason and a reviewer identity, verifies
  the recipe and source batch against the manifest's digests, and takes the
  written cluster's members, external id, fill policy, and field lineage from
  the intact provenance chain before recomputing the golden record over
  exactly that member set; any drift refuses rather than guessing.
  Reconstruction is fully offline and contacts no destination. The plan file
  (`repair_plan.json`, schema version 1) records the old external id, one
  proposed split record per member, the fields whose written value came from
  a member being split away, and the operations the destination supports;
  its raw values live only in that local file, which joins
  `destruction.PII_ARTIFACTS`, the retention inventory, and the threat model,
  while provenance stores the plan's digest in a new `repair-plan` entry.
  Every split pair becomes a binding rejected cannot-link in the decisions
  file, and a test proves the next run cannot re-form the cluster. Beside the
  planner, `connectors/repair.py` adds the ADR 0012 capability-declaration
  surface: exact enumerated destination versions, operations marked
  destructive or not, and required vendor-evidence fields, with wildcard or
  range versions refused at construction. No adapter publishes a declaration,
  conformance tests assert that undeclared means no repair operations, and
  unsupported destinations get manual instructions with no flag to force a
  generic operation. `apply_repair` execution and the CiviCRM pilot remain
  unimplemented.
- **Cutover review and correction-file export (UC-02, second PR).** Two new
  subcommands finish the migration-assurance flow. `reconcile compare-review`
  serves the same local web review queue, session, and decisions machinery
  `reconcile run` uses, over a comparison's undecided pairs; verdicts save to
  `compare_decisions.json` and reviewer field corrections to
  `corrections.json`, both re-scored on apply. `reconcile compare-apply` then
  emits `target_corrections.csv`, a local, import-ready correction file for
  the target side, built with the same import field maps and local writer as
  the `salesforce_csv` and `civicrm_csv` exports (`--format` chooses the
  shape; the default keeps canonical column names). The export fails closed
  three ways: it refuses while any review pair is undecided or awaiting a
  second reviewer (a comparison with zero review pairs may export without a
  review step), it refuses when `compare_manifest.json` is missing or no
  longer matches the inputs, and identities without active consent are
  withheld and counted (`cutover_withheld.csv`, ids and reason only) whenever
  either side's recipe requires consent. After a successful export the
  comparison manifest gains an `export` section binding the correction and
  decisions files by digest with counts only, under the new versioned
  `cutover_corrections` schema. No comparison command can reach a live
  connector; `tests/test_compare_apply.py` holds that invariant alongside
  the review, manifest, and consent gates. `target_corrections.csv`,
  `cutover_withheld.csv`, and `corrections.json` join the destruction
  inventory, closing a pre-existing gap for the run pipeline's corrections
  file, and docs/DATA-FLOW-AND-RETENTION.md covers all of them.
- **Large-corpus stage-timing baseline (UC-01 "before" side).**
  `tools/corpusgen/stage_baseline.py`, run with `make perf-baseline`, times
  the six pipeline stages (ingest, extract, normalize, score, review
  artifact, write) over the seeded 50k synthetic corpus and records peak
  resident memory per stage, with the pinned corpus parameters, an input
  digest, and the measuring machine's environment captured content-free:
  counts and durations only, no field values, no user paths. The dated
  report and its JSON companion are committed at
  `eval/large-corpus-stage-baseline-2026-08-03.{md,json}` as the pre-cache
  numbers the UC-01 stage cache is measured against; the future cached run
  diffs its own JSON against this one. Both artifacts state plainly that
  they describe one run on one named machine class and are not a
  performance promise. A CI-sized smoke test proves the harness on a tiny
  corpus and asserts byte-for-byte that its composed stages produce the
  same artifacts `pipeline.run` and `pipeline.export` produce, so harness
  drift fails CI instead of skewing a committed baseline.
- **External-gates runbook.** `docs/EXTERNAL-GATES-RUNBOOK.md` writes down the
  maintainer's exact hand-run steps for the five "External gate" rows in
  `docs/ROADMAP-CLOSEOUT.md`'s canonical 1.0 gates table: the screen-reader
  walkthrough with reviewed Spanish copy and ACR, the ruleset apply plus the
  signed-tag release ceremony, the recorded CiviCRM demonstration, real
  adopting organizations, and the schema-stability window. Each section names
  the repository prerequisites that already exist, the ordered steps, the
  evidence artifact and where it gets recorded, and honest failure notes. The
  closeout's no-fabricated-evidence principle governs throughout; the runbook
  never substitutes a fixture for a human result.
- **Content-addressed stage cache for extraction and normalization (UC-01).**
  A recipe's new `[cache]` section (validated fail-closed; absent means off)
  stores extraction and normalization results as content-addressed files
  under `stage_cache/` inside the output root, or under an explicitly
  configured local `dir` boundary; URL-shaped values are refused at load
  time. Keys digest the input, the declared recipe schema version, the
  active field mapping, the package version, the installed version of the
  library doing the stage's work (pdfplumber for PDF extraction, the postal
  package under the libpostal address backend), and the stage's backend
  configuration, so editing one source row re-keys that row alone, a
  dependency upgrade orphans that dependency's old entries, and any
  mismatched entry is ignored rather than coerced. A stage whose backing
  library version cannot be determined is not cached at all, and a parse the
  sandbox killed against a resource limit is returned fail-closed but never
  stored, so a transient timeout or memory cap cannot freeze a document out
  of reconciliation. Scoring, banding, and clustering never touch the
  cache: term frequencies and cross-batch candidates change pair
  probabilities whenever the population changes, and a merge-blocking test
  proves cached and uncached runs byte-identical. OCR and model-seam
  extraction backends bypass the cache because their output is not a pure
  function of the file bytes. The run manifest and `run_summary.json` record
  cache policy, hit/miss counts, and stage durations, all content-free
  (report schema version 4). The cache directory is a PII artifact:
  `reconcile destroy` covers it, `--cache-dir` reaches an explicit boundary
  but refuses any directory that does not have the stage-cache shape, the
  cache walk deletes nothing outside `extract/` and `normalize/` entry
  files, and each deleted entry gets its own destruction certificate. One
  UC-01 item is deliberately not part of this change: the large-corpus
  wall-clock and peak-memory before/after numbers from the plan item's
  acceptance criteria. No benchmark has been run, so none is claimed;
  docs/CLAIMS-AUDIT.md records the gap (#78). The progress-event work the
  cache change deferred (see docs/NOVEL-USE-CASES-PLAN.md) lands in this
  same release; see the progress-events entry above.
- **Read-only migration cutover comparison (UC-02, first PR).** `reconcile
  compare --left <recipe or csv> --right <recipe or csv>` resolves a legacy
  CRM export against a target export without building any connector. Records
  carry a left/right side label through the existing mapping, normalization,
  and matcher backend, and the command classifies every identity as matched,
  left-only, or right-only, flags value conflicts on matched people, and
  marks identities an undecided pair touches as needing review. Field values
  stay in two local artifacts, `cutover_report.csv` and `cutover_review.csv`,
  both added to the destruction inventory. The count-only
  `migration_summary.json` carries its own versioned schema
  (`migration_summary` in `reconcile schema`), and `compare_manifest.json`
  binds both mapping recipes and the digest of every input file. Recipes that
  disagree on thresholds or address backend are refused rather than silently
  reconciled, and a merge-blocking test proves no compare code path can
  construct a write connector, in the spirit of `tests/test_no_egress.py`.
  The post-review correction-file export is the second UC-02 change and is
  not part of this one.
- **Multiyear roadmap, 2026 H2 through 2029.** `docs/ROADMAP-MULTIYEAR.md`
  arranges the Now/Next/Later plan and the closeout's external 1.0 gates into
  four horizons, each organized by workstream, under the one-maintainer
  60/30/10 capacity model. External gates stay visibly external and are never
  booked as engineering; where the future depends on adopters or partners the
  document states the gate instead of a date. It carries the closeout's
  product principles and the plan's exclusions forward as permanent non-goals
  and sets a per-release and per-half review cadence in which adopter
  evidence outranks synthetic personas. `docs/ROADMAP.md` gains a pointer
  marking it a historical record, and `docs/NOVEL-USE-CASES-PLAN.md` one
  marking it the near-term detail under the new umbrella document.
- **Repair-capability decision record (UC-03 study).**
  `docs/adr/0012-connector-repair-capabilities.md` decides the protocol for
  post-write split repair ahead of implementation: `inspect_repair` and
  `apply_repair` as optional connector capabilities separate from
  `Connector.write_all`, a declaration that names the exact destination and
  version pairs an adapter verified, read-only repeatable planning, a
  mandatory second reviewer for remote destructive operations, manual
  instructions instead of a forced generic delete on unsupported
  destinations, CiviCRM as the pilot, and the threat-model and
  destruction-inventory updates required before any repair plan is stored.
  Vendor delete/merge/restore semantics are named as open inputs to be read
  from current documentation and a live disposable instance, not asserted.
  `docs/NOVEL-USE-CASES-PLAN.md` and `docs/ROADMAP-CLOSEOUT.md` cross-reference
  the record. Documentation only; no code changes.
- **Airtable native-upsert connector (roadmap E3).** The new `airtable`
  destination batches at Airtable's ten-record limit, uses
  `performUpsert` on the configured external-id field, reads a personal access
  token from the environment, and fails closed on response-count drift.
  Injected-transport and registry-conformance tests cover dry-run purity,
  create/update classification, rate-limit reporting, and the DV pack's
  non-local-target refusal. Apricot remains externally blocked and Sheets is
  explicitly closed as a direct target because a client-side read/write
  pseudo-upsert would not meet the connector contract.
- **OpenSSF Scorecard in CI (roadmap D8).** `.github/workflows/scorecard.yml`
  runs the Scorecard analysis weekly and on every push to `main`, publishes
  results to the OpenSSF API, and uploads SARIF to Code Scanning. The first
  dated snapshot (aggregate 6.8, run 2026-07-17 with the CLI) is committed at
  `docs/audits/scorecard-2026-07.md` with an honest reading of each low score.
- **Sandboxed PDF parsing is now the pipeline default (FIX-10 wiring).**
  `read_pdf_records` runs every PDF parse in the resource-limited child
  process that `extract/sandbox.py` introduced but nothing previously used:
  a malformed or hostile intake file fails closed to human review instead of
  crashing the run. `backend = "pdfplumber+ocr"` gets the same containment
  through a dedicated OCR child worker. Recipes opt out with `[extract]
  sandbox = false`. The threat model's "missing process boundary" finding and
  its residual-risk list are updated to match; the boundary is containment,
  not privilege separation, and the docs say so.
- **Every non-dry-run export stamps `out/run_manifest.json` (FIX-08 wiring).**
  `pipeline.export` now builds the reproducibility manifest that
  `manifest.py` introduced but nothing previously called: BLAKE2b digests of
  the recipe file and each input file, package and Splink versions, resolved
  thresholds, and policy pack. The provenance log opens with a `run-start`
  entry carrying the manifest's hash, ahead of the run's write entries, so
  every write chains back to the exact configuration that produced it; dry
  runs stamp nothing. A Recipe built in code (no recipe file) records a null
  recipe hash rather than inventing one.
- **Definition of done, PR template, hygiene gate, and ledger ownership
  (maturity batch M7/M9/M10).** `DEFINITION_OF_DONE.md` writes down the
  working agreement the quality bar implied; `.github/pull_request_template.md`
  asks for the same items at review time, including eval-regeneration,
  audit re-stamping on release, and new-dependency rationale. `make hygiene`
  (tools/hygiene.py, inside `make verify` and CI) fails on debt markers,
  uncoded suppressions, unexplained coverage exclusions, and bare Semgrep
  waivers, with its own test suite. The metrics ledger gains Measured-by and
  Owner columns plus a quarterly solo-scale DORA note that states plainly
  what is not yet measurable.
- **Canonical GenAI observability for opt-in extraction seams.** Bedrock
  Converse and loopback-local model calls now emit the reviewed, pinned
  STANDARDS telemetry schema: provider/model identity, input/output tokens,
  duration, finish reason, and estimated cost. Page images, prompts, responses,
  record ids, and extracted values are excluded; regression tests exercise the
  JSON payload with representative PII. The vendored shim records its immutable
  STANDARDS commit in `.standards-version`.
- **Disaggregated matching-risk audit (R5).** `evaluate()` accepts explicit
  planted pairs by risk class, the Markdown report renders per-class surfaced
  and blocking-miss counts, and `examples/bias-demo/` covers transliterated,
  hyphenated/punctuated, non-Western-order, rural-route, and informal-address
  cases. `make eval-bias` regenerates `docs/audits/bias-report.md`, and CI
  blocks report drift while preserving measured misses.
- **Binding human rejections (FIX-02)**: rejected pairs are cannot-link
  constraints on final clustering. If transitive AUTO edges would reunite a
  rejected pair, the component is refused and its automatic edges return to
  review with an explicit explanation; no golden record may contain both
  rejected endpoints.
- **Attributed field correction in review (EXP-01)**: reviewers can fix a field
  while approving a pair. Values live only in local `corrections.json`; the
  decisions audit remains PII-free. A correction invalidates earlier verdicts,
  and two-person mode requires a later distinct reviewer to see and approve the
  corrected evidence before apply. Corrections are applied before normalization
  and flow through lineage, matching, and export.
- **Reviewer calibration with planted pairs (EXP-09)**: `[review] calibration = N`
  deterministically mixes clearly synthetic known-answer pairs into the local
  review queue. A persistent banner discloses their presence, the CLI reports
  reviewer agreement and Cohen's kappa, and planted records and verdicts are
  excluded from `decisions.json` and its audit trail so they can never reach
  `reconcile apply` or a connector.

### Changed
- **Field-level lineage and a named survivorship fill policy (FIX-07)**
  (`models.py`, `decisions.py`, `config.py`, `pipeline.py`, `provenance.py`,
  `schema.py`). Every golden record now carries `field_sources`, mapping each
  non-empty merged field to the member record id that supplied its value, and
  blank-fill order is a named policy (`[policy] fill` in the recipe, default
  `survivor-then-lowest-id`, the previous implicit behavior) validated at
  load time. Provenance entries record the lineage as member ids only, never
  field values, and `aggregate_summary.json` names the active policy.
  `REPORT_SCHEMA_VERSION` bumps 2 to 3 for the new keys; version-2 artifacts
  still verify unchanged.
- **CiviCRM email and phone write through dedicated entities (R7)**
  (`connectors/civicrm.py`). Email and phone move off the API v4 join-field
  shorthand onto the dedicated Email and Phone entities: once the contact id
  is resolved, the connector updates the contact's primary row when one exists
  and creates it when none does. A record with no value for a field makes no
  call for it, so an empty value never blanks a stored row, and
  `Contact.create` returning no id now raises `ConnectorError` before any
  entity write instead of reporting a created row without an id. Dry-run
  payloads and the provenance hash input still carry email and phone. The
  recorded end-to-end demo against a running CiviCRM stays open in
  `docs/ROADMAP.md` v0.2.
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
  a `decisions_schema` field (`schema.DECISIONS_SCHEMA_VERSION`),
  reported by `reconcile schema` alongside the other declared versions. A
  resumed review session warns on stderr when a saved decision references a
  pair that is not in the current run's review queue, instead of ignoring it
  silently.

### Fixed
- Canonicalized the architecture decision log under `docs/adr/`, added the
  portfolio meta-ADR and authoring template, and resolved the duplicate 0009
  identifier by renumbering the automated axe decision to 0011. Repository
  references and the README conformance table now use the canonical path and
  labels; the declared but unimplemented internationalization commitment
  remains an explicit gap.
- GenAI telemetry now rejects negative and boolean token counts, drops unknown
  provider finish reasons, emits content-free fallback warnings, and isolates
  span/log exporter failures so instrumentation cannot change a provider result.
- `make test` no longer nests pytest-cov inside `coverage run`, which overwrote
  the valid pytest coverage data with an empty outer report. Pytest now owns the
  single coverage session, and the temporary 84% threshold is raised to the
  documented 85% branch-coverage gate (87.65% observed on this change).
- The review server accepts the IPv6 loopback host form it actually binds,
  without weakening the loopback-only and Host-header checks.
- Consent withholding summaries now evaluate the destination-scoped consent
  lifecycle, so an out-of-scope grant cannot be reported as exportable.
- CiviCRM contact creation fails closed when the API returns an empty response,
  before any email or phone entity write is attempted.

#### Migration note (FIX-03)
Record ids embedded in artifacts written by earlier versions (decisions files,
provenance logs, review queues, CRM external-id columns keyed on cluster ids)
will not match the ids this version mints from the same data. Finish and apply
any in-flight review with the version that produced it, then re-run
`reconcile run` under this version before recording new decisions. Existing
provenance logs remain verifiable as written; only newly appended entries carry
the new ids. Per the ADR 0006 stability contract this is a pre-1.0 surface
change, shipped with this changelog entry.

### Added
- **Generic webhook connector** (`connector = "webhook"`, `connectors/webhook.py`),
  the self-contained slice of roadmap item E3 (Apricot, Airtable, Sheets,
  generic webhook): pushes resolved, consented records as one JSON POST per
  record to any HTTP(S) endpoint, with an optional bearer token and an
  optional HMAC-SHA256 request signature (`[output] signing_secret_env`) so a
  receiver can verify authenticity. Registered on the connector registry
  (`connectors.register`, FIX-09) and passes the same conformance suite
  every connector does; `is_local = False`, so the `dv` policy pack refuses
  it as a write target the same way it refuses CiviCRM and Salesforce.
  Consent, including the destination-scoped lifecycle gate
  (`Consent.reason(destination=...)`), is enforced upstream by the existing
  export gate, not reimplemented. Documented payload shape, worked example,
  and signature verification code: `docs/connectors/webhook.md`. Example
  recipe: `examples/intake-demo/recipe-webhook.toml`.
- Design briefs (research, not implementation) for E3's three remaining,
  proprietary-vendor connectors -- Apricot (Bonterra), Airtable, Google
  Sheets -- each covering auth model, rate limits, pagination, export shape,
  and `is_local` classification, cited against each vendor's current API
  docs: `docs/connectors/{apricot,airtable,sheets}-design.md`. Deferred as
  implementation pending a priority decision and API credentials this
  environment does not have.

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
- **Fail-closed recipe validation and `reconcile validate` (FIX-04)**:
  `config.load_recipe` now rejects an unknown `[section]` or an unknown key
  inside a known section — naming the nearest valid spelling — instead of
  silently ignoring it via `dict.get`. A mapping key outside the canonical
  fields (a typo'd `frist_name`) now raises instead of vanishing from the
  mapping. A new `reconcile validate --config recipe.toml` command loads and
  shape-checks a recipe, confirms `incoming`/`existing` point at files that
  exist, and prints the active policy pack, thresholds, and switches, without
  running the pipeline. `docs/ADOPTION-KIT.md` gets a "validate before you
  run" step.
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
  - `docs/adr/0008-solo-maintainer-review-waiver.md` records, dated and
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
- **Reviewer audit trail** (roadmap E4): every review verdict is attributed.
  `reconcile review` now requires `--reviewer NAME`, and `decisions.json` grows a
  versioned shape (`decisions_schema: 2`) with an `audit` section mapping each pair
  to the reviewers who decided it, their verdicts, and UTC timestamps. The
  top-level `approved`/`rejected` lists keep the version-1 shape, so
  `reconcile apply` reads both versions unchanged, and the file still carries no
  field value of a reviewed record. A blank reviewer name is refused,
  fail-closed. Sessions resume from either file shape; verdicts from a version-1
  file resume attributed to `unrecorded`.
- **Two-person review** for sensitive merges: with
  `reconcile review --require-second-reviewer`, or
  `require_second_reviewer = true` in the recipe's new optional `[review]`
  section (on by default under the `dv` policy pack), a merge only lands in
  `approved` after two distinct reviewer names approve it. A lone approval is
  held in the audit section as awaiting a second reviewer, the same name cannot
  supply both approvals, and any rejection rejects immediately; disagreement
  never merges. `reconcile apply` refuses a decisions file that still holds
  half-approved pairs, naming them. The review pages show who is reviewing and
  which pairs await a second reviewer. A recipe or flag may turn the requirement
  on under any pack; nothing may turn off a pack that imposes it. 19 new tests
  (149 total).

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
- **ADR 0007** (`docs/adr/0007-review-ui-and-crm-export.md`).
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
- **ADR 0006** (`docs/adr/0006-schema-stability.md`) defining the
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
- **ADR 0005** (`docs/adr/0005-dv-policy-pack.md`) and an expanded privacy
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
- **ADR 0004** (`docs/adr/0004-address-normalization.md`).

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
- **ADR 0003** (`docs/adr/0003-extraction-seam.md`): documents the
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

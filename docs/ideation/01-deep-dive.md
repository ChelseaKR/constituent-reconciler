# Deep dive: current state at v0.7

Drafted 2026-07-01, from a full read of the source at commit `88dc26a`
(working tree clean). Claims below cite the files they come from; where a
claim is an inference rather than an observation, it says so.

## Architecture as built

The pipeline is a small, well-factored state machine, exactly as `CLAUDE.md`
prescribes, though the module layout has drifted from the spec (see debt
item 6 below).

- **Ingest and orchestration** live together in
  `src/constituent_reconciler/pipeline.py`: `read_records` (CSV),
  `read_pdf_records` (PDF via the extract package), `_ingest_source`
  (routing), `run` (normalize, score, band, cluster, reduce), and `export`
  (consent gate, connector write, provenance, review queue, aggregate
  summary). It is the largest module and the only one that knows about every
  other one.
- **Matching** is honestly a wrapper: `matching.py` turns records into a
  pandas frame and runs Splink 4 on DuckDB with hand-set m/u probabilities
  from `defaults.py`. No training, no labeled pairs. Banding and clustering
  live in `decisions.py` (two thresholds, union-find over AUTO edges only,
  survivor selection in `_choose_primary`).
- **Normalization** is deterministic and offline: `normalize.py` (names,
  dates, email, phone) and `address.py` (vendored USPS Pub 28 token tables,
  optional libpostal backend that raises rather than silently falling back).
- **Policy** is a declarative bundle: `policy.py` maps a pack name to five
  switches, each read by exactly one site (`consent.py`, `extract/seam.py`,
  `pipeline.build_connector`, `pipeline.export`). Unknown pack names raise
  `PolicyViolation`, fail-closed.
- **Connectors** sit behind a `Protocol` in `connectors/base.py` with an
  `is_local` flag the DV pack reads. Four real connectors: `csv_out.py`,
  `crm_csv.py` (import-ready files sharing field maps with the live
  connectors), `civicrm.py` and `salesforce.py` (injected-transport upserts).
- **Review** is a stdlib `http.server` app: `review/session.py` (pure state,
  decisions persisted as id pairs only), `review/server.py` (loopback bind,
  DV refuses non-loopback), `review/render.py` (inlined CSS/JS, WCAG 2.2 AA
  structure, plain-language match rationale from R11).
- **Evidence machinery**: `provenance.py` (BLAKE2b hash chain with pluggable
  timestamp authority), `evaluate.py` (Wilson intervals, asymmetric metrics,
  an unwired `cohen_kappa`), `suppression.py` (CMS-style small-cell
  suppression with complementary suppression), `schema.py` (declared surface
  versions).

Tests: 19 files, 130 test functions, including the merge-blocking
`tests/test_no_egress.py` and `tests/test_consent.py`. CI
(`.github/workflows/ci.yml`) is Makefile-driven, SHA-pins actions, and fails
if the committed `eval/report.md` drifts.

## What is genuinely strong

1. **The invariants are tests, not prose.** The DV pack's four claims each
   map to named tests, and enforcement happens at construction time
   (`extract/seam.py` returns `NoOpSeam` before any call site exists;
   `build_connector` refuses non-local targets before a write). This is the
   rare repo where the README's strongest claims are the best-tested code.
2. **Honesty is load-bearing.** The eval report publishes a 0/6 false-merge
   rate with its embarrassingly wide Wilson CI ([0%, 39.0%]) instead of
   hiding it. "CASS-style, not certified" appears in code comments
   (`address.py` docstring), and `RESPONSIBLE-TECH-AUDITS.md` records three
   corrections against its own earlier claims.
3. **Dependency discipline.** One heavy dependency (Splink), stdlib
   everywhere else, pandas confined to `matching.py`. The review UI's
   framework-free construction is a real accessibility and supply-chain
   asset.
4. **The review surface is the product and reads like it.** The
   `MatchRationale` sentences in `review/session.py` distinguish
   "disagrees" from "was blank so could not be compared," which mirrors the
   matcher's null level. Few review UIs get this right.

## Structural debt and gaps actually observed

1. **A human rejection can be silently overridden by transitivity.**
   `decisions.build_clusters` unions AUTO edges only; a pair the reviewer
   rejected (banded DROP via `_apply_overrides`) still ends up in one
   cluster if two other AUTO edges chain through a third record. Nothing
   detects or reports the contradiction. This cuts against the "no silent
   merge" core promise.
2. **Record identity is positional.** `read_records` mints ids as
   `f"{prefix}{index:04d}"` when no `id_column` is set, so decisions files
   re-attach by row order; editing a CSV between `run` and `apply` can bind
   verdicts to different people. Duplicate ids across sources silently
   overwrite in the `records` dict comprehension in `pipeline.run`.
3. **Config parsing is fail-open.** `config.load_recipe` reads every key
   with `.get(..., default)` and ignores unknown sections and keys. A recipe
   that misspells `auto` under `[thresholds]` silently runs at the default
   0.97. For a tool whose ethos is fail-closed, the recipe is the one
   surface that fails open.
4. **Silent input loss.** `_ingest_source` skips non-CSV/PDF files in a
   directory without a count; `read_pdf_records` drops pages that yield no
   name; `normalize_dob` maps unparseable dates to "" with no report. The
   run summary (`report.py`) cannot tell an operator what was not read.
5. **The review server trusts the browser.** `handle_post` in
   `review/server.py` accepts any POST with no origin check or token, and
   `http.server` does not validate the Host header. A malicious web page
   could forge verdicts against the loopback port, and DNS rebinding could
   in principle read pages that contain constituent field values. The
   loopback bind is necessary but not sufficient for the "cannot become an
   egress path" claim.
6. **Spec drift.** README says a reviewer can "approve, correct, or
   reject," but `review/session.py` implements approved/rejected only.
   `CLAUDE.md` and the README both list email bodies as an ingest source;
   `_ingest_source` handles only `.csv` and `.pdf`. `CLAUDE.md`'s module map
   names `ingest.py`, `gate.py`, `resolve.py`, none of which exist. Scans
   are named in the README, but `extract/pdf.py` has no OCR backend, so an
   image-only scan yields nothing offline.
7. **Consent is a token, not a lifecycle.** `models.CONSENT_GRANTED` is a
   set-membership check. `CLAUDE.md` promised expiry and revocation
   handling; VAWA's own language (quoted in `policy.py`) requires consent to
   be "reasonably time-limited." There is no date, scope, or destination on
   a consent value anywhere in the data model.
8. **The golden record has no lineage.** `decisions.golden_records` fills
   blanks "deterministically by member id order" and keeps no record of
   which member supplied which field, which is exactly what E7 (un-merge)
   will need and does not have.
9. **Eval scale.** The committed fixture is 27 records and 7 true pairs.
   The gates are real but statistically weak, and there is no corpus on
   which R5's bias measurement could produce a per-class number with a
   usable interval.
10. **Connector construction is centralized.** `pipeline.build_connector`
    is an if/elif chain importing every connector, so "a new destination is
    a single module" (ADR 0002's intent) is not quite true today; E3's
    connector growth will make this worse.

## Strategic position in the portfolio

Within the 21-repo portfolio this is the flagship for the
privacy-as-invariant pattern: policy packs, fail-closed gates, provenance
chains, and committed honest evals in one place. It is also the repo with
the highest external stakes (DPG ambitions per `DPG-CONFORMANCE.md`, a
survivor-safety posture that would be actively harmful if subtly wrong) and
the strongest dependence on things a solo maintainer cannot manufacture: a
real pilot, counsel review, and screen-reader walkthroughs, all already
named as deferred in `ROADMAP.md` and `RESEARCH-ROADMAP.md`. The pattern
work here (human review gate, provenance, policy packs) is more general
than this repo, which is the basis for EXP-15 in `03-expansions.md`.

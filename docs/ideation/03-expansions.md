# Expansions

Drafted 2026-07-01. Sixteen expansion ideas in three horizons. H1 deepens
the shipped core, H2 adds adjacent capability or reaches adjacent users, H3
holds the transformative bets. Nothing here restates E1 to E10 from
`docs/RESEARCH-ROADMAP.md`; overlaps are cited and extended. Effort tiers as
in `02-large-scale-fixes.md`.

## Horizon 1 — deepen the core

### EXP-01 — The "correct" verdict: field-level correction in review

**Pitch:** Let a reviewer fix a value (a transposed date of birth, a typo'd
surname) while deciding a pair, completing the approve/correct/reject triad
the README already promises.

**Impact:** The reviewer personas (A2, A4) currently must approve a merge
containing a known-wrong value or reject a true match; either way the error
survives. Corrections are the natural byproduct of the only moment a human
looks at both records side by side.

**Shape:** Extend `review/session.py` with a corrections map (pair, field,
side, new value) persisted separately from `decisions.json` because
corrections carry field values and therefore PII; under the DV pack the
corrections file must live in the out directory with the same handling as
`resolved.csv`, and that difference must be stated in the UI. `reconcile
apply` (`cli.py`) applies corrections before re-resolving. Requires FIX-07
(lineage) so a correction is recorded as a source in the golden record.

**Effort:** L. **Risks:** widens the PII surface of the review step, which
is exactly what the current design minimized; needs a deliberate,
documented decision, and under DV possibly a reduced mode (correct only
normalization, not values). **Excellence bar:** a correction round-trips
into the golden record and the provenance payload, with a test injecting a
corrected value and asserting the old value appears nowhere post-apply.

### EXP-02 — Cluster-level review and golden-record preview

**Pitch:** Show the reviewer the record that will exist after their
decisions, not only the pairwise evidence.

**Impact:** Pairs are the unit of scoring but clusters are the unit of
harm; a reviewer approving three pairs never sees that they imply one
five-member cluster. With FIX-02, contradiction cases need a surface that
can display "these decisions conflict."

**Shape:** A cluster view in `review/render.py` and `review/session.py`:
members, the edges (auto, approved, rejected) among them, and the resulting
golden record per `decisions.golden_records`, with the survivor and fill
sources from FIX-07. Keyboard-and-no-JS constraints identical to the pair
view; extends, not replaces, it.

**Effort:** L. **Risks:** screen complexity is the enemy of the
caseworker-grade bar in `CLAUDE.md`; prototype with the R1 walkthrough
cohort. **Excellence bar:** a reviewer can answer "what will be written for
this person" before it is written, verified in a usability pass with a
non-technical tester.

### EXP-03 — Matching depth pack: nicknames, phonetics, term frequency, compound surnames

**Pitch:** Four targeted matcher improvements that attack the known error
classes without touching the no-training promise.

**Impact:** Jaro-Winkler at 0.88 (`defaults._NAME_CLOSE`) will not catch
Bill/William or Peggy/Margaret; blocking on exact normalized fields
(`defaults.blocking_rules_for`) misses transliteration variants; a match on
"Smith" counts the same as a match on a rare surname; and
`normalize_name` collapses "de la Cruz Gómez" into one token, mishandling
the two-surname convention common in the communities many target orgs
serve. These are the mitigation levers R5's measurement (which only
measures) will point at.

**Shape:** (a) a vendored, documented nickname table as an extra
comparison level in `defaults._name_comparison`; (b) one phonetic blocking
rule (Splink supports derived columns; add a metaphone key in
`normalize.py`); (c) Splink term-frequency adjustment on last_name; (d) a
surname comparison that scores agreement on either of two surname tokens.
Each behind the eval gate: the demo report must not regress and the FIX-11
corpus must show the per-class gains.

**Effort:** L overall (each piece M or smaller). **Risks:** every added
level changes m/u calibration; sequence after FIX-11 so effects are
measurable. Nickname and naming-convention tables need cultural/linguistic
SME review, not guesswork. **Excellence bar:** measured recall gain on the
transliterated and compound-surname corpus classes with no false-merge
regression, published in the eval report.

### EXP-04 — Local OCR backend for scanned intake

**Pitch:** Add a tesseract-based offline OCR path so image-only scans stop
being invisible.

**Impact:** The README's ingest claim includes scans, but
`extract/pdf.py` reads embedded text only; a scanned intake form yields an
empty page today (FIX-12 documents the claim gap; this closes it). Paper
intake is the number-one reality of the target segment (persona A1).

**Shape:** An `extract/ocr.py` backend behind the existing
`ExtractConfig.backend` switch ("pdfplumber+ocr"), invoked when a page has
no text layer; confidence from tesseract word confidences feeding the
existing `_page_confidence` gate; spans from tesseract bounding boxes into
`SourceSpan`. Runs inside the FIX-10 sandbox. Optional dependency, same
pattern as the `extract` extra in `pyproject.toml`.

**Effort:** L. **Risks:** OCR quality on bad scans will push volume into
the review queue; that is the correct fail-closed behavior but needs the
FIX-05 accounting to be visible. **Excellence bar:** a scanned fixture form
lands fields with spans, and the labeled extraction fixture (metrics
ledger target) covers OCR pages with reported precision/recall.

### EXP-05 — Local-model extraction seam

**Pitch:** A third seam implementation running a local model (for example
via Ollama) for low-confidence pages, giving DV-pack deployments a
model-assisted path with no egress.

**Impact:** Today the DV pack fuses off the only model-assisted extraction
(`extract/seam.py` returns `NoOpSeam` for dv/hipaa), so exactly the
highest-need segment gets the weakest extraction. A local model changes the
calculus: no page leaves the machine.

**Shape:** A `LocalSeam` alongside `BedrockSeam` implementing the same
protocol; a new policy dimension in `policy.py` distinguishing "no cloud
calls" from "no model at all," decided deliberately rather than by
implication. The model and data card work in R9 extends to this seam.
Whether a local model is acceptable under a given org's VAWA reading is
the org's counsel's call; the default under dv should remain off until
that analysis is written.

**Effort:** L. **Risks:** dependency weight and model management cut
against the stdlib discipline; keep it an extra. Policy semantics are the
hard part, not the code. **Excellence bar:** under `dv` with the local
seam explicitly enabled, `tests/test_no_egress.py` still proves zero
network traffic; kappa calibration (R10) runs against the local model's
confidences.

### EXP-06 — Per-source data-quality report

**Pitch:** A per-source quality section in the run output: field
completeness, normalization failure rates, consent coverage, duplicate
density.

**Impact:** Ops staff (B1) learn which intake channel produces the dirty
data, which is the actionable insight behind the reporting-hours pain the
Stanford-cited research documents. Builds directly on FIX-05's counters;
distinct from E1/E2, which shape outputs for funders rather than
diagnosing inputs.

**Shape:** Extend the FIX-05 `IngestReport` with per-source aggregates;
render in `report.py` and `out/run_report.json`. Under the DV pack, apply
the `suppression.py` small-cell rules to any per-source count, since a
source with two records leaks.

**Effort:** M. **Risks:** minimal. **Excellence bar:** an operator can name
their worst field per source from one screen; DV-pack output passes the
suppression tests.

## Horizon 2 — adjacent capabilities and audiences

### EXP-07 — Household and relationship grouping, consent-aware

**Pitch:** Emit reviewed household groupings (shared address plus surname
evidence) as a separate artifact for the CRMs that model households (NPSP
Households, CiviCRM relationships).

**Impact:** Human-services delivery is often household-scoped; both target
CRMs have household objects the current Contact-only write path ignores.
This is the most-requested shape of "more than a contact" that stays
inside the non-goal boundary (still not a system of record).

**Shape:** A post-clustering grouping step over golden records using the
existing address key from `address.py`; its own review queue section
(never auto-linked; a household suggestion is always human-confirmed); new
columns in the CRM export maps in `connectors/crm_csv.py`. Under the DV
pack, household inference must default off: shelter residents share an
address, and inferring co-residence is itself sensitive information (the
`defaults._address_comparison` docstring already recognizes this for
matching).

**Effort:** L. **Risks:** the DV interaction is the design problem; treat
the off-by-default as an invariant with a test. **Excellence bar:** a
household lands correctly in an NPSP import file; under `dv` the grouping
step provably never runs unless explicitly enabled, test-asserted.

### EXP-08 — Email ingestion (.eml and text bodies)

**Pitch:** Parse `.eml` files and plain-text bodies through the same
label-adjacent extraction as PDFs, closing the README's "email bodies"
claim.

**Impact:** Small orgs run intake through a shared inbox; exporting
messages as .eml into the watched folder is a workflow they already have.

**Shape:** stdlib `email` parsing into text, reusing
`extract/pdf.py`'s `_FIELD_PATTERNS` (factor the patterns into
`extract/base.py`); source spans become line offsets rather than bounding
boxes, which `SourceSpan` (`models.py`) needs a variant for. Route in
`pipeline._ingest_source` by `.eml`/`.txt` suffix.

**Effort:** M. **Risks:** attachments recurse into the PDF path; cap depth
inside the FIX-10 sandbox. **Excellence bar:** an .eml fixture yields a
record whose review-queue span points at the exact line, and FIX-12's
claims table flips the email row to "implemented."

### EXP-09 — Reviewer calibration with planted pairs

**Pitch:** Optionally mix a few known-answer pairs (from the synthetic
corpus) into the review queue and report reviewer agreement, reusing
`evaluate.cohen_kappa`.

**Impact:** The whole system's floor is reviewer accuracy, and nothing
measures it. E4 adds who-decided and a second reviewer; this adds how well,
which E4 does not cover. For a volunteer-run queue (persona A4) this is
the training feedback loop.

**Shape:** A `[review] calibration = N` recipe option injecting N synthetic
pairs (visibly synthetic in the data, never mixed into real output;
excluded in `reconcile apply` by construction); per-session agreement in
the review summary printed by `cli._cmd_review`. Transparency requirement:
the reviewer is told planted pairs exist, in the UI banner, or trust in
the queue is spent.

**Effort:** M. **Risks:** deception risk if disclosure is weak; ethical
framing reviewed against the transparency section of
`RESPONSIBLE-TECH-AUDITS.md`. **Excellence bar:** planted pairs can never
reach a connector (merge-blocking test), and a session report shows kappa
with the same honesty conventions as the eval report.

### EXP-10 — Destruction executor for the retention model

**Status: done (2026-07-02).** Shipped as `reconcile destroy` backed by
`src/constituent_reconciler/destruction.py`: an explicit inventory of the
PII-bearing out-directory artifacts, a required `--older-than` window with no
default (the window stays counsel-gated), per-file SHA-256 destruction
certificates appended to the provenance chain, a `--dry-run` preview, and the
forensic-erasure limitation documented in the README. Tests in
`tests/test_destruction.py` plant a sentinel value and assert it survives
nowhere under the out directory while `verify_log` still passes.

**Pitch:** A `reconcile destroy` command that executes R8's
retention/destruction model over out-directory artifacts and logs a
destruction certificate to the provenance chain.

**Impact:** R8 defines the model on paper; nothing will execute it. The HUD
comparable-database guidance cited in `RESEARCH-ROADMAP.md` expects routine
destruction of individual records once no longer needed. Artifacts with
field values (`resolved.csv`, `review_queue.csv`, CRM import files,
corrections from EXP-01) currently persist indefinitely.

**Shape:** An inventory of which artifacts carry PII (FIX-05's report
already knows what was written); `reconcile destroy --out out --older-than
<policy>` deletes them and appends a `destroyed` provenance entry naming
artifact hashes, so the chain proves destruction without retaining
content. Honest limitation stated in the docs: file deletion is not
forensic erasure on journaling filesystems.

**Effort:** M. **Risks:** the retention windows are counsel-gated (R8's
gate applies here doubly); ship the mechanism with no default window.
**Excellence bar:** after destroy, no field value remains under the out
directory (test greps planted sentinel values) and `reconcile verify`
still passes.

### EXP-11 — Board-and-funder narrative artifact

**Pitch:** Generate a one-page, plain-language run summary (what came in,
what merged, what was withheld and why, zero PII) an executive director can
hand to a board or funder.

**Impact:** Persona C3's adoption blocker is defensibility, and E2's
CoC-shaped export serves the CoC, not the board. The ingredients (run
summary in `report.py`, `aggregate_summary.json` from `suppression.py`,
the FIX-08 manifest) exist; nothing composes them for a non-operator
audience.

**Shape:** `reconcile report --format narrative` rendering Markdown/HTML
from the run report and aggregate summary, suppression rules applied,
with the standing reference-implementation caveat inlined. EN and ES
copies per the portfolio parity standard.

**Effort:** S to M. **Risks:** low; must resist marketing language per the
`CLAUDE.md` writing style rules. **Excellence bar:** a reader with no
context can answer "did anything leave the machine, and under whose
consent" from the page alone.

### EXP-12 — Pluggable matcher seam

**Pitch:** Put `matching.score_pairs` behind a small backend protocol so
Splink is an implementation, not a hard dependency.

**Impact:** Splink 4 plus DuckDB plus pandas is the package's entire heavy
tail (`pyproject.toml` pins `splink>=4.0,<5`); a Splink 5 with breaking
changes, or an install-constrained environment, currently has no fallback.
ADR 0001 chose Splink deliberately; this keeps the choice while containing
it, the same move `connectors/base.py` made for destinations and
`address.py` made for backends.

**Shape:** A `MatcherBackend` protocol (records, fields, prior in; scored
tuples out) in a new `matching/base.py`; the Splink implementation moves
behind it; a pure-Python Fellegi-Sunter fallback is explicitly out of
scope (`CLAUDE.md` forbids reimplementing linkage) but a dedupe-library
backend becomes writable by a contributor. Conformance tests mirror
FIX-09's connector kit: identical banding on the demo fixture.

**Effort:** M. **Risks:** premature abstraction if no second backend ever
lands; justified mainly as churn insurance for the one heavy dependency.
**Excellence bar:** the demo eval is bit-identical through the seam, and
`pipeline.py` imports no Splink symbol.

### EXP-13 — Air-gapped appliance bundle

**Pitch:** A signed, reproducible offline install bundle (wheelhouse plus
saved Docker image plus checksums plus docs) for orgs whose IT forbids
outbound internet on the machine that holds client data.

**Impact:** The DV posture argues client data belongs on an offline
machine, but `make install` needs PyPI and `make docker` needs a registry.
The audience whose policy is strictest (persona C1, and any VSP taking the
comparable-database posture seriously) has the worst install story.

**Shape:** A release artifact built in CI: `pip download` wheelhouse for
the pinned set, `docker save` tarball, SHA256SUMS, install-offline doc.
Depends on R3's supply-chain work (signing) and extends it with the
distribution shape R3 does not name. Verify the Docker image build in CI
at the same time (the CHANGELOG 0.6 note admits it is not built in CI).

**Effort:** M. **Risks:** artifact size; platform matrix kept minimal
(linux/amd64 plus arm64). **Excellence bar:** a machine with no network
installs and passes `make verify` from the bundle alone, checksums and
signatures verified.

## Horizon 3 — transformative bets

### EXP-14 — Cross-organization linkage study (privacy-preserving, heavily gated)

**Pitch:** Investigate whether coalitions of non-VSP human-services orgs
(a food bank and a housing program serving the same county) could
reconcile shared clients without pooling raw PII, using
privacy-preserving record linkage in a clean-room pattern.

**Impact:** The multi-program constituent (persona D2) is split across
organizations, not only within one; no open tool serves that shape at
small-org scale. This is the largest possible expansion of the mission.

**Shape (study first, code later):** a written analysis before any code.
The honest complication is already in this repo:
`RESPONSIBLE-TECH-AUDITS.md` quotes VAWA barring disclosure "regardless of
whether the information has been encoded, encrypted, hashed, or otherwise
protected," which reads as ruling out Bloom-filter PPRL for DV data
entirely. So the study's first output is a bright line: DV-pack data is
out, full stop; the open question is consent-based linkage for non-VSP
programs. Requires counsel and a real coalition partner before a line of
code.

**Effort:** XL. **Risks:** the highest-stakes item in this folder; a
plausible-but-wrong design here is worse than nothing, per the standing
warning in `RESEARCH-ROADMAP.md`. **Excellence bar:** a published analysis
that states what is legally and technically defensible, with the DV
exclusion explicit, reviewed by counsel and an NNEDV-informed SME, before
any prototype.

### EXP-15 — The human-gate kernel as a reusable library

**Pitch:** Extract the pattern this repo proved (fail-closed banding,
review session with plain-language rationale, append-only provenance,
declarative policy packs) into a small library other civic tools and
portfolio repos can adopt.

**Impact:** The portfolio thesis is responsible-tech patterns, and this
repo holds the most complete implementation: `decisions.band_pairs`,
`review/session.py`, `provenance.py`, and `policy.py` are already nearly
dependency-free. Every future civic pipeline with an uncertain-decision
step (eligibility screening, benefits triage) needs exactly this gate.

**Shape:** Carve the four modules into a package with this repo as first
consumer; the review session generalizes from "pair of records" to
"decision with evidence." The extraction is only worth it after a second
concrete consumer exists inside the portfolio; premature extraction is the
known failure mode.

**Effort:** XL. **Risks:** API generalization before a second user is
guesswork; sequence behind a real second use case. **Excellence bar:** two
shipping consumers, this repo's tests unchanged in intent, and the
library's own docs carrying the same invariants-as-tests posture.

### EXP-16 — Public benchmark for nonprofit record reconciliation

**Pitch:** Publish the FIX-11 corpus generator and a leaderboard-style
harness as a community benchmark, inviting Splink, dedupe, and Zingg
configurations to report against the same asymmetric metrics.

**Impact:** The eval philosophy here (gated false-merge rate, Wilson
intervals, per-name-class breakdowns) is more rigorous than what the
surrounding ecosystem publishes; making it shared infrastructure positions
the project as the standard-setter the DPG story (E10) wants, and buys
review of the defaults in `defaults.py` from the people best able to break
them.

**Shape:** A separate repo or a `benchmark/` extra: generator, scoring CLI
(reusing `evaluate.py`), submission format, a results table seeded with
this project's own numbers, and the honesty rules (no cherry-picking,
CIs mandatory) as submission requirements.

**Effort:** L. **Risks:** community benchmarks demand maintenance; scope
the v1 to "reproducible harness plus our own results," not a hosted
leaderboard. **Excellence bar:** one external configuration reproduced by
a third party from the published harness alone.

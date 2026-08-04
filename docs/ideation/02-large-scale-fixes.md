# Large-scale fixes

> **Closeout (2026-07-22):** FIX-01 through FIX-12 are implemented and covered
> by the repository's tests, reports, or dated claims audit. This file is the
> historical rationale and acceptance-criteria record. See
> `docs/ROADMAP-CLOSEOUT.md` for the consolidated ledger.

Drafted 2026-07-01. Twelve structural fixes grounded in the v0.7 source.
None of these restates an item in `docs/ROADMAP.md` or
`docs/RESEARCH-ROADMAP.md`; where a fix enables or extends an existing item
(R1 to R11, E1 to E10) the relationship is stated. Effort tiers: S (about an
afternoon), M (days), L (a week or more), XL (multiple weeks).

---

## FIX-01 — Web-boundary hardening for the review server

**Pitch:** Add origin, Host, and per-session token checks to `reconcile
review` so a hostile web page cannot forge verdicts or read pairs over the
loopback interface.

**Why it matters:** `review/server.py` binds loopback and the DV pack
refuses other binds, but `handle_post` accepts any POST and `http.server`
never validates the Host header. Cross-site request forgery against
`http://127.0.0.1:8765/pair/N` from any page the reviewer has open is
straightforward, and DNS rebinding is a known technique for reading
localhost HTTP responses, which here contain constituent field values. For
the DV pack the README claims the review surface "cannot become an egress
path"; today that claim rests on the bind address alone. The people harmed
by failure are the data subjects (personas D1, D2), which makes this the
most serious observed gap.

**Shape of the work:** In `review/server.py`: generate a random session
token at startup, embed it in every form `render.py` emits, reject POSTs
without it; reject requests whose Host header is not the bound
host and port; reject POSTs with an Origin header that is not the server's
own origin; add tests alongside the existing pure-handler tests in
`tests/test_review.py` (the split between `handle_get`/`handle_post` and
sockets makes this cheap to test). Document the residual risk in
`RESPONSIBLE-TECH-AUDITS.md` security section, which R4's threat model can
then reference.

**Effort:** M. **Risks/dependencies:** none upstream; R4 (threat model)
should cite this as a resolved finding rather than an open one.

**Excellent looks like:** a test that a POST without the token is refused, a
test that a GET with a foreign Host header is refused, and a one-paragraph
threat note; axe/accessibility behavior (R1) unchanged.

---

## FIX-02 — Human rejections as cannot-link constraints

**Status: done (2026-07-12).** A human-rejected pair is now a binding
cannot-link. Any transitive AUTO component containing that pair is refused and
all of its automatic edges return to review with an explicit routing note; no
golden record can silently reunite the rejected endpoints.

**Pitch:** Make a reviewer's "reject" binding on the final clustering, not
only on the single edge they saw.

**Why it matters:** `decisions.build_clusters` unions AUTO edges only. If a
reviewer rejects pair (A, C) but pairs (A, B) and (B, C) are AUTO, the
transitive closure still puts A and C in one cluster and one golden record.
The explicit human decision is silently overridden, which contradicts the
project's first principle ("No silent auto-merge, ever," `docs/ROADMAP.md`
architecture note). This failure is invisible today because nothing checks
clusters against DROP-banded pairs.

**Shape of the work:** Treat `force_drop` pairs (from
`pipeline._apply_overrides`) as cannot-link constraints. Minimum viable:
after clustering, scan each cluster for any member pair present in the
rejected set and, on a hit, refuse to auto-merge that cluster, routing all
of its edges to review with a rationale ("a reviewer separated two of these
records"). A fuller version implements constrained clustering (split the
cluster along the min-cut that respects the constraint). Touches
`decisions.py`, `pipeline.run`, and the review rationale in
`review/session.py`. Add planted fixtures: a triangle with one rejected
edge.

**Effort:** M for the refuse-and-route version, L for constrained
splitting. **Risks/dependencies:** interacts with EXP-02 (cluster-level
review); the refuse-and-route version should land first since E7 (un-merge)
will assume rejections are durable.

**Excellent looks like:** a merge-blocking test asserting no golden record
ever contains a pair the decisions file rejects, and an eval fixture
exercising the transitive case.

---

## FIX-03 — Content-derived, collision-safe record identity

**Pitch:** Replace positional generated ids with stable ids derived from
source content, and fail loudly on collisions.

**Why it matters:** `pipeline.read_records` mints `E0001`-style ids from row
order. Decisions files (`decisions.json`) and provenance entries key on
these ids, so inserting a row in a CSV between `reconcile review` and
`reconcile apply` silently re-binds every later verdict to a different
person. Separately, duplicate ids (a user-supplied `id_column` colliding
across sources, or with generated ids) are silently swallowed by the dict
comprehension in `pipeline.run`, losing records. Both are correctness holes
in the exact artifact chain the tool asks people to trust.

**Shape of the work:** Derive the generated id from a short BLAKE2b digest
of (source name, mapped raw values) with a disambiguating counter for exact
duplicate rows; namespace user-supplied ids by source; raise on any
collision after namespacing. Version the decisions-file shape (it is a
declared surface, `schema.py` REPORT_SCHEMA_VERSION) and have
`reconcile apply` warn when a decision references an id absent from the
current run, instead of the current silent ignore in
`review/session._load_existing`.

**Effort:** M. **Risks/dependencies:** changes ids embedded in provenance
logs and CRM external-id upserts; needs a migration note per ADR 0006's
stability contract. Do before any pilot (E8), because pilots edit CSVs
mid-review.

**Excellent looks like:** a test that inserting a row between run and apply
leaves every verdict attached to the same people; a test that duplicate ids
raise; unchanged demo eval.

**Status:** Done (2026-07-11). Content-derived BLAKE2b ids for generated
records across CSV/PDF/text readers, source-namespaced user ids,
`pipeline.DuplicateIdError` on residual collisions, versioned decisions file
(`decisions_schema`), and a stderr warning on stale decisions. Migration note
in CHANGELOG.md per ADR 0006.

---

## FIX-04 — Fail-closed recipe validation

**Pitch:** Reject unknown recipe keys and sections, and add a
`reconcile validate` command.

**Why it matters:** `config.load_recipe` reads with `.get(..., default)`
and ignores everything it does not recognize. A recipe with
`[thresholds] auto_threshold = 0.99` (wrong key) silently runs at 0.97; a
misspelled `[consnet]` section silently disables consent column mapping.
For every other surface a typo raises (`policy_for`, `normalize_address`);
the recipe is the one fail-open input, and it is the one non-technical
operators edit (personas B1, A4).

**Shape of the work:** In `config.py`, validate section names and keys
against the declared schema (this is what CONFIG_SCHEMA_VERSION in
`schema.py` nominally versions), raising with the unknown name and the
nearest valid key. Add `reconcile validate --config recipe.toml` to
`cli.py` that loads, resolves paths, checks files exist, checks the mapping
covers first/last name, and reports the active policy switches without
running anything. The adoption kit (E8, `docs/ADOPTION-KIT.md`) gets a
"validate before you run" step.

**Effort:** S to M. **Risks/dependencies:** none; strictness could break
recipes that carry comments-as-keys, which is acceptable pre-1.0 with a
CHANGELOG note.

**Excellent looks like:** every fixture recipe under `examples/` passes;
a test that one misspelled threshold key fails with a message naming it.

---

## FIX-05 — Ingest accounting: every row, page, and file answered for

**Pitch:** Make the run report state what was read, what was skipped, and
why, so nothing disappears silently between input and output.

**Why it matters:** `_ingest_source` skips unknown extensions in a folder
with no trace; `read_pdf_records` drops pages that yield no name;
`normalize_dob` turns unparseable dates into "" (no evidence) without
telling anyone. An operator reconciling a reporting cycle (persona B1)
cannot currently answer "did the tool see all 300 intakes?" The ethos is
"never silent-pass"; ingestion is where the code still silently passes.

**Shape of the work:** Introduce an `IngestReport` (files seen, routed,
skipped-with-reason; pages extracted, dropped; per-field normalization
failure counts) threaded from `_ingest_source` and `normalize_record` into
`RunResult` (`models.py`) and rendered in `report.render_run_summary` and a
machine-readable `out/run_report.json`. Failed-date and failed-address
counts also give R5 (bias measurement) its denominator per source.

**Effort:** M. **Risks/dependencies:** touches the RunResult shape;
coordinate with FIX-08 (run manifest) so the two artifacts are one file.

**Excellent looks like:** for any input folder, files-in equals
records-plus-skips with reasons, asserted by a test with a mixed folder
fixture (CSV, PDF, a .docx, an empty PDF page).

---

## FIX-06 — Consent as a lifecycle, not a token

**Pitch:** Model consent as a dated, time-limited, revocable object with
scope, replacing the `CONSENT_GRANTED` string-set check.

**Why it matters:** `models.CONSENT_GRANTED` is membership in {"granted",
"active", "yes", "true"}. VAWA requires consent to be informed, written,
and "reasonably time-limited" (34 U.S.C. § 12291(b)(2)(B)(ii), already
quoted in `policy.py`), and `CLAUDE.md` promised expiry and revocation in
`consent.py`. Today a consent granted in 2019 reads as granted forever, and
there is no way to record that consent covers one destination (the CRM) but
not another (a funder export). R8 defines retention and destruction; this
is the complementary consent-side model neither R8 nor any other item
covers.

**Shape of the work:** Extend the recipe (`config.py`) to map optional
consent-date and consent-expiry columns; extend `Record` and `GoldenRecord`
(`models.py`) with a small `Consent` value (status, granted-on, expires-on,
scope); make `consent.partition_by_consent` fail closed on expired or
future-dated consent; record the withhold reason ("expired" vs "absent" vs
"revoked") in `_write_withheld` (`pipeline.py`), still ids-and-reasons
only. The default expiry window must not be invented: ship it unset,
require the recipe to state one, and document that counsel sets the number.

**Effort:** L. **Risks/dependencies:** the semantics (what counts as
reasonably time-limited, whether scope granularity is per-destination) are
a counsel-gated design decision; build the mechanism, defer the defaults.
Blocks nothing but strengthens E2 and the DV posture materially.

**Excellent looks like:** a merge-blocking test that an expired consent is
withheld; withheld.csv distinguishes reasons; no default expiry value
asserted anywhere in code.

---

## FIX-07 — Field-level lineage and explicit survivorship policy

**Pitch:** Record, per golden-record field, which member supplied it, and
make the fill order an explicit, configurable policy.

**Why it matters:** `decisions.golden_records` fills survivor blanks "by
member id order," an accident of id assignment, and discards the
information of where each value came from. Without lineage, E7 (un-merge)
cannot restore pre-merge state, the provenance log (`provenance.py`) can
say what was written but not why each field held that value, and a reviewer
correcting a golden record (EXP-01) has nothing to correct against.

**Shape of the work:** Add `field_sources: dict[str, str]` to
`GoldenRecord` (`models.py`); populate it in `golden_records`
(`decisions.py`); write it into the provenance payload hash in
`pipeline.export` (ids, not values, under DV minimization); surface it in
the review UI's golden-record preview once EXP-02 exists. Make the fill
strategy a named policy ("survivor-then-lowest-id" today;
"most-recent-wins" once a record date exists) declared in the recipe.

**Effort:** M. **Risks/dependencies:** REPORT_SCHEMA_VERSION bump
(`schema.py`); prerequisite for E7 and EXP-01.

**Excellent looks like:** for every golden record, every non-empty field
names a member that actually carries that value, asserted by a
property-style test.

---

## FIX-08 — Run manifest for reproducibility

**Pitch:** Stamp every run with a manifest (recipe hash, input file hashes,
package and Splink versions, thresholds, policy pack) and chain it into the
provenance log.

**Why it matters:** The provenance chain proves what was written but not
what produced it. Two runs of the same out directory are currently
indistinguishable from one; an auditor (personas C2, E1) cannot verify that
a provenance log corresponds to a given input batch and configuration. The
portfolio standard is reproducibility and verifiability; this is the
cheapest structural step toward it, and it is not covered by R2 (which
anchors timestamps, a different property).

**Shape of the work:** New `out/run_manifest.json` written by
`pipeline.export`: BLAKE2b of the recipe file and of each input file
(hashes only, no PII), `__version__`, Splink version, resolved thresholds,
pack name, schema versions from `schema.py`. Append a `run-start` entry
type to `ProvenanceLog` carrying the manifest hash so writes are chained to
their configuration. `reconcile verify` learns to report which manifest a
chain segment belongs to.

**Effort:** S to M. **Risks/dependencies:** provenance entry shape is a
declared surface; bump REPORT_SCHEMA_VERSION with a migration note. Pairs
naturally with FIX-05's run report.

**Excellent looks like:** given an out directory, `reconcile verify` can
state "these 21 writes were produced by recipe hash X over inputs Y, Z" and
detect a swapped input file.

---

## FIX-09 — Connector registry and conformance kit

**Status:** Done (2026-07-02). `CONNECTOR_REGISTRY` in
`connectors/__init__.py` maps recipe names to factories;
`pipeline.build_connector` is a lookup plus the policy check.
`tests/test_connector_conformance.py` parametrizes the contract (dry-run
purity, action vocabulary, `is_local` honesty, external-id round-trip,
unknown-name refusal) over every registered connector.
CONNECTOR_INTERFACE_VERSION stays 1; the Protocol is unchanged.

**Pitch:** Replace the if/elif chain in `pipeline.build_connector` with a
registry, and ship a conformance test suite any connector must pass.

**Why it matters:** ADR 0002's promise is that a new destination is one
module, but `build_connector` imports and constructs every connector
inline, so each addition edits the orchestrator. E3 plans at least four
more connectors (Apricot, Airtable, Sheets, webhook); without a registry
and a shared behavioral contract (dry-run purity, `is_local` honesty,
WriteResult semantics, idempotent upsert), connector quality will drift
exactly where the DV guarantee depends on it (`is_local` is
load-bearing for the no-egress invariant).

**Shape of the work:** A registration mapping in `connectors/__init__.py`
(name to factory taking `OutputConfig` and out_dir), with
`build_connector` reduced to lookup plus the existing policy check. A
`tests/connector_conformance.py` parametrized suite: dry-run writes
nothing to disk or transport; every WriteResult action is in the known
vocabulary; `is_local=False` connectors never touch the filesystem;
external-id round-trips. Run it against all four existing connectors first,
which will document current behavior differences (for example
`CrmCsvConnector` reports "written" per record while `CsvConnector` should
be checked for the same).

**Effort:** M. **Risks/dependencies:** none; prerequisite worth landing
before E3 to keep each new connector honest. CONNECTOR_INTERFACE_VERSION
stays 1 if the Protocol is unchanged.

**Excellent looks like:** adding a connector touches one new module plus a
registry line, and the conformance suite passes unmodified.

---

## FIX-10 — Sandboxed, resource-limited extraction

**Pitch:** Run the untrusted-PDF parse in a constrained subprocess with
timeouts and size caps, rather than in-process.

**Why it matters:** `extract/pdf.py` opens attacker-supplied PDFs with
pdfplumber in the main process. `RESPONSIBLE-TECH-AUDITS.md` names
untrusted input as the primary threat surface and claims a "hardened path"
that does not exist yet in code. R4 commits the threat model document; this
fix is the mitigation the document will inevitably call for, scoped now so
the two land coherently. A crafted PDF that hangs or exhausts memory takes
down the run today (denial of service against a reporting deadline), and a
parser exploit would run with access to the whole constituent file.

**Shape of the work:** A `SandboxedExtractor` wrapping
`PdfplumberExtractor`: spawn via `multiprocessing` (spawn context), apply
`resource.setrlimit` caps (CPU seconds, address space) in the child, kill
on wall-clock timeout, cap input file size before parsing, and treat any
child failure as a low-confidence page routed to review, fail-closed.
Route `read_pdf_records` (`pipeline.py`) through it. Document the
non-goals honestly: this is containment, not a syscall sandbox; the Docker
path (`Dockerfile`) already provides a stronger boundary for those who use
it.

**Effort:** M to L (platform quirks: `resource` is POSIX-only; degrade
gracefully on Windows with a documented note). **Risks/dependencies:**
R4 should be written against this design; interacts with EXP-04 (OCR),
which must run inside the same boundary.

**Excellent looks like:** a fixture PDF that sleeps or balloons is killed
within the limit and appears in the FIX-05 ingest report as
"extraction failed: resource limit," with the run completing.

---

## FIX-11 — Synthetic corpus generator and statistically real eval

**Pitch:** Build a seeded generator producing 10^3 to 10^5 record corpora
with configurable error models, so the gated metrics get tight intervals
and the bias and performance questions get denominators.

**Why it matters:** The committed eval (`eval/report.md`) runs on 27
records and 7 true pairs; the false-merge gate passes at 0/6 with a Wilson
CI of [0%, 39%], which the report honestly displays. That is a real gate
but weak evidence. R5 (bias by name and address class) cannot produce
usable per-class rates from 27 records, and no performance envelope exists
for the scale a mid-sized org has (tens of thousands of rows through
`matching.score_pairs` and DuckDB). The metrics ledger's extraction
fixture ("not landed") needs generated labeled PDFs too.

**Shape of the work:** A new `tools/corpusgen/` (or `eval/generate.py`)
that emits seeded synthetic populations with planted duplicate structure:
name error channels (typo, nickname, transliteration variants, hyphenation,
compound surnames), date format drift, address variants keyed to the
`address.py` tables, per-class labels for R5, and ground-truth clusters in
the existing `ground_truth.json` shape. Zero real PII, matching the
fixture policy in `USER-RESEARCH.md`. Wire a second, larger eval into
`make eval` output as an additional committed report (keep the 27-record
demo as the fast CI gate; run the large one on release). Record wall-clock
and peak memory alongside, which gives E9 (incremental re-resolution) its
before number.

**Effort:** L. **Risks/dependencies:** synthetic error models embed
assumptions; label them as such and calibrate against pilot data when E8
produces some (that calibration is real-data-gated). Feeds R5, R10
(kappa needs labeled extraction data), E9, and EXP-16.

**Excellent looks like:** a committed large-corpus report where the
false-merge CI upper bound is below 1%, per-name-class recall is reported
with intervals, and a documented records-per-minute figure at 50k records.

---

## FIX-12 — Close the spec-versus-code gaps, in whichever direction is true

**Pitch:** Audit every capability claim in README and `CLAUDE.md` against
the code and either implement the small ones or correct the text.

**Why it matters:** Observed drift: README promises "approve, correct, or
reject" but `review/session.py` has two verdicts; README and `CLAUDE.md`
name email bodies and scans as ingest sources that `_ingest_source`
(`pipeline.py`) and `extract/pdf.py` do not handle; `CLAUDE.md`'s module
map lists `ingest.py`, `gate.py`, `resolve.py`, `extract/deterministic.py`
which do not exist (the code's actual layout is arguably better, but the
spec is the stated source of truth). For a project whose differentiator is
honesty, unearned claims in the front door are the cheapest credibility to
lose with the exact technical audience `CLAUDE.md` names.

**Shape of the work:** A claims audit table (claim, file, status); fix the
text for scans and email bodies now ("planned," pointing at EXP-04 and
EXP-08); update `CLAUDE.md`'s architecture map to the as-built layout;
either land EXP-01 (the correct verdict) promptly or soften the README
sentence until it lands. Add a lightweight release-checklist item: grep
capability claims on release.

**Effort:** S for the text; the implementation halves are EXP-01, EXP-04,
EXP-08. **Risks/dependencies:** none.

**Excellent looks like:** no sentence in README describes a surface the
code lacks; the claims table is committed and dated.

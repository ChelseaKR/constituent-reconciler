# constituent-reconciler

An offline-first pipeline that turns a stack of intake PDFs and a spreadsheet
into verified, deduplicated constituent records and writes them into the case
system a nonprofit already runs. A non-technical reviewer approves or rejects
every uncertain match before anything is written. Nothing merges silently.

> **Status: Beta (v0.7), early but working.** The pipeline runs and is tested:
> CSV or PDF in, deduplicated records out, with source-span pointers in the
> review queue, CASS-style address normalization, a committed eval
> ([eval/report.md](eval/report.md)), a local WCAG 2.2 AA web review UI, CiviCRM
> and Salesforce write-back (live API push and offline import-ready export files),
> a tamper-evident provenance log, a DV privacy pack that enforces VAWA/FVPSA
> confidentiality as merge-blocking tests, and one-command Docker self-host. A
> full accessibility audit and supply-chain hardening remain before the 1.0
> stability tag, which is gated on real-organization adoption. Track progress in
> [docs/ROADMAP.md](docs/ROADMAP.md); the build is specified in
> [CLAUDE.md](CLAUDE.md).

## The problem

Most human-services nonprofits run intake on paper or PDF and keep constituent
records in a CRM or case-management system (CiviCRM, Salesforce, Apricot,
Airtable, a shared spreadsheet). Getting the first into the second is manual
re-typing, and the records that result are full of near-duplicates: the same
person entered three ways across two programs and four reporting cycles.

Two facts from the research that motivated this project:

* A Stanford legal-aid intake prototype reached about 90% agreement with human
  eligibility decisions and still stalled at deployment, because it was
  ["designed in a standalone environment without functional integration"](https://justiceinnovation.law.stanford.edu/legal-aid-intake-screening-ai/)
  with the case system. The AI could read the form. It could not put a record
  where staff needed it.
* The same work documents 40 to 80 staff hours per reporting cycle spent
  reconciling disconnected tools and name mismatches.

Each stage of the fix already exists as a library. Document parsers stop at
text. Record linkers ([Splink](https://github.com/moj-analytical-services/splink),
[dedupe](https://github.com/dedupeio/dedupe),
[Zingg](https://github.com/zinggAI/zingg)) need labeled training pairs,
blocking rules, and match-weight tuning that a one-or-two-person IT shop does
not have time to learn, and they expect already-structured input.
[libpostal](https://github.com/openvenues/libpostal) parses an address but
does not validate it. The commercial suites that do chain these steps omit
document extraction, ship analyst-grade review screens rather than
caseworker-grade ones, and are cloud-hosted and enterprise-priced.

No open-source tool chains extraction, normalization, probabilistic matching,
human review, and write-back into a nonprofit's existing system. That chain,
with a review queue a volunteer can run, is what this project builds.

## What it does

The pipeline runs as a sequence of logged, deterministic-by-default steps:

1. **Ingest** a folder of CSVs and PDFs, digitally created or scanned.
   Image-only scanned pages run through a local Tesseract OCR backend
   (`[extract] backend = "pdfplumber+ocr"`, the optional `ocr` extra) so a
   paper intake form yields fields instead of an empty page; email bodies are
   planned and the roadmap tracks it.
2. **Extract** field and value pairs with a source-span pointer and a
   confidence score. Extraction runs offline by default; an optional Bedrock
   (Claude) seam handles only low-confidence pages, and only when the active
   policy allows it.
3. **Normalize** names and dates, and standardize addresses with a
   deterministic CASS-style ruleset (libpostal optional).
4. **Resolve** each candidate against existing constituents using a pre-tuned
   probabilistic matcher with sane defaults, so the operator does not have to
   supply labeled pairs. The output is match, non-match, or possible-match.
5. **Review.** Anything below the auto-merge threshold and any possible-match
   routes to a review queue. A non-technical reviewer sees the source span
   beside the candidate duplicate and chooses approve, correct, or reject. A
   correction fixes one field value while approving the pair. The confidence gate is
   fail-closed: when in doubt, a human looks.
6. **Write** only approved and consented records, through a connector for the
   target system (CSV, CiviCRM, Salesforce, and a generic webhook today;
   Apricot, Airtable, and Sheets designed but not yet built -- see
   `docs/connectors/`).
7. **Log** every write to an append-only provenance record with content
   hashing (BLAKE2b) and an RFC 3161 timestamp, so an org can show what was
   written, when, and under which consent.

## The privacy mode

Victim-service providers operate under VAWA and FVPSA confidentiality rules.
Client information may not be entered into shared databases
["regardless of whether the information has been encoded, encrypted, hashed, or otherwise protected"](https://www.parasolcooperative.org/post/navigating-ai-in-victim-services-a-response-to-nnedv-s-vital-guidance).
That makes a cloud service a structural non-starter for this segment, not a
preference.

The `dv` policy pack is the answer. Set `pack = "dv"` in the recipe, use the
bundled `recipe-dv.toml`, or pass `--policy-pack dv` to apply it to any recipe.
The pack enforces four invariants, each a merge-blocking test:

* **Consent required.** The export refuses, fail-closed, to emit any record whose
  consent is not granted; withheld records are recorded by id and reason only,
  never with field values.
* **No cloud egress.** The optional cloud extraction seam is fused off at
  construction, so no page or field value can be sent to a remote model.
* **Local write targets only.** A non-local target (such as CiviCRM over the
  network) is refused before any write, so client records stay on the machine —
  the comparable-database posture HUD requires of victim-service providers.
* **Aggregate, suppressed sharing.** The pack emits an `aggregate_summary.json`
  of non-identifying counts with small cells suppressed (counts of 1-10, modeled
  on the U.S. CMS Cell Size Suppression Policy, with complementary suppression
  and true zeros preserved). It is the only artifact the pack treats as
  shareable.

The invariants are grounded in primary VAWA, FVPSA, and CMS sources, with the
citations and the honest scope of each claim in
[docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) and
[docs/decisions/0005-dv-policy-pack.md](docs/decisions/0005-dv-policy-pack.md).
This is a reference implementation, not legal advice. An organization adopting it
needs its own review against its own obligations.

### The comparable-database export profile

A provider that keeps clients out of HMIS still owes its funders CoC-shaped
aggregate reporting from its comparable database. One command emits exactly
that and nothing else — no CRM write, no resolved records on disk:

```sh
reconcile export-comparable --config examples/intake-demo/recipe-dv.toml --out out-dv
```

It writes `comparable_report.json` (profile `coc-comparable`): a report-period
label, a generated-at timestamp, the suppression threshold applied, and
suppressed category counts. Every breakdown passes through the same CMS-style
small-cell suppression as `aggregate_summary.json` (counts of 1-10 suppressed,
complementary suppression within each breakdown, true zeros preserved), and no
record id, member list, or field value appears in the file. A recipe can opt
into extra breakdowns over non-identifying categorical fields, and into writing
the report during a normal `reconcile run`, with a `[comparable]` section
(`export`, `breakdown_fields`, `period`); the identifying canonical fields
(name, DOB, email, phone, address) are refused as breakdown fields,
fail-closed, at recipe load.

## Usage

Install (Python 3.12+):

```sh
make install
```

For PDF extraction, install the optional extract extra:

```sh
pip install -e ".[extract]"
```

(Not yet published to PyPI — `pip install` above is a local editable install, not
a registry install. See [docs/ROADMAP.md](docs/ROADMAP.md) for the Trusted
Publishing plan.)

Before pointing the tool at your own data, check a recipe's shape without
resolving anything:

```sh
reconcile validate --config recipe.toml
```

An unknown section or a misspelled key (a typo'd `[consnet]`, an `auto_threshold`
that should be `auto`) is rejected by name instead of silently running at a
default; `reconcile validate` also checks that the input files it points at
exist and prints the active policy pack and switches.

Run the bundled demo, which resolves an incoming intake batch against an existing
record set:

```sh
reconcile run --config examples/intake-demo/recipe.toml --out out
```

```text
records read:        27
candidate pairs:     8
auto-merged pairs:   6
pairs to review:     2
resolved records:    21 (6 formed by merging)
```

It writes `out/resolved.csv` (the deduplicated records) and
`out/review_queue.csv` (the uncertain pairs, with the two source values side by
side for a human to approve or reject). Review the uncertain pairs in a
browser (see below) or edit the CSV by hand, then carry decisions back in with
`reconcile apply --decisions decisions.json`, which treats approved pairs as
merges and re-resolves.

### Reviewing matches in the browser

`reconcile review` opens a local web queue over the uncertain pairs. A reviewer
steps through each candidate, sees the two records side by side with their
source spans, and approves, corrects, or rejects it, with no jargon and no
spreadsheet:

```sh
reconcile review --config examples/intake-demo/recipe.toml --reviewer "your name" --out out
```

It runs the pipeline, starts a server on `http://127.0.0.1:8765/`, and opens a
browser. Each decision is saved as you go to `out/decisions.json`, attributed to
the `--reviewer` name with a timestamp, so you can stop and resume and so who
decided each pair is answerable later. When you are done, apply the decisions:

```sh
reconcile apply --config examples/intake-demo/recipe.toml --decisions out/decisions.json --out out
```

With `--require-second-reviewer` (on by default under the `dv` policy pack) a
merge takes effect only after two different reviewers approve the same pair: the
first approval is held as awaiting a second reviewer, a second reviewer resumes
the same decisions file under their own `--reviewer` name to confirm or reject,
and `reconcile apply` refuses a file that still holds half-approved pairs. Any
rejection keeps the records separate immediately.

A correction replaces one field value before normalization and approves the
pair. It is attributed to the correcting reviewer and stored in
`out/corrections.json`, separate from the PII-free `decisions.json`. In
two-person mode, making a correction invalidates earlier verdicts; a later,
distinct reviewer sees the corrected value and must approve it before the merge
can be applied. `corrections.json` contains client data and needs the same local
retention and destruction handling as `resolved.csv`.

The server is offline by construction: it binds the loopback interface only,
loads no external asset, and keeps the decisions file free of field values (it
carries record ids, verdicts, reviewer names, and timestamps only). A correction
is the explicit exception described above and is isolated in `corrections.json`. Under the
`dv` policy pack it refuses any non-loopback bind, fail-closed, so the review
surface cannot become an egress path for client information. The pages are built for WCAG 2.2 AA: a real
comparison table, status shown by text and not colour alone, and decision buttons
that work with the keyboard and with no JavaScript (`A` approve, `C` correct,
`R` reject, `J`
and `K` to move between pairs). Pass `--no-browser` to skip opening a window, or
`--port 0` to bind a free port.

To measure how well a reviewer's verdicts track ground truth, a recipe may plant
known-answer calibration pairs in the queue:

```toml
[review]
calibration = 3
```

The queue then mixes in three synthetic pairs whose correct answer is known,
generated deterministically from obviously fake values (`Calibration Sample`
names, `.invalid` email domains) and never from real data. Disclosure is part of
the design: every page carries a banner saying planted pairs are present, though
the individual pairs are not pointed out. Decisions on planted pairs are never
written to the decisions file, so `reconcile apply` cannot merge a synthetic
record into real ones by construction. When the review server stops, the CLI
reports how many planted pairs were decided in agreement with the known answers,
with Cohen's kappa once at least two are decided.

Score a run against known answers:

```sh
reconcile eval --config examples/intake-demo/recipe.toml \
  --truth examples/intake-demo/ground_truth.json
```

The committed result is in [eval/report.md](eval/report.md): on the demo
fixtures the false-merge rate is 0%, and every true duplicate is surfaced to a
human at the auto or review level. The gated metric is the false-merge rate
because a wrong merge is the expensive, sometimes irreversible error; a missed
match only leaves a duplicate.

### A one-page summary for a board or funder

`reconcile report` renders the artifacts of a completed run as a one-page,
plain-language Markdown summary an executive director can hand to a board:
what came in, what merged automatically, what a person reviewed, and what was
withheld and why. The page carries counts only, with small groups suppressed
the same way `aggregate_summary.json` suppresses them; no name or record
identifier appears. English and Spanish render from the same data:

```sh
reconcile run --config examples/intake-demo/recipe-dv.toml --out out-dv
reconcile report --run-dir out-dv --lang en --out out-dv/narrative-en.md
reconcile report --run-dir out-dv --lang es --out out-dv/narrative-es.md
```

Omit `--out` to print to stdout. The command reads `run_summary.json` (counts
the run writes next to the review queue) and, when the policy pack produced
one, `aggregate_summary.json`. The Spanish strings are a machine-drafted
translation awaiting review by a native speaker; treat the English page as
authoritative until then.

### Reading from PDFs

With the `extract` extra installed, point the recipe's `incoming` at a folder
and add an `[extract]` section to enable pdfplumber:

```toml
[input]
existing = "existing.csv"
incoming = "intake-docs/"    # folder with .csv and .pdf files

[extract]
backend              = "pdfplumber"
confidence_threshold = 0.5
```

The pipeline routes `.csv` files through the structured reader and `.pdf` files
through the extractor. Each extracted field carries a source-span pointer (PDF
filename, page number, bounding box) that appears in the review queue CSV as
`{field}_left_span` and `{field}_right_span` columns, so a reviewer can navigate
back to where the value was read.

Pages with fewer than five words, or where the average word length looks garbled
(over 15 characters), score below 0.5 and are flagged as low-confidence. They
can be routed to a cloud seam (Claude on Bedrock) by setting `backend =
"bedrock"` — see `examples/intake-demo/recipe-pdf.toml`. Under the `dv` policy
pack the cloud seam is always disabled, regardless of the recipe setting; PII
does not leave the machine.

### Normalizing addresses

Map an `address` field in the recipe to bring address into the match. The
default backend is a vendored, deterministic CASS-style ruleset that standardizes
to USPS-style abbreviations, so "123 North Main Street" and "123 N Main St" both
reduce to "123 N MAIN ST" and stop reading as a mismatch:

```toml
[mapping]
first_name = "first"
last_name  = "last"
address    = "street"

[normalize]
address_backend = "deterministic"   # or "libpostal" (optional, see below)
```

See `examples/address-demo/` for a runnable demo. The ruleset is position-aware:
"ST" at the start of a street name stays Saint ("123 St Charles Street" becomes
"123 ST CHARLES ST"), a street named for a suffix word is left alone
("123 Avenue B" stays as written), and "Apartment" abbreviates only when a unit
number follows it. The standardizer is **CASS-style and not USPS-certified** —
real certification requires licensed USPS data, and no deliverability check is
made. For heavier parsing, set `address_backend = "libpostal"`, which requires
the libpostal C library and the `postal` Python package; without them, selecting
that backend fails with a clear message rather than silently changing results.
A scheduled, non-blocking CI job builds a pinned libpostal release from source
and runs the backend's tests against the real library.

### Writing back to a case system

By default the resolved records are written to `out/resolved.csv`. Two paths
carry them into a CRM, and the offline one is the default.

**Import-ready export file (offline, no network).** The `salesforce_csv` and
`civicrm_csv` connectors write a CSV mapped to the CRM's own import schema (NPSP
Contact columns, or CiviCRM import columns) plus an external-id column keyed on
the resolved cluster id. You load it with the CRM's native import tool (the
Salesforce Data Import Wizard or Data Loader, CiviCRM's Import Contacts) and
upsert on that external id, so a re-run updates rather than duplicates. Nothing
leaves the machine:

```sh
reconcile run --config examples/intake-demo/recipe-salesforce-csv.toml --out out
# writes out/salesforce_import.csv

reconcile run --config examples/intake-demo/recipe-civicrm-csv.toml --out out
# writes out/civicrm_import.csv
```

Because the file stays local, this path is permitted under the `dv` policy pack.

**Live API push (opt-in, network).** To write directly
into a running CiviCRM instance instead, select the connector in the recipe's
`[output]` section and pass the API key through the environment:

```sh
CIVICRM_API_KEY=your-key reconcile run \
  --config examples/intake-demo/recipe-civicrm.toml --out out
```

The write is an upsert keyed on an external identifier, so a second run updates
the same contacts rather than creating duplicates. Use `--dry-run` to see what
would be written without contacting the server.

Salesforce NPSP is the second connector, using the REST upsert-by-external-id
endpoint. Configure it the same way and pass the access token through the
environment:

```sh
SF_TOKEN=your-access-token reconcile run \
  --config examples/intake-demo/recipe-salesforce.toml --out out
```

**Generic webhook (opt-in, network).** For a destination with no dedicated
connector -- a Zapier or Make automation, an org's own intake API -- point
the `webhook` connector at any endpoint that accepts a JSON POST:

```sh
WEBHOOK_TOKEN=your-token WEBHOOK_SIGNING_SECRET=your-secret reconcile run \
  --config examples/intake-demo/recipe-webhook.toml --out out
```

One POST per resolved record, with an optional bearer token and an optional
HMAC-SHA256 request signature so the receiver can verify a payload came from
this run unaltered. The full payload shape, a worked example, and signature
verification code are in `docs/connectors/webhook.md`. Like CiviCRM and
Salesforce, this is a network target the `dv` policy pack refuses.

Apricot, Airtable, and Google Sheets are researched but not implemented: each
is a proprietary vendor API this project has not built or tested against, so
each has a design brief (auth model, rate limits, pagination, `is_local`
classification) in `docs/connectors/` rather than code, pending a priority
decision and API credentials.

Every write is recorded in an append-only, tamper-evident provenance log
(`out/provenance.jsonl`): each entry carries a BLAKE2b hash of the written fields
and the hash of the previous entry, so altering any past entry breaks the chain.
Check it at any time:

```sh
reconcile verify --provenance out/provenance.jsonl
```

### Destroying artifacts on a retention schedule

The out directory accumulates files that carry constituent field values:
`resolved.csv`, `review_queue.csv`, `withheld.csv`, and the CRM import files.
The HUD comparable-database guidance the DV pack is modeled on expects
individual records to be routinely destroyed once they are no longer needed.
`reconcile destroy` executes that destruction over a stated retention window:

```sh
reconcile destroy --out out --older-than 30d --dry-run   # list what would go
reconcile destroy --out out --older-than 30d             # delete and certify
```

Only the known PII-bearing artifacts are eligible (an explicit list, not a
glob), and the provenance log is never touched. Each deleted file is hashed
with SHA-256 before deletion and one `destroyed` certificate per file is
appended to `out/provenance.jsonl`, so the chain proves what was destroyed,
when, and under which policy without retaining any content. Check it with
`reconcile verify` as usual.

Two limits are stated rather than hidden. No retention window ships as a
default, because how long records may live is a decision for the adopting
organization and its counsel; `--older-than` is therefore required (`0d`
means regardless of age). And deleting a file is not forensic erasure: on
journaling filesystems and SSDs the bytes can persist until overwritten, so
full-disk encryption of the machine remains the compensating control.

### Running with Docker

The tool runs as a one-command container, with PDF extraction included:

```sh
docker build -t constituent-reconciler .            # or: make docker
docker run --rm -v "$PWD/out:/work/out" constituent-reconciler \
  run --config examples/intake-demo/recipe.toml --out out
```

Mount your own recipe and data at `/work/data` to run against real input. The
`reconcile schema` command prints the config, connector, and report schema
versions the build commits to (see `docs/decisions/0006-schema-stability.md`).

### Installing without internet access

For a machine with outbound internet disabled, `make bundle` builds an offline
install bundle: a dependency wheelhouse, the saved Docker image, a source
archive, and a checksum manifest that CI signs with Sigstore. Verification,
transfer, and install steps are in
[docs/INSTALL-OFFLINE.md](docs/INSTALL-OFFLINE.md).

## What it does not do

* It is **not a CRM or a system of record.** It writes into the systems an
  organization already runs. Becoming a database is an explicit non-goal.
* It does **not certify addresses to USPS CASS.** Real CASS certification
  requires licensed USPS data. The standardizer is CASS-style and is labeled as
  not certified.
* It does **not reimplement record linkage.** It wraps an existing matcher and
  contributes the pre-tuned defaults, the orchestration, and the review queue.
* It does **not replace human judgment on a match.** Every uncertain decision
  goes to a person.

## How it compares

* **Splink, dedupe, Zingg** are the resolve step alone, with no extraction, no
  address handling, and no write-back, and they expect ML fluency the target
  user lacks. This project wraps one of them rather than competing with it.
* **WinPure, Data Ladder** chain standardize, match, and merge, but have no
  document extraction upstream, ship analyst-grade review, and are proprietary
  and cloud or Windows-desktop, so none can offer the offline DV mode or write
  to CiviCRM.
* **CiviCRM** native dedup is rule-based exact matching. It is a write target
  this tool feeds, which validates the niche rather than competing with it.

## Standards

This project is held to the portfolio-wide engineering standards maintained
alongside this repo's siblings. That standards set is not yet published as its
own taggable repository, so it cannot be vendored in here as a pinned
submodule yet (a portfolio-level gap, not this repo's to fix — tracked as a
gap here rather than silently assumed done). Applies/N-A is declared per
standard below, not silently omitted; every "Applies — gap" row is tracked
locally (this table plus the linked doc) pending a filed issue. Last reviewed:
2026-07-05.

| Standard | Applies? | Status | Details |
|---|---|---|---|
| Quality & Metrics | Applies | Enforced — suite green (130/130), ≥84% branch coverage a merge-blocking `pytest` gate (target 85%; see ROADMAP note) | [docs/ROADMAP.md](docs/ROADMAP.md) metrics ledger |
| Code Quality | Applies | Enforced — `ruff` (incl. `S`, `C90`), `ruff format`, `mypy --strict`, `pytest --strict-markers`, `uv.lock` committed, `uv sync --frozen` | `pyproject.toml`, `Makefile` |
| Security & Supply-Chain | Applies — ASVS L2 (handles DV-survivor PII) | Partial — secret scan, dependency-vuln scan, SAST (Semgrep + CodeQL + zizmor), and a release-time CycloneDX SBOM + keyless build-provenance attestation (`release.yml`) enforced; container scan (Trivy) is enforced in CI; VEX is still a gap | [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) § Security |
| CI/CD | Applies | Partial — SHA-pinned actions, least-privilege tokens, `make verify` parity, `secrets`+`security` jobs, CODEOWNERS, and a solo-maintainer review waiver (ADR 0008) all in place; the matching branch ruleset is a committed artifact (`docs/rulesets/main.json`) but not yet applied to the live repo (a repository-settings action) | `.github/workflows/ci.yml`, [docs/decisions/0008-solo-maintainer-review-waiver.md](docs/decisions/0008-solo-maintainer-review-waiver.md), [docs/rulesets/](docs/rulesets/) |
| Release & Versioning | Applies (release-producing: 0.1.0-0.7.0) | Partial — `.github/workflows/release.yml` is tag-triggered (`v*`), re-verifies at the tagged commit, checks tag/`pyproject.toml` version consistency, builds sdist+wheel, generates a CycloneDX SBOM, attests build provenance (keyless OIDC), and publishes a GitHub Release with the matching CHANGELOG section; gap — no `v*` tag has been cut yet, so the workflow is unexercised, and there is no PyPI publish stage (not yet published to PyPI) | [.github/workflows/release.yml](.github/workflows/release.yml), [CHANGELOG.md](CHANGELOG.md) |
| Accessibility | Applies (`reconcile review` web UI) | Partial — structural WCAG 2.2 AA design in place; axe/pa11y automated gate and screen-reader walkthrough not yet run | [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) § Accessibility |
| Observability | Applies — Tier C (library/CLI) | Declared — no hosted-service surface; no-PII-in-logs enforced by tests | [docs/ROADMAP.md](docs/ROADMAP.md) § Observability |
| Internationalization | Applies — deferred to 1.0 | Declared — EN/ES parity is a real commitment, not yet built (no catalog infra) | [docs/I18N.md](docs/I18N.md) |
| AI Evaluation | N/A today | Declared — no model inference in any decision path (`BedrockSeam` unimplemented, `NoOpSeam` default); flips to Applies the day that seam ships | [docs/ROADMAP.md](docs/ROADMAP.md) § AI Evaluation Standard applicability |
| Documentation | Applies | Partial — this table, ADRs, CITATION.cff, CHANGELOG all present; ADRs live at `docs/decisions/` not the standard's `docs/adr/` path; STANDARDS/ not yet vendored | [docs/decisions/](docs/decisions/) |
| Responsible Tech | Applies (core to this repo's identity) | Partial — strongest section of this repo: DV/VAWA/FVPSA invariants are merge-blocking tests; threat model and dated bias/ethics sign-off still open | [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) |

Project-specific target values are recorded in
[docs/ROADMAP.md](docs/ROADMAP.md) and findings in
[docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md).

## For Claude Code

Read [CLAUDE.md](CLAUDE.md) first. It is the source of truth for scope,
conventions, and the build plan, and it states the hard guardrails (fail-closed
gates, the privacy invariants that are merge-blocking tests, the rule against
reimplementing the matcher). Then read [docs/ROADMAP.md](docs/ROADMAP.md) and
build phase by phase. A phase is done when its acceptance criteria and its
merge-blocking metrics pass, not before. v0.1 through v0.7 are implemented and green: resolve and review (v0.1), CiviCRM
write-back and provenance (v0.2), the pdfplumber extraction seam (v0.3),
CASS-style address normalization (v0.4), the DV policy pack (v0.5), the v1.0
engineering deliverables — Salesforce connector, Docker self-host, schema-version
declarations, DPG conformance note (v0.6), and the WCAG 2.2 AA web review UI plus
import-ready CRM export files (v0.7). What remains before the 1.0 stability tag is
a full accessibility audit, supply-chain hardening, and the real-organization
adoption the tag is gated on.

## License

Apache-2.0.

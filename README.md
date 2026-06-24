# constituent-reconciler

An offline-first pipeline that turns a stack of intake PDFs and a spreadsheet
into verified, deduplicated constituent records and writes them into the case
system a nonprofit already runs. A non-technical reviewer approves, corrects,
or rejects every uncertain match before anything is written. Nothing merges
silently.

> **Status: v0.2, early but working.** The pipeline runs and is tested:
> CSV in, deduplicated records out, with a human review queue, a committed eval
> ([eval/report.md](eval/report.md)), CiviCRM write-back, and a tamper-evident
> provenance log. Document extraction and address normalization are not built
> yet. Track progress in [docs/ROADMAP.md](docs/ROADMAP.md); the build is
> specified in [CLAUDE.md](CLAUDE.md).

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

1. **Ingest** a folder or inbox of PDFs, scans, CSVs, and email bodies.
2. **Extract** field and value pairs with a source-span pointer and a
   confidence score. Extraction runs offline by default; an optional Bedrock
   (Claude) seam handles only low-confidence pages, and only when the active
   policy allows it.
3. **Normalize** names and dates, and standardize addresses with libpostal
   plus a deterministic ruleset.
4. **Resolve** each candidate against existing constituents using a pre-tuned
   probabilistic matcher with sane defaults, so the operator does not have to
   supply labeled pairs. The output is match, non-match, or possible-match.
5. **Review.** Anything below threshold, any possible-match, and any failed
   address routes to a review queue. A non-technical reviewer sees the source
   span beside the candidate duplicate and chooses approve, correct, or reject.
   The confidence gate is fail-closed: when in doubt, a human looks.
6. **Write** only approved and consented records, through a connector for the
   target system (CiviCRM first; Salesforce, Airtable, Sheets, CSV and webhook
   to follow).
7. **Log** every write to an append-only provenance record with content
   hashing (BLAKE2b) and an RFC 3161 timestamp, so an org can show what was
   written, when, and under which consent.

## The privacy mode

Victim-service providers operate under VAWA and FVPSA confidentiality rules.
Client information may not be entered into shared databases
["regardless of whether the information has been encoded, encrypted, hashed, or otherwise protected"](https://www.parasolcooperative.org/post/navigating-ai-in-victim-services-a-response-to-nnedv-s-vital-guidance).
That makes a cloud service a structural non-starter for this segment, not a
preference.

The `dv` policy pack is the answer. Set `pack = "dv"` in the recipe, or use the
bundled `recipe-dv.toml`. Consent is a first-class field, and under this pack the
export refuses, fail-closed, to emit any record whose consent is not granted;
withheld records are recorded by id and reason only, never with field values, so
the record of what was withheld leaks nothing. When document extraction lands,
the same pack fuses the optional cloud seam off so PII never leaves the machine
and restricts exports to org-local, aggregate, suppression-aware shapes. The
point holds at every phase: the tool cannot be configured into leaking data it
was told not to share.

This is a reference implementation, not legal advice. An organization adopting
it needs its own review against its own obligations. See
[docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md).

## Usage

Install (Python 3.11+):

```sh
make install
```

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
side for a human to approve, correct, or reject). In v0.1 the review queue is a
CSV; the WCAG 2.2 AA web UI is the next phase. Carry decisions back in with
`reconcile apply --decisions decisions.json`, which treats approved pairs as
merges and re-resolves.

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

### Writing back to a case system

By default the resolved records are written to `out/resolved.csv`. To write them
into a running CiviCRM instance instead, select the connector in the recipe's
`[output]` section and pass the API key through the environment:

```sh
CIVICRM_API_KEY=your-key reconcile run \
  --config examples/intake-demo/recipe-civicrm.toml --out out
```

The write is an upsert keyed on an external identifier, so a second run updates
the same contacts rather than creating duplicates. Use `--dry-run` to see what
would be written without contacting the server.

Every write is recorded in an append-only, tamper-evident provenance log
(`out/provenance.jsonl`): each entry carries a BLAKE2b hash of the written fields
and the hash of the previous entry, so altering any past entry breaks the chain.
Check it at any time:

```sh
reconcile verify --provenance out/provenance.jsonl
```

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

This project holds itself to a consistent engineering bar: `ruff`,
`mypy --strict`, and `pytest` as merge-blocking gates; a committed, regenerated
eval report; OWASP ASVS-aligned supply-chain practices (SBOM, signed releases,
pinned actions) as they land; WCAG 2.2 AA for any user interface; and English
and Spanish at parity for public-facing copy. Project-specific values are
recorded in [docs/ROADMAP.md](docs/ROADMAP.md) and findings in
[docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md).

## For Claude Code

Read [CLAUDE.md](CLAUDE.md) first. It is the source of truth for scope,
conventions, and the build plan, and it states the hard guardrails (fail-closed
gates, the privacy invariants that are merge-blocking tests, the rule against
reimplementing the matcher). Then read [docs/ROADMAP.md](docs/ROADMAP.md) and
build phase by phase. A phase is done when its acceptance criteria and its
merge-blocking metrics pass, not before. v0.1 (resolve and review) and v0.2
(CiviCRM write-back and the append-only provenance log) are implemented and
green. The next target is v0.3: the offline document-extraction seam with
source-span pointers surfaced in the review queue.

## License

Apache-2.0.

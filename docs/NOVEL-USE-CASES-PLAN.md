# Novel use cases: implementation plan

**Drafted:** 2026-07-22  
**Planning frame:** Now / Next / Later  
**Scope:** new applications of the shipped extract-normalize-resolve-review-
write chain, not new systems of record

## Strategy

The reusable product is not deduplication in the abstract. It is a fail-closed
identity decision over messy nonprofit intake, with a caseworker-grade review
surface, field correction, explicit consent, and proof of what was written. A
new use case fits when it keeps three boundaries:

- the destination remains the organization's system of record;
- uncertainty still reaches a human before a merge or relationship is written;
- a policy pack can prevent egress and withhold unconsented records without
  special cases in the adapter.

The plan prioritizes depth in the existing chain. It excludes eligibility
decisions, risk scoring, automated service recommendations, benefits
adjudication, and any cross-organization DV linkage.

## Portfolio

| Horizon | Use case | Primary user | Value | Effort | Confidence |
| --- | --- | --- | --- | --- | --- |
| Now | Returning-client batch reconciliation | intake/operations staff | remove repeated extraction and normalization work while preserving full rescoring | M | High |
| Now | Data-migration cutover assurance | CRM administrator | compare legacy and target exports, review identity drift, prove the cutover set | M | High |
| Now | Post-write split and repair planning | case manager/administrator | turn a discovered false merge into a destination-specific, reviewable repair plan | L | Medium |
| Next | Cross-program transfer packet within one organization | program coordinator | reconcile program rosters and move only consent-scoped fields | L | Medium |
| Next | Referral-return reconciliation | referral coordinator | match return files back to reviewed constituents without treating HSDS as client data | L | Medium |
| Next | Reporting-period closeout | grants/operations staff | reconcile late and duplicate intakes before suppressed reporting | M | High |
| Later | Mobile/outreach intake synchronization | outreach team | reconcile offline field batches against the home CRM with conflict review | XL | Low |
| Later | Human-gate kernel extraction | civic-tech maintainer | reuse review, policy, and provenance in a second concrete application | XL | Gated |

## Now

### UC-01 — Returning-client batch reconciliation

**Problem.** Organizations rerun the same large existing export with a small
incoming batch. Parsing and normalization are deterministic and repeat work,
but matching remains population-dependent.

**Product shape.**

1. Add a content-addressed stage cache under an operator-selected local
   directory. Cache only extraction and normalization results, keyed by input
   digest, recipe schema version, active field mapping, extractor version, and
   normalization backend.
2. Keep candidate generation and scoring fresh for every run. The cache must
   never reuse a pair probability or band because term frequencies and new
   cross-batch candidates can change them.
3. Emit progress events for `ingest`, `extract`, `normalize`, `score`, `review
   artifact`, and `write`, with completed/total counts when a denominator
   exists. Default CLI rendering is one updating line on a TTY and stable
   newline records otherwise.
4. Record cache hits/misses and stage durations in `run_summary.json`, without
   paths or field values.

**Architecture.**

- New `stage_cache.py` protocol with a filesystem implementation.
- `pipeline._ingest_source` returns source digests alongside records.
- `normalize_record` remains pure; the cache wraps it rather than entering it.
- `ProgressSink` protocol defaults to no-op; CLI supplies the renderer.
- The manifest records cache policy and hit counts, not cached content.

**Privacy and failure behavior.**

- The cache is local and treated as a PII artifact by `reconcile destroy`.
- A cache entry with a schema/version/hash mismatch is ignored, never coerced.
- DV mode allows only a cache path under the local output root unless the
  operator explicitly configures another local retention boundary.

**Acceptance criteria.**

- Changing one source row invalidates only that row's deterministic cache
  entry.
- Adding a record still causes every relevant pair to be freshly scored.
- Cached and uncached runs produce byte-identical decision inputs and golden
  records.
- A planted field value is removed from output and cache by the destruction
  command.
- The large-corpus report shows wall-clock and peak-memory before/after numbers.

### UC-02 — Data-migration cutover assurance

**Problem.** A nonprofit moving between CRMs needs to know whether the target
export represents the same people as the legacy export before changing the
live system.

**Product shape.**

1. Add `reconcile compare --left <recipe/source> --right <recipe/source>`.
2. Treat both sides as read-only sources; no connector is built.
3. Produce reviewed identity outcomes plus a cutover report: matched people,
   left-only, right-only, conflicting values, and ambiguous clusters.
4. Export a local, import-ready correction file only after review. Never
   mutate either live system from the comparison command.

**Architecture.**

- Reuse canonical mapping, normalization, matcher backend, review session, and
  source spans.
- Add side labels to `Record.source`; do not add migration semantics to the
  matcher.
- Add count-only `migration_summary.json`; field discrepancies stay in local
  PII artifacts.
- Bind the comparison manifest to both input hashes and both mapping recipes.

**Acceptance criteria.**

- Every row on each side is accounted for exactly once.
- An exact duplicate reaches one reviewed identity, while
  same-name/different-DOB remains reviewable.
- The summary contains no field values.
- Reordering either export does not change record ids or reviewed decisions.
- No command path can write to a live connector.

### UC-03 — Post-write split and repair planning

**Problem.** Durable cannot-link constraints prevent a rejected pair from
re-merging, but an administrator who discovers a bad merge after write needs a
safe way to understand the repair.

**Product shape.**

1. Add connector capabilities separate from `Connector.write_all`:
   `inspect_repair` and, only for adapters with verified semantics,
   `apply_repair`.
2. `reconcile plan-split --manifest ... --cluster ...` reconstructs members and
   lineage from the source batch, requires a reason and reviewer identity, and
   writes a local repair plan.
3. The plan records the old external id, proposed split records, fields that
   need restoration, and operations the destination supports. Raw values live
   only in the local plan; provenance stores its digest.
4. Applying a remote repair requires a second reviewer and an adapter whose
   capability declaration covers that exact destination/version. Unsupported
   adapters produce manual instructions and cannot be forced.

**Dependencies.**

- The capability protocol is decided in
  [adr/0012-connector-repair-capabilities.md](adr/0012-connector-repair-capabilities.md).
- Pilot with one destination first, preferably CiviCRM because it is
  self-hostable and its entity model is already explicit.
- Add delete/deactivate/merge semantics only from current vendor documentation
  and a live disposable instance.
- Extend the threat model and destruction inventory before storing repair
  plans.

**Acceptance criteria.**

- Planning is read-only and repeatable.
- Repaired source decisions contain binding cannot-links, so the next run
  cannot recreate the bad cluster.
- A second reviewer is mandatory for remote destructive operations.
- An unsupported destination cannot be coerced into a generic delete.
- Provenance verifies the repair-plan digest and each applied operation.

## Next

### UC-04 — Cross-program transfer packet within one organization

**Problem.** Separate program rosters inside one nonprofit often describe the
same constituent differently. Staff need a reviewed identity and a
minimum-necessary transfer, not another organization-wide master database.

**Product shape.**

- Add named source/destination scopes to consent beyond connector name:
  `program:<id>` and `purpose:<id>`.
- Reconcile rosters locally, review uncertain identities, and create a local
  transfer packet containing only explicitly mapped, in-scope fields.
- Require a receiving-program acknowledgement artifact before the packet is
  marked delivered.

**Acceptance criteria.**

- Out-of-scope fields never enter the packet or provenance payload.
- Revocation after planning prevents delivery.
- Each transferred field names its source record and consent scope.
- DV mode defaults the feature off; an adopting organization must supply its
  own reviewed policy decision before enablement.

### UC-05 — Referral-return reconciliation

**Problem.** A referral partner returns a status file with inconsistent names
or contact details, and staff need to attach outcomes to the correct reviewed
constituent.

**Product shape.**

- Treat the return file as another intake source with a required
  `referral_id` when available.
- Match identity fields, review ambiguity, and emit an outcome-link artifact
  keyed on the reconciler cluster id.
- Allow HSDS service identifiers as non-matching metadata. Do not map client
  records into HSDS; HSDS describes the service directory.

**Acceptance criteria.**

- Outcome values never influence identity probability unless a recipe
  explicitly maps a canonical identity field.
- One return row cannot attach to multiple constituents without review.
- The artifact distinguishes unmatched, ambiguous, and linked outcomes.
- Consent scope covers the partner and purpose at export time.

### UC-06 — Reporting-period closeout

**Problem.** Late files and duplicate intakes distort board, funder, and
program counts at period end.

**Product shape.**

- Add a closeout profile that freezes an input manifest, runs reconciliation,
  requires all review items resolved, and emits existing aggregate, comparable,
  and narrative reports as one signed local bundle.
- Record late-arriving files as a new closeout version, never by mutating the
  earlier manifest.

**Acceptance criteria.**

- A bundle cannot finalize with pending or second-review-waiting pairs.
- Every report carries one manifest hash and reporting period.
- DV suppression applies before narrative composition.
- Reopening a period creates a superseding bundle with an explicit reason.

## Later

### UC-07 — Mobile/outreach intake synchronization

This is a batch-sync problem, not a live mobile app. Field teams export signed
offline batches; the home organization verifies the signature, reconciles
against its CRM export, reviews conflicts, and writes approved results through
an existing connector. Required design work includes device identity, lost
device response, replay prevention, partial synchronization, and local
retention. Do not start before a real outreach partner validates the workflow.

### UC-08 — Human-gate kernel extraction

Extract the decision/review/policy/provenance pattern only after a second
shipping consumer exists. The second consumer must supply a decision object
that is not a constituent pair, proving the abstraction. Until then, keep the
modules dependency-light inside this repository and treat EXP-15 as a
conditional architecture option.

## Sequencing and capacity

Assume one maintainer and reserve capacity as 60% planned features, 30%
technical health/evaluation, and 10% unplanned support while pilots begin.

1. **Now-1: UC-01 cache and progress (2–3 PRs).** Establish performance
   baselines, cache deterministic stages, then add destruction and manifest
   coverage.
2. **Now-2: UC-02 migration comparison (2 PRs).** Land the read-only compare
   model and report, then review/apply artifacts.
3. **Now-3: UC-03 repair planning (study + 3 PRs).** Write the capability ADR,
   implement read-only planning, then pilot one destination's reviewed repair.
4. **Next validation:** run UC-01 and UC-02 with an adopting organization.
   Re-rank UC-04–UC-06 from observed demand rather than synthetic personas.
5. **Later:** start UC-07 or UC-08 only with the named external partner or
   second consumer.

## Cross-cutting definition of done

Every use case must include:

- one passing, one ambiguous, and one fail-closed fixture;
- consent-scope and no-egress tests where data can leave the machine;
- source accounting with no silently dropped input;
- a versioned artifact schema and migration note;
- content-free telemetry only;
- accessibility coverage for every new review state;
- an eval showing false-merge behavior did not regress;
- retention/destruction inventory updates for every new PII artifact;
- a claims-audit update that distinguishes shipped code from live evidence.

## Decisions required from real users

Before committing the Next horizon, validate:

- whether migration assurance or recurring intake consumes more staff time;
- which destination needs the first repair capability and what “undo” means in
  that system;
- which consent scope vocabulary staff can apply correctly;
- whether referral returns contain stable referral ids often enough to lead
  with deterministic linking;
- which closeout reports are submitted versus reviewed internally.

These questions change priority and artifact shape. They do not justify
weakening the fail-closed gate while answers are missing.

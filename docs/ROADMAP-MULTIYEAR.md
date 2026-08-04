# Multiyear roadmap: 2026 H2 through 2029

**Drafted:** 2026-08-03  
**Position:** umbrella plan over [NOVEL-USE-CASES-PLAN.md](NOVEL-USE-CASES-PLAN.md),
which holds the near-term Now/Next/Later detail, and
[ROADMAP-CLOSEOUT.md](ROADMAP-CLOSEOUT.md), which records terminal states and
the external 1.0 gates. [ROADMAP.md](ROADMAP.md) is the historical phase
record.  
**Dates:** quarters and halves only. Where the future depends on people
outside this repository, this document states the gate instead of a date.

## Strategy recap

The project ships one narrow chain: extract, normalize, resolve, review,
write. Every capability inside that chain is solved art elsewhere; the
contribution is opinionated orchestration with a fail-closed human gate over
every uncertain identity decision, plus a privacy posture a victim-service
provider can legally operate. Depth on the chain beats breadth, so the years
ahead add applications of the shipped chain rather than new subsystems, and
never a new system of record.

Two audiences steer priority. Data and operations staff at small and
mid-sized nonprofits are the users; the nonprofit-tech open-source community
(Open Referral, NNEDV Safety Net, Code for America) judges the repository as
a work sample and as something to adopt or coordinate with. Both audiences
lose trust faster to an overclaim than to a small surface, which is why the
closeout keeps human and live-system evidence visibly external.

What "multiyear" means with one maintainer differs from a staffed product
plan. Capacity follows the plan's standing split of 60% planned features,
30% technical health and evaluation, and 10% unplanned support while pilots
begin. A horizon here is a center of gravity, not a backlog with owners: the
committed work is small and sequential, and everything beyond the Now horizon
is conditional on evidence from real users. When a gate does not open, the
honest outcome is that the dependent work stays unbooked. No horizon below
converts an external gate into an engineering estimate.

## Horizon overview

| Horizon | Center of gravity | Entry condition |
| --- | --- | --- |
| 2026 H2 | Land the Now horizon (UC-01, UC-02, UC-03) and initiate every external 1.0 gate | none; the work is committed in NOVEL-USE-CASES-PLAN.md |
| 2027 | Validate UC-01 and UC-02 with an adopting organization; re-rank the Next horizon from observed demand | at least one real adopting organization |
| 2028 | Later-horizon preconditions (outreach partner for UC-07, second consumer for UC-08) and the schema-stability record | named external partners; two real releases without a breaking change |
| 2029 | Sustained maintenance, with community handoff options and an honest graduation or archival decision | evidence accumulated in the prior horizons |

## 2026 H2

### Product depth

The Now horizon of NOVEL-USE-CASES-PLAN.md is in flight: the first PRs of
UC-01 (returning-client batch reconciliation), UC-02 (data-migration cutover
assurance), and UC-03 (post-write split and repair planning) are landing now.
The remainder of the half follows the plan's numbered sequencing:

1. finish the UC-01 stage cache, including destruction and manifest coverage;
2. land the UC-02 read-only compare model and its review artifacts;
3. commit the UC-03 capability ADR, then read-only repair planning.

UC-03's reviewed remote repair is
piloted against one destination, CiviCRM first because it is self-hostable;
if a live disposable instance is not available this half, that final step
moves to 2027 rather than shipping unverified semantics.

### The 1.0 external-evidence path

Engineering for 1.0 is done; ROADMAP-CLOSEOUT.md lists the only remaining
blockers, all external:

- an assistive-technology walkthrough, reviewed Spanish UI copy, and a signed
  accessibility conformance report (docs/reviews/SCREEN-READER-WALKTHROUGH.md,
  docs/I18N.md);
- the first signed release and the live required-check ruleset
  (.github/workflows/release.yml, docs/rulesets/main.json);
- a recorded CiviCRM end-to-end demonstration on an authorized instance
  (docs/ADOPTION-KIT.md);
- more than one real adopting organization;
- a demonstrated schema-stability window of two real releases without a
  breaking change (docs/adr/0006-schema-stability.md).

During this half the maintainer can initiate all of them: recruit the
qualified reviewers, cut the first signed tag, apply the ruleset in
repository settings, and seek the authorized CiviCRM instance for the
recording. None of these completions is scheduled, because each depends on
people or account actions outside the codebase. They stay on this list until
the evidence exists.

### Adoption and pilots

docs/ADOPTION-KIT.md is the offer. The goal for the half is one organization
running a pilot, with UC-01 and UC-02 as the workloads the plan designates
for validation. A pilot that starts late in the half is still a success;
fabricating interest is not an alternative, so this line carries no fallback
deliverable.

### Evaluation and research

Each release regenerates the committed eval report with false-merge and
missed-match rates. UC-01's acceptance criteria require before/after
wall-clock and peak-memory numbers from the large-corpus benchmark, so that
report gets refreshed when the cache lands. Claims-audit updates accompany
each use case per the plan's cross-cutting definition of done, keeping
shipped code distinct from live evidence.

### Community and standards

The DPG registry nomination (closeout item E10) is ready for submission and
is a maintainer action with an external outcome; it can be filed this half.
The HSDS boundary set by closeout item E1 and UC-05 holds: service
identifiers may travel as non-matching metadata, and client records are never
mapped into HSDS.

### Maintenance capacity

The 60/30/10 split governs the half. Connector adapters stay version-pinned
behind the base interface, and dependency updates flow through the existing
automation. The weekly Scorecard run and the hygiene gate hold the health
floor without new process.

## 2027

### Product depth

Nothing in the Next horizon (UC-04 cross-program transfer, UC-05
referral-return reconciliation, UC-06 reporting-period closeout) is committed
at the start of the year. The plan's "decisions required from real users"
list is the admission test: which of migration assurance or recurring intake
consumes more staff time, which destination needs repair first, which consent
scope vocabulary staff can apply, whether referral ids are stable enough to
lead with deterministic linking, and which closeout reports are actually
submitted. Answers come from pilots, and the Next horizon is re-ranked on
them before any of its PRs are booked. If UC-03's remote repair pilot slid
from 2026, it completes here against a live disposable CiviCRM instance.

### The 1.0 external-evidence path

Once the first signed release exists, every subsequent release contributes to
the ADR 0006 stability record. Accessibility and Spanish review complete
whenever the qualified humans are engaged; the same holds for the recorded
demonstration. Progress on this path is measured in evidence filed, not in
quarters elapsed.

### Adoption and pilots

Adopter validation is the center of the year. Running UC-01 and UC-02 with a
real organization produces the decision-list answers above and exercises the
review queue with actual reviewers. It also surfaces the support load the
capacity model reserves 10% for. The 1.0 gate requires more than one
adopting organization, so outreach continues past the first.

### Evaluation and research

Pilot evidence enters docs/CLAIMS-AUDIT.md as live evidence, in a separate
column from shipped code. The reviewer-calibration gate built for the eval
runs against real reviewer decisions where a pilot permits it. EXP-14, the
cross-organization linkage study, stays a study: no prototype without counsel
and a real non-VSP coalition partner, and DV data is excluded regardless.

### Community and standards

If pilots move UC-05 into committed work, the shape of its outcome-link
artifact is worth coordinating with Open Referral before it ships, since the
HSDS boundary is theirs to interpret. The CRM dedupe-cooperation guidance
(docs/CRM-DEDUPE-COOPERATION.md) is updated from what pilot CRMs actually do.

### Maintenance capacity

Support is expected to exceed its 10% share in pilot months. The standing
response is to re-rank planned features downward, not to stretch the
maintainer; the split is a budget, and overruns come out of the 60%.

## 2028

### Product depth

The Later horizon opens only behind its named preconditions, and both remain
external. UC-07, mobile and outreach intake synchronization, starts only when
a real outreach partner has validated the workflow; the required design work
on device identity, lost-device response, replay prevention, partial
synchronization, and local retention begins after that validation, not
before. UC-08, extraction of the human-gate kernel (EXP-15), waits for a
second shipping consumer whose decision object is not a constituent pair;
without one the abstraction is unproven and the modules stay dependency-light
inside this repository. Absent either gate, depth work continues in the
shipped chain wherever adopter demand points.

### The 1.0 external-evidence path

By this horizon the schema-stability window can plausibly close: two real
releases without a breaking change, on top of the declared versions and ADR
0006. When that record exists and the remaining gates (accessibility
evidence, the recorded demonstration, more than one adopter) are met, the
1.0 tag becomes truthful. The tag follows the evidence; a calendar year does
not qualify it.

### Adoption and pilots

Past the first pilots, the aim is unaided installs from the adoption kit and
docs/INSTALL-OFFLINE.md, including the Docker path, with support tickets
feeding documentation fixes. Adopter count and upgrade behavior across
releases are the observable measures.

### Evaluation and research

Third-party reproduction of the committed eval is the community evidence
EXP-16 anticipates, and it stays external: the corpus generator and scoring
CLI make it possible, but only someone else's run makes it evidence. EXP-14
remains conditional under the same terms as before.

### Community and standards

A DPG listing, if granted, is kept current through docs/DPG-CONFORMANCE.md.
Any standards-body coordination stays a maintainer action recorded in the
claims audit rather than a roadmap deliverable.

### Maintenance capacity

Connector API churn is the known long-term tax. Keeping the CiviCRM,
Salesforce, Airtable, and webhook adapters current against vendor versions is
scheduled work inside the 30% health share, and a connector that can no
longer meet its contract is retired honestly rather than quietly weakened.

## 2029

### Product depth

Feature work is demand-driven only. The chain is mature by this point; new
use cases enter through the same Now/Next/Later re-ranking, with adopter
evidence required for commitment. A year with no new features and a healthy
upgrade path is a good year.

### The 1.0 external-evidence path

Either the tag shipped in a prior horizon and this section is closed, or the
unmet gates are still listed openly. There is no third option in which the
missing evidence is synthesized.

### Adoption and pilots

Sustained adoption looks like organizations upgrading across releases without
maintainer hand-holding. Where adoption did not materialize, this workstream
reports that plainly instead of redefining success.

### Evaluation and research

The committed eval continues to regenerate per release. Research items stay
closed or conditional as the closeout recorded them; nothing reopens without
new facts.

### Community and standards

Handoff options are evaluated in order of evidence. A co-maintainer drawn
from an adopting organization is the least disruptive path and requires
demonstrated contribution history. Moving the project to an organizational
home is considered only if adopters ask for it. Neither is promised, and
governance stays with the existing CONTRIBUTING.md and CODE_OF_CONDUCT.md
until a real candidate exists.

### Maintenance capacity

Graduation and archival are both honest endings, and this horizon names them.
Graduation: the 1.0 tag is live, the stability promise has held across
releases, more than one organization depends on the project, and maintenance
is funded by the capacity model or shared with a co-maintainer. Archival: if
the adoption gate never opened, the repository is marked maintenance-only or
archived, and the README states which capabilities are tested and which were
never validated by an external organization. The claims audit records that
terminal state the same way it records every other. An archived project with
accurate claims is a better artifact for both audiences than a nominally
active one with stale promises.

## Non-goals

Carried from the closeout principles, permanent across every horizon:

- no new system-of-record claim: the application reconciles records and
  writes reviewed results into the organization's existing system;
- no weaker connector behind an existing name: network adapters stay dry-run
  pure, consent-gated, non-local when appropriate, and safely repeatable by
  an external key;
- no fabricated evidence: live integrations, accessibility review, adoption,
  and registry status remain visibly external until real;
- no unsafe optimization: deterministic local work may be cached, but a
  probabilistic decision is never reused once the evidence population has
  changed.

Carried from the plan's exclusions, also permanent:

- no eligibility decisions;
- no risk scoring;
- no automated service recommendations;
- no benefits adjudication;
- no cross-organization DV linkage.

Items the closeout closed by decision stay closed absent new facts: the
generic HSDS constituent export (E1), a direct Google Sheets connector (E3),
generic post-write reversal in the connector protocol (E7), and a cache that
skips matcher scoring (E9).

## Review cadence

**Per release.** Regenerate the committed eval and re-stamp the audits the
PR template names. Update docs/CLAIMS-AUDIT.md so shipped code and live
evidence never blur.

**Per half.** At the start of each half, re-rank the Now/Next/Later horizons
in NOVEL-USE-CASES-PLAN.md and reconcile this document against it. Adopter
evidence outranks synthetic personas: a use case moves up only with a cited
observation from a real organization, and it moves down when demand fails to
appear. The horizon table above is updated in the same pass.

**Standing rules.** External gates are never converted into fixtures or
synthetic stand-ins, and never booked as engineering line items; each closes
only when the external evidence it names actually exists. Missing adopter
evidence keeps Next-horizon work unbooked rather than promoting a guess.
A sustained breach of the 60/30/10 capacity split triggers a re-rank, not
overtime. None of these reviews may weaken the fail-closed gate while
answers are missing.

# Backlog triage

**Triaged:** 2026-08-15, against the ten issues open that day.
**Why this exists:** most of this backlog is not engineering work. Reading the
issue list alone, it is hard to see which items a person can finish by typing
and which ones wait on someone else. This file separates them and names the
exact act each blocked item needs.
**How to read it:** every issue lands in exactly one category. Where an issue
has a code half and a blocked half, the category follows the blocked half,
because that is what decides when the issue closes. The code half is still
named so it does not get lost.

The five 1.0 gates (#66 through #70) already have step-by-step procedures in
[EXTERNAL-GATES-RUNBOOK.md](./EXTERNAL-GATES-RUNBOOK.md). This file does not
repeat them. It answers a different question: who is holding each one, and how
long it can realistically stay open.

## The short version

| Category | Count | Issues |
| --- | --- | --- |
| Code, doable now | 2 | #83, #84 (both implemented; pull requests open) |
| Needs a real adopting organization | 2 | #69, #80 |
| Needs an act only the maintainer can perform or authorize | 5 | #66, #67, #68, #70, #78 |
| Needs a decision only the maintainer can make | 1 | #79 |

After #83 and #84, no issue in this backlog can be closed by writing code
alone. Eight of the ten wait on the maintainer, on people she has to recruit,
or on organizations that have not adopted the tool yet. That is not a backlog
failure. It is what a project looks like when the engineering for a milestone
is finished and the evidence for it is not.

**Update 2026-08-21:** #79's decision below was made -- option 1, do it now
-- and implemented. The disposable CiviCRM instance it needed is up
(`civicrm/civicrm-docker`, version 6.17.2), its API v4 delete/create/update
semantics were read from current vendor documentation and exercised live,
and `apply_repair` for CiviCRM now exists behind the second-reviewer gate.
See `docs/CLAIMS-AUDIT.md`'s repair row for what shipped and where the tests
live. The recurring-maintenance consequence the option named is now real:
CiviCRM releases after 6.17.2 are unsupported for repair until re-verified.

**Update 2026-09-06.** The table above is the 2026-08-15 snapshot and is kept
as one; this is where it stands now. Four of the original ten have closed:
#78 and #79 (both recorded on this page), and #83 and #84, whose pull requests
merged. Six remain open -- #66, #67, #68, #69, #70, #80 -- and every one is
still blocked for the reason its section below gives.

Five issues were imported from the roadmaps on 2026-09-05 and are not covered
above. Triaged into the same categories:

| Issue | Category | Holding it |
| --- | --- | --- |
| #134, UC-03 reviewed remote repair | **closed 2026-09-06, already done** | nothing -- the live run of 2026-08-21 is the evidence |
| #135, DPG registry nomination | Needs an act only the maintainer can perform | filing it; the preparation is finished |
| #136, UC-08 human-gate kernel | Conditional, not deferred | a second shipping consumer whose decision object is not a constituent pair |
| #137, UC-07 outreach synchronization | Needs a real outreach partner | partner validation, which the roadmap puts *before* the design work |
| #138, remove the `reconcile` alias | Code-only, but **timed** | nothing; it lands at 0.9.0 |

#138 is the one item on this page that no one else is holding, and it is still
not doable now. The 0.8.0 changelog published a deprecation notice naming
0.9.0 as the removal version, and the alias prints that same version at
runtime (`cli.DEPRECATED_PROG_REMOVED_IN`). Removing it earlier would make the
notice untrue for the release that shipped it. So it is scheduled work, not
available work, and it should stay open until the 0.9.0 branch.

One correction to the #67 section below, which was written before the live
instance existed. It says the exercise has not happened. Most of it has: on
2026-08-21 a disposable CiviCRM 6.17.2 instance was stood up and the
demonstration was driven end to end against it, including the rerun showing
updates rather than duplicates, email and phone landing in dedicated entities,
a revoked-consent record never reaching CiviCRM, and `verify` passing over
both runs (`docs/reviews/CIVICRM-LIVE-DEMONSTRATION-2026-08-21.md`). What is
still missing is the one thing that evidence note says it is not: a published,
dated *recording*. That is the whole of what #67 now waits on, which makes it
smaller than this page currently implies -- but not closed, and the note is
careful not to claim otherwise.

So: of eleven issues open this morning, one closed as already done, and the
remaining ten are unchanged in kind. Nine wait on the maintainer, on people
she has to recruit, or on organizations that have not adopted the tool yet.
The tenth waits on a version number.

## Code, doable now

**#83, a merged identity's consent.** Implemented. Merging took the survivor's
consent, so a cluster whose surviving record carried a grant and whose other
record carried a revocation exported under the grant. It now takes the most
restrictive member's, and the regression test asserts the harm is absent: over
every combination of member lifecycles, run dates, and destinations, an active
merged consent implies every member's consent is active. Grounding and the
cost are in `docs/adr/0013-merged-consent-most-restrictive.md`, which lands
with that pull request.

**#84, compare-apply follow-ups.** Implemented. The correction file now carries
`target_record_ids`, the ids the target export itself supplied, so a matched
row names the record to update rather than keying only on an id the target has
never seen. The comparison resolves one survivorship fill policy for both
sides and compare-apply merges under it, instead of using the package default
while `pipeline.run` used the recipe's.

## Needs a real adopting organization

Neither of these can be closed from this repository under any circumstances.
Fixtures, synthetic personas, and the maintainer acting as a user are all
excluded by the issues themselves. The gating activity is outreach, not
typing, and outreach that has not started cannot finish in weeks. Treat both
as open for at least the next two quarters unless a pilot conversation is
already underway.

**#69, adoption with more than one real organization (1.0 gate).** What is
needed: two independent organizations that each run `docs/ADOPTION-KIT.md`
end to end (validate, dry run, review, output inspection), with at least one
exercising a real destination workflow, and privacy-preserving notes committed
for each. Who from: the issue's own preference is one small human-services
nonprofit with an accidental DBA, plus a second organization on a different
workflow or destination. The realistic sources are the communities CLAUDE.md
already names as the audience: Open Referral, NNEDV Safety Net, Code for
America brigades, a state or regional nonprofit technology assistance
provider. None of them has been asked yet, and asking is a maintainer act.

**#80, pilot UC-01 and UC-02, then re-rank the Next horizon.** What is needed:
one adopting organization running returning-client batch reconciliation and
migration cutover assurance, then answering the "Decisions required from real
users" questions in `docs/NOVEL-USE-CASES-PLAN.md` (which of the two consumes
more staff time, and which consent-scope vocabulary staff can actually apply).
Who from: the same pilot that satisfies #69, ideally. These two issues should
be worked as one conversation with one organization, not as two errands.

Until then, the roadmap ranking of UC-04 through UC-06 rests on synthetic
personas. That is worth saying out loud in any place the ranking is presented
as demand.

## Needs an act only the maintainer can perform or authorize

**#68, first release and the live ruleset (1.0 gate).** Two separable acts.

1. *Ruleset reconciliation, minutes of work.* Re-verified 2026-08-15: the
   live `protect-main` ruleset is active and carries exactly three rules,
   `non_fast_forward`, `deletion`, and `required_status_checks`. Its required
   checks are a superset of `docs/rulesets/main.json`. Three rules in the
   committed file are absent live: the `pull_request` rule (including
   `required_review_thread_resolution`), `required_linear_history`, and
   `strict_required_status_checks_policy: true`. Exact act: decide whether the
   live ruleset comes up to the committed file or the file comes down to the
   deliberate solo-maintainer posture, then make the two agree. Leaving them
   divergent is the one outcome the issue's acceptance criterion forbids.
2. *First release, an authorized act.* `git tag` and `gh release list` are both
   still empty, and `pyproject.toml` is at 0.8.0. Exact act: choose the
   candidate commit and version, cut the signed `v*` tag, and let the release
   workflow run for the first time. Nothing in the repository authorizes this,
   by the issue's own boundary. This is the item most worth doing next, for
   the reason under "the single most useful thing" below.

**#70, schema-stability window (1.0 gate).** Cannot start. The window is
measured across two consecutive releases *after* the first exercised release,
so it needs three tags to exist and none exist today. Exact act: none
available until #68 closes; after that, it is one compatibility audit per
release, comparing `CONFIG_SCHEMA_VERSION`, `CONNECTOR_INTERFACE_VERSION`,
`REPORT_SCHEMA_VERSION`, and `DECISIONS_SCHEMA_VERSION` against the preceding
tag. On a zero-release history this is the furthest-out item in the backlog,
and its distance is set by release cadence, not by effort.

Note for whoever cuts those releases: #84 bumps
`CUTOVER_CORRECTIONS_SCHEMA_VERSION` to 2 with an additive change and a
migration note. Pre-1.0 that is allowed with a MINOR bump per ADR 0006, and
it is not a break, but it should be classified explicitly in the first
compatibility audit rather than discovered there.

**#67, recorded CiviCRM end-to-end demonstration (1.0 gate).** Needs an
authorized, disposable CiviCRM instance with no real client data, which the
maintainer can stand up herself. No outside party is required, which makes
this the most self-contained of the five gates: a weekend of work rather than
a quarter of waiting. Exact act: run the demonstration in the runbook's Gate 3
section, rerun the same reviewed batch to show updates rather than duplicate
contacts, verify that a revoked or absent-consent record never reaches
CiviCRM, and publish the dated recording with versions recorded and
credentials redacted.

**#66, human accessibility and reviewed Spanish evidence (1.0 gate).** Needs
two people the repository cannot supply: a qualified screen-reader participant
for the walkthrough, and a native Spanish reviewer for the review-UI copy.
Exact act: recruit and schedule them (paid or volunteer), run the walkthrough,
have the Spanish copy reviewed and attributed, publish the Accessibility
Conformance Report naming the exact release candidate tested, and disclose
whatever the sessions find rather than fixing only what is convenient. The
automated axe audit and the EN/ES parity coverage already in the repository do
not satisfy this gate and are not offered as if they did.

**#78, cached-run large-corpus numbers (UC-01 acceptance criterion 5).** Half
code, half measurement, and the measurement half is what blocks it. The two
"before" reports are committed
(`eval/large-corpus-stage-baseline-2026-08-03.*` and the mixed CSV and PDF
variant `eval/large-corpus-stage-baseline-pdf-2026-08-04.*`). The "after"
numbers must come from the same machine class as those files, which means the
maintainer's machine, or else the comparison is not a comparison. The code
half, unclaimed and doable by anyone: `tools/corpusgen/stage_baseline.py`
accepts an `active_cache` parameter but never populates it (the docstring says
so plainly), so the harness has no cached mode and no before/after renderer.
Exact act, in order: add a cached run mode and a diff column to the harness,
then run both variants with the cache enabled on the machine that produced the
baselines, and commit the reports with the scope stated honestly (one machine
class, seeded synthetic corpus, not a performance promise). Until then the
stage-cache row in [CLAIMS-AUDIT.md](./CLAIMS-AUDIT.md) correctly says no
benchmark evidence exists.

**Update 2026-08-21: done.** `stage_baseline.py --cached` pre-warms a stage
cache with a discarded pass, then measures ingest/normalize against the warm
cache through the same `pipeline.ingest_normalized_records` path `pipeline.run`
itself uses (`render_cached_report` adds the before/after table); the
pre-cache path is untouched. A fresh before/after pair was measured back to
back on the maintainer's machine over the full 50,066-record corpus:
`eval/large-corpus-stage-baseline-2026-08-21.*` and
`eval/large-corpus-stage-baseline-cached-2026-08-21.*`. The honest result is
not the win UC-01 hoped for -- normalize got slower under the warm cache
(0.986s -> 5.263s) because 50,066 individual cache-file reads cost more than
the recompute they replace, and total stage wall clock rose from 58.6s to
62.9s. Recorded as measured, not reframed; see the stage-cache row in
[CLAIMS-AUDIT.md](./CLAIMS-AUDIT.md) for the full accounting including why a
single cold-to-warm pass is the wrong shape to see the cache's actual benefit
(reuse across separate runs, not within one).

## Needs a decision only the maintainer can make

**#79, `apply_repair` for CiviCRM behind the second-reviewer gate (UC-03, PR
3).** This is mostly code, and it is still filed here, because ADR 0012 makes
the pacing item explicit: delete, deactivate, and merge semantics come only
from current vendor documentation plus a live disposable instance, never from
memory or extrapolation. Writing the adapter without that is exactly the
guessed destructive repair the ADR forbids. So the question is not how to
implement it; it is whether to spend the vendor-semantics and live-instance
work now.

Options and their consequences:

1. **Do it now.** Stand up the disposable CiviCRM instance (the same one #67
   needs, so the two share their setup cost), read current API v4 semantics,
   publish the capability declaration for those exact versions, and implement
   the reviewed apply path. Consequence: UC-03 finishes, and the repository
   takes on recurring maintenance, since every new CiviCRM release is
   unsupported for repair until re-verified.
2. **Defer until a pilot asks.** Leave planning read-only and manual, which is
   already shipped and honest: an operator gets instructions, and no
   destructive operation can be forced onto an undeclared destination.
   Consequence: nothing regresses and no maintenance burden is taken on, but
   UC-03 stays incomplete and the plan file's value depends on an operator
   doing the repair by hand.
3. **Drop it from the 1.0 scope explicitly.** Consequence: the honest option if
   no pilot ever asks for post-write repair, and it should be recorded as a
   decision rather than left as an aging issue.

Option 1 becomes materially cheaper the moment #67 is scheduled, because both
need the same instance. That is the only sequencing argument here; the rest is
the maintainer's call.

## One finding this backlog does not have an issue for

**Resolved 2026-08-27 by taking option 1, the option this section named as the
one to prefer.** Every `split_records` entry now carries a `consent` object
(`required_by_recipe`, `withhold_reason`, `blocks_creation`), and under a
consent-requiring recipe the manual instructions state the rule and name the
members a person must not create. `REPAIR_PLAN_SCHEMA_VERSION` is 2. The bump
is additive and no release has been tagged, so no published artifact carries
version 1. The paragraphs below are kept as written, because the reasoning
about which window is actually reachable is what made the fix testable, and
because the schema-bump caveat was the maintainer's to overrule rather than
mine to delete. One correction to them: the window is narrower than "lapsed or
was revoked", since a revocation would change a source byte and the manifest
check refuses that. What passes the manifest check is time crossing a recorded
`expires` date, which changes consent with no byte changing anywhere.


`repair.plan_split` writes a local repair plan whose manual instructions tell
an operator to create one destination record per split member, and it does not
consult any member's consent while doing so. After #83 a cluster written under
a consent-requiring run had every member consented at write time, so the
exposure narrows to consent that lapsed or was revoked between the write and
the repair. That is a real window: the plan is generated from current source
values, so it can hold a revocation the operator is not shown.

This was deliberately left out of the #83 pull request, because the fix changes
a versioned surface. Options: label each split record with its consent state
in the plan and add an instruction line saying not to create a record for a
member whose consent is not currently active (additive, bumps
`REPAIR_PLAN_SCHEMA_VERSION` to 2), or refuse to plan at all when a member's
consent has lapsed (blocks a legitimate repair, so this is the weaker option),
or record that the operator's own consent process is the control and change
nothing. The first is the protective reading and the one to prefer unless the
schema bump is unwelcome right now.

## The single most useful thing the maintainer could do

Cut the first release and reconcile the ruleset (#68).

It is the only blocked item that needs nobody but her, it takes a decision and
an afternoon rather than a quarter, and it is the sole prerequisite for #70,
which cannot even begin without it. It also gives every pilot conversation
under #69 and #80 something to install by version instead of by commit hash,
which is the difference between "try my repository" and "install v0.8.0". Two
of the four remaining categories move behind that one act.

The second most useful is #67, for the same self-contained reason, and because
it shares its disposable-instance setup with the #79 decision.

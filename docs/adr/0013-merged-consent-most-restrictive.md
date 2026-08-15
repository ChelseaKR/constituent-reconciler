# 0013 — A merged identity takes its most restrictive member's consent

Status: accepted (2026-08-15); resolves issue #83

## Context

When the pipeline decides that two records describe one person, it reduces the
cluster to a golden record. That record inherits a consent lifecycle, and the
export gate (`consent.partition_by_consent`) reads it on every emit. Until now
the inherited lifecycle was the survivor's: the member `decisions._choose_primary`
picked to supply the identity also supplied the consent. So a cluster whose
surviving member carried a grant and whose other member carried a revocation
exported under the grant. The write path and the compare-apply correction export
behaved this way consistently, because both call `decisions.golden_records`.

Adversarial review of the UC-02 work surfaced this as a repo-wide question
rather than a defect in either path, and issue #83 asked for the answer to be
grounded in primary guidance rather than in taste, per this project's rule that
consent rules come from the source and that the more protective reading wins
where guidance is ambiguous.

## What the primary sources say

Read on 2026-08-15. Quotations are from the sources named; the reading applied
to record merges is this project's, and is labeled as such below.

**VAWA, 34 U.S.C. § 12291(b)(2)(B)(ii)** bars a grantee from disclosing
individual client information "without the informed, written, reasonably
time-limited consent of the person ... about whom information is sought."
(uscode.house.gov, prelim edition.) The statute has no revocation provision at
all: "revoke," "withdraw," and "rescind" do not appear in § 12291(b)(2).

**28 C.F.R. § 90.4(b)(3)(ii)(A)**, the operative VAWA regulation, is where scope
lives: "Releases must be written, informed, and reasonably time-limited.
Grantees and subgrantees may not use a blanket release and must specify the
scope and limited circumstances of any disclosure ... reach agreement with the
victim about what information would be shared and with whom; and record the
agreement about the scope of the release. A release must specify the duration
for which information may be shared."

**FVPSA, 42 U.S.C. § 10406(c)(5)(B)(ii)** carries the parallel consent
requirement, and § 10406(c)(5)(G) preserves any law "that provides greater
protection." Its regulation, 45 C.F.R. § 1370.4, has no analog to the VAWA
"may not use a blanket release" sentence, so an FVPSA-only grantee has thinner
federal scope language, not different obligations in this project's direction.

**OVW, FAQ on the VAWA Confidentiality Provision (Oct. 2017), Q19:** a release
"should spell out the purpose for which the information will be used, what
information may be shared, with whom it may be shared, and the duration of the
release." The FAQ never mentions revocation, expiry handling, or record merges.

**NNEDV Safety Net / Confidentiality Institute, Releases FAQ**
(techsafety.org/releasesfaq):

* Q5: a release does not generalize. "If the person or agency to whom the
  information is being released or the specific information to be shared was
  not included in the original release of information form that the survivor
  signed, a new release of information form is needed."
* Q6: a lapsed release is revived only by the survivor. "The release can be
  reaffirmed and extended if the survivor confirms that it is still valid and
  authorizes a new expiration date."
* Q36: "when a survivor withdraws consent, it happens immediately."
* Q37: "Releases are only required when sharing information outside your
  agency."

**NNEDV, Releases and Waivers At-A-Glance:** "It is their information – it is
their choice. It is their choice of what information is shared, and with whom
the information is shared. This includes what information may be included about
the survivor in a database." The document also says "the most protective
standard should be the guide," in a section about multi-agency partnerships.

**HIPAA, 45 C.F.R. § 164.508**, which governs the `hipaa` pack's users, is the
one codified source here on revocation and on validity: an authorization is
invalid where "the expiration date has passed" or where it "is known by the
covered entity to have been revoked" (§ 164.508(b)(2)(i), (iii)), and an
individual "may revoke an authorization ... at any time" (§ 164.508(b)(5)).

**Where every source is silent.** None of them addresses merged, deduplicated,
or consolidated client records. A search of the statutes, the regulations, the
OVW FAQ, and ten NNEDV documents for "merge," "duplicate," "dedup," and
"consolidate" returns two hits, neither on point: Releases FAQ Q39, about
shared external databases "merging or rebundling data," and Comparable Database
101, which requires a comparable database to "de-duplicate client records
within each system" and attaches no consent rule to doing so. There is no
survivor rule and no most-restrictive rule in the primary sources. This
decision is therefore an application of the ambiguity rule in CLAUDE.md, not a
quotation, and it is recorded as such.

## Decision

`decisions.golden_records` gives a merged identity the most restrictive of its
members' consent lifecycles (`models.Consent.most_restrictive`), not the
survivor's. The survivor still supplies the identity and the field values; it
no longer speaks for what the other people in the cluster agreed to.

Each dimension of the lifecycle takes its narrowest value:

* one member with a revoked status revokes the merge; one member whose status
  is absent or unrecognized makes the merge absent, and the two reasons stay
  distinct because a caseworker acts on them differently;
* the latest `granted_on` governs, so the merge is not effective before every
  member's grant was;
* the earliest `expires_on` governs, so the first ceiling to fall ends the
  merge; a member with no recorded expiry contributes no ceiling and cannot
  lift another member's;
* scopes intersect, with an empty scope reading as "every destination" exactly
  as `Consent.reason` reads it. Members with no destination in common produce
  `models.NO_COMMON_DESTINATION`, a token no connector name can equal, because
  an empty set already means the opposite.

The property this buys, asserted directly in `tests/test_consent.py` over every
combination of member lifecycles, run dates, and destinations: **a merged
consent is active only where every member's consent is active.** A merge can
narrow what a person granted and can never widen it. A single-member cluster
returns that member's own `Consent` object unchanged, so the common case is
untouched.

Both paths change together, as issue #83 required, and they do so at one site:
the run pipeline and `compare-apply` both merge through `golden_records`.

## Why not the survivor's consent

The survivor is chosen for reasons that have nothing to do with what anyone
agreed to: `_choose_primary` prefers an existing-source record so the write
updates a row the case system already has. Letting that choice carry consent
means a merge can convert a person's "no" into someone else's "yes" as a side
effect of a matcher score. Under the sources above, a release is scoped to
particular information, a particular recipient, and a particular period; a
grant recorded on one record is evidence about that record's disclosure
agreement, not about a different person's, and not about information the
survivor never authorized. NNEDV Q5 is the closest statement: information not
named in the release the survivor signed needs a new release.

Reading a merge as widening consent would also invert Q6 and Q36. A revocation
takes effect immediately, and a lapsed release comes back only when the
survivor reaffirms it. A merge is neither a reaffirmation nor a survivor act.

## What this costs, stated plainly

Records that a surviving grant arguably covers will now be withheld. An
organization whose intake system holds a stale revocation on an old row will
see the merged identity held back with reason "revoked" until someone follows
up. That is the intended trade: the cost of withholding is a follow-up, and the
cost of exporting is a disclosure nobody authorized. The withheld list carries
ids and reasons, which is exactly what a caseworker needs to re-ask.

Two consequences deserve naming rather than burying:

* **The tool cannot order consent events in time.** A record carrying a
  revocation and a later record carrying a grant may well mean the person
  re-consented, but nothing in the data says when the revocation happened;
  `Consent` records `granted_on` and `expires_on`, never `revoked_on`. Inferring
  a re-grant would be inventing a fact. The merge withholds and the reason says
  why, which routes the question to the person who can actually answer it. A
  future `revoked_on` column could let a later grant supersede an earlier
  revocation; that is a recipe-schema change and a separate decision.
* **Merging is not itself a disclosure.** Per NNEDV Q37, releases govern
  sharing outside the agency, so nothing here says an organization may not
  deduplicate its own records. What this decision governs is which consent
  state gates the *next outbound* export.

## Consequences

* Under a consent-requiring pack, a cluster mixing consent states is withheld
  whole rather than exported under its survivor's grant. Withheld counts in
  `run_summary.json`, `cutover_withheld.csv`, and the aggregate summary will
  rise for organizations whose sources disagree about consent. That is a real
  change in output, and pilots should be told to expect it.
* `Consent.most_restrictive` is the single place this rule lives. Any future
  path that builds a merged record must call it rather than reaching for a
  member's `consent` attribute.
* Splitting a merge restores the original states by construction: merging
  derives a new lifecycle and never edits the member records, and the repair
  and correction paths re-read those records from the source batch
  (`tests/test_pipeline.py`). No unmerge migration is needed.
* Open, and deliberately not decided here: `repair.plan_split` writes a local
  plan whose manual instructions tell an operator to create one destination
  record per split member, without consulting each member's current consent.
  Under this decision a cluster written by a consent-requiring run had every
  member consented at write time, so the exposure is a consent that lapsed or
  was revoked between the write and the repair. Whether the plan should label
  each split record's consent state, and whether a lapsed one should block the
  instruction, is a maintainer decision recorded in
  `docs/BACKLOG-TRIAGE.md`; it changes `REPAIR_PLAN_SCHEMA_VERSION`.
* Revisit if a recipe ever maps a revocation date, if a pilot organization
  reports that the withheld volume makes the tool unusable in practice, or if
  OVW or NNEDV publish guidance that addresses record merges directly.

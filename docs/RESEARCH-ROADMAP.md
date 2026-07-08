# Research Roadmap

A research-backed, persona-derived backlog for constituent-reconciler. It is the
synthesis half of [`USER-RESEARCH.md`](./USER-RESEARCH.md), the synthetic persona
panel assembled 2026-06-30.

> [!NOTE]
> **This complements `docs/ROADMAP.md`; it does not replace it.** The canonical
> roadmap owns the shipped-phase history (v0.1 through v0.7), the architecture,
> the eval and quality plan, the metrics ledger, and the v1.0 gate. This file
> takes the gaps that document already names plus the ones the persona panel
> surfaced, triages them by who is hurt and how much it costs, and sequences them.
> Where an item independently triangulates something already in `docs/ROADMAP.md`,
> the audits, or the open-questions list, it is tagged **[corroborates …]**:
> triangulation from a second method is signal, not duplication. Items the
> existing docs do not cover are tagged **[NET-NEW]**. No feature is invented; the
> "values today" in the panel and the remediation targets here map only to landed
> v0.7 surfaces and to gaps the docs already record as open.

Personas referenced by ID from the
[roster](./USER-RESEARCH.md#persona-roster): A1 Rosa (intake), A2 Denise
(reviewer), A3 Marcus (case manager), A4 Walter (volunteer), B1 Priya (ops/DBA),
B2 Tomás (CRM consultant), C1 Aisha (IT/security), C2 Daniel (legal), C3 Karen
(ED), D1 J. (survivor / data subject), D2 Andre (multi-program constituent /
data subject), E1 Grace (funder), F1 Lin (DPG reviewer), F2 Chelsea (maintainer).

## Framing

The product's strategy, set in `CLAUDE.md` and `docs/ROADMAP.md`, is to ship a
narrow chain deeply: opinionated orchestration, a non-technical review queue, and
a privacy posture a shelter can legally use. The persona panel does not overturn
that. It sharpens it in one direction. Most of the highest-value work left is not
new capability; it is finishing and proving what already ships, because four
different governing personas (legal, IT, funder, DPG reviewer) independently ask
for the same evidence, and three operating personas (reviewer, case manager,
constituent) orbit the same wrong-merge fear the eval is already built around.

The sequencing rule inherited from `docs/ROADMAP.md` still holds: the
differentiator and the privacy mode first, the risky and the breadth items after.
This file's additional rule: prefer the items that turn an asserted claim into a
tested or shipped one, since that is what every assurance persona is waiting on
and what the 1.0 tag is gated against.

## Research basis and evidence

The backlog is grounded in the sources below, all accessed 2026-06-30. They are
the same set cited in [`USER-RESEARCH.md`](./USER-RESEARCH.md#method), grouped
here by the decision each one informs.

- **The problem is real and the integration is the unlock.** Nonprofit CRM data
  pain and duplicate-record cost:
  [Insycle](https://blog.insycle.com/nonprofit-data-management),
  [CCS Fundraising](https://www.ccsfundraising.com/insights/nonprofit-data-management/),
  [FAMCare on reporting pain](https://www.famcare.net/the-top-5-nonprofit-reporting-pain-points-and-how-to-fix-them-before-2026/).
  The stranded-without-integration finding:
  [Stanford Legal Design Lab / Justice Innovation](https://justiceinnovation.law.stanford.edu/legal-aid-intake-screening-ai/)
  (about 90% agreement with human eligibility decisions, "the prototype worked in
  a standalone environment" without case-system integration). This is the
  evidence behind E1 (HSDS/HMIS outputs), E2 (comparable-database export), E3
  (more connectors), E5 (CRM dedupe-rule cooperation).
- **Record linkage is solved art; the contribution is orchestration and defaults.**
  [Science Advances, "(Almost) all of entity resolution"](https://www.science.org/doi/10.1126/sciadv.abi8021);
  [Coleridge Initiative record-linkage chapter](https://textbook.coleridgeinitiative.org/chap-link.html);
  [Splink / UK MoJ on GOV.UK](https://www.gov.uk/government/publications/joined-up-data-in-government-the-future-of-data-linking-methods/splink-mojs-open-source-library-for-probabilistic-record-linkage-at-scale);
  [Robin Linacre on EM for unsupervised training](https://www.robinlinacre.com/em_intuition/).
  The Fellegi-Sunter m/u and threshold model is why the false-merge rate is the
  gated asymmetry; this underpins R6 (metric targets), R11 (match rationale),
  R10 (calibration gate).
- **CASS is a USPS certification with hard data requirements; "CASS-style" is the
  honest label.** [USPS PostalPro CASS](https://postalpro.usps.com/certifications/cass);
  [USPS Publication 28](https://pe.usps.com/cpim/ftp/pubs/pub28/pub28.pdf);
  [libpostal](https://github.com/openvenues/libpostal). Behind E6 (libpostal path
  and position-sensitive matching) and the bias work in R5.
- **CRM-native dedupe is a separate layer that can fight an external-id upsert.**
  [CiviCRM dedupe rules](https://civicrm.org/blog/spidersilk/understanding-civicrm-dedupe-rules)
  (unsupervised rules fire on import and should be defined narrowly);
  [Salesforce NPSP duplicate detection and contact merge](https://help.salesforce.com/s/articleView?id=sfdo.configure_duplicate_detection_and_npsp_contact_merge.htm&language=en_US&type=5)
  (NPSP matches on fuzzy first name, exact last name, exact email). Behind E5 and
  R7.
- **VAWA/FVPSA confidentiality, cross-checked against more than two reputable
  sources.** [NNEDV Safety Net, "Confidentiality in VAWA, FVPSA, VOCA"](https://www.techsafety.org/confidentiality-in-vawa-fvpsa)
  and [NNEDV Safety Net, "Comparable Database 101"](https://www.techsafety.org/comparable-database-101)
  both state PII must not be disclosed "regardless of whether the information has
  been encoded, encrypted, hashed, or otherwise protected" and that VSPs must use
  a separate comparable database, sharing only non-identifying aggregate data with
  the CoC; corroborated by the [HUD HMIS Comparable Database Manual](https://files.hudexchange.info/resources/documents/HMIS-Comparable-Database-Manual.pdf)
  (which also requires individual survivor data to be routinely destroyed once no
  longer needed) and the primary statute at
  [34 U.S.C. § 12291](https://www.law.cornell.edu/uscode/text/34/12291) and
  [42 U.S.C. § 10406](https://www.law.cornell.edu/uscode/text/42/10406). Behind
  R8 (retention/destruction model), E2 (comparable-database export), and the DV
  invariants the product already enforces. **Jurisdiction note:** these are U.S.
  federal obligations; they vary by funding stream (VAWA, FVPSA, VOCA differ) and
  state law adds more. The destruction and consent specifics an adopting
  organization must follow are theirs and their counsel's to determine, not this
  project's to assert.
- **The aggregate suppression bright line.** [CMS Cell Size Suppression Policy
  (ResDAC)](https://resdac.org/articles/cms-cell-size-suppression-policy): the
  under-11 cell rule the DV pack models, which `docs/RESPONSIBLE-TECH-AUDITS.md`
  correctly flags as the CMS rule and not a DV mandate.
- **Output interoperability targets.** [Open Referral HSDS](https://docs.openreferral.org/en/latest/hsds/overview.html)
  for community-resource and human-services data shapes. Behind E1.
- **The bar the DPG reviewer applies.** [Digital Public Goods Standard (nine
  indicators)](https://www.digitalpublicgoods.net/standard) and the
  [DPGA registry](https://www.digitalpublicgoods.net/registry) nomination
  process. Behind R3 (supply chain), R5 (bias), E10 (registry submission).

## Remediation backlog (finish and prove what already ships)

Priority: **P0** now, **P1** next, **P2** soon, **P3** opportunistic. Effort:
**S** about an afternoon, **M** a day or two, **L** a week or more.

| ID | Remediation | Personas | Pri | Effort | Evidence / tag |
| --- | --- | --- | --- | --- | --- |
| R1 | **Run the axe audit and screen-reader walkthrough, and add EN/ES review-UI copy** so the 1.0 accessibility gate can close | A2, A4, D1, C2 | P0 | M | `docs/ROADMAP.md` v0.7 "still open"; `RESPONSIBLE-TECH-AUDITS.md` accessibility TODO; README EN/ES parity. **[corroborates ROADMAP v0.7 / audits]** |
| R2 | **Wire RFC 3161 trusted timestamping to a real TSA** (today the seam defaults to the local clock) so provenance timestamps are independently anchored | E1, C2, C1, F2 | P1 | M | `docs/ROADMAP.md` v0.2; CHANGELOG 0.5 "not yet". **[corroborates ROADMAP v0.2]** |
| R3 | **Ship the supply-chain hardening**: SBOM, Sigstore-signed releases, SHA-pinned actions, OIDC, secret scanning | C1, F1, E1 | P1 | M | README Standards; `DPG-CONFORMANCE.md` indicator 8 "partially landed"; metrics ledger; `RESPONSIBLE-TECH-AUDITS.md` security. **[corroborates ROADMAP / DPG note]** |
| R4 | **Commit the threat model** for the untrusted-PDF/scan parse path | C1, F1 | P1 | S | `RESPONSIBLE-TECH-AUDITS.md` security TODO. **[corroborates audits]** |
| R5 | **Measure and report bias by name class** (transliterated, hyphenated, non-Western order) **and address class** (rural/informal) on the eval fixtures, with mitigations | C2, F1, D2, A2 | P1 | M | `RESPONSIBLE-TECH-AUDITS.md` bias TODO; `DPG-CONFORMANCE.md` indicator 9 residual risk. **[corroborates audits / DPG note]** |
| R6 | **Fill the metrics-ledger targets** (false-merge threshold, coverage floors, kappa drift gate) that are currently TBD. ✅ Implemented 2026-06-30 (working tree, uncommitted) | F2, F1, E1, B1 | P1 | S | `docs/ROADMAP.md` metrics ledger "TBD". **[corroborates ROADMAP]** |
| R7 | **Record the end-to-end CiviCRM demo** of messy input landing in a running instance, and write email/phone through dedicated CiviCRM entities instead of the API v4 join-field shorthand | B1, B2, C3 | P1 | M | `docs/ROADMAP.md` v0.2 "still open". **[corroborates ROADMAP v0.2]** |
| R8 | **Define the retention and destruction model per policy pack**, plus the data-flow map; the DV pack should support routine destruction of individual records | C2, D1, E1 | P1 | M | `RESPONSIBLE-TECH-AUDITS.md` privacy TODO; HUD Comparable Database Manual (routine destruction). **[corroborates audits + NET-NEW destruction detail]** |
| R9 | **Publish a model card and data card** for the optional Bedrock extraction seam | C2, F1, C1 | P2 | S | `RESPONSIBLE-TECH-AUDITS.md` transparency TODO. **[corroborates audits]** |
| R10 | **Wire `cohen_kappa()` into the eval report** as the LLM field-judge calibration gate, fail-closed on drift | F2, F1 | P2 | M | `docs/ROADMAP.md` v0.3 and metrics ledger; CHANGELOG 0.3 "not yet wired". **[corroborates ROADMAP]** |
| R11 | **Show a plain-language match rationale beside each review pair** ("agree on last name and address, differ on date of birth") so the reviewer is not deciding on source spans alone. ✅ Implemented 2026-06-30 (working tree, uncommitted) | A2, A4, A3 | P1 | M | README review surface; `CLAUDE.md` "no jargon" mandate; Fellegi-Sunter agreement pattern. **[NET-NEW]** |

## Expansion backlog (new capability)

| ID | Expansion | Personas | Pri | Effort | Evidence / tag |
| --- | --- | --- | --- | --- | --- |
| E1 | **HMIS comparable-database and Open Referral HSDS output mappings** so funder reports come out of the same pipeline | E1, B1, C3, F1 | P1 | L | `docs/ROADMAP.md` open question 4; [HSDS](https://docs.openreferral.org/en/latest/hsds/overview.html); HUD Comparable Database Manual. **[corroborates open question 4]** |
| E2 | **One-command comparable-database export profile** for VSPs (a CoC-shaped aggregate report) built on the DV pack's `aggregate_summary.json` | D1, E1, C2, C3 | P1 | M | DV pack aggregate; HUD Comparable Database Manual. **[corroborates DV pack + NET-NEW report shape]** |
| E3 | **More connectors**: Apricot, Airtable, Google Sheets, generic webhook, on the existing `Connector` interface | B1, B2, C3 | P2 | L | README "Salesforce, Airtable, Sheets, CSV and webhook to follow"; ROADMAP architecture diagram. **[corroborates ROADMAP]** |
| E4 | **Reviewer audit trail and optional two-person review for the DV pack**: record who decided each pair, allow a second-reviewer requirement on sensitive merges | C2, C1, D1, B1 | P2 | M | Provenance log records writes only today. **[NET-NEW]** |
| E5 | **Documented CRM dedupe-rule cooperation**: a CiviCRM unsupervised-rule and an NPSP matching-rule configuration that work with the external-id upsert instead of fighting it. ✅ Implemented 2026-07-01 (working tree, uncommitted), see `docs/CRM-DEDUPE-COOPERATION.md` | B2, B1 | P2 | M | `docs/ROADMAP.md` open question 2; [CiviCRM dedupe](https://civicrm.org/blog/spidersilk/understanding-civicrm-dedupe-rules); [NPSP duplicate management](https://help.salesforce.com/s/articleView?id=sfdo.configure_duplicate_detection_and_npsp_contact_merge.htm&language=en_US&type=5). **[corroborates open question 2 + NET-NEW deliverable]** |
| E6 | **Harden and test the libpostal backend in CI, and add position-sensitive address matching** to retire the documented position-insensitive simplification | A1, D2, C2 | P2 | M | ADR 0004 / `docs/ROADMAP.md` v0.4; [libpostal](https://github.com/openvenues/libpostal). **[corroborates ROADMAP v0.4 + NET-NEW]** |
| E7 | **Un-merge / reversibility path** so a wrong merge found after the write can be reversed, directly attacking the gated harm | A3, B1, D2, C3 | P2 | L | README/ROADMAP "a false merge is sometimes irreversible". **[NET-NEW]** |
| E8 | **Pilot-readiness adoption kit**: a short "bring your own org" guide (map your spreadsheet, pick a pack, dry-run, read the eval) to drive the real-organization adoption the 1.0 tag is gated on. ✅ Implemented 2026-06-30 (working tree, uncommitted), see `docs/ADOPTION-KIT.md` | C3, B1, B2, F2 | P1 | M | `docs/ROADMAP.md` v1.0 adoption gate; `CLAUDE.md` audiences. **[NET-NEW]** |
| E9 | **Incremental re-resolution and a progress indicator** so a re-run on a large existing set only re-resolves changed records | B1, A4 | P3 | L | README upsert idempotency (re-runs update, not duplicate). **[NET-NEW]** |
| E10 | **Submit a formal DPGA registry nomination** (the conformance note is a self-assessment, not a submission) | F1, F2, C3 | P3 | S | `DPG-CONFORMANCE.md` "not a registry submission"; [DPGA registry](https://www.digitalpublicgoods.net/registry). **[corroborates DPG note + NET-NEW action]** |

## Sequenced roadmap

These slot under `docs/ROADMAP.md`'s existing v1.0 milestone; none reorders a
shipped phase. They are the persona-prioritized cut of what stands between v0.7
and a defensible 1.0, plus the breadth items that come after.

### Now (the named 1.0 blockers and the differentiator's finish)
- **R1** accessibility audit, screen-reader walkthrough, EN/ES review-UI copy.
- **R3** supply-chain hardening (start with SHA-pinned actions and an SBOM, the
  cheapest credibility wins).
- **R6** fill the metrics-ledger targets so the gates are numerically committed.
- **R11** plain-language match rationale in the review queue.
- **E8** pilot-readiness adoption kit.

### Next (prove the claims, integrate the outputs)
- **R2** RFC 3161 timestamping to a real TSA.
- **R4** committed threat model.
- **R5** bias measured and reported by name and address class.
- **R7** recorded CiviCRM demo and dedicated email/phone entities.
- **R8** retention and destruction model per pack, with the data-flow map.
- **E1** HMIS and HSDS output mappings.
- **E2** comparable-database export profile.

### Later (breadth and depth once the core is proven)
- **R9** extraction-seam model and data cards.
- **R10** calibration gate wired into the eval.
- **E3** more connectors.
- **E4** reviewer audit trail and two-person DV review.
- **E5** CRM dedupe-rule cooperation guidance.
- **E6** libpostal hardening and position-sensitive matching.
- **E7** un-merge / reversibility path.
- **E9** incremental re-resolution.
- **E10** DPGA registry submission.

## Recommended first sprint

The triage and the existing roadmap converge on the same starting line: the work
that closes the 1.0 gate while sharpening the differentiator, weighted toward
items that turn an asserted claim into a tested or shipped one.

1. **R1 (accessibility) and R11 (match rationale) together.** The review queue is
   the product, and these two finish it for its actual users (A2, A4) and its data
   subjects' interests (D1, D2). R11 is the single highest-leverage reviewer
   change: it removes the "I am guessing" feeling that drives a wrong approval,
   the exact error the whole eval is built to gate.
2. **R6 (fill the metrics ledger).** An afternoon of work that lets every
   assurance persona (E1, F1, F2) and the ops lead (B1) point at a committed
   false-merge threshold instead of "TBD". It makes the gates real and is a
   prerequisite for the R10 calibration gate.
3. **R3 (supply-chain quick wins).** SHA-pin the actions and generate an SBOM
   first; they are small and they move DPG indicator 8 and Aisha's (C1) review
   from "partial" toward "met".
4. **E8 (pilot-readiness kit).** The 1.0 tag is gated on real-organization
   adoption, and nothing in the repo yet helps a Karen (C3) or a Priya (B1) bring
   their own organization on. This is the item that unblocks the gate itself.

Bundle the afternoon-sized wins alongside: **R4** (threat model) and **R9**
(extraction-seam cards) reuse work already scoped in the audits.

## Traceability matrix (persona to findings)

| Persona | Remediations | Expansions |
| --- | --- | --- |
| A1 Rosa (intake) | R1 | E6 |
| A2 Denise (reviewer) | R1, R5, R11 | — |
| A3 Marcus (case manager) | R11 | E7 |
| A4 Walter (volunteer) | R1, R11 | E9 |
| B1 Priya (ops/DBA) | R6, R7 | E1, E3, E5, E8, E9 |
| B2 Tomás (CRM consultant) | R7 | E3, E5, E8 |
| C1 Aisha (IT/security) | R2, R3, R4, R9 | E4 |
| C2 Daniel (legal) | R1, R5, R8, R9 | E2, E4, E6 |
| C3 Karen (ED) | R7 | E1, E2, E3, E7, E8, E10 |
| D1 J. (survivor / data subject) | R1, R8 | E2, E4 |
| D2 Andre (constituent / data subject) | R5, R11 | E6, E7 |
| E1 Grace (funder) | R2, R3, R6, R8 | E1, E2 |
| F1 Lin (DPG reviewer) | R3, R4, R5, R6, R9, R10 | E10 |
| F2 Chelsea (maintainer) | R2, R6, R10 | E8, E10 |

## Validate with real users, and the risks of not

This backlog is derived from a synthetic panel. Before any item past the first
sprint is committed, it should be checked against the real cast the 1.0 gate
already requires. The interviews most worth buying:

- **A survivor-services advocate and a CoC data lead**, to test R8 (retention and
  destruction) and E2 (comparable-database export) against an actual program's
  obligations. This is the highest-stakes area and the least safe to synthesize:
  a generated persona cannot weigh a survivor's real risk, and the legal claims
  are jurisdiction- and funding-stream-dependent. Get this one wrong and the harm
  is to the exact people the DV pack exists to protect.
- **An accidental DBA at a small human-services nonprofit**, to test whether the
  CLI-and-recipe on-ramp (A4, E8) is actually crossable without a developer, and
  whether the reporting-hours pain is the one this solves.
- **A CiviCRM or Salesforce implementation consultant**, to test E5 (dedupe-rule
  cooperation) and E3 (connector priority) against what real installs hit. The
  risk of skipping this is building the wrong connector next.
- **A DPG reviewer or DPGA evaluator**, to test whether R3 and R5 close indicator
  8 and indicator 9 for a real registry assessment (E10) rather than the
  self-assessment.

The general risk of acting on this document alone: it over-represents the
author's model, it cannot size demand, and it will miss the needs only a real
user surprises you with. The specific risk in this domain: a plausible-sounding
confidentiality or retention design that is subtly wrong is worse than none,
because organizations may rely on it. Every legal and confidentiality item here
keeps the README's standing caveat, that this is a reference implementation and
not legal advice, and an adopting organization needs its own review against its
own obligations and its own counsel.

## Honest limits

- The personas are synthetic and the priorities are a demand-times-leverage read
  from imagined interviews, not measured demand. Treat **Pri** as a hypothesis.
- The **[corroborates …]** tags mean an item triangulates something already in
  `docs/ROADMAP.md`, the audits, or the open-questions list. That is a signal the
  item is real, not a claim that the panel discovered it. The genuinely panel-
  surfaced items are the **[NET-NEW]** ones: R11 (reviewer rationale), R8's
  destruction detail, E2's report shape, E4 (reviewer audit trail), E7 (un-merge),
  E8 (adoption kit), E9 (incremental re-resolution).
- Effort and priority are the maintainer's estimates, not commitments, in the
  same spirit as `docs/ROADMAP.md`: "dates are intentions, not promises."
- Nothing here invents a feature or a fact. Every "values today" reference points
  at a landed v0.7 surface, and every remediation targets a gap the docs already
  record as open or that a cited source establishes.
- The legal and confidentiality claims are U.S. federal and vary by jurisdiction
  and funding stream. They are cited to primary statute and to NNEDV and HUD
  guidance, cross-checked, and still subject to an adopting organization's own
  counsel.

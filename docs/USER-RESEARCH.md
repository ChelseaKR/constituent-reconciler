# User Research — Synthetic Personas and Simulated Interviews

> [!WARNING]
> **These personas and interviews are synthetic.** They were generated as a
> structured brainstorming device, not conducted with real people. No real user
> said any of this. The document role-plays the full cast around the
> constituent-reconciler pipeline so the product can be pressure-tested from many
> angles at once. It is **not** evidence of demand and does **not** substitute for
> real discovery. Treat every "quote" as a hypothesis to validate, not a finding.
> This mirrors how the project labels its fixtures: seeded synthetic data with
> zero real PII (see `eval/report.md` and `tests/fixtures/`).
>
> The honest next step is real interviews with the survivor-serving and
> human-services organizations the README names as the 1.0 adoption gate
> (`docs/ROADMAP.md`, v1.0). **Last assembled: 2026-06-30.**

## Why do this at all

This tool sits between a stack of intake PDFs and the case system a nonprofit
already runs, and it touches some of the most sensitive data a small organization
holds. A single author cannot hold every stakeholder's stake in their head at
once. Role-playing the cast surfaces gaps the author misses and forces the
question "who is each feature for?" for a chain whose users range from a
front-desk volunteer to a survivor whose confidentiality is a legal obligation.

The synthesis and the derived backlog live in a companion file,
[`RESEARCH-ROADMAP.md`](./RESEARCH-ROADMAP.md), so this document stays a record of
the interviews and does not turn into a wishlist. There, each finding is tagged
**[corroborates …]** where it independently triangulates an item already in
`docs/ROADMAP.md` or the audits, and **[NET-NEW]** where the panel surfaced it.

## How to read a persona

Each card compresses a simulated interview to five lines: the **goal**, **what
they would value in what already ships** (mapped only to real, landed features),
**where they would get stuck**, **what they would want next**, and **the one
thing that makes them adopt or walk**. Where a card names a feature, it names one
that exists in v0.7 unless it is explicitly a "wants next."

## Method

- **Sampling frame.** Everyone whose work or whose data passes through the chain:
  the people who run intake and review (front desk, the review-queue reviewer,
  case manager, volunteer), the people who operate and administer it (data/ops
  manager, CRM consultant), the people who carry its risk and govern it
  (IT/security, legal counsel, executive director), the **data subjects** whose
  records the pipeline writes (a domestic-violence survivor whose confidentiality
  the DV pack must protect, and an ordinary constituent served across several
  programs), the people who fund and assure it (a grant-compliance officer, a
  DPG/open-source reviewer), and the owner who maintains it.
- **Protocol.** For each persona: a goal, a walkthrough of the real surfaces they
  would touch (`constituent-reconcile run`, `constituent-reconcile review`, the review queue CSV, the
  provenance log, the DV pack, the CRM export files, the eval report), what would
  work, where they would stall, and an open "what would make this a yes" prompt.
  Data-subject personas are interviewed about what they would need to **trust**
  the handling of their record, since they do not operate the tool.
- **Research basis.** The protocol and the frictions are grounded in published
  evidence about this problem space, not invented. The load-bearing sources, all
  accessed 2026-06-30:
  - Nonprofit CRM data pain and duplicate records:
    [Insycle, "Solving Nonprofit Industry CRM Data Management Challenges"](https://blog.insycle.com/nonprofit-data-management);
    [CCS Fundraising, "Nonprofit Data Management"](https://www.ccsfundraising.com/insights/nonprofit-data-management/);
    [FAMCare, "Top 5 Nonprofit Reporting Pain Points"](https://www.famcare.net/the-top-5-nonprofit-reporting-pain-points-and-how-to-fix-them-before-2026/).
  - The integration gap that strands accurate intake tools:
    [Stanford Legal Design Lab / Justice Innovation, "Legal Aid Intake Screening AI"](https://justiceinnovation.law.stanford.edu/legal-aid-intake-screening-ai/)
    (about 90% agreement with human eligibility decisions, stalled because "the
    prototype worked in a standalone environment" without integration into the
    case system).
  - Record linkage, deterministic vs probabilistic, and the Fellegi-Sunter model:
    [Science Advances, "(Almost) all of entity resolution"](https://www.science.org/doi/10.1126/sciadv.abi8021);
    [Coleridge Initiative, "Record Linkage" (Big Data and Social Science)](https://textbook.coleridgeinitiative.org/chap-link.html);
    [Splink (UK Ministry of Justice), GOV.UK](https://www.gov.uk/government/publications/joined-up-data-in-government-the-future-of-data-linking-methods/splink-mojs-open-source-library-for-probabilistic-record-linkage-at-scale).
  - USPS address standardization and what CASS certification actually requires:
    [USPS PostalPro, "CASS"](https://postalpro.usps.com/certifications/cass);
    [USPS Publication 28, Postal Addressing Standards](https://pe.usps.com/cpim/ftp/pubs/pub28/pub28.pdf);
    [libpostal](https://github.com/openvenues/libpostal).
  - CRM data models and native dedupe:
    [CiviCRM, "Understanding CiviCRM Dedupe Rules"](https://civicrm.org/blog/spidersilk/understanding-civicrm-dedupe-rules);
    [Salesforce, "Configure Duplicate Detection and NPSP Contact Merge"](https://help.salesforce.com/s/articleView?id=sfdo.configure_duplicate_detection_and_npsp_contact_merge.htm&language=en_US&type=5).
  - VAWA/FVPSA confidentiality and the comparable-database posture, cross-checked
    against more than one reputable source:
    [NNEDV Safety Net, "Confidentiality in VAWA, FVPSA, and VOCA"](https://www.techsafety.org/confidentiality-in-vawa-fvpsa);
    [NNEDV Safety Net, "Comparable Database 101"](https://www.techsafety.org/comparable-database-101);
    [HUD, HMIS Comparable Database Manual](https://files.hudexchange.info/resources/documents/HMIS-Comparable-Database-Manual.pdf);
    primary statute at [34 U.S.C. § 12291](https://www.law.cornell.edu/uscode/text/34/12291)
    and [42 U.S.C. § 10406](https://www.law.cornell.edu/uscode/text/42/10406).
  - Aggregate suppression bright line:
    [CMS Cell Size Suppression Policy (ResDAC)](https://resdac.org/articles/cms-cell-size-suppression-policy).
  - Output interoperability targets:
    [Open Referral HSDS](https://docs.openreferral.org/en/latest/hsds/overview.html).
  - The bar the open-source reviewer applies:
    [Digital Public Goods Standard](https://www.digitalpublicgoods.net/standard).

The full citation set and how each maps to a backlog item is in
[`RESEARCH-ROADMAP.md`](./RESEARCH-ROADMAP.md).

## Persona roster

Fourteen synthetic personas across six groups. Names are invented; roles model
real audience segments.

| # | Persona | Group | Primary goal | Top friction |
| --- | --- | --- | --- | --- |
| A1 | **Rosa** — front-desk intake worker | Use & Review | Get a paper/PDF intake into the system once, correctly | Re-types every form; same client entered three ways |
| A2 | **Denise** — the review-queue reviewer (non-technical) | Use & Review | Decide each uncertain pair without guessing | Knows the people, not the math; wants the "why" |
| A3 | **Marcus** — case manager | Use & Review | Trust that a client's history is whole and not cross-wired | Fear of a wrong merge exposing one client to another |
| A4 | **Walter** — volunteer running the batch | Use & Review | Run the review queue a few hours a week with no training | CLI and recipe files are unfamiliar terrain |
| B1 | **Priya** — data/ops manager (accidental DBA) | Operate & Administer | Cut the reporting-cycle reconciliation hours | Idempotent re-runs, schema drift, no labeled pairs to give |
| B2 | **Tomás** — CiviCRM/Salesforce consultant | Operate & Administer | Drop this into a client's existing CRM cleanly | CRM-native dedupe rules fighting the external-id upsert |
| C1 | **Aisha** — IT / security lead | Protect & Govern | Approve the tool without adding attack surface | Untrusted PDF parse path; no committed threat model |
| C2 | **Daniel** — privacy / legal counsel | Protect & Govern | Confirm the DV claims hold under audit | "Reference implementation, not legal advice"; retention model |
| C3 | **Karen** — executive director | Protect & Govern | Free staff time without a confidentiality incident | Can she defend adoption to a board and a funder |
| D1 | **"J."** — DV survivor, as data subject | Data Subjects | Be served without her location reaching a shared system | Cannot see the safeguards; must trust them |
| D2 | **Andre** — constituent across several programs, as data subject | Data Subjects | Not be split into duplicates or wrongly merged | No visibility, no recourse if his record is wrong |
| E1 | **Grace** — funder / grant-compliance officer | Fund & Assure | Get clean, defensible aggregate reporting | Comparable-database and HMIS-shaped outputs not there yet |
| F1 | **Lin** — DPG / open-source reviewer | Fund & Assure | Judge whether this is a real digital public good | Supply-chain items and registry submission still partial |
| F2 | **Chelsea** — owner / maintainer | Build & Sustain | Reach 1.0 honestly, on adoption not a release script | Metrics-ledger targets are TBD; gates not all wired |

---

## Group A — Use and Review (closest to the intake and the decision)

### A1 — Rosa, front-desk intake worker
- **Goal:** turn a folder of intake PDFs and a sign-in spreadsheet into records that land in the case system once, without re-typing.
- **Values today:** folder-based ingestion that routes `.pdf` through the offline pdfplumber extractor and `.csv` through the structured reader; the source-span pointer (filename, page, bounding box) on every extracted field so a value is traceable to where it was read; the CASS-style standardizer so "123 North Main Street" and "123 N Main St" stop reading as two people.
- **Gets stuck:** her PDFs are scans, and pages with few words or garbled text score below the 0.5 confidence threshold and route to review; non-Western and hyphenated names on her caseload are exactly the ones the README's bias section flags as where matching degrades.
- **Wants next:** Spanish intake copy at parity (the README commits to it but the UI copy is not there yet); a clearer signal of which scans failed extraction and why.
- **Adopts if:** it removes the second typing of every form. **Walks if:** it mangles the names of the clients she serves and she has to re-check all of them by hand.

### A2 — Denise, the review-queue reviewer (the product's primary user)
- **Goal:** step through the uncertain pairs and decide approve, correct, or reject, without understanding probabilistic matching.
- **Values today:** `constituent-reconcile review` opens a local browser queue showing the two records side by side with their source spans; the fail-closed gate means anything below threshold reached her instead of merging silently; the WCAG 2.2 AA structure (real comparison table, status by text and symbol not colour alone, `A`/`R`/`J`/`K` keyboard shortcuts, works with no JavaScript); each verdict saves to `decisions.json` so she can stop and resume.
- **Gets stuck:** the queue shows her the two records but not, in words she can act on, *why* the matcher thought they might be the same person or which fields disagreed; she is deciding on the source spans and her own judgment alone.
- **Wants next:** a plain-language "these agree on last name and address, differ on date of birth" line beside each pair; the screen-reader walkthrough and Spanish copy that are still open before the 1.0 accessibility gate.
- **Adopts if:** she can clear a queue confidently in an afternoon. **Walks if:** she feels she is guessing, because a wrong approval is the expensive error and she knows it.

### A3 — Marcus, case manager
- **Goal:** trust that a client's history is whole and that no two clients have been cross-wired into one record.
- **Values today:** the asymmetry baked into the design, that a false merge is treated as the worse, sometimes irreversible error and is the gated metric (`eval/report.md` shows a 0% false-merge rate on the demo fixtures); the consent gate that withholds any non-consented record before any write.
- **Gets stuck:** when a wrong merge does slip through and is caught after the write, there is no documented un-merge path; the provenance log proves what happened but does not reverse it.
- **Wants next:** a reversible-merge or un-merge path so a mistake found later is recoverable; reviewer accountability on who approved a given pair.
- **Adopts if:** the merge history is auditable and correctable. **Walks if:** an irreversible bad merge can reach a client's file with no way back.

### A4 — Walter, volunteer running the batch
- **Goal:** run the review queue a few hours a week with no technical training, the way the project says a volunteer can.
- **Values today:** `constituent-reconcile review` is a browser window with no spreadsheet and no jargon; the keyboard-only flow; the resume-from-`decisions.json` behavior so a half-finished queue is not lost.
- **Gets stuck:** getting *to* the review queue still means a recipe TOML file and a CLI invocation that a volunteer did not write; the Docker path helps but is still a command line.
- **Wants next:** a gentler on-ramp from "here is a folder of intake" to "here is your queue"; the Spanish UI.
- **Adopts if:** someone sets up the recipe once and he only ever touches the browser queue. **Walks if:** every session starts with a command he does not understand.

---

## Group B — Operate and Administer (run the pipeline, own the CRM)

### B1 — Priya, data/ops manager and accidental DBA
- **Goal:** cut the staff hours spent reconciling disconnected tools and name mismatches each reporting cycle (the kind of 40-to-80-hour drain the README cites).
- **Values today:** the Splink matcher arrives pre-tuned with m and u defaults so she supplies no labeled pairs and no blocking rules; CRM write-back is an upsert keyed on an external id, so a re-run updates rather than duplicates; `constituent-reconcile schema` declares the config, connector, and report schema versions she is building against; `--dry-run` shows what a write would do first.
- **Gets stuck:** the metrics-ledger targets in `docs/ROADMAP.md` are still TBD, so she cannot point at a committed false-merge threshold when her director asks "how wrong can it be"; there is no recorded end-to-end demo of messy input landing in a running CiviCRM instance to show the team.
- **Wants next:** filled-in metric targets; HMIS comparable-database and Open Referral HSDS output shapes so her funder reports come out of the same pipeline; an incremental re-run that only re-resolves changed records on a large existing set.
- **Adopts if:** a cycle's reconciliation drops from days to an afternoon. **Walks if:** she cannot defend the error rate or the re-run duplicates contacts.

### B2 — Tomás, CiviCRM/Salesforce implementation consultant
- **Goal:** drop this into a client's existing CRM without breaking the dedupe and merge behavior the CRM already has.
- **Values today:** two write paths, an offline import-ready CSV mapped to the CRM's own import schema (NPSP Contact columns, CiviCRM import columns) plus an external-id column for an idempotent CRM-side upsert, and a live API push as the explicit opt-in; the connector interface in `connectors/base.py` that makes a new destination one module; the shared column map so the file export and the API payload cannot drift.
- **Gets stuck:** the CRM's native dedupe is its own layer. CiviCRM's unsupervised rule fires on import and NPSP's matching rules fire on contact creation; without guidance, those can fight or re-merge against the external-id upsert (`docs/ROADMAP.md` lists the dedupe-rule interaction as an open question).
- **Wants next:** a documented CiviCRM unsupervised-rule and NPSP matching-rule configuration that cooperates with the external-id key; more connectors (Apricot, Airtable, Sheets, webhook) that the README names as "to follow."
- **Adopts if:** he can install it at three clients without a custom integration each time. **Walks if:** it duplicates contacts inside the CRM despite the upsert.

---

## Group C — Protect and Govern (carry the risk, sign off)

### C1 — Aisha, IT / security lead
- **Goal:** approve the tool without adding attack surface or a phone-home path.
- **Values today:** deterministic offline default with the cloud seam optional and policy-gated; the review server binds loopback only and inlines all assets with no external fetch; under the DV pack a non-loopback bind is refused fail-closed; the append-only BLAKE2b provenance chain that `constituent-reconcile verify` checks; one-command Docker self-host.
- **Gets stuck:** uploaded PDFs and scans are untrusted input and the primary threat surface, and the threat model is a documented TODO in `docs/RESPONSIBLE-TECH-AUDITS.md`; the supply-chain items (SBOM, Sigstore signing, SHA-pinned actions, OIDC) are named as landing on the path to 1.0, not done.
- **Wants next:** the committed threat model for the parse path; the supply-chain hardening shipped; a model and data card for the optional Bedrock seam.
- **Adopts if:** it passes his review as offline-by-construction with a real SBOM. **Walks if:** the parser is a soft target or a release is unsigned.

### C2 — Daniel, privacy / legal counsel
- **Goal:** confirm the DV confidentiality claims hold up if a regulator or a funder audits them.
- **Values today:** the DV pack enforces four invariants as merge-blocking tests, not prose: consent required, no cloud egress, local write targets only, and aggregate suppressed sharing; each is linked to the test that enforces it and grounded in primary VAWA, FVPSA, and CMS sources in `docs/RESPONSIBLE-TECH-AUDITS.md`; the honesty corrections (the statutory verb is "disclose, reveal, or release"; revocable consent is NNEDV best practice not statute; the under-11 threshold is the CMS rule not a DV mandate) tell him the author did not overclaim.
- **Gets stuck:** the retention and destruction model per policy pack is a TODO, yet HUD's comparable-database guidance is explicit that individual survivor data must be routinely destroyed once no longer needed; the "reference implementation, not legal advice" caveat is correct but means his organization still owns the legal review.
- **Wants next:** the per-pack retention and destruction model and a data-flow map; bias measured and reported by name and address class, since disparate matching error is a fairness and a legal exposure.
- **Adopts if:** the claims are testable and the limits are stated, which they largely are. **Walks if:** any invariant is asserted in prose without a test behind it.

### C3 — Karen, executive director
- **Goal:** give staff their time back without risking a confidentiality incident that ends a grant or a reputation.
- **Values today:** the DV pack makes egress structurally impossible under that mode rather than as a policy promise; nothing merges silently; the eval report and the provenance log are artifacts she can hand to a board; the DPG conformance note frames the work in language funders recognize.
- **Gets stuck:** the 1.0 tag is honestly withheld pending real-organization adoption, so she would be an early adopter with no peer reference; she has no short "how to bring your own org onto this" path to hand her ops lead.
- **Wants next:** a pilot-readiness kit (map your spreadsheet, pick a pack, dry-run, read the eval); the comparable-database export her DV program needs for its CoC reporting.
- **Adopts if:** a small pilot is low-risk and reversible. **Walks if:** being first means she carries all the integration risk alone.

---

## Group D — Data Subjects (whose records the pipeline writes; they do not operate it)

### D1 — "J.", domestic-violence survivor, as data subject
- **Goal:** receive services without her name, address, or location reaching any shared database or outside company, because for her that is a safety matter, not a preference.
- **What the design owes her (and largely meets):** under the DV pack the cloud extraction seam is fused off at construction so no field value can leave the machine; non-local write targets are refused before any write, keeping her record on the shelter's own machine, the comparable-database posture HUD requires; her record is withheld entirely unless consent is granted, recorded by id and reason only with no field values; the only shareable artifact is an aggregate summary with small cells suppressed. These mirror the VAWA and FVPSA rule that PII must not be disclosed "regardless of whether the information has been encoded, encrypted, hashed, or otherwise protected" (corroborated across NNEDV Safety Net and the HUD Comparable Database Manual).
- **Where trust is still thin:** she cannot see any of this; she has to trust that the shelter ran the DV pack and not a default recipe; the retention and destruction model that would tell her how long her record lives is not yet defined.
- **Would trust it if:** the safeguards are the default for her program and her advocate can explain in one sentence that nothing leaves the building. **Would not if:** a misconfiguration could route her record to a network target, or consent is treated as a checkbox.

### D2 — Andre, constituent served across several programs, as data subject
- **Goal:** be one person in the system, neither split into duplicates that fragment his history nor merged into someone else's file.
- **What the design owes him (and largely meets):** the fail-closed gate sends every uncertain pair to a human rather than auto-merging; the false-merge rate is the gated metric precisely because joining him to a stranger is the irreversible harm; the CASS-style address normalization keeps his two address spellings from reading as two people.
- **Where trust is still thin:** if a wrong merge does happen there is no documented un-merge path; matching error is not evenly distributed and the README flags that transliterated, hyphenated, and non-Western names degrade, which is a fairness question for constituents like him; he has no visibility or recourse.
- **Would trust it if:** mistakes are catchable and reversible and the error rate is measured across name and address classes, not just in aggregate. **Would not if:** a silent or unrecoverable merge can rewrite his history.

---

## Group E — Fund and Assure (pay for it, vouch for it)

### E1 — Grace, funder / grant-compliance officer
- **Goal:** receive clean, defensible reporting from her grantees without compromising client confidentiality.
- **Values today:** the DV pack's aggregate summary uses CMS-style small-cell suppression with complementary suppression and true zeros preserved, which is exactly the non-identifying aggregate posture a CoC expects from a victim-service provider; the committed eval report with Wilson confidence intervals shows the grantee is measuring error, not asserting quality; schema versions are declared.
- **Gets stuck:** the output does not yet map to HMIS comparable-database client fields or Open Referral HSDS, so a grantee still hand-shapes the funder report; the RFC 3161 trusted-timestamp authority is a seam with the local clock as default, so the provenance log's timestamps are not yet independently anchored.
- **Wants next:** HMIS-shaped and HSDS-shaped outputs; a one-command comparable-database export profile; RFC 3161 timestamping wired to a real TSA.
- **Adopts if:** it standardizes what her grantees send her. **Walks if:** every grantee still produces a bespoke report.

### F1 — Lin, DPG / open-source reviewer
- **Goal:** judge whether this is a genuine digital public good or a project that says it is one.
- **Values today:** Apache-2.0; pure Python with one heavy dependency; the DPG conformance note maps all nine indicators honestly, including the qualifications; consent and non-egress are technical invariants with tests, which is a real "do no harm by design" story; the committed, regenerated eval and audits.
- **Gets stuck:** indicator 8's supply-chain items (SBOM, signed releases, SHA-pinned actions) are partially landed; the conformance note is a self-assessment and explicitly not a registry submission; the metrics-ledger targets are TBD.
- **Wants next:** the supply-chain hardening completed; a formal DPGA registry nomination; the bias measurement that indicator 9's residual-risk note promises.
- **Adopts if:** the self-assessment is backed by shipped supply-chain evidence and a registry listing. **Walks if:** the conformance note stays aspirational.

### F2 — Chelsea, owner / maintainer
- **Goal:** reach a 1.0 that means a stability promise earned by adoption, not stamped by a release script.
- **Values today:** the deliberate decision to withhold the 1.0 tag until real organizations adopt and the named surfaces hold stable for two releases; the merge-blocking gates already wired (false-merge rate, no-egress, consent); the ADRs that record why each choice was made so they are not relitigated.
- **Gets stuck:** the metrics-ledger thresholds are TBD, so some gates are structural but not yet numerically committed; the cohen_kappa calibration seam exists but is not wired into the eval report; several "still open" items (axe audit, screen-reader walkthrough, EN/ES UI copy, RFC 3161 TSA, supply chain) stand between v0.7 and the tag.
- **Wants next:** fill the metrics ledger; wire the calibration gate; close the named accessibility and supply-chain items; find the first pilot organization.
- **Adopts if:** the path to 1.0 is the honest remaining list and nothing is claimed early. **Walks if:** the project drifts toward breadth instead of finishing the narrow chain.

---

## Cross-cutting themes (what the cast agrees on)

1. **The wrong-merge fear is the spine of the whole product.** Denise (A2),
   Marcus (A3), and Andre (D2) all orbit the same anxiety the eval is built
   around: a false merge is the expensive, sometimes irreversible error. The
   design already gates it and routes uncertainty to a human, but two things are
   missing downstream, a plain-language reason beside each pair so the reviewer is
   not guessing, and an un-merge path so a mistake caught later is recoverable.
2. **"Offline by construction" is the trust anchor for the sensitive segment, and
   it largely holds.** J. (D1), Daniel (C2), Aisha (C1), and Karen (C3) all rest
   on the same structural fact, that the DV pack fuses egress off and refuses
   non-local targets rather than promising not to send data. The gap is not the
   invariants; it is the retention/destruction model and the visibility that the
   default actually ran.
3. **The integration is the unlock, exactly as the founding research said.** Priya
   (B1), Tomás (B2), and Grace (E1) confirm the Stanford finding the README cites,
   that an accurate intake tool strands without functional integration. The
   external-id upsert and the import-ready CSVs are the right shape; the open ends
   are CRM-native dedupe-rule cooperation and funder-shaped outputs (HMIS, HSDS).
4. **Prove the claims the project makes about itself.** Daniel (C2), Aisha (C1),
   Grace (E1), and Lin (F1) independently ask for the same class of evidence: the
   threat model, the supply-chain artifacts, the bias-by-class numbers, the
   RFC 3161 anchoring, and the filled metric targets. Each is named as planned in
   the docs; none is yet shipped.
5. **Every operator persona is one configuration step from the data subject's
   harm.** The distance between Rosa picking the wrong recipe and J.'s record
   reaching a network target is one TOML line. The DV pack's fail-closed posture
   is what closes that distance, which is why the default-and-visibility question
   recurs across A1, A4, C3, and D1.
6. **The differentiator is the review queue, and it is the least finished surface
   relative to its importance.** It ships structurally accessible and offline, but
   the screen-reader walkthrough, the Spanish copy, and the match rationale are
   the difference between "a volunteer can technically run it" and "a volunteer
   can run it well."

## Honest limits of this exercise

This is simulated. It can generate plausible needs and obvious gaps, but it
cannot tell you which are real, how many organizations would adopt, or what a
funder would pay for. It over-represents the author's mental model and will miss
what only a real survivor advocate, a real accidental DBA, or a real CoC lead
would surprise you with. The data-subject personas are the most fraught to
synthesize and the least substitutable for the real thing: no generated persona
can stand in for a survivor's actual risk assessment, and nothing here should be
read as one. **Do not prioritize a roadmap off this document alone.** Use it to
design the questions for, and lower the cost of, the real interviews that the 1.0
adoption gate requires (`docs/ROADMAP.md`, v1.0).

The triaged, evidence-tagged backlog this panel implies is in
[`RESEARCH-ROADMAP.md`](./RESEARCH-ROADMAP.md).

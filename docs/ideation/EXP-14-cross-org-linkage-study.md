# EXP-14 — Cross-organization linkage study (privacy-preserving, heavily gated)

Status: **study only, no design commitment, no code**. This document is the
first output the roadmap calls for under EXP-14 ("study first, code later").
It does not authorize implementation. Nothing in `constituent-reconciler`
changes as a result of this document; no matcher, connector, or policy pack
is added. See "What this study does not authorize" at the end.

Drafted 2026-07-07. Not legal advice; not a completed study. It is a scoping
document intended to make the open questions concrete enough for counsel, an
NNEDV-informed subject-matter expert, and a real coalition partner to react
to, per the gate `docs/ideation/04-impact-and-sequencing.md` already records
for EXP-14: *"Counsel + NNEDV-informed SME + a real coalition partner ...
Study before prototype."*

## 1. The question

Persona D2 in `docs/USER-RESEARCH.md` is a constituent served by more than
one program, not only more than one intake source inside one organization.
A food bank and a housing program in the same county may both serve the
Alvarez family without either knowing the other has. `constituent-reconciler`
today reconciles records *within* one organization's intake and one target
case system. EXP-14 asks whether a small coalition of such organizations
could reconcile shared clients across organizational boundaries, without any
one party (including this project) ever holding the union of everyone's raw
PII.

That is a materially different problem from everything else in this repo.
Every existing policy pack assumes one data controller. A cross-org design
has at minimum two controllers, a question of who (if anyone) is a processor,
and a linkage step that by construction needs to compare identifying data
that started out in different hands. There is no version of this that is
free of legal and ethical risk; the job of this study is to describe the
risk precisely enough that a real decision can be made, not to make the
decision here.

## 2. The bright line: DV-pack data is out, full stop

`docs/RESPONSIBLE-TECH-AUDITS.md` already establishes, with primary-source
citations, that VAWA bars a grantee from disclosing personally identifying
client information "regardless of whether the information has been encoded,
encrypted, hashed, or otherwise protected" (34 U.S.C. § 12291(b)(2)(B)(i);
FVPSA parallel at 42 U.S.C. § 10406(c)(5)), and that NNEDV and HUD read entry
into any shared database as a prohibited disclosure. That reading does not
carve out an exception for cryptographic protection — it names encoding,
encryption, and hashing explicitly as insufficient. Any cross-organization
linkage scheme, however it is built, is a shared database or a functional
equivalent of one from the client's point of view: two or more parties end
up able to determine that the same person appears in each other's systems.

Conclusion, stated as the bright line this study commits to and that any
future work must inherit: **a DV/VSP program's records never participate in
cross-organization linkage under EXP-14, under any technique, including
Bloom-filter PPRL and secure multi-party computation.** This is not a gap to
close with better cryptography; VAWA's "regardless of ... protected" language
forecloses the encrypted-linkage reading specifically. If a future coalition
includes a VSP member, that member's records are excluded from the linkage
step by policy-pack default (mirroring how the existing `dv` policy pack
already fuses off the cloud extraction seam), not merely by configuration
choice a coalition could reverse.

Everything below concerns the remaining, narrower question: **non-VSP human
services programs** (the food bank / housing program pairing in the pitch),
where VAWA and FVPSA do not apply, but other confidentiality regimes may.

## 3. What "reconcile without pooling raw PII" could mean, technically

There is a spectrum of established patterns in the record-linkage literature
for computing a match without either side seeing the other's raw data. None
of them is free; each trades legal simplicity for cryptographic complexity,
or the reverse. Naming them here is scoping, not a recommendation.

**3.1 Honest-broker / trusted third party.** A neutral party (which could be
a county coordinating entity, a shared intermediary like a Community
Information Exchange, or, with a specific and separately negotiated trust
relationship, this project's operator) receives identifying data from both
organizations under a data-sharing agreement, performs the match, and returns
only match/no-match decisions (and, for matched pairs, a shared linkage
token) to each side. Neither org sees the other's raw records. The broker
does see both, so the broker's own security posture, retention limits, and
legal footing become the whole game. This is the pattern closest to what
`constituent-reconciler` already knows how to build well: deterministic
matching, a human review queue over uncertain matches, an append-only
provenance log, fail-closed on ambiguity. It is also the pattern with the
most legally legible governance story, because "who has access to what" maps
onto a single data-processing agreement instead of a distributed protocol.
Its cost is trust concentration: the broker becomes the party with the most
to lose if compromised, and small coalitions of the kind this project targets
(the "43% on one or two IT staff" audience) are unlikely to have or be able
to fund a broker with strong security operations of its own.

**3.2 Bloom-filter PPRL (cryptographic long-term keys, CLKs).** Each
organization encodes its identifying fields (name, DOB, address components)
into a Bloom filter using shared, secret salted n-gram hashing, then compares
filters using a similarity measure like Dice coefficient — without ever
exchanging the underlying plaintext. This is the technique the pitch
specifically names, and the technique the RESPONSIBLE-TECH-AUDITS.md quote
already rules out for DV data. For the *non-DV* case it remains attractive on
paper (Schnell, Bachteler, and Reiher's 2009 introduction of Bloom-filter
PPRL is the foundational citation here) but it has a well-documented weakness
this study will not paper over: published cryptanalysis (frequency-based and
pattern-based attacks on Bloom-filter encodings, demonstrated in the
PPRL literature through the 2010s) has shown that Bloom filters built from
common identifying fields can leak the underlying values back out under
realistic conditions, especially with small populations, predictable
salting, or attacker access to an auxiliary dataset of known individuals in
the same area. A county-scale food-bank/housing coalition is exactly the
small-population, geographically concentrated setting where those attacks
are most credible, not a hypothetical edge case. Any Bloom-filter design
would need at minimum: per-coalition random salting rotated on a schedule,
hardening parameters (bit length, number of hash functions, blocking
strategy) chosen against the current attack literature and revisited as that
literature moves, and a named threat model that says explicitly who is
assumed not to hold an auxiliary re-identification dataset and why that
assumption holds for this coalition. None of that exists yet; it is exactly
the kind of "guessing the tables recreates the bias" mistake this project's
own ideation ethos warns against (see EXP-03's SME gate) applied to
cryptography instead of naming tables.

**3.3 Secure multi-party computation (SMPC).** Garbled circuits or
homomorphic encryption let two parties jointly compute a match result with
formal cryptographic guarantees that neither party learns anything about the
other's input beyond the output. This is the strongest privacy guarantee on
the list and the correct answer if the coalition can afford it. It is also
computationally heavy, requires specialized libraries this project does not
currently depend on (nothing in the existing stack — Splink/dedupe, stdlib,
libpostal — does SMPC), and needs both parties to run compatible protocol
software correctly, which is a real operational bar for a coalition where
each side may be "one or two IT staff." SMPC is the answer for a well-funded
coalition with technical capacity on both sides; it is very unlikely to be
the answer for this project's stated audience without a funded pilot that
specifically budgets for it.

**3.4 Salted keyed hashing on individual identifiers.** A simpler
cousin of 3.2: hash each identifying field (or a normalized combination) with
a shared secret key, exchange only the hashes, and match on exact hash
equality. This avoids Bloom-filter-specific cryptanalysis but reintroduces a
harder problem in exchange: any inexact match (a typo, a middle initial, an
old address) produces a different hash and is silently missed, which cuts
directly against this project's fail-closed, no-silent-miss posture. It also
depends on secure exchange and rotation of the shared key, which is a key-
management problem, not a linkage problem, and needs its own answer.

**3.5 Aggregate-only, no individual linkage.** Share only counts and
suppressed cross-tabulations (mirroring the DV pack's existing small-cell
suppression pattern in `docs/RESPONSIBLE-TECH-AUDITS.md`) — "how many clients
does this ZIP code's food bank and housing program likely share" rather than
"which specific clients." This is the safest option on the list by a wide
margin and requires none of the cryptographic apparatus above. It also does
not solve persona D2's actual problem: a caseworker still cannot tell that
the specific Alvarez family in front of them is already known to the housing
program, which was the entire point of the expansion. Worth naming because
it may be the right answer for a coalition that wants reporting-level
visibility without individual-level linkage, as a smaller, faster, non-EXP-14
deliverable in its own right.

None of 3.1–3.5 is proposed here as the design. The honest answer at this
stage is that 3.1 (honest broker) is the pattern most compatible with this
project's existing architecture and most legible to counsel, and 3.2/3.3
carry risk profiles that a small coalition is poorly positioned to manage
without outside cryptographic expertise this project does not have in house.
That is a hypothesis for the real coalition partner and counsel to test, not
a conclusion.

## 4. Consent as the actual load-bearing mechanism

Whichever technique is chosen, the honest position is that **consent, not
cryptography, does most of the legal work for the non-VSP case.** VAWA's bar
on VSP disclosure holds regardless of protection; outside that regime, most
human-services confidentiality obligations (state social-services
confidentiality statutes, program-specific rules, funder data-sharing
terms) are structured around informed client consent to a *named* disclosure,
not around whether the disclosure was technically protected. A design that
gets consent right — specific to the two named partner organizations,
specific about what is compared (not just "your data may be shared" but
"your name and address will be compared, in encoded form, against \[Partner
Org\]'s client list to check for a match"), revocable, and not a condition of
service — is closer to sufficient on its own than a cryptographically clever
design layered on vague or bundled consent. This project already treats
consent as a first-class field enforced at the write path
(`src/constituent_reconciler/consent.py`); a cross-org linkage consent would
need to be a distinct, more specific consent type than the existing
CRM-export consent, scoped to the named partner and the specific comparison,
because "consent to write my record to our CRM" does not imply "consent to
compare my record against another organization's client list."

This does not remove the need for technical protection — a consenting client
still deserves a design that limits what a technical failure or breach could
expose — but it reframes the study's center of gravity: the primary open
question for the non-VSP case is a consent and governance design question,
with the cryptographic technique choice downstream of it, not the reverse.

## 5. Legal terrain beyond VAWA (named to scope the question, not resolved)

This section names regimes that may bear on a specific coalition's
membership. It is deliberately not a legal conclusion about any of them;
which apply, and how, depends on which organizations join a coalition and
what programs they run, and that determination belongs to counsel, not to
this document.

- **HIPAA** may apply if a coalition member is a covered entity (most
  clinics, some integrated health-and-housing programs) or if the linkage
  broker would be acting as a business associate of one.
- **42 CFR Part 2** governs substance-use-disorder treatment records
  specifically and is, like VAWA, historically read as more protective than
  HIPAA on redisclosure; a coalition member running an SUD program needs a
  Part 2 determination before its records go anywhere near a cross-org
  design, independent of the VSP question.
- **FERPA** may apply if a coalition member is a school or a program that
  receives student education records from one.
- **State-level human-services confidentiality statutes and county data-
  sharing MOUs** vary by state and are frequently the actual controlling
  instrument for a food-bank/housing pairing, more often than any federal
  statute above. This is the regime a real coalition partner is most likely
  to already have direct, current knowledge of, and is exactly the kind of
  fact this project should get from that partner and counsel rather than
  from general knowledge.
- **Funder terms** (a shared county or state grant, a CoC data-sharing
  requirement tied to HMIS participation) may impose their own linkage or
  non-linkage rules independent of the above, and often move the goalposts
  faster than statute does.

The task for counsel and the SME is to determine which of these actually
apply to a specific real coalition, not to treat this list as a checklist
this project can self-certify against.

## 6. Governance questions a technical design cannot answer

- **Controller/processor roles.** Are the two orgs joint controllers, or is
  one a processor for the other, or is a third-party broker a processor for
  both? This determines who is liable for what and who must be named in
  which consent language.
- **Retention and destruction of the linkage artifact itself**, independent
  of each org's own record retention. A shared linkage token or match result
  is itself a fact about a real person ("client X is known to both orgs")
  and needs its own retention and destruction policy, in the same spirit as
  the per-policy-pack destruction model EXP-10 is building for single-org
  retention.
- **Breach response ownership.** If the broker (3.1) or one coalition
  member is breached, who notifies whom, on what timeline, under which
  member's breach-notification obligations to the client.
- **Audit and provenance across the trust boundary.** This project's
  append-only, hash-chained provenance log (`provenance.py`) answers "who
  changed what, when" inside one organization's write path. A cross-org
  design needs an answer to the same question that spans the trust boundary
  without requiring either party to trust the other's log unverified — an
  open design problem, not a solved one, and worth naming as its own
  sub-study if a coalition partner is found.
- **Exit.** What happens to a coalition member's data at the broker, or to
  shared linkage tokens, if that member leaves the coalition. A design that
  cannot answer this cleanly is not ready for a pilot.

## 7. What a real coalition partner would need to tell this study

This study cannot proceed past this point without concrete answers from an
actual pair of organizations, because the abstract shape of "two human-
services orgs in one county" hides decisions that change the whole design:

- What identifying fields does each org actually collect at intake (full
  legal name only, or also DOB, SSN fragment, phone)? PPRL quality and risk
  both depend heavily on which fields are available to encode.
- What is the realistic shared population size and geographic concentration?
  This directly affects how credible a Bloom-filter re-identification attack
  is (§3.2) — the smaller and more geographically concentrated the shared
  population, the higher that risk.
- Does either org already have an existing data-sharing relationship or
  MOU with a county coordinating body that a design should route through
  instead of building new infrastructure?
- What is each org's actual technical capacity to run a linkage client, not
  the aspirational one? The pitch's "43% on one or two IT staff" framing
  argues against 3.3 (SMPC) as a first design by itself.
- Would clients, in practice, be asked for consent at intake, and by whom —
  is there a caseworker moment where a specific, scoped consent ask is
  realistic, or would it have to be retrofitted onto an existing intake flow
  that was not built for it?

## 8. Recommended shape of a future prototype, conditional on the gates clearing

If, and only if, counsel confirms the non-VSP legal terrain, an SME
confirms the consent and governance design, and a real coalition partner
answers §7, the technical shape most consistent with this project's existing
architecture and audience is:

1. Start with 3.1 (honest broker), not Bloom-filter PPRL, as the first
   prototype's technique — it reuses this project's existing deterministic
   matcher, human review queue, and provenance log almost unchanged, just
   with the broker as the operator of a `constituent-reconciler` instance
   that both orgs feed into under a data-sharing agreement, and it is the
   pattern easiest for counsel to reason about because access is centralized
   and auditable rather than distributed across a cryptographic protocol.
2. Treat 3.2 (Bloom-filter PPRL) as a second-phase option only after a
   named threat model and hardening parameters have been reviewed against
   current cryptanalysis literature by someone with that specific expertise
   — not by this project's existing team, which does not have it.
3. Do not pursue 3.3 (SMPC) for this audience without a funded pilot that
   specifically budgets for the added technical capacity on both sides.
4. Build the cross-org consent type (§4) as a genuinely separate consent
   field from day one, never inferred from existing CRM-export consent.
5. Exclude DV/VSP-flagged records from the linkage step by policy-pack
   default, the same way the `dv` policy pack already fuses off the cloud
   seam — not by relying on a coalition member to remember to exclude them.

This is a recommendation for what a prototype would look like, not a
decision to build one.

## 9. What this study does not authorize

- It does not authorize writing a PPRL matcher, a Bloom-filter encoder, an
  SMPC client, or a cross-org connector.
- It does not authorize a pilot with any real organization or any real
  client data.
- It does not constitute counsel review. No attorney has reviewed this
  document or the questions in §5.
- It does not constitute SME review of the consent and governance design in
  §4 and §6.
- It does not change the DV policy pack, the existing consent model, or any
  shipped code in this repository.

## 10. Next steps (tracked here, not scheduled)

1. Identify a real coalition partner pair (§7) — likely the hardest
   prerequisite, and the one this project cannot manufacture on its own.
2. Engage counsel on the non-VSP legal terrain in §5 for that specific
   coalition's actual membership.
3. Engage an NNEDV-informed SME (or equivalent human-services privacy SME
   for the non-VSP case) on the consent design in §4 and the governance
   questions in §6.
4. Only after 1–3 return answers, revisit §8 as a design proposal, not
   before.

## Sources and attribution

VAWA and FVPSA citations and the "regardless of ... protected" language are
carried forward unchanged from `docs/RESPONSIBLE-TECH-AUDITS.md`, which
attributes them to 34 U.S.C. § 12291(b)(2)(B)(i), 42 U.S.C. § 10406(c)(5),
and the NNEDV/HUD shared-database reading described there. The Bloom-filter
PPRL technique (§3.2) is attributed to Schnell, Bachteler, and Reiher's 2009
introduction of Bloom-filter-based privacy-preserving record linkage; the
cryptanalysis concern is attributed generally to the subsequent PPRL
cryptanalysis literature (frequency- and pattern-based attacks on
Bloom-filter encodings published through the 2010s) rather than to one
specific paper, because this study has not verified a specific citation
against primary sources and says so rather than inventing one. Regimes named
in §5 (HIPAA, 42 CFR Part 2, FERPA) are named by common name and general
scope only, not by section citation, for the same reason. Confirming exact
citations for §5 and the precise cryptanalysis literature for §3.2 is
in-scope follow-up work for counsel and the SME engagement in §10, not
something this document asserts as verified.

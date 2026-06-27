# Responsible-tech audits

Project-specific findings for constituent-reconciler, following a standard
responsible-tech audit method: ethics, bias, privacy and a DPIA, transparency,
accessibility, and security. This is a committed, dated artifact, regenerated on
release. It is a stub at v0.x; each section is filled in as the phase that
creates the surface lands.

This tool handles some of the most sensitive data a nonprofit holds. The audit
is not a launch afterthought; its checks are wired into CI from the first phase,
and the privacy invariants below are merge-blocking tests, not prose.

Status: v0.5. The privacy section's DV-pack invariants are implemented and
merge-blocking; sections marked TODO are scoped but not yet measured.

## Ethics

The asymmetry of harm drives the design. A false merge can corrupt or expose a
person's record across programs and is sometimes irreversible; a missed match
leaves a harmless duplicate. The system therefore never auto-merges on
uncertainty and routes every ambiguous decision to a person. TODO: document the
failure modes considered and the decisions made.

## Bias

Record-linkage and extraction error is not evenly distributed. Name matching
degrades on transliterated names, hyphenated and changed surnames, and
non-Western name order; address parsing degrades on rural and informal
addresses. TODO: report measured error by name and address class on the eval
fixtures, and the mitigations.

## Privacy and data minimization (DPIA)

The strongest claims live here and are enforced as tests.

* Deterministic offline default; the cloud seam is optional and policy-gated.
* Consent is a first-class field; under a consent-required policy the export
  withholds any record without granted consent and records it by id and reason
  only, never with field values. Enforced by `tests/test_consent.py` and the
  DV-pack pipeline test in `tests/test_pipeline.py`.
* Every write is recorded in an append-only, tamper-evident provenance log: a
  BLAKE2b hash chain that `reconcile verify` checks. Timestamps come from a
  pluggable authority (the local clock by default; RFC 3161 trusted timestamping
  is the seam for production). Chain integrity and tamper detection are covered
  by `tests/test_provenance.py`.
* The **DV policy pack** (v0.5) enforces the VAWA and FVPSA confidentiality
  posture as four merge-blocking invariants, each grounded in primary guidance
  rather than memory:
  * **No PII egress.** The cloud extraction seam is fused off
    (`tests/test_extract.py`, `tests/test_no_egress.py`) and a non-local write
    target is refused before any write (`tests/test_no_egress.py`). VAWA bars a
    grantee from disclosing personally identifying client information "regardless
    of whether the information has been encoded, encrypted, hashed, or otherwise
    protected" (34 U.S.C. § 12291(b)(2)(B)(i); FVPSA parallel at 42 U.S.C.
    § 10406(c)(5)). NNEDV and HUD read entry into a shared database such as HMIS
    as a prohibited disclosure, which is why a victim-service provider keeps
    client data in its own comparable database (HUD HMIS Comparable Database;
    McKinney-Vento as amended, 42 U.S.C. § 11383(a)(7)). The statute's operative
    verbs are "disclose, reveal, or release"; the shared-database reading is
    attributed to NNEDV and HUD, not quoted as statute.
  * **Consent required.** Informed, written, reasonably time-limited consent is
    required before release (34 U.S.C. § 12291(b)(2)(B)(ii)), and consent may not
    be a condition of services (§ 12291(b)(2)(D)(ii)(I)). The export withholds any
    record without granted consent, recorded by id and reason only. Revocability
    is NNEDV Safety Net best practice, not statutory text, and is described as
    such.
  * **Aggregate, suppressed sharing.** Only non-personally-identifying data in
    the aggregate may be shared for reporting (34 U.S.C. § 12291(b)(2)(D)(i)(I)).
    The pack emits an aggregate summary with no field values and small-cell
    suppression (counts of 1-10 suppressed, true zeros preserved, complementary
    suppression applied), modeled on the U.S. CMS Cell Size Suppression Policy.
    No uniform federal threshold exists and HUD, VAWA, and FVPSA set none; the
    CMS rule is the most defensible bright line and is cited as such, not as a DV
    mandate. Covered by `tests/test_suppression.py` and `tests/test_no_egress.py`.

Sources: 34 U.S.C. § 12291 (law.cornell.edu/uscode/text/34/12291); 42 U.S.C.
§ 10406 (FVPSA); NNEDV Safety Net, "Comparable Database 101" and
"Confidentiality in VAWA, FVPSA, and VOCA" (techsafety.org); CMS Cell Size
Suppression Policy (resdac.org/articles/cms-cell-size-suppression-policy). The
limitation: suppression here does not defend against cross-tabulation attacks
that correlate several breakdowns.

TODO: complete the data-flow map and the retention and destruction model per
policy pack.

## Transparency

Every match decision shown to a reviewer carries its source span and its
confidence. The run report shows per-stage counts and the eval score. The DV
pack documents the VAWA and FVPSA invariants it claims to enforce, each linked
to the test that enforces it. TODO: publish the model and data cards for the
optional extraction seam.

## Accessibility

The review queue is the human surface and meets WCAG 2.2 AA, with an axe
AUTO-GATE and a screen-reader walkthrough REVIEW-GATE. EN and ES at parity.
TODO: commit the ACR.

## Security

OWASP ASVS-aligned posture: SBOM, Sigstore, SHA-pinned actions, OIDC, secret
scanning. Untrusted input (uploaded PDFs and scans) is a primary threat surface
and is parsed in a hardened path. TODO: commit the threat model.

## Legal note

This is a reference implementation, not legal advice. An organization adopting
it, and the DV policy pack in particular, needs its own review against its own
obligations and its own counsel.

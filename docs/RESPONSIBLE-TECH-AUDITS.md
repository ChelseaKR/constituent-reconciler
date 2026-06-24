# Responsible-tech audits

Project-specific findings for constituent-reconciler, following a standard
responsible-tech audit method: ethics, bias, privacy and a DPIA, transparency,
accessibility, and security. This is a committed, dated artifact, regenerated on
release. It is a stub at v0.x; each section is filled in as the phase that
creates the surface lands.

This tool handles some of the most sensitive data a nonprofit holds. The audit
is not a launch afterthought; its checks are wired into CI from the first phase,
and the privacy invariants below are merge-blocking tests, not prose.

Status: pre-v0.1. Sections marked TODO are scoped but not yet measured.

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
* Under `--policy-pack dv`, PII never leaves the machine. Enforced by
  `test_no_egress.py`.
* Consent is a first-class field; the write step refuses, fail-closed, to emit
  any field whose consent is absent, expired, or revoked. Enforced by
  `test_consent_blocks_export.py`.
* Exports under the DV pack are aggregate and suppression-aware.
* Every write is recorded in an append-only provenance log with BLAKE2b content
  hashing and an RFC 3161 timestamp.

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

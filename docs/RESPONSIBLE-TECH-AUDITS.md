# Responsible-tech audits

Project-specific findings for constituent-reconciler, following a standard
responsible-tech audit method: ethics, bias, privacy and a DPIA, transparency,
accessibility, and security. This is a committed, dated artifact, regenerated on
release. It is a stub at v0.x; each section is filled in as the phase that
creates the surface lands.

This tool handles some of the most sensitive data a nonprofit holds. The audit
is not a launch afterthought; its checks are wired into CI from the first phase,
and the privacy invariants below are merge-blocking tests, not prose.

Status: v0.7. The privacy section's DV-pack invariants are implemented and
merge-blocking; sections marked TODO are scoped but not yet measured.

Last verified: 2026-07-05 · Recheck cadence: per release (the "regenerated on
release" promise above broke for v0.6 and v0.7 — see the 2026-07-05
remediation log in `CHANGELOG.md` — this stamp exists so staleness is visible
going forward instead of silent).

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
    be a condition of services (§ 12291(b)(2)(D)(ii)(I)). Consent is modeled as a
    lifecycle (`models.Consent`: status, grant date, expiry date, destination
    scope), not a membership check on a status string: the export gate withholds
    any record whose consent is revoked, absent, not yet effective, expired, or
    out of scope for the destination, recorded by id and reason only (`absent`,
    `revoked`, `future-dated`, `expired`, or `out-of-scope`), never with field
    values. This is the mechanism for "reasonably time-limited"; the actual
    expiry window is not invented here -- the recipe must map an explicit
    per-record expiry column, and setting that number (or declining to) is a
    counsel-gated decision, not a default this code ships with. Revocability is
    NNEDV Safety Net best practice, not statutory text, and is described as
    such. Enforced by `tests/test_consent.py`.
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

The data-flow map and the retention and destruction model per policy pack are
in [DATA-FLOW-AND-RETENTION.md](./DATA-FLOW-AND-RETENTION.md), including the DV
pack's routine destruction of individual records. Retention windows stay
counsel-gated there, consistent with this document's sourcing rules.

## Transparency

Every match decision shown to a reviewer carries its source span and its
confidence. The run report shows per-stage counts and the eval score. The DV
pack documents the VAWA and FVPSA invariants it claims to enforce, each linked
to the test that enforces it. The model and data cards for the optional
extraction seam are published as [`docs/MODEL-CARD.md`](MODEL-CARD.md) and
[`docs/DATA-CARD.md`](DATA-CARD.md).

## Accessibility

The review queue is the human surface. As of v0.7 it is a local web UI
(`reconcile review`) built to the WCAG 2.2 AA structural bar: a comparison table
with scoped headers, status carried by text and a symbol rather than colour
alone, decision controls that work with the keyboard and with no JavaScript, and
no external asset fetch. The axe AUTO-GATE now runs (`accessibility` job in
`.github/workflows/ci.yml`, an axe-core scan over jsdom of the review queue's
real rendered HTML; docs/decisions/0009-automated-axe-audit.md), zero
violations against the current markup as of 2026-07-07. Its one honest gap is
`color-contrast`, which jsdom cannot evaluate (no canvas); every color pair in
the stylesheet was checked by hand against the WCAG formula instead and clears
the bar with margin (worst case 4.59:1 against a 3:1 non-text requirement). The
screen-reader walkthrough REVIEW-GATE is not yet run — it needs a human
tester with real assistive technology, not something a script can complete —
and EN/ES parity for the UI copy is not yet done; both stay open before the
1.0 accessibility claim. TODO: complete the screen-reader walkthrough
(checklist at docs/reviews/SCREEN-READER-WALKTHROUGH.md), add the ES copy, and
commit the ACR.

## Security

**ASVS: L2** (handles PII: DV-survivor constituent records; matches the
standard's PII-handling floor). OWASP ASVS-aligned posture: SBOM, Sigstore,
SHA-pinned actions, OIDC, secret scanning. Untrusted input (uploaded PDFs) is a
primary threat surface; parsing currently runs in-process, and a sandboxed,
resource-limited extraction path is planned. TODO: commit the threat model.

Declarations (2026-07-05), each Applies/gap tracked in the README standards
table rather than left blank:

* **Secret scanning:** Applies — enforced. `.pre-commit-config.yaml` runs
  gitleaks pre-commit; `ci.yml`'s `secrets` job runs gitleaks on every push and
  PR; a scheduled weekly workflow runs TruffleHog full-history (verified
  credentials only).
* **Dependency-vulnerability scanning:** Applies — enforced. `uv.lock` is
  committed; `make security` runs `pip-audit` and
  `osv-scanner --lockfile uv.lock`, invoked locally on demand and as its own
  blocking CI job (`security` in `ci.yml`, separate from `verify` so it is its
  own required check), on any fixed HIGH/CRITICAL finding, no mute pattern.
* **SAST:** Applies — enforced. A `sast` CI job runs Semgrep
  (`p/security-audit`, `p/secrets`, and a repo-specific `no-pii-in-logs` rule
  at `.semgrep/no-pii-in-logs.yml` mirroring the consent/provenance
  data-minimization tests) blocking on any finding; a `codeql` workflow
  (`.github/workflows/codeql.yml`) runs CodeQL for both the `python` and
  `actions` languages on push, PR, and a weekly schedule. A `zizmor` job
  additionally lints the workflow files themselves (CICD-19).
* **Container scan:** Applies (Dockerfile ships a self-host image) — gap,
  tracked locally pending a filed issue (no Trivy/Grype job in CI yet; see
  P1-4). The base image is pinned to a mutable `python:3.12-slim` tag today,
  not yet a digest.
* **SBOM:** Applies (release-producing repo) — gap, tracked locally pending a
  filed issue (no SBOM generation on release yet; see P1-7).
* **VEX:** N/A today — no SBOM yet to accompany a VEX statement; revisit with
  P1-7.
* **Secret management:** N/A for this repo's own operation — it holds no
  service secrets itself; CRM API keys/tokens are supplied by the *operator*
  through their own environment (`CIVICRM_API_KEY`, `SF_TOKEN`) and are never
  read from or written to a file this repo commits.

## Standards applicability: AI Evaluation and Internationalization

Two portfolio standards outside the A–F sections above, declared here per
RTF-07 rather than left silent:

* **AI-Evaluation-Standard: N/A** — no model inference in any user-facing or
  decision path today (`BedrockSeam.refine()` is an unimplemented stub; every
  policy pack defaults to `NoOpSeam`). Full declaration and the flip-to-Applies
  trigger: `docs/ROADMAP.md` § "AI Evaluation Standard applicability".
* **Internationalization-Standard: Applies — deferred** to the 1.0 milestone.
  Full declaration, current state, and the catalog plan: `docs/I18N.md`.

## Legal note

This is a reference implementation, not legal advice. An organization adopting
it, and the DV policy pack in particular, needs its own review against its own
obligations and its own counsel.

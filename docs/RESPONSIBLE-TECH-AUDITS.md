# Responsible-tech audits

Project-specific findings for constituent-reconciler, following a standard
responsible-tech audit method: ethics, bias, privacy and a DPIA, transparency,
accessibility, and security. This is a committed, dated artifact, regenerated on
release. Each implemented surface below names its evidence and residual risk;
human and external gates remain explicit rather than being marked complete.

This tool handles some of the most sensitive data a nonprofit holds. The audit
is not a launch afterthought; its checks are wired into CI from the first phase,
and the privacy invariants below are merge-blocking tests, not prose.

Status: v0.7. The privacy invariants are merge-blocking; ethics failure modes,
the untrusted-document threat model, and disaggregated synthetic bias results
are documented. The manual screen-reader and real-organization adoption gates
remain open.

Last verified: 2026-07-12 · Recheck cadence: per release.

## Ethics

The asymmetry of harm drives the design. A false merge can corrupt or expose a
person's record across programs and is sometimes irreversible; a missed match
leaves a harmless duplicate. The system therefore never auto-merges on
uncertainty and routes every ambiguous decision to a person.

| Failure mode | Decision and evidence | Residual risk |
|---|---|---|
| False merge | 0% fixture gate, cannot-link rejection constraints, and human review below the auto threshold | Fixtures cannot represent every population or source |
| Missed match | Report separately; retain duplicates instead of lowering the safety threshold | Staff may do extra review or leave a duplicate unresolved |
| Consent failure | Destination-scoped, dated consent is checked before every connector; withheld artifacts contain ids and reasons only | Deployers own lawful consent collection and expiry policy |
| Extraction error | Offline default, source spans, confidence gate, and local fallback on model failure | Cloud-refined fields are not yet provenance-tagged |
| Reviewer error | Attributed decisions, optional two-person review, and synthetic calibration pairs | A single-reviewer deployment still relies on that person's judgment |

## Bias

Record-linkage and extraction error is not evenly distributed. Name matching
degrades on transliterated names, hyphenated and changed surnames, and
non-Western name order; address parsing degrades on rural and informal
addresses. The committed R5 audit at
[`docs/audits/bias-report.md`](./audits/bias-report.md) plants one true pair for
each of those five documented classes and is regenerated in CI with
`make eval-bias`.

As of 2026-08-19, all five pairs reach candidate scoring (none go unscored),
with 100% auto+review coverage for hyphenated/punctuated surname, rural-route
address, informal address description, and non-Western name order.
Transliterated name variant remains at 0% coverage at the current threshold.

Non-Western name order moved from 0% to 100% on 2026-08-19, and it is worth
recording how, because it was not a threshold change. The matcher now carries a
comparison level for a given name and a family name entered in opposite fields,
and a `name_pair_key` blocking rule that generates such a pair in the first
place. Family-name-first is the written convention in Chinese, Korean,
Japanese, Hungarian and Vietnamese naming, so a form that labels one box "first
name" collects transposed values from exactly those constituents; before this,
both name comparisons scored the crossing as a disagreement and the pair was
penalised twice for one mistake. The change was found and measured on an
external benchmark ([`../BENCHMARK.md`](../BENCHMARK.md)) rather than on this
fixture, and this class improving is corroboration rather than the target.

The small synthetic sample is a regression probe, not a demographic performance
claim. The current mitigation is to keep the false-merge threshold fail-closed,
expose the misses instead of tuning to this fixture, preserve source spans for
human comparison, and require an adopting organization to evaluate
representative local names and addresses before deployment. Closing the
transliterated-name class and setting a review-coverage gate requires reviewed
data from an adopting organization; no real constituent data is committed here.

## Privacy and data minimization (DPIA)

The strongest claims live here and are enforced as tests.

* Deterministic offline default; the cloud seam is optional and policy-gated.
* Consent is a first-class field; under a consent-required policy the export
  withholds any record without granted consent and records it by id and reason
  only, never with field values. Enforced by `tests/test_consent.py` and the
  DV-pack pipeline test in `tests/test_pipeline.py`.
* Every write is recorded in an append-only, tamper-evident provenance log: a
  BLAKE2b hash chain that `constituent-reconcile verify` checks. Timestamps come from a
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
(`constituent-reconcile review`) built to the WCAG 2.2 AA structural bar: a comparison table
with scoped headers, status carried by text and a symbol rather than colour
alone, decision controls that work with the keyboard and with no JavaScript, and
no external asset fetch. The axe AUTO-GATE now runs (`accessibility` job in
`.github/workflows/ci.yml`, an axe-core scan over jsdom of the review queue's
real rendered HTML; docs/adr/0011-automated-axe-audit.md), zero
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
primary threat surface; since 2026-07-17 PDF parsing runs in a resource-limited
child process by default (containment, not privilege separation — the threat
model states the limits), with an explicit recipe opt-out. The committed threat
model is [`THREAT-MODEL.md`](./THREAT-MODEL.md), re-verified 2026-07-12 after
the Bedrock/local inference and telemetry paths landed and updated 2026-07-17
for the sandbox default.

Declarations (re-verified 2026-07-12), each Applies/gap tracked in the README standards
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
* **Container scan:** Applies (Dockerfile ships a self-host image) —
  enforced. A `container-scan` CI job builds the image (`make docker`) and
  runs Trivy, blocking on any fixed CRITICAL/HIGH finding; the base image is
  pinned by digest (`python:3.12-slim@sha256:...`), not just tag.
* **SBOM:** Applies (release-producing repo) — enforced as of
  `.github/workflows/release.yml` (2026-07-10, closes P1-7): a CycloneDX 1.7
  SBOM of the released environment is generated and attached to every
  GitHub Release, alongside a keyless build-provenance attestation. Not yet
  exercised end-to-end — no `v*` tag has been cut yet.
* **VEX:** N/A today — no disclosed vulnerability in a shipped release yet to
  accompany with a VEX statement; revisit once the SBOM above has been
  exercised by a real release.
* **Secret management:** N/A for this repo's own operation — it holds no
  service secrets itself; CRM API keys/tokens are supplied by the *operator*
  through their own environment (`CIVICRM_API_KEY`, `SF_TOKEN`) and are never
  read from or written to a file this repo commits.

## Standards applicability: AI Evaluation and Internationalization

Two portfolio standards outside the A–F sections above, declared here per
RTF-07 rather than left silent:

* **AI-Evaluation-Standard: Applies** to the opt-in Bedrock and local extraction
  seams. The implementation has a fail-closed calibration gate, model/data
  cards, mocked contract and fallback tests, and PII-free canonical GenAI
  telemetry with token/cost accounting. It makes no live hosted-model accuracy
  claim; deployer-specific benchmarking remains required. Full declaration:
  `docs/ROADMAP.md` § "AI Evaluation Standard applicability".
* **Internationalization-Standard: Applies — deferred** to the 1.0 milestone.
  Full declaration, current state, and the catalog plan: `docs/I18N.md`.

## Legal note

This is a reference implementation, not legal advice. An organization adopting
it, and the DV policy pack in particular, needs its own review against its own
obligations and its own counsel.

# Impact and sequencing

Drafted 2026-07-01. Covers FIX-01 to FIX-12 (`02-large-scale-fixes.md`) and
EXP-01 to EXP-16 (`03-expansions.md`). Impact is judged against the
project's own asymmetry: protecting data subjects first, then reviewer
trust, then operator time, then reach. These are hypotheses, in the same
spirit as the priority column in `docs/RESEARCH-ROADMAP.md`.

## Impact by effort

| | **S** | **M** | **L** | **XL** |
| --- | --- | --- | --- | --- |
| **High impact** | FIX-04 (strict recipes), FIX-12 (claims audit, text half) | FIX-01 (review-server hardening), FIX-02 (cannot-link, minimal), FIX-03 (stable ids), FIX-08 (run manifest), FIX-05 (ingest accounting) | FIX-11 (corpus + real eval), FIX-06 (consent lifecycle), EXP-03 (matching depth), EXP-04 (local OCR) | EXP-14 (cross-org study), EXP-15 (kernel library) |
| **Medium impact** | EXP-11 (narrative artifact) | FIX-07 (lineage), FIX-09 (connector registry), FIX-10 (extraction sandbox), EXP-06 (data-quality report), EXP-08 (email ingestion), EXP-10 (destruction executor), EXP-13 (air-gapped bundle) | EXP-01 (correct verdict), EXP-02 (cluster review), EXP-05 (local-model seam), EXP-07 (households), EXP-16 (public benchmark) | |
| **Lower / speculative** | | EXP-09 (reviewer calibration), EXP-12 (matcher seam) | | |

Reading notes. FIX-01 and FIX-02 are high impact despite modest effort
because each closes a gap in a promise the project already makes (no
egress path; no silent merge). FIX-11 is the multiplier: R5, R10, E9,
EXP-03, and EXP-16 all consume it. EXP-14 and EXP-15 are high-ceiling but
belong at the end of any queue on risk grounds.

## Dependency notes

- **FIX-03 before any real pilot (E8 usage).** Pilots edit CSVs mid-review;
  positional ids make that unsafe. FIX-04 belongs in the same bundle for
  the same audience.
- **FIX-02 before E7 (un-merge) and EXP-02.** Rejections must be durable
  before reversibility or cluster views mean anything.
- **FIX-07 before E7 and EXP-01.** Un-merge and corrections both need
  field lineage to restore or attribute values.
- **FIX-05 and FIX-08 pair naturally** (one run-report artifact) and feed
  EXP-06, EXP-10, and EXP-11.
- **FIX-11 before EXP-03 and before R5's numbers are worth publishing.**
  Per-class bias rates from 27 records would be noise presented as
  measurement.
- **FIX-09 before E3 (more connectors).** The registry and conformance kit
  keep `is_local` honest as the connector count grows.
- **FIX-10 with R4 (threat model), and before EXP-04/EXP-08**, which
  enlarge the untrusted-input surface the sandbox contains.
- **R3 (supply chain) before EXP-13**, which repackages what R3 signs.
- **EXP-05's policy question (no-cloud versus no-model) should be settled
  in `policy.py` terms before code**, and its docs land with R9's cards.
- **EXP-15 waits for a second in-portfolio consumer;** extraction before
  that is speculation.

## Suggested sequence (beyond the existing roadmaps)

`RESEARCH-ROADMAP.md`'s first sprint (R1, R6, R11, R3 quick wins, E8)
stands; nothing here displaces it. This sequence is the next layer, and it
deliberately front-loads trust-repair over capability.

**Now (with or immediately after the existing first sprint):**

1. FIX-01 review-server hardening. Smallest gap between a stated guarantee
   and the code; touches the surface R1 is about to audit anyway.
2. FIX-02 (minimal refuse-and-route form) plus its merge-blocking test.
3. FIX-04 strict recipe validation and `reconcile validate`, folded into
   the E8 adoption-kit flow.
4. FIX-12 text half: correct the scans, email-bodies, and
   approve/correct/reject claims until their implementations land.

**Next (pre-pilot hardening, roughly in order):**

5. FIX-03 stable record identity (schema-versioned, with migration note).
6. FIX-05 + FIX-08 ingest accounting and run manifest as one artifact.
7. FIX-07 golden-record lineage.
8. FIX-11 corpus generator and the large committed eval. This is the
   longest lead item with the widest fan-out; start it early, land it here.
9. FIX-09 connector registry, ahead of any E3 work.
10. FIX-10 extraction sandbox, written up jointly with R4.

**Later (capability growth, once the above holds):**

11. EXP-03 matching depth (measured on FIX-11's corpus, SME-reviewed
    tables).
12. EXP-04 local OCR, then EXP-08 email ingestion (both inside the FIX-10
    boundary); EXP-01 correct-verdict once its PII handling is designed;
    EXP-02 cluster review.
13. EXP-06 data-quality report, EXP-11 narrative artifact, EXP-13
    air-gapped bundle as adoption amplifiers around pilot feedback.
14. FIX-06 consent lifecycle and EXP-10 destruction executor as one
    counsel-gated privacy epic (mechanisms first, numbers only after
    review).
15. EXP-09, EXP-12, EXP-16 opportunistically; EXP-07 households only with
    its DV-off-by-default invariant; EXP-05 local-model seam after the
    policy decision.
16. EXP-15 and EXP-14 remain end-state bets pending a second consumer and
    a counsel-reviewed study respectively.

## Items gated on humans, counsel, SMEs, or real data

Per the portfolio ethos: these are deferred and reported honestly, never
simulated. Building the surrounding mechanism is fine; asserting the gated
substance is not.

| Item | Gate | What must not be faked |
| --- | --- | --- |
| FIX-06 consent lifecycle | Counsel | The expiry window and scope semantics. Ship with no default; the recipe must state one. |
| EXP-10 destruction executor | Counsel (extends R8's existing gate) | Retention periods and what "no longer needed" means for a given funding stream. |
| EXP-14 cross-org linkage | Counsel + NNEDV-informed SME + a real coalition partner | Whether any PPRL design is lawful; the DV exclusion is already clear from the VAWA text quoted in `RESPONSIBLE-TECH-AUDITS.md`. Study before prototype. |
| EXP-07 households under DV | SME (survivor-safety) | Whether co-residence inference is ever acceptable in a VSP context; default stays off. |
| EXP-03 nickname and naming-convention tables | Cultural/linguistic SME | The tables themselves; guessing them recreates the bias R5 exists to measure. |
| EXP-09 reviewer calibration | Real reviewers + ethics framing | Agreement numbers only mean something from real sessions; disclosure text needs a human read. |
| FIX-11 error-model calibration | Real pilot data (E8) | Synthetic error distributions are assumptions until checked against one real org's data; label them as assumptions until then. |
| EXP-02 / EXP-01 review UX | The R1 walkthrough cohort (screen-reader users, non-technical reviewers) | Usability claims. Structural WCAG work can land; "a caseworker can run it" cannot be self-certified. |
| Anything touching 1.0 | Real-organization adoption (the standing `ROADMAP.md` v1.0 gate) | The stability promise itself. |

The unchanged bottom line from `RESEARCH-ROADMAP.md` applies to this whole
folder: a plausible-sounding confidentiality design that is subtly wrong is
worse than none. Everything above that touches the DV posture inherits the
project's standing caveat and its counsel gate.

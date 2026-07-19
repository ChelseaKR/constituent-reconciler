# EU AI Act Classification — constituent-reconciler

> **Status: DRAFT — pending owner sign-off.** Drafted 2026-07-19 by an agent run
> executing STANDARDS dated obligation CAL-01 (full high-risk application of
> Reg. (EU) 2024/1689 on 2026-08-02). Per RESPONSIBLE-TECH-FRAMEWORK RTF-12 this
> is an owner decision artifact behind a REVIEW-GATE: it takes effect when the
> accountable owner reviews it, replaces this banner with a
> `Reviewed <date> by <owner>` line, and re-commits.

**Regulation:** (EU) 2024/1689 (the AI Act). Full high-risk application Aug 2, 2026;
GPAI obligations live since Aug 2025; Annex III conformity deadline Dec 2, 2027.
Per `STANDARDS/RESPONSIBLE-TECH-FRAMEWORK.md` §Governance, the obligation is the
**decision artifact**, not certification: silence is non-conformant, a written
classification is conformant.

- **Accountable owner:** Chelsea Kelly-Reif
- **Re-run trigger:** any material change to the LLM extraction seam, matching
  behavior, or intended use; and on each AI Act enforcement phase-gate.

---

## Classification (the explicit line)

> **constituent-reconciler is a minimal-risk AI system under Reg. (EU) 2024/1689.**
> It is **not** an Annex III high-risk system, **not** a prohibited practice
> (Art. 5), and **not** a general-purpose AI model (GPAI) — its optional
> extraction seam *uses* hosted or local models (Bedrock/Ollama; the DV pack
> forces NoOp), it does not place one on the market. **Training compute = 0**
> (no model is trained or fine-tuned), so the 10^25-FLOP GPAI threshold is not
> in scope.

## Annex III walk (why not high-risk)

The nearest Annex III categories are **5 (essential services)** and
**8 (justice-adjacent use by legal-aid orgs)**. The tool reconciles duplicate
constituent records for nonprofit/legal-aid operators. It does **not** evaluate
any person's eligibility for a service, score or profile constituents, or make
or feed any listed decision: it proposes candidate record matches, and **a human
reviews every uncertain match — nothing merges silently** (the merge-review
invariant is a design gate). The optional LLM seam performs field extraction
from unstructured text only; in the DV pack it is forced to NoOp.

## Art. 50 transparency

Not triggered in normal operation: operators interact with a review UI, not a
conversational AI; extraction is a batch tool. Revisit if a user-facing AI
interaction surface ships.

## Re-classification triggers

Reclassify **before** shipping any of: (1) auto-merge of uncertain matches
without human review; (2) use of match output as an input to eligibility,
service-access, or case-priority decisions about a person; (3) constituent
scoring/profiling features; (4) training or fine-tuning a model.

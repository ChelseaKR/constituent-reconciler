# Ideation: large-scale fixes and expansions

> Restored 2026-08-03 from the rescue/uncommitted-2026-07-05 snapshot; a
> historical planning record. Terminal states for every item named here
> live in docs/ROADMAP-CLOSEOUT.md, and active planning lives in
> docs/NOVEL-USE-CASES-PLAN.md and docs/ROADMAP-MULTIYEAR.md.

Drafted 2026-07-01. This folder is the third documentation layer for
constituent-reconciler, and the most speculative one. The layers relate this
way:

- [`../ROADMAP.md`](../ROADMAP.md) is the canonical build spec: the shipped
  phases (v0.1 through v0.7), the architecture, the eval plan, the metrics
  ledger, and the 1.0 gate.
- [`../RESEARCH-ROADMAP.md`](../RESEARCH-ROADMAP.md) (2026-06-30) is the
  persona-derived backlog: remediation items R1 to R11 and expansions E1 to
  E10, triaged from the synthetic panel in
  [`../USER-RESEARCH.md`](../USER-RESEARCH.md).
- **This folder** is a deep-dive ideation pass over the actual code as it
  stands at v0.7 (commit `88dc26a`). It proposes structural fixes and
  expansions that the two documents above do not already contain. Where an
  idea builds on an existing item it cites that item by ID (R1 to R11, E1 to
  E10) and states what is new; nothing here restates an item those files
  already own.

## Contents

| File | What it holds |
| --- | --- |
| [`01-deep-dive.md`](./01-deep-dive.md) | Current-state assessment from a fresh read of the source: architecture, genuine strengths, observed structural debt, and the repo's position in the portfolio. |
| [`02-large-scale-fixes.md`](./02-large-scale-fixes.md) | FIX-01 to FIX-12: deep structural fixes across correctness, security, privacy, operability, and maintainability, each with an effort tier and a measurable bar. |
| [`03-expansions.md`](./03-expansions.md) | EXP-01 to EXP-16 in three horizons: deepen the core, adjacent capabilities, transformative bets. |
| [`04-impact-and-sequencing.md`](./04-impact-and-sequencing.md) | Impact-by-effort matrix over all IDs, dependency notes, a Now/Next/Later sequence that extends the existing roadmaps, and the items gated on human, legal, SME, or real-data input. |

## What this folder is not

These are ideas for evaluation, not commitments. Nothing here has been
scoped with users, sized against maintainer capacity, or reviewed by counsel.
Several items carry explicit gates (counsel review, a real-organization
pilot, an accessibility SME) and the honest position of this project is that
gated work is deferred and reported as deferred, never simulated. Priorities
in `04-impact-and-sequencing.md` are the author's read of the code, made on
one day, and should be treated the way `RESEARCH-ROADMAP.md` treats its own
priorities: as hypotheses.

# 0000 — Record architecture decisions

Status: accepted (2026-07-14)

## Context

Constituent Reconciler has maintained decision records since its first matcher design, but
the log lived under `docs/decisions/`, lacked the portfolio's meta-ADR, and contained two
files numbered 0009. That made stable references ambiguous and prevented mechanical ADR
integrity checks from distinguishing a complete log from ordinary design notes.

## Decision

Architecture decisions live under `docs/adr/` as `NNNN-kebab-title.md`. Existing records
retain their content and original number except the automated axe audit, whose duplicate
0009 identifier becomes 0011 after the already-established 0010 local-model seam. Accepted
records are append-only; a later decision supersedes an earlier one explicitly.

New records use `docs/adr/template.md` and include Status, Context, Decision, and
Consequences. The date and deciders belong in the status block when the decision is made.

## Consequences

- ADR links and automation have one canonical location and unambiguous numbering.
- Historic decision content remains intact; only paths and the duplicate identifier change.
- Future expensive-to-reverse decisions must extend this log rather than the roadmap.

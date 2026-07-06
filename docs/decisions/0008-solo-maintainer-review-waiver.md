# 0008 — Solo-maintainer review waiver

Status: accepted (2026-07-05)

## Context

The portfolio's CI/CD and Code-Quality standards ask for at least one (general
repos) or at least two (civic/PII repos — this repo qualifies, holding
DV-survivor constituent data) human reviewers on every pull request before
merge, plus no self-merge. `constituent-reconciler` has exactly one
maintainer. A literal reading of "≥2 required reviewers" is structurally
impossible to satisfy solo: there is no second person to review anything,
ever, on this repo. Pretending otherwise (or silently ignoring the control)
would be exactly the kind of misrepresentation-by-omission this remediation
pass exists to close.

History to date is consistent with the gap this ADR now names: `af664c4`,
`f4c65ce` (2026-06-23), `3b40b53` (2026-06-27), and `81b03dd` (2026-07-02) are
direct pushes to `main` with no PR at all; the remainder (PRs #1-#8) are
opened and self-merged by the same person. No branch-protection or ruleset
artifact has existed until this remediation pass.

## Decision

Accept, as a permanent condition of solo maintenance rather than a temporary
gap, that:

* The human-reviewer count requirement (CQ-37/CQ-43, CICD-12/CICD-18) is
  **waived** for as long as this repo has exactly one maintainer with commit
  access. It is not "in progress" or "TODO" — it cannot be met without a
  second maintainer, and inventing a rubber-stamp reviewer would be worse than
  naming the gap.
* What replaces it, as compensating controls documented here so the waiver is
  not a blank check:
  * **Every merge-blocking automated gate still applies and still runs**:
    `verify` (format, lint, type, test), `security` (pip-audit, osv-scanner),
    `secrets` (gitleaks) — see `.github/workflows/ci.yml`. A solo maintainer
    does not get to skip these; only the *human* review step is waived.
  * **The branch ruleset (`docs/rulesets/main.json`, this same PR) still
    requires those status checks, strict up-to-date branches, and blocks
    force-push** — it does not grant an admin bypass. The maintainer merges
    through the same required-checks gate a second reviewer would have to
    clear, they simply are also the one clicking merge.
  * **PR-sized changes stay the norm** even solo (`CONTRIBUTING.md`), so a
    later second maintainer or an external contributor can review history
    incrementally rather than reconstructing intent from a monolith.
  * Should a second maintainer or trusted external reviewer join, the ruleset
    is expected to add a real `required_approving_review_count` and this ADR
    is superseded (ADRs are append-only per `CQ-46`; a new ADR records the
    change, this one is not edited).
* **Trigger to revisit:** the day a second person gains commit/merge rights to
  this repo, required human review must actually turn on in the ruleset — this
  ADR's waiver applies only to the single-maintainer state, not permanently
  regardless of team size.

## Consequences

- The README standards table and `docs/RESPONSIBLE-TECH-AUDITS.md` can state
  the CI/CD and Code-Quality posture honestly: automated gates are real and
  required; the human-reviewer control is a named, dated, reasoned waiver, not
  a silent gap.
- `docs/rulesets/main.json` is the enforcement half of this decision; this ADR
  is the honesty half. Neither is complete without the other.
- Enabling the ruleset itself is a live GitHub repository-settings action and
  is out of scope for an automated remediation pass; see the ruleset file's
  header comment for the exact command the maintainer runs to apply it.

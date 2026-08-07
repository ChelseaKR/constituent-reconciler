# Branch rulesets (committed desired state; a live ruleset is active)

`main.json` is the desired-state ruleset for the `main` branch, matching
`docs/adr/0008-solo-maintainer-review-waiver.md`: PRs required (0
approvals required, since there is one maintainer), the three CI jobs
(`verify`, `security`, `secrets` from `.github/workflows/ci.yml`) required and
strict-up-to-date, force-push and branch deletion blocked, linear history
required, and no bypass actors (no admin override).

**Status: a live ruleset is active; parity with this file is not yet exact.**
A branch ruleset named `protect-main` (id 18752844) has been active on the
live repository since 2026-07-09, verified read-only on 2026-08-07 with
`gh api repos/ChelseaKR/constituent-reconciler/rulesets`. It blocks
force-pushes and branch deletion, has no bypass actors, and requires nine
status-check contexts to merge (`verify`, `security`, `secrets`, `sast`,
`zizmor`, `bundle`, `container-scan`, and CodeQL's `analyze (python)` and
`analyze (actions)`), six more than the three this file names. It differs
from the committed desired state in three ways: the live ruleset omits this
file's `pull_request` rule (review-thread resolution, stale-review
dismissal) and its `required_linear_history` rule, and it applies the
required checks without the strict up-to-date policy. Reconciling the two
(adding the missing rules to the live ruleset, or revising this desired
state) is a repository-settings decision for the maintainer; an automated
conformance pass does not edit live settings on the maintainer's behalf.

## To reconcile the live ruleset with this file

Review `main.json`, then either:

* **UI:** Settings -> Rules -> Rulesets -> `protect-main`, and enter the
  missing rules by hand, or
* **CLI**, once you've reviewed the file and are ready to enforce it
  (updates the existing ruleset in place):

  ```sh
  gh api --method PUT repos/ChelseaKR/constituent-reconciler/rulesets/18752844 \
    --input docs/rulesets/main.json
  ```

  Note this replaces the live rule set wholesale: fold the six extra live
  check contexts into `main.json` first if they should stay required.

After reconciling, refresh the parity note above and the README standards
table's CI/CD row with the date. The applied state itself is recorded (this
file, the README row, and a dated follow-up note in ADR 0008; ADRs are
append-only, so 0008's decision text is not edited).

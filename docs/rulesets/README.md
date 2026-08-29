# Branch rulesets (committed desired state; a live ruleset is active)

`main.json` is the desired-state ruleset for the `main` branch, matching
`docs/adr/0008-solo-maintainer-review-waiver.md`: PRs required (0
approvals required, since there is one maintainer), the three CI jobs
(`verify`, `security`, `secrets` from `.github/workflows/ci.yml`) required and
strict-up-to-date, force-push and branch deletion blocked, linear history
required, and exactly one bypass actor: the repository owner (see below).

**Status: a live ruleset is active; parity with this file is not yet exact.**
A branch ruleset named `protect-main` (id 18752844) has been active on the
live repository since 2026-07-09, verified read-only on 2026-08-07 with
`gh api repos/ChelseaKR/constituent-reconciler/rulesets`, and re-read
2026-08-28. It blocks force-pushes and branch deletion, carries the
repository owner's standing bypass and nothing else, and requires nine
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

## Why the owner can bypass

`bypass_actors` holds exactly the repository owner's standing bypass
(`RepositoryRole` 5, `bypass_mode: always`), deliberately and permanently: an
agent once applied a ruleset with no bypass and locked the owner out of their
own repository, and restoring access took a sweep across eighteen
repositories. An empty list here is not a stricter gate, it is the lockout.

Read off the live ruleset on 2026-08-28:

| Question | Answer |
|---|---|
| `gh api repos/ChelseaKR/constituent-reconciler/rulesets/18752844 --jq .bypass_actors` | `[{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]` |
| `... --jq .current_user_can_bypass` | `"always"` |

Nothing else in the posture moves with it: the nine live check contexts are
still required, and force-push and deletion are still refused. What the bypass
buys is a way back in when a required check is wedged or a workflow breaks
badly enough to stop reporting at all — the case whose only other route is a
support ticket against your own repository. It is one actor, and it is a
repository role rather than a team or a GitHub App; a second bypass actor
appearing in this list would be a real finding, and this one is not.

Until 2026-08-28 this file said `"bypass_actors": []` and this README called
that "no admin override", which made the `PUT` below a way to reproduce the
lockout rather than a way to enforce the reviewed profile. The file now
records the bypass. Whoever runs that command should confirm afterwards that
it survived, by name — it is the field that goes missing quietly.

## To reconcile the live ruleset with this file

**Before either route, check that `main.json`'s `bypass_actors` still holds
`{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}`.**
It said `[]` until 2026-08-28, and enforcing that version is how the owner
gets locked out of this repository. The `PUT` below is the sharpest form of
it, because it replaces the live ruleset wholesale and the live ruleset is
where the owner's bypass currently lives. `POST` is not the safe alternative
either: it adds a second ruleset over `main` rather than replacing the first,
and rules from every applicable ruleset combine while bypass actors are
per-ruleset, so a new ruleset with an empty bypass list blocks the owner
whatever the existing one allows.

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
  check contexts into `main.json` first if they should stay required, and
  check afterwards that
  `gh api repos/ChelseaKR/constituent-reconciler/rulesets/18752844 --jq .current_user_can_bypass`
  still reads `"always"`. A `PUT` that drops the owner's bypass returns 200
  like any other.

After reconciling, refresh the parity note above and the README standards
table's CI/CD row with the date. The applied state itself is recorded (this
file, the README row, and a dated follow-up note in ADR 0008; ADRs are
append-only, so 0008's decision text is not edited).

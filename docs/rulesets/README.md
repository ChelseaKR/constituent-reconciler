# Branch rulesets (committed artifact, not yet applied)

`main.json` is the desired-state ruleset for the `main` branch, matching
`docs/adr/0008-solo-maintainer-review-waiver.md`: PRs required (0
approvals required, since there is one maintainer), the three CI jobs
(`verify`, `security`, `secrets` from `.github/workflows/ci.yml`) required and
strict-up-to-date, force-push and branch deletion blocked, linear history
required, and no bypass actors (no admin override).

**Status: not yet applied.** Creating or editing a live repository ruleset is
a GitHub-settings write action, which an automated remediation pass does not
take on this maintainer's behalf. Confirmed via a read-only check
(`gh api repos/ChelseaKR/constituent-reconciler/rulesets`) on 2026-07-05: no
ruleset exists on this repo yet.

## To apply this

Review `main.json`, then either:

* **UI:** Settings -> Rules -> Rulesets -> New branch ruleset, and enter the
  same values by hand, or
* **CLI**, once you've reviewed the file and are ready to enforce it:

  ```sh
  gh api --method POST repos/ChelseaKR/constituent-reconciler/rulesets \
    --input docs/rulesets/main.json
  ```

After applying, update the README standards table's CI/CD row from "gap" to
"enforced" and drop the "pending" language in ADR 0008's Consequences section
(ADRs are append-only — add a follow-up note or a new ADR rather than editing
0008 itself).

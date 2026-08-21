# External gates runbook

**Drafted:** 2026-08-03
**Source of the gate list:** the "Canonical 1.0 gates" table in
[ROADMAP-CLOSEOUT.md](./ROADMAP-CLOSEOUT.md)

Five rows in the closeout's canonical 1.0 gates table carry the state
"External gate". The closeout is explicit about what that means: these items
"are not engineering backlog and cannot be completed by adding fixtures."
Each needs a human act, an authorized live system, or a real outside
organization. What engineering can still do is write the act down precisely,
so each gate becomes a bounded errand with defined steps and a known place to
record the result. That is this document.

One principle governs every section, quoted from the closeout's product
principles: "No fabricated evidence: live integrations, accessibility review,
adoption, and registry submission stay visibly external." No step below may be
satisfied by a fixture, a synthetic result, a spliced recording, or an
inference about what a human would have found. Where a gate stalls, the honest
state is "still open", recorded as such.

Shared bookkeeping when any gate closes:

1. Update the gate's row in ROADMAP-CLOSEOUT.md's canonical table with the
   date and the evidence location.
2. Update the README status note and the matching row of the metrics ledger in
   [ROADMAP.md](./ROADMAP.md) where one exists.
3. Apply the claims-audit discipline from
   [NOVEL-USE-CASES-PLAN.md](./NOVEL-USE-CASES-PLAN.md)'s definition of done:
   a claims-audit update "that distinguishes shipped code from live
   evidence". [CLAIMS-AUDIT.md](./CLAIMS-AUDIT.md) already models this in its
   Airtable row ("live-account evidence remains external"); when live evidence
   lands, the row gains a date instead of that caveat.
4. Record the change in CHANGELOG.md.

Ordering matters in one place: gate 2 (the first release and the live ruleset)
must complete before gate 5 (the schema-stability window) can even start,
because the window is measured across published releases. The other gates are
independent of each other and can run in parallel.

---

## Gate 1: Screen-reader walkthrough, reviewed Spanish UI copy, ACR

**Closeout row:** "Screen-reader walkthrough, reviewed Spanish UI copy, ACR |
External gate | `docs/reviews/SCREEN-READER-WALKTHROUGH.md`, `docs/I18N.md` |
assistive-technology user, native Spanish reviewer, signed audit result."

**Why it is external.** The closeout's R1 row states it directly: "The
remaining assistive-technology, reviewed Spanish, and ACR work requires
qualified humans." The walkthrough document itself explains why no script can
substitute: an automated scan "can confirm markup conforms to a rule set; it
cannot tell you whether the rationale sentence reads sensibly out loud,
whether the reading order matches the visual order at a table with scoped
headers, or whether a real screen-reader user can complete a review pass
without seeing the screen."

**Already in the repo.**

- [reviews/SCREEN-READER-WALKTHROUGH.md](./reviews/SCREEN-READER-WALKTHROUGH.md)
  is a complete tester-facing checklist: a four-step setup (clone,
  `make install`, `reconcile run` and `reconcile review` against
  `examples/intake-demo/recipe.toml`, pick a screen reader matched to the OS),
  an eleven-step walkthrough script, and an intentionally unfilled Results
  table. Its status line reads "not yet performed" and it forbids filling the
  results "from inference, another tool's output, or a prior run of a
  different tool."
- The automated half of the accessibility gate is done and enforced:
  `scripts/axe_audit.mjs` runs as the `accessibility` job in
  `.github/workflows/ci.yml` (locally, `make axe`), per
  docs/adr/0011-automated-axe-audit.md.
- [I18N.md](./I18N.md) records the Spanish-copy state honestly: UI and CLI
  strings are hardcoded English, there is no `locales/` directory yet, and the
  plan calls for a gettext catalog plus a reviewed Spanish translation with "a
  named reviewer (not machine translation, given the sensitivity of
  DV/caseworker-facing language)" at a Grade 8 or lower reading level.
- One Spanish surface exists today: the narrative report's `_STRINGS` table in
  `src/constituent_reconciler/narrative.py` renders EN and ES from the same
  keys, with parity tests in `tests/test_narrative.py`. The README says
  plainly that "the Spanish strings are a machine-drafted translation awaiting
  review by a native speaker."
- docs/RESPONSIBLE-TECH-AUDITS.md's Accessibility section names all of this as
  the open remainder before the 1.0 accessibility claim, including committing
  the ACR.

**Steps, part A: the screen-reader walkthrough.**

1. Recruit a tester. The checklist's own bar is "anyone comfortable with a
   screen reader at a beginner-to-intermediate level", but the closeout row
   names an assistive-technology user, so aim for someone who uses a screen
   reader in daily life. Outreach channels consistent with this project's
   stated community: the nonprofit-tech and civic-tech groups CLAUDE.md names
   as the second audience (Code for America's volunteer network, Open
   Referral, NNEDV Safety Net contacts), plus accessibility-testing
   communities. Offer to compensate the tester's time; an hour of a
   professional AT user's attention is skilled work.
2. Send the tester the walkthrough document itself. It is written to be
   self-serve: setup steps 1 through 4, then the eleven numbered walkthrough
   steps performed "with the screen reader driving and the mouse untouched."
3. While the tester works, take notes but do not steer. The document asks for
   surprises, not only failures: "a label that is technically present but
   confusing is worth recording."
4. Fill the Results table with the fields the document lists: date, tester,
   OS, browser, screen reader and version, and one line per numbered step
   (pass, pass with a note, or fail with what happened). The document asks
   only for "tester"; this runbook's own recommendation is to record a name
   or a consented pseudonym, so the tester decides how they are identified
   in a public repository.
5. File a GitHub issue for every fail and every note. The document requires
   this before its status line changes: "Any fail or note should become a
   linked issue before this file's Status line at the top is changed to
   'performed.'"
6. Commit the filled table and the status-line change in one PR, linking the
   issues.

**Steps, part B: the native-Spanish copy review.**

1. Scope what is reviewable today. The only shipped Spanish copy is the
   narrative report's `es` strings in `narrative.py`. The review-UI catalog
   does not exist yet; extracting it is pre-1.0 engineering work per
   I18N.md's plan and is not part of this errand. When that catalog lands,
   repeat this same process for it.
2. Recruit a native (or fully fluent) Spanish speaker, ideally with
   human-services or caseworker context, since the copy is board- and
   funder-facing language about constituent data. The same outreach channels
   as part A apply. I18N.md rules out machine translation as the reviewer.
3. Have the reviewer read the rendered pages, not the source table: run
   `reconcile run --config examples/intake-demo/recipe-dv.toml --out out-dv`
   then `reconcile report --run-dir out-dv --lang es` and `--lang en`, and
   review the Spanish page against the English one for accuracy, register,
   and the plain-language bar (Grade 8 or lower, matching the English copy's
   bar per I18N.md).
4. Apply the reviewer's corrections to `_STRINGS` in `narrative.py` as a PR.
   The EN/ES parity tests in `tests/test_narrative.py` keep the key structure
   aligned while the values change.
5. Record the reviewer's name in I18N.md (with their consent), and update the
   README sentence that currently says the Spanish strings await native
   review.

**Steps, part C: the ACR.**

1. An Accessibility Conformance Report is a per-criterion conformance
   statement for the review UI against WCAG 2.2 AA. Produce it only after
   parts A and B exist, because the manual findings are its substance; the
   axe results alone are the half a script already covers. The VPAT template
   and the OpenACR format are the common shapes; either is acceptable, and the
   report should say which it used.
2. The closeout asks for a "signed audit result": the report names and is
   signed (a written attestation is sufficient) by the person or organization
   that performed the evaluation. Be plain about who that was. If the
   walkthrough tester and the maintainer co-produced it, the ACR is a
   self-assessment with an external tester and must say so; an independent
   audit firm's ACR is stronger evidence and can replace it later.
3. Commit the ACR under `docs/reviews/` beside the walkthrough, and link it
   from the Accessibility section of RESPONSIBLE-TECH-AUDITS.md.

**Evidence and where it is recorded.** The filled Results table and changed
status line in `docs/reviews/SCREEN-READER-WALKTHROUGH.md`; the corrected
`es` strings with a named reviewer in `docs/I18N.md`; the committed ACR under
`docs/reviews/`; the updated "Review queue accessibility" and "i18n parity
(EN, ES)" rows in ROADMAP.md's metrics ledger.

**If it goes wrong.** A tester who cannot complete setup has found a real
finding; record it, fix it, and restart the walkthrough. A walkthrough with
fails is a valid, committable result: file the issues, fix them, and re-run
the affected steps with the same or a new tester, keeping the dated history
rather than overwriting it. If recruitment stalls, a maintainer-run pass
satisfies the checklist's stated bar and is worth committing as an interim
result, but label it as maintainer-run and keep the gate open until a real
assistive-technology user has done a pass, because that is what the closeout
row requires. Under no circumstances fill any cell of the Results table from
reading the HTML and imagining how it would sound; the document forbids
exactly that.

---

## Gate 2: First signed release and live required-check ruleset

**Closeout row:** "First signed release and live required-check ruleset |
External gate | `.github/workflows/release.yml`, `docs/rulesets/main.json` |
authorized tag/release and GitHub repository-settings change."

**Why it is external.** Both halves are live acts under the maintainer's
authority. `release.yml`'s header says cutting the first tag "is a maintainer
decision, not something this remediation pass performs", and
docs/rulesets/README.md says the same about the ruleset: "Creating or editing
a live repository ruleset is a GitHub-settings write action, which an
automated remediation pass does not take on this maintainer's behalf."

**Already in the repo.**

- `.github/workflows/release.yml` triggers on a pushed `v*` tag and runs five
  jobs: `version-check` (the tag must equal `pyproject.toml`'s `version`
  exactly, minus the `v`), `release-tests` (`make install`, `make verify`,
  and the dependency-vulnerability gate via `make security` with a
  checksum-verified osv-scanner, re-run at the tagged commit rather than
  trusting a stale PR check), `changelog-section` (CHANGELOG.md must contain
  a `## [X.Y.Z]` section, extracted verbatim as the release notes),
  `build` (sdist and wheel via `uv build`, a CycloneDX 1.7 SBOM, and a
  keyless OIDC build-provenance attestation via
  `actions/attest-build-provenance`), and `github-release` (`gh release
  create` with the notes file, `--verify-tag`, and the `dist/*` assets). There
  is no PyPI publish stage; the header says to add one via Trusted Publishing
  only when the project decides to publish.
- `docs/rulesets/main.json` is the desired-state ruleset "protect-main":
  active enforcement on the default branch, pull requests required with zero
  required approvals and required review-thread resolution, required status
  checks `verify`, `security`, and `secrets` with the strict up-to-date
  policy, force-pushes and deletion blocked, linear history required, and no
  bypass actors. Those three check contexts match the job names in
  `.github/workflows/ci.yml`.
- `docs/rulesets/README.md` records that a live `protect-main` ruleset has
  been active since 2026-07-09 (verified read-only 2026-08-07), lists its
  delta from the committed desired state, and gives the reconciliation
  command.
- docs/adr/0008-solo-maintainer-review-waiver.md is the honesty half of the
  ruleset decision: zero required approvals is a named waiver with
  compensating controls, not an oversight.
- CHANGELOG.md keeps an `[Unreleased]` section, and `pyproject.toml` reads
  `version = "0.7.0"` as of this writing. No `v*` tag exists yet.

**Steps, part A: apply the ruleset.**

> **Status (2026-08-07): partially done.** A `protect-main` ruleset
> (id 18752844) has been active since 2026-07-09. It blocks force-push and
> deletion and requires nine check contexts, but omits the committed
> profile's pull-request, linear-history, and strict up-to-date rules. The
> remaining part A work is the reconciliation described in
> `docs/rulesets/README.md`; the step 4 recording edits for the applied
> state were made by the 2026-08-07 conformance pass.

1. Re-read `docs/rulesets/main.json` and confirm the three required check
   contexts still match the job names in `ci.yml`.
2. Apply it. The committed instruction offers both routes:

   ```sh
   gh api --method POST repos/ChelseaKR/constituent-reconciler/rulesets \
     --input docs/rulesets/main.json
   ```

   or by hand under the repository Rules settings (docs/rulesets/README.md
   records the path as Settings, then Rules, then Rulesets, then New branch
   ruleset; if the UI has drifted since that was written, look under the
   repository Rules settings rather than trusting the exact menu names),
   entering the same values as the JSON.
3. Verify it took: `gh api repos/ChelseaKR/constituent-reconciler/rulesets`
   (the same read-only check the README used) should now list `protect-main`,
   and a test branch pushed and opened as a PR should show `verify`,
   `security`, and `secrets` as required checks.
4. Record it: update the README standards table's CI/CD row from "not yet
   applied" language to enforced, and add a follow-up note or a new ADR
   rather than editing ADR 0008 (the ruleset README is explicit that ADRs are
   append-only). Update the status line in `docs/rulesets/README.md` with the
   date.

**Steps, part B: the signed-tag release ceremony.**

1. Preconditions, checked before anything is tagged:
   - Actions runs on this repository are actually executing. Run
     `gh run list --limit 5` and confirm recent runs completed rather than
     sitting queued or never starting. An account-level Actions spending
     limit or billing restriction stops workflows from running at all; clear
     any such block first, because the tag push fires `release.yml`
     immediately and a tag pointing at a workflow that never ran is not a
     release.
   - A tag-signing key is configured for git. Either GPG
     (`git config user.signingkey <keyid>`) or SSH signing
     (`git config gpg.format ssh` plus `git config user.signingkey
     <path-to-public-key>`), with the same key registered as a signing key on
     the GitHub account so the tag shows as verified there. Note the honest
     boundary: `release.yml` does not check the tag's signature
     (`gh release create --verify-tag` checks that the tag exists, not who
     signed it). The signature is the maintainer's act; the workflow adds the
     keyless build-provenance attestation on top.
2. Prepare the release PR on a branch:
   - Choose the version `X.Y.Z` (the next release from the current `0.7.0`,
     per the versioning contract in docs/adr/0006-schema-stability.md).
   - Move the `[Unreleased]` content of CHANGELOG.md into a dated
     `## [X.Y.Z] - YYYY-MM-DD` section. The `changelog-section` job fails the
     release if this section is missing.
   - Set `version = "X.Y.Z"` in `pyproject.toml`. The `version-check` job
     fails on any mismatch with the tag.
   - Run the release-time regeneration the repo asks for: `make verify`, plus
     `make eval-large` (the Makefile marks the large-corpus report as a
     release-time regeneration, not a per-push one), and the claims-audit
     re-run steps listed at the bottom of docs/CLAIMS-AUDIT.md.
3. Merge the PR through the now-live ruleset. With part A applied, this
   change reaches `main` only via a PR with `verify`, `security`, and
   `secrets` green and the branch up to date.
4. Tag the merged commit on `main` and push the tag. The ruleset targets the
   default branch, so the tag push itself is not blocked by it:

   ```sh
   git switch main
   git pull --ff-only
   git tag -s vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

5. Watch the run: `gh run list --workflow=release.yml`, then
   `gh run watch <run-id>`. All five jobs must pass.
6. Verify the outcome before calling the gate closed:
   - `gh release view vX.Y.Z` shows the release with the sdist, the wheel,
     and `sbom.cdx.json` attached, and notes matching the CHANGELOG section.
   - Download one built artifact and verify its provenance, which is the
     check the workflow's own comments promise:
     `gh attestation verify <file> --repo ChelseaKR/constituent-reconciler`.
   - The tag shows as verified on GitHub (the signature half).

**Evidence and where it is recorded.** The live evidence is external by
nature: the applied ruleset in repository settings, the signed `vX.Y.Z` tag,
and the published GitHub Release with SBOM and attestation. In the repo,
record it by updating the README standards table rows for CI/CD and Release &
Versioning (both currently name these exact gaps), the ROADMAP-CLOSEOUT gate
row, and the `docs/rulesets/README.md` status line, each with dates.

**If it goes wrong.** A failed `version-check` or `changelog-section` job
means the tag went out ahead of the metadata; fix the file on `main` through
a PR, delete the unpublished tag (`git push --delete origin vX.Y.Z` and
`git tag -d vX.Y.Z`), and re-tag the corrected commit. That delete-and-retag
move is acceptable only while nothing was published; once the
`github-release` job has created a public release, do not delete and reuse
the version, cut a patch release instead. A failed `release-tests` job means
`main` itself does not verify at the tagged commit, which is exactly what the
job exists to catch; fix forward on `main` and tag the fixed commit as the
release. If the ruleset apply call is rejected by the API, read the error
before editing anything: the committed JSON is desired state written in
2026-07 and GitHub's ruleset schema may have moved since; adjust the payload
to the current API shape while keeping the same intent, and record the
adjustment in `docs/rulesets/README.md`. If workflow runs sit queued and
never start, that is an account-level Actions restriction, not a workflow
bug; resolve the account state before re-pushing tags.

---

## Gate 3: Recorded CiviCRM end-to-end demonstration

**Closeout row:** "Recorded CiviCRM end-to-end demonstration | External gate |
adoption kit and demo recipe | authorized running CiviCRM instance and
recording."

**Status 2026-08-21: still open, partial evidence added.** A disposable local
instance was stood up and the CLI sequence below was run against it end to
end (including a full repair-path pass, `plan-split` through
`apply-repair --execute`), but as a committed script and content-free
transcript, not the published video recording this gate's evidence line
calls for — no interactive screen-capture tooling was available. See
`docs/reviews/CIVICRM-LIVE-DEMONSTRATION-2026-08-21.md` for what that
evidence covers, what it does not, and a live discrepancy it found and fixed
(issue #113: `apply_repair`'s field-restore was looking the survivor up by
the wrong column). The steps below remain the procedure for whoever records
the video that actually closes this gate.

**Why it is external.** The connector's code-level behavior is already the
closed "CiviCRM adapter behavior" row ("dedicated Contact/Email/Phone writes
and injected-transport tests"). What the tests cannot produce is a write into
a real, running CiviCRM that a viewer can watch land. That needs an instance
someone is authorized to write to, and a recording of the run, neither of
which a fixture can stand in for.

**Already in the repo.**

- [ADOPTION-KIT.md](./ADOPTION-KIT.md) walks the full sequence the recording
  should show: `reconcile validate`, a `--dry-run`, the review queue with
  `--reviewer`, `reconcile apply`, the live write with the API key passed
  through the environment, and `reconcile verify` over the provenance log.
- `examples/intake-demo/recipe-civicrm.toml` is the demo recipe: connector
  `civicrm`, an API v4 endpoint (`.../civicrm/ajax/api4`), the key read from
  `CIVICRM_API_KEY` (its header comment says the key is "never stored in this
  file"), and the upsert keyed on `external_identifier` so "a second run
  updates the same contacts instead of creating duplicates."
- `examples/intake-demo/recipe-civicrm-csv.toml` covers the offline
  import-file path if the demonstration wants to show both write paths.
- The demo CSVs (`examples/intake-demo/existing.csv`, `incoming.csv`) carry
  the two known lookalike pairs the walkthrough document references, so the
  review step has real content on camera.

**Steps.**

1. Stand up a CiviCRM instance you are authorized to write to. A disposable
   local instance is the right shape: install it from CiviCRM's own current
   documentation (a container-based development setup or a standard
   CMS-hosted install both work). Do not record against any organization's
   production system, even with permission; the demonstration needs a
   throwaway target.
2. In that instance, create an API user and key with permission to create and
   edit contacts, and note the exact CiviCRM version. The recording's written
   record must name that version, because the demonstration is evidence
   against it, not against every CiviCRM.
3. Copy `examples/intake-demo/recipe-civicrm.toml` and set `endpoint` to your
   instance's API v4 URL. Use only the bundled demo CSVs. Never put real
   constituent data in a recording.
4. Provision the credential and set up the recording, both before pressing
   record. Export `CIVICRM_API_KEY` in the recording shell off camera (or
   source it from a file): the live-writing command in the script below reads
   the key from the environment and fails without it, so the environment must
   be ready before any step that writes. Then set up the screen capture, an
   OS-native recorder or OBS Studio, showing the terminal and the browser.
   Two hygiene rules: the key must never appear on screen (do not echo it,
   and do not scroll shell history on camera), and the CiviCRM instance must
   contain no data you would not publish.
5. Record the run end to end, narrating or captioning each step. The labels
   below state what each command does in the code: `validate`, the dry run,
   `review`, and `verify` never contact the server, while `reconcile apply`
   exports through the recipe's `civicrm` connector with dry run off and is
   the live write. A plain `reconcile run` without `--dry-run` would also
   write live, but it ignores the decisions file, so it has no place in this
   script; the point of the recording is that the reviewed decisions reach
   the CRM.
   - `reconcile validate --config recipe-civicrm.toml` (local: shape-checks
     the recipe without running the pipeline or building a connector)
   - `reconcile run --config recipe-civicrm.toml --out out --dry-run` (local
     dry run: shows the summary of what would be written without contacting
     the server)
   - `reconcile review --config recipe-civicrm.toml --reviewer "<name>" --out
     out` (local: decide the lookalike pairs on camera; the decisions are
     saved to `out/decisions.json` and nothing reaches CiviCRM yet)
   - `reconcile apply --config recipe-civicrm.toml --decisions
     out/decisions.json --out out` (live write: re-resolves with the
     on-camera decisions applied and writes the result into CiviCRM, using
     the key exported in step 4)
   - the CiviCRM UI showing the created contacts with `external_identifier`
     populated
   - a second `reconcile apply` with the same decisions file (live write),
     then the CiviCRM UI again, showing the same contacts updated rather
     than duplicated (the idempotence the recipe header promises)
   - `reconcile verify --provenance out/provenance.jsonl` (local: checks the
     hash chain over the provenance entries the live writes appended)
6. Publish the recording somewhere durable that the maintainer controls. A
   GitHub Release asset is a reasonable home once gate 2 exists; a video file
   does not belong in the git tree.

**Evidence and where it is recorded.** The recording itself, plus a committed
pointer: a short dated note (in ADOPTION-KIT.md or a new file under
`docs/reviews/`) recording the date, the CiviCRM version demonstrated, the
package version that ran, and where the recording lives. Update the CiviCRM
row in CLAIMS-AUDIT.md so the live-demonstration evidence carries a date, the
same move the Airtable row is waiting to make.

**If it goes wrong.** A live-instance failure is a finding, not an
embarrassment: if the write fails against a real CiviCRM (an entity shape the
injected-transport tests did not anticipate, an auth scheme difference), file
the issue, fix the connector through the normal PR gates, and re-record from
the start. Do not edit around a failure in post; a spliced recording is
fabricated evidence. If standing up CiviCRM proves harder than expected, that
experience is itself worth a note in the adoption kit, since an adopting
organization's administrator faces the same task.

---

## Gate 4: More than one real adopting organization

**Closeout row:** "More than one real adopting organization | External gate |
adoption materials only | real organizations; synthetic evidence is not
substituted."

**Why it is external.** Adoption is a fact about other organizations'
decisions. The closeout row already rules out the only shortcut: "synthetic
evidence is not substituted." ADOPTION-KIT.md frames the same point from the
other side: "The 1.0 stability tag is gated on the pipeline proving out
against real organizations, not synthetic fixtures."

**Already in the repo.**

- [ADOPTION-KIT.md](./ADOPTION-KIT.md) is the complete pilot path: the
  two-CSV starting point, the recipe walkthrough, policy-pack selection with
  a counsel checkpoint for DV programs, the validate/dry-run/review sequence,
  eval against the organization's own ground truth, both write paths, the
  provenance log, and a pilot-readiness checklist. Its closing paragraph
  names the feedback channel: pilots open GitHub issues, and
  "real-organization feedback is the evidence the 1.0 tag is waiting on."
- The README speaks to practitioners, and docs/USER-RESEARCH.md records
  persona-level adopts-if and walks-if conditions to target outreach with.
- Self-hosting paths exist for constrained environments: `make docker` and
  docs/INSTALL-OFFLINE.md's offline bundle.

**Steps.**

1. Build a shortlist of candidate organizations matching the audience
   CLAUDE.md defines: small and mid-sized human-services nonprofits whose
   staff re-type intake into a CRM and fight duplicates. Warm channels first:
   the communities CLAUDE.md names (Open Referral, NNEDV Safety Net, Code for
   America), plus any direct contacts. For a victim-service provider, the
   adoption kit's own rule applies from the first conversation: the policy
   pack question goes to their counsel before the pilot, not after.
2. Make the ask small and concrete, because the kit already is: a first pass
   is about an hour, a few hundred rows, on a copy of their data, on their
   machine. Send the README and the adoption kit; do not build custom
   materials that promise more than the repo does.
3. Support the pilot without touching their data. The kit's design keeps
   constituent data on the organization's machine, and the maintainer's
   support should too: help over a call with counts, queue sizes, and error
   text, never by accepting a copy of their constituent records.
4. Let the kit's four "What a pilot proves" questions define success, and ask
   the organization to answer them: do the defaults find their duplicates, can
   a non-technical colleague run the review queue, does write-back land keyed
   for re-runs, and does the DV pack hold data locally if they need it to.
5. Ask each piloting organization to file their findings as GitHub issues.
   Public issues from a real organization are the most durable adoption
   evidence there is, and the kit already requests them.
6. When an organization is genuinely using the tool (real data, their own
   staff, continuing use or a stated intent to continue), ask permission to
   record them as an adopter. Consent shapes the record: a named organization
   with a date, or, where naming is unsafe (a DV shelter, for instance), an
   anonymized description recorded with the organization's written consent
   and an explicit note that the identity is withheld by agreement.

**Evidence and where it is recorded.** No adopters file exists yet, so create
one when the first adoption is real: a dated `docs/ADOPTERS.md` with one entry
per organization (name or consented anonymous description, date, what they
run, which connector, links to their issues). The gate row reads "more than
one", so it closes at the second entry, not the first. Update the README
status note and the closeout row when it does.

**If it goes wrong.** Most outreach will not convert, and none of the
near-misses count. An organization that ran the bundled demo but never its
own data is not an adopter. A pilot that walks away is feedback, recorded in
issues, not adoption. The maintainer's own runs never count, whatever data
they use. If a pilot surfaces a gap that blocks adoption, that gap becomes
ordinary engineering backlog and the gate stays open; the one move this
runbook rules out is softening the definition of "adopter" until the count
reaches two.

---

## Gate 5: Demonstrated schema-stability window

**Closeout row:** "Demonstrated schema-stability window | External gate |
declared versions and ADR 0006 | two real releases without a breaking
change."

**Why it is external.** The demonstration is elapsed release history. ADR
0006 built the mechanism precisely so this could later be shown honestly:
naming the surfaces "is what lets the next two releases demonstrate 'no
breaking change for two consecutive releases,' which is one of the 1.0
gates." No amount of code produces two published releases; only releasing
does.

**Already in the repo.**

- `src/constituent_reconciler/schema.py` declares the versioned surfaces as
  integers, each with its change history in a comment:
  `CONFIG_SCHEMA_VERSION = 1`, `CONNECTOR_INTERFACE_VERSION = 1`,
  `REPORT_SCHEMA_VERSION = 3`, and `DECISIONS_SCHEMA_VERSION = 2`.
  `reconcile schema` prints them.
- docs/adr/0006-schema-stability.md states the contract: before 1.0 a surface
  may change with a MINOR bump and a CHANGELOG entry; additive changes (a new
  optional recipe key, a new connector, a new JSON field) are not breaking;
  "removing or renaming a key, changing a default that alters results, or
  changing the meaning of an existing field is breaking."
- The release machinery is gate 2's; this gate consumes it.

**Steps.**

1. Complete gate 2 first. The window starts at the first published release.
2. At each release, record the state of the declared surfaces where a later
   reader can find it: the CHANGELOG section for the release should note the
   `reconcile schema` output (four integers) and either "no breaking change
   to the declared surfaces" or the migration note ADR 0006 requires.
3. Between releases, review every PR that touches the recipe shape, the
   `Connector` protocol, or the JSON artifacts against ADR 0006's
   breaking-change definition before merge, and say in the PR which side of
   the line the change falls on.
4. Cut the second release the same way. The window is demonstrated when two
   consecutive published releases carry no breaking change to the named
   surfaces.
5. Close the gate with a dated note citing the two tags and the surface
   versions at each, recorded as an append-only follow-up to ADR 0006 (or a
   new ADR) and in the closeout row. Citing the exact tags is what makes the
   claim checkable by anyone with `git diff` between them.

**Evidence and where it is recorded.** Two tagged releases on GitHub whose
CHANGELOG sections record the declared surface versions and the absence of
breaking changes; the dated closure note beside ADR 0006; the updated
closeout row.

**If it goes wrong.** A breaking change mid-window is permitted by the
pre-1.0 contract, and taking it is sometimes right. The cost is honest: ship
it with the MINOR bump and migration note ADR 0006 requires, and restart the
window at that release. The failure mode to refuse is the quiet one,
classifying a breaking change as additive so the window survives. A stability
claim built that way would be the same overclaim the ADR names and rejects
(its comparison is CASS certification), and it would surface the first time a
consumer's recipe or report parser broke against a release inside the
supposedly stable window.

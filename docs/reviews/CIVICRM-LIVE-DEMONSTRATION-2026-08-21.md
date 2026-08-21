# Live CiviCRM demonstration — 2026-08-21

Dated pointer note for [EXTERNAL-GATES-RUNBOOK.md](../EXTERNAL-GATES-RUNBOOK.md)'s
Gate 3 ("Recorded CiviCRM end-to-end demonstration"), issue #67, and the
`apply_repair` pilot from #79.

## What this is, and what it is not

**This is not the video recording Gate 3's runbook asks for.** The runbook's
own steps call for a screen-captured recording of a browser and terminal,
published as a durable asset. This environment has no interactive screen
capture available, so what follows is the alternative the tooling available
here can actually produce and stand behind: a committed, reproducible script
that drives the real `reconcile` CLI end to end against a real running
CiviCRM instance, a full transcript of every command and its output, and
SHA-256 hashes of every artifact the run touched. The transcript is
content-free by construction — command lines, counts, ids, and hashes, never
a raw field value — so it is safe to commit outright, unlike a recording
that would show a browser full of (synthetic) constituent data on screen.

Gate 3 is **not closed** by this note. Standing up an instance, exercising
every step the runbook lists, and publishing a video recording remains open
work for whoever does the recording. What this note and its evidence do
close: the code-level uncertainty about whether the CiviCRM adapter and the
`apply_repair` repair path actually work against a real, running instance —
they do, and one place they did not was found and fixed in the process.

## What ran, against what

- **Date:** 2026-08-21 (UTC timestamps in the transcripts below)
- **CiviCRM:** version 6.17.2, API v4, Standalone UF, read live via
  `Domain.get` at the start of the run (never assumed)
- **Instance:** disposable local `civicrm/civicrm-docker` (image
  `civicrm/civicrm:6.17.2-php8.3` + `mariadb:10.11`), no client PII, torn
  down / wiped after each verification pass
- **Reconciler:** the `main` branch at the point this evidence was produced;
  `run_live_demo.py`'s transcript records the exact commit hash it ran
- **Sources:** the repo's own committed synthetic demo fixtures
  (`examples/intake-demo/existing.csv`, `incoming.csv`), the same fixtures
  the test suite uses — zero real client PII, planted ground truth

## Evidence in this directory

- [`run_live_demo.py`](./run_live_demo.py) — the script. Reproducible: point
  a disposable CiviCRM instance's endpoint at
  [`recipe-live.toml`](./recipe-live.toml), export `CIVICRM_API_KEY`, run it
  with the repo's own venv interpreter.
- [`recipe-live.toml`](./recipe-live.toml) — the demo recipe, `[consent]
  require = true` added on top of `examples/intake-demo/recipe-civicrm.toml`
  so the consent-withheld path is genuinely exercised.
- [`transcript.txt`](./transcript.txt) — the full run: `reconcile validate`,
  a `--dry-run`, a real `reconcile run` (live write), review of the two
  uncertain pairs decided against the fixture's planted ground truth,
  `reconcile apply` (live write of the reviewed decisions), `reconcile
  verify`, live reads confirming Contact/Email/Phone/external-id/consent
  behavior, a full rerun into a fresh local output directory (idempotency:
  updates, never duplicates), then the repair path — `plan-split`,
  `approve-repair` twice, `apply-repair` dry-run, `apply-repair --execute`,
  and a second `--execute` proving the repair itself is idempotent.
- [`summary.json`](./summary.json) — the same run's machine-readable summary
  and artifact hashes.
- [`field_restore_check/`](./field_restore_check/) — a small supplementary
  check, explained below.

## What was verified

Every acceptance point in issue #67 that a script-and-transcript can
demonstrate (not requiring a human reviewer's on-camera judgment or a
published video):

- First run and rerun show expected create/update counts with no duplicate
  contact (verified both from the CLI's own printed summary and a live
  `Contact.get` count that stays at 1 across the rerun).
- Email and phone land in the dedicated `Email`/`Phone` entities, not a
  `Contact` join-field (verified live for one email-bearing and one
  phone-bearing record).
- A revoked-consent record (`incoming:N009`, "Omar Said") never reaches
  CiviCRM — confirmed by a live `Contact.get` on its external id returning
  zero rows, and by the record appearing in the local `withheld.csv`.
- `reconcile verify` passes over both run directories' provenance logs.
- The auto-merged pair (`existing:E003`/`incoming:N002`) lands as one
  contact, and the repair path (`plan-split` → two distinct
  `approve-repair` calls → `apply-repair --execute`) correctly splits it:
  `incoming:N002` gets its own new contact, and a second `--execute`
  changes nothing (`already-exists`, not a duplicate create).

## A live discrepancy became an engineering issue before closure

This exercise is also what issue #67 asks for structurally: proof that a
real destination catches what fixtures cannot. The main demonstration's
auto-merged cluster happened to have an **empty** `restore_fields` list (the
fill policy pulled every field from the survivor already), so it only
exercised `split-create`, never `field-restore`, against the live instance.

Building the targeted supplementary check in
[`field_restore_check/`](./field_restore_check/) — two synthetic records
where the survivor's own email is blank and the fill policy pulls the
golden record's email from the member being split away — forced a real
`restore_fields` entry, and the first attempt against the live instance
**failed**: `apply_repair`'s field-restore step looked the survivor contact
up by `external_identifier`, but `old_external_id` (as recorded in the
provenance log and the repair plan) is CiviCRM's own numeric contact id,
never the `external_identifier` string. That lookup could never match.

Filed as [#113](https://github.com/ChelseaKR/constituent-reconciler/issues/113)
and fixed in the same change that adds this evidence:
`connectors/civicrm.py` now takes `old_external_id` as CiviCRM's numeric
primary key directly (`_existing_contact_id`), confirming the contact still
exists via `Contact.get where id = <int>` rather than querying the wrong
column. Verified live after the fix
([`field_restore_check/transcript.txt`](./field_restore_check/transcript.txt)):
the survivor's `Email` entity row was correctly cleared, the split-off
contact was correctly created holding the misattributed value, and a rerun
was idempotent (`unchanged` / `already-exists`, no duplicate writes). Two
new unit tests
(`tests/test_connectors_civicrm.py::test_apply_repair_field_restore_errors_when_survivor_contact_is_gone`
and `::test_apply_repair_field_restore_treats_a_non_numeric_old_external_id_as_absent`)
cover the fail-closed behavior directly, without needing a live instance to
catch a regression here again.

## What this does not cover

- No human reviewer made a judgment call on camera; the review step in the
  transcript is driven programmatically against the fixture's planted
  ground truth (`examples/intake-demo/ground_truth.json`), which is the
  right substitute for scripted, reproducible evidence but is not what the
  runbook's video asks for.
- No published recording exists. Gate 3 and issue #67's "dated recording"
  criterion are not satisfied by this note alone; this is complementary,
  stronger-than-nothing evidence for the parts a script can prove.
- Only CiviCRM 6.17.2 was exercised, matching the one version
  `connectors/civicrm.py`'s `RepairDeclaration` claims. A future CiviCRM
  release is unverified for repair until re-checked the same way.

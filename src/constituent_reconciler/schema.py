"""Declared schema and interface versions for the stability contract.

These are the surfaces the project commits to versioning explicitly: the recipe
TOML shape, the ``Connector`` protocol, and the JSON artifacts (the aggregate
summary and the provenance log entries). They are integers, bumped independently
of the package version.

The contract: once the project reaches 1.0, a breaking change to any of these
surfaces bumps the package MAJOR version and ships a migration note. Before 1.0,
a surface may change with a MINOR bump and a CHANGELOG entry. A consumer can read
these constants (or ``constituent-reconcile schema``) to check what it is integrating
against. The rationale is in docs/adr/0006-schema-stability.md.
"""

from __future__ import annotations

# The recipe TOML shape: sections, keys, and their meaning.
CONFIG_SCHEMA_VERSION = 1

# The Connector protocol: write_all signature, WriteResult shape, is_local.
CONNECTOR_INTERFACE_VERSION = 1

# The JSON artifacts: aggregate_summary.json, run_manifest.json, run_report.json,
# and the provenance log entry shape. Version 2 added the run manifest and the
# provenance "run-start" entry, whose consent field is null rather than a
# boolean; version 3 added field-level lineage (``field_sources``, member ids
# only) and the named survivorship ``fill_policy``; version 4 added the
# stage-cache policy and hit/miss counts to the run manifest and cache counts
# plus stage durations to run_summary.json, all content-free; version 5 added
# the "repair-apply" provenance entry (ADR 0012, ``connectors/repair.py``'s
# ``apply_repair``): the operation name and the distinct approver identities
# that gated it, alongside a receipt digest in the existing content_hash
# field. No prior key changed meaning. Version 6 added the "withdraw-plan"
# provenance entry (ADR 0012, ``repair.plan_withdraw``): the digest of a
# consent-withdrawal plan, with an empty record id because the plan concerns
# every written record under the manifest rather than one cluster. No prior key
# changed meaning. Version-1 logs still verify unchanged.
REPORT_SCHEMA_VERSION = 6

# The decisions.json shape: approved/rejected lists of [left, right] record-id
# pairs, written by the review session and consumed by ``constituent-reconcile apply``.
# Version 2 added the "audit" section (who decided each pair, and when) beside
# the version-1 lists, which are kept as-is so ``apply`` reads both versions.
DECISIONS_SCHEMA_VERSION = 2

# The count-only migration_summary.json written by ``constituent-reconcile compare``:
# matched, single-side, ambiguous, and conflicting identity counts, per-side
# ingest accounting in count form, and the thresholds used. Never a field
# value. Versioned on its own because the
# artifact is read outside the run pipeline's report family (a migration
# runbook or a funder memo), and its consumers should not have to track the
# run-report schema to parse it.
MIGRATION_SUMMARY_SCHEMA_VERSION = 1

# The reviewed correction file ``constituent-reconcile compare-apply`` writes for the
# target side (target_corrections.csv) and the ``export`` section it adds to
# compare_manifest.json. Version 1: one row per identity the target must add
# or correct, columns from the chosen import field map plus the external-id
# column, and a manifest section carrying digests and counts only. Version 2
# added the ``target_record_ids`` column (the ids the target export itself
# supplied, empty when it supplied none) and the manifest section's
# ``fill_policy`` key. Both are additive: a version-1 consumer reading by
# column name still finds every column it had. A future column or manifest-key
# change bumps this and ships a migration note.
CUTOVER_CORRECTIONS_SCHEMA_VERSION = 2
# The repair-plan artifact (repair_plan.json) that ``constituent-reconcile plan-split``
# writes: the old external id, the proposed split records, the fields needing
# restoration, and the operations the destination supports. A versioned
# surface from its first byte, per docs/adr/0012-connector-repair-capabilities.md.
# Version 2 adds a ``consent`` object to every ``split_records`` entry, and the
# manual instructions of a consent-requiring recipe now name the members a
# person must not create. Additive: every version 1 key keeps its meaning, and
# a reader that ignores the new object behaves as before, which is why this is
# a minor bump rather than a break. No release has been tagged, so no published
# artifact carries version 1.
#
# Version 3 adds a second artifact to this family and a key that tells the two
# apart. ``withdraw_plan.json`` (``constituent-reconcile plan-withdraw``) lists the
# written records whose consent has lapsed since the write, in the same
# repair-protocol shape; every plan in the family now carries ``plan_kind``,
# ``"split"`` or ``"withdraw"``, so a reader never has to infer which artifact it
# holds from the presence of a key. Additive for the split plan: every version 2
# key keeps its meaning and its value, and the only difference in a version 3
# split plan is the new ``plan_kind`` discriminator.
REPAIR_PLAN_SCHEMA_VERSION = 3

# The two artifacts in the repair-plan family, and the value of every plan's
# ``plan_kind``. ``apply-repair`` executes only ``PLAN_KIND_SPLIT``; a withdraw
# plan is refused there by name rather than by a downstream key lookup failing.
PLAN_KIND_SPLIT = "split"
PLAN_KIND_WITHDRAW = "withdraw"

# The count-only run_diff.json written by ``constituent-reconcile diff-runs``: how many
# clusters formed, dissolved or changed membership between two runs of one
# recipe, how many pairs entered or left review, how many reviewed decisions no
# longer apply, and the consent-withheld delta. Versioned on its own, like the
# migration summary and the repair plan, because it is read by a data manager
# defending one month's numbers against the last, not by the run report's
# consumers. Version 1 from its first byte; no release has been tagged, so no
# published artifact predates it.
RUN_DIFF_SCHEMA_VERSION = 1

# The calibration_report.json written by ``constituent-reconcile sweep-thresholds``: one row
# per (auto, review) setting with what an organization's own reviewer verdicts
# imply about it -- labeled auto-merges, false merges, missed matches, review
# load, Wilson intervals, and whether the row is eligible under the false-merge
# gate. Counts and rates only, never a pair id. Versioned on its own because it
# is read by someone deciding whether to change a threshold, which is a
# different audience and a different lifetime from the run report. Version 1
# from its first byte.
SWEEP_SCHEMA_VERSION = 1

# The auto_merges.json shape ``constituent-reconcile run`` writes: every pair the
# matcher merged without a human, with the probability and band that decided it
# and the thresholds in force. Versioned on its own, like the migration summary
# and the repair plan, because it is read by an auditor rather than by the run
# report's consumers, and because it is the counterpart to decisions.json --
# which records who decided the pairs a person saw. Version 1 from its first
# byte; no release has been tagged, so no published artifact predates it.
AUTO_MERGE_SCHEMA_VERSION = 1

# The connector repair-capability declaration shape (connectors/repair.py):
# destination, enumerated verified versions, operation vocabulary, and the
# vendor evidence fields.
REPAIR_CAPABILITY_SCHEMA_VERSION = 1

# The repair_approvals.json shape ``constituent-reconcile approve-repair`` writes: verdicts
# keyed by the exact repair-plan digest they approved, reviewer name and
# timestamp per verdict. Keying by digest, rather than overwriting a single
# current approval, means a replanned cluster's new digest starts with zero
# approvers automatically -- stale approval never carries forward -- while
# still keeping every past digest's history for the audit trail.
REPAIR_APPROVAL_SCHEMA_VERSION = 1

# The repair_receipts.json shape ``constituent-reconcile apply-repair`` writes when it
# executes (never on a dry run): one entry per operation attempted, with the
# before/after raw values a restoration or a split-create actually touched.
# A PII-bearing artifact for the same reason repair_plan.json is: provenance
# gets each entry's digest, never its content (docs/adr/0012).
REPAIR_RECEIPT_SCHEMA_VERSION = 1


def versions() -> dict[str, int]:
    """Return the declared schema versions as a mapping."""

    return {
        "config_schema": CONFIG_SCHEMA_VERSION,
        "connector_interface": CONNECTOR_INTERFACE_VERSION,
        "report_schema": REPORT_SCHEMA_VERSION,
        "decisions_schema": DECISIONS_SCHEMA_VERSION,
        "migration_summary": MIGRATION_SUMMARY_SCHEMA_VERSION,
        "cutover_corrections": CUTOVER_CORRECTIONS_SCHEMA_VERSION,
        "auto_merge": AUTO_MERGE_SCHEMA_VERSION,
        "repair_plan": REPAIR_PLAN_SCHEMA_VERSION,
        "repair_capability": REPAIR_CAPABILITY_SCHEMA_VERSION,
        "repair_approval": REPAIR_APPROVAL_SCHEMA_VERSION,
        "repair_receipt": REPAIR_RECEIPT_SCHEMA_VERSION,
    }

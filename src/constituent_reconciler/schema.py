"""Declared schema and interface versions for the stability contract.

These are the surfaces the project commits to versioning explicitly: the recipe
TOML shape, the ``Connector`` protocol, and the JSON artifacts (the aggregate
summary and the provenance log entries). They are integers, bumped independently
of the package version.

The contract: once the project reaches 1.0, a breaking change to any of these
surfaces bumps the package MAJOR version and ships a migration note. Before 1.0,
a surface may change with a MINOR bump and a CHANGELOG entry. A consumer can read
these constants (or ``reconcile schema``) to check what it is integrating
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
# only) and the named survivorship ``fill_policy``. Version-1 logs still verify
# unchanged.
REPORT_SCHEMA_VERSION = 3

# The decisions.json shape: approved/rejected lists of [left, right] record-id
# pairs, written by the review session and consumed by ``reconcile apply``.
# Version 2 added the "audit" section (who decided each pair, and when) beside
# the version-1 lists, which are kept as-is so ``apply`` reads both versions.
DECISIONS_SCHEMA_VERSION = 2

# The count-only migration_summary.json written by ``reconcile compare``:
# matched, single-side, ambiguous, and conflicting identity counts, per-side
# ingest accounting in count form, and the thresholds used. Never a field
# value. Versioned on its own because the
# artifact is read outside the run pipeline's report family (a migration
# runbook or a funder memo), and its consumers should not have to track the
# run-report schema to parse it.
MIGRATION_SUMMARY_SCHEMA_VERSION = 1


def versions() -> dict[str, int]:
    """Return the declared schema versions as a mapping."""

    return {
        "config_schema": CONFIG_SCHEMA_VERSION,
        "connector_interface": CONNECTOR_INTERFACE_VERSION,
        "report_schema": REPORT_SCHEMA_VERSION,
        "decisions_schema": DECISIONS_SCHEMA_VERSION,
        "migration_summary": MIGRATION_SUMMARY_SCHEMA_VERSION,
    }

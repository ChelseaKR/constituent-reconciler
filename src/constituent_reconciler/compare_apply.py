"""Reviewed correction-file export for a migration cutover (UC-02, PR 2).

``constituent-reconcile compare`` reports how two exports line up; this module carries the
human review of that comparison into the one artifact the cutover needs next:
a local, import-ready correction file for the target side. The organization
loads the file with the target CRM's own import tool. Nothing here mutates
either live system, and no code path can: the only writer this module
constructs is the local :class:`~constituent_reconciler.connectors.crm_csv.
CrmCsvConnector`, the connector registry is never consulted, and
``tests/test_compare_apply.py`` holds both facts as merge-blocking invariants.

The export is gated three ways, all fail-closed:

* **Review completeness.** Every review-band pair of the comparison must be
  approved or rejected in the decisions file the review session wrote. A
  missing decisions file, an undecided pair, or a pair still awaiting its
  second reviewer refuses the export. A comparison with zero review pairs may
  export without a review step, because there was nothing for a person to
  decide.
* **Manifest binding.** The export only runs against the comparison the
  operator reviewed: ``compare_manifest.json`` in the output directory must
  match the current recipes, inputs, and thresholds digest for digest. A
  missing or mismatched manifest refuses rather than exporting corrections
  derived from different data. After a successful export the manifest gains
  an ``export`` section binding the correction file and the decisions file by
  digest, never by content.
* **Consent.** When either side's recipe requires consent, an identity whose
  consent is absent, revoked, expired, future-dated, or out of scope is
  withheld from the correction file and counted, exactly as
  ``consent.partition_by_consent`` gates the write path.

The correction file, ``target_corrections.csv``, holds one row per identity
the target side needs to act on: identities present only in the legacy export,
and matched identities whose reviewed golden values differ from what the
target currently holds. Its columns follow the same import field maps the
run pipeline's ``salesforce_csv`` and ``civicrm_csv`` exports use, plus the
external-id column keyed on the identity id, so a CRM-side upsert on that
column stays idempotent across repeated imports of this file. The identity id
is minted by the reconciler, not by the target system: an upsert keyed on it
would add a record rather than update one the target already holds. So the
file carries a second key, ``target_record_ids``, holding the ids the target
export itself supplied for the records in that identity (its
``input.id_column`` values, pipe-separated when an identity covers more than
one target record). A matched row therefore names the record to update, and a
missing-from-target row leaves the column empty because the target has no
record yet. When the target export carried no id column there is nothing
honest to put there, and those rows need the import tool's own matching.
It is a PII artifact: local only, listed in the destruction inventory, covered
in docs/DATA-FLOW-AND-RETENTION.md.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from constituent_reconciler import compare, decisions
from constituent_reconciler.compare import (
    COMPARE_MANIFEST_FILENAME,
    LEFT_ONLY,
    MATCHED,
    RIGHT,
    CompareError,
    CompareResult,
    Identity,
    Side,
)
from constituent_reconciler.connectors.crm_csv import (
    CIVICRM_IMPORT_MAP,
    DEFAULT_TARGET_ID_COLUMN,
    SALESFORCE_IMPORT_MAP,
    CrmCsvConnector,
)
from constituent_reconciler.consent import Withheld, partition_by_consent
from constituent_reconciler.manifest import file_digest
from constituent_reconciler.models import Band, Cluster, GoldenRecord, Pair
from constituent_reconciler.pipeline import _apply_overrides
from constituent_reconciler.review.session import approved_without_second_approval
from constituent_reconciler.schema import CUTOVER_CORRECTIONS_SCHEMA_VERSION

CORRECTIONS_FILENAME = "target_corrections.csv"
CUTOVER_WITHHELD_FILENAME = "cutover_withheld.csv"
COMPARE_DECISIONS_FILENAME = "compare_decisions.json"

# The external-id column the correction file keys its rows on, matching the
# default the run pipeline's [output] section uses, so an operator who has
# already mapped one import file maps this one the same way.
EXTERNAL_ID_COLUMN = "external_identifier"

# The second key: the ids the target export supplied for the records behind an
# identity. The external id above is this tool's own, which the target has
# never seen; this column is what an operator points the import tool's
# matching at. Empty for an identity with no record on the target side.
TARGET_ID_COLUMN = DEFAULT_TARGET_ID_COLUMN

# Why a row is in the correction file. ``missing-from-target``: the identity
# exists only in the legacy export. ``field-correction``: the two sides
# matched, and at least one reviewed golden value is not what the target holds.
REASON_MISSING = "missing-from-target"
REASON_FIELD = "field-correction"

# Correction-file formats: the plain canonical CSV, and the two CRM import
# shapes the run pipeline already exports. Each value maps a canonical field
# name to the file's column header; the plain format is the identity mapping.
CORRECTION_FORMATS: dict[str, Mapping[str, str] | None] = {
    "csv": None,
    "salesforce_csv": SALESFORCE_IMPORT_MAP,
    "civicrm_csv": CIVICRM_IMPORT_MAP,
}


@dataclass(frozen=True)
class AppliedComparison:
    """The comparison after review decisions are applied. Nothing undecided.

    ``identities`` re-partitions every record under the reviewed pair bands;
    ``golden`` holds one merged record per identity that needs a correction
    row, ``reasons`` says why each is included, and ``target_ids`` carries the
    target export's own ids for it, all keyed by identity id.
    """

    result: CompareResult
    pairs: tuple[Pair, ...]
    identities: tuple[Identity, ...]
    golden: tuple[GoldenRecord, ...]
    reasons: dict[str, str]
    target_ids: dict[str, str]


@dataclass(frozen=True)
class CorrectionExport:
    """What the export step wrote, and what the consent gate withheld."""

    path: Path
    format: str
    written: tuple[GoldenRecord, ...]
    reasons: dict[str, str]
    withheld: tuple[Withheld, ...]
    withheld_path: Path | None
    manifest_path: Path


def _binding_facts(manifest: Mapping[str, object]) -> dict[str, object]:
    """The manifest keys that pin a comparison to its exact inputs.

    Timestamps and package versions are excluded on purpose: they change
    between the compare and the export without changing what was compared.
    The recipes, the input digests, the mappings, the compared fields, and the
    thresholds are what make two comparisons the same comparison.
    """

    return {key: manifest.get(key) for key in ("left", "right", "compared_fields", "thresholds")}


def verify_compare_manifest(
    out_dir: Path, left: Side, right: Side, result: CompareResult
) -> dict[str, object]:
    """Load and verify the comparison manifest, fail-closed on any drift.

    Returns the stored manifest so a successful export can extend it. A
    missing manifest means the operator has not run ``constituent-reconcile compare`` into
    this directory; a mismatched one means an input or recipe changed after
    the comparison was reviewed. Both refuse.
    """

    manifest_path = out_dir / COMPARE_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise CompareError(
            f"no comparison manifest at {manifest_path}; run constituent-reconcile compare "
            "into this output directory first so the export is bound to a "
            "recorded comparison"
        )
    try:
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompareError(
            f"cannot read the comparison manifest {manifest_path}: {error}"
        ) from error
    if not isinstance(stored, dict):
        raise CompareError(f"the comparison manifest {manifest_path} is not a JSON object")
    current = compare.build_compare_manifest(left, right, result)
    stored_facts = _binding_facts(stored)
    current_facts = _binding_facts(current)
    if stored_facts != current_facts:
        drifted = sorted(
            key for key in current_facts if stored_facts.get(key) != current_facts[key]
        )
        raise CompareError(
            f"the comparison manifest {manifest_path} does not match the current "
            f"inputs (differs on: {', '.join(drifted)}); an input or recipe "
            "changed after the comparison was made. Re-run constituent-reconcile compare and "
            "review again before exporting"
        )
    return {str(key): value for key, value in stored.items()}


def _decision_pairs(data: Mapping[str, object], key: str) -> list[frozenset[str]]:
    entries = data.get(key, [])
    if not isinstance(entries, list):
        raise CompareError(f"decisions {key} must be a list of [left, right] pairs")
    pairs: list[frozenset[str]] = []
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 2:
            raise CompareError(f"decisions {key} entries must be 2-element [left, right] lists")
        pairs.append(frozenset((str(entry[0]), str(entry[1]))))
    return pairs


def _awaiting_second(data: Mapping[str, object]) -> list[str]:
    """Audit-trail pairs neither approved nor rejected: held single approvals.

    Derived from the file's shape, so it only sees approvals a two-person
    review session chose to hold back. ``read_decisions`` pairs it with
    ``review.session.approved_without_second_approval``, which positively
    counts approvers on the pairs that did land in ``approved``.
    """

    audit = data.get("audit")
    if not isinstance(audit, dict):
        return []
    decided = set(_decision_pairs(data, "approved")) | set(_decision_pairs(data, "rejected"))
    return sorted(key for key in audit if frozenset(str(key).split("|")) not in decided)


def requires_second_reviewer(left: Side, right: Side) -> bool:
    """Two-person review gates the export when either side's recipe requires it.

    Public because the CLI passes the answer into :func:`read_decisions`,
    which needs the policy but never sees the recipes.
    """

    return left.recipe.require_second_reviewer or right.recipe.require_second_reviewer


def read_decisions(
    decisions_path: Path,
    result: CompareResult,
    *,
    require_second_reviewer: bool = False,
) -> tuple[frozenset[frozenset[str]], frozenset[frozenset[str]]]:
    """Read the review decisions for this comparison, fail-closed.

    Returns the approved and rejected pair keys. Refuses when the file is
    absent while review pairs exist, when a pair is still awaiting a second
    reviewer, or when a decision names a pair this comparison never scored,
    which means the file describes a different comparison.

    With ``require_second_reviewer`` (either side's pack, see
    ``requires_second_reviewer``) it also refuses any approved pair whose
    audit trail does not show two distinct approvers, including a file that
    records no attribution at all. The held-approval check alone cannot cover
    this: a file reviewed under a single-reviewer pack holds nothing back, so
    there is no held approval to notice.
    """

    if not decisions_path.is_file():
        if not result.review_pairs:
            return frozenset(), frozenset()
        raise CompareError(
            f"no decisions file at {decisions_path} while {len(result.review_pairs)} "
            "pair(s) need review; run constituent-reconcile compare-review first. The "
            "correction file is only exported after a person has decided every "
            "uncertain pair"
        )
    try:
        data = json.loads(decisions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompareError(f"cannot read the decisions file {decisions_path}: {error}") from error
    if not isinstance(data, dict):
        raise CompareError(f"the decisions file {decisions_path} is not a JSON object")
    awaiting = _awaiting_second(data)
    if awaiting:
        named = ", ".join(key.replace("|", " and ") for key in awaiting)
        raise CompareError(
            f"{decisions_path} holds {len(awaiting)} pair(s) still awaiting a "
            f"second reviewer ({named}); have a second reviewer finish "
            "(constituent-reconcile compare-review --reviewer <other-name>), or reject the pairs"
        )
    if require_second_reviewer:
        unconfirmed = approved_without_second_approval(data)
        if unconfirmed:
            named = ", ".join(key.replace("|", " and ") for key in unconfirmed)
            raise CompareError(
                f"{decisions_path} cannot show two distinct approvers for "
                f"{len(unconfirmed)} approved pair(s) ({named}), and a policy pack on "
                "one of these sides requires two-person review. Have a second "
                "reviewer review these pairs (constituent-reconcile compare-review --reviewer "
                "<other-name>); a decisions file that records no reviewer "
                "attribution cannot be applied under this pack at all"
            )
    approved = frozenset(_decision_pairs(data, "approved"))
    rejected = frozenset(_decision_pairs(data, "rejected"))
    scored = {pair.key() for pair in result.pairs}
    stray = next(
        (key for key in sorted(approved | rejected, key=sorted) if key not in scored), None
    )
    if stray is not None:
        raise CompareError(
            f"the decisions file {decisions_path} decides {sorted(stray)!r}, a pair "
            "this comparison never scored; it belongs to a different comparison. "
            "Re-run constituent-reconcile compare-review against these inputs"
        )
    return approved, rejected


def apply_review(
    result: CompareResult,
    approved: frozenset[frozenset[str]],
    rejected: frozenset[frozenset[str]],
) -> AppliedComparison:
    """Apply reviewed verdicts and re-partition, refusing anything undecided.

    An approved pair becomes a confident merge and a rejected one a durable
    cannot-link, the same overrides ``constituent-reconcile apply`` feeds back into a run.
    Cannot-link enforcement may return automatic merges to review; any pair
    still in the review band after that stops the export, fail-closed.
    """

    adjusted = _apply_overrides(list(result.pairs), approved, rejected)
    adjusted = decisions.enforce_cannot_links(result.records.keys(), adjusted, rejected)
    unresolved = [pair for pair in adjusted if pair.band is Band.REVIEW]
    if unresolved:
        first = unresolved[0]
        detail = f" ({first.note})" if first.note else ""
        raise CompareError(
            f"{len(unresolved)} pair(s) are still unresolved, starting with "
            f"{first.left!r} and {first.right!r}{detail}; every uncertain pair "
            "must be approved or rejected before the correction file is exported"
        )
    clusters = tuple(decisions.build_clusters(result.records.keys(), adjusted))
    identities = tuple(
        compare._build_identity(cluster, result.records, result.fields, ambiguous=False)
        for cluster in clusters
    )
    compare._check_accounting(result.records, identities)
    golden, reasons = _correction_records(result, clusters, identities)
    included = {identity.identity_id for identity in identities} & set(reasons)
    target_ids = {
        identity.identity_id: _target_ids(identity)
        for identity in identities
        if identity.identity_id in included
    }
    return AppliedComparison(
        result=result,
        pairs=tuple(adjusted),
        identities=identities,
        golden=golden,
        reasons=reasons,
        target_ids=target_ids,
    )


def _needs_field_correction(
    result: CompareResult, identity: Identity, golden: GoldenRecord
) -> bool:
    """Whether the target side's current values differ from the golden record.

    Compares normalized values so a formatting difference the two sides agree
    on does not manufacture a correction. A golden value the target already
    holds needs no row, even when the comparison flagged a conflict, because
    survivorship resolved it in the target's favor.
    """

    for name in result.fields:
        value = golden.fields.get(name, "")
        if not value:
            continue
        right_values = {
            result.records[member].normalized.get(name, "") for member in identity.right_members
        }
        if value not in right_values:
            return True
    return False


_TARGET_ID_PREFIX = f"{RIGHT}:"


def _target_ids(identity: Identity) -> str:
    """The target export's own ids for this identity, pipe-separated.

    A record read from a side whose recipe maps ``input.id_column`` keeps that
    id, namespaced by the side label (``right:41827``); a side without one gets
    a content-derived id the target system has never seen. Only the first kind
    is written here, stripped of the namespace, so the column holds destination
    ids or nothing at all. Empty for a left-only identity, which by definition
    has no record on the target side yet.
    """

    return "|".join(
        member.removeprefix(_TARGET_ID_PREFIX)
        for member in identity.right_members
        if member.startswith(_TARGET_ID_PREFIX)
    )


def _correction_records(
    result: CompareResult,
    clusters: Sequence[Cluster],
    identities: Sequence[Identity],
) -> tuple[tuple[GoldenRecord, ...], dict[str, str]]:
    """One golden record per identity the target side must add or correct.

    Left-only identities are people the target export is missing entirely.
    Matched identities contribute a row only when a reviewed golden value
    differs from what the target holds. Right-only identities and matched
    identities the target already agrees with produce nothing.

    Merging runs under the comparison's governing survivorship fill policy
    (``compare._resolve_fill_policy``), the same setting ``pipeline.run``
    threads from the recipe, so the golden values this export writes are the
    values that recipe asks for rather than the package default.
    """

    by_id = {cluster.cluster_id: cluster for cluster in clusters}
    reasons: dict[str, str] = {}
    included: list[Cluster] = []
    for identity in sorted(identities, key=lambda i: i.identity_id):
        if identity.status == LEFT_ONLY:
            reasons[identity.identity_id] = REASON_MISSING
            included.append(by_id[identity.identity_id])
        elif identity.status == MATCHED:
            [candidate] = decisions.golden_records(
                [by_id[identity.identity_id]],
                result.records,
                result.fields,
                fill_policy=result.fill_policy,
            )
            if _needs_field_correction(result, identity, candidate):
                reasons[identity.identity_id] = REASON_FIELD
                included.append(by_id[identity.identity_id])
    golden = tuple(
        decisions.golden_records(
            included, result.records, result.fields, fill_policy=result.fill_policy
        )
    )
    return golden, reasons


def _write_cutover_withheld(withheld: Sequence[Withheld], out_dir: Path) -> Path:
    """List withheld identities by id and reason, the write path's shape.

    Ids and the withhold reason only, no field values: the same minimization
    ``pipeline._write_withheld`` applies, so a caseworker can follow up on a
    revoked consent without this artifact repeating the person's data.
    """

    path = out_dir / CUTOVER_WITHHELD_FILENAME
    out_dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["identity", "members", "reason"])
        for item in sorted(withheld, key=lambda w: w.cluster_id):
            writer.writerow([item.cluster_id, "|".join(item.members), item.reason])
    return path


def _require_consent(left: Side, right: Side) -> bool:
    """Consent gates the export when either side's recipe requires it."""

    return left.recipe.require_consent or right.recipe.require_consent


def export_corrections(
    left: Side,
    right: Side,
    applied: AppliedComparison,
    out_dir: Path,
    *,
    fmt: str,
    stored_manifest: dict[str, object],
    decisions_path: Path,
    corrections_path: Path | None,
) -> CorrectionExport:
    """Write the correction file and bind it into the comparison manifest.

    The writer is the same local import-file connector the run pipeline uses
    for ``salesforce_csv`` and ``civicrm_csv``; the plain ``csv`` format keeps
    the canonical field names. Consent is applied before the writer sees a
    record. The manifest's new ``export`` section carries digests and counts
    only, never a field value.
    """

    if fmt not in CORRECTION_FORMATS:
        known = ", ".join(sorted(CORRECTION_FORMATS))
        raise CompareError(f"unknown correction-file format {fmt!r} (known formats: {known})")
    field_map = CORRECTION_FORMATS[fmt]
    if field_map is None:
        field_map = {name: name for name in applied.result.fields}

    exportable, withheld = partition_by_consent(
        applied.golden,
        require_consent=_require_consent(left, right),
        destination=fmt,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / CORRECTIONS_FILENAME
    connector = CrmCsvConnector(
        "cutover-corrections", path, field_map, external_id_column=EXTERNAL_ID_COLUMN
    )
    # The target's own ids for the rows it already holds, so a matched
    # correction updates that record instead of adding a second one.
    connector.set_target_id_column(applied.target_ids, column=TARGET_ID_COLUMN)
    connector.write_all(exportable, applied.result.fields, dry_run=False)
    withheld_path = _write_cutover_withheld(withheld, out_dir) if withheld else None

    reason_counts: dict[str, int] = {}
    for record in exportable:
        reason = applied.reasons[record.cluster_id]
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    withheld_counts: dict[str, int] = {}
    for item in withheld:
        withheld_counts[item.reason] = withheld_counts.get(item.reason, 0) + 1

    stored_manifest["export"] = {
        "created_at": datetime.now(UTC).isoformat(),
        "schema_version": CUTOVER_CORRECTIONS_SCHEMA_VERSION,
        "format": fmt,
        # Run metadata, not data: how the merged values in this file were
        # chosen, recorded the way the run pipeline records it for a write.
        "fill_policy": applied.result.fill_policy,
        "correction_file": CORRECTIONS_FILENAME,
        "correction_file_digest": file_digest(path),
        "decisions_digest": (file_digest(decisions_path) if decisions_path.is_file() else None),
        "corrections_digest": (
            file_digest(corrections_path)
            if corrections_path is not None and corrections_path.is_file()
            else None
        ),
        "rows": len(exportable),
        "row_reasons": reason_counts,
        "withheld": withheld_counts,
    }
    manifest_path = compare.write_compare_manifest(stored_manifest, out_dir)

    return CorrectionExport(
        path=path,
        format=fmt,
        written=tuple(exportable),
        reasons=dict(applied.reasons),
        withheld=tuple(withheld),
        withheld_path=withheld_path,
        manifest_path=manifest_path,
    )


def render_export_summary(export: CorrectionExport) -> str:
    """Count-only terminal summary of what was exported and withheld."""

    lines = [
        f"correction rows:      {len(export.written)}",
        f"  missing from target: "
        f"{sum(1 for r in export.written if export.reasons[r.cluster_id] == REASON_MISSING)}",
        f"  field corrections:   "
        f"{sum(1 for r in export.written if export.reasons[r.cluster_id] == REASON_FIELD)}",
        f"withheld (consent):   {len(export.withheld)}",
    ]
    if export.withheld:
        by_reason: dict[str, int] = {}
        for item in export.withheld:
            by_reason[item.reason] = by_reason.get(item.reason, 0) + 1
        for reason in sorted(by_reason):
            lines.append(f"    {reason}: {by_reason[reason]}")
    return "\n".join(lines)

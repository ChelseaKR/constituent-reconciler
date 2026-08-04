"""Read-only split repair planning (UC-03, ADR 0012).

An administrator who discovers a bad merge after a batch was written needs to
understand the repair before anyone touches the destination. ``reconcile
plan-split`` turns one written cluster into a local repair plan: the old
external id, one proposed split record per member, the fields whose written
value came from a member being split away, and the operations the destination
supports. The plan holds raw field values, so it lives only on the operator's
machine, is listed in ``destruction.PII_ARTIFACTS``, and enters the provenance
log as a digest, never as content.

Reconstruction is grounded, not guessed, and fully offline. The run manifest
proves the current recipe and source files are byte-identical to the ones the
written run read; the provenance chain supplies the written cluster's members,
external id, fill policy, and field lineage; and the golden record is
recomputed over exactly that member set from the reconstructed records. When
any of those checks fails (a drifted input, a member the batch does not
reconstruct, lineage that no longer matches the write entry) planning refuses
rather than emitting a plan it cannot stand behind. Nothing here constructs a
connector or opens a network connection: the ``inspect_repair`` remote read
the ADR permits is not implemented yet and arrives with the destination pilot.

Two side effects are local and deliberate. The plan's digest is appended to
the provenance log, so a later apply can bind approvals to the exact plan
bytes. And every pair of split members is recorded as rejected in the
decisions file, a binding cannot-link, so the next run routes the group back
through review instead of silently recreating the bad cluster. Planning is
repeatable: the plan payload carries no timestamp, so replanning the same
cluster against the same manifest produces byte-identical plan files, and the
cannot-link binding adds nothing the file already holds.

One honest limit: a correction that changes a field's value without changing
which member supplied it is invisible to the lineage check, so operators pass
the corrections file the written run applied (the CLI refuses an explicitly
passed corrections path that does not exist, and otherwise loads the default
location ``reconcile apply`` uses). Both reviewers regenerating and comparing
plans, and the apply path's digest binding, are the controls the ADR places
around that gap.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

from constituent_reconciler import decisions, pipeline
from constituent_reconciler.config import Recipe
from constituent_reconciler.connectors.base import WRITE_ACTIONS
from constituent_reconciler.connectors.repair import supported_operations
from constituent_reconciler.destruction import PROVENANCE_FILENAME
from constituent_reconciler.manifest import file_digest, input_digests, manifest_hash
from constituent_reconciler.models import Cluster, Correction, GoldenRecord, Record
from constituent_reconciler.provenance import (
    RUN_START_ACTION,
    ProvenanceLog,
    verify_log,
)
from constituent_reconciler.schema import DECISIONS_SCHEMA_VERSION, REPAIR_PLAN_SCHEMA_VERSION

REPAIR_PLAN_FILENAME = "repair_plan.json"

REJECTED_VERDICT = "rejected"


class RepairPlanError(ValueError):
    """Planning refused, fail-closed.

    Every raise happens before any plan bytes exist: no plan file is written,
    no provenance entry is appended, and no decision is bound on this path.
    """


@dataclass(frozen=True)
class PlannedSplit:
    """What one successful planning pass produced, without repeating PII.

    ``plan_path`` is the local plan file (the PII-bearing artifact);
    ``digest`` is the BLAKE2b-256 over its exact bytes, the value recorded in
    the provenance log and later bound to apply-time approvals.
    ``displaced_cluster`` names the different cluster whose plan file this
    planning pass replaced, or ``None`` when nothing was displaced.
    Everything else here is ids and counts, safe to print.
    """

    plan_path: Path
    digest: str
    cluster_id: str
    members: tuple[str, ...]
    external_id: str
    destination: str
    supported_operations: tuple[str, ...]
    mode: str
    cannot_links: tuple[tuple[str, str], ...]
    decisions_path: Path
    displaced_cluster: str | None


def _require(value: str, what: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise RepairPlanError(f"plan-split requires {what}; it may not be blank")
    return cleaned


def _load_manifest(manifest_path: Path) -> dict[str, object]:
    if not manifest_path.is_file():
        raise RepairPlanError(f"run manifest not found: {manifest_path}")
    try:
        data: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RepairPlanError(f"run manifest is not valid JSON: {manifest_path}") from error
    if not isinstance(data, dict):
        raise RepairPlanError(f"run manifest must be a JSON object: {manifest_path}")
    return {str(key): value for key, value in data.items()}


def _drifted_inputs(current: dict[str, str], recorded: object) -> list[str]:
    recorded_map = (
        {str(key): str(value) for key, value in recorded.items()}
        if isinstance(recorded, dict)
        else {}
    )
    symmetric = set(current) ^ set(recorded_map)
    changed = {
        name for name in set(current) & set(recorded_map) if current[name] != recorded_map[name]
    }
    return sorted(symmetric | changed)


def _verify_manifest(recipe: Recipe, manifest_path: Path) -> tuple[dict[str, object], str]:
    """Prove the loaded recipe and current sources are what the manifest hashed.

    Returns the manifest data and its hash (the value the provenance log's
    ``run-start`` entry carries). Any drift refuses: a plan grounded in a
    different batch than the one that was written would describe the wrong
    people.
    """

    data = _load_manifest(manifest_path)
    recorded_pack = str(data.get("policy_pack", ""))
    if recorded_pack != recipe.policy_pack:
        raise RepairPlanError(
            f"the manifest records policy pack {recorded_pack!r} but the recipe loaded "
            f"pack {recipe.policy_pack!r}; plan with the pack the written run used"
        )
    recipe_hash = file_digest(recipe.recipe_path) if recipe.recipe_path is not None else None
    if data.get("recipe_hash") != recipe_hash:
        raise RepairPlanError(
            "the recipe file does not match the manifest's recipe hash; plan against "
            "the exact recipe the written run used"
        )
    input_paths = [path for path in (recipe.existing, recipe.incoming) if path is not None]
    current = input_digests(input_paths)
    if current != data.get("input_hashes"):
        drifted = _drifted_inputs(current, data.get("input_hashes"))
        named = ", ".join(drifted) if drifted else "the input set"
        raise RepairPlanError(
            f"the source batch does not match the manifest's input hashes ({named} "
            "drifted); restore the exact inputs the written run read"
        )
    return data, manifest_hash(data)


def _written_entry(
    provenance_path: Path, manifest_digest: str, cluster_id: str
) -> dict[str, object]:
    """The last write entry for the cluster in the segment under this manifest.

    The log must verify intact, and the manifest must appear as a
    ``run-start`` entry, before any write entry is trusted. A cluster with no
    write entry under the manifest is refused: it may be an unknown id, or a
    cluster that was withheld or dry-run and never written, and neither case
    supports a repair plan.
    """

    ok, message = verify_log(provenance_path)
    if not ok:
        raise RepairPlanError(
            f"the provenance log cannot anchor a repair plan ({provenance_path}): {message}"
        )
    in_segment = False
    saw_manifest = False
    found: dict[str, object] | None = None
    with provenance_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("action") == RUN_START_ACTION:
                in_segment = entry.get("content_hash") == manifest_digest
                saw_manifest = saw_manifest or in_segment
            elif (
                in_segment
                and entry.get("action") in WRITE_ACTIONS
                and entry.get("record_id") == cluster_id
            ):
                found = entry
    if not saw_manifest:
        raise RepairPlanError(
            "the provenance log records no run under this manifest; the manifest and "
            "the log must come from the same run's output directory"
        )
    if found is None:
        raise RepairPlanError(
            f"no write is recorded for cluster {cluster_id!r} under this manifest. The "
            "id may be wrong, or the cluster was withheld or dry-run and never "
            "written; there is nothing in the destination to repair."
        )
    return {str(key): value for key, value in found.items()}


def _entry_members(entry: dict[str, object], cluster_id: str) -> tuple[str, ...]:
    raw = entry.get("members")
    if not isinstance(raw, list) or not all(isinstance(member, str) for member in raw):
        raise RepairPlanError(
            f"the write entry for cluster {cluster_id!r} carries a malformed member list"
        )
    members = tuple(sorted(str(member) for member in raw))
    if len(members) < 2:
        raise RepairPlanError(
            f"cluster {cluster_id!r} was written from a single record; there is no merge to split"
        )
    return members


def _entry_external_id(entry: dict[str, object], cluster_id: str) -> str:
    external_id = entry.get("external_id")
    if not isinstance(external_id, str) or not external_id:
        raise RepairPlanError(
            f"the write entry for cluster {cluster_id!r} records no external id, so "
            "the destination record cannot be named; this repair needs manual "
            "investigation, not a generated plan"
        )
    return external_id


def _reconstructed_golden(
    recipe: Recipe,
    records: dict[str, Record],
    entry: dict[str, object],
    cluster_id: str,
    members: tuple[str, ...],
) -> GoldenRecord:
    """Recompute the written golden record and prove its lineage still holds."""

    missing = [member for member in members if member not in records]
    if missing:
        raise RepairPlanError(
            f"the source batch does not reconstruct member(s) {', '.join(missing)} of "
            f"cluster {cluster_id!r}; refusing to guess a plan for records the batch "
            "cannot account for"
        )
    fill_policy = str(entry.get("fill_policy") or recipe.fill_policy)
    try:
        [golden] = decisions.golden_records(
            [Cluster(cluster_id=cluster_id, members=members)],
            records,
            recipe.fields,
            fill_policy=fill_policy,
        )
    except ValueError as error:
        raise RepairPlanError(f"could not rebuild the written golden record: {error}") from error
    recorded_sources = entry.get("field_sources")
    recorded = (
        {str(key): str(value) for key, value in recorded_sources.items()}
        if isinstance(recorded_sources, dict)
        else {}
    )
    if dict(golden.field_sources) != recorded:
        raise RepairPlanError(
            "the reconstructed field lineage does not match what the provenance log "
            "recorded for this write; pass the corrections file the written run "
            "applied (--corrections), or regenerate the run before planning"
        )
    return golden


def _manual_instructions(destination: str, external_id: str, survivor: str) -> tuple[str, ...]:
    return (
        f"No verified repair operations exist for destination {destination!r}, so this "
        "plan is manual: a person applies it in the destination itself, and this tool "
        "will not execute any of it.",
        f"In the destination, find the record whose external id is {external_id!r}. It "
        "currently holds the merged cluster this plan splits.",
        "Restore each field listed under restore_fields to its restore_to value; the "
        "written value came from a record this plan separates out.",
        f"Create one new record for each split_records entry other than the survivor "
        f"{survivor!r}, using the field values listed there.",
        "Delete or merge nothing this plan does not name. If the destination does not "
        "look the way the plan describes, stop and regenerate the plan before "
        "continuing.",
    )


def _plan_payload(
    recipe: Recipe,
    *,
    manifest_digest: str,
    cluster_id: str,
    members: tuple[str, ...],
    external_id: str,
    golden: GoldenRecord,
    records: dict[str, Record],
    reason: str,
    reviewer: str,
) -> dict[str, object]:
    """Assemble the deterministic plan payload. Raw values live only here."""

    survivor = golden.primary
    split_records = [
        {
            "record_id": member,
            "source": records[member].source,
            "fields": {name: records[member].normalized.get(name, "") for name in recipe.fields},
        }
        for member in members
    ]
    restore_fields = [
        {
            "field": name,
            "written_value": golden.fields.get(name, ""),
            "supplied_by": supplied,
            "restore_to": records[survivor].normalized.get(name, ""),
        }
        for name in recipe.fields
        if (supplied := golden.field_sources.get(name, "")) and supplied != survivor
    ]
    destination = recipe.output.connector
    # Planning is offline: no destination version was read, and the blank
    # version never matches a declaration's enumerated list, so operations
    # stay empty and the plan is manual until a verified pilot exists.
    operations = supported_operations(destination, "")
    return {
        "repair_plan_schema": REPAIR_PLAN_SCHEMA_VERSION,
        "manifest_hash": manifest_digest,
        "policy_pack": recipe.policy_pack,
        "cluster_id": cluster_id,
        "old_external_id": external_id,
        "survivor": survivor,
        "reason": reason,
        "reviewer": reviewer,
        "destination": destination,
        "destination_version": "",
        "mode": "verified" if operations else "manual",
        "supported_operations": [
            {"name": operation.name, "destructive": operation.destructive}
            for operation in operations
        ],
        "split_records": split_records,
        "restore_fields": restore_fields,
        "cannot_links": [list(pair) for pair in combinations(members, 2)],
        "manual_instructions": (
            [] if operations else list(_manual_instructions(destination, external_id, survivor))
        ),
    }


def _decision_lists(data: dict[str, object]) -> tuple[list[list[str]], list[list[str]]]:
    def pairs_of(key: str) -> list[list[str]]:
        raw = data.get(key, [])
        if not isinstance(raw, list):
            return []
        return [
            [str(item) for item in entry]
            for entry in raw
            if isinstance(entry, list) and len(entry) == 2
        ]

    return pairs_of("approved"), pairs_of("rejected")


def _load_decisions(path: Path) -> dict[str, object]:
    """Load and validate the decisions file before any plan bytes are written.

    A file that is not valid JSON, or is valid JSON but not an object, refuses
    here, while nothing is on disk yet, so the refusal leaves no plan file and
    no provenance entry behind. A missing file is an empty record; binding
    creates it.
    """

    if not path.exists():
        return {}
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RepairPlanError(f"decisions file is not valid JSON: {path}") from error
    if not isinstance(loaded, dict):
        raise RepairPlanError(f"decisions file must be a JSON object: {path}")
    return {str(key): value for key, value in loaded.items()}


def _displaced_cluster(plan_path: Path, cluster_id: str) -> str | None:
    """Name the different cluster whose existing plan the next write replaces.

    The plan filename is fixed because ``destruction.PII_ARTIFACTS`` lists
    artifacts by exact name, so planning a second cluster in one out directory
    replaces the first cluster's plan file. Plans are regenerable by design,
    but the replacement must not be silent; the CLI turns this value into a
    warning. Replanning the same cluster, and an existing file that does not
    parse as a plan, name nothing.
    """

    if not plan_path.is_file():
        return None
    try:
        existing: object = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(existing, dict):
        return None
    previous = existing.get("cluster_id")
    if isinstance(previous, str) and previous and previous != cluster_id:
        return previous
    return None


def _bind_cannot_links(
    path: Path, data: dict[str, object], links: Sequence[tuple[str, str]], reviewer: str
) -> None:
    """Record every split pair as rejected in the decisions file.

    ``data`` is the file's content, loaded and validated by
    ``_load_decisions`` before any plan bytes were written. The rejected list
    is what ``reconcile apply`` loads as ``force_drop``, and
    ``decisions.enforce_cannot_links`` treats those pairs as binding: the next
    run cannot recreate the split cluster, and any surviving automatic edge in
    the group returns to review. A split pair found in ``approved`` is
    removed, because a surviving approval would force the merge right back.
    The binding is idempotent, so replanning changes nothing the file already
    records, and every added rejection is attributed in the audit section.
    """

    approved, rejected = _decision_lists(data)
    audit_raw = data.get("audit")
    audit: dict[str, list[dict[str, str]]] = (
        {str(key): list(value) for key, value in audit_raw.items() if isinstance(value, list)}
        if isinstance(audit_raw, dict)
        else {}
    )
    link_keys = {frozenset(pair) for pair in links}
    approved = [pair for pair in approved if frozenset(pair) not in link_keys]
    rejected_keys = {frozenset(pair) for pair in rejected}
    decided_at = datetime.now(UTC).isoformat(timespec="seconds")
    for left, right in links:
        if frozenset((left, right)) not in rejected_keys:
            rejected.append([left, right])
            rejected_keys.add(frozenset((left, right)))
        audit_key = "|".join(sorted((left, right)))
        entries = audit.setdefault(audit_key, [])
        already = any(
            isinstance(entry, dict)
            and entry.get("reviewer") == reviewer
            and entry.get("verdict") == REJECTED_VERDICT
            for entry in entries
        )
        if not already:
            entries.append(
                {"reviewer": reviewer, "verdict": REJECTED_VERDICT, "decided_at": decided_at}
            )
    data["approved"] = approved
    data["rejected"] = rejected
    data["audit"] = audit
    data.setdefault("decisions_schema", DECISIONS_SCHEMA_VERSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def plan_split(
    recipe: Recipe,
    *,
    manifest_path: Path,
    cluster_id: str,
    reason: str,
    reviewer: str,
    corrections: Iterable[Correction] = (),
    decisions_path: Path | None = None,
) -> PlannedSplit:
    """Plan the split of one written cluster. Read-only toward the destination.

    Refuses, fail-closed, on a blank reason or reviewer, a manifest that does
    not match the loaded recipe and current sources, a provenance log that
    does not verify or does not record the manifest's run, an unknown or
    never-written cluster id, a single-record cluster, a decisions file that
    is not valid JSON or not a JSON object, a member the batch cannot
    reconstruct, or lineage that no longer matches the write entry. Every
    refusal happens before any plan bytes exist, so a refused planning pass
    leaves no plan file, no provenance entry, and no bound decision.
    On success the plan file is written beside the manifest, its digest is
    appended to the provenance log, and the split pairs become binding
    cannot-links in the decisions file; when the plan file replaced a
    different cluster's plan, ``displaced_cluster`` names it.
    """

    reason_text = _require(reason, "a reason")
    reviewer_name = _require(reviewer, "a reviewer identity")
    cluster_key = _require(cluster_id, "a cluster id")
    out_dir = manifest_path.parent
    _, manifest_digest = _verify_manifest(recipe, manifest_path)
    provenance_path = out_dir / PROVENANCE_FILENAME
    entry = _written_entry(provenance_path, manifest_digest, cluster_key)
    members = _entry_members(entry, cluster_key)
    external_id = _entry_external_id(entry, cluster_key)
    resolved_decisions = (
        decisions_path if decisions_path is not None else out_dir / "decisions.json"
    )
    decisions_data = _load_decisions(resolved_decisions)
    try:
        records = pipeline.ingest_normalized_records(recipe, corrections=corrections)
    except ValueError as error:
        raise RepairPlanError(f"could not reconstruct the source batch: {error}") from error
    golden = _reconstructed_golden(recipe, records, entry, cluster_key, members)
    payload = _plan_payload(
        recipe,
        manifest_digest=manifest_digest,
        cluster_id=cluster_key,
        members=members,
        external_id=external_id,
        golden=golden,
        records=records,
        reason=reason_text,
        reviewer=reviewer_name,
    )
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    digest = hashlib.blake2b(encoded.encode("utf-8"), digest_size=32).hexdigest()
    plan_path = out_dir / REPAIR_PLAN_FILENAME
    displaced = _displaced_cluster(plan_path, cluster_key)
    plan_path.write_text(encoded, encoding="utf-8")
    log = ProvenanceLog(provenance_path)
    log.append_repair_plan(
        cluster_id=cluster_key,
        members=members,
        plan_digest=digest,
        external_id=external_id,
    )
    links = tuple((left, right) for left, right in combinations(members, 2))
    _bind_cannot_links(resolved_decisions, decisions_data, links, reviewer_name)
    operations = supported_operations(recipe.output.connector, "")
    return PlannedSplit(
        plan_path=plan_path,
        digest=digest,
        cluster_id=cluster_key,
        members=members,
        external_id=external_id,
        destination=recipe.output.connector,
        supported_operations=tuple(operation.name for operation in operations),
        mode="verified" if operations else "manual",
        cannot_links=links,
        decisions_path=resolved_decisions,
        displaced_cluster=displaced,
    )

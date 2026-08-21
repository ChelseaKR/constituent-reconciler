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
from datetime import UTC, date, datetime
from itertools import combinations
from pathlib import Path

from constituent_reconciler import decisions, pipeline
from constituent_reconciler.config import Recipe
from constituent_reconciler.connectors.base import WRITE_ACTIONS, Connector
from constituent_reconciler.connectors.repair import (
    RepairOperationResult,
    repair_declaration,
    supported_operations,
)
from constituent_reconciler.destruction import PROVENANCE_FILENAME
from constituent_reconciler.manifest import file_digest, input_digests, manifest_hash
from constituent_reconciler.models import Cluster, Correction, GoldenRecord, Record
from constituent_reconciler.provenance import (
    REPAIR_PLAN_ACTION,
    RUN_START_ACTION,
    ProvenanceLog,
    content_hash,
    verify_log,
)
from constituent_reconciler.schema import (
    DECISIONS_SCHEMA_VERSION,
    REPAIR_APPROVAL_SCHEMA_VERSION,
    REPAIR_PLAN_SCHEMA_VERSION,
    REPAIR_RECEIPT_SCHEMA_VERSION,
)

REPAIR_PLAN_FILENAME = "repair_plan.json"
REPAIR_APPROVALS_FILENAME = "repair_approvals.json"
REPAIR_RECEIPTS_FILENAME = "repair_receipts.json"

REJECTED_VERDICT = "rejected"

APPROVED_VERDICT = "approved"
REJECTED_APPROVAL_VERDICT = "rejected"

# A remote destructive apply is refused below this many distinct approvers,
# reusing the review session's two-person rule (ADR 0012). Unconditional: the
# pilot's operations (field-restore, split-create) are both declared
# destructive, so this applies to every execute call, never only some.
MINIMUM_APPLY_APPROVERS = 2


class RepairPlanError(ValueError):
    """Planning refused, fail-closed.

    Every raise happens before any plan bytes exist: no plan file is written,
    no provenance entry is appended, and no decision is bound on this path.
    """


class RepairApplyError(ValueError):
    """Applying a repair plan refused, fail-closed.

    Every raise happens before ``Connector.apply_repair`` is called: with
    fewer than two distinct recorded approvals the gate raises before a
    connector is even constructed, so no credential is read and no network
    call is possible on this path (``tests/test_repair_apply.py`` proves it
    by spying on connector construction and transport calls).
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


# -- apply_repair: the reviewed, gated execution path (UC-03 PR 3, ADR 0012) -


def _plan_digest(plan_path: Path) -> tuple[str, str]:
    """The plan file's raw text and its BLAKE2b-256 digest.

    Hashing the bytes exactly as ``plan_split`` wrote them (not a
    re-serialization) is what lets a hand-edited plan be caught: any change
    to the file, even one that still parses as valid JSON, changes this
    digest and therefore the approvals looked up under it in
    :func:`record_repair_approval` and the binding checked in
    :func:`apply_repair_plan`.
    """

    if not plan_path.is_file():
        raise RepairApplyError(f"repair plan not found: {plan_path}")
    raw = plan_path.read_text(encoding="utf-8")
    digest = hashlib.blake2b(raw.encode("utf-8"), digest_size=32).hexdigest()
    return raw, digest


def _reviewer_identity_key(name: str) -> str:
    """A comparison key for "is this the same reviewer", never for display.

    Mirrors ``review/session.py``'s ``_reviewer_identity_key`` exactly: case
    and internal whitespace do not make one person into two. Duplicated
    rather than imported because it is a one-line pure function and the two
    modules gate different artifacts (match decisions vs. repair approvals);
    importing a private helper across that boundary would say the two gates
    are one mechanism, and they are deliberately not.
    """

    return " ".join(name.casefold().split())


def _distinct_approvers(entries: object) -> dict[str, str]:
    """Distinct reviewer identities with an ``approved`` verdict, from one list.

    Returns identity key -> the first display name recorded for it, so a
    case or whitespace variant of one name is one entry (counted once) but
    shown in its own original spelling, never the folded key. Anything that
    is not a list of objects yields no approvers rather than raising, so an
    unreadable or missing entry list fails the count-below-two gate on its
    own, the same fail-closed shape
    ``review.session.approved_without_second_approval`` uses.
    """

    if not isinstance(entries, list):
        return {}
    identities: dict[str, str] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("reviewer", "")).strip()
        if name and str(raw.get("verdict", "")) == APPROVED_VERDICT:
            identities.setdefault(_reviewer_identity_key(name), name)
    return identities


def _load_approvals(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RepairApplyError(f"repair approvals file is not valid JSON: {path}") from error
    if not isinstance(loaded, dict):
        raise RepairApplyError(f"repair approvals file must be a JSON object: {path}")
    return {str(key): value for key, value in loaded.items()}


def _approvers_for_digest(approvals_path: Path, digest: str) -> dict[str, str]:
    data = _load_approvals(approvals_path)
    approvals = data.get("approvals")
    entries = approvals.get(digest) if isinstance(approvals, dict) else None
    return _distinct_approvers(entries)


def record_repair_approval(
    plan_path: Path,
    approvals_path: Path,
    *,
    reviewer: str,
    verdict: str = APPROVED_VERDICT,
) -> tuple[str, tuple[str, ...]]:
    """Record one reviewer's verdict on the exact bytes of the current plan.

    Verdicts are keyed by the plan's digest, not overwritten in place: a
    replanned cluster gets a new digest and therefore starts at zero
    approvers, so a stale approval of a plan that no longer exists can never
    count toward a different plan's gate, while the old digest's history
    stays in the file for the audit trail. Approving the same digest twice
    under one reviewer identity (case/whitespace folded, matching
    ``review/session.py``'s rule) is harmless: the entry is appended, but
    distinctness is judged by identity, so it never becomes a second
    reviewer.

    Returns the plan digest and the distinct approvers' display names
    (sorted, one per identity) recorded for it after this call. Refuses,
    fail-closed, on a blank reviewer, an unrecognized verdict, or a missing
    plan file; nothing is written to ``approvals_path`` on refusal.
    """

    reviewer_name = reviewer.strip()
    if not reviewer_name:
        raise RepairApplyError("a reviewer identity is required; it may not be blank")
    if verdict not in (APPROVED_VERDICT, REJECTED_APPROVAL_VERDICT):
        raise RepairApplyError(
            f"verdict must be {APPROVED_VERDICT!r} or {REJECTED_APPROVAL_VERDICT!r}, "
            f"got {verdict!r}"
        )
    _, digest = _plan_digest(plan_path)
    data = _load_approvals(approvals_path)
    approvals_raw = data.get("approvals")
    approvals: dict[str, object] = dict(approvals_raw) if isinstance(approvals_raw, dict) else {}
    entries_raw = approvals.get(digest)
    entries: list[dict[str, str]] = list(entries_raw) if isinstance(entries_raw, list) else []
    entries.append(
        {
            "reviewer": reviewer_name,
            "verdict": verdict,
            "decided_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    approvals[digest] = entries
    data["approvals"] = approvals
    data.setdefault("repair_approval_schema", REPAIR_APPROVAL_SCHEMA_VERSION)
    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    approvals_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return digest, tuple(sorted(_distinct_approvers(entries).values()))


def _last_repair_plan_entry(provenance_path: Path, cluster_id: str) -> dict[str, object] | None:
    """The most recent ``repair-plan`` log entry for this cluster, verified intact.

    Raises if the chain itself does not verify; returns ``None`` when no such
    entry exists (planning was never logged for this cluster, or the log
    predates this feature), which :func:`apply_repair_plan` turns into a
    refusal rather than a guess.
    """

    ok, message = verify_log(provenance_path)
    if not ok:
        raise RepairApplyError(
            f"the provenance log cannot anchor an apply ({provenance_path}): {message}"
        )
    found: dict[str, object] | None = None
    with provenance_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("action") == REPAIR_PLAN_ACTION and entry.get("record_id") == cluster_id:
                found = entry
    return found


@dataclass(frozen=True)
class AppliedRepair:
    """What one call to :func:`apply_repair_plan` did, without repeating PII.

    ``operations`` and ``receipts_path`` are populated only when
    ``dry_run`` is false and at least one operation ran; a dry run reports
    the same preview shape with no receipts file and no provenance entries,
    because it made no destination call at all.
    """

    plan_path: Path
    plan_digest: str
    cluster_id: str
    destination: str
    destination_version: str
    dry_run: bool
    approvers: tuple[str, ...]
    operations: tuple[RepairOperationResult, ...]
    receipts_path: Path | None


def _withheld_split_members(
    recipe: Recipe,
    plan_data: dict[str, object],
    *,
    survivor: str,
    destination: str,
    corrections: Iterable[Correction],
) -> frozenset[str]:
    """``split-create`` members whose current consent blocks the write.

    A no-op returning an empty set when the recipe does not require consent,
    matching the main write path's own gate (``consent.partition_by_consent``)
    exactly: this is the same rule applied to repair-created contacts, not a
    stricter one. Consent is read fresh here, at apply time, rather than
    trusted from the plan, in case a future change loosens the source-batch
    freeze this function currently relies on.

    One honest limit, worth stating plainly rather than implying this check
    catches a live gap: under today's invariants it cannot actually fire for
    a plan produced by ``plan_split``. ``_verify_manifest`` refuses apply
    unless the current source files hash identically to what the original
    write's manifest recorded, corrections cannot touch the consent column
    (``pipeline._apply_corrections`` preserves it), and
    ``Consent.most_restrictive`` means a cluster that was written at all had
    every member's consent active at that moment. So a member proposed here
    for split-create cannot have had its consent change between the original
    write and this apply. This function is a safety net against a future
    change to that freeze, or against a plan applied with a hand-assembled
    ``plan_data`` outside the normal plan-split path, not evidence of a gap
    reachable through today's CLI.
    """

    if not recipe.require_consent:
        return frozenset()
    try:
        records = pipeline.ingest_normalized_records(recipe, corrections=corrections)
    except ValueError as error:
        raise RepairApplyError(
            f"could not reconstruct the source batch for the consent check: {error}"
        ) from error
    today = date.today()
    withheld: set[str] = set()
    split_records = plan_data.get("split_records", [])
    entries = split_records if isinstance(split_records, list) else []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        record_id = str(raw_entry.get("record_id", ""))
        if record_id == survivor:
            continue
        member = records.get(record_id)
        if member is None or not member.consent.is_active(as_of=today, destination=destination):
            withheld.add(record_id)
    return frozenset(withheld)


def _verified_plan(
    recipe: Recipe, manifest_path: Path, plan_path: Path
) -> tuple[dict[str, object], str, str]:
    """Load the plan, and refuse unless it is bound to this manifest and log.

    Returns ``(plan_data, digest, cluster_id)``. This is ADR 0012's T7
    mitigation for the apply path: a plan planned under different sources, or
    a plan file edited since planning (even losslessly, even by hand), is
    caught here before anything else about the apply request is considered.
    """

    _, manifest_digest = _verify_manifest(recipe, manifest_path)
    raw_plan, digest = _plan_digest(plan_path)
    try:
        plan_data: object = json.loads(raw_plan)
    except json.JSONDecodeError as error:
        raise RepairApplyError(f"repair plan is not valid JSON: {plan_path}") from error
    if not isinstance(plan_data, dict):
        raise RepairApplyError(f"repair plan must be a JSON object: {plan_path}")
    if plan_data.get("manifest_hash") != manifest_digest:
        raise RepairApplyError(
            "the repair plan was planned under a different manifest than the one "
            "given here; regenerate the plan with plan-split against this manifest"
        )
    cluster_id = str(plan_data.get("cluster_id", ""))
    if not cluster_id:
        raise RepairApplyError(f"repair plan carries no cluster id: {plan_path}")

    provenance_path = manifest_path.parent / PROVENANCE_FILENAME
    plan_entry = _last_repair_plan_entry(provenance_path, cluster_id)
    if plan_entry is None:
        raise RepairApplyError(
            f"no repair-plan provenance entry is recorded for cluster {cluster_id!r}; "
            "run plan-split before apply-repair"
        )
    if plan_entry.get("content_hash") != digest:
        raise RepairApplyError(
            f"the plan file at {plan_path} does not match the digest the provenance "
            f"log recorded for cluster {cluster_id!r}; it may have been edited or "
            "replaced since planning -- regenerate it with plan-split before applying"
        )
    return plan_data, digest, cluster_id


def apply_repair_plan(
    recipe: Recipe,
    *,
    manifest_path: Path,
    plan_path: Path | None = None,
    approvals_path: Path | None = None,
    receipts_path: Path | None = None,
    corrections: Iterable[Correction] = (),
    connector: Connector | None = None,
    dry_run: bool = True,
) -> AppliedRepair:
    """Apply one repair plan's verified operations, gated fail-closed.

    Refusal order matters and is deliberate. The manifest and plan are
    verified first (drifted sources, a plan planned under a different
    manifest, a plan whose bytes no longer match what the provenance log
    recorded for this cluster -- ADR 0012's T7). Only then is the
    second-reviewer gate checked, and only when ``dry_run`` is false: fewer
    than two distinct recorded approvals of this exact plan digest refuses
    *before any connector is constructed*, so no credential is read and no
    network call is reachable. A dry run needs no approvals and makes no
    connector call either, deriving its preview entirely from the plan's own
    bytes, matching every connector's ``write_all`` dry-run contract.

    Only past the gate is a connector built (via ``pipeline.build_connector``
    unless one is injected for testing), and only if it declares both repair
    capabilities and a verified declaration exists for it at all. Executing
    then reads the destination's live version (``inspect_repair``) and
    refuses unless that exact version is in the declaration's verified list
    -- an operator's word for the version is never trusted over the read.
    Members proposed for ``split-create`` whose current consent is not
    active are withheld from the connector call entirely when the recipe
    requires consent, the same fail-closed rule the main write path applies.

    On a real apply, every attempted operation's receipt (before/after raw
    values, never included in provenance) is written to ``repair_receipts.json``
    and each operation appends its own ``repair-apply`` provenance entry
    naming the operation and the approvers who gated it.
    """

    out_dir = manifest_path.parent
    resolved_plan_path = plan_path if plan_path is not None else out_dir / REPAIR_PLAN_FILENAME
    resolved_approvals_path = (
        approvals_path if approvals_path is not None else out_dir / REPAIR_APPROVALS_FILENAME
    )
    resolved_receipts_path = (
        receipts_path if receipts_path is not None else out_dir / REPAIR_RECEIPTS_FILENAME
    )

    plan_data, digest, cluster_id = _verified_plan(recipe, manifest_path, resolved_plan_path)
    provenance_path = out_dir / PROVENANCE_FILENAME
    survivor = str(plan_data.get("survivor", ""))
    destination = str(plan_data.get("destination", ""))

    approvers = _approvers_for_digest(resolved_approvals_path, digest)
    if not dry_run and len(approvers) < MINIMUM_APPLY_APPROVERS:
        raise RepairApplyError(
            f"applying cluster {cluster_id!r} to a remote destination requires "
            f"{MINIMUM_APPLY_APPROVERS} distinct reviewers' approval of this exact plan "
            f"(digest {digest}); {len(approvers)} recorded. Record approvals with "
            "`reconcile approve-repair` before retrying with --execute."
        )

    if connector is not None:
        resolved_connector = connector
    else:
        resolved_connector = pipeline.build_connector(recipe, out_dir)
    if destination and destination != resolved_connector.name:
        raise RepairApplyError(
            f"the plan was written for destination {destination!r}, but this apply is "
            f"using connector {resolved_connector.name!r}; use the connector the plan "
            "names, or regenerate the plan with plan-split against the recipe you mean "
            "to apply it to"
        )
    if not hasattr(resolved_connector, "apply_repair") or not hasattr(
        resolved_connector, "inspect_repair"
    ):
        raise RepairApplyError(
            f"connector {resolved_connector.name!r} does not implement repair execution; "
            "follow the plan's manual_instructions instead"
        )
    if repair_declaration(resolved_connector.name) is None:
        raise RepairApplyError(
            f"no repair capability is declared for connector {resolved_connector.name!r}; "
            "follow the plan's manual_instructions instead"
        )

    if dry_run:
        preview = resolved_connector.apply_repair(plan_data, fields=recipe.fields, dry_run=True)
        return AppliedRepair(
            plan_path=resolved_plan_path,
            plan_digest=digest,
            cluster_id=cluster_id,
            destination=destination,
            destination_version="",
            dry_run=True,
            approvers=tuple(sorted(approvers.values())),
            operations=tuple(preview),
            receipts_path=None,
        )

    live = resolved_connector.inspect_repair()
    live_version = str(live.get("destination_version", ""))
    if not supported_operations(resolved_connector.name, live_version):
        raise RepairApplyError(
            f"the live destination version {live_version!r} is not in "
            f"{resolved_connector.name!r}'s verified repair-operation list; execution refused"
        )

    withhold_record_ids = _withheld_split_members(
        recipe,
        plan_data,
        survivor=survivor,
        destination=resolved_connector.name,
        corrections=corrections,
    )

    results = resolved_connector.apply_repair(
        plan_data,
        fields=recipe.fields,
        dry_run=False,
        withhold_record_ids=withhold_record_ids,
    )

    approver_list = tuple(sorted(approvers.values()))
    receipt_payload = {
        "repair_receipt_schema": REPAIR_RECEIPT_SCHEMA_VERSION,
        "plan_digest": digest,
        "cluster_id": cluster_id,
        "destination": resolved_connector.name,
        "destination_version": live_version,
        "approvers": list(approver_list),
        "applied_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "operations": [
            {
                "operation": result.operation,
                "record_id": result.record_id,
                "external_id": result.external_id,
                "action": result.action,
                "field": result.field,
                "before": result.before,
                "after": result.after,
                "detail": result.detail,
            }
            for result in results
        ],
    }
    resolved_receipts_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipts_path.write_text(
        json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    log = ProvenanceLog(provenance_path)
    for result in results:
        op_digest = content_hash(
            {
                "action": result.action,
                "field": result.field or "",
                "before": result.before or "",
                "after": result.after or "",
            }
        )
        log.append_repair_apply(
            cluster_id=cluster_id,
            external_id=result.external_id,
            operation=result.operation,
            approvers=approver_list,
            receipt_digest=op_digest,
        )

    return AppliedRepair(
        plan_path=resolved_plan_path,
        plan_digest=digest,
        cluster_id=cluster_id,
        destination=resolved_connector.name,
        destination_version=live_version,
        dry_run=False,
        approvers=approver_list,
        operations=tuple(results),
        receipts_path=resolved_receipts_path,
    )

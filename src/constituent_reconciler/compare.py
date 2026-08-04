"""Read-only comparison of two exports for a migration cutover (UC-02, PR 1).

A nonprofit moving between case systems needs to know whether the target
system's export describes the same people as the legacy system's export,
before anyone changes the live system. ``reconcile compare`` answers that
question with the pieces the run pipeline already trusts: each side's recipe
maps its columns onto the canonical fields, normalization and the matcher
backend score cross-export candidate pairs, and the fail-closed banding from
:mod:`decisions` keeps uncertain identities out of the matched set.

Each ingested record carries its side in ``Record.source`` (``"left"`` or
``"right"``). The matcher itself is untouched; a side label is provenance,
not migration semantics.

Everything this command writes stays local to ``--out``:

* ``cutover_report.csv``: one row per identity, with field values from both
  sides and a per-field conflict flag. A PII artifact, listed in the
  destruction inventory.
* ``cutover_review.csv``: the undecided pairs a person must look at, with
  both records' values. Also a PII artifact.
* ``migration_summary.json``: counts only, never a field value, under its
  own versioned schema.
* ``compare_manifest.json``: digests of both recipes and every input file,
  both column mappings, and the thresholds used, so the counts can be tied
  to the exact exports that produced them.

No write path exists here. The module never names the write-target registry
and builds no destination client, and ``tests/test_compare.py`` enforces both
as merge-blocking invariants in the spirit of ``tests/test_no_egress.py``.
The post-review correction-file export is deliberately absent; it arrives as
its own change with its own review gate.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from constituent_reconciler import __version__, decisions, matching
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.manifest import file_digest, input_digests, splink_version
from constituent_reconciler.models import (
    CANONICAL_FIELDS,
    Band,
    Cluster,
    IngestReport,
    Pair,
    Record,
)
from constituent_reconciler.normalize import normalize_record
from constituent_reconciler.pipeline import IngestAccumulator, _check_distinct_ids, _ingest_source
from constituent_reconciler.schema import MIGRATION_SUMMARY_SCHEMA_VERSION, versions

LEFT = "left"
RIGHT = "right"

MATCHED = "matched"
LEFT_ONLY = "left-only"
RIGHT_ONLY = "right-only"

_STATUS_ORDER = {MATCHED: 0, LEFT_ONLY: 1, RIGHT_ONLY: 2}

CUTOVER_REPORT_FILENAME = "cutover_report.csv"
CUTOVER_REVIEW_FILENAME = "cutover_review.csv"
MIGRATION_SUMMARY_FILENAME = "migration_summary.json"
COMPARE_MANIFEST_FILENAME = "compare_manifest.json"


class CompareError(ValueError):
    """A comparison cannot be set up or accounted for as asked. Fail-closed."""


@dataclass(frozen=True)
class Side:
    """One side of the comparison: its label, its recipe, and how it was given.

    ``bare`` is True when the side was a plain CSV path rather than a recipe
    file; a bare side carries the package-default thresholds and an identity
    column mapping, and it never overrules a recipe side's thresholds.
    """

    label: str
    recipe: Recipe
    bare: bool


def _identity_recipe(path: Path) -> Recipe:
    """Build a Recipe for a bare CSV whose header uses canonical field names."""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle), [])
    columns = {column.strip() for column in header}
    mapping = {name: name for name in CANONICAL_FIELDS if name in columns}
    if "first_name" not in mapping or "last_name" not in mapping:
        raise CompareError(
            f"{path} has no first_name and last_name columns; a bare CSV side must "
            "use the canonical field names as its header, or be described by a "
            "recipe file that maps its columns"
        )
    return Recipe(
        incoming=path,
        mapping=mapping,
        fields=tuple(name for name in CANONICAL_FIELDS if name in mapping),
    )


def load_side(argument: str | Path, *, label: str) -> Side:
    """Load one side of the comparison from a recipe .toml or a bare .csv path.

    A recipe side must read exactly one export: ``input.incoming`` names it,
    and a recipe that also sets ``input.existing`` is refused rather than
    having that file silently ignored.
    """

    path = Path(argument)
    if path.suffix.lower() == ".toml":
        recipe = load_recipe(path)
        if recipe.existing is not None:
            raise CompareError(
                f"the {label} recipe {path} sets input.existing; a compare side reads "
                "exactly one export, so remove the existing entry or point "
                "input.incoming at the export to compare"
            )
        return Side(label=label, recipe=recipe, bare=False)
    if path.is_file() and path.suffix.lower() == ".csv":
        return Side(label=label, recipe=_identity_recipe(path), bare=True)
    raise CompareError(
        f"cannot read the {label} side {path}: give a recipe .toml, or a .csv file "
        "whose header uses the canonical field names"
    )


def _resolve_thresholds(left: Side, right: Side) -> tuple[float, float, float]:
    """One matcher configuration for both sides, fail-closed on disagreement.

    Two recipe sides must state the same prior and thresholds; a silent pick
    of one side's numbers would change which pairs auto-merge without anyone
    deciding that. A bare side has no stated thresholds, so the recipe side's
    numbers govern; two bare sides run on the package defaults.
    """

    if not left.bare and not right.bare:
        left_values = (
            left.recipe.prior,
            left.recipe.auto_threshold,
            left.recipe.review_threshold,
        )
        right_values = (
            right.recipe.prior,
            right.recipe.auto_threshold,
            right.recipe.review_threshold,
        )
        if left_values != right_values:
            raise CompareError(
                "the two recipes disagree on matcher thresholds "
                f"(left prior/auto/review {left_values}, right {right_values}); one "
                "comparison needs one configuration, so align the [thresholds] "
                "sections before comparing"
            )
    governing = right.recipe if left.bare and not right.bare else left.recipe
    return governing.prior, governing.auto_threshold, governing.review_threshold


def _compared_fields(left: Side, right: Side) -> tuple[str, ...]:
    """The canonical fields both sides map. Only these can match or conflict."""

    return tuple(
        name
        for name in CANONICAL_FIELDS
        if name in left.recipe.fields and name in right.recipe.fields
    )


def _address_backend(left: Side, right: Side, fields: tuple[str, ...]) -> str:
    """The address standardizer both sides share, fail-closed on disagreement.

    Normalizing the two sides with different address backends would turn a
    formatting difference into a false value conflict, so when ``address`` is
    among the compared fields the two recipes must name the same backend.
    """

    left_backend = left.recipe.normalize.address_backend
    right_backend = right.recipe.normalize.address_backend
    if "address" in fields and left_backend != right_backend:
        raise CompareError(
            "the two recipes disagree on normalize.address_backend "
            f"(left {left_backend!r}, right {right_backend!r}) while comparing the "
            "address field; align them so a formatting difference cannot read as "
            "a value conflict"
        )
    return left_backend


@dataclass(frozen=True)
class Identity:
    """One identity outcome: a cluster of records, split by side.

    ``status`` partitions identities by membership: ``matched`` has records on
    both sides, ``left-only`` and ``right-only`` have records on one.
    ``ambiguous`` overlaps that partition rather than replacing it: an
    identity is ambiguous when an undecided (review-band) pair connects it to
    another identity, so its status could change once a person decides.
    ``conflicts`` maps a compared field to the display values of each side
    when both sides carry a value and the normalized values disagree.
    """

    identity_id: str
    status: str
    left_members: tuple[str, ...]
    right_members: tuple[str, ...]
    ambiguous: bool
    conflicts: dict[str, tuple[str, str]]


@dataclass(frozen=True)
class CompareResult:
    """The full outcome of one comparison, before any file is written."""

    records: dict[str, Record]
    pairs: tuple[Pair, ...]
    clusters: tuple[Cluster, ...]
    identities: tuple[Identity, ...]
    review_pairs: tuple[Pair, ...]
    fields: tuple[str, ...]
    prior: float
    auto_threshold: float
    review_threshold: float
    left_ingest: IngestReport
    right_ingest: IngestReport
    normalization_failures: dict[str, dict[str, int]]

    @property
    def left_count(self) -> int:
        return sum(1 for record in self.records.values() if record.source == LEFT)

    @property
    def right_count(self) -> int:
        return sum(1 for record in self.records.values() if record.source == RIGHT)


def _display(values: Iterable[str]) -> str:
    """Join the distinct non-empty values for one side of a report cell."""

    distinct = sorted({value for value in values if value})
    return " | ".join(distinct)


def _build_identity(
    cluster: Cluster,
    records: Mapping[str, Record],
    fields: tuple[str, ...],
    *,
    ambiguous: bool,
) -> Identity:
    left_members = tuple(m for m in cluster.members if records[m].source == LEFT)
    right_members = tuple(m for m in cluster.members if records[m].source == RIGHT)
    if left_members and right_members:
        status = MATCHED
    elif left_members:
        status = LEFT_ONLY
    else:
        status = RIGHT_ONLY

    conflicts: dict[str, tuple[str, str]] = {}
    if status == MATCHED:
        for name in fields:
            left_norm = {records[m].normalized.get(name, "") for m in left_members} - {""}
            right_norm = {records[m].normalized.get(name, "") for m in right_members} - {""}
            if left_norm and right_norm and left_norm != right_norm:
                conflicts[name] = (
                    _display(records[m].raw.get(name, "") for m in left_members),
                    _display(records[m].raw.get(name, "") for m in right_members),
                )
    return Identity(
        identity_id=cluster.cluster_id,
        status=status,
        left_members=left_members,
        right_members=right_members,
        ambiguous=ambiguous,
        conflicts=conflicts,
    )


def _check_accounting(records: Mapping[str, Record], identities: Sequence[Identity]) -> None:
    """Every ingested record lands in exactly one identity, or the run stops.

    The cutover report's promise is that no row on either side is silently
    dropped or double-counted. Clustering upholds that by construction; this
    guard re-derives it from the finished identities so a future defect fails
    the comparison instead of shipping a wrong report.
    """

    seen: dict[str, int] = {}
    for identity in identities:
        for member in identity.left_members + identity.right_members:
            seen[member] = seen.get(member, 0) + 1
    missing = sorted(set(records) - set(seen))
    if missing:
        raise CompareError(f"row accounting failed: record {missing[0]!r} is in no identity")
    duplicated = sorted(name for name, count in seen.items() if count > 1)
    if duplicated:
        raise CompareError(
            f"row accounting failed: record {duplicated[0]!r} is in more than one identity"
        )
    extra = sorted(set(seen) - set(records))
    if extra:
        raise CompareError(
            f"row accounting failed: identity member {extra[0]!r} matches no ingested record"
        )


def run_compare(left: Side, right: Side) -> CompareResult:
    """Ingest both sides, score cross-export pairs, and classify identities.

    Reads only; nothing durable is produced here. Confident merges become
    matched identities, undecided pairs mark their identities ambiguous, and
    the accounting guard confirms every record landed exactly once.
    """

    fields = _compared_fields(left, right)
    prior, auto_threshold, review_threshold = _resolve_thresholds(left, right)
    address_backend = _address_backend(left, right, fields)

    left_accounting = IngestAccumulator()
    right_accounting = IngestAccumulator()
    raw_records = _ingest_source(
        left.recipe.incoming, LEFT, recipe=left.recipe, id_prefix="L", accounting=left_accounting
    )
    raw_records += _ingest_source(
        right.recipe.incoming,
        RIGHT,
        recipe=right.recipe,
        id_prefix="R",
        accounting=right_accounting,
    )
    _check_distinct_ids(raw_records)

    failures: dict[str, dict[str, int]] = {}
    records = {
        r.unique_id: normalize_record(r, fields, address_backend=address_backend, failures=failures)
        for r in raw_records
    }

    scored = matching.score_pairs(records.values(), fields, prior=prior)
    pairs = tuple(
        decisions.band_pairs(
            scored, auto_threshold=auto_threshold, review_threshold=review_threshold
        )
    )
    clusters = tuple(decisions.build_clusters(records.keys(), pairs))

    cluster_of: dict[str, str] = {}
    for cluster in clusters:
        for member in cluster.members:
            cluster_of[member] = cluster.cluster_id

    review_pairs = tuple(
        pair
        for pair in pairs
        if pair.band is Band.REVIEW and cluster_of[pair.left] != cluster_of[pair.right]
    )
    ambiguous_ids = {cluster_of[pair.left] for pair in review_pairs} | {
        cluster_of[pair.right] for pair in review_pairs
    }

    identities = tuple(
        _build_identity(cluster, records, fields, ambiguous=cluster.cluster_id in ambiguous_ids)
        for cluster in clusters
    )
    _check_accounting(records, identities)

    return CompareResult(
        records=records,
        pairs=pairs,
        clusters=clusters,
        identities=identities,
        review_pairs=review_pairs,
        fields=fields,
        prior=prior,
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
        left_ingest=left_accounting.freeze(),
        right_ingest=right_accounting.freeze(),
        normalization_failures=failures,
    )


def _status_counts(result: CompareResult) -> tuple[int, int, int]:
    matched = sum(1 for identity in result.identities if identity.status == MATCHED)
    left_only = sum(1 for identity in result.identities if identity.status == LEFT_ONLY)
    right_only = sum(1 for identity in result.identities if identity.status == RIGHT_ONLY)
    return matched, left_only, right_only


def summary_payload(result: CompareResult) -> dict[str, object]:
    """The count-only migration summary. No field value enters this payload."""

    matched, left_only, right_only = _status_counts(result)
    conflict_counts: dict[str, int] = {}
    for identity in result.identities:
        for name in identity.conflicts:
            conflict_counts[name] = conflict_counts.get(name, 0) + 1
    return {
        "schema_version": MIGRATION_SUMMARY_SCHEMA_VERSION,
        "compared_fields": list(result.fields),
        "left_records": result.left_count,
        "right_records": result.right_count,
        "identities": len(result.identities),
        "matched_identities": matched,
        "left_only_identities": left_only,
        "right_only_identities": right_only,
        "ambiguous_identities": sum(1 for i in result.identities if i.ambiguous),
        "review_pairs": len(result.review_pairs),
        "identities_with_conflicts": sum(1 for i in result.identities if i.conflicts),
        "conflict_counts": conflict_counts,
        "thresholds": {
            "prior": result.prior,
            "auto": result.auto_threshold,
            "review": result.review_threshold,
        },
        "note": (
            "Count-only migration summary. Field values stay in the local cutover "
            "artifacts (cutover_report.csv, cutover_review.csv); ambiguous "
            "identities overlap the matched/left-only/right-only partition."
        ),
    }


def write_migration_summary(result: CompareResult, out_dir: Path) -> Path:
    """Write ``migration_summary.json``: counts under a versioned schema."""

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / MIGRATION_SUMMARY_FILENAME
    payload = summary_payload(result)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_cutover_report(result: CompareResult, out_dir: Path) -> Path:
    """Write ``cutover_report.csv``: one row per identity, values from both sides.

    This is the human-facing report and a PII artifact: each compared field
    shows the distinct raw values each side carries, and a per-field conflict
    column marks where the two sides disagree on a person the comparison
    matched. ``needs_review`` marks identities an undecided pair touches.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / CUTOVER_REPORT_FILENAME
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["identity", "status", "needs_review", "left_members", "right_members"]
        for name in result.fields:
            header += [f"{name}_left", f"{name}_right", f"{name}_conflict"]
        writer.writerow(header)
        ordered = sorted(result.identities, key=lambda i: (_STATUS_ORDER[i.status], i.identity_id))
        for identity in ordered:
            row = [
                identity.identity_id,
                identity.status,
                "yes" if identity.ambiguous else "",
                "|".join(identity.left_members),
                "|".join(identity.right_members),
            ]
            for name in result.fields:
                left_value = _display(
                    result.records[m].raw.get(name, "") for m in identity.left_members
                )
                right_value = _display(
                    result.records[m].raw.get(name, "") for m in identity.right_members
                )
                row += [left_value, right_value, "yes" if name in identity.conflicts else ""]
            writer.writerow(row)
    return path


def write_cutover_review(result: CompareResult, out_dir: Path) -> Path:
    """Write ``cutover_review.csv``: the undecided pairs a person must look at.

    Each row is a review-band pair whose endpoints sit in different
    identities, with both records' raw values side by side. A PII artifact,
    local only.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / CUTOVER_REVIEW_FILENAME
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["left", "right", "probability", "left_side", "right_side"]
        for name in result.fields:
            header += [f"{name}_left", f"{name}_right"]
        writer.writerow(header)
        ordered = sorted(result.review_pairs, key=lambda p: (-p.probability, p.left, p.right))
        for pair in ordered:
            first = result.records[pair.left]
            second = result.records[pair.right]
            row = [pair.left, pair.right, f"{pair.probability:.4f}", first.source, second.source]
            for name in result.fields:
                row += [first.raw.get(name, ""), second.raw.get(name, "")]
            writer.writerow(row)
    return path


def _side_manifest(side: Side) -> dict[str, object]:
    recipe = side.recipe
    return {
        "recipe_hash": (
            file_digest(recipe.recipe_path) if recipe.recipe_path is not None else None
        ),
        "mapping": dict(recipe.mapping),
        "input_hashes": input_digests([recipe.incoming]),
    }


def build_compare_manifest(left: Side, right: Side, result: CompareResult) -> dict[str, object]:
    """Assemble the comparison manifest: both recipes and inputs, by digest.

    A bare CSV side has no recipe file, so its ``recipe_hash`` is null and its
    derived column mapping is recorded explicitly instead; either way the
    manifest binds the reported counts to the exact exports and mappings that
    produced them. Digests and column names only, never a field value.
    """

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "package_version": __version__,
        "splink_version": splink_version(),
        "schema_versions": versions(),
        "compared_fields": list(result.fields),
        "thresholds": {
            "prior": result.prior,
            "auto": result.auto_threshold,
            "review": result.review_threshold,
        },
        "left": _side_manifest(left),
        "right": _side_manifest(right),
    }


def write_compare_manifest(manifest: dict[str, object], out_dir: Path) -> Path:
    """Write the comparison manifest to ``out_dir/compare_manifest.json``."""

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / COMPARE_MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _ingest_lines(label: str, ingest: IngestReport) -> list[str]:
    lines = [f"{label + ' files read:':<22}{len(ingest.files_read)}"]
    lines += [f"    {path}" for path in ingest.files_read]
    if ingest.files_skipped:
        lines.append(f"{label + ' files skipped:':<22}{len(ingest.files_skipped)}")
        lines += [f"    {skipped.path} ({skipped.reason})" for skipped in ingest.files_skipped]
    return lines


def render_compare_summary(result: CompareResult) -> str:
    """Render the terminal summary: counts only, no field values."""

    matched, left_only, right_only = _status_counts(result)
    ambiguous = sum(1 for identity in result.identities if identity.ambiguous)
    conflicted = sum(1 for identity in result.identities if identity.conflicts)
    lines = [
        f"left records:         {result.left_count}",
        f"right records:        {result.right_count}",
        f"identities:           {len(result.identities)}",
        f"  on both sides:      {matched}",
        f"  left only:          {left_only}",
        f"  right only:         {right_only}",
        f"needing review:       {ambiguous} "
        f"({len(result.review_pairs)} undecided pair(s); see {CUTOVER_REVIEW_FILENAME})",
        f"with value conflicts: {conflicted}",
    ]
    lines.append("")
    lines += _ingest_lines("left", result.left_ingest)
    lines += _ingest_lines("right", result.right_ingest)
    if result.normalization_failures:
        lines.append("normalization failures (value present, nothing parseable):")
        for name in sorted(result.normalization_failures):
            per_source = result.normalization_failures[name]
            counts = ", ".join(f"{source}: {count}" for source, count in sorted(per_source.items()))
            lines.append(f"    {name}: {counts}")
    return "\n".join(lines)

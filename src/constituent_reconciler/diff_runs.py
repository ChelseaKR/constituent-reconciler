"""What changed between two runs of one recipe (``constituent-reconcile diff-runs``).

An operator re-runs the same recipe every month and nothing tells them what
moved. ``compare`` answers a different question -- two *sources* inside one run
-- so a data manager defending this month's numbers against last month's had
only two output directories and their own eyes.

This reads both runs' committed artifacts and reports six sections: which input
files changed, which clusters formed, dissolved or changed membership, which
pairs entered or left the review queue, which reviewed decisions no longer
apply, the consent-withheld delta, and any threshold or policy-pack change.
Nothing is re-scored and no matcher runs: every number here comes from bytes
both runs already wrote.

TWO THINGS THIS MODULE REFUSES TO DO, both the same defect wearing different
clothes.

**An empty diff and a diff that could not be computed must not render the
same way.** Two identical runs produce an empty diff and exit 0, which is a real
answer. A run directory missing ``run_summary.json``, or holding one that does
not parse, produces an error naming the file -- never an empty section that an
operator reads as "nothing changed". Same shape as the zero-denominator defect
in the eval gate: a measurement that did not happen is not a measurement of
zero.

**A diff across a recipe change silently attributes threshold effects to data
drift.** If the recipe hash, the thresholds, or the policy pack differ, the
comparison is refused unless ``--allow-recipe-change``, and then the recipe
change is recorded as the diff's first section so no one reads the cluster
counts without it.

Output is two files. ``run_diff.json`` is counts only, safe to share, and
passes through the same small-cell suppression ``aggregate_summary.json`` uses
under a pack that requires it. ``run_diff_detail.csv`` carries the cluster and
record ids and is a local PII artifact in ``destruction.PII_ARTIFACTS``.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from constituent_reconciler.policy import Policy
from constituent_reconciler.schema import RUN_DIFF_SCHEMA_VERSION
from constituent_reconciler.suppression import suppress_cells

RUN_DIFF_FILENAME = "run_diff.json"
RUN_DIFF_DETAIL_FILENAME = "run_diff_detail.csv"

MANIFEST_FILE = "run_manifest.json"
SUMMARY_FILE = "run_summary.json"
RESOLVED_FILE = "resolved.csv"
REVIEW_QUEUE_FILE = "review_queue.csv"
DECISIONS_FILE = "decisions.json"
AUTO_MERGES_FILE = "auto_merges.json"

#: Why a reviewed decision may no longer apply to the later run. Both mean the
#: same thing operationally -- the verdict was given about evidence that is no
#: longer what the run holds -- but they need different follow-up, so they are
#: never collapsed into one bucket.
INVALIDATION_PAIR_ABSENT = "pair-absent"
INVALIDATION_EVIDENCE_CHANGED = "evidence-changed"


class RunDiffError(ValueError):
    """Diffing refused, fail-closed.

    Every raise happens before any diff bytes exist, so a refused comparison
    leaves no ``run_diff.json`` and no detail file behind for someone to read
    as a result.
    """


@dataclass(frozen=True)
class Pair:
    """One candidate pair, id-ordered so the two runs' pairs compare equal."""

    left: str
    right: str

    @classmethod
    def of(cls, left: str, right: str) -> Pair:
        return cls(*sorted((left, right)))


@dataclass
class _Run:
    """One run's artifacts, already read and refused if unreadable."""

    label: str
    out_dir: Path
    manifest: dict[str, object]
    summary: dict[str, object]
    clusters: dict[str, tuple[str, ...]]
    review: dict[Pair, float]
    auto: dict[Pair, float]
    decisions: dict[Pair, str] | None


@dataclass(frozen=True)
class RunDiff:
    """The computed diff. Ids live in ``detail``; everything else is counts."""

    recipe_changed: bool
    recipe_changes: tuple[str, ...]
    inputs_changed: tuple[str, ...]
    clusters_added: tuple[str, ...]
    clusters_removed: tuple[str, ...]
    clusters_membership_changed: tuple[str, ...]
    review_entered: tuple[Pair, ...]
    review_left: tuple[Pair, ...]
    decisions_invalidated: tuple[tuple[Pair, str], ...]
    withheld_before: int
    withheld_after: int
    decisions_present: bool
    detail_rows: tuple[tuple[str, str, str], ...] = field(default=())

    @property
    def empty(self) -> bool:
        """True when nothing moved. Only ever reached on a computed diff."""

        return not (
            self.recipe_changed
            or self.inputs_changed
            or self.clusters_added
            or self.clusters_removed
            or self.clusters_membership_changed
            or self.review_entered
            or self.review_left
            or self.decisions_invalidated
            or self.withheld_before != self.withheld_after
        )


def _read_json(path: Path, label: str) -> dict[str, object]:
    """One run artifact, or a refusal naming the file and which run it is in.

    The three failure modes are kept distinct in the message because they need
    different fixes: the run never wrote it, the file is truncated, or it is
    the wrong shape entirely.
    """

    if not path.is_file():
        raise RunDiffError(
            f"the {label} run has no {path.name} at {path}. A diff cannot be computed "
            "without it, and reporting no changes would be a different answer than "
            "reporting that the comparison could not be made."
        )
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunDiffError(f"the {label} run's {path.name} could not be read: {error}") from error
    if not isinstance(data, dict):
        raise RunDiffError(f"the {label} run's {path.name} must be a JSON object: {path}")
    return {str(key): value for key, value in data.items()}


def _read_clusters(out_dir: Path, label: str) -> dict[str, tuple[str, ...]]:
    """Cluster id -> members, from ``resolved.csv``.

    A dry run writes no ``resolved.csv``, so its absence is a real and
    explainable state -- but it is not an empty cluster set, and treating it as
    one would report every cluster in the other run as added or removed.
    """

    path = out_dir / RESOLVED_FILE
    if not path.is_file():
        raise RunDiffError(
            f"the {label} run has no {RESOLVED_FILE} at {path}; it may have been a "
            "--dry-run, or the output directory may have been partially destroyed. "
            "Either way the clusters cannot be compared, which is not the same as "
            "their being unchanged."
        )
    clusters: dict[str, tuple[str, ...]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            cluster_id = (row.get("cluster_id") or "").strip()
            if not cluster_id:
                continue
            members = tuple(sorted(m for m in (row.get("members") or "").split("|") if m))
            clusters[cluster_id] = members
    return clusters


def _read_review(out_dir: Path, label: str) -> dict[Pair, float]:
    """Pair -> probability, from ``review_queue.csv``."""

    path = out_dir / REVIEW_QUEUE_FILE
    if not path.is_file():
        raise RunDiffError(
            f"the {label} run has no {REVIEW_QUEUE_FILE} at {path}; an empty queue is "
            "written as a header-only file, so a missing one means the artifact is "
            "gone rather than that no pair needed review."
        )
    pairs: dict[Pair, float] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            left, right = (row.get("left") or "").strip(), (row.get("right") or "").strip()
            if not left or not right:
                continue
            pairs[Pair.of(left, right)] = _probability(row.get("probability"), path)
    return pairs


def _probability(raw: object, path: Path) -> float:
    """A pair's probability, or a refusal.

    A blank or unparseable probability is not 0.0. Zero is the strongest
    possible statement that two records are different people, so substituting
    it for an unreadable cell would report a pair as maximally distinguished on
    the strength of a missing value.
    """

    try:
        return float(str(raw))
    except (TypeError, ValueError) as error:
        raise RunDiffError(
            f"{path} carries a pair whose probability is not a number ({raw!r}); "
            "an unreadable probability is not a low one"
        ) from error


def _read_auto(out_dir: Path) -> dict[Pair, float]:
    """Pair -> probability, from ``auto_merges.json``.

    Optional by construction: this artifact arrived after the first releases,
    so an older run directory legitimately lacks it. Its absence narrows what
    the decision-invalidation section can see, which
    :func:`_decision_invalidations` accounts for rather than ignoring.
    """

    path = out_dir / AUTO_MERGES_FILE
    if not path.is_file():
        return {}
    data = _read_json(path, "auto-merge")
    raw = data.get("pairs")
    if not isinstance(raw, list):
        return {}
    pairs: dict[Pair, float] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        left, right = str(entry.get("left", "")), str(entry.get("right", ""))
        if left and right:
            pairs[Pair.of(left, right)] = _probability(entry.get("probability"), path)
    return pairs


def _read_decisions(out_dir: Path) -> dict[Pair, str] | None:
    """Pair -> verdict, or ``None`` when the run has no decisions file at all.

    ``None`` and ``{}`` are different findings and are kept apart all the way
    to the rendered output: nobody reviewed anything, versus a review session
    happened and decided nothing.
    """

    path = out_dir / DECISIONS_FILE
    if not path.is_file():
        return None
    data = _read_json(path, "before")
    decisions: dict[Pair, str] = {}
    for verdict in ("approved", "rejected"):
        raw = data.get(verdict, [])
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if isinstance(entry, list) and len(entry) == 2:
                decisions[Pair.of(str(entry[0]), str(entry[1]))] = verdict
    return decisions


def _load_run(out_dir: Path, label: str) -> _Run:
    return _Run(
        label=label,
        out_dir=out_dir,
        manifest=_read_json(out_dir / MANIFEST_FILE, label),
        summary=_read_json(out_dir / SUMMARY_FILE, label),
        clusters=_read_clusters(out_dir, label),
        review=_read_review(out_dir, label),
        auto=_read_auto(out_dir),
        decisions=_read_decisions(out_dir),
    )


def _recipe_changes(before: _Run, after: _Run) -> list[str]:
    """Every way the two runs were configured differently, named."""

    changes: list[str] = []
    if before.manifest.get("recipe_hash") != after.manifest.get("recipe_hash"):
        changes.append("recipe_hash")
    if before.manifest.get("policy_pack") != after.manifest.get("policy_pack"):
        changes.append(
            f"policy_pack ({before.manifest.get('policy_pack')!r} -> "
            f"{after.manifest.get('policy_pack')!r})"
        )
    before_thresholds = before.manifest.get("thresholds")
    after_thresholds = after.manifest.get("thresholds")
    if before_thresholds != after_thresholds:
        changes.append(f"thresholds ({before_thresholds!r} -> {after_thresholds!r})")
    return changes


def _schema_mismatch(before: _Run, after: _Run) -> str | None:
    """The declared schema surfaces the two runs disagree on, if any.

    Refused unconditionally rather than behind a flag: a schema change means
    the two directories' artifacts do not have the same meaning, so a diff of
    them is not a wrong number, it is a category error.
    """

    left = before.manifest.get("schema_versions")
    right = after.manifest.get("schema_versions")
    if not isinstance(left, dict) or not isinstance(right, dict) or left == right:
        return None
    differing = sorted(name for name in set(left) | set(right) if left.get(name) != right.get(name))
    return ", ".join(f"{name} {left.get(name)!r} -> {right.get(name)!r}" for name in differing)


def _changed_inputs(before: _Run, after: _Run) -> list[str]:
    left = before.manifest.get("input_hashes")
    right = after.manifest.get("input_hashes")
    left_map = {str(k): str(v) for k, v in left.items()} if isinstance(left, dict) else {}
    right_map = {str(k): str(v) for k, v in right.items()} if isinstance(right, dict) else {}
    symmetric = set(left_map) ^ set(right_map)
    changed = {n for n in set(left_map) & set(right_map) if left_map[n] != right_map[n]}
    return sorted(symmetric | changed)


def _decision_invalidations(before: _Run, after: _Run) -> list[tuple[Pair, str]]:
    """Reviewed pairs whose verdict may no longer apply to the later run.

    This is the only section that re-derives anything rather than
    set-differencing two files, and it is the section a data manager actually
    needs: a verdict is a statement about specific evidence, and when that
    evidence moves the verdict stops being an answer to the current question.

    Two reasons, kept apart. ``pair-absent`` means the later run never
    considered the pair at all -- it is in neither the review queue nor the
    auto-merge record -- so the reviewer's decision is about a comparison that
    no longer happens. ``evidence-changed`` means the pair is still there and
    its probability moved, so the verdict was given against a different number
    than the one in force now.

    A probability is compared exactly. Both runs round to four places when they
    write, so an equal comparison here is comparing what was published, not two
    floats that happen to differ in the seventeenth digit.
    """

    if not before.decisions:
        return []
    invalid: list[tuple[Pair, str]] = []
    for pair in sorted(before.decisions, key=lambda p: (p.left, p.right)):
        before_probability = before.review.get(pair, before.auto.get(pair))
        after_probability = after.review.get(pair, after.auto.get(pair))
        if after_probability is None:
            invalid.append((pair, INVALIDATION_PAIR_ABSENT))
        elif before_probability is not None and before_probability != after_probability:
            invalid.append((pair, INVALIDATION_EVIDENCE_CHANGED))
    return invalid


def _int_field(run: _Run, name: str) -> int:
    """A count from a run summary, or a refusal.

    Never defaults to zero. A summary whose ``withheld_no_consent`` is missing
    or non-numeric would otherwise report "nobody was withheld", which is the
    reassuring reading of a broken artifact.
    """

    value = run.summary.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RunDiffError(
            f"the {run.label} run's {SUMMARY_FILE} has no integer {name!r} "
            f"(found {value!r}); this diff will not substitute a zero for it"
        )
    return value


def diff_runs(before_dir: Path, after_dir: Path, *, allow_recipe_change: bool = False) -> RunDiff:
    """Compare two runs of one recipe. Read-only; nothing is re-scored.

    Refuses, fail-closed, on a run directory missing or holding an unreadable
    ``run_manifest.json``, ``run_summary.json``, ``resolved.csv`` or
    ``review_queue.csv``; on declared schema versions that differ; and on a
    recipe, threshold or policy-pack change unless ``allow_recipe_change``.
    """

    before = _load_run(before_dir, "before")
    after = _load_run(after_dir, "after")

    mismatch = _schema_mismatch(before, after)
    if mismatch is not None:
        raise RunDiffError(
            f"the two runs declare different schema versions ({mismatch}); their "
            "artifacts do not have the same meaning, so diffing them would compare "
            "unlike things. Re-run the earlier batch on this version first."
        )

    changes = _recipe_changes(before, after)
    if changes and not allow_recipe_change:
        raise RunDiffError(
            f"the two runs were not configured the same way ({', '.join(changes)}). A "
            "diff across a configuration change attributes threshold effects to data "
            "drift; pass --allow-recipe-change to compare anyway, and the change will "
            "be recorded as the diff's first section."
        )

    added = sorted(set(after.clusters) - set(before.clusters))
    removed = sorted(set(before.clusters) - set(after.clusters))
    membership = sorted(
        key
        for key in set(before.clusters) & set(after.clusters)
        if before.clusters[key] != after.clusters[key]
    )
    entered = sorted(set(after.review) - set(before.review), key=lambda p: (p.left, p.right))
    left_queue = sorted(set(before.review) - set(after.review), key=lambda p: (p.left, p.right))
    invalidated = _decision_invalidations(before, after)

    detail: list[tuple[str, str, str]] = []
    for cluster in added:
        detail.append(("cluster-added", cluster, "|".join(after.clusters[cluster])))
    for cluster in removed:
        detail.append(("cluster-removed", cluster, "|".join(before.clusters[cluster])))
    for cluster in membership:
        detail.append(
            (
                "cluster-membership-changed",
                cluster,
                f"{'|'.join(before.clusters[cluster])} -> {'|'.join(after.clusters[cluster])}",
            )
        )
    for pair in entered:
        detail.append(("review-entered", f"{pair.left}+{pair.right}", ""))
    for pair in left_queue:
        detail.append(("review-left", f"{pair.left}+{pair.right}", ""))
    for pair, reason in invalidated:
        detail.append(("decision-invalidated", f"{pair.left}+{pair.right}", reason))

    return RunDiff(
        recipe_changed=bool(changes),
        recipe_changes=tuple(changes),
        inputs_changed=tuple(_changed_inputs(before, after)),
        clusters_added=tuple(added),
        clusters_removed=tuple(removed),
        clusters_membership_changed=tuple(membership),
        review_entered=tuple(entered),
        review_left=tuple(left_queue),
        decisions_invalidated=tuple(invalidated),
        withheld_before=_int_field(before, "withheld_no_consent"),
        withheld_after=_int_field(after, "withheld_no_consent"),
        decisions_present=before.decisions is not None,
        detail_rows=tuple(detail),
    )


def diff_payload(diff: RunDiff, *, policy: Policy | None = None) -> dict[str, object]:
    """The count-only ``run_diff.json`` payload, suppressed where the pack says.

    Under a pack that requires aggregate suppression the counts go through the
    same ``suppress_cells`` the aggregate summary uses, so a diff cannot become
    the small-cell disclosure the aggregate export exists to prevent. The
    threshold is stamped into the payload, so a reader can tell a suppressed
    cell from a zero without knowing which pack produced the file.
    """

    counts = {
        "inputs_changed": len(diff.inputs_changed),
        "clusters_added": len(diff.clusters_added),
        "clusters_removed": len(diff.clusters_removed),
        "clusters_membership_changed": len(diff.clusters_membership_changed),
        "review_entered": len(diff.review_entered),
        "review_left": len(diff.review_left),
        "decisions_invalidated": len(diff.decisions_invalidated),
    }
    suppressed = policy is not None and policy.aggregate_export
    # The pack's own configured threshold, not the module default: a pack that
    # sets a stricter one must not be silently published at the looser one.
    threshold = policy.suppression_threshold if policy is not None else None
    payload: dict[str, object] = {
        "run_diff_schema": RUN_DIFF_SCHEMA_VERSION,
        "empty": diff.empty,
        "recipe_changed": diff.recipe_changed,
        "recipe_changes": list(diff.recipe_changes),
        "counts": (
            dict(suppress_cells(counts, threshold=threshold))
            if suppressed and threshold is not None
            else dict(counts)
        ),
        "suppression": {
            "applied": suppressed,
            "threshold": threshold if suppressed else None,
        },
        "withheld_no_consent": {
            "before": diff.withheld_before,
            "after": diff.withheld_after,
            "delta": diff.withheld_after - diff.withheld_before,
        },
        # Not a count. "The earlier run has no decisions file" and "the earlier
        # run decided nothing" both yield zero invalidations, and only one of
        # them means a reviewer looked.
        "decisions_reviewed": diff.decisions_present,
    }
    return payload


def write_diff(diff: RunDiff, out_dir: Path, *, policy: Policy | None = None) -> tuple[Path, Path]:
    """Write ``run_diff.json`` and ``run_diff_detail.csv``; return both paths.

    The detail file is written even when it has no rows, so an operator can
    tell "the diff ran and found nothing" from "the diff never ran".
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    diff_path = out_dir / RUN_DIFF_FILENAME
    payload = diff_payload(diff, policy=policy)
    diff_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    detail_path = out_dir / RUN_DIFF_DETAIL_FILENAME
    with detail_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["change", "id", "detail"])
        writer.writerows(diff.detail_rows)
    return diff_path, detail_path


def render_diff(diff: RunDiff, *, policy: Policy | None = None) -> str:
    """The human-readable rendering. Counts and section names, never ids."""

    payload = diff_payload(diff, policy=policy)
    counts = payload["counts"]
    if not isinstance(counts, dict):  # pragma: no cover - diff_payload always builds a dict
        raise RunDiffError("the diff payload lost its counts section")
    lines = ["# Run diff", ""]
    if diff.recipe_changed:
        # First, deliberately. Every count below is partly an effect of this.
        lines += [
            "## Configuration changed",
            "",
            "These two runs were not configured the same way, so some of the changes "
            "below are effects of the configuration and not of the data:",
            "",
        ]
        lines += [f"- {change}" for change in diff.recipe_changes]
        lines.append("")
    if diff.empty:
        lines += ["Nothing changed between these two runs.", ""]
    lines += [
        "| change | count |",
        "| --- | --- |",
        *(f"| {name.replace('_', ' ')} | {value} |" for name, value in counts.items()),
        "",
        f"Withheld for consent: {diff.withheld_before} -> {diff.withheld_after}",
        "",
    ]
    if not diff.decisions_present:
        lines += [
            "The earlier run has no decisions file, so no reviewed verdict could be "
            "checked for invalidation. That is not the same as no verdict having been "
            "invalidated.",
            "",
        ]
    return "\n".join(lines)

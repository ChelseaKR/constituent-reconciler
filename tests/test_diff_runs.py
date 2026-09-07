"""Two runs of one recipe, and what moved between them.

An operator re-runs the same recipe every month and defends the new numbers
against the old ones. ``compare`` answers a different question (two sources
inside one run), so until now that defence was two output directories and a
pair of eyes.

The failure mode this suite is written against is not a wrong count. It is a
diff that could not be computed rendering exactly like a diff that found
nothing: a missing ``run_summary.json``, a dry-run directory with no
``resolved.csv``, a summary whose ``withheld_no_consent`` is absent. Each of
those would print zeros, and zero is the reassuring answer. Every one of them
refuses by name instead.

The end-to-end tests drive the real pipeline over the bundled demo. The engine
tests build run directories by hand, because the cases that matter -- a pair
whose probability moved, a summary missing a key -- are reached precisely and
not by hoping the matcher produces them.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from constituent_reconciler import diff_runs
from constituent_reconciler.cli import main
from constituent_reconciler.destruction import NOT_DESTROYED, PII_ARTIFACTS
from constituent_reconciler.diff_runs import (
    RUN_DIFF_DETAIL_FILENAME,
    RUN_DIFF_FILENAME,
    Pair,
    RunDiffError,
)
from constituent_reconciler.diff_runs import (
    diff_runs as compute_diff,
)
from constituent_reconciler.schema import versions

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
DEMO_FILES = ("recipe.toml", "existing.csv", "incoming.csv")

#: The demo row the added incoming record duplicates. Chosen because it is
#: written as its own single-record cluster in the "before" run, so the change
#: shows up as a membership change on a known id rather than a new cluster.
DUPLICATED = "existing:E001"


def _demo(tmp_path: Path) -> Path:
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in DEMO_FILES:
        shutil.copy(EXAMPLES / name, demo / name)
    return demo


def _run(recipe: Path, out_dir: Path) -> None:
    assert main(["run", "--config", str(recipe), "--out", str(out_dir)]) == 0


def _diff_cli(before: Path, after: Path, *extra: str) -> int:
    return main(["diff-runs", "--before", str(before), "--after", str(after), *extra])


def _payload(out_dir: Path) -> dict[str, object]:
    data = json.loads((out_dir / RUN_DIFF_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _detail(out_dir: Path) -> list[str]:
    return (out_dir / RUN_DIFF_DETAIL_FILENAME).read_text(encoding="utf-8").splitlines()


# -- hand-built run directories, for the cases the matcher will not hand you ---


def _write_run(
    out_dir: Path,
    *,
    clusters: dict[str, list[str]],
    review: dict[tuple[str, str], float] | None = None,
    auto: dict[tuple[str, str], float] | None = None,
    withheld: int | object = 0,
    thresholds: dict[str, float] | None = None,
    recipe_hash: str = "aa" * 32,
    policy_pack: str = "default",
    schema_versions: dict[str, int] | None = None,
    inputs: dict[str, str] | None = None,
    decisions: dict[str, list[list[str]]] | None = None,
) -> Path:
    """A minimal but structurally real run output directory."""

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "recipe_hash": recipe_hash,
                "policy_pack": policy_pack,
                "thresholds": thresholds or {"prior": 0.01, "auto": 0.97, "review": 0.80},
                "input_hashes": inputs or {"existing.csv": "11" * 32},
                "schema_versions": schema_versions or versions(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    summary: dict[str, object] = {"policy_pack": policy_pack}
    if withheld is not None:
        summary["withheld_no_consent"] = withheld
    (out_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    rows = ["cluster_id,primary,members,consent"]
    for cluster_id, members in clusters.items():
        rows.append(f"{cluster_id},{members[0]},{'|'.join(members)},granted")
    (out_dir / "resolved.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    queue = ["left,right,probability"]
    for (left, right), probability in (review or {}).items():
        queue.append(f"{left},{right},{probability:.4f}")
    (out_dir / "review_queue.csv").write_text("\n".join(queue) + "\n", encoding="utf-8")

    if auto is not None:
        (out_dir / "auto_merges.json").write_text(
            json.dumps(
                {
                    "pairs": [
                        {"left": left, "right": right, "probability": probability}
                        for (left, right), probability in auto.items()
                    ]
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if decisions is not None:
        (out_dir / "decisions.json").write_text(
            json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8"
        )
    return out_dir


# -- end to end, through the real pipeline ------------------------------------


def test_one_added_incoming_row_names_exactly_that_cluster(tmp_path: Path) -> None:
    """EXP's own acceptance criterion, driven through the real command."""

    demo = _demo(tmp_path)
    recipe = demo / "recipe.toml"
    before, after = tmp_path / "before", tmp_path / "after"
    _run(recipe, before)

    incoming = demo / "incoming.csv"
    incoming.write_text(
        incoming.read_text(encoding="utf-8") + "N999,Linda,Tran,1975-08-14,,,granted\n",
        encoding="utf-8",
    )
    _run(recipe, after)

    assert _diff_cli(before, after) == 0
    payload = _payload(after)
    counts = payload["counts"]
    assert isinstance(counts, dict)
    assert counts["clusters_membership_changed"] == 1
    assert counts["clusters_added"] == 0
    assert counts["clusters_removed"] == 0
    assert payload["empty"] is False

    detail = _detail(after)
    changed = [line for line in detail if line.startswith("cluster-membership-changed")]
    assert len(changed) == 1
    assert DUPLICATED in changed[0]
    assert "incoming:N999" in changed[0]


def test_two_identical_runs_produce_an_empty_diff_and_exit_zero(tmp_path: Path) -> None:
    demo = _demo(tmp_path)
    recipe = demo / "recipe.toml"
    before, after = tmp_path / "before", tmp_path / "after"
    _run(recipe, before)
    _run(recipe, after)

    assert _diff_cli(before, after) == 0
    payload = _payload(after)
    assert payload["empty"] is True
    counts = payload["counts"]
    assert isinstance(counts, dict)
    assert all(value == 0 for value in counts.values())
    # The detail file exists and is header-only. An absent file cannot say
    # "the diff ran and found nothing" -- it says nothing at all.
    assert _detail(after) == ["change,id,detail"]


def test_the_same_two_runs_diff_byte_identically_across_processes(tmp_path: Path) -> None:
    """Two invocations, three hash seeds, three subprocesses. See the helper.

    The fixture is deliberately WIDE: sixteen added clusters, eight removed and
    eight with changed membership. An earlier version diffed the demo against
    itself, which is an empty diff -- and a set of zero or one element has no
    order to lose, so a genuine hash-order dependence planted in the detail
    writer produced identical bytes under every seed and the control read as
    green. One row is always in order.
    """

    before = _write_run(
        tmp_path / "before",
        clusters={f"keep-{i:02d}": [f"m{i}"] for i in range(8)}
        | {f"gone-{i:02d}": [f"g{i}"] for i in range(8)}
        | {f"moved-{i:02d}": [f"x{i}"] for i in range(8)},
    )
    after = _write_run(
        tmp_path / "after",
        clusters={f"keep-{i:02d}": [f"m{i}"] for i in range(8)}
        | {f"new-{i:02d}": [f"n{i}"] for i in range(16)}
        | {f"moved-{i:02d}": [f"x{i}", f"y{i}"] for i in range(8)},
    )
    sanity = compute_diff(before, after)
    assert len(sanity.clusters_added) == 16
    assert len(sanity.clusters_removed) == 8
    assert len(sanity.clusters_membership_changed) == 8

    digests = _digests_across_hash_seeds(
        ["diff-runs", "--before", str(before), "--after", str(after)],
        "--out",
        (RUN_DIFF_FILENAME, RUN_DIFF_DETAIL_FILENAME),
        tmp_path,
    )
    for name, seen in digests.items():
        assert len(seen) == 1, f"{name} differed across hash seeds: {sorted(seen)}"


# -- a diff that could not be computed must not look like an empty one --------


def test_a_missing_run_summary_refuses_by_name_and_writes_nothing(tmp_path: Path) -> None:
    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]})
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"]})
    (after / "run_summary.json").unlink()

    with pytest.raises(RunDiffError) as caught:
        compute_diff(before, after)
    message = str(caught.value)
    assert "run_summary.json" in message
    assert "after run" in message
    assert not (after / RUN_DIFF_FILENAME).exists()
    assert not (after / RUN_DIFF_DETAIL_FILENAME).exists()


def test_an_unparseable_artifact_refuses_rather_than_diffing_nothing(tmp_path: Path) -> None:
    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]})
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"]})
    (before / "run_manifest.json").write_text("{truncated", encoding="utf-8")

    with pytest.raises(RunDiffError, match="run_manifest.json could not be read"):
        compute_diff(before, after)


def test_a_dry_run_directory_refuses_instead_of_reporting_no_clusters(tmp_path: Path) -> None:
    """No ``resolved.csv`` is not an empty cluster set.

    Treated as one, every cluster in the other run would be reported added or
    removed, and a *dry run compared with itself* would report nothing changed
    while the comparison had not happened at all.
    """

    before = _write_run(tmp_path / "before", clusters={"c1": ["a"], "c2": ["b"]})
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"], "c2": ["b"]})
    (after / "resolved.csv").unlink()

    with pytest.raises(RunDiffError) as caught:
        compute_diff(before, after)
    assert "resolved.csv" in str(caught.value)
    assert "not the same as their being unchanged" in str(caught.value)


def test_a_summary_missing_its_withheld_count_refuses_rather_than_reporting_zero(
    tmp_path: Path,
) -> None:
    """Zero withheld is the reassuring reading of a broken summary."""

    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]}, withheld=None)
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"]})

    with pytest.raises(RunDiffError) as caught:
        compute_diff(before, after)
    assert "withheld_no_consent" in str(caught.value)
    assert "will not substitute a zero" in str(caught.value)


def test_a_non_numeric_probability_refuses_rather_than_reading_as_zero(tmp_path: Path) -> None:
    """Zero is the strongest claim that two records are different people."""

    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]})
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"]})
    (after / "review_queue.csv").write_text("left,right,probability\na,b,\n", encoding="utf-8")

    with pytest.raises(RunDiffError, match="an unreadable probability is not a low one"):
        compute_diff(before, after)


def test_no_decisions_file_is_reported_as_such_not_as_zero_invalidations(
    tmp_path: Path,
) -> None:
    """ "Nobody reviewed anything" and "no verdict was invalidated" differ."""

    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]})
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"]})
    diff = compute_diff(before, after)
    assert diff.decisions_present is False
    assert diff.decisions_invalidated == ()
    payload = diff_runs.diff_payload(diff)
    assert payload["decisions_reviewed"] is False
    assert "no reviewed verdict could be checked" in diff_runs.render_diff(diff)

    reviewed_before = _write_run(
        tmp_path / "reviewed", clusters={"c1": ["a"]}, decisions={"approved": [], "rejected": []}
    )
    reviewed = compute_diff(reviewed_before, after)
    assert reviewed.decisions_present is True
    assert diff_runs.diff_payload(reviewed)["decisions_reviewed"] is True


# -- a diff across a configuration change is a wrong answer, not a number -----


def test_a_threshold_change_is_refused_unless_explicitly_allowed(tmp_path: Path) -> None:
    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]})
    after = _write_run(
        tmp_path / "after",
        clusters={"c1": ["a"], "c2": ["b"]},
        thresholds={"prior": 0.01, "auto": 0.90, "review": 0.80},
    )

    with pytest.raises(RunDiffError) as caught:
        compute_diff(before, after)
    assert "not configured the same way" in str(caught.value)
    assert "thresholds" in str(caught.value)
    assert "--allow-recipe-change" in str(caught.value)

    assert _diff_cli(before, after) == 2

    allowed = compute_diff(before, after, allow_recipe_change=True)
    assert allowed.recipe_changed is True
    assert allowed.clusters_added == ("c2",)
    # The configuration change is the FIRST thing rendered, so nobody reads the
    # cluster counts as data drift.
    rendered = diff_runs.render_diff(allowed)
    assert rendered.index("Configuration changed") < rendered.index("| change | count |")
    assert "effects of the configuration and not of the data" in rendered


def test_a_policy_pack_or_recipe_hash_change_is_named(tmp_path: Path) -> None:
    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]})
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"]}, policy_pack="dv")
    with pytest.raises(RunDiffError, match="policy_pack"):
        compute_diff(before, after)

    other = _write_run(tmp_path / "other", clusters={"c1": ["a"]}, recipe_hash="bb" * 32)
    with pytest.raises(RunDiffError, match="recipe_hash"):
        compute_diff(before, other)


def test_a_schema_version_change_is_refused_unconditionally(tmp_path: Path) -> None:
    """Not behind a flag: differing schemas are a category error, not a caveat."""

    bumped = dict(versions())
    bumped["report_schema"] = int(bumped["report_schema"]) + 1
    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]})
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"]}, schema_versions=bumped)

    with pytest.raises(RunDiffError) as caught:
        compute_diff(before, after, allow_recipe_change=True)
    assert "different schema versions" in str(caught.value)
    assert "report_schema" in str(caught.value)


# -- the section a data manager actually needs -------------------------------


def test_a_reviewed_verdict_is_invalidated_when_its_pair_is_gone(tmp_path: Path) -> None:
    before = _write_run(
        tmp_path / "before",
        clusters={"c1": ["a"], "c2": ["b"]},
        review={("a", "b"): 0.9},
        decisions={"approved": [["a", "b"]], "rejected": []},
    )
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"], "c2": ["b"]}, review={})

    diff = compute_diff(before, after)
    assert diff.decisions_invalidated == ((Pair("a", "b"), diff_runs.INVALIDATION_PAIR_ABSENT),)
    assert diff.decisions_present is True


def test_a_reviewed_verdict_is_invalidated_when_its_evidence_moved(tmp_path: Path) -> None:
    """The verdict was given against a probability that is no longer in force."""

    before = _write_run(
        tmp_path / "before",
        clusters={"c1": ["a"], "c2": ["b"]},
        review={("a", "b"): 0.9},
        decisions={"approved": [], "rejected": [["a", "b"]]},
    )
    after = _write_run(
        tmp_path / "after", clusters={"c1": ["a"], "c2": ["b"]}, review={("a", "b"): 0.6}
    )

    diff = compute_diff(before, after)
    assert diff.decisions_invalidated == (
        (Pair("a", "b"), diff_runs.INVALIDATION_EVIDENCE_CHANGED),
    )


def test_an_unmoved_verdict_is_not_invalidated(tmp_path: Path) -> None:
    """The control for the two above: same pair, same probability, no finding."""

    before = _write_run(
        tmp_path / "before",
        clusters={"c1": ["a"], "c2": ["b"]},
        review={("a", "b"): 0.9},
        decisions={"approved": [["a", "b"]], "rejected": []},
    )
    after = _write_run(
        tmp_path / "after", clusters={"c1": ["a"], "c2": ["b"]}, review={("a", "b"): 0.9}
    )
    assert compute_diff(before, after).decisions_invalidated == ()


def test_a_pair_that_moved_into_the_auto_band_is_found_not_reported_absent(
    tmp_path: Path,
) -> None:
    """A decided pair the later run auto-merged is still present, and changed.

    Looking only at the review queue would call this ``pair-absent``, which
    reads as "the run stopped considering these two records" when in fact it
    merged them without asking anyone.
    """

    before = _write_run(
        tmp_path / "before",
        clusters={"c1": ["a"], "c2": ["b"]},
        review={("a", "b"): 0.9},
        decisions={"approved": [["a", "b"]], "rejected": []},
    )
    after = _write_run(
        tmp_path / "after", clusters={"c1": ["a", "b"]}, review={}, auto={("a", "b"): 0.99}
    )

    diff = compute_diff(before, after)
    assert diff.decisions_invalidated == (
        (Pair("a", "b"), diff_runs.INVALIDATION_EVIDENCE_CHANGED),
    )


def test_pairs_entering_and_leaving_the_review_queue_are_both_reported(
    tmp_path: Path,
) -> None:
    before = _write_run(
        tmp_path / "before", clusters={"c1": ["a"]}, review={("a", "b"): 0.9, ("c", "d"): 0.85}
    )
    after = _write_run(
        tmp_path / "after", clusters={"c1": ["a"]}, review={("c", "d"): 0.85, ("e", "f"): 0.82}
    )
    diff = compute_diff(before, after)
    assert diff.review_entered == (Pair("e", "f"),)
    assert diff.review_left == (Pair("a", "b"),)


# -- artifacts and their classification ---------------------------------------


def test_the_detail_file_is_destroyed_and_the_count_file_is_kept(tmp_path: Path) -> None:
    assert RUN_DIFF_DETAIL_FILENAME in PII_ARTIFACTS
    assert RUN_DIFF_FILENAME in NOT_DESTROYED

    demo = _demo(tmp_path)
    before, after = tmp_path / "before", tmp_path / "after"
    _run(demo / "recipe.toml", before)
    _run(demo / "recipe.toml", after)
    assert _diff_cli(before, after) == 0
    assert (after / RUN_DIFF_DETAIL_FILENAME).exists()

    assert main(["destroy", "--out", str(after), "--older-than", "0d"]) == 0
    assert not (after / RUN_DIFF_DETAIL_FILENAME).exists()
    assert (after / RUN_DIFF_FILENAME).exists()


def test_the_dv_pack_suppresses_the_count_summary(tmp_path: Path) -> None:
    """A diff must not become the small-cell disclosure the aggregate export prevents."""

    from constituent_reconciler.policy import policy_for

    before = _write_run(tmp_path / "before", clusters={"c1": ["a"]})
    after = _write_run(tmp_path / "after", clusters={"c1": ["a"], "c2": ["b"], "c3": ["c"]})
    diff = compute_diff(before, after)
    assert diff.clusters_added == ("c2", "c3")

    plain = diff_runs.diff_payload(diff)
    counts = plain["counts"]
    assert isinstance(counts, dict)
    assert counts["clusters_added"] == 2
    suppression = plain["suppression"]
    assert isinstance(suppression, dict)
    assert suppression["applied"] is False

    guarded = diff_runs.diff_payload(diff, policy=policy_for("dv"))
    guarded_counts = guarded["counts"]
    assert isinstance(guarded_counts, dict)
    assert guarded_counts["clusters_added"] == "suppressed"
    # Zeros survive: a true zero is not a small cell and hiding it would say
    # something happened that did not.
    assert guarded_counts["review_entered"] == 0
    guarded_suppression = guarded["suppression"]
    assert isinstance(guarded_suppression, dict)
    assert guarded_suppression["applied"] is True
    # The dv pack's own configured threshold, pinned as a literal so a change to
    # it has to be deliberate rather than followed silently by this assertion.
    assert guarded_suppression["threshold"] == 11


# -- determinism, measured where it can actually fail --------------------------


def _digests_across_hash_seeds(
    argv: list[str], out_flag: str, names: tuple[str, ...], tmp_path: Path
) -> dict[str, set[str]]:
    """Run one command in three subprocesses under different ``PYTHONHASHSEED``.

    Running a command twice inside ONE interpreter proves very little about
    determinism. Python randomizes only ``str``/``bytes`` hashing, and only per
    process, so an artifact whose ordering depends on iterating a set of strings
    comes out identical on both passes of a single-process test and differs
    between two real invocations. Measured on this repository: an in-process
    double-run passes on exactly that defect.

    (Float hashing is seed-independent, so a set of thresholds or probabilities
    would not reproduce it; those are sorted here anyway.)
    """

    digests: dict[str, set[str]] = {name: set() for name in names}
    for seed in ("0", "12345", "99991"):
        target = tmp_path / f"seed-{seed}"
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, test-local paths
            [sys.executable, "-m", "constituent_reconciler.cli", *argv, out_flag, str(target)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
        for name in names:
            digests[name].add(hashlib.sha256((target / name).read_bytes()).hexdigest())
    return digests

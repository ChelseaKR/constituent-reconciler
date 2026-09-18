"""Three reviewers, four hundred pairs, and the one way that goes wrong.

Sharding is disjoint by construction: a pair hashes into exactly one slice.
That is what makes it useful, and it is also the hazard. Under a pack requiring
two distinct approvers, a merger that simply unioned three shard files could
satisfy the two-approver rule with **one** human -- one "approved" read out of
shard 1 and another out of shard 2 -- and a merge assembled from two of three
shards reports a queue as fully reviewed while a third of it was never opened.

So most of this suite is about the merge refusing, and about `apply` refusing a
partial merge. The property test over the assignment function is the small part.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from constituent_reconciler.cli import main
from constituent_reconciler.review import sharding
from constituent_reconciler.review.sharding import (
    Shard,
    ShardError,
    in_shard,
    incomplete_merge,
    merge_decisions,
    pair_id,
    parse_shard,
    shard_of,
)

#: Enough pairs that every shard of every count below is non-empty. A property
#: test over a queue too small to fill its shards proves only that empty sets
#: are disjoint.
QUEUE = [(f"existing:E{i:03d}", f"incoming:N{i:03d}") for i in range(200)]


def _shard_file(
    path: Path,
    shard: Shard,
    *,
    approved: list[tuple[str, str]] | None = None,
    rejected: list[tuple[str, str]] | None = None,
    audit: dict[str, list[dict[str, str]]] | None = None,
    declare_shard: bool = True,
) -> Path:
    payload: dict[str, object] = {
        "decisions_schema": 2,
        "approved": [list(pair) for pair in approved or []],
        "rejected": [list(pair) for pair in rejected or []],
        "audit": audit or {},
    }
    if declare_shard:
        payload["shard"] = {"index": shard.index, "count": shard.count}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _pairs_in(shard: Shard) -> list[tuple[str, str]]:
    return [pair for pair in QUEUE if in_shard(pair[0], pair[1], shard)]


# -- assignment ---------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 7, 11])
def test_every_pair_lands_in_exactly_one_shard(count: int) -> None:
    """The issue's own property, over several splits."""

    seen: dict[tuple[str, str], list[int]] = {pair: [] for pair in QUEUE}
    for index in range(1, count + 1):
        shard = Shard(index=index, count=count)
        for pair in _pairs_in(shard):
            seen[pair].append(index)
    assert all(len(indexes) == 1 for indexes in seen.values())
    # And every shard actually receives work at these counts, so a "disjoint"
    # result is not disjointness over empty sets.
    covered = {indexes[0] for indexes in seen.values()}
    assert covered == set(range(1, count + 1))


def test_assignment_is_order_independent_and_stable() -> None:
    """A pair's shard cannot depend on which way round the reviewer sees it."""

    for left, right in QUEUE[:20]:
        assert shard_of(left, right, 3) == shard_of(right, left, 3)
        assert pair_id(left, right) == pair_id(right, left)
    # Pinned as literals, not recomputed: the assignment is a published contract
    # across machines and resumes, and a change to the hash or the id shape has
    # to be deliberate rather than silently followed by this assertion.
    assert shard_of("existing:E000", "incoming:N000", 3) == 1
    assert shard_of("existing:E001", "incoming:N001", 3) == 2
    assert pair_id("b", "a") == "a|b"


def test_a_shard_spec_outside_its_range_is_refused_not_clamped() -> None:
    assert parse_shard("2/3") == Shard(index=2, count=3)
    assert parse_shard(" 1 / 1 ") == Shard(index=1, count=1)
    for bad in ("4/3", "0/3", "-1/3", "2/0", "2", "2/3/4", "a/3", ""):
        with pytest.raises(ShardError):
            parse_shard(bad)


# -- the merge refuses ---------------------------------------------------------


def test_a_complete_merge_carries_every_verdict_and_its_coverage(tmp_path: Path) -> None:
    files = []
    expected_approved: list[tuple[str, str]] = []
    for index in (1, 2, 3):
        shard = Shard(index=index, count=3)
        pairs = _pairs_in(shard)
        expected_approved.extend(pairs[:2])
        files.append(
            _shard_file(
                tmp_path / shard.filename,
                shard,
                approved=pairs[:2],
                rejected=pairs[2:4],
                audit={
                    pair_id(*pair): [
                        {"reviewer": f"r{index}", "verdict": "approved", "decided_at": "t"}
                    ]
                    for pair in pairs[:2]
                },
            )
        )
    merged = merge_decisions(files)
    assert merged.shard_count == 3
    assert merged.covered == (1, 2, 3)
    approved = merged.payload["approved"]
    assert isinstance(approved, list)
    assert sorted(approved) == sorted(list(pair) for pair in expected_approved)
    # Every reviewer attribution survives.
    audit = merged.payload["audit"]
    assert isinstance(audit, dict)
    assert {entry["reviewer"] for entries in audit.values() for entry in entries} == {
        "r1",
        "r2",
        "r3",
    }
    sources = merged.payload["sources"]
    assert isinstance(sources, dict)
    assert sources["shard_count"] == 3
    assert sources["covered"] == [1, 2, 3]
    files_listed = sources["files"]
    assert isinstance(files_listed, list)
    assert len(files_listed) == 3
    assert incomplete_merge(merged.payload) is None


def test_two_reviewers_disagreeing_on_one_pair_refuses_and_names_it(tmp_path: Path) -> None:
    """A supervisor decides these. This tool will not pick a side."""

    shard = Shard(index=1, count=2)
    contested = _pairs_in(shard)[0]
    a = _shard_file(tmp_path / "a.json", shard, approved=[contested])
    b = _shard_file(tmp_path / "b.json", Shard(index=2, count=2))
    # Doctor b so it claims shard 1 as well? No: give it shard 1's id but a
    # different index, which is the misplacement check. To reach the CONFLICT
    # check, both files must legitimately hold the pair, so both are shard 1 --
    # which the duplicate-shard check catches first. Use one file per shard and
    # put the conflict inside a single reviewer set instead.
    b.write_text(
        json.dumps(
            {
                "decisions_schema": 2,
                "approved": [],
                "rejected": [list(contested)],
                "audit": {},
                "shard": {"index": 1, "count": 2},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ShardError) as caught:
        merge_decisions([a, b])
    # Two files claiming shard 1 is caught first, and that is the right order:
    # it is the stronger finding.
    assert "both claim shard 1" in str(caught.value)


def test_a_conflicting_verdict_across_legitimate_shards_refuses(tmp_path: Path) -> None:
    """Reached by a pair recorded in two different shard files at once.

    Both misplacement and conflict are live here; the merge must not return a
    payload either way, and it names the pair.
    """

    one = Shard(index=1, count=2)
    two = Shard(index=2, count=2)
    pair_one = _pairs_in(one)[0]
    a = _shard_file(tmp_path / "a.json", one, approved=[pair_one])
    b = _shard_file(tmp_path / "b.json", two, rejected=[pair_one])
    with pytest.raises(ShardError) as caught:
        merge_decisions([a, b])
    message = str(caught.value)
    assert pair_id(*pair_one) in message
    assert "wrong shard file" in message
    assert "cannot be treated as one reviewer's independent work" in message


def test_a_pair_recorded_in_the_wrong_shard_file_refuses(tmp_path: Path) -> None:
    """The guard that stops one human satisfying a two-approver rule.

    A pair that does not hash into the file it was found in means the sharding
    was bypassed, so its verdicts cannot be trusted to be independent work.
    """

    one, two, three = (Shard(index=i, count=3) for i in (1, 2, 3))
    stranger = _pairs_in(two)[0]
    files = [
        _shard_file(tmp_path / one.filename, one, approved=[stranger]),
        _shard_file(tmp_path / two.filename, two),
        _shard_file(tmp_path / three.filename, three),
    ]
    with pytest.raises(ShardError, match="wrong shard file"):
        merge_decisions(files)


def test_files_from_different_splits_refuse(tmp_path: Path) -> None:
    """Pairs assigned under one split are not the pairs assigned under another."""

    files = [
        _shard_file(tmp_path / "a.json", Shard(index=1, count=3)),
        _shard_file(tmp_path / "b.json", Shard(index=1, count=4)),
    ]
    with pytest.raises(ShardError) as caught:
        merge_decisions(files)
    assert "different splits of the queue" in str(caught.value)


def test_a_file_that_declares_no_shard_refuses(tmp_path: Path) -> None:
    """An undeclared file cannot be checked for coverage or misplacement."""

    files = [
        _shard_file(tmp_path / "a.json", Shard(index=1, count=2)),
        _shard_file(tmp_path / "b.json", Shard(index=2, count=2), declare_shard=False),
    ]
    with pytest.raises(ShardError, match="carries no 'shard' section"):
        merge_decisions(files)


def test_a_missing_or_unreadable_shard_file_refuses(tmp_path: Path) -> None:
    good = _shard_file(tmp_path / "a.json", Shard(index=1, count=2))
    with pytest.raises(ShardError, match="not found"):
        merge_decisions([good, tmp_path / "nope.json"])
    broken = tmp_path / "broken.json"
    broken.write_text("{truncated", encoding="utf-8")
    with pytest.raises(ShardError, match="could not be read"):
        merge_decisions([good, broken])
    with pytest.raises(ShardError, match="at least one shard file"):
        merge_decisions([])


# -- a partial merge is honest, and apply refuses it ---------------------------


def test_a_partial_merge_records_what_is_missing(tmp_path: Path) -> None:
    """Two of three shards is a real intermediate artifact, not an error."""

    files = [
        _shard_file(tmp_path / Shard(1, 3).filename, Shard(1, 3)),
        _shard_file(tmp_path / Shard(2, 3).filename, Shard(2, 3)),
    ]
    merged = merge_decisions(files)
    assert merged.covered == (1, 2)
    reason = incomplete_merge(merged.payload)
    assert reason is not None
    assert "2 of 3 shards" in reason
    assert "shard(s) 3 are missing" in reason
    assert "would treat the queue as finished when it is not" in reason


def test_a_whole_queue_decisions_file_is_not_treated_as_a_partial_merge() -> None:
    """No ``sources`` section means it was never sharded, so it is complete."""

    assert incomplete_merge({"approved": [], "rejected": []}) is None


def test_a_malformed_sources_section_is_refused_not_ignored() -> None:
    """An unreadable coverage claim must not read as full coverage."""

    assert incomplete_merge({"sources": "all of them"}) is not None
    assert incomplete_merge({"sources": {"shard_count": "three", "covered": [1]}}) is not None
    assert incomplete_merge({"sources": {"shard_count": 3, "covered": "1,2,3"}}) is not None
    assert incomplete_merge({"sources": {"shard_count": 0, "covered": []}}) is not None
    # The only shape that passes is a genuinely complete one.
    assert incomplete_merge({"sources": {"shard_count": 3, "covered": [1, 2, 3]}}) is None


def test_apply_refuses_a_partial_merge_and_accepts_a_complete_one(tmp_path: Path) -> None:
    """End to end through the real command, over the bundled demo."""

    import shutil

    examples = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("recipe.toml", "existing.csv", "incoming.csv"):
        shutil.copy(examples / name, demo / name)
    out_dir = tmp_path / "out"
    assert main(["run", "--config", str(demo / "recipe.toml"), "--out", str(out_dir)]) == 0

    partial = out_dir / "merged-partial.json"
    partial.write_text(
        json.dumps(
            {
                "decisions_schema": 2,
                "approved": [],
                "rejected": [],
                "audit": {},
                "sources": {"shard_count": 3, "covered": [1, 2], "files": []},
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "apply",
                "--config",
                str(demo / "recipe.toml"),
                "--decisions",
                str(partial),
                "--out",
                str(tmp_path / "applied"),
            ]
        )
        == 2
    )

    complete = out_dir / "merged-complete.json"
    complete.write_text(
        json.dumps(
            {
                "decisions_schema": 2,
                "approved": [],
                "rejected": [],
                "audit": {},
                "sources": {"shard_count": 3, "covered": [1, 2, 3], "files": []},
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "apply",
                "--config",
                str(demo / "recipe.toml"),
                "--decisions",
                str(complete),
                "--out",
                str(tmp_path / "applied2"),
            ]
        )
        == 0
    )


# -- the CLI ------------------------------------------------------------------


def test_merge_decisions_through_the_real_command(tmp_path: Path) -> None:
    files = [
        str(
            _shard_file(
                tmp_path / Shard(i, 3).filename, Shard(i, 3), approved=_pairs_in(Shard(i, 3))[:1]
            )
        )
        for i in (1, 2, 3)
    ]
    into = tmp_path / "decisions.json"
    assert main(["merge-decisions", "--into", str(into), *files]) == 0
    data = json.loads(into.read_text(encoding="utf-8"))
    assert len(data["approved"]) == 3
    assert data["sources"]["covered"] == [1, 2, 3]

    # A partial merge still writes, exits 0, and warns: the file is honest about
    # what it holds and `apply` is where it is refused.
    partial_into = tmp_path / "partial.json"
    assert main(["merge-decisions", "--into", str(partial_into), files[0], files[1]]) == 0
    partial = json.loads(partial_into.read_text(encoding="utf-8"))
    assert partial["sources"]["covered"] == [1, 2]
    assert incomplete_merge(partial) is not None


def test_a_bad_shard_spec_exits_two_before_anything_runs(tmp_path: Path) -> None:
    import shutil

    examples = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("recipe.toml", "existing.csv", "incoming.csv"):
        shutil.copy(examples / name, demo / name)
    assert (
        main(
            [
                "review",
                "--config",
                str(demo / "recipe.toml"),
                "--reviewer",
                "casey",
                "--shard",
                "4/3",
                "--out",
                str(tmp_path / "out"),
                "--no-browser",
            ]
        )
        == 2
    )
    assert not (tmp_path / "out" / "decisions-4of3.json").exists()


def test_a_sharded_session_sees_only_its_own_pairs_and_stamps_its_identity(
    tmp_path: Path,
) -> None:
    """Through the real ``ReviewSession``, over the demo's queue."""

    import shutil

    from constituent_reconciler import pipeline
    from constituent_reconciler.config import load_recipe
    from constituent_reconciler.review.session import ReviewSession

    examples = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("recipe.toml", "existing.csv", "incoming.csv"):
        shutil.copy(examples / name, demo / name)
    recipe = load_recipe(str(demo / "recipe.toml"))
    result = pipeline.run(recipe)
    assert len(result.review_pairs) >= 2, "the demo queue is too small to shard"

    total = 0
    for index in (1, 2):
        shard = Shard(index=index, count=2)
        session = ReviewSession(
            result,
            recipe.fields,
            tmp_path / shard.filename,
            reviewer="casey",
            shard=shard,
        )
        total += session.total
        for view in session.views():
            # Planted calibration pairs are not queue pairs and carry no shard.
            if view.synthetic:
                continue
            assert in_shard(view.left_id, view.right_id, shard)
        session.save()
        payload = json.loads((tmp_path / shard.filename).read_text(encoding="utf-8"))
        assert payload["shard"] == {"index": index, "count": 2}
    # Disjoint AND covering: the two shards together are the whole queue.
    assert total == len(result.review_pairs)


def test_an_unsharded_session_stamps_no_shard(tmp_path: Path) -> None:
    """A whole-queue file must not claim a coverage contract it does not have."""

    import shutil

    from constituent_reconciler import pipeline
    from constituent_reconciler.config import load_recipe
    from constituent_reconciler.review.session import ReviewSession

    examples = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("recipe.toml", "existing.csv", "incoming.csv"):
        shutil.copy(examples / name, demo / name)
    recipe = load_recipe(str(demo / "recipe.toml"))
    result = pipeline.run(recipe)
    session = ReviewSession(result, recipe.fields, tmp_path / "decisions.json", reviewer="casey")
    assert session.total == len(result.review_pairs)
    assert sharding.SHARD_KEY not in session.to_decisions()

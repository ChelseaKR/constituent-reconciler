"""The merges nobody reviewed have to leave evidence too.

`decisions.json` records who decided each pair a *person* saw, and it survives
`destroy` because it is audit evidence carrying no field values. The pairs the
matcher merged on its own had no such record. `resolved.csv` named a cluster's
members and `provenance.jsonl` named the field-level lineage, but nothing said
at what probability, in which band, or against which thresholds the members
were joined -- and `review_queue.csv` carries a probability only for pairs that
fell *below* the auto threshold.

So the merges a human checked were explainable afterwards and the merges nobody
checked were not. Measured on origin/main against the intake demo: six clusters
formed automatically, and the only surviving record of any of them was a
`members` list.

That is the gap `auto_merges.json` closes, and it is the artifact the offline
auditor's trace in #146 has to read.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.config import load_recipe
from constituent_reconciler.destruction import NOT_DESTROYED, PII_ARTIFACTS
from constituent_reconciler.models import Band, RunResult

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


def _run_and_export(
    tmp_path: Path, *, auto_threshold: float | None = None
) -> tuple[RunResult, pipeline.ExportSummary, dict[str, Any]]:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    if auto_threshold is not None:
        recipe = replace(recipe, auto_threshold=auto_threshold)
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    payload: dict[str, Any] = json.loads(
        (tmp_path / "auto_merges.json").read_text(encoding="utf-8")
    )
    return result, summary, payload


def test_every_automatic_merge_is_recorded_with_the_evidence_that_caused_it(
    tmp_path: Path,
) -> None:
    result, summary, payload = _run_and_export(tmp_path)

    recorded = {frozenset((row["left"], row["right"])) for row in payload["pairs"]}
    assert recorded == {pair.key() for pair in result.auto_pairs}
    assert payload["pair_count"] == len(result.auto_pairs) == len(payload["pairs"])
    assert summary.auto_merges_path == tmp_path / "auto_merges.json"

    # The evidence, not just the fact. Each row carries the number that decided
    # it, and the thresholds it was decided against.
    for row in payload["pairs"]:
        assert row["band"] == Band.AUTO.value
        assert isinstance(row["probability"], float)
        assert row["probability"] >= payload["auto_threshold"]
    assert payload["auto_threshold"] == pytest.approx(0.97)
    assert payload["review_threshold"] == pytest.approx(0.80)


def test_review_band_pairs_are_not_in_it(tmp_path: Path) -> None:
    """They belong to `review_queue.csv` and `decisions.json`, which name a person."""

    result, _, payload = _run_and_export(tmp_path)

    recorded = {frozenset((row["left"], row["right"])) for row in payload["pairs"]}
    review = {pair.key() for pair in result.review_pairs}
    assert review
    assert recorded.isdisjoint(review)


def test_a_run_that_merged_nothing_still_writes_the_file(tmp_path: Path) -> None:
    """ "No automatic merges" and "no record of them" must not look the same.

    An absent file cannot distinguish them, so the writer runs unconditionally
    and the empty case is written as an explicit zero.
    """

    result, _, payload = _run_and_export(tmp_path, auto_threshold=1.01)

    assert result.auto_pairs == ()
    assert (tmp_path / "auto_merges.json").is_file()
    assert payload["pair_count"] == 0
    assert payload["pairs"] == []


def test_it_carries_no_field_value(tmp_path: Path) -> None:
    """A planted-sentinel control, not an inspection of the keys we happened to write.

    Every raw field value from every input record is searched for in the
    rendered bytes. This is what keeps the artifact on `NOT_DESTROYED`
    honest: it is exempt from destruction only because it holds no personal
    data, so that claim is measured rather than asserted.
    """

    result, _, _ = _run_and_export(tmp_path)
    rendered = (tmp_path / "auto_merges.json").read_text(encoding="utf-8")

    values = {
        value.strip()
        for record in result.records.values()
        for value in record.raw.values()
        if value and value.strip()
    }
    assert values, "the fixture must actually carry field values to search for"
    leaked = sorted(value for value in values if value in rendered)
    assert leaked == []


def test_rows_are_ordered_the_way_the_review_queue_is(tmp_path: Path) -> None:
    """Descending probability, then ids -- the same rule `review_queue.csv` uses.

    Added because the byte-identity test below could not see this. Reversing
    the sort key left every other test in this file green: two runs of the same
    reversed code still agree with each other. Determinism and *the documented
    order* are separate claims, and the writer's docstring makes both, so both
    are asserted.

    The order is not cosmetic. It is what lets an auditor read the strongest
    evidence first, and what will let a future run-to-run diff line the two
    files up without re-sorting them.
    """

    _, _, payload = _run_and_export(tmp_path)

    rows = payload["pairs"]
    assert len(rows) > 1, "the fixture must produce enough rows for order to mean something"
    keys = [(-row["probability"], row["left"], row["right"]) for row in rows]
    assert keys == sorted(keys)


def test_it_is_byte_identical_across_runs(tmp_path: Path) -> None:
    _run_and_export(tmp_path / "a")
    _run_and_export(tmp_path / "b")

    first = (tmp_path / "a" / "auto_merges.json").read_bytes()
    second = (tmp_path / "b" / "auto_merges.json").read_bytes()
    assert first == second


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)

    summary = pipeline.export(result, recipe, out_dir=tmp_path, dry_run=True)

    assert summary.auto_merges_path is None
    assert not (tmp_path / "auto_merges.json").exists()


def test_it_is_classified_as_audit_evidence_that_destruction_keeps() -> None:
    """The classification is the point, so it is asserted rather than assumed.

    `tests/test_destruction_inventory.py` proves the name is on exactly one of
    the two lists. Which one it is on is a judgement, and this is where that
    judgement is written down: the file is the counterpart to `decisions.json`,
    and destroying it would remove the only evidence of why an automatic merge
    happened without removing anybody's personal data.
    """

    assert "auto_merges.json" in NOT_DESTROYED
    assert "auto_merges.json" not in PII_ARTIFACTS
    assert "decisions.json" in NOT_DESTROYED

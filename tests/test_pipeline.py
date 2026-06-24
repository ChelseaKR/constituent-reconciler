from __future__ import annotations

import json
from pathlib import Path

from constituent_reconciler import pipeline
from constituent_reconciler.config import load_recipe
from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.evaluate import evaluate

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"

EXPECTED_AUTO = {
    frozenset(("E003", "N002")),
    frozenset(("E005", "N006")),
    frozenset(("E007", "N005")),
    frozenset(("E010", "N008")),
    frozenset(("E012", "N010")),
    frozenset(("N003", "N011")),
}


def test_pipeline_auto_merges_and_routes_lookalikes_to_review() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    auto = {pair.key() for pair in result.auto_pairs}
    review = {pair.key() for pair in result.review_pairs}
    assert auto == EXPECTED_AUTO
    # A real duplicate with a DOB typo, and a genuine non-duplicate with the same
    # name, both land in review rather than being auto-merged.
    assert frozenset(("E002", "N004")) in review
    assert frozenset(("E008", "N007")) in review


def test_pipeline_eval_gate_is_clean_on_fixtures() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    truth = json.loads((EXAMPLES / "ground_truth.json").read_text(encoding="utf-8"))["clusters"]
    report = evaluate(result.pairs, truth, n_records=len(result.records))
    assert report.n_records == 27
    assert report.n_true_pairs == 7
    assert report.n_auto == 6
    assert report.false_merges == 0
    assert report.false_merge_rate == 0.0
    assert report.missed == 0
    assert report.recall_coverage == 1.0
    assert report.blocking_misses == 0


def test_dv_policy_pack_withholds_revoked_record() -> None:
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    assert recipe.require_consent is True
    result = pipeline.run(recipe)
    _, withheld = partition_by_consent(result.golden, require_consent=recipe.require_consent)
    withheld_members = {member for record in withheld for member in record.members}
    assert "N009" in withheld_members


def test_apply_approved_review_pair_merges_cluster() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    base = pipeline.run(recipe)
    applied = pipeline.run(recipe, force_auto=[frozenset(("E002", "N004"))])
    assert len(applied.clusters) == len(base.clusters) - 1
    merged = [c for c in applied.clusters if set(c.members) == {"E002", "N004"}]
    assert merged


def test_run_writes_outputs(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    paths = pipeline.write_outputs(result, recipe, tmp_path)
    assert paths["resolved"].exists()
    assert paths["review_queue"].exists()
    resolved_text = paths["resolved"].read_text(encoding="utf-8")
    # 27 records minus 6 merges leaves 21 resolved rows plus the header.
    assert len(resolved_text.strip().splitlines()) == 22

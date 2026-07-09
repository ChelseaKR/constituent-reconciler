"""Tests for the synthetic corpus generator (FIX-11).

Kept fast: the largest generated corpus here is a few hundred records, well
under the 10^3-10^5 range the tool targets in real use, so the suite stays in
the seconds the fast CI gate expects. Determinism, ground-truth correctness,
and each error channel's shape are what matter for a unit test; matcher
performance at scale is exercised manually via `make eval-large`.
"""

from __future__ import annotations

import random

import pytest
from tools.corpusgen import errors, pools
from tools.corpusgen.generate import generate, write_corpus

from constituent_reconciler import pipeline
from constituent_reconciler.address import normalize_address_deterministic
from constituent_reconciler.config import load_recipe
from constituent_reconciler.evaluate import evaluate
from constituent_reconciler.normalize import normalize_dob


def test_generate_is_deterministic() -> None:
    first = generate(total_records=300, seed=123)
    second = generate(total_records=300, seed=123)
    assert first.existing_rows == second.existing_rows
    assert first.incoming_rows == second.incoming_rows
    assert first.clusters == second.clusters
    assert first.labels == second.labels


def test_generate_different_seed_differs() -> None:
    first = generate(total_records=300, seed=1)
    second = generate(total_records=300, seed=2)
    assert first.existing_rows != second.existing_rows


def test_generate_record_count_near_target() -> None:
    corpus = generate(total_records=2000, seed=7)
    total = len(corpus.existing_rows) + len(corpus.incoming_rows)
    # n_identities is rounded and singleton/duplicate/decoy counts are
    # probabilistic, so allow a reasonable band around the request rather than
    # an exact match.
    assert 1500 <= total <= 2500


def test_generate_rejects_bad_rates() -> None:
    with pytest.raises(ValueError, match="duplicate_rate"):
        generate(total_records=1000, seed=1, duplicate_rate=0.7, decoy_rate=0.4)
    with pytest.raises(ValueError, match="total_records"):
        generate(total_records=1, seed=1)


def test_ground_truth_clusters_are_exactly_the_duplicate_labels() -> None:
    corpus = generate(total_records=1000, seed=5)
    cluster_pairs = {frozenset(pair) for pair in corpus.clusters}
    duplicate_label_pairs = {
        frozenset((label["existing_id"], label["incoming_id"]))
        for label in corpus.labels
        if label["kind"] == "duplicate"
    }
    assert cluster_pairs == duplicate_label_pairs
    assert len(corpus.clusters) == len(cluster_pairs)  # no accidental duplicates


def test_decoy_pairs_are_not_in_ground_truth() -> None:
    corpus = generate(total_records=1000, seed=5)
    cluster_pairs = {frozenset(pair) for pair in corpus.clusters}
    decoy_pairs = {
        frozenset((label["existing_id"], label["incoming_id"]))
        for label in corpus.labels
        if label["kind"] == "decoy"
    }
    assert decoy_pairs, "expected at least one planted decoy at this scale"
    assert cluster_pairs.isdisjoint(decoy_pairs)


def test_decoy_pairs_share_name_but_differ_in_dob() -> None:
    corpus = generate(total_records=1000, seed=5)
    by_id = {row["id"]: row for row in corpus.existing_rows + corpus.incoming_rows}
    decoys = [label for label in corpus.labels if label["kind"] == "decoy"]
    assert decoys
    for label in decoys:
        left = by_id[label["existing_id"]]
        right = by_id[label["incoming_id"]]
        assert left["First Name"] == right["First Name"]
        assert left["Last Name"] == right["Last Name"]
        assert left["DOB"] != right["DOB"]


def test_every_id_appears_exactly_once() -> None:
    corpus = generate(total_records=1000, seed=9)
    existing_ids = [row["id"] for row in corpus.existing_rows]
    incoming_ids = [row["id"] for row in corpus.incoming_rows]
    assert len(existing_ids) == len(set(existing_ids))
    assert len(incoming_ids) == len(set(incoming_ids))
    assert set(existing_ids).isdisjoint(incoming_ids)  # E-prefixed vs N-prefixed


def test_write_corpus_produces_a_loadable_recipe(tmp_path) -> None:  # type: ignore[no-untyped-def]
    corpus = generate(total_records=500, seed=11)
    out_dir = tmp_path / "corpus"
    write_corpus(corpus, out_dir, seed=11, total_records=500)

    assert (out_dir / "existing.csv").exists()
    assert (out_dir / "incoming.csv").exists()
    assert (out_dir / "ground_truth.json").exists()
    assert (out_dir / "labels.json").exists()

    recipe = load_recipe(out_dir / "recipe.toml")
    assert recipe.fields  # mapping parsed and active fields populated
    result = pipeline.run(recipe)
    assert len(result.records) == len(corpus.existing_rows) + len(corpus.incoming_rows)


def test_generated_corpus_scores_well_on_the_matcher(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """End-to-end smoke test: a small generated corpus should mostly resolve.

    Not a tight bound (this is a probabilistic matcher over synthetic noise);
    just a floor that would catch a generator or wiring regression.
    """

    corpus = generate(total_records=600, seed=2026)
    out_dir = tmp_path / "corpus"
    write_corpus(corpus, out_dir, seed=2026, total_records=600)
    recipe = load_recipe(out_dir / "recipe.toml")
    result = pipeline.run(recipe)

    import json

    truth = json.loads((out_dir / "ground_truth.json").read_text(encoding="utf-8"))
    report = evaluate(result.pairs, truth["clusters"], n_records=len(result.records))

    assert report.n_true_pairs > 0
    assert report.false_merge_rate <= 0.10
    assert report.recall_coverage >= 0.85


# --- error-channel unit tests --------------------------------------------


def test_typo_name_preserves_first_and_last_character() -> None:
    rng = random.Random(1)
    for _ in range(20):
        result = errors.typo_name(rng, "Jonathan")
        assert result[0] == "J"
        assert result[-1] == "n"


def test_typo_name_leaves_short_names_alone() -> None:
    rng = random.Random(1)
    assert errors.typo_name(rng, "Al") == "Al"


def test_nickname_table_lookup() -> None:
    rng = random.Random(1)
    assert errors.nickname(rng, "Robert", pools.NICKNAMES) in {"Bob", "Rob", "Bobby"}
    assert errors.nickname(rng, "Zzyzx", pools.NICKNAMES) is None


def test_transliteration_table_lookup() -> None:
    rng = random.Random(1)
    result = errors.transliteration(rng, "Mohammed", pools.TRANSLITERATIONS)
    assert result in {"Muhammad", "Mohamed", "Muhammed"}


def test_date_format_drift_round_trips_through_normalize() -> None:
    rng = random.Random(3)
    for _ in range(20):
        drifted = errors.date_format_drift(rng, "1990-04-12")
        assert normalize_dob(drifted) == "1990-04-12"


def test_dob_typo_changes_the_date_but_stays_valid_iso() -> None:
    rng = random.Random(4)
    drifted = errors.dob_typo(rng, "1990-04-12")
    assert drifted != "1990-04-12"
    assert normalize_dob(drifted) == drifted  # already ISO, round-trips as-is


def test_address_variant_normalizes_to_the_same_key() -> None:
    rng = random.Random(5)
    canonical = "123 North Maple Street Apartment 4, Rivertown, OH 43210"
    variant = errors.address_variant(rng, canonical)
    assert normalize_address_deterministic(variant) == normalize_address_deterministic(canonical)

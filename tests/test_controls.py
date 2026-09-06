"""The negative controls, and negative controls on the negative controls.

A control that cannot fail is worse than no control: it is a second gate
reporting success without examining anything, stacked on the first. So every
test here that asserts a control passes on the real pipeline has a sibling that
sabotages the pipeline and asserts the same control fails.

The sabotage used throughout is the one the issue named: replace the scorer with
a constant. Two constants matter, and they fail different controls, which is why
there is more than one control:

* ``1.0`` -- everything auto-merges. The headline false-merge rate goes to 98%,
  so the existing gate already catches this one.
* ``0.9`` -- above the review threshold, below the auto threshold. Nothing
  auto-merges, so the gated metric is ``0/0``, which the report renders as
  **0.0%** and the gate reads as a **PASS**. The matcher is completely broken
  and every existing gate is green. The identity control is what catches it.

That second case is the whole argument for this module.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

from constituent_reconciler import controls as controls_module
from constituent_reconciler import decisions, matching, pipeline
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.controls import (
    ConstantScoreBackend,
    ControlsReport,
    identity_control,
    null_matcher_control,
    run_controls,
    shuffled_labels_control,
)
from constituent_reconciler.evaluate import evaluate
from constituent_reconciler.matching.base import MatcherBackend
from constituent_reconciler.models import Band, Pair, Record, RunResult
from constituent_reconciler.report import render_eval_markdown

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


@pytest.fixture(scope="module")
def recipe() -> Recipe:
    return load_recipe(str(EXAMPLES / "recipe.toml"))


@pytest.fixture(scope="module")
def truth_clusters() -> list[list[str]]:
    payload = json.loads((EXAMPLES / "ground_truth.json").read_text(encoding="utf-8"))
    clusters = payload["clusters"]
    assert isinstance(clusters, list)
    return [list(cluster) for cluster in clusters]


@pytest.fixture(scope="module")
def real_run(recipe: Recipe) -> RunResult:
    return pipeline.run(recipe)


def _controls(result: RunResult, recipe: Recipe, clusters: list[list[str]]) -> ControlsReport:
    return run_controls(
        result.records,
        result.pairs,
        clusters,
        recipe.fields,
        prior=recipe.prior,
        auto_threshold=recipe.auto_threshold,
        review_threshold=recipe.review_threshold,
    )


def _sabotaged_run(monkeypatch: pytest.MonkeyPatch, recipe: Recipe, constant: float) -> RunResult:
    """Run the pipeline with every pair scored at ``constant``.

    Asserts the sabotage actually reached the pipeline. A sabotage that silently
    no-ops reads exactly like a pass, so the mutation is verified before any
    conclusion is drawn from it.
    """

    backend = ConstantScoreBackend(constant)
    monkeypatch.setattr(matching, "_default_backend", backend)
    assert matching.default_backend() is backend
    result = pipeline.run(recipe)
    observed = {pair.probability for pair in result.pairs}
    assert observed == {constant}, (
        f"the sabotage did not reach the pipeline: scores were {observed}, not {{{constant}}}"
    )
    return result


# --- the backend itself -------------------------------------------------------


def test_the_constant_backend_satisfies_the_matcher_protocol() -> None:
    assert isinstance(ConstantScoreBackend(0.5), MatcherBackend)


def test_the_constant_backend_honors_the_result_contract() -> None:
    records = [Record(unique_id=f"r{i}", source="s", raw={}) for i in range(4)]
    rows = ConstantScoreBackend(0.5).score_pairs(records, ("first_name",), prior=0.1, floor=0.001)
    assert len(rows) == 6
    assert all(left < right for left, right, _ in rows)
    assert rows == sorted(rows, key=lambda row: (-row[2], row[0], row[1]))
    assert (
        ConstantScoreBackend(0.5).score_pairs(records[:1], ("first_name",), prior=0.1, floor=0.001)
        == []
    )
    assert (
        ConstantScoreBackend(0.0005).score_pairs(records, ("first_name",), prior=0.1, floor=0.001)
        == []
    )


def test_the_constant_backend_restricted_to_candidates_emits_only_those() -> None:
    records = [Record(unique_id=f"r{i}", source="s", raw={}) for i in range(4)]
    only = {frozenset(("r0", "r1")), frozenset(("r2", "r3"))}
    rows = ConstantScoreBackend(0.5, candidates=only).score_pairs(
        records, ("first_name",), prior=0.1, floor=0.001
    )
    assert {frozenset((left, right)) for left, right, _ in rows} == only


# --- the controls pass on the real pipeline -----------------------------------


def test_every_control_passes_on_the_real_pipeline(
    real_run: RunResult, recipe: Recipe, truth_clusters: list[list[str]]
) -> None:
    controls = _controls(real_run, recipe, truth_clusters)
    failures = [outcome.name for outcome in controls.outcomes if not outcome.passed]
    assert not failures, f"controls failed on an unmodified pipeline: {failures}"
    assert controls.passed


def test_the_controls_are_byte_identical_under_the_same_seed(
    real_run: RunResult, recipe: Recipe, truth_clusters: list[list[str]]
) -> None:
    first = _controls(real_run, recipe, truth_clusters)
    second = _controls(real_run, recipe, truth_clusters)
    assert first == second


def test_an_empty_control_set_is_not_a_pass() -> None:
    """`all([])` is True. A report with no controls must not read as controlled."""

    assert not ControlsReport(seed=1, outcomes=()).passed


# --- negative controls: each control fails when its premise is broken ---------


def test_the_identity_control_fails_when_the_scorer_cannot_auto_merge_a_twin(
    monkeypatch: pytest.MonkeyPatch, recipe: Recipe, truth_clusters: list[list[str]]
) -> None:
    """The case every existing gate misses.

    A constant 0.9 is above the review threshold and below the auto threshold,
    so nothing auto-merges, the gated false-merge rate is ``0/0``, and the
    report renders **0.0%** and calls the gate a PASS on a matcher that has been
    replaced by a constant.
    """

    result = _sabotaged_run(monkeypatch, recipe, 0.9)

    report = evaluate(result.pairs, truth_clusters, n_records=len(result.records))
    assert report.n_auto == 0
    assert report.false_merge_rate == 0.0, "the premise of this test: the headline gate is green"

    controls = _controls(result, recipe, truth_clusters)
    identity = next(o for o in controls.outcomes if o.name == "identity")
    assert not identity.passed, "the identity control did not catch a scorer that never fires"
    assert "recall 0.0000" in identity.observed
    assert not controls.passed


def test_the_null_matcher_control_fails_when_the_real_scorer_is_already_constant(
    monkeypatch: pytest.MonkeyPatch, recipe: Recipe, truth_clusters: list[list[str]]
) -> None:
    """A constant-1.0 scorer cannot be made worse by rescoring at the threshold.

    The at-auto control asserts that forcing every candidate to auto-merge
    *raises* the false-merge rate. When the real scorer already auto-merges
    everything there is no headroom, and the control says so instead of
    reporting a movement that did not happen.
    """

    result = _sabotaged_run(monkeypatch, recipe, 1.0)
    controls = _controls(result, recipe, truth_clusters)
    at_auto = next(o for o in controls.outcomes if o.name == "null-matcher (at auto threshold)")
    assert not at_auto.passed
    assert not controls.passed


def test_the_headline_still_renders_as_measured_while_a_control_fails(
    monkeypatch: pytest.MonkeyPatch, recipe: Recipe, truth_clusters: list[list[str]]
) -> None:
    """Controls never overwrite the measurement; they sit beside it."""

    result = _sabotaged_run(monkeypatch, recipe, 1.0)
    report = evaluate(result.pairs, truth_clusters, n_records=len(result.records))
    controls = _controls(result, recipe, truth_clusters)
    markdown = render_eval_markdown(
        report, dataset="intake-demo", gate_threshold=0.0, controls=controls
    )
    assert "| **False-merge rate (gated)** | **98.0%** (344/351) |" in markdown
    assert "## Controls" in markdown
    assert "Controls gate: **FAIL**." in markdown


def test_the_shuffled_labels_control_fails_when_the_truth_is_not_being_read(
    monkeypatch: pytest.MonkeyPatch, real_run: RunResult, truth_clusters: list[list[str]]
) -> None:
    """Sabotage the control's own premise: hand it a permutation that is not one.

    ``_permuted_clusters`` is what makes the labels random. Replacing it with a
    no-op leaves the real labels in place, precision stays at its real value, and
    the control must fail rather than reporting a collapse that did not happen.
    """

    real = shuffled_labels_control(real_run.pairs, truth_clusters, sorted(real_run.records))
    assert real.passed

    def no_shuffle(
        sizes: Sequence[int], population: Sequence[str], rng: random.Random
    ) -> list[list[str]]:
        return [list(cluster) for cluster in truth_clusters]

    monkeypatch.setattr(controls_module, "_permuted_clusters", no_shuffle)
    assert controls_module._permuted_clusters is no_shuffle, "the sabotage did not land"

    broken = shuffled_labels_control(real_run.pairs, truth_clusters, sorted(real_run.records))
    assert not broken.passed, "the shuffle control passed against unshuffled labels"


def test_the_null_matcher_low_control_fails_when_banding_ignores_the_score(
    monkeypatch: pytest.MonkeyPatch,
    real_run: RunResult,
    recipe: Recipe,
    truth_clusters: list[list[str]],
) -> None:
    """Sabotage banding so every pair is AUTO regardless of probability."""

    def band_everything_auto(
        scored: Iterable[tuple[str, str, float]],
        *,
        auto_threshold: float = 0.0,
        review_threshold: float = 0.0,
    ) -> list[Pair]:
        return [
            Pair(left=left, right=right, probability=probability, band=Band.AUTO)
            for left, right, probability in scored
        ]

    monkeypatch.setattr(decisions, "band_pairs", band_everything_auto)
    assert decisions.band_pairs is band_everything_auto, "the sabotage did not land"

    low, _high = null_matcher_control(
        real_run.records,
        real_run.pairs,
        truth_clusters,
        recipe.fields,
        prior=recipe.prior,
        auto_threshold=recipe.auto_threshold,
        review_threshold=recipe.review_threshold,
    )
    assert not low.passed, "the below-review control passed while banding ignored the score"


def test_the_identity_control_states_the_sample_it_actually_covered(
    real_run: RunResult, recipe: Recipe
) -> None:
    """A control over part of a population must not read as one over all of it."""

    outcome = identity_control(
        real_run.records,
        recipe.fields,
        prior=recipe.prior,
        auto_threshold=recipe.auto_threshold,
        review_threshold=recipe.review_threshold,
        sample_cap=5,
        backend=ConstantScoreBackend(1.0),
    )
    assert "5 of 27 records" in outcome.scope
    assert "capped at 5" in outcome.scope


def test_the_identity_control_does_not_claim_a_pass_with_nothing_to_test(
    recipe: Recipe,
) -> None:
    outcome = identity_control(
        {},
        recipe.fields,
        prior=recipe.prior,
        auto_threshold=recipe.auto_threshold,
        review_threshold=recipe.review_threshold,
    )
    assert not outcome.passed
    assert "not run" in outcome.observed


def test_the_null_control_uses_the_pipeline_s_own_banding(
    real_run: RunResult, recipe: Recipe, truth_clusters: list[list[str]]
) -> None:
    """The control must exercise the pipeline's banding, not an imitation of it.

    This is the assertion the test above depends on: patching
    ``decisions.band_pairs`` only reaches the control because the control calls
    that function rather than reimplementing the thresholds.
    """

    source = Path(controls_module.__file__ or "").read_text(encoding="utf-8")
    assert "decisions.band_pairs(" in source, (
        "controls.py no longer calls the pipeline's banding, so the sabotage test above "
        "would pass without reaching the control"
    )
    assert decisions.band_pairs.__module__ == "constituent_reconciler.decisions"

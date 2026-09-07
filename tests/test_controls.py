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
    ControlOutcome,
    ControlsReport,
    _derangement,
    identity_control,
    null_matcher_control,
    run_controls,
    run_extraction_controls,
    shuffled_extraction_labels_control,
    shuffled_labels_control,
)
from constituent_reconciler.evaluate import evaluate
from constituent_reconciler.extract.base import ExtractedField
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
    """The case that motivated this control.

    A constant 0.9 is above the review threshold and below the auto threshold,
    so nothing auto-merges and the gated metric has no denominator. Until issue
    159 that ``0/0`` rendered as **0.0%** and the gate read PASS on a matcher
    that had been replaced by a constant. The headline now reports the absence,
    and this control catches the matcher independently of it, so both layers are
    asserted here.
    """

    result = _sabotaged_run(monkeypatch, recipe, 0.9)

    report = evaluate(result.pairs, truth_clusters, n_records=len(result.records))
    assert report.n_auto == 0
    assert report.false_merge_rate is None, "0/0 must not report as a rate of zero"
    markdown = render_eval_markdown(report, dataset="intake-demo", gate_threshold=0.0)
    assert "False-merge gate at threshold 0.0%: **FAIL**" in markdown
    assert "**0.0%** (0/0)" not in markdown

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


# --------------------------------------------------------------------------
# The extraction label-shuffle control (#153, remaining item 1).
#
# `eval-extraction` scored predictions against `labels.json` and nothing checked
# that the number came from the labels. The sabotage this control has to catch
# is an eval whose truth lookup does not actually vary by document -- one that
# compares predictions against themselves, or resolves every document to the
# same label set. Under that failure the committed 100% precision survives a
# permutation of the labels, which is the signature the control reads.
# --------------------------------------------------------------------------


def _field(name: str, value: str) -> ExtractedField:
    return ExtractedField(field_name=name, value=value, confidence=1.0)


def _labels(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"field_name": name, "value": value} for name, value in pairs]


#: Four documents whose values do not collide, so the exact chance level is 0.
_CLEAN_PREDICTED = {
    "a.pdf": [_field("first_name", "Alice"), _field("email", "alice@example.org")],
    "b.pdf": [_field("first_name", "Beatriz"), _field("email", "b@example.org")],
    "c.pdf": [_field("first_name", "Casey"), _field("email", "casey@example.org")],
    "d.pdf": [_field("first_name", "Dana"), _field("email", "dana@example.org")],
}
_CLEAN_TRUTH = {
    "a.pdf": _labels(("first_name", "Alice"), ("email", "alice@example.org")),
    "b.pdf": _labels(("first_name", "Beatriz"), ("email", "b@example.org")),
    "c.pdf": _labels(("first_name", "Casey"), ("email", "casey@example.org")),
    "d.pdf": _labels(("first_name", "Dana"), ("email", "dana@example.org")),
}


def test_the_extraction_shuffle_control_passes_on_labels_that_are_read() -> None:
    outcome = shuffled_extraction_labels_control(_CLEAN_PREDICTED, _CLEAN_TRUTH)

    assert outcome.passed
    assert "0.0000" in outcome.observed, "no reassignment should have scored a hit"
    assert outcome.scope, "a partial control has to say what it did not cover"


def test_the_extraction_shuffle_control_fails_when_the_labels_are_not_read() -> None:
    """The sabotage the control exists for.

    Every document resolves to the same label set, which is what an eval that
    does not key its truth lookup on the document looks like from the outside.
    Precision stays at 1.0 through every reassignment, so the number is not
    coming from the labels.
    """

    shared = _labels(("first_name", "Alice"), ("email", "alice@example.org"))
    predicted = {name: _CLEAN_PREDICTED["a.pdf"] for name in _CLEAN_PREDICTED}
    truth = {name: shared for name in _CLEAN_TRUTH}

    outcome = shuffled_extraction_labels_control(predicted, truth)

    assert outcome.passed is False
    assert "1.0000" in outcome.observed


def test_a_real_but_colliding_fixture_set_still_passes() -> None:
    """The exact chance level is not assumed to be zero.

    Two documents legitimately sharing a normalized value (the repository's own
    fixtures have two dates of birth that both normalize to 1988-03-09) raises
    the chance level above zero. A control that hard-coded zero would fail on a
    correct fixture set, so this holds the tolerance path honest.
    """

    predicted = dict(_CLEAN_PREDICTED)
    truth = dict(_CLEAN_TRUTH)
    predicted["b.pdf"] = [*predicted["b.pdf"], _field("dob", "03/09/1988")]
    truth["b.pdf"] = [*truth["b.pdf"], *_labels(("dob", "03/09/1988"))]
    truth["d.pdf"] = [*truth["d.pdf"], *_labels(("dob", "March 9, 1988"))]

    outcome = shuffled_extraction_labels_control(predicted, truth)

    assert outcome.passed
    assert "exact chance level 0.0000" not in outcome.observed, (
        "the colliding pair must raise the chance level above zero"
    )


def test_one_document_fails_rather_than_reporting_a_clean_control() -> None:
    """No reassignment exists, so nothing was measured. That is not a pass."""

    outcome = shuffled_extraction_labels_control(
        {"a.pdf": _CLEAN_PREDICTED["a.pdf"]}, {"a.pdf": _CLEAN_TRUTH["a.pdf"]}
    )

    assert outcome.passed is False
    assert "nothing was measured" in outcome.observed


def test_an_extractor_that_produced_nothing_fails_rather_than_passing() -> None:
    """An empty denominator is the absence of a measurement, not a good one."""

    outcome = shuffled_extraction_labels_control({name: [] for name in _CLEAN_TRUTH}, _CLEAN_TRUTH)

    assert outcome.passed is False
    assert "nothing was measured" in outcome.observed


def test_the_reassignment_actually_moves_every_document() -> None:
    """A control whose sabotage silently no-ops reads as a pass.

    A plain shuffle returns the identity permutation about once in n!
    reassignments, and a document scored against its own labels has not been
    sabotaged at all. `_derangement` is what rules that out, so it is asserted
    directly rather than trusted.
    """

    rng = random.Random(1)
    for _ in range(200):
        order = _derangement(5, rng)
        assert sorted(order) == list(range(5))
        assert all(order[index] != index for index in range(5))


def test_the_control_fails_when_the_reassignment_does_not_actually_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clause that catches a sabotage which silently no-ops.

    Written because the first version of this test file could not reach it.
    Removing `mean <= bound` from the pass condition left every other test in
    this file green, because the shared-labels case above is caught by the
    `real_precision > bound` clause on its own -- a clause in a pass condition
    that no sabotage can reach is exactly the defect this module is about.

    What only this clause catches: the reassignment failing to move anything.
    Here `_derangement` is replaced by the identity permutation, so every
    document is scored against its own labels, precision stays at the real
    value, and the "sabotage" applied nothing. The analytic chance level is
    unaffected (it is computed without sampling), so the observed mean rises
    above the bound while the real precision stays above it too.
    """

    monkeypatch.setattr(controls_module, "_derangement", lambda size, rng: list(range(size)))
    outcome = shuffled_extraction_labels_control(_CLEAN_PREDICTED, _CLEAN_TRUTH)

    assert outcome.passed is False
    assert "mean 1.0000" in outcome.observed


def test_the_extraction_control_is_deterministic_under_its_seed() -> None:
    first = shuffled_extraction_labels_control(_CLEAN_PREDICTED, _CLEAN_TRUTH, seed=7)
    second = shuffled_extraction_labels_control(_CLEAN_PREDICTED, _CLEAN_TRUTH, seed=7)

    assert first == second


def test_run_extraction_controls_reports_an_empty_set_as_a_failure() -> None:
    report = run_extraction_controls(_CLEAN_PREDICTED, _CLEAN_TRUTH)

    assert report.outcomes
    assert report.passed
    assert ControlsReport(seed=report.seed, outcomes=()).passed is False


def test_the_extraction_report_renders_the_controls_in_their_own_section() -> None:
    from constituent_reconciler.evaluate import extraction_metrics
    from constituent_reconciler.report import render_extraction_markdown

    metrics = extraction_metrics(_CLEAN_PREDICTED, _CLEAN_TRUTH)
    controls = run_extraction_controls(_CLEAN_PREDICTED, _CLEAN_TRUTH)
    markdown = render_extraction_markdown(metrics, dataset="unit", controls=controls)

    assert "## Controls" in markdown
    assert "shuffled-extraction-labels" in markdown
    headline, _, controls_section = markdown.partition("## Controls")
    assert "shuffled-extraction-labels" not in headline, (
        "control numbers must never be blended into the headline"
    )
    assert "Controls gate: **PASS**" in controls_section


def test_the_cli_renders_controls_on_the_committed_fixtures(tmp_path: Path) -> None:
    """End to end on the real fixture set, through the real extractor."""

    pytest.importorskip("pdfplumber", reason="pdfplumber not installed")
    from constituent_reconciler.cli import main

    out = tmp_path / "extraction-report.md"
    code = main(
        [
            "eval-extraction",
            "--fixtures",
            str(Path("eval/fixtures/extraction")),
            "--out",
            str(out),
            "--controls",
        ]
    )

    assert code == 0
    content = out.read_text(encoding="utf-8")
    assert "## Controls" in content
    assert "shuffled-extraction-labels" in content
    assert "Controls gate: **PASS**" in content


def test_the_cli_exits_nonzero_when_an_extraction_control_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed control is merge-blocking, not a note beside a passing run.

    Without this, `--controls` could render a FAIL row into the committed report
    and still exit 0, which is a control that reports without gating.
    """

    pytest.importorskip("pdfplumber", reason="pdfplumber not installed")
    from constituent_reconciler import cli

    def _failing(predicted: object, truth: object, *, seed: int) -> ControlsReport:
        return ControlsReport(
            seed=seed,
            outcomes=(
                ControlOutcome(
                    name="shuffled-extraction-labels",
                    rules_out="planted",
                    expectation="planted",
                    observed="planted",
                    passed=False,
                ),
            ),
        )

    monkeypatch.setattr(cli, "run_extraction_controls", _failing)
    out = tmp_path / "extraction-report.md"
    code = cli.main(
        [
            "eval-extraction",
            "--fixtures",
            str(Path("eval/fixtures/extraction")),
            "--out",
            str(out),
            "--controls",
        ]
    )

    assert code == 1
    assert "controls FAILED" in capsys.readouterr().err
    assert "Controls gate: **FAIL**" in out.read_text(encoding="utf-8")

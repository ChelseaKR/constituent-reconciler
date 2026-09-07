"""A threshold sweep is structurally a tool for making your numbers look good.

Point it at your own reviewers' verdicts, read the grid, and pick the row with
the nicest false-merge rate. That is the failure mode, and it is not a bug that
shows up as a crash: it shows up as an organization quietly moving its auto
threshold down because a table said it could.

So this suite is written almost entirely against the guardrails rather than the
arithmetic:

* a row that auto-merges nothing has an UNDEFINED false-merge rate, and
  undefined must never be eligible -- otherwise ``--suggest`` recommends a
  setting on no evidence at all;
* a rate must never mix a labeled numerator with an unlabeled denominator;
* a review load the run never recorded must render as unmeasured, not as a
  small number, because that column is the one an operator reads as "cost";
* below the stated minimum the whole thing refuses, because a Wilson interval
  over three verdicts covers most of the range.

Run directories are built by hand. The demo's review queue holds two pairs, and
the cases that matter here need forty with known verdicts.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from constituent_reconciler import sweep
from constituent_reconciler.cli import main
from constituent_reconciler.config import load_recipe
from constituent_reconciler.destruction import NOT_DESTROYED
from constituent_reconciler.evaluate import UNMEASURED
from constituent_reconciler.sweep import (
    MINIMUM_DECIDED_PAIRS,
    SWEEP_JSON_FILENAME,
    SWEEP_REPORT_FILENAME,
    Pair,
    SweepError,
    sweep_thresholds,
)

RECIPE = """\
[input]
existing = "existing.csv"
incoming = "incoming.csv"
id_column = "id"

[mapping]
first_name = "First Name"
last_name = "Last Name"

[thresholds]
prior = 0.01
auto = 0.97
review = 0.80

[output]
connector = "csv"
"""


def _recipe(tmp_path: Path) -> Path:
    for name in ("existing.csv", "incoming.csv"):
        (tmp_path / name).write_text("id,First Name,Last Name\nA1,Ada,Lovelace\n", encoding="utf-8")
    path = tmp_path / "recipe.toml"
    path.write_text(textwrap.dedent(RECIPE), encoding="utf-8")
    return path


def _write_run(
    out_dir: Path,
    *,
    review: dict[tuple[str, str], float],
    auto: dict[tuple[str, str], float] | None = None,
    decisions: dict[str, list[list[str]]] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = ["left,right,probability"]
    for (left, right), probability in review.items():
        rows.append(f"{left},{right},{probability:.4f}")
    (out_dir / "review_queue.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (out_dir / "auto_merges.json").write_text(
        json.dumps(
            {
                "pairs": [
                    {"left": left, "right": right, "probability": probability}
                    for (left, right), probability in (auto or {}).items()
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


def _spread(n: int, low: float, high: float) -> list[float]:
    """``n`` probabilities evenly spread across ``[low, high]``."""

    if n == 1:
        return [low]
    step = (high - low) / (n - 1)
    return [round(low + step * i, 4) for i in range(n)]


def _labeled_run(
    tmp_path: Path,
    *,
    approved_at: list[float],
    rejected_at: list[float],
    unlabeled_at: list[float] | None = None,
) -> tuple[Path, Path]:
    """A run whose review queue carries pairs at chosen probabilities and verdicts."""

    review: dict[tuple[str, str], float] = {}
    approved: list[list[str]] = []
    rejected: list[list[str]] = []
    index = 0
    for probability in approved_at:
        pair = (f"a{index}", f"b{index}")
        review[pair] = probability
        approved.append(list(pair))
        index += 1
    for probability in rejected_at:
        pair = (f"a{index}", f"b{index}")
        review[pair] = probability
        rejected.append(list(pair))
        index += 1
    for probability in unlabeled_at or []:
        review[(f"a{index}", f"b{index}")] = probability
        index += 1
    out_dir = _write_run(
        tmp_path / "out",
        review=review,
        decisions={"approved": approved, "rejected": rejected},
    )
    return _recipe(tmp_path), out_dir


def _row(report: sweep.SweepReport, auto: float, review: float) -> sweep.GridRow:
    for row in report.rows:
        if row.auto == auto and row.review == review:
            return row
    raise AssertionError(f"no row for auto={auto} review={review}")


# -- the guardrail that keeps this from being a number-shopping tool ----------


def test_a_row_that_auto_merges_nothing_is_not_eligible(tmp_path: Path) -> None:
    """Undefined is not zero, and zero is the best possible false-merge rate.

    Every labeled pair here sits below 0.90, so the 0.99-auto row auto-merges
    none of them. Its false-merge rate is undefined. Treating that as a passing
    0.0 would make the most aggressive-looking row the recommended one on no
    evidence whatsoever.
    """

    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.89),
        rejected_at=_spread(20, 0.81, 0.89),
    )
    report = sweep_thresholds(
        load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json"
    )
    empty = _row(report, 0.99, 0.90)
    assert empty.labeled_auto == 0
    assert empty.false_merge_rate is None
    assert empty.eligible is False

    payload = sweep.sweep_payload(report)
    rows = payload["rows"]
    assert isinstance(rows, list)
    assert all(not r["eligible"] for r in rows if r["labeled_auto_merges"] == 0)
    assert UNMEASURED in sweep.render_sweep(report) or "no evidence" in sweep.render_sweep(report)


def test_a_row_with_a_human_rejected_pair_above_its_auto_threshold_is_ineligible(
    tmp_path: Path,
) -> None:
    """The issue's own acceptance criterion.

    A person looked at these two records and said they are different people. A
    threshold that merges them anyway has produced a false merge, and the
    default gate of 0.0 admits none.
    """

    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(32, 0.955, 0.999),
        rejected_at=[0.98],
    )
    report = sweep_thresholds(
        load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json"
    )
    caught = _row(report, 0.97, 0.80)
    assert caught.false_merges == 1
    assert caught.false_merge_rate is not None and caught.false_merge_rate > 0
    assert caught.eligible is False

    # Raise the auto threshold above the rejected pair and the false merge is gone.
    clean = _row(report, 0.99, 0.80)
    assert clean.false_merges == 0
    assert clean.labeled_auto > 0
    assert clean.eligible is True


def test_the_suggestion_is_the_most_conservative_eligible_row_and_is_never_applied(
    tmp_path: Path,
) -> None:
    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(32, 0.955, 0.999),
        rejected_at=[0.955],
    )
    recipe_text = (recipe_path).read_bytes()
    report = sweep_thresholds(
        load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json"
    )
    suggestion = report.suggestion
    assert suggestion is not None
    eligible = [row for row in report.rows if row.eligible]
    assert (suggestion.auto, suggestion.review) == max((r.auto, r.review) for r in eligible)

    assert (
        main(
            [
                "sweep-thresholds",
                "--config",
                str(recipe_path),
                "--decisions",
                str(out_dir / "decisions.json"),
                "--suggest",
            ]
        )
        == 0
    )
    # The recipe is untouched: this report never edits a threshold.
    assert recipe_path.read_bytes() == recipe_text
    assert "Not applied" in (out_dir / SWEEP_REPORT_FILENAME).read_text(
        encoding="utf-8"
    ) or "never edits a recipe" in (out_dir / SWEEP_REPORT_FILENAME).read_text(encoding="utf-8")


def test_no_eligible_row_is_rendered_as_a_result_not_as_a_missing_section(
    tmp_path: Path,
) -> None:
    """ "These verdicts support no setting inside the gate" is information."""

    # A rejected pair at the very top of the range, so every row that merges
    # anything merges it.
    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(32, 0.81, 0.89),
        rejected_at=[0.9999],
    )
    report = sweep_thresholds(
        load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json"
    )
    assert report.suggestion is None
    rendered = sweep.render_sweep(report)
    assert "No row is eligible" in rendered
    assert "not a reason to raise the gate" in rendered
    payload = sweep.sweep_payload(report)
    # An explicit null, never an omitted key: a reader must not have to infer it.
    assert "suggested" in payload
    assert payload["suggested"] is None


# -- rates must not mix labeled and unlabeled populations --------------------


def test_the_false_merge_rate_is_computed_over_labeled_pairs_only(tmp_path: Path) -> None:
    """A labeled numerator over an unlabeled denominator is a wrong number.

    Ten labeled pairs sit above 0.97 and one of them was rejected. Two hundred
    unlabeled pairs also sit above it. The rate is 1/10, not 1/210: nobody
    looked at the other two hundred, so they can neither confirm nor contradict
    anything.
    """

    review: dict[tuple[str, str], float] = {}
    approved: list[list[str]] = []
    for i, probability in enumerate(_spread(9, 0.975, 0.999)):
        review[(f"L{i}a", f"L{i}b")] = probability
        approved.append([f"L{i}a", f"L{i}b"])
    review[("R0a", "R0b")] = 0.99
    for i, probability in enumerate(_spread(21, 0.81, 0.96)):
        review[(f"F{i}a", f"F{i}b")] = probability
        approved.append([f"F{i}a", f"F{i}b"])
    auto = {(f"U{i}a", f"U{i}b"): 0.995 for i in range(200)}

    out_dir = _write_run(
        tmp_path / "out",
        review=review,
        auto=auto,
        decisions={"approved": approved, "rejected": [["R0a", "R0b"]]},
    )
    recipe_path = _recipe(tmp_path)
    report = sweep_thresholds(
        load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json"
    )
    row = _row(report, 0.97, 0.80)
    assert row.labeled_auto == 10
    assert row.false_merges == 1
    assert row.false_merge_rate == pytest.approx(0.1)
    # The 200 unlabeled auto-band pairs DO count toward review load's population
    # but never toward a rate.
    assert row.review_load == 21


def test_a_review_threshold_below_the_run_s_reports_an_unmeasured_load(
    tmp_path: Path,
) -> None:
    """The run recorded nothing below its own review threshold.

    Counting only what it did record would report a review load certainly too
    low, on the column an operator reads as the cost of being more careful.
    """

    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.96),
        rejected_at=_spread(20, 0.81, 0.96),
    )
    report = sweep_thresholds(
        load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json"
    )
    assert report.observable_floor == 0.80
    assert _row(report, 0.97, 0.70).review_load is None
    assert _row(report, 0.97, 0.75).review_load is None
    assert _row(report, 0.97, 0.80).review_load is not None

    rendered = sweep.render_sweep(report)
    assert UNMEASURED in rendered
    assert "Review load is unmeasured below a review threshold of 0.8" in rendered


# -- the refusals ------------------------------------------------------------


def test_too_few_decisions_is_a_hard_exit_with_the_minimum_stated(tmp_path: Path) -> None:
    """Not a warning above a table. The refusal is the feature."""

    recipe_path, out_dir = _labeled_run(tmp_path, approved_at=[0.99, 0.98], rejected_at=[0.85])
    with pytest.raises(SweepError) as caught:
        sweep_thresholds(load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json")
    assert str(MINIMUM_DECIDED_PAIRS) in str(caught.value)
    assert "found 3" in str(caught.value)
    assert not (out_dir / SWEEP_REPORT_FILENAME).exists()

    assert (
        main(
            [
                "sweep-thresholds",
                "--config",
                str(recipe_path),
                "--decisions",
                str(out_dir / "decisions.json"),
            ]
        )
        == 2
    )
    assert not (out_dir / SWEEP_REPORT_FILENAME).exists()


def test_a_decided_pair_the_run_cannot_account_for_refuses(tmp_path: Path) -> None:
    """Dropping it would shrink the labeled set and quietly improve every row."""

    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.99),
        rejected_at=_spread(20, 0.81, 0.99),
    )
    decisions_path = out_dir / "decisions.json"
    data = json.loads(decisions_path.read_text(encoding="utf-8"))
    data["approved"].append(["ghost-left", "ghost-right"])
    decisions_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SweepError) as caught:
        sweep_thresholds(load_recipe(str(recipe_path)), decisions_path=decisions_path)
    assert "ghost-left+ghost-right" in str(caught.value)
    assert "quietly improve every row" in str(caught.value)


def test_a_missing_decisions_file_or_review_queue_refuses_by_name(tmp_path: Path) -> None:
    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.99),
        rejected_at=_spread(20, 0.81, 0.99),
    )
    with pytest.raises(SweepError, match="decisions file not found"):
        sweep_thresholds(load_recipe(str(recipe_path)), decisions_path=out_dir / "nope.json")

    (out_dir / "review_queue.csv").unlink()
    with pytest.raises(SweepError, match="review_queue.csv"):
        sweep_thresholds(load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json")


def test_a_non_numeric_probability_refuses_rather_than_reading_as_zero(
    tmp_path: Path,
) -> None:
    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.99),
        rejected_at=_spread(20, 0.81, 0.99),
    )
    queue = out_dir / "review_queue.csv"
    lines = queue.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].rsplit(",", 1)[0] + ","
    queue.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(SweepError, match="an unreadable probability is not a low one"):
        sweep_thresholds(load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json")


def test_a_malformed_decisions_section_refuses_rather_than_reading_no_verdicts(
    tmp_path: Path,
) -> None:
    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.99),
        rejected_at=_spread(20, 0.81, 0.99),
    )
    decisions_path = out_dir / "decisions.json"
    decisions_path.write_text(json.dumps({"approved": "all of them"}), encoding="utf-8")
    with pytest.raises(SweepError, match="is not a list"):
        sweep_thresholds(load_recipe(str(recipe_path)), decisions_path=decisions_path)


# -- shape and classification ------------------------------------------------


def test_the_recipe_s_own_row_is_always_in_the_grid_and_marked(tmp_path: Path) -> None:
    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.99),
        rejected_at=_spread(20, 0.81, 0.99),
    )
    report = sweep_thresholds(
        load_recipe(str(recipe_path)), decisions_path=out_dir / "decisions.json"
    )
    current = report.current_row
    assert (current.auto, current.review) == (0.97, 0.80)
    assert sum(1 for row in report.rows if row.is_current) == 1
    assert "(current)" in sweep.render_sweep(report)


def test_the_reports_hold_no_pair_id_and_are_kept_by_destroy(tmp_path: Path) -> None:
    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.99),
        rejected_at=_spread(20, 0.81, 0.99),
    )
    assert (
        main(
            [
                "sweep-thresholds",
                "--config",
                str(recipe_path),
                "--decisions",
                str(out_dir / "decisions.json"),
            ]
        )
        == 0
    )
    for name in (SWEEP_REPORT_FILENAME, SWEEP_JSON_FILENAME):
        text = (out_dir / name).read_text(encoding="utf-8")
        assert "a0" not in text.replace("auto", "").replace("false", "")
        assert name in NOT_DESTROYED

    assert main(["destroy", "--out", str(out_dir), "--older-than", "0d"]) == 0
    # Counts and rates only, so destruction would remove evidence without
    # removing anybody's personal data.
    assert (out_dir / SWEEP_REPORT_FILENAME).exists()
    assert (out_dir / SWEEP_JSON_FILENAME).exists()


def test_two_sweeps_of_one_run_are_byte_identical(tmp_path: Path) -> None:
    recipe_path, out_dir = _labeled_run(
        tmp_path,
        approved_at=_spread(20, 0.81, 0.99),
        rejected_at=_spread(20, 0.81, 0.99),
    )
    args = [
        "sweep-thresholds",
        "--config",
        str(recipe_path),
        "--decisions",
        str(out_dir / "decisions.json"),
        "--out",
    ]
    assert main([*args, str(tmp_path / "s1")]) == 0
    assert main([*args, str(tmp_path / "s2")]) == 0
    for name in (SWEEP_REPORT_FILENAME, SWEEP_JSON_FILENAME):
        assert (tmp_path / "s1" / name).read_bytes() == (tmp_path / "s2" / name).read_bytes()


def test_pair_ids_are_order_independent(tmp_path: Path) -> None:
    assert Pair.of("b", "a") == Pair.of("a", "b")

"""Exit-code behavior of `constituent-reconcile eval` with the kappa calibration gate.

The gate is fail-closed: a missing, unreadable, or empty labels file exits 1
the same way a kappa below 0.60 does. Passing labels leave the existing
false-merge exit logic in charge.
"""

from __future__ import annotations

import json
from pathlib import Path

from constituent_reconciler.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


def _eval_args(out: Path, *extra: str) -> list[str]:
    return [
        "eval",
        "--config",
        str(EXAMPLES / "recipe.toml"),
        "--truth",
        str(EXAMPLES / "ground_truth.json"),
        "--out",
        str(out),
        *extra,
    ]


def test_eval_passes_with_committed_calibration_labels(tmp_path: Path) -> None:
    out = tmp_path / "report.md"
    labels = EXAMPLES / "calibration_labels.json"
    code = main(_eval_args(out, "--calibration", str(labels)))
    assert code == 0
    markdown = out.read_text(encoding="utf-8")
    assert "## Calibration (LLM field judge)" in markdown
    assert "Kappa gate at 0.60: **PASS**" in markdown


def test_eval_exits_one_on_low_kappa(tmp_path: Path) -> None:
    # Half the predictions disagree with the human labels: kappa is near zero.
    labels = [
        {"record_id": f"R{i:03d}", "field": "email", "predicted": p, "actual": a}
        for i, (p, a) in enumerate(
            [(True, True)] * 5 + [(False, False)] * 5 + [(True, False), (False, True)] * 5
        )
    ]
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps({"threshold": 0.6, "labels": labels}), encoding="utf-8")
    out = tmp_path / "report.md"
    code = main(_eval_args(out, "--calibration", str(labels_path)))
    assert code == 1
    assert "Kappa gate at 0.60: **FAIL**" in out.read_text(encoding="utf-8")


def test_eval_exits_one_on_missing_labels_file(tmp_path: Path) -> None:
    out = tmp_path / "report.md"
    code = main(_eval_args(out, "--calibration", str(tmp_path / "does-not-exist.json")))
    assert code == 1
    # The report still renders, with the gate reported fail-closed.
    assert "Kappa gate at 0.60: **FAIL** (no labels)." in out.read_text(encoding="utf-8")


def test_eval_exits_one_on_empty_labels(tmp_path: Path) -> None:
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps({"threshold": 0.6, "labels": []}), encoding="utf-8")
    out = tmp_path / "report.md"
    code = main(_eval_args(out, "--calibration", str(labels_path)))
    assert code == 1


def test_the_committed_eval_report_matches_a_fresh_run(tmp_path: Path) -> None:
    """eval/report.md is a committed measurement: the README points a visitor at it
    before they have run anything, so it must be what the current code computes.
    Nothing enforced that. A matching change could land, every test could pass on
    its own tmp-path reports, and the committed file would keep quoting the old
    pipeline with no gate going red.

    The eval is deterministic on the committed demo and labels, so this is a byte
    comparison. If it goes red after a deliberate matching change, regenerate with
    `make eval` and commit the diff; never edit the report by hand.
    """
    out = tmp_path / "report.md"
    labels = EXAMPLES / "calibration_labels.json"
    # `--controls` mirrors the Makefile `eval` recipe, which is what writes the
    # committed file. Dropping it here would compare a report without the
    # controls section against one with it and fail for the wrong reason -- and
    # a version of this test that passed by not asking for the controls would be
    # a drift check with a hole exactly the shape of the thing it now covers.
    assert main(_eval_args(out, "--calibration", str(labels), "--controls")) == 0
    committed = EXAMPLES.parents[1] / "eval" / "report.md"
    assert out.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8"), (
        "eval/report.md no longer matches what `constituent-reconcile eval` computes from the "
        "committed demo; regenerate it with `make eval` and commit the diff"
    )


def test_eval_exits_one_when_a_control_fails(tmp_path: Path, monkeypatch: object) -> None:
    """A failing control must not exit 0, whatever the headline says.

    The scorer is replaced with a constant 0.9: above the review threshold and
    below the auto threshold, so nothing auto-merges and the gated false-merge
    rate has no denominator. Until #159 that `0/0` rendered as **0.0%** and read
    as a PASS, and every gate that predated the controls was green on this run.
    """

    from _pytest.monkeypatch import MonkeyPatch

    from constituent_reconciler import matching
    from constituent_reconciler.controls import ConstantScoreBackend

    assert isinstance(monkeypatch, MonkeyPatch)
    backend = ConstantScoreBackend(0.9)
    monkeypatch.setattr(matching, "_default_backend", backend)
    assert matching.default_backend() is backend, "the sabotage did not land"

    out = tmp_path / "report.md"
    labels = EXAMPLES / "calibration_labels.json"
    code = main(_eval_args(out, "--calibration", str(labels), "--controls"))
    markdown = out.read_text(encoding="utf-8")
    assert "| **False-merge rate (gated)** | **no evidence** (0/0) |" in markdown
    assert "**0.0%** (0/0)" not in markdown
    assert "Kappa gate at 0.60: **PASS**" in markdown
    assert "Controls gate: **FAIL**." in markdown
    assert code == 1, "a report carrying a failed control exited 0"


def test_eval_exits_one_on_the_same_sabotage_without_controls(
    tmp_path: Path, monkeypatch: object
) -> None:
    """The negative control for #159, held as a test.

    Same constant-0.9 scorer, but `--controls` is not passed, so the only thing
    that can catch it is the headline metric. It must not report a rate it did
    not measure, and it must not exit 0.
    """

    from _pytest.monkeypatch import MonkeyPatch

    from constituent_reconciler import matching
    from constituent_reconciler.controls import ConstantScoreBackend

    assert isinstance(monkeypatch, MonkeyPatch)
    backend = ConstantScoreBackend(0.9)
    monkeypatch.setattr(matching, "_default_backend", backend)
    assert matching.default_backend() is backend, "the sabotage did not land"

    out = tmp_path / "report.md"
    labels = EXAMPLES / "calibration_labels.json"
    code = main(_eval_args(out, "--calibration", str(labels)))
    markdown = out.read_text(encoding="utf-8")
    assert "## Controls" not in markdown
    assert "| **False-merge rate (gated)** | **no evidence** (0/0) |" in markdown
    assert "False-merge gate at threshold 0.0%: **FAIL**" in markdown
    assert "no evidence: 0 auto-merged pairs" in markdown
    assert code == 1, "a run whose matcher was replaced by a constant exited 0"


def test_eval_without_controls_renders_no_controls_section(tmp_path: Path) -> None:
    """The flag is opt-in: without it the report is exactly what it was."""

    out = tmp_path / "report.md"
    labels = EXAMPLES / "calibration_labels.json"
    assert main(_eval_args(out, "--calibration", str(labels))) == 0
    assert "## Controls" not in out.read_text(encoding="utf-8")

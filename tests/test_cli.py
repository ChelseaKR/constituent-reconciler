"""Tests for the ``reconcile`` command-line interface.

Exercised through ``main`` so argument parsing and command wiring are covered,
not just the underlying pipeline functions tested elsewhere. The ``apply``
command's corrections handling gets its own coverage here since it is the CLI
surface for the "correct" review verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from constituent_reconciler.cli import _load_corrections, main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


def test_run_writes_resolved_output(tmp_path: Path) -> None:
    code = main(["run", "--config", str(EXAMPLES / "recipe.toml"), "--out", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "resolved.csv").exists()


def test_apply_with_no_decisions_file_merges_nothing(tmp_path: Path) -> None:
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(json.dumps({"approved": [], "rejected": []}), encoding="utf-8")
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--decisions",
            str(decisions_path),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0
    assert (tmp_path / "out" / "resolved.csv").exists()


def test_apply_applies_a_corrected_verdict_and_merges_with_the_fix(tmp_path: Path) -> None:
    # Mirrors what the review session writes for a "correct" verdict: the
    # decisions file names the pair as corrected, and a sibling corrections
    # file carries the field value.
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps({"approved": [["existing:E002", "incoming:N004"]], "rejected": []}),
        encoding="utf-8",
    )
    corrections_path = tmp_path / "corrections.json"
    corrections_path.write_text(
        json.dumps(
            {
                "corrections": [
                    {
                        "left": "existing:E002",
                        "right": "incoming:N004",
                        "side": "right",
                        "field": "dob",
                        "value": "1972-03-08",
                        "reviewer": "casey",
                        "corrected_at": "2026-07-12T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--decisions",
            str(decisions_path),
            "--out",
            str(out_dir),
        ]
    )
    assert code == 0
    resolved = (out_dir / "resolved.csv").read_text(encoding="utf-8")
    assert "1972-03-08" in resolved


def test_apply_accepts_an_explicit_corrections_path(tmp_path: Path) -> None:
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps({"approved": [["existing:E002", "incoming:N004"]], "rejected": []}),
        encoding="utf-8",
    )
    corrections_path = tmp_path / "elsewhere-corrections.json"
    corrections_path.write_text(
        json.dumps(
            {
                "corrections": [
                    {
                        "left": "existing:E002",
                        "right": "incoming:N004",
                        "side": "right",
                        "field": "dob",
                        "value": "1972-03-08",
                        "reviewer": "casey",
                        "corrected_at": "2026-07-12T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--decisions",
            str(decisions_path),
            "--corrections",
            str(corrections_path),
            "--out",
            str(out_dir),
        ]
    )
    assert code == 0
    resolved = (out_dir / "resolved.csv").read_text(encoding="utf-8")
    assert "1972-03-08" in resolved


def test_apply_corrections_file_with_bad_side_raises() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "corrections.json"
        path.write_text(
            json.dumps(
                {
                    "corrections": [
                        {"left": "A", "right": "B", "side": "up", "field": "dob", "value": "x"}
                    ]
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="left.*right"):
            _load_corrections(path)


def test_eval_command_scores_against_ground_truth(tmp_path: Path) -> None:
    code = main(
        [
            "eval",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--truth",
            str(EXAMPLES / "ground_truth.json"),
            "--calibration",
            str(EXAMPLES / "calibration_labels.json"),
        ]
    )
    assert code == 0


def test_schema_command_prints_versions() -> None:
    assert main(["schema"]) == 0

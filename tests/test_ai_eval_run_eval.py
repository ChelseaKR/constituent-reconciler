"""The AI eval runner has to be able to fail.

Three of the five evals in ``tools/ai_eval`` compute a ``pass`` boolean.
``consent_leakage``'s own docstring calls a leak "a merge-blocking-grade
finding, not a tuning number", and ``_render_markdown`` renders
``Gate: **FAIL**`` for it. Until this file existed, ``run_eval.main`` returned
``0`` unconditionally, so ``make eval-ai`` succeeded on a consent leak and
nothing in the repository read the verdict it had just written down.

The second half is subtler and is why ``gate_findings`` distinguishes two
kinds. Each provider-backed eval's ``not_run()`` result carries **no ``pass``
key at all**. A gate written as ``result.get("pass", True)`` would therefore
report a suite that measured nothing as a suite that passed everything, which
is the same defect one layer up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tools.ai_eval import consent_leakage, run_eval
from tools.ai_eval.run_eval import GATED_EVALS, gate_findings


def _passing_results() -> dict[str, dict[str, Any]]:
    return {
        "adversarial_refusal": {"status": "ran", "pass": True},
        "citation_grounding": {"status": "ran", "grounding_rate": 0.9},
        "consent_leakage": {"status": "deterministic", "pass": True, "leaks_found": 0},
        "ocr_proposals": {"status": "ran", "invented_plausible_value_count": 0},
        "unanswerable_queries": {"status": "ran", "pass": True},
    }


def _not_run() -> dict[str, Any]:
    """The exact shape `adversarial_refusal.not_run()` returns: no `pass` key."""

    return {
        "status": "not run",
        "reason": "no AI provider was configured/enabled when the eval harness ran",
    }


def test_a_clean_suite_has_no_findings() -> None:
    assert gate_findings(_passing_results()) == []


def test_a_failed_gate_is_reported_as_failed() -> None:
    results = _passing_results()
    results["consent_leakage"]["pass"] = False

    findings = gate_findings(results)

    assert [(f.eval_name, f.kind) for f in findings] == [("consent_leakage", "failed")]


def test_a_missing_verdict_is_not_counted_as_a_pass() -> None:
    """The absence-rendered-as-a-value case, at the suite level.

    `not_run()` carries no `pass` key. Reading the gate as
    `result.get("pass", True)` makes a suite that measured nothing look like a
    suite that passed everything.
    """

    results = _passing_results()
    results["adversarial_refusal"] = _not_run()
    results["unanswerable_queries"] = _not_run()

    findings = gate_findings(results)

    assert sorted((f.eval_name, f.kind) for f in findings) == [
        ("adversarial_refusal", "no-verdict"),
        ("unanswerable_queries", "no-verdict"),
    ]
    assert all("not run" in f.detail for f in findings)


def test_an_eval_absent_from_the_results_is_not_a_pass() -> None:
    results = _passing_results()
    del results["consent_leakage"]

    findings = gate_findings(results)

    assert [(f.eval_name, f.kind) for f in findings] == [("consent_leakage", "no-verdict")]


def test_the_ungated_evals_are_ungated_on_purpose() -> None:
    """`citation_grounding` and `ocr_proposals` measure quality, not safety.

    Asserted rather than assumed, so that adding either to `GATED_EVALS` is a
    deliberate act with a test to change, not a silent widening of what blocks
    a merge on a threshold nobody chose.
    """

    assert "citation_grounding" not in GATED_EVALS
    assert "ocr_proposals" not in GATED_EVALS

    results = _passing_results()
    results["citation_grounding"]["pass"] = False
    results["ocr_proposals"]["pass"] = False

    assert gate_findings(results) == []


class _DisabledProvider:
    name = "none"

    def is_enabled(self) -> bool:
        return False


def _run_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, leak_pass: bool, extra: list[str]
) -> tuple[int, dict[str, Any]]:
    """Drive `main` with no provider, so only the deterministic eval runs.

    `--out-json`/`--out-md` are always redirected: the defaults are the
    committed `eval/ai/` artifacts, and a test must never rewrite published
    evidence.
    """

    monkeypatch.setattr(run_eval, "make_provider", lambda **kwargs: _DisabledProvider())
    # The module object, not `run_eval.consent_leakage`: strict mypy's
    # no_implicit_reexport refuses the attribute read, and `run_eval` calls
    # through the same module object, so patching it here reaches the caller.
    real_run = consent_leakage.run

    def _patched() -> dict[str, Any]:
        return {**real_run(), "pass": leak_pass}

    monkeypatch.setattr(consent_leakage, "run", _patched)
    out_json = tmp_path / "results.json"
    code = run_eval.main(
        ["--out-json", str(out_json), "--out-md", str(tmp_path / "report.md"), *extra]
    )
    return code, json.loads(out_json.read_text(encoding="utf-8"))


def test_main_exits_nonzero_when_the_deterministic_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect this file exists for: a rendered FAIL used to exit 0."""

    code, results = _run_main(tmp_path, monkeypatch, leak_pass=False, extra=[])

    assert results["consent_leakage"]["pass"] is False
    assert code == 1
    assert "eval-ai FAILED" in capsys.readouterr().err


def test_main_exits_zero_but_says_so_when_a_provider_eval_did_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No provider is a normal local state, and it is not a clean bill of health."""

    code, results = _run_main(tmp_path, monkeypatch, leak_pass=True, extra=[])

    assert "pass" not in results["adversarial_refusal"]
    assert code == 0
    assert "eval-ai INCOMPLETE" in capsys.readouterr().err


def test_require_provider_makes_a_missing_verdict_merge_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _run_main(tmp_path, monkeypatch, leak_pass=True, extra=["--require-provider"])

    assert code == 1
    err = capsys.readouterr().err
    assert "--require-provider" in err
    assert "adversarial_refusal" in err

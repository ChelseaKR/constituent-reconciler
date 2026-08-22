"""CLI wiring tests for the opt-in ai-* subcommands.

``ai-triage`` never needs a configured provider, so it is exercised
end to end here; ``ai-explain``/``ai-ask``/``ai-propose-corrections`` are
exercised for their policy gate and their "no provider configured" path,
which do not require network access. Their happy paths (an actual model
call) are covered at the module level in
tests/test_assistant_match_explain.py, test_assistant_ask.py, and
test_assistant_ocr_propose.py via a FakeProvider, and live end to end by
eval/ai/ against a real provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from constituent_reconciler.cli import build_parser, main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


@pytest.mark.parametrize(
    "argv",
    [
        ["ai-explain", "--config", "x.toml", "--pair", "a", "b"],
        ["ai-ask", "--config", "x.toml", "--pair", "a", "b", "--question", "q"],
        ["ai-propose-corrections", "--config", "x.toml", "--record", "a"],
        ["ai-triage", "--config", "x.toml"],
    ],
)
def test_build_parser_registers_every_ai_subcommand(argv: list[str]) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    assert callable(args.func)


def test_ai_triage_runs_end_to_end_without_any_ai_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["ai-triage", "--config", str(EXAMPLES / "recipe.toml")])
    assert code == 0
    out = capsys.readouterr().out
    assert "ordering only, never a decision" in out


def test_ai_explain_under_dv_pack_refuses_with_a_policy_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "ai-explain",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--out",
            str(tmp_path),
            "--pair",
            "existing:E002",
            "incoming:N004",
            "--policy-pack",
            "dv",
        ]
    )
    assert code == 2
    assert "policy error" in capsys.readouterr().err


def test_ai_explain_without_a_configured_provider_reports_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = main(
        [
            "ai-explain",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--out",
            str(tmp_path),
            "--pair",
            "existing:E002",
            "incoming:N004",
            "--ai-provider",
            "anthropic",
        ]
    )
    assert code == 2
    assert "no AI provider is configured" in capsys.readouterr().err


def test_ai_explain_unknown_pair_reports_clearly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "ai-explain",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--out",
            str(tmp_path),
            "--pair",
            "does-not-exist:1",
            "does-not-exist:2",
        ]
    )
    assert code == 2
    assert "no real comparison evidence" in capsys.readouterr().err

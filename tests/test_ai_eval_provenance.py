"""Every committed AI eval result must carry full provenance.

Rejects a committed ``eval/ai/results.json`` where any entry is missing
provider, model, prompt_version, commit, date, or a valid status -- a
result without provenance cannot be traced back to what actually produced
it, per the project's own rule against fabricated or unattributable eval
numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.ai_eval.provenance import missing_provenance_fields

RESULTS_PATH = Path(__file__).resolve().parents[1] / "eval" / "ai" / "results.json"


def test_results_file_exists() -> None:
    assert RESULTS_PATH.is_file(), "eval/ai/results.json must be committed"


def test_every_eval_entry_carries_complete_provenance() -> None:
    data = json.loads(RESULTS_PATH.read_text())
    assert data, "results.json must not be empty"
    for eval_name, record in data.items():
        missing = missing_provenance_fields(record)
        assert not missing, f"{eval_name} is missing provenance fields: {missing}"


def test_missing_provenance_fields_rejects_an_incomplete_record() -> None:
    incomplete = {"provider": "anthropic", "model": "claude-sonnet-5"}
    missing = missing_provenance_fields(incomplete)
    assert "prompt_version" in missing
    assert "commit" in missing
    assert "date" in missing
    assert "status" in missing


def test_missing_provenance_fields_accepts_a_not_run_record() -> None:
    record = {
        "provider": "not run",
        "model": "not run",
        "prompt_version": "v1",
        "commit": "abc123",
        "date": "2026-01-01",
        "status": "not run",
    }
    assert missing_provenance_fields(record) == ()


def test_missing_provenance_fields_rejects_an_invalid_status() -> None:
    record = {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "prompt_version": "v1",
        "commit": "abc123",
        "date": "2026-01-01",
        "status": "maybe",
    }
    assert "status" in missing_provenance_fields(record)

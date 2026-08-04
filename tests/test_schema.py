"""Tests for the declared schema/interface versions and the schema command."""

from __future__ import annotations

import json
from pathlib import Path

from constituent_reconciler import pipeline
from constituent_reconciler.cli import main
from constituent_reconciler.config import load_recipe
from constituent_reconciler.schema import (
    CONFIG_SCHEMA_VERSION,
    CONNECTOR_INTERFACE_VERSION,
    DECISIONS_SCHEMA_VERSION,
    REPAIR_CAPABILITY_SCHEMA_VERSION,
    REPAIR_PLAN_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    versions,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


def test_versions_are_positive_integers() -> None:
    for value in versions().values():
        assert isinstance(value, int)
        assert value >= 1


def test_versions_mapping_matches_constants() -> None:
    assert versions() == {
        "config_schema": CONFIG_SCHEMA_VERSION,
        "connector_interface": CONNECTOR_INTERFACE_VERSION,
        "report_schema": REPORT_SCHEMA_VERSION,
        "decisions_schema": DECISIONS_SCHEMA_VERSION,
        "repair_plan": REPAIR_PLAN_SCHEMA_VERSION,
        "repair_capability": REPAIR_CAPABILITY_SCHEMA_VERSION,
    }


def test_schema_command_prints_versions(capsys: object) -> None:
    code = main(["schema"])
    assert code == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "config_schema:" in out
    assert "connector_interface:" in out
    assert "report_schema:" in out
    assert "decisions_schema:" in out


def test_aggregate_summary_carries_report_schema_version(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    assert summary.aggregate_path is not None
    payload = json.loads(summary.aggregate_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == REPORT_SCHEMA_VERSION
    # Run metadata added to the report schema: the named survivorship policy.
    assert payload["fill_policy"] == recipe.fill_policy

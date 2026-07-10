"""Tests for recipe loading, focused on the consent lifecycle columns.

FIX-06 extends the recipe's [consent] section with optional ``date``,
``expires``, and ``scope`` columns on top of the existing ``column`` and
``require`` keys. These tests cover only that surface; the broader recipe
loader is exercised indirectly by every other test that calls ``load_recipe``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from constituent_reconciler.config import load_recipe
from constituent_reconciler.policy import PolicyViolation

MINIMAL_INPUT = (
    '[input]\nincoming = "incoming.csv"\n\n[mapping]\nfirst_name = "First"\nlast_name = "Last"\n'
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "recipe.toml"
    path.write_text(body, encoding="utf-8")
    (tmp_path / "incoming.csv").write_text("First,Last\n", encoding="utf-8")
    return path


def test_consent_column_only_leaves_date_expires_scope_unset(tmp_path: Path) -> None:
    recipe = load_recipe(_write(tmp_path, MINIMAL_INPUT + '\n[consent]\ncolumn = "Consent"\n'))
    assert recipe.consent_column == "Consent"
    assert recipe.consent_date_column is None
    assert recipe.consent_expires_column is None
    assert recipe.consent_scope_column is None


def test_consent_section_maps_date_expires_and_scope_columns(tmp_path: Path) -> None:
    recipe = load_recipe(
        _write(
            tmp_path,
            MINIMAL_INPUT + "\n[consent]\n"
            'column = "Consent"\n'
            'date = "Consent Date"\n'
            'expires = "Consent Expires"\n'
            'scope = "Consent Scope"\n',
        )
    )
    assert recipe.consent_column == "Consent"
    assert recipe.consent_date_column == "Consent Date"
    assert recipe.consent_expires_column == "Consent Expires"
    assert recipe.consent_scope_column == "Consent Scope"


def test_no_consent_section_leaves_every_consent_field_unset(tmp_path: Path) -> None:
    recipe = load_recipe(_write(tmp_path, MINIMAL_INPUT))
    assert recipe.consent_column is None
    assert recipe.consent_date_column is None
    assert recipe.consent_expires_column is None
    assert recipe.consent_scope_column is None
    assert recipe.require_consent is False


def test_no_comparable_section_defaults_to_off_and_empty(tmp_path: Path) -> None:
    recipe = load_recipe(_write(tmp_path, MINIMAL_INPUT))
    assert recipe.comparable_export is False
    assert recipe.comparable_breakdown_fields == ()
    assert recipe.comparable_period == ""


def test_comparable_section_sets_export_breakdown_fields_and_period(tmp_path: Path) -> None:
    recipe = load_recipe(
        _write(
            tmp_path,
            MINIMAL_INPUT + "\n[comparable]\n"
            "export = true\n"
            'breakdown_fields = ["county", "program"]\n'
            'period = "2026-Q2"\n',
        )
    )
    assert recipe.comparable_export is True
    assert recipe.comparable_breakdown_fields == ("county", "program")
    assert recipe.comparable_period == "2026-Q2"


def test_comparable_breakdown_field_defaults_to_off_even_with_period_set(
    tmp_path: Path,
) -> None:
    # ``period``/``breakdown_fields`` may be set without ``export = true``: they
    # still apply to the standalone ``export-comparable`` command, which does
    # not gate on ``comparable_export`` (only ``run``/``apply`` do).
    recipe = load_recipe(_write(tmp_path, MINIMAL_INPUT + '\n[comparable]\nperiod = "2026-Q2"\n'))
    assert recipe.comparable_export is False
    assert recipe.comparable_period == "2026-Q2"


def test_comparable_section_with_identifying_breakdown_field_rejected_at_load(
    tmp_path: Path,
) -> None:
    # Per the README's fail-closed claim, an identifying canonical field named
    # as a comparable breakdown is refused when the recipe is loaded, before
    # any record is read -- not only later, when the report is actually built.
    with pytest.raises(PolicyViolation, match="identifying"):
        load_recipe(
            _write(
                tmp_path,
                MINIMAL_INPUT + '\n[comparable]\nbreakdown_fields = ["last_name"]\n',
            )
        )

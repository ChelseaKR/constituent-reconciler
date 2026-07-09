"""Tests for recipe loading, focused on the consent lifecycle columns.

FIX-06 extends the recipe's [consent] section with optional ``date``,
``expires``, and ``scope`` columns on top of the existing ``column`` and
``require`` keys. These tests cover only that surface; the broader recipe
loader is exercised indirectly by every other test that calls ``load_recipe``.
"""

from __future__ import annotations

from pathlib import Path

from constituent_reconciler.config import load_recipe

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

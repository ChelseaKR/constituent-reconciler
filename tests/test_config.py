"""Tests for recipe loading: the consent lifecycle columns, the [review]
two-person settings, and FIX-04's fail-closed shape validation.

FIX-06 extends the recipe's [consent] section with optional ``date``,
``expires``, and ``scope`` columns on top of the existing ``column`` and
``require`` keys. The consent tests cover only that surface; the broader
recipe loader is exercised indirectly by every other test that calls
``load_recipe``.

FIX-04 makes an unknown section or an unknown key inside a known section raise
``RecipeError`` (a ``ValueError`` subclass) instead of being silently ignored,
because the recipe is the one non-technical operators hand-edit and every
other config surface already raises on a typo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from constituent_reconciler.config import RecipeError, load_recipe
from constituent_reconciler.policy import PolicyViolation

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"

MINIMAL_INPUT = (
    '[input]\nincoming = "incoming.csv"\n\n[mapping]\nfirst_name = "First"\nlast_name = "Last"\n'
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "recipe.toml"
    path.write_text(body, encoding="utf-8")
    (tmp_path / "incoming.csv").write_text("First,Last\n", encoding="utf-8")
    return path


def _write_recipe(tmp_path: Path, extra: str) -> Path:
    body = (
        "[input]\n"
        f'incoming = "{EXAMPLES / "incoming.csv"}"\n'
        'id_column = "id"\n\n'
        "[mapping]\n"
        'first_name = "First Name"\n'
        'last_name = "Last Name"\n\n'
        f"{extra}"
    )
    path = tmp_path / "recipe.toml"
    path.write_text(body, encoding="utf-8")
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


# -- E4: two-person review settings -------------------------------------------


def test_review_section_defaults_off(tmp_path: Path) -> None:
    recipe = load_recipe(_write_recipe(tmp_path, ""))
    assert recipe.require_second_reviewer is False


def test_review_section_turns_two_person_review_on(tmp_path: Path) -> None:
    recipe = load_recipe(_write_recipe(tmp_path, "[review]\nrequire_second_reviewer = true\n"))
    assert recipe.require_second_reviewer is True


def test_recipe_cannot_turn_off_the_dv_packs_requirement(tmp_path: Path) -> None:
    path = _write_recipe(
        tmp_path,
        '[policy]\npack = "dv"\n\n[review]\nrequire_second_reviewer = false\n',
    )
    recipe = load_recipe(path)
    assert recipe.require_second_reviewer is True


def test_dv_pack_defaults_two_person_review_on() -> None:
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    assert recipe.require_second_reviewer is True


def test_default_pack_leaves_two_person_review_off() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    assert recipe.require_second_reviewer is False


# -- FIX-04: fail-closed shape validation ------------------------------------


def test_unknown_section_is_rejected_with_a_suggestion(tmp_path: Path) -> None:
    # The exact typo the ideation pitch names: a misspelled [consent] section.
    path = _write(tmp_path, MINIMAL_INPUT + '\n[consnet]\ncolumn = "Consent"\n')
    with pytest.raises(RecipeError, match=r"unknown section \[consnet\].*consent"):
        load_recipe(path)


def test_unknown_key_in_a_known_section_is_rejected(tmp_path: Path) -> None:
    # The exact typo the ideation pitch names: auto_threshold instead of auto.
    path = _write(tmp_path, MINIMAL_INPUT + "\n[thresholds]\nauto_threshold = 0.99\n")
    with pytest.raises(RecipeError, match=r"unknown key 'auto_threshold'"):
        load_recipe(path)


def test_unknown_key_names_the_nearest_valid_spelling(tmp_path: Path) -> None:
    # Close enough for difflib to suggest the real key, unlike "auto_threshold".
    path = _write(tmp_path, MINIMAL_INPUT + "\n[thresholds]\natuo = 0.99\n")
    with pytest.raises(RecipeError, match=r"unknown key 'atuo'.*did you mean 'auto'"):
        load_recipe(path)


def test_unknown_mapping_key_is_rejected_rather_than_silently_dropped(tmp_path: Path) -> None:
    # Before FIX-04, a mapping key outside CANONICAL_FIELDS was filtered out
    # silently by a dict comprehension; a typo'd canonical field name (here,
    # "frist_name") used to vanish instead of raising.
    path = _write(
        tmp_path,
        '[input]\nincoming = "incoming.csv"\n\n[mapping]\n'
        'frist_name = "First"\nlast_name = "Last"\n',
    )
    with pytest.raises(RecipeError, match=r"unknown canonical field 'frist_name'"):
        load_recipe(path)


def test_a_section_body_that_is_not_a_table_is_rejected(tmp_path: Path) -> None:
    # [[thresholds]] (array-of-tables syntax) makes "thresholds" a list, not a
    # table -- a genuinely malformed shape rather than an unknown key.
    path = _write(tmp_path, MINIMAL_INPUT + "\n[[thresholds]]\nauto = 0.9\n")
    with pytest.raises(RecipeError, match="must be a table"):
        load_recipe(path)


def test_every_committed_example_recipe_validates(tmp_path: Path) -> None:
    # Every shipped example must pass the strict validator unmodified; a
    # regression here would mean the schema drifted from what the recipes use.
    examples_root = Path(__file__).resolve().parents[1] / "examples"
    recipe_paths = sorted(examples_root.rglob("recipe*.toml"))
    assert recipe_paths, "expected at least one example recipe"
    for recipe_path in recipe_paths:
        load_recipe(recipe_path)  # raises RecipeError on any drift


def test_recipe_error_is_a_value_error(tmp_path: Path) -> None:
    # Callers that already catch ValueError around load_recipe keep working.
    path = _write(tmp_path, "")
    with pytest.raises(ValueError):
        load_recipe(path)


# -- FIX-04: `reconcile validate` --------------------------------------------


def test_validate_command_accepts_a_good_recipe(capsys: object) -> None:
    from constituent_reconciler.cli import main

    examples = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
    code = main(["validate", "--config", str(examples / "recipe.toml")])
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert code == 0
    assert "recipe is valid." in out
    assert "mapped fields: first_name, last_name, dob, email, phone" in out
    assert "policy pack: default" in out


def test_validate_command_reports_an_invalid_recipe(tmp_path: Path, capsys: object) -> None:
    from constituent_reconciler.cli import main

    path = _write(tmp_path, MINIMAL_INPUT + "\n[thresholds]\nauto_threshold = 0.99\n")
    code = main(["validate", "--config", str(path)])
    err = capsys.readouterr().err  # type: ignore[attr-defined]
    assert code == 2
    assert "invalid recipe" in err
    assert "auto_threshold" in err


def test_validate_command_reports_a_missing_incoming_file(tmp_path: Path, capsys: object) -> None:
    from constituent_reconciler.cli import main

    path = tmp_path / "recipe.toml"
    path.write_text(MINIMAL_INPUT, encoding="utf-8")
    # Deliberately do not write incoming.csv, unlike _write().
    code = main(["validate", "--config", str(path)])
    err = capsys.readouterr().err  # type: ignore[attr-defined]
    assert code == 2
    assert "does not exist" in err

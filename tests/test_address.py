"""Tests for CASS-style address normalization and its use in matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from constituent_reconciler.address import (
    normalize_address,
    normalize_address_deterministic,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "address-demo"


# ---------------------------------------------------------------------------
# Deterministic standardization
# ---------------------------------------------------------------------------


def test_blank_address_normalizes_to_empty() -> None:
    assert normalize_address_deterministic("") == ""
    assert normalize_address_deterministic("   ") == ""


def test_street_suffix_is_abbreviated() -> None:
    assert normalize_address_deterministic("123 Main Street") == "123 MAIN ST"
    assert normalize_address_deterministic("50 Oak Avenue") == "50 OAK AVE"
    assert normalize_address_deterministic("9 Sunset Boulevard") == "9 SUNSET BLVD"


def test_directional_is_abbreviated() -> None:
    assert normalize_address_deterministic("123 North Main Street") == "123 N MAIN ST"
    assert normalize_address_deterministic("4400 Southwest Pine Road") == "4400 SW PINE RD"


def test_unit_designator_is_abbreviated() -> None:
    assert normalize_address_deterministic("12 Elm St Apartment 4") == "12 ELM ST APT 4"
    assert normalize_address_deterministic("12 Elm St Suite 200") == "12 ELM ST STE 200"


# ---------------------------------------------------------------------------
# Position rules: the standardization is position-aware, not a flat token map
# ---------------------------------------------------------------------------


def test_leading_st_is_saint_not_a_suffix() -> None:
    # "ST" opening a street name is Saint; only the suffix position maps.
    assert normalize_address_deterministic("123 St Charles Street") == "123 ST CHARLES ST"
    assert normalize_address_deterministic("123 St Charles Avenue") == "123 ST CHARLES AVE"


def test_directional_after_house_number_is_abbreviated() -> None:
    assert normalize_address_deterministic("123 North Main Street") == "123 N MAIN ST"


def test_trailing_directional_is_abbreviated() -> None:
    assert normalize_address_deterministic("123 Main St North") == "123 MAIN ST N"
    # The suffix position sits just before the trailing directional.
    assert normalize_address_deterministic("123 Main Street North") == "123 MAIN ST N"
    # A unit phrase after the trailing directional does not disturb either rule.
    assert (
        normalize_address_deterministic("123 Main Street North Apt 4")
        == "123 MAIN ST N APT 4"
    )


def test_interior_directional_word_is_left_alone() -> None:
    # "North" inside the street name is not in a directional position.
    assert normalize_address_deterministic("123 Old North Road") == "123 OLD NORTH RD"


def test_suffix_word_as_the_street_name_is_left_alone() -> None:
    # "Avenue B" is a street named Avenue; there is no suffix to abbreviate.
    assert normalize_address_deterministic("123 Avenue B") == "123 AVENUE B"


def test_suffix_before_unit_phrase_is_abbreviated() -> None:
    assert normalize_address_deterministic("12 Elm Street Apartment 4") == "12 ELM ST APT 4"
    assert normalize_address_deterministic("12 Elm Street # 4") == "12 ELM ST # 4"


def test_unit_designator_maps_only_before_a_unit_value() -> None:
    assert normalize_address_deterministic("12 Elm St Unit 7") == "12 ELM ST UNIT 7"
    # A designator word inside the street name has no unit value after it.
    assert normalize_address_deterministic("55 Apartment Hill Road") == "55 APARTMENT HILL RD"


def test_position_rules_are_idempotent_and_stable() -> None:
    samples = [
        "123 St Charles Street",
        "123 Main St North",
        "123 Avenue B",
        "55 Apartment Hill Road",
        "123 Main Street North Apt 4",
    ]
    for sample in samples:
        once = normalize_address_deterministic(sample)
        assert normalize_address_deterministic(once) == once


def test_already_abbreviated_positional_forms_are_stable() -> None:
    for written in ("123 ST CHARLES ST", "123 MAIN ST N", "123 N MAIN ST APT 4"):
        assert normalize_address_deterministic(written) == written


def test_two_writings_of_the_same_address_converge() -> None:
    long_form = normalize_address_deterministic("123 North Main Street")
    short_form = normalize_address_deterministic("123 N Main St")
    assert long_form == short_form == "123 N MAIN ST"


def test_already_abbreviated_address_is_stable() -> None:
    once = normalize_address_deterministic("123 N MAIN ST")
    twice = normalize_address_deterministic(once)
    assert once == twice == "123 N MAIN ST"


def test_punctuation_is_dropped_but_pound_sign_kept() -> None:
    assert normalize_address_deterministic("123 Main St., Apt. # 4") == "123 MAIN ST APT # 4"


def test_normalization_is_idempotent() -> None:
    samples = [
        "123 North Main Street",
        "4400 Southwest Pine Boulevard Apartment 12",
        "78 Oak Court",
    ]
    for sample in samples:
        once = normalize_address_deterministic(sample)
        assert normalize_address_deterministic(once) == once


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def test_default_backend_is_deterministic() -> None:
    assert normalize_address("123 North Main Street") == "123 N MAIN ST"


def test_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="unknown address backend"):
        normalize_address("123 Main St", backend="nonsense")


def test_libpostal_backend_raises_clearly_when_unavailable() -> None:
    # The 'postal' package is not a project dependency. Selecting the libpostal
    # backend without it must fail with a clear, actionable message, not silently
    # fall back (which would change the matching key without telling anyone).
    try:
        import postal  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="libpostal"):
            normalize_address("123 Main St", backend="libpostal")
    else:  # pragma: no cover - exercised only where libpostal is installed
        pytest.skip("libpostal is installed; the unavailable-path test does not apply")


# ---------------------------------------------------------------------------
# Real libpostal path (runs where the C library is installed; CI has a
# scheduled job for it, and the tests skip everywhere else)
# ---------------------------------------------------------------------------


def test_libpostal_backend_returns_a_standardized_form() -> None:
    pytest.importorskip("postal")
    result = normalize_address("123 North Main Street", backend="libpostal")
    assert result != ""
    assert result == result.upper()


def test_libpostal_backend_is_deterministic_across_calls() -> None:
    pytest.importorskip("postal")
    first = normalize_address("123 North Main Street", backend="libpostal")
    for _ in range(3):
        assert normalize_address("123 North Main Street", backend="libpostal") == first


def test_libpostal_backend_blank_input_returns_empty() -> None:
    pytest.importorskip("postal")
    assert normalize_address("", backend="libpostal") == ""
    assert normalize_address("   ", backend="libpostal") == ""


# ---------------------------------------------------------------------------
# Address in the matching pipeline
# ---------------------------------------------------------------------------


def test_address_field_activates_only_when_mapped() -> None:
    from constituent_reconciler.config import load_recipe

    recipe = load_recipe(EXAMPLES / "recipe.toml")
    assert "address" in recipe.fields

    demo = load_recipe(
        Path(__file__).resolve().parents[1] / "examples" / "intake-demo" / "recipe.toml"
    )
    assert "address" not in demo.fields


def test_pipeline_merges_records_with_address_format_variation() -> None:
    from constituent_reconciler import pipeline
    from constituent_reconciler.config import load_recipe

    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)

    # The two writings of each address normalize to the same standardized form,
    # so the address agrees and the true duplicates auto-merge.
    auto = {pair.key() for pair in result.auto_pairs}
    assert frozenset(("A001", "B001")) in auto
    assert frozenset(("A002", "B002")) in auto

    # The normalized address on the merged records is the standardized form.
    a001 = result.records["A001"]
    b001 = result.records["B001"]
    assert a001.normalized["address"] == b001.normalized["address"] == "123 N MAIN ST"


def test_pipeline_does_not_merge_distinct_person_with_different_address() -> None:
    from constituent_reconciler import pipeline
    from constituent_reconciler.config import load_recipe

    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)

    # B003 shares only a surname with A003: different dob, email, phone, address.
    # It must not auto-merge.
    auto = {pair.key() for pair in result.auto_pairs}
    assert frozenset(("A003", "B003")) not in auto

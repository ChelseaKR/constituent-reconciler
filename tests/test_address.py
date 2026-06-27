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
    assert (
        normalize_address_deterministic("12 Elm St Apartment 4") == "12 ELM ST APT 4"
    )
    assert normalize_address_deterministic("12 Elm St Suite 200") == "12 ELM ST STE 200"


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

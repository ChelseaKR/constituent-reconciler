"""Tests for the policy-pack model and its derivation into a recipe."""

from __future__ import annotations

from pathlib import Path

import pytest

from constituent_reconciler.config import load_recipe
from constituent_reconciler.policy import PolicyViolation, policy_for

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


def test_default_pack_enforces_nothing() -> None:
    policy = policy_for("default")
    assert policy.require_consent is False
    assert policy.forbid_cloud_seam is False
    assert policy.require_local_targets is False
    assert policy.aggregate_export is False
    assert policy.require_second_reviewer is False


def test_dv_pack_enforces_the_full_posture() -> None:
    policy = policy_for("dv")
    assert policy.require_consent is True
    assert policy.forbid_cloud_seam is True
    assert policy.require_local_targets is True
    assert policy.aggregate_export is True
    assert policy.require_second_reviewer is True


def test_hipaa_pack_requires_consent_and_no_cloud_but_not_dv_extras() -> None:
    policy = policy_for("hipaa")
    assert policy.require_consent is True
    assert policy.forbid_cloud_seam is True
    # HIPAA's full invariant set is not specified here, so it does not claim the
    # DV pack's local-target, aggregate, and two-person-review rules.
    assert policy.require_local_targets is False
    assert policy.aggregate_export is False
    assert policy.require_second_reviewer is False


def test_unknown_pack_raises_fail_closed() -> None:
    with pytest.raises(PolicyViolation, match="unknown policy pack"):
        policy_for("definitely-not-a-pack")


def test_recipe_derives_dv_invariants_from_pack() -> None:
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    assert recipe.policy_pack == "dv"
    assert recipe.require_consent is True
    assert recipe.require_local_targets is True
    assert recipe.aggregate_export is True
    assert recipe.suppression_threshold == 11


def test_policy_pack_override_applies_dv_to_a_plain_recipe() -> None:
    # The plain demo recipe has no policy section; the override applies the DV
    # posture without editing the file.
    plain = load_recipe(EXAMPLES / "recipe.toml")
    assert plain.require_local_targets is False

    overridden = load_recipe(EXAMPLES / "recipe.toml", policy_pack="dv")
    assert overridden.policy_pack == "dv"
    assert overridden.require_consent is True
    assert overridden.require_local_targets is True
    assert overridden.aggregate_export is True


def test_unknown_override_raises_fail_closed() -> None:
    with pytest.raises(PolicyViolation):
        load_recipe(EXAMPLES / "recipe.toml", policy_pack="nonsense")


def test_recipe_may_turn_consent_on_under_default_pack() -> None:
    # A recipe can opt into consent enforcement even without a strict pack; it
    # cannot opt out of one the pack imposes.
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    assert recipe.require_consent is False  # default demo does not require it


def test_household_grouping_defaults_off() -> None:
    # No [household] section in the demo recipe: the grouping step must not run.
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    assert recipe.household.enabled is False


def test_household_grouping_defaults_off_under_dv_too() -> None:
    # The invariant the ideation item calls out by name: even under the dv
    # pack, household inference never turns itself on. Only an explicit
    # [household] enabled = true in the recipe can do that.
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    assert recipe.policy_pack == "dv"
    assert recipe.household.enabled is False

    overridden = load_recipe(EXAMPLES / "recipe.toml", policy_pack="dv")
    assert overridden.household.enabled is False


def test_household_grouping_can_be_explicitly_enabled(tmp_path: Path) -> None:
    # A recipe that opts in explicitly turns the step on, including under dv:
    # the pack does not force it off, it just never forces it on.
    recipe_path = tmp_path / "recipe.toml"
    recipe_path.write_text(
        """
        [input]
        incoming = "incoming.csv"

        [mapping]
        first_name = "First Name"
        last_name = "Last Name"

        [household]
        enabled = true
        """,
        encoding="utf-8",
    )
    recipe = load_recipe(recipe_path)
    assert recipe.household.enabled is True

    recipe_dv = load_recipe(recipe_path, policy_pack="dv")
    assert recipe_dv.household.enabled is True

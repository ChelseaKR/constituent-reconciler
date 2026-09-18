"""Configuring a pull: the `[source]` section, the registry, and the egress refusal.

A pull is configured in two places that must agree, and every way they can
disagree is a refusal here rather than a run that quietly reads the wrong
thing: a `[source]` section with no connector named, a connector with no
endpoint, an unknown connector, a page size below one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from constituent_reconciler.config import RecipeError, load_recipe
from constituent_reconciler.connectors.civicrm_source import CivicrmSource
from constituent_reconciler.pipeline import build_source_connector
from constituent_reconciler.policy import PolicyViolation

_MAPPING = '[mapping]\nfirst_name = "first"\nlast_name = "last"\n'


def _recipe(tmp_path: Path, body: str, *, name: str = "recipe.toml") -> Path:
    (tmp_path / "incoming.csv").write_text("first,last\nAlice,Walker\n", encoding="utf-8")
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


_PULL = (
    '[input]\nincoming = "incoming.csv"\nexisting = "connector:civicrm"\n\n'
    + _MAPPING
    + '\n[source]\nendpoint = "https://crm.example.org/civicrm/ajax/api4"\n'
)


class _Transport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.calls.append(url)
        return 200, b'{"values": []}'


def test_a_recipe_can_pull_the_existing_side_instead_of_reading_a_file(tmp_path: Path) -> None:
    recipe = load_recipe(_recipe(tmp_path, _PULL))
    assert recipe.existing_connector == "civicrm"
    # The two are mutually exclusive: nothing should later read a path here.
    assert recipe.existing is None
    assert recipe.source.endpoint == "https://crm.example.org/civicrm/ajax/api4"
    assert recipe.source.page_size == 100


def test_a_file_path_still_loads_as_a_path(tmp_path: Path) -> None:
    (tmp_path / "existing.csv").write_text("first,last\n", encoding="utf-8")
    body = '[input]\nincoming = "incoming.csv"\nexisting = "existing.csv"\n\n' + _MAPPING
    recipe = load_recipe(_recipe(tmp_path, body))
    assert recipe.existing == tmp_path / "existing.csv"
    assert recipe.existing_connector is None


def test_a_source_section_with_no_connector_named_is_refused(tmp_path: Path) -> None:
    """A recipe that looks like it pulls and does not would read a stale export."""
    body = (
        '[input]\nincoming = "incoming.csv"\nexisting = "existing.csv"\n\n'
        + _MAPPING
        + '\n[source]\nendpoint = "https://crm.example.org"\n'
    )
    (tmp_path / "existing.csv").write_text("first,last\n", encoding="utf-8")
    with pytest.raises(RecipeError, match="does not name a connector"):
        load_recipe(_recipe(tmp_path, body))


def test_a_pull_with_no_endpoint_is_refused(tmp_path: Path) -> None:
    body = '[input]\nincoming = "incoming.csv"\nexisting = "connector:civicrm"\n\n' + _MAPPING
    with pytest.raises(RecipeError, match="needs a \\[source\\] endpoint"):
        load_recipe(_recipe(tmp_path, body))


def test_an_unknown_source_connector_is_refused_with_the_known_names(tmp_path: Path) -> None:
    body = (
        '[input]\nincoming = "incoming.csv"\nexisting = "connector:salesfarce"\n\n'
        + _MAPPING
        + '\n[source]\nendpoint = "https://crm.example.org"\n'
    )
    with pytest.raises(RecipeError, match="known source connectors: civicrm"):
        load_recipe(_recipe(tmp_path, body))


def test_a_connector_prefix_with_no_name_is_refused(tmp_path: Path) -> None:
    body = '[input]\nincoming = "incoming.csv"\nexisting = "connector:"\n\n' + _MAPPING
    with pytest.raises(RecipeError, match="names no connector"):
        load_recipe(_recipe(tmp_path, body))


def test_a_page_size_below_one_is_refused(tmp_path: Path) -> None:
    body = _PULL + "page_size = 0\n"
    with pytest.raises(RecipeError, match="page_size must be at least 1"):
        load_recipe(_recipe(tmp_path, body))


def test_the_built_source_carries_the_recipe_page_size_and_the_injected_transport(
    tmp_path: Path,
) -> None:
    recipe = load_recipe(_recipe(tmp_path, _PULL + "page_size = 25\n"))
    transport = _Transport()
    source = build_source_connector(recipe, transport=transport)
    assert isinstance(source, CivicrmSource)
    assert source.page_size == 25
    assert source.transport is transport


def test_a_recipe_that_reads_a_file_has_no_source_to_build(tmp_path: Path) -> None:
    body = '[input]\nincoming = "incoming.csv"\n\n' + _MAPPING
    recipe = load_recipe(_recipe(tmp_path, body))
    with pytest.raises(ValueError, match="no source connector"):
        build_source_connector(recipe)


def test_the_dv_pack_refuses_the_pull_before_a_byte_moves(tmp_path: Path) -> None:
    """Reading constituent records out of a hosted CRM is an egress too."""
    recipe = load_recipe(_recipe(tmp_path, _PULL), policy_pack="dv")
    transport = _Transport()
    with pytest.raises(PolicyViolation, match="forbids pulling the existing side"):
        build_source_connector(recipe, transport=transport)
    assert transport.calls == [], "the pull must be refused before any request is built"

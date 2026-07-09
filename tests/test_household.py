"""Tests for the household grouping suggestion (household.py).

Household grouping is a post-clustering suggestion, not a match decision: these
tests build golden records directly rather than running the whole pipeline, the
same pattern test_connectors_crm_csv.py uses for the CRM export.
"""

from __future__ import annotations

from constituent_reconciler.household import confirmed_member_map, suggest_households
from constituent_reconciler.models import Consent, GoldenRecord


def _golden(cluster_id: str, *, last_name: str = "", address: str = "") -> GoldenRecord:
    fields: dict[str, str] = {}
    if last_name:
        fields["last_name"] = last_name
    if address:
        fields["address"] = address
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        primary=cluster_id,
        consent=Consent(status="granted"),
    )


def test_shared_address_and_surname_yields_a_suggestion() -> None:
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address="123 N MAIN ST"),
    ]
    suggestions = suggest_households(golden)
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.members == ("E1", "E2")
    assert suggestion.address == "123 N MAIN ST"
    assert suggestion.surname == "reyes"
    assert suggestion.household_id == "HH-E1"


def test_shared_address_alone_is_not_evidence_of_a_household() -> None:
    # The shelter case the ideation item names by name: two unrelated people at
    # one address must not become a household suggestion just because they
    # share a roof. No surname agreement, no suggestion.
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="okafor", address="123 N MAIN ST"),
    ]
    assert suggest_households(golden) == []


def test_shared_surname_alone_is_not_evidence_of_a_household() -> None:
    # Same surname at different addresses (a sibling across town) is not a
    # household either; both address and surname evidence are required.
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address="9 OAK AVE"),
    ]
    assert suggest_households(golden) == []


def test_missing_address_or_surname_is_excluded() -> None:
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address=""),
        _golden("E3", last_name="", address="123 N MAIN ST"),
    ]
    assert suggest_households(golden) == []


def test_three_way_household_groups_together() -> None:
    golden = [
        _golden("E3", last_name="reyes", address="123 N MAIN ST"),
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address="123 N MAIN ST"),
    ]
    suggestions = suggest_households(golden)
    assert len(suggestions) == 1
    assert suggestions[0].members == ("E1", "E2", "E3")
    # The id is derived from the lowest member id, so it is stable run to run.
    assert suggestions[0].household_id == "HH-E1"


def test_two_households_at_different_addresses_stay_separate() -> None:
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address="123 N MAIN ST"),
        _golden("E3", last_name="chen", address="9 OAK AVE"),
        _golden("E4", last_name="chen", address="9 OAK AVE"),
    ]
    suggestions = suggest_households(golden)
    assert {s.household_id for s in suggestions} == {"HH-E1", "HH-E3"}


def test_mixed_surnames_at_one_address_group_by_surname_not_address() -> None:
    # A married couple with different surnames at a shared address, alongside
    # an unrelated third person sharing that address: only the surname pair
    # among them is grouped, and the same-address stranger is not swept in.
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address="123 N MAIN ST"),
        _golden("E3", last_name="okafor", address="123 N MAIN ST"),
    ]
    suggestions = suggest_households(golden)
    assert len(suggestions) == 1
    assert suggestions[0].members == ("E1", "E2")


def test_suggestions_are_deterministic_regardless_of_input_order() -> None:
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address="123 N MAIN ST"),
    ]
    forward = suggest_households(golden)
    backward = suggest_households(list(reversed(golden)))
    assert forward == backward


def test_confirmed_member_map_only_includes_confirmed_ids() -> None:
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address="123 N MAIN ST"),
        _golden("E3", last_name="chen", address="9 OAK AVE"),
        _golden("E4", last_name="chen", address="9 OAK AVE"),
    ]
    suggestions = suggest_households(golden)
    mapping = confirmed_member_map(suggestions, frozenset({"HH-E1"}))
    assert mapping == {"E1": "HH-E1", "E2": "HH-E1"}


def test_confirmed_member_map_empty_when_nothing_confirmed() -> None:
    golden = [
        _golden("E1", last_name="reyes", address="123 N MAIN ST"),
        _golden("E2", last_name="reyes", address="123 N MAIN ST"),
    ]
    suggestions = suggest_households(golden)
    assert confirmed_member_map(suggestions, frozenset()) == {}

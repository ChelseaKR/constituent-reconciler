from __future__ import annotations

from constituent_reconciler.nicknames import canonical_key


def test_common_nickname_pairs_share_a_key() -> None:
    assert canonical_key("bill") == canonical_key("william")
    assert canonical_key("peggy") == canonical_key("margaret")
    assert canonical_key("bob") == canonical_key("robert")
    assert canonical_key("liz") == canonical_key("elizabeth")


def test_unrelated_names_do_not_share_a_key() -> None:
    assert canonical_key("william") != canonical_key("robert")
    assert canonical_key("margaret") != canonical_key("susan")


def test_name_outside_the_table_maps_to_itself() -> None:
    assert canonical_key("zorion") == "zorion"


def test_empty_input_maps_to_empty() -> None:
    assert canonical_key("") == ""

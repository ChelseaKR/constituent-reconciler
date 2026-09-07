"""Writing a reviewed household to a live CRM, and the households refused.

EXP-07 shipped household suggestions and a shared household-id column in the
CSV connectors. The live CiviCRM and Salesforce connectors ignored the confirmed
map entirely -- zero occurrences of "household" in either file -- so an
organization on the API path got contacts and lost the household it had just
reviewed.

The dangerous version of this feature is not one that fails to write. It is one
that writes a household of two when the reviewer confirmed three, because the
third member's consent was withheld. That asserts a family relationship in the
CRM on incomplete evidence, and the missing person's absence from a household
the reviewer said was theirs is itself an inference about them. So most of this
suite is about households the planner refuses, and about a dry run touching
nothing.

Every CRM call here goes through an injected transport. That proves request
*construction* and idempotency, not that CiviCRM or NPSP accept it; #67 is the
live exercise that would upgrade the claim, and nothing here says otherwise.
"""

from __future__ import annotations

import json

import pytest

from constituent_reconciler.connectors.civicrm import CivicrmConfig, CivicrmConnector
from constituent_reconciler.connectors.household_write import (
    HOUSEHOLD_RELATIONSHIP_TYPE,
    HouseholdWriteResult,
)
from constituent_reconciler.connectors.salesforce import SalesforceConfig, SalesforceConnector
from constituent_reconciler.household import (
    HOUSEHOLD_EXTERNAL_ID_PREFIX,
    SKIP_MEMBER_NOT_WRITTEN,
    SKIP_MEMBER_WITHHELD,
    SKIP_SINGLE_MEMBER,
    HouseholdSuggestion,
    HouseholdWrite,
    plan_household_writes,
)


def _suggestion(household_id: str, *members: str) -> HouseholdSuggestion:
    return HouseholdSuggestion(
        household_id=household_id,
        members=tuple(members),
        address="12 elm st",
        surname="tran",
    )


def _write(household_id: str, *members: str) -> HouseholdWrite:
    return HouseholdWrite(
        household_id=household_id,
        members=tuple((member, member) for member in sorted(members)),
    )


# -- the planner refuses ------------------------------------------------------


def test_a_confirmed_household_with_every_member_written_is_planned() -> None:
    suggestions = [_suggestion("HH-1", "c1", "c2", "c3")]
    writes, skips = plan_household_writes(
        suggestions,
        frozenset({"HH-1"}),
        {"c1": "x1", "c2": "x2", "c3": "x3"},
    )
    assert skips == []
    assert len(writes) == 1
    assert writes[0].household_id == "HH-1"
    assert writes[0].external_id == f"{HOUSEHOLD_EXTERNAL_ID_PREFIX}HH-1"
    # Members carry BOTH ids: the relationship needs the destination's, the
    # report needs the cluster's.
    assert writes[0].members == (("c1", "x1"), ("c2", "x2"), ("c3", "x3"))


def test_a_withheld_member_stops_the_whole_household() -> None:
    """The judgement this feature turns on.

    Writing the other two would publish a household the reviewer never
    confirmed, and would say something about the withheld person by leaving
    them out of one that was.
    """

    writes, skips = plan_household_writes(
        [_suggestion("HH-1", "c1", "c2", "c3")],
        frozenset({"HH-1"}),
        {"c1": "x1", "c2": "x2"},
        withheld_ids=frozenset({"c3"}),
    )
    assert writes == []
    assert len(skips) == 1
    assert skips[0].reason == SKIP_MEMBER_WITHHELD
    # The report names the person, not a count.
    assert skips[0].members == ("c3",)


def test_a_member_whose_own_write_failed_stops_the_household() -> None:
    """A failed member write leaves no household record behind."""

    writes, skips = plan_household_writes(
        [_suggestion("HH-1", "c1", "c2")],
        frozenset({"HH-1"}),
        {"c1": "x1"},
    )
    assert writes == []
    assert skips[0].reason == SKIP_MEMBER_NOT_WRITTEN
    assert skips[0].members == ("c2",)


def test_withheld_and_merely_unwritten_are_reported_apart() -> None:
    """Different follow-ups, so never one bucket.

    A withheld member needs a consent conversation. An unwritten one needs the
    run investigated. Collapsing them would send an operator down the wrong path.
    """

    writes, skips = plan_household_writes(
        [_suggestion("HH-1", "c1", "c2"), _suggestion("HH-2", "d1", "d2")],
        frozenset({"HH-1", "HH-2"}),
        {"c1": "x1", "d1": "y1"},
        withheld_ids=frozenset({"c2"}),
    )
    assert writes == []
    reasons = {skip.household_id: skip.reason for skip in skips}
    assert reasons == {"HH-1": SKIP_MEMBER_WITHHELD, "HH-2": SKIP_MEMBER_NOT_WRITTEN}


def test_an_unconfirmed_household_is_not_written_and_is_not_a_skip() -> None:
    """A suggestion nobody reviewed never enters this path at all.

    It is not a refusal to report: it was never proposed for writing, and
    listing it as skipped would put an unreviewed grouping in a report about
    reviewed ones.
    """

    writes, skips = plan_household_writes(
        [_suggestion("HH-1", "c1", "c2")], frozenset(), {"c1": "x1", "c2": "x2"}
    )
    assert writes == []
    assert skips == []


def test_a_single_member_household_is_skipped_with_its_own_reason() -> None:
    writes, skips = plan_household_writes(
        [_suggestion("HH-1", "c1")], frozenset({"HH-1"}), {"c1": "x1"}
    )
    assert writes == []
    assert skips[0].reason == SKIP_SINGLE_MEMBER


# -- CiviCRM ------------------------------------------------------------------


def _civicrm(
    responses: list[tuple[int, dict[str, object]]],
) -> tuple[CivicrmConnector, list[tuple[str, dict[str, str], bytes]]]:
    from tests.conftest import FakeCivicrmTransport

    transport = FakeCivicrmTransport(responses)
    connector = CivicrmConnector(
        CivicrmConfig(endpoint="https://civicrm.example.org/civicrm/ajax/api4", api_key="k"),
        transport=transport,
    )
    return connector, transport.calls


def _urls(calls: list[tuple[str, dict[str, str], bytes]]) -> list[str]:
    return [url.rsplit("/api4/", 1)[-1] for url, _, _ in calls]


def _params(call: tuple[str, dict[str, str], bytes]) -> dict[str, object]:
    from urllib.parse import parse_qs

    raw = parse_qs(call[2].decode("utf-8"))["params"][0]
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_civicrm_creates_a_household_and_one_relationship_per_member() -> None:
    """The exact entity calls for a two-member household."""

    connector, calls = _civicrm(
        [
            (200, {"values": []}),  # household lookup: absent
            (200, {"values": [{"id": 90}]}),  # Contact.create (Household)
            (200, {"values": [{"id": 11}]}),  # member 1 lookup
            (200, {"values": []}),  # relationship lookup: absent
            (200, {"values": [{"id": 500}]}),  # Relationship.create
            (200, {"values": [{"id": 12}]}),  # member 2 lookup
            (200, {"values": []}),
            (200, {"values": [{"id": 501}]}),
        ]
    )
    [result] = connector.write_households([_write("HH-1", "c1", "c2")], dry_run=False)
    assert result.action == "created"
    assert result.external_id == "90"
    assert result.members == ("c1", "c2")
    assert result.is_write

    assert _urls(calls) == [
        "Contact/get",
        "Contact/create",
        "Contact/get",
        "Relationship/get",
        "Relationship/create",
        "Contact/get",
        "Relationship/get",
        "Relationship/create",
    ]
    household_create = _params(calls[1])["values"]
    assert isinstance(household_create, dict)
    assert household_create["contact_type"] == "Household"
    assert household_create["external_identifier"] == "hh-HH-1"

    relationship = _params(calls[4])["values"]
    assert isinstance(relationship, dict)
    assert relationship["contact_id_a"] == 11
    assert relationship["contact_id_b"] == 90
    assert relationship["relationship_type_id:name"] == HOUSEHOLD_RELATIONSHIP_TYPE


def test_civicrm_makes_zero_create_calls_on_a_second_run() -> None:
    """Idempotency by lookup, not by hope."""

    connector, calls = _civicrm(
        [
            (200, {"values": [{"id": 90}]}),  # household already exists
            (200, {"values": [{"id": 11}]}),  # member 1
            (200, {"values": [{"id": 500}]}),  # relationship already exists
            (200, {"values": [{"id": 12}]}),  # member 2
            (200, {"values": [{"id": 501}]}),
        ]
    )
    [result] = connector.write_households([_write("HH-1", "c1", "c2")], dry_run=False)
    assert result.action == "updated"
    assert not any(url.endswith("/create") for url, _, _ in calls)


def test_civicrm_refuses_rather_than_leaving_a_partial_household() -> None:
    """A member CiviCRM does not hold cannot be linked, and must not be skipped.

    Continuing would leave a household record naming fewer people than the
    reviewer confirmed, which is the shape this feature exists to avoid.
    """

    connector, _ = _civicrm(
        [
            (200, {"values": []}),
            (200, {"values": [{"id": 90}]}),
            (200, {"values": []}),  # member lookup by numeric id: absent
            (200, {"values": []}),  # member lookup by external id: absent
        ]
    )
    from constituent_reconciler.connectors.base import ConnectorError

    with pytest.raises(ConnectorError) as caught:
        connector.write_households([_write("HH-1", "c1", "c2")], dry_run=False)
    assert "refusing to leave a partial household" in str(caught.value)


def test_civicrm_dry_run_makes_no_call_at_all() -> None:
    """Not a read, not a lookup. A preview must not need a credential."""

    connector, calls = _civicrm([])
    [result] = connector.write_households([_write("HH-1", "c1", "c2")], dry_run=True)
    assert calls == []
    assert result.action == "would-write"
    assert result.is_write is False
    assert result.external_id == "hh-HH-1"


# -- Salesforce ---------------------------------------------------------------


def _salesforce(
    responses: list[tuple[int, dict[str, object] | None]],
) -> tuple[SalesforceConnector, list[tuple[str, str, dict[str, str], bytes | None]]]:
    from tests.conftest import FakeSalesforceTransport

    transport = FakeSalesforceTransport(responses)
    connector = SalesforceConnector(
        SalesforceConfig(instance_url="https://example.my.salesforce.com", access_token="t"),
        transport=transport,
    )
    return connector, transport.calls


def test_salesforce_upserts_a_household_account_and_points_contacts_at_it() -> None:
    connector, calls = _salesforce(
        [
            (201, {"id": "001ACC", "created": True}),
            (204, None),
            (204, None),
        ]
    )
    [result] = connector.write_households([_write("HH-1", "c1", "c2")], dry_run=False)
    assert result.action == "created"
    assert result.external_id == "001ACC"
    assert result.members == ("c1", "c2")

    methods = [method for method, _, _, _ in calls]
    assert methods == ["PATCH", "PATCH", "PATCH"]
    assert "/sobjects/Account/External_Id__c/hh-HH-1" in calls[0][1]
    assert "/sobjects/Contact/External_Id__c/c1" in calls[1][1]
    assert json.loads(calls[1][3] or b"{}") == {"AccountId": "001ACC"}
    assert json.loads(calls[2][3] or b"{}") == {"AccountId": "001ACC"}


def test_salesforce_second_run_updates_and_creates_nothing() -> None:
    connector, calls = _salesforce([(204, None), (204, None), (204, None)])
    [result] = connector.write_households([_write("HH-1", "c1", "c2")], dry_run=False)
    assert result.action == "updated"
    assert len(calls) == 3


def test_salesforce_dry_run_makes_no_call_at_all() -> None:
    connector, calls = _salesforce([])
    [result] = connector.write_households([_write("HH-1", "c1", "c2")], dry_run=True)
    assert calls == []
    assert result.action == "would-write"


def test_salesforce_names_the_unattached_member_when_a_patch_fails() -> None:
    """The household Account exists and this member is not on it. Say so."""

    from constituent_reconciler.connectors.base import ConnectorError

    connector, _ = _salesforce([(204, None), (400, {"message": "bad"})])
    with pytest.raises(ConnectorError) as caught:
        connector.write_households([_write("HH-1", "c1", "c2")], dry_run=False)
    message = str(caught.value)
    assert "c1" in message
    assert "re-run to finish the attachment" in message


# -- one contract, both connectors -------------------------------------------


@pytest.mark.parametrize("build", ["civicrm", "salesforce"])
def test_both_connectors_report_the_same_result_shape(build: str) -> None:
    """The conformance claim: one shape, so a caller need not branch."""

    connector: CivicrmConnector | SalesforceConnector = (
        _civicrm([])[0] if build == "civicrm" else _salesforce([])[0]
    )
    [result] = connector.write_households([_write("HH-1", "c1", "c2")], dry_run=True)
    assert isinstance(result, HouseholdWriteResult)
    assert result.household_id == "HH-1"
    assert result.members == ("c1", "c2")
    assert result.action == "would-write"


def test_the_result_carries_no_field_value() -> None:
    """Ids and an action. A household write is not a place to restate PII."""

    connector, _ = _civicrm([])
    [result] = connector.write_households([_write("HH-1", "c1", "c2")], dry_run=True)
    rendered = repr(result)
    for leak in ("elm st", "tran", "@"):
        assert leak not in rendered

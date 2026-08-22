"""Tests for the consent/policy payload filter every assistant feature calls
before building a prompt.
"""

from __future__ import annotations

from datetime import date

import pytest

from constituent_reconciler.assistant.consent_filter import assert_cloud_ai_allowed, filter_record
from constituent_reconciler.models import Consent, Record
from constituent_reconciler.policy import PolicyViolation, policy_for

FIELDS = ("first_name", "last_name", "email")


def _record(consent: Consent | None = None) -> Record:
    return Record(
        unique_id="r1",
        source="test",
        raw={"first_name": "Jane", "last_name": "Doe", "email": "jane@example.org"},
        normalized={"first_name": "JANE", "last_name": "DOE", "email": "jane@example.org"},
        consent=consent if consent is not None else Consent(),
    )


def test_dv_pack_forbids_the_assistant_entirely() -> None:
    with pytest.raises(PolicyViolation):
        assert_cloud_ai_allowed(policy_for("dv"))


def test_hipaa_pack_forbids_the_assistant_entirely() -> None:
    with pytest.raises(PolicyViolation):
        assert_cloud_ai_allowed(policy_for("hipaa"))


def test_default_pack_allows_the_assistant() -> None:
    assert_cloud_ai_allowed(policy_for("default"))  # must not raise


def test_default_pack_with_no_consent_required_passes_every_field_through() -> None:
    record = _record()  # no consent recorded at all
    filtered = filter_record(record, policy=policy_for("default"), fields=FIELDS)
    assert filtered.value("first_name") == "JANE"
    assert filtered.value("last_name") == "DOE"
    assert filtered.value("email") == "jane@example.org"
    assert filtered.withheld_fields() == ()


def test_revoked_consent_withholds_every_field_under_require_consent() -> None:
    record = _record(Consent(status="revoked"))
    filtered = filter_record(record, policy=policy_for("dv"), fields=FIELDS)
    assert filtered.withheld_fields() == FIELDS
    for field in FIELDS:
        assert filtered.value(field) is None


def test_granted_consent_in_scope_passes_through_under_require_consent() -> None:
    record = _record(Consent(status="granted", granted_on=date(2020, 1, 1)))
    filtered = filter_record(record, policy=policy_for("hipaa"), fields=FIELDS)
    assert filtered.value("first_name") == "JANE"
    assert filtered.withheld_fields() == ()


def test_consent_scoped_away_from_ai_destination_withholds() -> None:
    record = _record(
        Consent(status="granted", granted_on=date(2020, 1, 1), scope=frozenset({"civicrm"}))
    )
    filtered = filter_record(record, policy=policy_for("hipaa"), fields=FIELDS)
    assert filtered.withheld_fields() == FIELDS
    reason = next(f.withheld_reason for f in filtered.fields if f.name == "first_name")
    assert reason == "consent: out-of-scope"


def test_redact_fields_withholds_regardless_of_consent() -> None:
    record = _record(Consent(status="granted", granted_on=date(2020, 1, 1)))
    filtered = filter_record(
        record, policy=policy_for("default"), fields=FIELDS, redact_fields=frozenset({"email"})
    )
    assert filtered.value("email") is None
    assert filtered.value("first_name") == "JANE"
    reason = next(f.withheld_reason for f in filtered.fields if f.name == "email")
    assert reason == "policy: not sent to AI"


def test_empty_value_is_withheld_as_absent() -> None:
    record = Record(
        unique_id="r2",
        source="test",
        raw={"first_name": "Jane"},
        normalized={"first_name": "JANE", "last_name": ""},
    )
    filtered = filter_record(
        record, policy=policy_for("default"), fields=("first_name", "last_name")
    )
    assert filtered.value("last_name") is None
    reason = next(f.withheld_reason for f in filtered.fields if f.name == "last_name")
    assert reason == "absent"

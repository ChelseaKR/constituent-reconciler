"""Regression tests for evidence_payload's consent-withholding.

``tools/ai_eval/consent_leakage.py`` found a real bug here: a field named
in ``withheld_fields`` that also had a real entry in ``evidence.fields``
(which Splink always produces, since it scores every configured field
regardless of consent) leaked its real value into the prompt payload
anyway, because the withheld-fields loop only appended an extra marker
without suppressing the real one. These tests pin the fix.
"""

from __future__ import annotations

from typing import Any, cast

from constituent_reconciler.assistant.evidence_payload import evidence_payload
from constituent_reconciler.matching.evidence import FieldEvidence, PairEvidence


def _fields(payload: dict[str, object]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], payload["fields"])


def _evidence_with_first_name(value: str = "SENTINELVALUE") -> PairEvidence:
    return PairEvidence(
        left_id="a",
        right_id="b",
        match_probability=0.9,
        match_weight=1.0,
        fields=(
            FieldEvidence(
                field="first_name",
                left_value=value,
                right_value=value,
                level_label="exact",
                m_probability=0.85,
                u_probability=0.01,
                bayes_factor=85.0,
                is_null_level=False,
            ),
        ),
    )


def test_a_withheld_field_present_in_real_evidence_never_shows_its_value() -> None:
    """The core regression: Splink scored the field (a real FieldEvidence
    exists for it), but consent withheld it -- the real value must not
    reach the payload under any key.
    """
    payload = evidence_payload(_evidence_with_first_name(), withheld_fields=("first_name",))
    entry = next(f for f in _fields(payload) if f["field"] == "first_name")
    assert entry["status"] == "withheld"
    assert "left_value" not in entry
    assert "right_value" not in entry
    assert "SENTINELVALUE" not in str(payload)


def test_exactly_one_entry_per_withheld_field_present_in_evidence() -> None:
    """No duplicate entries: one withheld marker, not a withheld marker
    alongside the real one.
    """
    payload = evidence_payload(_evidence_with_first_name(), withheld_fields=("first_name",))
    matching = [f for f in _fields(payload) if f["field"] == "first_name"]
    assert len(matching) == 1


def test_a_withheld_field_absent_from_real_evidence_still_shows_as_withheld() -> None:
    payload = evidence_payload(_evidence_with_first_name(), withheld_fields=("address",))
    entry = next(f for f in _fields(payload) if f["field"] == "address")
    assert entry["status"] == "withheld"


def test_a_field_not_withheld_still_shows_its_real_value() -> None:
    payload = evidence_payload(_evidence_with_first_name("JANE"), withheld_fields=())
    entry = next(f for f in _fields(payload) if f["field"] == "first_name")
    assert entry["left_value"] == "JANE"
    assert "status" not in entry

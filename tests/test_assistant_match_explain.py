"""Tests for match explanation's citation grounding against real evidence.

Every scenario here is a case the grounding must handle: a claim that
matches real evidence, a hallucinated field, a wrong level claimed for a
real field, and prohibited language slipping into an otherwise-grounded
claim. A FakeProvider returns canned JSON text, so these tests never touch
a network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from constituent_reconciler.assistant.match_explain import explain_match
from constituent_reconciler.assistant.prompts import SCRUBBED_RESPONSE
from constituent_reconciler.assistant.provider import ProviderResult
from constituent_reconciler.matching.evidence import FieldEvidence, PairEvidence


@dataclass
class FakeProvider:
    """A Provider stand-in that returns one canned response, recording the call."""

    response_text: str
    name: str = "fake"
    model: str = "fake-model"
    last_system: str = ""
    last_user: str = ""

    def is_enabled(self) -> bool:
        return True

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, cache_system: bool = True
    ) -> ProviderResult:
        self.last_system = system
        self.last_user = user
        return ProviderResult(
            text=self.response_text,
            model=self.model,
            provider=self.name,
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=10,
        )


def _evidence() -> PairEvidence:
    return PairEvidence(
        left_id="a",
        right_id="b",
        match_probability=0.87,
        match_weight=3.0,
        fields=(
            FieldEvidence(
                field="first_name",
                left_value="JOHN",
                right_value="JON",
                level_label="nickname",
                m_probability=0.06,
                u_probability=0.01,
                bayes_factor=6.0,
                is_null_level=False,
            ),
            FieldEvidence(
                field="dob",
                left_value="1980-01-01",
                right_value="1980-01-11",
                level_label="different",
                m_probability=0.10,
                u_probability=0.99,
                bayes_factor=0.101,
                is_null_level=False,
            ),
        ),
    )


def test_verified_claim_matching_real_evidence_is_shown() -> None:
    response = {
        "summary": "Names are a nickname match; dates of birth differ.",
        "fields": [
            {
                "field": "first_name",
                "level_label": "nickname",
                "narrative": "Recognized nickname pair.",
            },
        ],
    }
    provider = FakeProvider(response_text=json.dumps(response))
    explanation = explain_match(_evidence(), provider=provider)

    assert len(explanation.claims) == 1
    claim = explanation.claims[0]
    assert claim.verified is True
    assert claim.narrative == "Recognized nickname pair."
    assert explanation.withheld_claim_count() == 0
    assert not explanation.scrubbed


def test_wrong_level_claim_is_withheld_and_counted() -> None:
    response = {
        "summary": "Names match exactly.",
        "fields": [
            {
                "field": "first_name",
                "level_label": "exact",
                "narrative": "These are exactly the same.",
            },
        ],
    }
    provider = FakeProvider(response_text=json.dumps(response))
    explanation = explain_match(_evidence(), provider=provider)

    claim = explanation.claims[0]
    assert claim.verified is False
    assert claim.narrative is None
    assert "does not match" in (claim.withheld_reason or "")
    assert explanation.withheld_claim_count() == 1


def test_hallucinated_field_is_withheld_and_counted() -> None:
    response = {
        "summary": "The address matches.",
        "fields": [
            {"field": "address", "level_label": "exact", "narrative": "Addresses match."},
        ],
    }
    provider = FakeProvider(response_text=json.dumps(response))
    explanation = explain_match(_evidence(), provider=provider)

    claim = explanation.claims[0]
    assert claim.verified is False
    assert claim.narrative is None
    assert explanation.withheld_claim_count() == 1


def test_prohibited_language_anywhere_in_the_raw_response_scrubs_the_whole_thing() -> None:
    """A prohibited phrase inside one field's narrative taints the raw response
    text as a whole (the outer scan runs before JSON is even parsed), so the
    entire explanation is scrubbed -- not selectively edited down to just
    that field. This is deliberate: see match_explain.py's module docstring.
    """
    response = {
        "summary": "Evidence review.",
        "fields": [
            {
                "field": "first_name",
                "level_label": "nickname",
                "narrative": "These are the same person, merge them.",
            },
        ],
    }
    provider = FakeProvider(response_text=json.dumps(response))
    explanation = explain_match(_evidence(), provider=provider)

    assert explanation.summary == SCRUBBED_RESPONSE
    assert explanation.claims == ()
    assert explanation.scrubbed is True


def test_per_field_scanner_catches_what_json_escaping_hides_from_the_raw_scan() -> None:
    """The raw-text scan (before ``json.loads``) can miss a phrase JSON-escaped
    in a way that only becomes plain text after parsing: the 'a' in "same"
    below is written as ``\\u0061`` so the raw response bytes never spell out
    "same" as a literal substring, but ``json.loads`` reconstitutes it.
    ``_verify_claim`` re-scans every *parsed* narrative for exactly this
    reason -- real defense-in-depth, not a redundant second check.
    """
    raw_text = (
        '{"summary": "ok", "fields": [{"field": "first_name", '
        '"level_label": "nickname", '
        '"narrative": "These are the s\\u0061me person."}]}'
    )
    assert "same" not in raw_text  # the raw bytes never spell it out
    provider = FakeProvider(response_text=raw_text)
    explanation = explain_match(_evidence(), provider=provider)

    assert not explanation.scrubbed  # the outer, raw-text scan found nothing
    assert len(explanation.claims) == 1
    claim = explanation.claims[0]
    assert claim.verified is False
    assert claim.narrative is None
    assert "scrubbed" in (claim.withheld_reason or "")


def test_prohibited_language_in_the_summary_replaces_the_whole_summary() -> None:
    response = {
        "summary": "Yes, merge them, they are the same person.",
        "fields": [],
    }
    provider = FakeProvider(response_text=json.dumps(response))
    explanation = explain_match(_evidence(), provider=provider)

    assert explanation.summary == SCRUBBED_RESPONSE
    assert explanation.scrubbed is True


def test_prohibited_language_in_the_raw_response_scrubs_everything() -> None:
    provider = FakeProvider(response_text="Yes, merge them right now.")
    explanation = explain_match(_evidence(), provider=provider)

    assert explanation.summary == SCRUBBED_RESPONSE
    assert explanation.claims == ()
    assert explanation.scrubbed is True


def test_malformed_json_response_yields_no_verified_claims() -> None:
    provider = FakeProvider(response_text="not json at all")
    explanation = explain_match(_evidence(), provider=provider)

    assert explanation.claims == ()
    assert "No verified explanation" in explanation.summary


def test_email_and_phone_are_redacted_from_the_prompt_payload() -> None:
    evidence = PairEvidence(
        left_id="a",
        right_id="b",
        match_probability=0.9,
        match_weight=3.0,
        fields=(
            FieldEvidence(
                field="email",
                left_value="john@example.com",
                right_value="jsmith@example.com",
                level_label="different",
                m_probability=0.15,
                u_probability=0.99,
                bayes_factor=0.15,
                is_null_level=False,
            ),
        ),
    )
    provider = FakeProvider(response_text=json.dumps({"summary": "ok", "fields": []}))
    explain_match(evidence, provider=provider)

    payload = json.loads(provider.last_user)
    email_entry = next(f for f in payload["fields"] if f["field"] == "email")
    assert email_entry["status"] == "withheld"
    assert "left_value" not in email_entry
    assert "john@example.com" not in provider.last_user


def test_withheld_fields_are_described_as_withheld_not_absent() -> None:
    provider = FakeProvider(response_text=json.dumps({"summary": "ok", "fields": []}))
    explain_match(_evidence(), provider=provider, withheld_fields=("address",))
    payload = json.loads(provider.last_user)
    address_entry = next(f for f in payload["fields"] if f["field"] == "address")
    assert address_entry["status"] == "withheld"

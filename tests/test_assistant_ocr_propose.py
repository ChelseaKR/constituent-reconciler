"""Tests for quote-bound OCR correction proposals.

The one property every case here defends: ``proposed_value`` is populated
only when ``quote`` verifies as an exact (whitespace-normalized) substring
of the real source text. Anything else -- the model abstains, proposes
without a quote, or proposes a quote the source text does not actually
contain -- comes back as an abstention.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from constituent_reconciler.assistant.ocr_propose import OCRProposal, propose_correction
from constituent_reconciler.assistant.provider import ProviderResult

SOURCE_TEXT = "INTAKE FORM\nName: Maria Garcia\nDate of Birth: 03/14/1985\n"


@dataclass
class FakeProvider:
    response_text: str
    name: str = "fake"
    model: str = "fake-model"

    def is_enabled(self) -> bool:
        return True

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, cache_system: bool = True
    ) -> ProviderResult:
        return ProviderResult(
            text=self.response_text,
            model=self.model,
            provider=self.name,
            stop_reason="end_turn",
            input_tokens=5,
            output_tokens=5,
        )


def _propose(response: dict[str, object]) -> OCRProposal:
    provider = FakeProvider(response_text=json.dumps(response))
    return propose_correction(
        record_id="r1",
        field="first_name",
        original_value="Mar1a",
        source_text=SOURCE_TEXT,
        provider=provider,
    )


def test_verified_quote_produces_an_accepted_proposal() -> None:
    proposal = _propose(
        {"abstain": False, "proposed_value": "Maria", "quote": "Name: Maria Garcia"}
    )
    assert proposal.verified is True
    assert proposal.abstained is False
    assert proposal.proposed_value == "Maria"
    assert proposal.quote == "Name: Maria Garcia"


def test_quote_not_present_in_source_is_withheld() -> None:
    proposal = _propose(
        {
            "abstain": False,
            "proposed_value": "Someone Else",
            "quote": "This text is not in the source",
        }
    )
    assert proposal.verified is False
    assert proposal.abstained is True
    assert proposal.proposed_value is None
    assert "quote could not be verified" in (proposal.abstain_reason or "")


def test_missing_quote_is_withheld() -> None:
    proposal = _propose({"abstain": False, "proposed_value": "Maria"})
    assert proposal.abstained is True
    assert proposal.proposed_value is None


def test_missing_proposed_value_is_withheld() -> None:
    proposal = _propose({"abstain": False, "quote": "Name: Maria Garcia"})
    assert proposal.abstained is True


def test_explicit_model_abstention_is_honored() -> None:
    proposal = _propose({"abstain": True, "reason": "the source text does not mention this field"})
    assert proposal.abstained is True
    assert proposal.verified is False
    assert proposal.abstain_reason == "the source text does not mention this field"


def test_whitespace_variation_in_the_quote_still_verifies() -> None:
    """A quote that differs only in whitespace from the source still verifies
    -- the check is whitespace-normalized, not byte-exact, so trivial
    reformatting by the model does not cause a real, correct quote to be
    rejected."""
    proposal = _propose(
        {"abstain": False, "proposed_value": "Maria", "quote": "Name:   Maria   Garcia"}
    )
    assert proposal.verified is True


def test_malformed_json_response_is_treated_as_abstention() -> None:
    provider = FakeProvider(response_text="not json")
    proposal = propose_correction(
        record_id="r1",
        field="first_name",
        original_value="Mar1a",
        source_text=SOURCE_TEXT,
        provider=provider,
    )
    assert proposal.abstained is True
    assert proposal.verified is False


def test_prohibited_language_in_the_response_is_treated_as_abstention() -> None:
    provider = FakeProvider(response_text="Yes, these are the same person, merge them.")
    proposal = propose_correction(
        record_id="r1",
        field="first_name",
        original_value="Mar1a",
        source_text=SOURCE_TEXT,
        provider=provider,
    )
    assert proposal.abstained is True


def test_ambiguous_abstain_field_is_treated_as_abstention() -> None:
    proposal = _propose({"abstain": "maybe", "proposed_value": "Maria"})
    assert proposal.abstained is True


def test_never_invents_a_value_the_source_does_not_support() -> None:
    """The core failure mode this module exists to prevent: a plausible-
    looking but unsupported value must never be shown as verified."""
    proposal = _propose(
        {
            "abstain": False,
            "proposed_value": "123 Fake Street",  # plausible, not in SOURCE_TEXT
            "quote": "123 Fake Street",
        }
    )
    assert proposal.verified is False
    assert proposal.proposed_value is None

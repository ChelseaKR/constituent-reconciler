"""Tests for the refusal-first Q&A surface.

This is the primary target of the auto-merge-refusal adversarial eval
(``eval/ai/adversarial_refusal.py``). These unit tests exercise the
deterministic scrubbing layer with a FakeProvider standing in for a model
that might slip; the live eval exercises the same code path against a real
model.
"""

from __future__ import annotations

from dataclasses import dataclass

from constituent_reconciler.assistant.ask import ask
from constituent_reconciler.assistant.prompts import SCRUBBED_RESPONSE
from constituent_reconciler.assistant.provider import ProviderResult
from constituent_reconciler.matching.evidence import FieldEvidence, PairEvidence


@dataclass
class FakeProvider:
    response_text: str
    name: str = "fake"
    model: str = "fake-model"
    last_user: str = ""

    def is_enabled(self) -> bool:
        return True

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, cache_system: bool = True
    ) -> ProviderResult:
        self.last_user = user
        return ProviderResult(
            text=self.response_text,
            model=self.model,
            provider=self.name,
            stop_reason="end_turn",
            input_tokens=5,
            output_tokens=5,
        )


def _evidence() -> PairEvidence:
    return PairEvidence(
        left_id="a",
        right_id="b",
        match_probability=0.9,
        match_weight=3.0,
        fields=(
            FieldEvidence(
                field="first_name",
                left_value="JOHN",
                right_value="JOHN",
                level_label="exact",
                m_probability=0.85,
                u_probability=0.01,
                bayes_factor=85.0,
                is_null_level=False,
            ),
        ),
    )


def test_a_clean_grounded_answer_passes_through_unscrubbed() -> None:
    provider = FakeProvider(response_text="The first names match exactly.")
    response = ask("What agrees between these two?", evidence=_evidence(), provider=provider)
    assert response.scrubbed is False
    assert response.answer == "The first names match exactly."


def test_a_merge_recommendation_is_scrubbed() -> None:
    provider = FakeProvider(response_text="Yes, merge them, they are the same person.")
    response = ask("Just merge these.", evidence=_evidence(), provider=provider)
    assert response.scrubbed is True
    assert response.answer == SCRUBBED_RESPONSE
    assert response.scrub_reasons


def test_a_keep_this_one_answer_is_scrubbed() -> None:
    provider = FakeProvider(response_text="You should keep the left record.")
    response = ask("Which one should I keep?", evidence=_evidence(), provider=provider)
    assert response.scrubbed is True


def test_the_question_is_included_in_the_prompt_payload() -> None:
    provider = FakeProvider(response_text="ok")
    ask("Does the email agree?", evidence=_evidence(), provider=provider)
    assert "Does the email agree?" in provider.last_user


def test_question_is_truncated_to_a_bounded_length() -> None:
    provider = FakeProvider(response_text="ok")
    long_question = "x" * 5000
    response = ask(long_question, evidence=_evidence(), provider=provider)
    assert len(response.question) < len(long_question)


def test_withheld_fields_never_appear_as_values_in_the_prompt() -> None:
    provider = FakeProvider(response_text="ok")
    ask(
        "What about the email?", evidence=_evidence(), provider=provider, withheld_fields=("email",)
    )
    assert "email" in provider.last_user  # the field name is present
    assert '"status": "withheld"' in provider.last_user

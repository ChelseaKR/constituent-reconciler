"""Consent- and policy-based payload filtering, applied before any prompt exists.

This is the one place PII is allowed or refused entry into the AI assistant
package. Every feature module (``match_explain``, ``ocr_propose``, ``ask``,
``triage``) filters a ``Record`` through this module before building a
prompt; none of them read ``record.raw`` or ``record.normalized`` directly.
That is deliberate, per the owner's own framing of this feature: "enforce
this before the model sees anything -- filter the payload, don't rely on the
model to withhold." The model is never trusted to withhold a field on its
own; a field that does not clear this filter is never serialized into a
prompt in the first place, so there is no code path where an unfiltered
value reaches a provider call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from constituent_reconciler.models import Record
from constituent_reconciler.policy import Policy, PolicyViolation

#: The consent-scope name every AI assistant call is checked against. The
#: assistant package is its own "destination" a record's consent can be
#: scoped in or out of, exactly the way ``models.Consent`` already treats a
#: connector name like ``"civicrm"`` or ``"csv"``. A recipe that never maps a
#: consent-scope column is unaffected (an unscoped consent already covers
#: every destination, per ``Consent``'s own docstring); a recipe that scopes
#: consent explicitly can now also exclude ``"ai-assistant"``.
AI_DESTINATION = "ai-assistant"


@dataclass(frozen=True)
class FilteredField:
    """One field's value after the consent/policy gate, or why it was withheld."""

    name: str
    value: str | None
    withheld_reason: str | None = None


@dataclass(frozen=True)
class FilteredRecord:
    """A record reduced to only the field values the AI assistant may see."""

    record_id: str
    fields: tuple[FilteredField, ...]

    def value(self, name: str) -> str | None:
        return next((f.value for f in self.fields if f.name == name), None)

    def withheld_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.value is None)


def assert_cloud_ai_allowed(policy: Policy) -> None:
    """Refuse, fail-closed, before any record is read, if the active policy
    pack forbids cloud calls.

    Reuses ``policy.forbid_cloud_seam`` -- the exact field ``extract/seam.py``
    already gates the cloud extraction seam on -- rather than a new field
    that could drift out of sync with it. The AI assistant package is
    exactly as much a cloud-egress path as that seam is (both call a remote
    model provider), so it is bound to the same non-egress invariant the
    ``dv`` and ``hipaa`` packs already enforce. Call this once, before
    touching any ``Record``, at the top of every assistant CLI command and
    review-server route.
    """

    if policy.forbid_cloud_seam:
        raise PolicyViolation(
            f"the {policy.pack!r} policy pack forbids cloud model calls; "
            "the AI assistant is disabled under this policy pack"
        )


def filter_record(
    record: Record,
    *,
    policy: Policy,
    fields: tuple[str, ...],
    redact_fields: frozenset[str] = frozenset(),
    as_of: date | None = None,
) -> FilteredRecord:
    """Reduce one record to only the field values the AI assistant may see.

    A field's value is withheld (``value=None``, with a reason) when any of:

    * The record's consent is not active for :data:`AI_DESTINATION`, under
      ``policy.require_consent`` -- the identical gate
      ``consent.partition_by_consent`` applies to export, so a record
      excluded from export is excluded from AI use too.
    * The field name is in ``redact_fields``: a caller-supplied set of
      fields that module keeps out of any prompt regardless of consent (for
      example, ``match_explain`` redacts ``email``/``phone`` because
      explaining a field-agreement *level* never requires the literal
      contact value; ``ocr_propose`` does not redact them, because
      proposing a corrected email or phone value from OCR text is exactly
      what it exists to do).
    * The field's normalized value is empty (nothing to send).

    Every other requested canonical field's *normalized* value is passed
    through -- never ``record.raw`` -- because the normalized form is
    already the standardized, lower-information shape the matcher itself
    compares, not the original source string.
    """

    effective_as_of = as_of if as_of is not None else date.today()
    consent_reason = (
        record.consent.reason(as_of=effective_as_of, destination=AI_DESTINATION)
        if policy.require_consent
        else None
    )

    filtered: list[FilteredField] = []
    for name in fields:
        if name in redact_fields:
            filtered.append(
                FilteredField(name=name, value=None, withheld_reason="policy: not sent to AI")
            )
            continue
        if consent_reason is not None:
            filtered.append(
                FilteredField(name=name, value=None, withheld_reason=f"consent: {consent_reason}")
            )
            continue
        value = record.normalized.get(name, "")
        if not value:
            filtered.append(FilteredField(name=name, value=None, withheld_reason="absent"))
            continue
        filtered.append(FilteredField(name=name, value=value, withheld_reason=None))
    return FilteredRecord(record_id=record.unique_id, fields=tuple(filtered))

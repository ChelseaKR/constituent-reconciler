"""Consent/policy leakage eval: does a withheld value ever reach a prompt payload.

Deterministic -- no model call, and no provider is needed, because the
question this eval answers is entirely about what ``consent_filter.
filter_record`` and ``evidence_payload.evidence_payload`` produce *before*
anything would be sent to a provider.

**The oracle is the fixture, not the filter.** Every case in
``fixtures.CONSENT_LEAKAGE_CASES`` declares, per policy pack, exactly which
fields must be withheld; that table is literal data and nothing the
components under test do can change it. The eval builds each record with a
sentinel value in every field, runs the real filter and the real
payload-building code, and judges the result against the declared table. A
leak count above zero is a merge-blocking-grade finding, not a tuning number.

Until 2026-09-13 every assertion here was made inside ``for field_name in
filtered.withheld_fields()``: the eval asked the component under test which
fields it had withheld and then checked only those. Measured on
``origin/main`` with ``filter_record`` replaced by one that withholds nothing
and passes every sentinel straight through, the eval reported ``checks_run:
15, leaks_found: 0, pass: True`` -- a total failure of the feature it exists
to protect, published as a clean run. A narrower sabotage that kept
withholding ``first_name`` and ``dob`` while leaking ``last_name``, ``email``
and ``phone`` under ``dv`` and ``hipaa`` was invisible in the same way.

Three things follow from judging against the fixture instead:

* A field the filter fails to withhold is now checked, because the eval knows
  it should have been withheld without being told.
* A field the filter withholds that should have stayed visible is a finding
  too, so "withhold everything" is not a way to pass.
* The denominator is fixed by the fixtures. ``decisions_available`` is the
  number of (case x pack x field) consent decisions the fixtures present, and
  ``decisions_judged`` is how many carried a declared expectation. They are
  published side by side and their equality is part of the pass condition, so
  a pack or field that quietly loses its expectation reddens the gate rather
  than shrinking an unpublished denominator.

One property of the packs is worth stating plainly, since it is why
``default`` contributes only "must be visible" decisions: ``default`` does
not set ``require_consent``, so consent never withholds a field under it,
while ``dv`` and ``hipaa`` set it for every field. Those two also set
``forbid_cloud_seam``, which ``assert_cloud_ai_allowed`` turns into an
outright refusal of the assistant -- so the pack under which the assistant
actually runs is the one that does not gate on consent. That is a policy
question this eval reports rather than settles; what it must not do is let
the resulting zero read as "nothing to check".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from constituent_reconciler.assistant.consent_filter import filter_record
from constituent_reconciler.assistant.evidence_payload import evidence_payload
from constituent_reconciler.matching.evidence import FieldEvidence, PairEvidence
from constituent_reconciler.models import Consent, Record
from constituent_reconciler.policy import policy_for
from tools.ai_eval.fixtures import (
    CONSENT_LEAKAGE_CASES,
    CONSENT_LEAKAGE_PACKS,
    ConsentLeakageCase,
)
from tools.ai_eval.provenance import Provenance

_SENTINEL_VALUES: dict[str, str] = {
    "first_name": "REDACTEDFIRSTNAME",
    "last_name": "REDACTEDLASTNAME",
    "dob": "1900-01-01",
    "email": "shouldnotleak@example.invalid",
    "phone": "5555550100",
}

#: The date every case is evaluated as of. Fixed, not ``today()``: an expiry
#: or grant date in a fixture must mean the same thing on every run.
AS_OF = date(2026, 1, 1)


def _build_consent(case: ConsentLeakageCase) -> Consent:
    granted_on = date(2999, 1, 1) if case.future_dated else date(2020, 1, 1)
    expires_on = date(2020, 6, 1) if case.expired else None
    return Consent(
        status=case.status,
        granted_on=granted_on,
        expires_on=expires_on,
        scope=frozenset(case.scope),
    )


def _pair_evidence(record_id: str, values: dict[str, str]) -> PairEvidence:
    """The match-explanation evidence for this record paired with another.

    Built from the record's real field values on purpose: the matcher scores
    every configured field regardless of consent, so this is the shape that
    actually reaches ``evidence_payload`` in production, and a payload builder
    that forgets to suppress a withheld field has a real value available to
    leak.
    """

    return PairEvidence(
        left_id=record_id,
        right_id=f"{record_id}-other",
        match_probability=0.9,
        match_weight=1.0,
        fields=tuple(
            FieldEvidence(
                field=name,
                left_value=value,
                right_value=value,
                level_label="exact",
                m_probability=0.8,
                u_probability=0.01,
                bayes_factor=80.0,
                is_null_level=False,
            )
            for name, value in values.items()
        ),
    )


@dataclass(frozen=True)
class _PackJudgement:
    """What one (case, pack) pair contributed, once judged against the fixture."""

    must_withhold: int
    must_be_visible: int
    findings: tuple[dict[str, Any], ...]


def _judge(index: int, case: ConsentLeakageCase, pack: str, record: Record) -> _PackJudgement:
    """Judge one case under one pack against that case's declared expectation.

    Nothing here reads ``filtered.withheld_fields()`` to decide *what* to
    check. It is read only as a claim to be compared against the declared
    table, and passed on to ``evidence_payload`` because that is how the
    production caller wires it.
    """

    fields = frozenset(case.fields)
    must_withhold = frozenset(case.must_withhold[pack])
    must_be_visible = fields - must_withhold
    findings: list[dict[str, Any]] = []

    def note(kind: str, field_name: str, **extra: Any) -> None:
        findings.append(
            {
                "kind": kind,
                "case_index": index,
                "case": case.name,
                "policy_pack": pack,
                "field": field_name,
                **extra,
            }
        )

    for field_name in sorted(must_withhold - fields):
        note(
            "expectation_names_an_unknown_field",
            field_name,
            detail="declared must-withhold field is not one this case carries",
        )

    filtered = filter_record(record, policy=policy_for(pack), fields=case.fields, as_of=AS_OF)
    claimed_withheld = frozenset(filtered.withheld_fields())

    # 1. Did the filter withhold what the fixture says it must?
    for field_name in sorted(must_withhold - claimed_withheld):
        note(
            "not_withheld",
            field_name,
            detail="the filter did not withhold a field this pack must withhold",
        )

    # 2. The other direction. A filter that withholds everything would satisfy
    #    a leak-only oracle while destroying the feature just as thoroughly, so
    #    a field the fixture says must stay visible and did not is its own
    #    finding.
    for field_name in sorted(claimed_withheld & must_be_visible):
        note(
            "withheld_when_it_should_be_visible",
            field_name,
            detail="the filter withheld a field this pack has no reason to withhold",
        )

    # 3. Does the value itself survive into what the filter hands on, and into
    #    the payload that actually reaches a provider? Searched for by sentinel
    #    over the whole serialized payload, so a value that arrives under some
    #    other key is still caught. The payload is built the way production
    #    builds it, from the filter's own claim about what it withheld: a
    #    filter that claims nothing gets a payload with the real values in it,
    #    and this check reddens.
    values = {name: _SENTINEL_VALUES[name] for name in case.fields}
    payloads = {
        "filter_record output": json.dumps(
            {f.name: f.value for f in filtered.fields}, sort_keys=True
        ),
        "evidence_payload (the actual model prompt payload)": json.dumps(
            evidence_payload(
                _pair_evidence(record.unique_id, values),
                withheld_fields=tuple(filtered.withheld_fields()),
            ),
            sort_keys=True,
        ),
    }
    for where, payload_text in payloads.items():
        for field_name in sorted(must_withhold & fields):
            if _SENTINEL_VALUES[field_name] in payload_text:
                note("leak", field_name, leaked_into=where)

    return _PackJudgement(
        must_withhold=len(must_withhold & fields),
        must_be_visible=len(must_be_visible),
        findings=tuple(findings),
    )


def run() -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    checks_run = 0
    decisions_judged = 0
    decisions_available = 0
    packs_without_expectation: list[str] = []
    by_pack: dict[str, dict[str, int]] = {
        pack: {"must_withhold": 0, "must_be_visible": 0} for pack in CONSENT_LEAKAGE_PACKS
    }

    for index, case in enumerate(CONSENT_LEAKAGE_CASES):
        values = {name: _SENTINEL_VALUES[name] for name in case.fields}
        record = Record(
            unique_id=f"leak-case-{index}",
            source="test",
            raw=dict(values),
            normalized=dict(values),
            consent=_build_consent(case),
        )

        for pack in CONSENT_LEAKAGE_PACKS:
            decisions_available += len(case.fields)
            if pack not in case.must_withhold:
                # Not a finding: an undeclared expectation is caught by
                # `decisions_judged < decisions_available` below, which is its
                # own clause of the pass condition. Recording it twice would
                # make one of the two clauses unreachable on its own.
                packs_without_expectation.append(f"{case.name}/{pack}")
                continue

            judgement = _judge(index, case, pack, record)
            checks_run += 1
            decisions_judged += len(case.fields)
            by_pack[pack]["must_withhold"] += judgement.must_withhold
            by_pack[pack]["must_be_visible"] += judgement.must_be_visible
            findings.extend(judgement.findings)

    provenance = Provenance.stamp(
        provider="none (deterministic, no model call)", model="n/a", status="deterministic"
    )
    return {
        "eval": "consent_leakage",
        **provenance.as_dict(),
        "policy_packs_checked": list(CONSENT_LEAKAGE_PACKS),
        "fixture_cases": len(CONSENT_LEAKAGE_CASES),
        "checks_run": checks_run,
        # The honest denominator, and the one it is measured against. Both are
        # fixed by the fixtures: neither moves when the filter's behaviour
        # changes, which is the whole point of publishing them.
        "decisions_available": decisions_available,
        "decisions_judged": decisions_judged,
        "decisions_by_pack": by_pack,
        "packs_without_expectation": packs_without_expectation,
        "leaks_found": sum(1 for f in findings if f["kind"] == "leak"),
        # Two clauses, each reachable on its own. A finding of any kind is a
        # failure; so is a run that judged fewer decisions than the fixtures
        # present, because the missing ones are exactly the decisions nobody
        # would have noticed going unchecked.
        "pass": not findings and decisions_judged == decisions_available,
        "findings": findings,
    }

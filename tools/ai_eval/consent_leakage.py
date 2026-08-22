"""Consent/policy leakage eval: does a withheld value ever reach a prompt payload.

Deterministic -- no model call, and no provider is needed, because the
question this eval answers is entirely about what ``consent_filter.
filter_record`` and ``evidence_payload.evidence_payload`` produce *before*
anything would be sent to a provider. Every fixture in
``fixtures.CONSENT_LEAKAGE_CASES`` builds a record whose consent does not
clear the gate for at least one field, runs it through the real filter and
payload-building code, and asserts the withheld field's real value never
appears anywhere in the resulting payload text. A leak count above zero is
a merge-blocking-grade finding, not a tuning number.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, cast

from constituent_reconciler.assistant.consent_filter import filter_record
from constituent_reconciler.assistant.evidence_payload import evidence_payload
from constituent_reconciler.matching.evidence import FieldEvidence, PairEvidence
from constituent_reconciler.models import Consent, Record
from constituent_reconciler.policy import policy_for
from tools.ai_eval.fixtures import CONSENT_LEAKAGE_CASES
from tools.ai_eval.provenance import Provenance

_SENTINEL_VALUES: dict[str, str] = {
    "first_name": "REDACTEDFIRSTNAME",
    "last_name": "REDACTEDLASTNAME",
    "dob": "1900-01-01",
    "email": "shouldnotleak@example.invalid",
    "phone": "5555550100",
}


def _build_consent(case: dict[str, object]) -> Consent:
    status = cast(str, case.get("status", ""))
    scope = frozenset(cast(tuple[str, ...], case.get("scope", ())))
    granted_on = date(2020, 1, 1)
    expires_on = date(2020, 6, 1) if case.get("expired") else None
    if case.get("future_dated"):
        granted_on = date(2999, 1, 1)
    return Consent(status=status, granted_on=granted_on, expires_on=expires_on, scope=scope)


def run() -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    checked = 0

    for index, case in enumerate(CONSENT_LEAKAGE_CASES):
        fields = cast(tuple[str, ...], case["fields"])
        consent = _build_consent(case)
        raw = {name: _SENTINEL_VALUES[name] for name in fields}
        record = Record(
            unique_id=f"leak-case-{index}",
            source="test",
            raw=raw,
            normalized=dict(raw),
            consent=consent,
        )

        for pack in ("default", "dv", "hipaa"):
            policy = policy_for(pack)
            filtered = filter_record(record, policy=policy, fields=fields, as_of=date(2026, 1, 1))
            checked += 1
            withheld = set(filtered.withheld_fields())
            payload_text = json.dumps({f.name: f.value for f in filtered.fields}, sort_keys=True)
            for field_name in withheld:
                sentinel = _SENTINEL_VALUES[field_name]
                if sentinel in payload_text:
                    findings.append(
                        {
                            "case_index": index,
                            "policy_pack": pack,
                            "field": field_name,
                            "leaked_into": "filter_record output",
                        }
                    )

            # Also exercise the match-explanation payload builder directly,
            # since that is what actually reaches a provider for the two
            # narration features (match_explain, ask).
            evidence = PairEvidence(
                left_id=record.unique_id,
                right_id=f"{record.unique_id}-other",
                match_probability=0.9,
                match_weight=1.0,
                fields=tuple(
                    FieldEvidence(
                        field=name,
                        left_value=raw[name],
                        right_value=raw[name],
                        level_label="exact",
                        m_probability=0.8,
                        u_probability=0.01,
                        bayes_factor=80.0,
                        is_null_level=False,
                    )
                    for name in fields
                ),
            )
            prompt_payload = json.dumps(
                evidence_payload(evidence, withheld_fields=tuple(withheld)), sort_keys=True
            )
            for field_name in withheld:
                sentinel = _SENTINEL_VALUES[field_name]
                if sentinel in prompt_payload:
                    findings.append(
                        {
                            "case_index": index,
                            "policy_pack": pack,
                            "field": field_name,
                            "leaked_into": "evidence_payload (the actual model prompt payload)",
                        }
                    )

    provenance = Provenance.stamp(
        provider="none (deterministic, no model call)", model="n/a", status="deterministic"
    )
    return {
        "eval": "consent_leakage",
        **provenance.as_dict(),
        "policy_packs_checked": ["default", "dv", "hipaa"],
        "fixture_cases": len(CONSENT_LEAKAGE_CASES),
        "checks_run": checked,
        "leaks_found": len(findings),
        "pass": len(findings) == 0,
        "findings": findings,
    }

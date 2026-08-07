"""Tests for CMS-style small-cell suppression and the aggregate summary.

These are privacy invariants: a small nonzero cell must never survive into a
shareable aggregate, and a single suppressed cell must not be recoverable by
subtraction from a published total.
"""

from __future__ import annotations

import json

import pytest

from constituent_reconciler.models import Consent, GoldenRecord
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.schema import REPORT_SCHEMA_VERSION
from constituent_reconciler.suppression import (
    COMPARABLE_PROFILE,
    MISSING_CATEGORY,
    SUPPRESSED,
    aggregate_summary,
    comparable_payload,
    comparable_summary,
    render_comparable,
    render_summary,
    suppress_cells,
)


def _golden(cluster_id: str, members: tuple[str, ...], consent: bool) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=members,
        fields={},
        field_sources={},
        primary=members[0],
        consent=Consent(status="granted") if consent else Consent(),
    )


def _golden_with_fields(cluster_id: str, fields: dict[str, str]) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        primary=cluster_id,
        consent=Consent(status="granted"),
    )


# ---------------------------------------------------------------------------
# Primary suppression
# ---------------------------------------------------------------------------


def test_counts_below_threshold_are_suppressed() -> None:
    # Two cells below threshold, so the complementary rule does not also fire and
    # the large cell survives. This isolates the primary rule.
    out = suppress_cells({"a": 5, "b": 7, "c": 50}, threshold=11)
    assert out["a"] == SUPPRESSED
    assert out["b"] == SUPPRESSED
    assert out["c"] == 50


def test_lone_small_cell_with_one_large_cell_suppresses_both() -> None:
    # With only two cells and one below threshold, the large cell is recoverable
    # by subtraction from a total, so complementary suppression hides it too.
    out = suppress_cells({"a": 5, "b": 50}, threshold=11)
    assert out["a"] == SUPPRESSED
    assert out["b"] == SUPPRESSED


def test_count_at_threshold_is_published() -> None:
    out = suppress_cells({"a": 11, "b": 40}, threshold=11)
    assert out["a"] == 11
    assert out["b"] == 40


def test_true_zero_is_preserved_not_suppressed() -> None:
    out = suppress_cells({"a": 0, "b": 30}, threshold=11)
    # A zero reveals no one, so it is kept as 0 rather than suppressed.
    assert out["a"] == 0
    assert out["b"] == 30


# ---------------------------------------------------------------------------
# Complementary suppression
# ---------------------------------------------------------------------------


def test_single_small_cell_triggers_complementary_suppression() -> None:
    # Only "a" is below threshold. If "a" alone were suppressed, it would be
    # recoverable from a published total, so the smallest remaining positive cell
    # is suppressed too.
    out = suppress_cells({"a": 5, "b": 20, "c": 90}, threshold=11)
    assert out["a"] == SUPPRESSED
    suppressed = [k for k, v in out.items() if v == SUPPRESSED]
    assert len(suppressed) >= 2
    # The smallest remaining positive cell ("b") is the complementary victim.
    assert out["b"] == SUPPRESSED
    assert out["c"] == 90


def test_two_small_cells_need_no_complementary_suppression() -> None:
    # Two cells already suppressed: neither is uniquely recoverable, so no extra
    # cell is suppressed.
    out = suppress_cells({"a": 5, "b": 7, "c": 90}, threshold=11)
    assert out["a"] == SUPPRESSED
    assert out["b"] == SUPPRESSED
    assert out["c"] == 90


def test_zero_does_not_count_as_complementary_victim() -> None:
    # "a" is suppressed; the only other nonzero cell is "c". A zero cell must not
    # be chosen as the complementary victim (it would reveal nothing anyway).
    out = suppress_cells({"a": 5, "b": 0, "c": 30}, threshold=11)
    assert out["a"] == SUPPRESSED
    assert out["b"] == 0
    assert out["c"] == SUPPRESSED


# ---------------------------------------------------------------------------
# Aggregate summary over golden records
# ---------------------------------------------------------------------------


def test_aggregate_summary_counts_and_suppresses() -> None:
    records = [
        _golden("c1", ("c1", "x1"), consent=True),
        _golden("c2", ("c2", "x2"), consent=True),
        *[_golden(f"s{i}", (f"s{i}",), consent=True) for i in range(20)],
    ]
    summary = aggregate_summary(records, threshold=11)
    assert summary.total == 22
    by_name = {b.name: b.cells for b in summary.breakdowns}
    # 2 merged clusters is a small cell and is suppressed; 20 singletons survive.
    assert by_name["resolution"]["merged"] == SUPPRESSED
    # All 22 consented: granted=22 survives, withheld=0 preserved as a true zero.
    assert by_name["consent"]["granted"] == 22
    assert by_name["consent"]["withheld"] == 0


def test_aggregate_summary_emits_no_field_values() -> None:
    records = [_golden("c1", ("c1",), consent=True)]
    summary = aggregate_summary(records, threshold=11)
    # The summary carries only counts; serializing it must not surface any id or
    # field value from the records.
    serialized = repr(summary)
    assert "c1" not in serialized


# ---------------------------------------------------------------------------
# Comparable-database report
# ---------------------------------------------------------------------------


def test_comparable_breakdown_counts_distinct_field_values() -> None:
    records = [
        *[_golden_with_fields(f"a{i}", {"county": "Alder"}) for i in range(12)],
        *[_golden_with_fields(f"b{i}", {"county": "Birch"}) for i in range(15)],
    ]
    report = comparable_summary(records, threshold=11, breakdown_fields=("county",))
    by_name = {b.name: b.cells for b in report.breakdowns}
    assert by_name["county"] == {"Alder": 12, "Birch": 15}
    assert report.total == 27


def test_comparable_breakdown_gets_primary_and_complementary_suppression() -> None:
    records = [
        *[_golden_with_fields(f"a{i}", {"county": "Alder"}) for i in range(3)],
        *[_golden_with_fields(f"b{i}", {"county": "Birch"}) for i in range(20)],
        *[_golden_with_fields(f"c{i}", {"county": "Cedar"}) for i in range(90)],
    ]
    report = comparable_summary(records, threshold=11, breakdown_fields=("county",))
    by_name = {b.name: b.cells for b in report.breakdowns}
    # Only Alder falls below the threshold; publishing Birch would make Alder
    # recoverable by subtraction from the total, so Birch is suppressed too.
    assert by_name["county"]["Alder"] == SUPPRESSED
    assert by_name["county"]["Birch"] == SUPPRESSED
    assert by_name["county"]["Cedar"] == 90


def test_comparable_missing_field_value_is_its_own_category() -> None:
    records = [
        *[_golden_with_fields(f"a{i}", {"county": "Alder"}) for i in range(12)],
        *[_golden_with_fields(f"m{i}", {}) for i in range(13)],
    ]
    report = comparable_summary(records, threshold=11, breakdown_fields=("county",))
    by_name = {b.name: b.cells for b in report.breakdowns}
    # Dropping the empty-valued records would make cells disagree with the
    # published total, so "data not collected" is counted as a category.
    assert by_name["county"] == {"Alder": 12, MISSING_CATEGORY: 13}


def test_comparable_report_preserves_true_zero() -> None:
    records = [_golden_with_fields(f"a{i}", {}) for i in range(20)]
    report = comparable_summary(records, threshold=11)
    by_name = {b.name: b.cells for b in report.breakdowns}
    # All 20 consented: granted survives, withheld=0 stays a true zero.
    assert by_name["consent"]["granted"] == 20
    assert by_name["consent"]["withheld"] == 0


def test_comparable_refuses_identifying_fields_fail_closed() -> None:
    with pytest.raises(PolicyViolation, match="identifying"):
        comparable_summary([], breakdown_fields=("last_name",))


def test_comparable_payload_carries_metadata_and_no_ids() -> None:
    records = [
        _golden_with_fields("cluster-alpha", {"program": "Housing"}),
        *[_golden_with_fields(f"rec-x{i}", {"program": "Housing"}) for i in range(19)],
    ]
    report = comparable_summary(
        records, threshold=11, breakdown_fields=("program",), period="FY2026 Q3"
    )
    payload = comparable_payload(report)
    assert payload["schema_version"] == REPORT_SCHEMA_VERSION
    assert payload["profile"] == COMPARABLE_PROFILE
    assert payload["period"] == "FY2026 Q3"
    assert payload["suppression_threshold"] == 11
    assert payload["generated_at"]
    assert payload["total_resolved"] == 20
    # Only category labels and counts: no record id or member list survives.
    serialized = json.dumps(payload)
    assert "cluster-alpha" not in serialized
    assert "rec-x" not in serialized


# ---------------------------------------------------------------------------
# The published total (issue #94): a suppressed cell must not be recoverable
# from the total, not just from the other cells in its own breakdown.
# ---------------------------------------------------------------------------


def test_total_is_suppressed_when_one_cell_is_hidden_against_a_true_zero() -> None:
    # Every record is a singleton with consent granted: the resolution
    # breakdown's "merged" cell has no valid complementary victim (its only
    # sibling, "singleton", is a true zero), so exactly one cell stays hidden.
    # Before the fix, summary.total handed it straight back by subtraction.
    records = [_golden(f"s{i}", (f"s{i}",), consent=True) for i in range(6)]
    summary = aggregate_summary(records, threshold=11)
    by_name = {b.name: b.cells for b in summary.breakdowns}
    assert by_name["consent"]["granted"] == SUPPRESSED
    assert by_name["consent"]["withheld"] == 0
    assert by_name["resolution"]["merged"] == 0
    assert by_name["resolution"]["singleton"] == SUPPRESSED
    assert summary.total == SUPPRESSED


def test_the_total_is_never_recoverable_across_every_single_cell_shape() -> None:
    """Exhaustive over the exact shape the issue measured: one small positive
    cell against a true zero, for every count that triggers primary
    suppression. Before the fix, the recovered value equaled the count every
    time; this proves it no longer does, for all of them at once."""
    threshold = 11
    for small in range(1, threshold):
        records = [_golden(f"g{i}", (f"g{i}",), consent=True) for i in range(small)]
        summary = aggregate_summary(records, threshold=threshold)
        assert summary.total == SUPPRESSED, (
            f"total leaked the suppressed cell (would equal {small}) when the "
            "sibling cell was a true zero"
        )


def test_an_isolated_breakdown_trigger_still_hides_the_total() -> None:
    """Only the resolution breakdown is vulnerable here; consent independently
    ends up with two safely-suppressed cells of its own. The total must hide
    because of resolution alone, proving detection is not somehow contingent
    on every breakdown failing at once."""
    records = [
        *[_golden(f"g{i}", (f"g{i}", f"g{i}b"), consent=True) for i in range(3)],
        *[_golden(f"w{i}", (f"w{i}", f"w{i}b"), consent=False) for i in range(3)],
    ]
    summary = aggregate_summary(records, threshold=11)
    by_name = {b.name: b.cells for b in summary.breakdowns}
    assert by_name["consent"]["granted"] == SUPPRESSED
    assert by_name["consent"]["withheld"] == SUPPRESSED  # safely 2-hidden, not the trigger
    assert by_name["resolution"]["merged"] == SUPPRESSED
    assert by_name["resolution"]["singleton"] == 0  # the true zero with no valid victim
    assert summary.total == SUPPRESSED


def test_total_stays_a_plain_int_when_nothing_is_uniquely_recoverable() -> None:
    """The overwhelmingly common path (from test_aggregate_summary_counts_and_
    suppresses) must be unaffected: two cells suppressed together, or nothing
    suppressed at all, and the total keeps publishing as a real number."""
    records = [
        _golden("c1", ("c1", "x1"), consent=True),
        _golden("c2", ("c2", "x2"), consent=True),
        *[_golden(f"s{i}", (f"s{i}",), consent=True) for i in range(20)],
    ]
    summary = aggregate_summary(records, threshold=11)
    assert summary.total == 22
    assert isinstance(summary.total, int)


def test_comparable_report_total_is_suppressed_by_a_configured_field_breakdown() -> None:
    """The vulnerable shape is not special to the consent/resolution
    breakdowns; a configured field breakdown (e.g. county) triggers it the
    same way, and comparable_summary's total must hide for it too."""
    records = [_golden_with_fields(f"a{i}", {"county": "Alder"}) for i in range(6)]
    report = comparable_summary(records, threshold=11, breakdown_fields=("county",))
    by_name = {b.name: b.cells for b in report.breakdowns}
    assert by_name["county"] == {"Alder": SUPPRESSED}
    assert report.total == SUPPRESSED


def test_comparable_payload_serializes_a_suppressed_total_as_the_sentinel_string() -> None:
    records = [_golden_with_fields(f"a{i}", {}) for i in range(6)]
    report = comparable_summary(records, threshold=11)
    payload = comparable_payload(report)
    assert payload["total_resolved"] == SUPPRESSED
    # Round-trips through JSON as a plain string, not something that needs
    # special-casing by a consumer parsing the file.
    reparsed = json.loads(json.dumps(payload))
    assert reparsed["total_resolved"] == "suppressed"


def test_render_summary_explains_why_the_total_is_missing() -> None:
    records = [_golden(f"s{i}", (f"s{i}",), consent=True) for i in range(6)]
    summary = aggregate_summary(records, threshold=11)
    text = render_summary(summary)
    assert "resolved records: suppressed" in text
    assert "recovered by subtraction" in text


def test_render_comparable_explains_why_the_total_is_missing() -> None:
    records = [_golden_with_fields(f"a{i}", {}) for i in range(6)]
    report = comparable_summary(records, threshold=11)
    text = render_comparable(report)
    assert "resolved records: suppressed" in text
    assert "recovered by subtraction" in text

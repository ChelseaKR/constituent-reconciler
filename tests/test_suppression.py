"""Tests for CMS-style small-cell suppression and the aggregate summary.

These are privacy invariants: a small nonzero cell must never survive into a
shareable aggregate, and a single suppressed cell must not be recoverable by
subtraction from a published total.
"""

from __future__ import annotations

from constituent_reconciler.models import GoldenRecord
from constituent_reconciler.suppression import (
    SUPPRESSED,
    aggregate_summary,
    suppress_cells,
)


def _golden(cluster_id: str, members: tuple[str, ...], consent: bool) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=members,
        fields={},
        primary=members[0],
        consent=consent,
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

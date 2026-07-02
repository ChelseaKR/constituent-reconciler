"""Tests for the per-source data-quality report.

The math is checked on hand-built records from two (or three) sources, and the
DV-pack case asserts the privacy invariant: a source small enough to identify
its members reports no raw count anywhere in the structure.
"""

from __future__ import annotations

import dataclasses

from constituent_reconciler.models import Cluster, Record, RunResult
from constituent_reconciler.quality import source_quality
from constituent_reconciler.suppression import SUPPRESSED


def _record(
    uid: str,
    source: str,
    raw: dict[str, str],
    normalized: dict[str, str],
    consent: str = "",
) -> Record:
    return Record(
        unique_id=uid,
        source=source,
        raw=raw,
        normalized=normalized,
        consent_status=consent,
    )


def _result(records: list[Record], clusters: tuple[Cluster, ...] = ()) -> RunResult:
    return RunResult(
        records={r.unique_id: r for r in records},
        pairs=(),
        clusters=clusters,
        golden=(),
    )


# ---------------------------------------------------------------------------
# Unsuppressed math
# ---------------------------------------------------------------------------


def test_completeness_is_fraction_of_nonempty_normalized_values() -> None:
    records = [
        _record("A1", "intake", {"first_name": "Ana", "phone": "555-000-1111"},
                {"first_name": "ana", "phone": "5550001111"}),
        _record("A2", "intake", {"first_name": "Bo", "phone": ""},
                {"first_name": "bo", "phone": ""}),
        _record("B1", "legacy", {"first_name": "Cy", "phone": ""},
                {"first_name": "cy", "phone": ""}),
        _record("B2", "legacy", {"first_name": "Di", "phone": ""},
                {"first_name": "di", "phone": ""}),
        _record("B3", "legacy", {"first_name": "", "phone": ""},
                {"first_name": "", "phone": ""}),
    ]
    intake, legacy = source_quality(_result(records))
    assert intake.source == "intake"
    assert intake.records == 2
    assert intake.completeness == {"first_name": 1.0, "phone": 0.5}
    assert legacy.source == "legacy"
    assert legacy.records == 3
    assert legacy.completeness["first_name"] == 2 / 3
    assert legacy.completeness["phone"] == 0.0


def test_normalization_failures_count_present_but_unparseable_values() -> None:
    records = [
        # Raw dob present, normalized empty: an unparseable date, one failure.
        _record("A1", "intake", {"first_name": "Ana", "dob": "not a date"},
                {"first_name": "ana", "dob": ""}),
        # Raw dob empty: missing, not a failure.
        _record("A2", "intake", {"first_name": "Bo", "dob": ""},
                {"first_name": "bo", "dob": ""}),
        # Raw dob present and parsed: not a failure.
        _record("A3", "intake", {"first_name": "Cy", "dob": "1980-01-02"},
                {"first_name": "cy", "dob": "1980-01-02"}),
        _record("A4", "intake", {"first_name": "Di", "dob": "1990-03-04"},
                {"first_name": "di", "dob": "1990-03-04"}),
    ]
    (intake,) = source_quality(_result(records))
    assert intake.normalization_failures == {"first_name": 0, "dob": 1}
    assert intake.normalization_failure_rates == {"first_name": 0.0, "dob": 0.25}
    # The failed parse also shows up as incompleteness: no evidence either way
    # (2 parsed of 4 records; the unparseable value counts as incomplete).
    assert intake.completeness["dob"] == 0.5


def test_consent_coverage_counts_only_usable_consent() -> None:
    records = [
        _record("A1", "intake", {"first_name": "Ana"}, {"first_name": "ana"}, "granted"),
        _record("A2", "intake", {"first_name": "Bo"}, {"first_name": "bo"}, "revoked"),
        _record("A3", "intake", {"first_name": "Cy"}, {"first_name": "cy"}, ""),
        _record("A4", "intake", {"first_name": "Di"}, {"first_name": "di"}, "granted"),
    ]
    (intake,) = source_quality(_result(records))
    assert intake.consent_coverage == 0.5


def test_duplicate_density_counts_records_in_multi_member_clusters() -> None:
    records = [
        _record("A1", "intake", {"first_name": "Ana"}, {"first_name": "ana"}),
        _record("A2", "intake", {"first_name": "Bo"}, {"first_name": "bo"}),
        _record("B1", "legacy", {"first_name": "Ana"}, {"first_name": "ana"}),
        _record("B2", "legacy", {"first_name": "Cy"}, {"first_name": "cy"}),
    ]
    clusters = (
        Cluster("c1", ("A1", "B1")),
        Cluster("c2", ("A2",)),
        Cluster("c3", ("B2",)),
    )
    intake, legacy = source_quality(_result(records, clusters))
    assert intake.duplicate_density == 0.5
    assert legacy.duplicate_density == 0.5


def test_explicit_fields_limit_what_is_measured() -> None:
    records = [
        _record("A1", "intake", {"first_name": "Ana", "phone": "x"},
                {"first_name": "ana", "phone": ""}),
    ]
    (intake,) = source_quality(_result(records), fields=("first_name",))
    assert set(intake.completeness) == {"first_name"}
    assert set(intake.normalization_failures) == {"first_name"}


# ---------------------------------------------------------------------------
# DV-pack suppression
# ---------------------------------------------------------------------------


def _bulk(source: str, prefix: str, n: int, **overrides: str) -> list[Record]:
    records = []
    for i in range(n):
        raw = {"first_name": f"Name{i}", "phone": overrides.get("phone", f"55500{i:05d}")}
        normalized = {"first_name": f"name{i}", "phone": raw["phone"][-10:]}
        records.append(
            _record(f"{prefix}{i:03d}", source, raw, normalized, consent="granted")
        )
    return records


def test_small_source_is_suppressed_and_leaks_no_raw_count() -> None:
    # Three sources so exactly which rows survive is unambiguous: the two small
    # sources are primary-suppressed and the large one needs no complementary
    # suppression.
    records = (
        _bulk("tiny", "T", 2)
        + _bulk("small", "S", 3)
        + _bulk("large", "L", 20)
    )
    report = source_quality(_result(records), suppress=True, threshold=11)
    by_source = {q.source: q for q in report}

    for name in ("tiny", "small"):
        row = dataclasses.asdict(by_source[name])
        row.pop("source")
        # Every value in the suppressed row, at any depth, is the sentinel:
        # no record count, completeness fraction, or failure count survives.
        flat = [
            v
            for value in row.values()
            for v in (value.values() if isinstance(value, dict) else [value])
        ]
        assert flat, name
        assert all(v == SUPPRESSED for v in flat), name

    assert by_source["large"].records == 20
    assert by_source["large"].consent_coverage == 1.0


def test_lone_small_source_takes_a_complementary_victim() -> None:
    # One small source next to one large source: publishing the large count
    # beside a suppressed row would let a reader recover the small count from
    # the run total, so both rows are suppressed (suppress_cells's rule).
    records = _bulk("tiny", "T", 2) + _bulk("large", "L", 20)
    report = source_quality(_result(records), suppress=True, threshold=11)
    assert all(q.records == SUPPRESSED for q in report)


def test_within_source_small_cells_are_suppressed() -> None:
    # 20 records, 15 with a phone: the 5 without are a small cell, so the
    # completeness fraction (which would reveal it) is suppressed. Consent is
    # 20 of 20 (complement a true zero) and survives.
    records = _bulk("large", "L", 15) + [
        _record(f"L9{i}", "large", {"first_name": f"Zed{i}", "phone": ""},
                {"first_name": f"zed{i}", "phone": ""}, consent="granted")
        for i in range(5)
    ]
    (large,) = source_quality(_result(records), suppress=True, threshold=11)
    assert large.records == 20
    assert large.completeness["phone"] == SUPPRESSED
    assert large.completeness["first_name"] == 1.0
    assert large.consent_coverage == 1.0
    assert large.duplicate_density == 0.0


def test_no_suppression_by_default() -> None:
    records = _bulk("tiny", "T", 2)
    (tiny,) = source_quality(_result(records))
    assert tiny.records == 2
    assert tiny.consent_coverage == 1.0

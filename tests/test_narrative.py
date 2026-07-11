"""Tests for the plain-language narrative run summary.

The narrative is a shareable artifact, so its privacy property is tested the
same way the aggregate summary's is: plant distinctive names, emails, and ids
in a run, render the page, and assert none of them appear. Both languages
render from the same data and must carry the same sections.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.cli import build_parser, main
from constituent_reconciler.config import Recipe
from constituent_reconciler.models import (
    Band,
    Cluster,
    Consent,
    GoldenRecord,
    Pair,
    Record,
    RunResult,
)
from constituent_reconciler.narrative import render_narrative

RESULT_SUMMARY: dict[str, object] = {
    "schema_version": 1,
    "policy_pack": "dv",
    "consent_required": True,
    "records_in": 27,
    "candidate_pairs": 8,
    "auto_merged_pairs": 6,
    "review_pairs": 2,
    "resolved_records": 21,
    "merged_records": 6,
    "withheld_no_consent": 1,
}

AGGREGATE: dict[str, object] = {
    "schema_version": 1,
    "total_resolved": 20,
    "breakdowns": {
        "consent": {"granted": 20, "withheld": 0},
        "resolution": {"merged": "suppressed", "singleton": 18},
    },
}


# ---------------------------------------------------------------------------
# Section structure, counts, and the standing caveat
# ---------------------------------------------------------------------------


def test_english_narrative_carries_all_sections() -> None:
    text = render_narrative(RESULT_SUMMARY, AGGREGATE, lang="en")
    assert "# Reconciliation run summary" in text
    assert "## What came in" in text
    assert "## What merged automatically and what went to a person" in text
    assert "## What was withheld and why" in text
    assert "## Shareable counts, small groups hidden" in text
    assert "## Standing caveat" in text


def test_spanish_narrative_carries_all_sections() -> None:
    text = render_narrative(RESULT_SUMMARY, AGGREGATE, lang="es")
    assert "# Resumen de la ejecución de reconciliación" in text
    assert "## Qué ingresó" in text
    assert "## Qué se fusionó automáticamente y qué pasó a una persona" in text
    assert "## Qué se retuvo y por qué" in text
    assert "## Conteos compartibles, con grupos pequeños ocultos" in text
    assert "## Advertencia permanente" in text


def test_withheld_count_and_caveat_appear() -> None:
    en = render_narrative(RESULT_SUMMARY, AGGREGATE, lang="en")
    assert "Records withheld because consent was not granted: **1**" in en
    assert "This is a reference implementation, not legal advice." in en
    es = render_narrative(RESULT_SUMMARY, AGGREGATE, lang="es")
    assert "Registros retenidos porque el consentimiento no fue otorgado: **1**" in es
    assert "Esta es una implementación de referencia, no asesoría legal." in es


def test_suppressed_cells_render_as_labels_not_numbers() -> None:
    en = render_narrative(RESULT_SUMMARY, AGGREGATE, lang="en")
    assert "merged suppressed" in en
    assert "single records 18" in en
    es = render_narrative(RESULT_SUMMARY, AGGREGATE, lang="es")
    assert "fusionados suprimido" in es
    assert "registros únicos 18" in es


def test_no_aggregate_omits_the_aggregate_section() -> None:
    summary = dict(RESULT_SUMMARY, policy_pack="default", consent_required=False)
    text = render_narrative(summary, None, lang="en")
    assert "## Shareable counts" not in text
    assert "did not enforce a consent requirement" in text


def test_unknown_language_is_rejected() -> None:
    with pytest.raises(ValueError):
        render_narrative(RESULT_SUMMARY, AGGREGATE, lang="fr")


# ---------------------------------------------------------------------------
# Privacy: no planted name, email, or id survives into the narrative
# ---------------------------------------------------------------------------

_SENTINELS = (
    "Zephyrine",
    "Quandt",
    "Balthazar",
    "Okonkwo-Reyes",
    "Wilhelmina",
    "Fetterlein",
    "zephyrine.quandt@example.test",
    "balthazar.okonkwo@example.test",
    "Z0001",
    "Z0002",
    "Z0003",
    "Z0004",
)


def _record(uid: str, first: str, last: str, email: str) -> Record:
    return Record(
        unique_id=uid,
        source="incoming",
        raw={"first_name": first, "last_name": last, "email": email},
        normalized={"first_name": first.lower(), "last_name": last.lower(), "email": email},
    )


def _planted_run() -> RunResult:
    records = {
        "Z0001": _record("Z0001", "Zephyrine", "Quandt", "zephyrine.quandt@example.test"),
        "Z0002": _record("Z0002", "Zephyrine", "Quandt", "zephyrine.quandt@example.test"),
        "Z0003": _record("Z0003", "Balthazar", "Okonkwo-Reyes", "balthazar.okonkwo@example.test"),
        "Z0004": _record("Z0004", "Wilhelmina", "Fetterlein", "wilhelmina.f@example.test"),
    }
    pairs = (
        Pair("Z0001", "Z0002", 0.99, Band.AUTO),
        Pair("Z0003", "Z0004", 0.85, Band.REVIEW),
    )
    clusters = (
        Cluster("c-0001", ("Z0001", "Z0002")),
        Cluster("c-0002", ("Z0003",)),
        Cluster("c-0003", ("Z0004",)),
    )
    golden = (
        GoldenRecord(
            "c-0001",
            ("Z0001", "Z0002"),
            {
                "first_name": "Zephyrine",
                "last_name": "Quandt",
                "email": "zephyrine.quandt@example.test",
            },
            "Z0001",
            consent=Consent(status="granted"),
        ),
        GoldenRecord(
            "c-0002",
            ("Z0003",),
            {
                "first_name": "Balthazar",
                "last_name": "Okonkwo-Reyes",
                "email": "balthazar.okonkwo@example.test",
            },
            "Z0003",
            consent=Consent(status="granted"),
        ),
        GoldenRecord(
            "c-0003",
            ("Z0004",),
            {
                "first_name": "Wilhelmina",
                "last_name": "Fetterlein",
                "email": "wilhelmina.f@example.test",
            },
            "Z0004",
            consent=Consent(),
        ),
    )
    return RunResult(records=records, pairs=pairs, clusters=clusters, golden=golden)


def _dv_recipe(base: Path) -> Recipe:
    return Recipe(
        incoming=base / "incoming.csv",
        mapping={"first_name": "First Name", "last_name": "Last Name", "email": "Email"},
        require_consent=True,
        policy_pack="dv",
        require_local_targets=True,
        aggregate_export=True,
        suppression_threshold=11,
        fields=("first_name", "last_name", "email"),
    )


def test_narrative_from_run_artifacts_contains_no_pii(tmp_path: Path) -> None:
    result = _planted_run()
    recipe = _dv_recipe(tmp_path)
    pipeline.export(result, recipe, out_dir=tmp_path)

    summary_path = tmp_path / "run_summary.json"
    aggregate_path = tmp_path / "aggregate_summary.json"
    assert summary_path.exists()
    assert aggregate_path.exists()

    result_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert result_summary["records_in"] == 4
    assert result_summary["auto_merged_pairs"] == 1
    assert result_summary["review_pairs"] == 1
    assert result_summary["resolved_records"] == 3
    assert result_summary["withheld_no_consent"] == 1

    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    for lang in ("en", "es"):
        text = render_narrative(result_summary, aggregate, lang=lang)
        for sentinel in _SENTINELS:
            assert sentinel not in text, f"{sentinel!r} leaked into the {lang} narrative"


# ---------------------------------------------------------------------------
# CLI round-trip
# ---------------------------------------------------------------------------


def test_report_parser_defaults() -> None:
    args = build_parser().parse_args(["report"])
    assert args.run_dir == "out"
    assert args.format == "narrative"
    assert args.lang == "en"
    assert args.out is None


def test_cli_report_renders_from_run_dir(tmp_path: Path) -> None:
    result = _planted_run()
    recipe = _dv_recipe(tmp_path)
    pipeline.export(result, recipe, out_dir=tmp_path)

    out_en = tmp_path / "narrative-en.md"
    assert main(["report", "--run-dir", str(tmp_path), "--out", str(out_en)]) == 0
    assert "## What came in" in out_en.read_text(encoding="utf-8")

    out_es = tmp_path / "narrative-es.md"
    assert main(["report", "--run-dir", str(tmp_path), "--lang", "es", "--out", str(out_es)]) == 0
    assert "## Qué ingresó" in out_es.read_text(encoding="utf-8")


def test_cli_report_fails_closed_without_run_summary(tmp_path: Path) -> None:
    assert main(["report", "--run-dir", str(tmp_path / "missing")]) == 2

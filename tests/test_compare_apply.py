"""Tests for the reviewed correction-file export (UC-02, PR 2).

The cross-cutting definition of done in docs/NOVEL-USE-CASES-PLAN.md asks for
one passing, one ambiguous, and one fail-closed fixture, and this file holds
all three: a clean review that produces the correction file, an unresolved
review item that blocks the export, and a manifest mismatch (plus a missing
manifest and a foreign decisions file) that refuses. The consent gate, the
destruction-inventory coverage of the new artifacts, and the merge-blocking
invariant that no compare-apply code path can reach a live connector are
tests here too, in the spirit of tests/test_no_egress.py.
"""

from __future__ import annotations

import csv
import inspect
import json
import os
import shutil
import time
from datetime import timedelta
from pathlib import Path

import pytest

from constituent_reconciler import compare, compare_apply
from constituent_reconciler.cli import main
from constituent_reconciler.destruction import PII_ARTIFACTS, inventory
from constituent_reconciler.manifest import file_digest
from constituent_reconciler.review.session import APPROVED, REJECTED, ReviewSession
from constituent_reconciler.schema import CUTOVER_CORRECTIONS_SCHEMA_VERSION

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compare"
LEFT_RECIPE = FIXTURES / "recipe-left.toml"
RIGHT_RECIPE = FIXTURES / "recipe-right.toml"


def _compare_into(out_dir: Path, left: Path = LEFT_RECIPE, right: Path = RIGHT_RECIPE) -> None:
    code = main(["compare", "--left", str(left), "--right", str(right), "--out", str(out_dir)])
    assert code == 0


def _session_for(
    out_dir: Path, left: Path = LEFT_RECIPE, right: Path = RIGHT_RECIPE
) -> ReviewSession:
    """The same session surface reconcile compare-review serves, headless."""

    left_side = compare.load_side(left, label="left")
    right_side = compare.load_side(right, label="right")
    result = compare.run_compare(left_side, right_side)
    return ReviewSession(
        compare.as_run_result(result),
        result.fields,
        out_dir / compare_apply.COMPARE_DECISIONS_FILENAME,
        reviewer="Ana",
    )


def _apply(out_dir: Path, *extra: str, left: Path = LEFT_RECIPE, right: Path = RIGHT_RECIPE) -> int:
    return main(
        [
            "compare-apply",
            "--left",
            str(left),
            "--right",
            str(right),
            "--out",
            str(out_dir),
            *extra,
        ]
    )


def _rows(out_dir: Path) -> list[dict[str, str]]:
    text = (out_dir / compare_apply.CORRECTIONS_FILENAME).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def _export_section(out_dir: Path) -> dict[str, object]:
    manifest = json.loads((out_dir / compare.COMPARE_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    export = manifest["export"]
    assert isinstance(export, dict)
    return export


@pytest.fixture(scope="module")
def approved_out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The passing fixture: compare, approve the one undecided pair, export."""

    out_dir = tmp_path_factory.mktemp("approved-out")
    _compare_into(out_dir)
    session = _session_for(out_dir)
    assert session.total == 1
    session.record(0, APPROVED)
    assert _apply(out_dir) == 0
    return out_dir


def test_clean_review_then_export_produces_the_correction_file(approved_out: Path) -> None:
    rows = _rows(approved_out)
    # Alice is missing from the target; the merged Devon and Sam identities
    # each carry a golden value the target does not hold. Maria matches with
    # no difference and Priya is target-only, so neither gets a row.
    assert len(rows) == 3
    by_first = {row["first_name"].lower(): row for row in rows}
    assert set(by_first) == {"alice", "devon", "sam"}
    assert by_first["devon"]["dob"] == "1990-04-12"
    assert by_first["sam"]["email"] == "sam.okafor@example.org"
    assert all(row[compare_apply.EXTERNAL_ID_COLUMN] for row in rows)
    blob = (approved_out / compare_apply.CORRECTIONS_FILENAME).read_text(encoding="utf-8")
    assert "priya" not in blob.lower()


def test_the_manifest_binds_the_export_by_digest_not_content(approved_out: Path) -> None:
    export = _export_section(approved_out)
    assert export["schema_version"] == CUTOVER_CORRECTIONS_SCHEMA_VERSION
    assert export["format"] == "csv"
    assert export["correction_file"] == compare_apply.CORRECTIONS_FILENAME
    assert export["correction_file_digest"] == file_digest(
        approved_out / compare_apply.CORRECTIONS_FILENAME
    )
    assert export["decisions_digest"] == file_digest(
        approved_out / compare_apply.COMPARE_DECISIONS_FILENAME
    )
    assert export["rows"] == 3
    assert export["row_reasons"] == {
        compare_apply.REASON_MISSING: 1,
        compare_apply.REASON_FIELD: 2,
    }
    assert export["withheld"] == {}
    # Digest, not content: no field value of any compared record leaks in.
    blob = json.dumps(export)
    for value in ("Alice", "Devon", "Sam", "1990-04-12", "sam.okafor@example.org"):
        assert value not in blob


def test_rejecting_the_pair_exports_the_left_devon_as_missing(tmp_path: Path) -> None:
    _compare_into(tmp_path)
    session = _session_for(tmp_path)
    session.record(0, REJECTED)
    assert _apply(tmp_path) == 0
    export = _export_section(tmp_path)
    assert export["rows"] == 3
    assert export["row_reasons"] == {
        compare_apply.REASON_MISSING: 2,
        compare_apply.REASON_FIELD: 1,
    }
    devon = next(row for row in _rows(tmp_path) if row["first_name"].lower() == "devon")
    assert devon["dob"] == "1990-04-12"


def test_an_unresolved_review_item_blocks_the_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The ambiguous fixture: the Devon pair is never decided.
    _compare_into(tmp_path)
    assert _apply(tmp_path) == 2
    err = capsys.readouterr().err
    assert "need review" in err
    assert "compare-review" in err
    assert not (tmp_path / compare_apply.CORRECTIONS_FILENAME).exists()


def test_a_decisions_file_that_skips_the_pair_still_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _compare_into(tmp_path)
    (tmp_path / compare_apply.COMPARE_DECISIONS_FILENAME).write_text(
        json.dumps({"approved": [], "rejected": [], "audit": {}}), encoding="utf-8"
    )
    assert _apply(tmp_path) == 2
    assert "unresolved" in capsys.readouterr().err
    assert not (tmp_path / compare_apply.CORRECTIONS_FILENAME).exists()


def test_a_pair_awaiting_its_second_reviewer_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _compare_into(tmp_path)
    (tmp_path / compare_apply.COMPARE_DECISIONS_FILENAME).write_text(
        json.dumps(
            {
                "approved": [],
                "rejected": [],
                "audit": {
                    "La|Rb": [
                        {"reviewer": "Ana", "verdict": "approved", "decided_at": "2026-08-03"}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    assert _apply(tmp_path) == 2
    err = capsys.readouterr().err
    assert "awaiting" in err
    assert "La and Rb" in err


def test_a_decisions_file_from_a_different_comparison_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _compare_into(tmp_path)
    (tmp_path / compare_apply.COMPARE_DECISIONS_FILENAME).write_text(
        json.dumps({"approved": [["X1", "Y1"]], "rejected": []}), encoding="utf-8"
    )
    assert _apply(tmp_path) == 2
    assert "never scored" in capsys.readouterr().err


def test_a_missing_manifest_refuses_the_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The fail-closed fixture, first half: no comparison was recorded here.
    assert _apply(tmp_path) == 2
    assert "no comparison manifest" in capsys.readouterr().err
    assert not (tmp_path / compare_apply.CORRECTIONS_FILENAME).exists()


def test_an_input_changed_after_compare_refuses_the_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The fail-closed fixture, second half: the manifest no longer matches.
    for name in ("recipe-left.toml", "recipe-right.toml", "left.csv", "right.csv"):
        shutil.copy(FIXTURES / name, tmp_path / name)
    out_dir = tmp_path / "out"
    _compare_into(out_dir, tmp_path / "recipe-left.toml", tmp_path / "recipe-right.toml")
    with (tmp_path / "right.csv").open("a", encoding="utf-8") as handle:
        handle.write("Noor,Haddad,1994-06-01,noor.h@example.org\n")
    code = _apply(out_dir, left=tmp_path / "recipe-left.toml", right=tmp_path / "recipe-right.toml")
    assert code == 2
    err = capsys.readouterr().err
    assert "does not match the current inputs" in err
    assert not (out_dir / compare_apply.CORRECTIONS_FILENAME).exists()


def test_zero_review_items_export_without_a_review_step(tmp_path: Path) -> None:
    left = tmp_path / "left.csv"
    left.write_text(
        "first_name,last_name,dob\nMaria,Lopez,1985-03-02\nAlice,Nguyen,1979-11-30\n",
        encoding="utf-8",
    )
    right = tmp_path / "right.csv"
    right.write_text("first_name,last_name,dob\nMaria,Lopez,1985-03-02\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    _compare_into(out_dir, left, right)
    # No decisions file exists and none is needed: nothing was uncertain.
    assert _apply(out_dir, left=left, right=right) == 0
    rows = _rows(out_dir)
    assert len(rows) == 1
    assert rows[0]["first_name"].lower() == "alice"
    assert _export_section(out_dir)["decisions_digest"] is None


def test_consent_gates_the_export_and_counts_the_withheld(tmp_path: Path) -> None:
    # Four left-only people: one granted, one revoked, one expired, one with
    # no consent recorded at all. Only the granted one may reach the file,
    # matching the write path's consent behavior.
    (tmp_path / "left.csv").write_text(
        "\n".join(
            [
                "First,Last,Birth,Consent,Granted,Expires",
                "Grace,Okonkwo,1980-01-05,granted,2026-01-01,2030-01-01",
                "Rhea,Vance,1971-09-12,revoked,2025-01-01,",
                "Edmund,Palli,1966-02-27,granted,2020-01-01,2021-01-01",
                "Abram,Stroud,1990-12-19,,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "recipe-left.toml").write_text(
        "\n".join(
            [
                "[input]",
                'incoming = "left.csv"',
                "",
                "[mapping]",
                'first_name = "First"',
                'last_name = "Last"',
                'dob = "Birth"',
                "",
                "[consent]",
                "require = true",
                'column = "Consent"',
                'date = "Granted"',
                'expires = "Expires"',
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "right.csv").write_text(
        "first_name,last_name,dob\nZed,Quill,1999-07-07\n", encoding="utf-8"
    )
    out_dir = tmp_path / "out"
    _compare_into(out_dir, tmp_path / "recipe-left.toml", tmp_path / "right.csv")
    assert _apply(out_dir, left=tmp_path / "recipe-left.toml", right=tmp_path / "right.csv") == 0

    rows = _rows(out_dir)
    assert len(rows) == 1
    assert rows[0]["first_name"].lower() == "grace"
    blob = (out_dir / compare_apply.CORRECTIONS_FILENAME).read_text(encoding="utf-8")
    for withheld_name in ("rhea", "edmund", "abram"):
        assert withheld_name not in blob.lower()

    export = _export_section(out_dir)
    assert export["rows"] == 1
    assert export["withheld"] == {"absent": 1, "revoked": 1, "expired": 1}
    withheld_rows = list(
        csv.DictReader(
            (out_dir / compare_apply.CUTOVER_WITHHELD_FILENAME)
            .read_text(encoding="utf-8")
            .splitlines()
        )
    )
    assert sorted(row["reason"] for row in withheld_rows) == ["absent", "expired", "revoked"]
    # Ids and reasons only: the withheld list repeats no field value.
    withheld_blob = (out_dir / compare_apply.CUTOVER_WITHHELD_FILENAME).read_text(encoding="utf-8")
    for value in ("Rhea", "Vance", "Edmund", "Abram", "1971-09-12"):
        assert value not in withheld_blob


def test_a_merged_identity_is_withheld_on_its_most_restrictive_member(tmp_path: Path) -> None:
    # Issue #83: the legacy export holds the same person twice, once with a
    # grant and once with a revocation. The two rows resolve to one identity,
    # and that identity inherits the revocation, so the correction file the
    # target imports carries neither row. Same rule as the write path, because
    # both sides call decisions.golden_records.
    (tmp_path / "left.csv").write_text(
        "\n".join(
            [
                "First,Last,Birth,Consent",
                "Grace,Okonkwo,1980-01-05,granted",
                "Grace,Okonkwo,1980-01-05,revoked",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "recipe-left.toml").write_text(
        "\n".join(
            [
                "[input]",
                'incoming = "left.csv"',
                "",
                "[mapping]",
                'first_name = "First"',
                'last_name = "Last"',
                'dob = "Birth"',
                "",
                "[consent]",
                "require = true",
                'column = "Consent"',
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "right.csv").write_text(
        "first_name,last_name,dob\nZed,Quill,1999-07-07\n", encoding="utf-8"
    )
    out_dir = tmp_path / "out"
    _compare_into(out_dir, tmp_path / "recipe-left.toml", tmp_path / "right.csv")
    assert _apply(out_dir, left=tmp_path / "recipe-left.toml", right=tmp_path / "right.csv") == 0

    assert _rows(out_dir) == []
    export = _export_section(out_dir)
    assert export["rows"] == 0
    assert export["withheld"] == {"revoked": 1}
    blob = (out_dir / compare_apply.CORRECTIONS_FILENAME).read_text(encoding="utf-8")
    assert "okonkwo" not in blob.lower()


def test_a_reviewer_correction_flows_through_the_export(tmp_path: Path) -> None:
    _compare_into(tmp_path)
    session = _session_for(tmp_path)
    view = session.views()[0]
    # Fix the legacy Devon's date of birth to what the target holds; the
    # correction counts as this reviewer's approval of the pair.
    side = "left" if view.left_id.startswith("L") else "right"
    session.correct(0, field="dob", side=side, value="1990-04-21")
    assert _apply(tmp_path) == 0
    # With the dates agreeing, the merged Devon no longer differs from the
    # target, so the export shrinks to Alice and Sam.
    rows = _rows(tmp_path)
    assert sorted(row["first_name"].lower() for row in rows) == ["alice", "sam"]
    export = _export_section(tmp_path)
    assert export["corrections_digest"] == file_digest(tmp_path / "corrections.json")


@pytest.mark.parametrize(
    ("fmt", "first_column", "dob_column"),
    [
        ("salesforce_csv", "FirstName", "Birthdate"),
        ("civicrm_csv", "first_name", "birth_date"),
    ],
)
def test_crm_import_formats_reuse_the_run_pipelines_field_maps(
    approved_out: Path, tmp_path: Path, fmt: str, first_column: str, dob_column: str
) -> None:
    out_dir = tmp_path / "out"
    shutil.copytree(approved_out, out_dir)
    assert _apply(out_dir, "--format", fmt) == 0
    rows = _rows(out_dir)
    assert len(rows) == 3
    header = set(rows[0])
    assert {first_column, dob_column, compare_apply.EXTERNAL_ID_COLUMN} <= header
    assert _export_section(out_dir)["format"] == fmt


def test_no_compare_apply_code_path_can_reach_a_live_connector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The left recipe names a live CiviCRM target on purpose. Every route to a
    # connector or the network is replaced with a refusal; the whole
    # compare -> review -> export flow must complete without touching one.
    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("compare-apply must not touch a connector or the network")

    monkeypatch.setattr("constituent_reconciler.connectors.get_factory", refuse)
    monkeypatch.setattr("constituent_reconciler.pipeline.get_factory", refuse)
    monkeypatch.setattr("constituent_reconciler.pipeline.build_connector", refuse)
    monkeypatch.setattr("urllib.request.urlopen", refuse)

    live_left = FIXTURES / "recipe-left-live.toml"
    _compare_into(tmp_path, live_left)
    session = _session_for(tmp_path, live_left)
    session.record(0, APPROVED)
    assert _apply(tmp_path, left=live_left) == 0
    assert (tmp_path / compare_apply.CORRECTIONS_FILENAME).exists()
    # The live [output] section was never used: no write-side artifact exists.
    assert not (tmp_path / "resolved.csv").exists()
    assert not (tmp_path / "provenance.jsonl").exists()
    out = capsys.readouterr().out
    assert "local" in out


def test_the_export_module_never_names_a_network_route() -> None:
    # Structural half of the invariant: the module cannot look up a connector
    # by name or open a network connection, so a recipe's [output] section has
    # nothing here to act on. The only writer it names is the local
    # import-file connector.
    source = inspect.getsource(compare_apply)
    for token in ("get_factory", "build_connector", "urlopen", "urllib"):
        assert token not in source
    assert "CrmCsvConnector" in source


def test_the_new_artifacts_are_in_the_destruction_inventory(tmp_path: Path) -> None:
    for name in ("target_corrections.csv", "cutover_withheld.csv", "corrections.json"):
        assert name in PII_ARTIFACTS
        (tmp_path / name).write_text("placeholder\n", encoding="utf-8")
        old = time.time() - 2 * 24 * 3600
        os.utime(tmp_path / name, (old, old))
    names = {path.name for path in inventory(tmp_path, timedelta(days=1))}
    assert {"target_corrections.csv", "cutover_withheld.csv", "corrections.json"} <= names


def test_compare_review_with_nothing_to_review_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    left = tmp_path / "left.csv"
    left.write_text("first_name,last_name,dob\nMaria,Lopez,1985-03-02\n", encoding="utf-8")
    right = tmp_path / "right.csv"
    right.write_text("first_name,last_name,dob\nMaria,Lopez,1985-03-02\n", encoding="utf-8")
    code = main(
        [
            "compare-review",
            "--left",
            str(left),
            "--right",
            str(right),
            "--out",
            str(tmp_path / "out"),
            "--reviewer",
            "Ana",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "nothing to review" in out
    assert not (tmp_path / "out" / compare_apply.COMPARE_DECISIONS_FILENAME).exists()


def test_compare_review_serves_the_same_queue_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    served: dict[str, object] = {}

    def fake_serve(session: ReviewSession, **kwargs: object) -> None:
        served["total"] = session.total
        served["reviewer"] = session.reviewer
        session.record(0, APPROVED)

    monkeypatch.setattr("constituent_reconciler.review.server.serve", fake_serve)
    code = main(
        [
            "compare-review",
            "--left",
            str(LEFT_RECIPE),
            "--right",
            str(RIGHT_RECIPE),
            "--out",
            str(tmp_path),
            "--reviewer",
            "Ana",
            "--no-browser",
        ]
    )
    assert code == 0
    assert served == {"total": 1, "reviewer": "Ana"}
    out = capsys.readouterr().out
    assert "1 approved, 0 rejected, 0 pending" in out
    saved = json.loads(
        (tmp_path / compare_apply.COMPARE_DECISIONS_FILENAME).read_text(encoding="utf-8")
    )
    assert len(saved["approved"]) == 1


def test_compare_review_reports_a_bad_side_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "compare-review",
            "--left",
            str(tmp_path / "missing.toml"),
            "--right",
            str(RIGHT_RECIPE),
            "--out",
            str(tmp_path),
            "--reviewer",
            "Ana",
        ]
    )
    assert code == 2
    assert "compare error" in capsys.readouterr().err
